"""The biometric door transaction (TW-671 prototype).

One transaction, one purpose, one server derived flow:

- The venue location's state resolves through the TW-657 profile
  resolver at creation and again at finalization. The client never
  selects a flow; a supplied flow mode is an error.
- reliance_biometric (FULL states): the member takes their own
  picture at the door. The capture is staged inside the service,
  embedded at the TW-658 model boundary, answered against the
  randomized active liveness challenges the transaction issued,
  and matched 1 to 1 against the enrollment template the TW-658
  pipeline persisted at onboarding (document portrait vs the ID
  the member uploaded, sealed encrypted in the verify store).
  The venue receives a verdict and an age result, nothing more.
  The service never receives or stores the template, a document
  image, a name, or a birthdate. The raw match score is computed
  inside this boundary and preserved only in the server side
  evidence record, with the threshold in force and the engine
  version, because TW-672 Section 4.3 item 5 makes that preserved
  record the defense package; the score never crosses to a venue
  facing surface (TW-657 Section 2.1 governs that boundary).
- lane_qr_photo (PARTIAL states): the surviving TW-647 lane. No
  biometric step exists in this flow, and a selfie submitted to a
  lane transaction is refused: the flows never blend, and a failed
  reliance transaction never degrades into the lane for the same
  service decision.
- Fail closed everywhere: a kill or disabled profile refuses at
  creation; an unavailable model stage, a profile change mid
  transaction, or any doubt at finalization resolves to
  unavailable or not valid, and the venue falls back to physical
  ID outside Door Pass. There is no offline verification.

D3 hard gate: the prototype transacts on consented fixture
enrollments only. A non-fixture enrollment is refused whatever the
environment says, and the engine config's production capture flag
is read from TW-658's EngineConfig, never reinvented here.

Every finalized transaction writes its evidence record (the dps_
scan shape) before it answers: profile id, version and hash,
purpose, flow, claim keys, proof bundle key, authorization status
at scan, match band, the server side match score and threshold,
the engine version, venue identity and the venue agreement
reference the transaction ran under, the session initiation
record, and two one way reference hashes. The evidentiary
identity fields are snapshotted on the transaction at creation,
so an abort path record still names the profile and pack the
transaction was created under. California's defense is proven
after the fact, so the record is a product requirement, not a
log line.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.verify.benchmark.stages import StageUnavailable
from app.verify.engine.config import ENGINE_VERSION, EngineConfig
from app.verify.engine.crypto import Encryptor
from app.verify.engine.face import band_for_similarity, cosine_similarity
from app.verify.engine.liveness import evaluate_challenges, generate_challenges
from app.verify.engine.ports import PipelinePorts
from app.verify.engine.staging import Staging
from app.verify.engine.store import VerifyStore

from .errors import (
    ClientFlowSelection,
    DoorCaptureDisabled,
    DoorConsentRequired,
    DoorRefused,
    TransactionStateError,
    UnknownTransaction,
)
from .profiles import (
    LANE_QR_PHOTO,
    RELIANCE_BIOMETRIC,
    ProfileResolver,
)
from .store import InMemoryDoorStore, active_door_consent

PURPOSES = ("entry", "alcohol_service")

# The staff actions TW-672 Section 4.3 item 7 names: what the
# venue did with the result, recorded on the scan at the time.
STAFF_ACTIONS = ("served", "admitted", "fell_back_to_physical_id", "denied")


@dataclass(frozen=True)
class TransactionView:
    id: str
    flow_mode: str
    purpose: str
    venue_state: str
    venue_id: str
    profile_version: int | None
    profile_hash: str
    challenges: list = field(default_factory=list)


@dataclass(frozen=True)
class DoorResult:
    """One finalized transaction. `failure_class` is internal only:
    the external shape renders every failure identically (TW-647
    Section 3.2), so as_dict never carries it. The external shape
    never carries the match score, the threshold, the engine
    version, or the venue identity either: those live in the
    server side evidence record only."""

    result: str  # valid | not_valid | unavailable
    flow_mode: str
    purpose: str
    failure_class: str | None
    decision_code: str  # valid | not_valid | review | unavailable
    scan_id: str
    claim_keys: tuple = ()
    age_result: str | None = None
    legal_effect: str = "none"
    profile_version: int | None = None

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "flow_mode": self.flow_mode,
            "purpose": self.purpose,
            "age_result": self.age_result,
            "legal_effect": self.legal_effect,
            "claim_keys": list(self.claim_keys),
            "profile_version": self.profile_version,
        }


class DoorPassService:
    def __init__(
        self,
        *,
        door_store: InMemoryDoorStore,
        verify_store: VerifyStore,
        staging: Staging,
        encryptor: Encryptor,
        ports: PipelinePorts,
        config: EngineConfig,
        resolver: ProfileResolver,
        clock=None,
        rng: random.Random | None = None,
    ):
        self.door_store = door_store
        self.verify_store = verify_store
        self.staging = staging
        self.encryptor = encryptor
        self.ports = ports
        self.config = config
        self.resolver = resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = rng or random.Random()

    # ------------------------------------------------------- member state

    def _member_facts(self, user_id: str) -> dict:
        """Standing, anchor, band, and template, read from the
        verify store: the door adds no identity state of its own."""
        state = self.verify_store.get_verification_state(user_id)
        anchor = state.get("identity_anchor_hash") if state else None
        attestation = self.verify_store.get_age_attestation(anchor) if anchor else None
        anchor_row = self.verify_store.get_anchor(anchor) if anchor else None
        templates = self.verify_store.list_live_biometrics(user_id)
        return {
            "state": state,
            "anchor": anchor,
            "band": attestation["band"] if attestation else None,
            "banned": bool(anchor_row and anchor_row["is_banned"]),
            "template_row": templates[0] if templates else None,
        }

    def _check_member_for_creation(self, user_id: str) -> dict:
        enrollment = self.door_store.get_enrollment(user_id)
        if enrollment is None:
            raise DoorRefused(f"member {user_id} has no door enrollment")
        if not enrollment["fixture"]:
            # D3: prototype scope is fixtures only, whatever the
            # flag says; a production enrollment has no path here.
            raise DoorCaptureDisabled(
                "non-fixture enrollment refused: the D3 production "
                "capture flag stays off until counsel's written "
                "review is filed to TW-654"
            )
        facts = self._member_facts(user_id)
        state = facts["state"]
        if (
            not state
            or state.get("status") != "verified"
            or (state.get("current_tier") or 0) < 2
        ):
            raise DoorRefused("member standing is not verified T2")
        if facts["band"] != "age_21_plus":
            raise DoorRefused("member age band is not age_21_plus")
        if facts["banned"]:
            # Spec Section 3 step 3: standing at creation includes
            # the ban. A banned member never gets a transaction;
            # the match time check stays as defense in depth for a
            # ban that lands mid transaction.
            raise DoorRefused("member identity anchor is banned")
        if facts["template_row"] is None:
            raise DoorRefused("member has no live enrollment template")
        return facts

    def _agreement_current(self, txn: dict) -> bool:
        """The finalization re-check of the venue agreement binding
        (spec Section 8.3): the agreement the transaction was
        created under must still be registered and active for that
        venue."""
        agreement = self.door_store.get_venue_agreement(txn["venue_agreement_ref"])
        return (
            agreement is not None
            and agreement["status"] == "active"
            and agreement["venue_id"] == txn["venue_id"]
            and agreement["state_code"] == txn["venue_state"]
        )

    # ------------------------------------------------------------ creation

    def create_transaction(
        self,
        *,
        venue_state: str,
        venue_id: str,
        purpose: str,
        user_id: str,
        venue_agreement_ref: str | None = None,
        session_initiated_by: str = "member_device",
        staff_ref: str | None = None,
        flow_mode: str | None = None,
    ) -> TransactionView:
        if flow_mode is not None:
            raise ClientFlowSelection(
                "flow is server derived from the state profile, never client selected"
            )
        if purpose not in PURPOSES:
            raise ValueError(f"unknown door purpose {purpose!r}")
        now = self.clock()
        decision = self.resolver.resolve(venue_state, now)
        if decision.mode == "kill":
            raise DoorRefused(f"door pass is not an objective in {venue_state} (kill)")
        if decision.mode == "disabled":
            raise DoorRefused(
                f"state profile for {venue_state} is disabled: {decision.reason}"
            )
        flow = decision.flow_for(purpose)
        if flow is None:
            raise DoorRefused(
                f"purpose {purpose} is not enabled in {venue_state} "
                f"under profile v{decision.profile_version}"
            )
        facts = self._check_member_for_creation(user_id)
        if flow == RELIANCE_BIOMETRIC and decision.venue_agreement_required:
            # The record must be able to prove the transaction ran
            # at a venue, under that venue's agreement (TW-672
            # Section 4.3 item 1; in NC the seller agent agreement
            # is the registration prong itself, spec Section 8.3).
            if venue_agreement_ref is None:
                raise DoorRefused(
                    f"reliance in {venue_state} requires a venue "
                    "agreement reference for the venue"
                )
            candidate = {
                "venue_id": venue_id,
                "venue_state": venue_state,
                "venue_agreement_ref": venue_agreement_ref,
            }
            if not self._agreement_current(candidate):
                raise DoorRefused(
                    f"venue agreement {venue_agreement_ref!r} is not "
                    f"registered and active for venue {venue_id!r}"
                )
        if active_door_consent(self.door_store, user_id) is None:
            raise DoorConsentRequired(
                "no current door capture consent for this member "
                "under the adopted biometric policy"
            )
        challenges: list = []
        if flow == RELIANCE_BIOMETRIC:
            challenges = generate_challenges(
                self.rng, self.config.liveness_challenge_count
            )
        anchor = facts["anchor"]
        # Snapshot the evidentiary identity at creation: the
        # profile, pack, bundle, authorization status, legal
        # effect, venue, and agreement this transaction runs under.
        # Finalization reads the snapshot, so an abort path record
        # still names what the transaction was created under, even
        # when the profile has since been downgraded or disabled.
        row = self.door_store.create_transaction(
            {
                "user_id": user_id,
                "venue_state": venue_state,
                "venue_id": venue_id,
                "venue_agreement_ref": venue_agreement_ref,
                "session_initiated_by": session_initiated_by,
                "session_staff_ref": staff_ref,
                "purpose": purpose,
                "flow_mode": flow,
                "profile_version": decision.profile_version,
                "profile_hash": decision.profile_hash,
                "claim_keys": list(decision.claim_key_set),
                "legal_effect": (
                    decision.entry_legal_effect
                    if purpose == "entry"
                    else decision.alcohol_service_legal_effect
                ),
                "contract_pack_key": (
                    f"{decision.contract_pack_key}:{venue_id}"
                    if decision.contract_pack_key
                    else None
                ),
                "proof_bundle_key": decision.proof_bundle_key,
                "authorization_status": decision.authorization_status,
                "enrollment_ref_hash": (
                    self._ref_hash("enrollment", anchor) if anchor else None
                ),
                "enrollment_auth_summary_ref": (
                    self._ref_hash("enrollment-auth", anchor) if anchor else None
                ),
                "status": (
                    "awaiting_capture"
                    if flow == RELIANCE_BIOMETRIC
                    else "awaiting_presentation"
                ),
                "challenges": challenges,
                "created_at": now,
            }
        )
        self.door_store.update_transaction(
            row["id"],
            biometric_ref_hash=(
                self._ref_hash("door-txn", row["id"], anchor or "")
                if flow == RELIANCE_BIOMETRIC
                else None
            ),
        )
        return TransactionView(
            id=row["id"],
            flow_mode=flow,
            purpose=purpose,
            venue_state=venue_state,
            venue_id=venue_id,
            profile_version=decision.profile_version,
            profile_hash=decision.profile_hash,
            challenges=challenges,
        )

    # ------------------------------------------------------------ evidence

    def _finalize(
        self,
        txn: dict,
        *,
        result: str,
        failure_class: str | None,
        decision_code: str,
        match_band: str | None = None,
        match_score: float | None = None,
        match_threshold: float | None = None,
        liveness_active_ok: bool | None = None,
        age_result: str | None = None,
    ) -> DoorResult:
        claim_keys = tuple(txn["claim_keys"])
        scan = self.door_store.add_scan(
            {
                "transaction_id": txn["id"],
                "venue_state_code": txn["venue_state"],
                "venue_id": txn["venue_id"],
                "venue_agreement_ref": txn["venue_agreement_ref"],
                "session_initiated_by": txn["session_initiated_by"],
                "session_staff_ref": txn["session_staff_ref"],
                "state_profile_id": (f"{txn['venue_state']}-v{txn['profile_version']}"),
                "state_profile_version": txn["profile_version"],
                "state_profile_hash": txn["profile_hash"],
                "purpose": txn["purpose"],
                "flow_mode": txn["flow_mode"],
                "legal_effect": txn["legal_effect"],
                "result": result,
                "failure_class": failure_class,
                "decision_code": decision_code,
                "match_band": match_band,
                # Server side evidence only (TW-672 Section 4.3
                # item 5): the preserved record carries the score,
                # the threshold in force, and the engine version.
                # None of the three ever crosses to a venue surface.
                "match_score": match_score,
                "match_threshold": match_threshold,
                "engine_version": ENGINE_VERSION,
                "liveness_active_ok": liveness_active_ok,
                "biometric_transaction_ref_hash": txn["biometric_ref_hash"],
                "enrollment_ref_hash": txn["enrollment_ref_hash"],
                "enrollment_auth_summary_ref": txn["enrollment_auth_summary_ref"],
                "claim_keys": list(claim_keys),
                "claim_key_set_hash": hashlib.sha256(
                    ",".join(sorted(claim_keys)).encode("utf-8")
                ).hexdigest(),
                "contract_pack_key": txn["contract_pack_key"],
                "proof_bundle_key": txn["proof_bundle_key"],
                "authorization_status_at_scan": txn["authorization_status"],
                # Recorded when the venue acts on the result, via
                # record_staff_action (TW-672 Section 4.3 item 7).
                "staff_action": None,
                "created_at": self.clock().isoformat(),
            }
        )
        self.door_store.update_transaction(txn["id"], status="finalized")
        return DoorResult(
            result=result,
            flow_mode=txn["flow_mode"],
            purpose=txn["purpose"],
            failure_class=failure_class,
            decision_code=decision_code,
            scan_id=scan["id"],
            claim_keys=claim_keys,
            age_result=age_result,
            legal_effect=txn["legal_effect"],
            profile_version=txn["profile_version"],
        )

    def record_staff_action(self, scan_id: str, action: str) -> dict:
        """Record what venue staff did with a result (served,
        admitted, fell back to physical ID, denied) on its scan
        record, at the time they did it."""
        if action not in STAFF_ACTIONS:
            raise ValueError(
                f"unknown staff action {action!r}; expected one of {STAFF_ACTIONS}"
            )
        scan = self.door_store.set_scan_staff_action(scan_id, action)
        if scan is None:
            raise ValueError(f"no door scan {scan_id!r}")
        return scan

    def _ref_hash(self, *parts: str) -> str:
        return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------- reliance

    def submit_selfie(
        self, transaction_id: str, image_bytes: bytes, frames: list
    ) -> DoorResult:
        txn = self.door_store.get_transaction(transaction_id)
        if txn is None:
            raise UnknownTransaction(f"no door transaction {transaction_id!r}")
        if txn["flow_mode"] != RELIANCE_BIOMETRIC:
            raise TransactionStateError(
                "a lane transaction has no biometric step; the flows never blend"
            )
        if txn["status"] != "awaiting_capture":
            raise TransactionStateError(
                f"transaction is {txn['status']}, not awaiting_capture"
            )
        now = self.clock()
        decision = self.resolver.resolve(txn["venue_state"], now)
        facts = self._member_facts(txn["user_id"])

        if decision.mode in ("kill", "disabled") or (
            decision.profile_hash != txn["profile_hash"]
        ):
            # A downgrade mid transaction aborts it (TW-657 S4.3).
            # The record finalizes from the creation snapshot, so it
            # still names the profile and pack it ran under.
            return self._finalize(
                txn,
                result="unavailable",
                failure_class="profile_changed",
                decision_code="unavailable",
            )
        if active_door_consent(self.door_store, txn["user_id"]) is None:
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="consent_not_current",
                decision_code="not_valid",
            )
        if txn["venue_agreement_ref"] is not None and not self._agreement_current(txn):
            # Spec Section 8.3: the agreement binding is re-checked
            # at scan finalization. No current agreement, no reliance
            # record for that venue.
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="agreement_not_current",
                decision_code="not_valid",
            )

        ref = self.staging.put(image_bytes)
        try:
            return self._run_match(
                txn,
                facts=facts,
                ref=ref,
                frames=frames,
                liveness_required=decision.liveness_required,
            )
        finally:
            # The door capture is destroyed on decision, every
            # outcome, exactly as the pipeline destroys on decision.
            self.staging.wipe(ref)

    def _run_match(
        self,
        txn: dict,
        *,
        facts: dict,
        ref: str,
        frames: list,
        liveness_required: bool,
    ) -> DoorResult:
        path = self.staging.local_path(ref)
        try:
            face_count = len(self.ports.face_detect.detect(path))
            embedding = self.ports.embed.embed(path) if face_count >= 1 else None
            passive = self.ports.liveness.bona_fide_probability(path)
        except StageUnavailable:
            return self._finalize(
                txn,
                result="unavailable",
                failure_class="stage_unavailable",
                decision_code="unavailable",
            )

        if facts["banned"]:
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="banned_anchor",
                decision_code="not_valid",
            )
        if face_count == 0:
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="no_face_captured",
                decision_code="not_valid",
            )
        if face_count > 1:
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="multiple_faces",
                decision_code="review",
            )

        template = json.loads(
            self.encryptor.decrypt(facts["template_row"]["template_encrypted"]).decode(
                "utf-8"
            )
        )
        similarity = cosine_similarity(embedding, tuple(template))
        band = band_for_similarity(similarity, self.config)
        challenge_results = evaluate_challenges(txn["challenges"], frames)
        active_ok = all(challenge_results.values())

        if band == "no_match":
            failure, code = "face_no_match", "not_valid"
        elif band == "review":
            failure, code = "face_review_band", "review"
        elif liveness_required and not active_ok:
            failure, code = "liveness_failed", "not_valid"
        elif passive is not None and passive < self.config.liveness_review_floor:
            # Passive liveness is one signal, never a sole gate in
            # the engine; at the door it routes to a review decision
            # code and fails closed for reliance, never a pass.
            failure, code = "liveness_passive_weak", "review"
        else:
            return self._finalize(
                txn,
                result="valid",
                failure_class=None,
                decision_code="valid",
                match_band=band,
                match_score=similarity,
                match_threshold=self.config.match_threshold,
                liveness_active_ok=active_ok,
                age_result="age_21_plus",
            )
        return self._finalize(
            txn,
            result="not_valid",
            failure_class=failure,
            decision_code=code,
            match_band=band,
            match_score=similarity,
            match_threshold=self.config.match_threshold,
            liveness_active_ok=active_ok,
        )

    # ----------------------------------------------------------------- lane

    def complete_lane(self, transaction_id: str) -> DoorResult:
        txn = self.door_store.get_transaction(transaction_id)
        if txn is None:
            raise UnknownTransaction(f"no door transaction {transaction_id!r}")
        if txn["flow_mode"] != LANE_QR_PHOTO:
            raise TransactionStateError(
                "a reliance transaction completes through its "
                "biometric step, not the lane path"
            )
        if txn["status"] != "awaiting_presentation":
            raise TransactionStateError(
                f"transaction is {txn['status']}, not awaiting_presentation"
            )
        decision = self.resolver.resolve(txn["venue_state"], self.clock())
        facts = self._member_facts(txn["user_id"])
        if decision.mode in ("kill", "disabled") or (
            decision.profile_hash != txn["profile_hash"]
        ):
            return self._finalize(
                txn,
                result="unavailable",
                failure_class="profile_changed",
                decision_code="unavailable",
            )
        if active_door_consent(self.door_store, txn["user_id"]) is None:
            # Spec Section 6 states the consent double check without
            # flow qualification: checked at creation and again at
            # finalization, on the lane exactly as on reliance. The
            # production lane shows a door photo per scan, which is
            # the artifact this consent governs.
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="consent_not_current",
                decision_code="not_valid",
            )
        state = facts["state"]
        if not state or state.get("status") != "verified" or facts["banned"]:
            return self._finalize(
                txn,
                result="not_valid",
                failure_class="standing_changed",
                decision_code="not_valid",
            )
        # The lane result is a member confirmation under the
        # venue's house rule. It is never an age result: age_result
        # stays None and the claim keys carry the non reliance
        # statements the profile was validated to hold.
        return self._finalize(
            txn,
            result="valid",
            failure_class=None,
            decision_code="valid",
        )
