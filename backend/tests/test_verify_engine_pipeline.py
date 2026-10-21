"""TW-658 verification engine: consent service + pipeline (TDD RED).

The pipeline runs end to end here with the model boundary stubbed
by deterministic fakes: the five spec Section 5.2 stages execute,
verdicts and hashes persist into an in-memory store shaped exactly
like the vfy_ tables, staged images are destroyed on decision, and
the D3 capture flag makes production capture impossible while off.

Fixtures are family: Ama Serwaa (passport, b. 1990) passes; Kofi
Mensah is her duplicate-search decoy; a banned anchor is a refusal.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone

import pytest

from app.verify.benchmark.documents import mrz_check_digit
from app.verify.benchmark.stages import parse_mrz_lines
from app.verify.engine.config import EngineConfig
from app.verify.engine.consent import (
    ConsentRequired,
    consent_type_for,
    grant_consent,
    require_consent,
    withdraw_consent,
)
from app.verify.engine.crypto import AesGcmEncryptor
from app.verify.engine.errors import (
    CaptureDisabled,
    DocumentClassNotEnabled,
    UnknownSession,
)
from app.verify.engine.hashing import FieldHasher
from app.verify.engine.pipeline import VerificationPipeline
from app.verify.engine.ports import PipelinePorts
from app.verify.engine.staging import InMemoryStaging
from app.verify.engine.store import InMemoryVerifyStore

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
KEY = b"tw658-test-hmac-key"
ENC_KEY = bytes(range(32))

AMA_DOC = b"ama-doc-photo"
AMA_SELFIE = b"ama-selfie-photo"
KOFI_DOC = b"kofi-doc-photo"

# Embeddings keyed by image content hash, like the staging refs.
_VECTORS = {
    AMA_DOC: (1.0, 0.05, 0.0),
    AMA_SELFIE: (0.99, 0.10, 0.0),
    # Kofi's document carries Ama's portrait: the stolen-photo case,
    # the same face recycled across accounts (spec Section 5.2.5).
    KOFI_DOC: (1.0, 0.05, 0.0),
}


def _ref(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeOcr:
    def extract_text(self, image_ref: str) -> list[str]:
        return ["AMA SERWAA", "P12345678", "27 JUL 1990"]


class FakeMrz:
    def read(self, image_ref: str):
        doc, dob, expiry, personal = "P12345678", "900727", "301231", "A12345"
        line = (
            doc
            + str(mrz_check_digit(doc))
            + "UTO"
            + dob
            + str(mrz_check_digit(dob))
            + "F"
            + expiry
            + str(mrz_check_digit(expiry))
            + personal.ljust(14, "<")
        )
        line += str(mrz_check_digit(line[28:42]))
        composite = line[0:10] + line[13:20] + line[21:28] + line[28:43]
        line += str(mrz_check_digit(composite))
        return parse_mrz_lines(["P<UTO SERWAA<<AMA<<<<<<<<<<<<<<<<<<<<<", line])


class FakePdf417:
    def decode_fields(self, image_ref: str) -> dict[str, str]:
        return {}


class FakeDetect:
    def __init__(self, count: int = 1):
        self.count = count

    def detect(self, image_ref: str) -> list[tuple]:
        return [(0, 0, 10, 10)] * self.count


class FakeEmbed:
    def embed(self, image_path: str) -> tuple[float, ...]:
        from pathlib import Path

        data = Path(image_path).read_bytes()
        return _VECTORS.get(data, (0.0, 0.0, 1.0))


def _open_landmarks(nose_x: float = 0.5, eyes_open: bool = True):
    lms = [(0.5, 0.5, 0.0)] * 468

    def s(idx, x, y):
        lms[idx] = (x, y, 0.0)

    s(33, 0.35, 0.5)
    s(133, 0.45, 0.5)
    s(362, 0.55, 0.5)
    s(263, 0.65, 0.5)
    s(1, nose_x, 0.55)
    top = 0.47 if eyes_open else 0.495
    bottom = 0.53 if eyes_open else 0.505
    for idx, x in ((159, 0.40), (160, 0.42), (386, 0.60), (387, 0.62)):
        s(idx, x, top)
    for idx, x in ((145, 0.40), (144, 0.42), (374, 0.60), (373, 0.62)):
        s(idx, x, bottom)
    return lms


class FakeLandmarks:
    def landmarks(self, image_ref: str):
        # Frames are synthetic landmark sets in the pipeline tests:
        # the ref encodes which synthetic frame to return.
        return _open_landmarks()


class FakeLiveness:
    def __init__(self, prob: float = 0.97):
        self.prob = prob

    def bona_fide_probability(self, image_ref: str) -> float:
        return self.prob


def _ports(**overrides) -> PipelinePorts:
    ports = PipelinePorts(
        ocr=FakeOcr(),
        mrz=FakeMrz(),
        pdf417=FakePdf417(),
        face_detect=FakeDetect(),
        embed=FakeEmbed(),
        landmarks=FakeLandmarks(),
        liveness=FakeLiveness(),
    )
    for name, value in overrides.items():
        object.__setattr__(ports, name, value)
    return ports


def _pipeline(
    store=None, staging=None, config=None, ports=None
) -> VerificationPipeline:
    return VerificationPipeline(
        store=store or InMemoryVerifyStore(),
        staging=staging or InMemoryStaging(),
        hasher=FieldHasher(KEY),
        encryptor=AesGcmEncryptor(ENC_KEY),
        ports=ports or _ports(),
        config=config or EngineConfig(production_capture_enabled=True),
        rng=random.Random(7),
        clock=lambda: NOW,
    )


def _frames_for(challenges: list[str]):
    """Synthetic landmark frames satisfying any issued challenge set."""
    frames = [_open_landmarks()]
    frames.append(_open_landmarks(nose_x=0.36))  # turn one way
    frames.append(_open_landmarks(nose_x=0.64))  # turn the other way
    frames.append(_open_landmarks(eyes_open=False))  # blink
    frames.append(_open_landmarks())
    return frames


def _run_pass(pipe: VerificationPipeline, user_id: str = "u-ama"):
    grant_consent(pipe.store, user_id, "passport")
    session = pipe.start_session(user_id, "passport")
    pipe.submit_document(session.id, user_id, AMA_DOC)
    challenges = pipe.begin_liveness(session.id)
    verdict = pipe.submit_selfie(
        session.id, user_id, AMA_SELFIE, _frames_for(challenges)
    )
    return session, verdict


# ---------------------------------------------------------------- consent


class TestConsent:
    def test_type_mapping(self):
        assert consent_type_for("passport") == "biometric_capture"
        assert consent_type_for("drivers_license") == "biometric_capture"
        assert consent_type_for("school_id") == "school_id_biometric_capture"

    def test_grant_records_the_adopted_versions(self):
        store = InMemoryVerifyStore()
        consent = grant_consent(store, "u-ama", "passport")
        from app.verify.engine.config import (
            CONSENT_POLICY_VERSION,
            RETENTION_SCHEDULE_VERSION,
        )

        assert consent["policy_version"] == CONSENT_POLICY_VERSION
        assert consent["retention_schedule_version"] == RETENTION_SCHEDULE_VERSION
        assert consent["revoked_at"] is None

    def test_require_consent(self):
        store = InMemoryVerifyStore()
        with pytest.raises(ConsentRequired):
            require_consent(store, "u-ama", "passport")
        grant_consent(store, "u-ama", "drivers_license")
        require_consent(store, "u-ama", "passport")  # same consent class

    def test_wrong_class_consent_does_not_satisfy(self):
        store = InMemoryVerifyStore()
        grant_consent(store, "u-teen", "school_id")
        with pytest.raises(ConsentRequired):
            require_consent(store, "u-teen", "passport")

    def test_withdrawal_revokes_schedules_destruction_and_drops_tier(self):
        pipe = _pipeline()
        _run_pass(pipe)
        store = pipe.store
        assert store.verification_states["u-ama"]["current_tier"] == 2
        summary = withdraw_consent(store, "u-ama", at=NOW)
        assert summary["consents_revoked"] == 1
        state = store.verification_states["u-ama"]
        assert state["current_tier"] == 0
        assert state["status"] == "revoked"
        actions = [
            a
            for a in store.retention_actions
            if a["action"] == "destroy_biometric"
            and a["trigger"] == "verification_revocation"
        ]
        assert len(actions) == 1
        assert actions[0]["executed_at"] is None  # the retention job executes
        with pytest.raises(ConsentRequired):
            require_consent(store, "u-ama", "passport")


# --------------------------------------------------------------- pipeline


class TestCaptureGate:
    def test_capture_impossible_while_flag_off(self):
        pipe = _pipeline(config=EngineConfig())  # flag defaults OFF (D3)
        grant_consent(pipe.store, "u-ama", "passport")
        with pytest.raises(CaptureDisabled):
            pipe.start_session("u-ama", "passport")
        # No session, no staging, nothing captured as a side effect.
        assert pipe.store.sessions == {}
        assert pipe.staging.refs() == []

    def test_consent_required_before_capture(self):
        pipe = _pipeline()
        with pytest.raises(ConsentRequired):
            pipe.start_session("u-ama", "passport")

    def test_school_id_capture_is_phase_2(self):
        pipe = _pipeline()
        grant_consent(pipe.store, "u-teen", "school_id")
        with pytest.raises(DocumentClassNotEnabled):
            pipe.start_session("u-teen", "school_id")


class TestPassPath:
    def test_full_pass(self):
        store = InMemoryVerifyStore()
        staging = InMemoryStaging()
        pipe = _pipeline(store=store, staging=staging)
        session, verdict = _run_pass(pipe)

        assert verdict.status == "passed"
        assert verdict.tier_granted == 2
        assert verdict.confidence_band == "high"
        assert verdict.age_band == "age_21_plus"

        # Images destroyed on decision: staging holds nothing.
        assert staging.refs() == []

        # The document row carries hashes only; the match payload was
        # cleared at decision and the image destruction is stamped.
        doc = store.documents[0]
        hasher = FieldHasher(KEY)
        assert doc["name_hash"] == hasher.field_hash("AMA SERWAA")
        assert doc["dob_hash"] == hasher.field_hash("1990-07-27")
        assert doc["doc_number_hash"] == hasher.field_hash("P12345678")
        assert doc["match_payload_encrypted"] is None
        assert doc["match_payload_cleared_at"] == NOW
        assert doc["image_destroyed_at"] == NOW

        # The template persists encrypted, never as raw floats.
        bio = store.biometrics[0]
        assert bio["destroyed_at"] is None
        assert bio["template_encrypted"] != b""
        plain = pipe.encryptor.decrypt(bio["template_encrypted"])
        assert b"0.99" in plain  # the selfie embedding, inside the envelope

        # Signal trail: the verdict can be re-derived from vfy_signals.
        types = {s["signal_type"] for s in store.signals}
        assert {"match_score", "mrz_check", "liveness_score", "velocity"} <= types

        # Attestation + state: band only, tier live.
        att = store.age_attestations[0]
        assert att["band"] == "age_21_plus"
        assert att["next_band_date"] is None
        state = store.verification_states["u-ama"]
        assert state["status"] == "verified" and state["current_tier"] == 2

        # Retention log: the document image destruction is recorded
        # as an executed action (the legal destruction log).
        actions = {(a["action"], a["trigger"]) for a in store.retention_actions}
        assert ("destroy_document_image", "purpose_satisfied") in actions
        assert ("clear_document_payload", "purpose_satisfied") in actions
        for a in store.retention_actions:
            assert a["executed_at"] == NOW

    def test_verdict_view_is_verdict_only(self):
        pipe = _pipeline()
        session, _ = _run_pass(pipe)
        view = pipe.get_verdict(session.id)
        assert set(view.as_dict()) == {
            "verification_id",
            "status",
            "tier_granted",
            "confidence_band",
            "age_band",
            "decided_at",
        }

    def test_anchor_lookup_answers_booleans_only(self):
        pipe = _pipeline()
        _run_pass(pipe)
        hasher = FieldHasher(KEY)
        anchor = hasher.anchor_hash("AMA SERWAA", "1990-07-27", "P12345678")
        assert pipe.anchor_lookup(anchor) == {"exists": True, "banned": False}
        assert pipe.anchor_lookup("0" * 64) == {"exists": False, "banned": False}


class TestReviewAndFailPaths:
    def test_banned_anchor_is_refused_and_no_template_persists(self):
        store = InMemoryVerifyStore()
        hasher = FieldHasher(KEY)
        anchor = hasher.anchor_hash("AMA SERWAA", "1990-07-27", "P12345678")
        store.upsert_anchor(anchor, user_id="u-someone-else", is_banned=True)
        pipe = _pipeline(store=store)
        session, verdict = _run_pass(pipe, user_id="u-new")
        assert verdict.status == "failed"
        assert store.biometrics == []
        assert store.verification_states.get("u-new", {}).get("current_tier", 0) == 0

    def test_duplicate_face_routes_to_review(self):
        store = InMemoryVerifyStore()
        pipe = _pipeline(store=store)
        _run_pass(pipe, user_id="u-ama")
        # Kofi presents Ama's selfie against his own session: the
        # face already stands behind u-ama's verified identity.
        grant_consent(store, "u-kofi", "passport")
        session = pipe.start_session("u-kofi", "passport")
        pipe.submit_document(session.id, "u-kofi", KOFI_DOC)
        challenges = pipe.begin_liveness(session.id)
        verdict = pipe.submit_selfie(
            session.id, "u-kofi", AMA_SELFIE, _frames_for(challenges)
        )
        assert verdict.status == "review"
        dup = [s for s in store.signals if s["signal_type"] == "duplicate_hit"]
        assert dup and dup[-1]["session_id"] == session.id

    def test_mid_similarity_routes_to_review(self):
        class MidEmbed(FakeEmbed):
            def embed(self, image_path: str):
                from pathlib import Path

                if Path(image_path).read_bytes() == AMA_SELFIE:
                    return (0.5, 0.86, 0.0)  # cosine ~0.545: review band
                return super().embed(image_path)

        pipe = _pipeline(ports=_ports(embed=MidEmbed()))
        _, verdict = _run_pass(pipe)
        assert verdict.status == "review"

    def test_no_match_fails(self):
        class FarEmbed(FakeEmbed):
            def embed(self, image_path: str):
                from pathlib import Path

                if Path(image_path).read_bytes() == AMA_SELFIE:
                    return (0.0, 0.0, 1.0)
                return super().embed(image_path)

        pipe = _pipeline(ports=_ports(embed=FarEmbed()))
        _, verdict = _run_pass(pipe)
        assert verdict.status == "failed"

    def test_images_destroyed_on_review_and_fail_too(self):
        class FarEmbed(FakeEmbed):
            def embed(self, image_path: str):
                from pathlib import Path

                if Path(image_path).read_bytes() == AMA_SELFIE:
                    return (0.0, 0.0, 1.0)
                return super().embed(image_path)

        staging = InMemoryStaging()
        pipe = _pipeline(staging=staging, ports=_ports(embed=FarEmbed()))
        _run_pass(pipe)
        assert staging.refs() == []
        assert pipe.store.documents[0]["image_destroyed_at"] == NOW

    def test_unknown_session(self):
        pipe = _pipeline()
        with pytest.raises(UnknownSession):
            pipe.get_verdict("no-such-session")

    def test_session_belongs_to_its_user(self):
        pipe = _pipeline()
        grant_consent(pipe.store, "u-ama", "passport")
        session = pipe.start_session("u-ama", "passport")
        with pytest.raises(PermissionError):
            pipe.submit_document(session.id, "u-kofi", AMA_DOC)


class TestStaging:
    def test_file_staging_roundtrip_and_wipe(self, tmp_path):
        from app.verify.engine.staging import EncryptedFileStaging

        staging = EncryptedFileStaging(tmp_path, AesGcmEncryptor(ENC_KEY))
        ref = staging.put(b"ama-bytes")
        assert staging.get(ref) == b"ama-bytes"
        # On disk it is ciphertext, not the image.
        assert b"ama-bytes" not in (tmp_path / ref).read_bytes()
        staging.wipe(ref)
        assert staging.refs() == []
        with pytest.raises(KeyError):
            staging.get(ref)


class TestCrypto:
    def test_aes_gcm_roundtrip_and_tamper(self):
        enc = AesGcmEncryptor(ENC_KEY)
        blob = enc.encrypt(b"template-bytes")
        assert blob != b"template-bytes"
        assert enc.decrypt(blob) == b"template-bytes"
        with pytest.raises(Exception):
            enc.decrypt(blob[:-1] + b"\x00")
