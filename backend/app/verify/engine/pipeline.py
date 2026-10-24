"""The verification pipeline: one session, five stages (TW-658).

Implements spec S5.2 end to end over the port boundary:

1. Document capture and extraction (OCR + MRZ + PDF417, cross-
   checked; extracted fields hashed at ingestion, the minimum
   plaintext the match step needs sealed in the encrypted match
   payload and cleared at decision).
2. Authenticity signals from the cross-checks (in the evaluation).
3. Face match: document portrait vs live selfie, banded.
4. Liveness: randomized active challenges over landmark frames,
   plus the passive MiniFASNet signal as one score among many.
5. Fraud signals and verdict: duplicate and stolen-photo search,
   banned-anchor refusal, velocity, the verdict combiner.

Decision discipline, on every outcome including review and fail:
staged images are wiped, the match payload is cleared, and the
destruction is written into the retention log as executed actions.
Only a pass persists a template, an attestation, and a tier.
Consent is required twice: before a session exists, and again at
decision time before anything persists, so a mid-session
withdrawal cannot be silently undone by an in-flight session. No
exception path leaves a staged image behind either: a failed stage
wipes what it staged before the error propagates, and staged refs
that outlive their session are bounded by the staging TTL sweep
(sweep_staging).

Production capture is gated by the D3 flag in EngineConfig and by
current consent (TW-654 D1/D2), in that order, before a session
exists. Benchmark runs never come through here: they score dataset
files through benchmarking.py and create no sessions, ever.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.verify.benchmark.stages import StageUnavailable

from .config import ENGINE_VERSION, EngineConfig
from .consent import require_consent
from .crosschecks import evaluate_document, parse_ocr_fields
from .crypto import Encryptor
from .errors import (
    CaptureDisabled,
    ConsentRequired,
    DocumentClassNotEnabled,
    SessionStateError,
    UnknownSession,
)
from .face import (
    TemplateRecord,
    band_for_similarity,
    cosine_similarity,
    find_duplicates,
)
from .hashing import FieldHasher
from .liveness import evaluate_challenges, generate_challenges
from .normalize import age_band_for, next_band_date_for
from .ports import PipelinePorts
from .secondary import (
    SecondaryCheckRequest,
    SecondaryCheckResult,
    evaluate_secondary_check,
)
from .staging import Staging
from .store import VerifyStore
from .velocity import AttemptRecord, check_velocity
from .verdict import Verdict, VerdictInputs, decide

# Where the template key lives: a secrets-store reference, never a
# value, recorded on each biometric row per the schema.
TEMPLATE_KEY_REF = "env:VFY_TEMPLATE_KEY_B64"


@dataclass(frozen=True)
class SessionView:
    id: str
    status: str
    document_type: str


@dataclass(frozen=True)
class VerdictView:
    """The verdict-only shape of spec S5.6: nothing else leaves."""

    verification_id: str
    status: str  # passed | failed | review | in-progress session status
    tier_granted: int
    confidence_band: str | None
    age_band: str | None
    decided_at: datetime | None

    def as_dict(self) -> dict:
        return {
            "verification_id": self.verification_id,
            "status": self.status,
            "tier_granted": self.tier_granted,
            "confidence_band": self.confidence_band,
            "age_band": self.age_band,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }


class VerificationPipeline:
    def __init__(
        self,
        *,
        store: VerifyStore,
        staging: Staging,
        hasher: FieldHasher,
        encryptor: Encryptor,
        ports: PipelinePorts,
        config: EngineConfig,
        secondary_port=None,
        rng: random.Random | None = None,
        clock=None,
    ):
        self.store = store
        self.staging = staging
        self.hasher = hasher
        self.encryptor = encryptor
        self.ports = ports
        self.config = config
        # TW-678: the secondary identity stage's port. None is the
        # production posture today (no $0 records source exists);
        # flows that require the stage then fail closed in the
        # verdict combiner, honestly, instead of skipping it.
        self.secondary_port = secondary_port
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._engine_version_id: str | None = None
        # Process-local session working state: device/network refs
        # for velocity, staging refs awaiting destruction, the
        # issued liveness challenges. None of it is a record; the
        # durable trail is the vfy_ rows and signals.
        self._context: dict[str, dict] = {}

    # ------------------------------------------------------------ sessions

    def _version_id(self) -> str:
        if self._engine_version_id is None:
            self._engine_version_id = self.store.ensure_engine_version(
                ENGINE_VERSION,
                self.config.pipeline_config(),
                {"embedding": "dlib-face-recognition", "ocr": "paddleocr"},
            )
        return self._engine_version_id

    def start_session(
        self,
        user_id: str,
        document_type: str,
        tier_requested: int = 2,
        *,
        device_ref: str | None = None,
        network_ref: str | None = None,
    ) -> SessionView:
        if not self.config.production_capture_enabled:
            raise CaptureDisabled(
                "production capture is disabled (TW-654 D3): the capture "
                "flag stays off until counsel's written review is filed"
            )
        if document_type not in self.config.enabled_document_classes:
            raise DocumentClassNotEnabled(
                f"capture for document class {document_type!r} is not "
                "built in this phase"
            )
        require_consent(self.store, user_id, document_type)
        row = self.store.create_session(
            user_id=user_id,
            document_type=document_type,
            tier_requested=tier_requested,
            engine_version_id=self._version_id(),
        )
        self._context[row["id"]] = {
            "device_ref": device_ref,
            "network_ref": network_ref,
        }
        return SessionView(
            id=row["id"], status=row["status"], document_type=document_type
        )

    def _session(self, session_id: str, user_id: str | None = None) -> dict:
        row = self.store.get_session(session_id)
        if row is None:
            raise UnknownSession(f"no verification session {session_id!r}")
        if user_id is not None and row["user_id"] != user_id:
            raise PermissionError("session belongs to a different member")
        return row

    # ------------------------------------------------------- stage 1 and 2

    def submit_document(self, session_id: str, user_id: str, image_bytes: bytes) -> str:
        session = self._session(session_id, user_id)
        if session["status"] != "started":
            raise SessionStateError(
                f"session {session_id} is {session['status']}, not started"
            )
        ref = self.staging.put(image_bytes)
        try:
            self._run_document_stage(session, ref)
        except Exception:
            # No failure path leaves a staged image behind. An
            # unavailable stage surfaces as its typed refusal (the
            # router maps StageUnavailable to 503), the session
            # stays at started, and the member can retry.
            self.staging.wipe(ref)
            raise
        return "document_received"

    def _run_document_stage(self, session: dict, ref: str) -> None:
        session_id = session["id"]
        now = self.clock()
        path = self.staging.local_path(ref)
        document_type = session["document_type"]

        ocr_fields = parse_ocr_fields(self.ports.ocr.extract_text(path))
        mrz = None
        if document_type == "passport":
            mrz = self.ports.mrz.read(path)
        pdf417_fields = self.ports.pdf417.decode_fields(path)
        evaluation = evaluate_document(
            document_type=document_type,
            ocr_fields=ocr_fields,
            mrz=mrz,
            pdf417_fields=pdf417_fields,
            today=now.date(),
        )

        face_count = len(self.ports.face_detect.detect(path))
        portrait = None
        if face_count >= 1:
            try:
                portrait = self.ports.embed.embed(path)
            except StageUnavailable:
                portrait = None

        fields = evaluation.fields
        name = ocr_fields.get("name")
        if not name and (fields.get("surname") or fields.get("given_names")):
            name = (
                f"{fields.get('given_names', '')} {fields.get('surname', '')}".strip()
            )
        payload = {
            "name": name,
            "dob": fields.get("dob"),
            "expiry": fields.get("expiry"),
            "doc_number": fields.get("doc_number"),
            "portrait_embedding": list(portrait) if portrait else None,
            "document_face_count": face_count,
            "mrz_valid": evaluation.mrz_valid,
            "tamper_signals": evaluation.tamper_signals,
            "agreement": evaluation.agreement,
            "extracted": evaluation.extracted,
        }
        document = self.store.save_document(
            {
                "session_id": session_id,
                "document_type": document_type,
                "issuing_region": None,
                "name_hash": self.hasher.field_hash(name or ""),
                "dob_hash": (
                    self.hasher.field_hash(fields["dob"]) if fields.get("dob") else None
                ),
                "doc_number_hash": self.hasher.field_hash(
                    fields.get("doc_number") or ""
                ),
                "match_payload_encrypted": self.encryptor.encrypt(
                    json.dumps(payload).encode("utf-8")
                ),
                "match_payload_cleared_at": None,
                "image_staging_ref": ref,
                "image_destroyed_at": None,
            }
        )
        if evaluation.mrz_valid is not None:
            self.store.add_signal(
                {
                    "session_id": session_id,
                    "signal_type": "mrz_check",
                    "score": 1.0 if evaluation.mrz_valid else 0.0,
                    "detail": {"valid": evaluation.mrz_valid},
                }
            )
        if evaluation.agreement:
            agreed = sum(1 for ok in evaluation.agreement.values() if ok)
            self.store.add_signal(
                {
                    "session_id": session_id,
                    "signal_type": "front_barcode_agreement",
                    "score": agreed / len(evaluation.agreement),
                    "detail": evaluation.agreement,
                }
            )
        if evaluation.mrz_agreement:
            agreed = sum(1 for ok in evaluation.mrz_agreement.values() if ok)
            self.store.add_signal(
                {
                    "session_id": session_id,
                    "signal_type": "front_mrz_agreement",
                    "score": agreed / len(evaluation.mrz_agreement),
                    "detail": evaluation.mrz_agreement,
                }
            )
        self.store.add_signal(
            {
                "session_id": session_id,
                "signal_type": "capture_forensics",
                "score": None,
                "detail": {
                    "document_face_count": face_count,
                    "tamper_signals": evaluation.tamper_signals,
                },
            }
        )
        self.store.update_session(session_id, status="document_received")
        context = self._context.setdefault(session_id, {})
        context["document_ref"] = ref
        context["document_id"] = document["id"]

    # ------------------------------------------------------------- stage 4

    def begin_liveness(self, session_id: str, user_id: str) -> list[str]:
        session = self._session(session_id, user_id)
        if session["status"] != "document_received":
            raise SessionStateError(
                f"session {session_id} is {session['status']}; "
                "liveness begins after the document stage"
            )
        challenges = generate_challenges(self.rng, self.config.liveness_challenge_count)
        self._context.setdefault(session_id, {})["challenges"] = challenges
        return challenges

    # ------------------------------------------------------- stage 3 and 5

    def submit_selfie(
        self,
        session_id: str,
        user_id: str,
        image_bytes: bytes,
        frames: list,
    ) -> VerdictView:
        session = self._session(session_id, user_id)
        if session["status"] != "document_received":
            raise SessionStateError(
                f"session {session_id} is {session['status']}, not document_received"
            )
        now = self.clock()
        today = now.date()
        context = self._context.setdefault(session_id, {})
        challenges = context.get("challenges") or generate_challenges(
            self.rng, self.config.liveness_challenge_count
        )

        document = self.store.get_document_for_session(session_id)
        payload = json.loads(
            self.encryptor.decrypt(document["match_payload_encrypted"]).decode("utf-8")
        )

        ref = self.staging.put(image_bytes)
        try:
            return self._run_selfie_stage(
                session=session,
                session_id=session_id,
                user_id=user_id,
                document=document,
                payload=payload,
                ref=ref,
                frames=frames,
                now=now,
                today=today,
                context=context,
                challenges=challenges,
            )
        except Exception:
            # No failure path leaves the selfie staged. The
            # document stays with its still-live session, bounded
            # by its decision or the staging sweep.
            self.staging.wipe(ref)
            raise

    def _run_selfie_stage(
        self,
        *,
        session: dict,
        session_id: str,
        user_id: str,
        document: dict,
        payload: dict,
        ref: str,
        frames: list,
        now: datetime,
        today: date,
        context: dict,
        challenges: list,
    ) -> VerdictView:
        path = self.staging.local_path(ref)
        selfie_count = len(self.ports.face_detect.detect(path))
        embedding = None
        if selfie_count >= 1:
            try:
                embedding = self.ports.embed.embed(path)
            except StageUnavailable:
                embedding = None
        try:
            passive = self.ports.liveness.bona_fide_probability(path)
        except StageUnavailable:
            passive = None

        challenge_results = evaluate_challenges(challenges, frames)
        active_ok = all(challenge_results.values())

        portrait = (
            tuple(payload["portrait_embedding"])
            if payload.get("portrait_embedding")
            else None
        )
        similarity = None
        if embedding is not None and portrait is not None:
            similarity = cosine_similarity(embedding, portrait)
            face_band = band_for_similarity(similarity, self.config)
        elif selfie_count == 0 or payload["document_face_count"] == 0:
            face_band = "no_match"
        else:
            face_band = "review"

        # Identity anchor + ban + duplicate search.
        anchor = None
        if payload.get("name") and payload.get("dob") and payload.get("doc_number"):
            anchor = self.hasher.anchor_hash(
                payload["name"], payload["dob"], payload["doc_number"]
            )
        anchor_row = self.store.get_anchor(anchor) if anchor else None
        banned = bool(anchor_row and anchor_row["is_banned"])
        duplicate_hit = bool(anchor_row and anchor_row["user_id"] != user_id)
        hits = []
        if embedding is not None:
            corpus = []
            for row in self.store.list_live_biometrics():
                if row["user_id"] == user_id:
                    continue
                row_anchor = self.store.get_anchor(row.get("anchor_hash") or "")
                corpus.append(
                    TemplateRecord(
                        anchor_hash=row.get("anchor_hash") or "",
                        user_id=row["user_id"],
                        is_banned=bool(row_anchor and row_anchor["is_banned"]),
                        embedding=tuple(
                            json.loads(
                                self.encryptor.decrypt(
                                    row["template_encrypted"]
                                ).decode("utf-8")
                            )
                        ),
                    )
                )
            hits = find_duplicates(embedding, corpus, self.config)
        if hits:
            duplicate_hit = True
            if any(hit.is_banned for hit in hits):
                banned = True

        # Velocity: record this attempt, then check the window.
        # A document with no extracted number has no document key:
        # its doc_number_hash is the empty-string placeholder
        # (design call 7), and recording velocity against it would
        # bucket every unreadable document on the platform into
        # one shared limit (Rosa's m4).
        doc_key = document["doc_number_hash"] if payload.get("doc_number") else None
        if doc_key is not None:
            self.store.record_attempt(
                session_id, AttemptRecord("document", doc_key, now)
            )
        if context.get("device_ref"):
            self.store.record_attempt(
                session_id, AttemptRecord("device", context["device_ref"], now)
            )
        if context.get("network_ref"):
            self.store.record_attempt(
                session_id, AttemptRecord("network", context["network_ref"], now)
            )
        exceeded = check_velocity(
            self.store.list_attempts(),
            document_key=doc_key,
            device_key=context.get("device_ref"),
            network_key=context.get("network_ref"),
            now=now,
            config=self.config,
        )

        # Secondary identity stage (TW-678): runs only for flows
        # that require it (Arizona-purpose enrollment, A.R.S.
        # 4-101 condition (b)). The request is built from the
        # document row's hashes and the anchor, never plaintext;
        # the outcome is a verdict input and a signal, nothing
        # more is kept.
        secondary_result: SecondaryCheckResult | None = None
        if self.config.secondary_identity_required:
            if anchor:
                secondary_result = evaluate_secondary_check(
                    self.secondary_port,
                    SecondaryCheckRequest(
                        anchor_hash=anchor,
                        name_hash=document["name_hash"],
                        dob_hash=document["dob_hash"],
                        doc_number_hash=document["doc_number_hash"],
                        document_type=session["document_type"],
                    ),
                )
            else:
                secondary_result = SecondaryCheckResult(
                    status="unavailable",
                    source_kind=None,
                    source_ref_hash=None,
                    reasons=("secondary_identity_fields_missing",),
                )

        dob = payload.get("dob")
        band = age_band_for(date.fromisoformat(dob), today) if dob else None
        expired = bool(
            payload.get("expiry") and date.fromisoformat(payload["expiry"]) < today
        )
        verdict = decide(
            VerdictInputs(
                document_extracted=bool(payload["extracted"]),
                mrz_valid=payload["mrz_valid"],
                tamper_signals=list(payload["tamper_signals"]),
                document_expired=expired,
                dob_band=band,
                face_band=face_band,
                face_similarity=similarity,
                selfie_face_count=selfie_count,
                document_face_count=payload["document_face_count"],
                liveness_active_ok=active_ok,
                liveness_passive=passive,
                duplicate_hit=duplicate_hit,
                banned_anchor=banned,
                velocity_exceeded=exceeded,
                ai_face_score=None,
                secondary_required=self.config.secondary_identity_required,
                secondary_status=(
                    secondary_result.status if secondary_result is not None else None
                ),
            ),
            self.config,
        )

        # Signal trail (vfy_signals): the verdict must be re-derivable.
        self.store.add_signal(
            {
                "session_id": session_id,
                "signal_type": "match_score",
                "score": similarity,
                "detail": {"band": face_band},
            }
        )
        self.store.add_signal(
            {
                "session_id": session_id,
                "signal_type": "liveness_score",
                "score": passive,
                "detail": {"active": challenge_results, "active_ok": active_ok},
            }
        )
        if hits:
            self.store.add_signal(
                {
                    "session_id": session_id,
                    "signal_type": "duplicate_hit",
                    "score": max(hit.similarity for hit in hits),
                    "detail": {
                        "anchors": [hit.anchor_hash for hit in hits],
                        "banned": any(hit.is_banned for hit in hits),
                    },
                }
            )
        self.store.add_signal(
            {
                "session_id": session_id,
                "signal_type": "velocity",
                "score": None,
                "detail": {
                    "exceeded": exceeded,
                    "document": doc_key,
                    "device": context.get("device_ref"),
                    "network": context.get("network_ref"),
                },
            }
        )
        if secondary_result is not None:
            # Verdict + reference hashes only (TW-678): the trail
            # proves the stage ran and what it concluded, and
            # holds nothing a bronze dump could identify anyone
            # from beyond the hashes the document row already has.
            self.store.add_signal(
                {
                    "session_id": session_id,
                    "signal_type": "secondary_identity_check",
                    "score": 1.0 if secondary_result.status == "passed" else 0.0,
                    "detail": {
                        "status": secondary_result.status,
                        "source_kind": secondary_result.source_kind,
                        "source_ref_hash": secondary_result.source_ref_hash,
                        "matched_fields": list(secondary_result.matched_fields),
                        "reasons": list(secondary_result.reasons),
                    },
                }
            )

        status = {"pass": "passed", "fail": "failed", "review": "review"}[
            verdict.status
        ]
        try:
            if verdict.status == "pass":
                # Consent is re-required at decision time, before
                # anything persists (design call 5, enforced): a
                # session that started before a withdrawal cannot
                # resurrect the standing the member revoked.
                consent = None
                try:
                    consent = require_consent(
                        self.store, user_id, session["document_type"]
                    )
                except ConsentRequired:
                    consent = None
                if consent is None:
                    verdict = Verdict("review", "low", ["consent_not_current"])
                    status = "review"
                else:
                    # The attestation inputs are validated before
                    # the first write: a pass carries a DOB by
                    # construction (the combiner), and this path
                    # never discovers otherwise mid-persist.
                    dob_date = date.fromisoformat(dob)
                    self.store.save_biometric(
                        {
                            "session_id": session_id,
                            "user_id": user_id,
                            "consent_id": consent["id"],
                            "template_encrypted": self.encryptor.encrypt(
                                json.dumps(list(embedding)).encode("utf-8")
                            ),
                            "template_key_ref": TEMPLATE_KEY_REF,
                            "anchor_hash": anchor,
                        }
                    )
                    self.store.upsert_anchor(anchor, user_id=user_id)
                    self.store.save_age_attestation(
                        {
                            "identity_anchor_hash": anchor,
                            "band": band,
                            "band_as_of": today,
                            "next_band_date": next_band_date_for(dob_date, today),
                            "engine_version": ENGINE_VERSION,
                            "computed_at": now,
                        }
                    )
                    self.store.set_verification_state(
                        user_id,
                        identity_anchor_hash=anchor,
                        status="verified",
                        current_tier=session["tier_requested"],
                        verified_at=now,
                        last_interaction_at=now,
                    )
            self.store.update_session(
                session_id,
                status=status,
                decision_by="engine",
                confidence_band=verdict.confidence_band,
                decided_at=now,
            )
        finally:
            # Destruction on decision, every outcome, and on any
            # failure inside the persist tail too: staged images
            # are wiped and the match payload is cleared; the
            # retention log records both as executed actions
            # (spec S5.7).
            self.staging.wipe(
                context.get("document_ref") or document["image_staging_ref"]
            )
            self.staging.wipe(ref)
            self.store.update_document(
                document["id"],
                match_payload_encrypted=None,
                match_payload_cleared_at=now,
                image_destroyed_at=now,
            )
            for action in ("destroy_document_image", "clear_document_payload"):
                self.store.add_retention_action(
                    {
                        "user_id": user_id,
                        "biometric_id": None,
                        "document_id": document["id"],
                        "action": action,
                        "trigger": "purpose_satisfied",
                        "determined_at": now,
                        "due_at": now,
                        "extended_due_at": None,
                        "executed_at": now,
                        "hold": False,
                        "hold_reason": None,
                        "notice_sent_at": None,
                        "tier_dropped_at": None,
                    }
                )
        self._context.pop(session_id, None)

        return VerdictView(
            verification_id=session_id,
            status=status,
            tier_granted=session["tier_requested"] if verdict.status == "pass" else 0,
            confidence_band=verdict.confidence_band,
            age_band=band if verdict.status == "pass" else None,
            decided_at=now,
        )

    # ------------------------------------------------------------ verdict API

    def get_verdict(self, session_id: str, user_id: str) -> VerdictView:
        # Ownership, the same check the submit endpoints enforce:
        # a verdict carries the member's age band and is readable
        # by its owner only.
        session = self._session(session_id, user_id)
        decided = session.get("decided_at")
        age_band = None
        if session["status"] == "passed":
            state = self.store.get_verification_state(session["user_id"])
            anchor = state.get("identity_anchor_hash") if state else None
            if anchor:
                attestation = self.store.get_age_attestation(anchor)
                if attestation:
                    age_band = attestation["band"]
        return VerdictView(
            verification_id=session_id,
            status=session["status"],
            tier_granted=(
                session["tier_requested"] if session["status"] == "passed" else 0
            ),
            confidence_band=session.get("confidence_band"),
            age_band=age_band,
            decided_at=decided,
        )

    def anchor_lookup(self, anchor_hash: str) -> dict:
        """Service-to-service (S5.6): booleans, never records."""
        row = self.store.get_anchor(anchor_hash)
        return {
            "exists": row is not None,
            "banned": bool(row and row["is_banned"]),
        }

    def sweep_staging(self) -> list[str]:
        """Destroy staged images past the TTL ceiling (S5.7).

        The pipeline destroys on decision; this bounds everything
        else (a stage that died mid-session, an abandoned flow).
        The service runs it on a schedule; returns the wiped refs.
        """
        return self.staging.sweep_expired(self.config.staging_ttl_hours)
