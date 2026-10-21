"""TW-671 Door Pass biometric re-scope: prototype transaction tests.

The door transaction runs end to end here against the TW-658
engine's primitives (face bands, cosine match, active liveness
challenges, consent and capture gates) with the model boundary
stubbed by deterministic fakes, exactly as the TW-658 pipeline
tests stub it. Every member is a consented test fixture; the D3
production capture flag stays off for the whole file.

Fixture family: Ama Serwaa is the enrolled 21+ member; a stranger
selfie is the no-match case; Kofi Mensah is the banned anchor.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.door.errors import (
    ClientFlowSelection,
    DoorCaptureDisabled,
    DoorConsentRequired,
    DoorRefused,
    TransactionStateError,
    UnknownTransaction,
)
from app.door.profiles import ProfileResolver, build_fixture_profiles
from app.door.service import DoorPassService
from app.door.store import InMemoryDoorStore, grant_door_consent
from app.verify.benchmark.stages import StageUnavailable
from app.verify.engine.config import EngineConfig
from app.verify.engine.crypto import AesGcmEncryptor
from app.verify.engine.ports import PipelinePorts
from app.verify.engine.staging import InMemoryStaging
from app.verify.engine.store import InMemoryVerifyStore

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
ENC_KEY = bytes(range(32))

AMA_SELFIE = b"ama-door-selfie"
STRANGER_SELFIE = b"stranger-door-selfie"
REVIEW_SELFIE = b"review-door-selfie"

_VECTORS = {
    AMA_SELFIE: (0.99, 0.10, 0.0),
    STRANGER_SELFIE: (0.0, 0.0, 1.0),
    REVIEW_SELFIE: (0.5, 0.866, 0.0),
}
AMA_TEMPLATE = (1.0, 0.05, 0.0)


class FakeDetect:
    def __init__(self, count: int = 1):
        self.count = count

    def detect(self, image_ref: str) -> list[tuple]:
        return [(0, 0, 10, 10)] * self.count


class FakeEmbed:
    def embed(self, image_path: str) -> tuple[float, ...]:
        from pathlib import Path

        data = Path(image_path).read_bytes()
        return _VECTORS.get(data, (0.0, 1.0, 0.0))


class FakeLiveness:
    def __init__(self, prob: float = 0.97, unavailable: bool = False):
        self.prob = prob
        self.unavailable = unavailable

    def bona_fide_probability(self, image_ref: str) -> float:
        if self.unavailable:
            raise StageUnavailable("liveness model not installed")
        return self.prob


class _Unused:
    def __getattr__(self, name):
        def _raise(*args, **kwargs):
            raise AssertionError("door transaction must not call this port")

        return _raise


def _ports(**overrides) -> PipelinePorts:
    ports = PipelinePorts(
        ocr=_Unused(),
        mrz=_Unused(),
        pdf417=_Unused(),
        face_detect=FakeDetect(),
        embed=FakeEmbed(),
        landmarks=_Unused(),
        liveness=FakeLiveness(),
    )
    for name, value in overrides.items():
        object.__setattr__(ports, name, value)
    return ports


def _landmarks(nose_x: float = 0.5, eyes_open: bool = True):
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


def _answering_frames():
    """Frames that answer any challenge subset the engine issues."""
    return [
        _landmarks(nose_x=0.2),
        _landmarks(nose_x=0.8),
        _landmarks(eyes_open=True),
        _landmarks(eyes_open=False),
    ]


def _still_frames():
    """One neutral frame: answers no challenge at all."""
    return [_landmarks()]


def _seed_member(
    verify_store: InMemoryVerifyStore,
    encryptor: AesGcmEncryptor,
    user_id: str = "ama",
    *,
    band: str = "age_21_plus",
    banned: bool = False,
    template=AMA_TEMPLATE,
):
    anchor = f"anchor-{user_id}"
    verify_store.upsert_anchor(anchor, user_id=user_id, is_banned=banned)
    verify_store.save_biometric(
        {
            "session_id": f"fixture-session-{user_id}",
            "user_id": user_id,
            "consent_id": f"fixture-consent-{user_id}",
            "template_encrypted": encryptor.encrypt(
                json.dumps(list(template)).encode("utf-8")
            ),
            "template_key_ref": "env:VFY_TEMPLATE_KEY_B64",
            "anchor_hash": anchor,
        }
    )
    verify_store.save_age_attestation(
        {
            "identity_anchor_hash": anchor,
            "band": band,
            "band_as_of": NOW.date(),
            "next_band_date": None,
            "engine_version": "tw-verify-engine-1.0.0",
            "computed_at": NOW,
        }
    )
    verify_store.set_verification_state(
        user_id,
        identity_anchor_hash=anchor,
        status="verified",
        current_tier=2,
        verified_at=NOW,
        last_interaction_at=NOW,
    )
    return anchor


def _service(
    *,
    ports=None,
    config=None,
    door_store=None,
    verify_store=None,
    staging=None,
    with_consent: bool = True,
    fixture: bool = True,
    user_id: str = "ama",
    **member_kwargs,
):
    encryptor = AesGcmEncryptor(ENC_KEY)
    verify_store = verify_store or InMemoryVerifyStore()
    door_store = door_store or InMemoryDoorStore()
    staging = staging or InMemoryStaging()
    _seed_member(verify_store, encryptor, user_id, **member_kwargs)
    door_store.register_enrollment(user_id, fixture=fixture)
    if with_consent:
        grant_door_consent(door_store, user_id, at=NOW)
    service = DoorPassService(
        door_store=door_store,
        verify_store=verify_store,
        staging=staging,
        encryptor=encryptor,
        ports=ports or _ports(),
        config=config or EngineConfig(),  # D3 flag OFF by default
        resolver=ProfileResolver(build_fixture_profiles(now=NOW)),
        clock=lambda: NOW,
        rng=random.Random(7),
    )
    return service, door_store, staging


def _reliance_txn(service, state="AZ", purpose="alcohol_service", user_id="ama"):
    return service.create_transaction(
        venue_state=state, purpose=purpose, user_id=user_id
    )


# ---------------------------------------------------------------- match


def test_biometric_match_is_valid_reliance_in_full_state():
    service, door_store, staging = _service()
    txn = _reliance_txn(service)
    assert txn.flow_mode == "reliance_biometric"
    assert txn.challenges, "reliance capture must issue liveness challenges"
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "valid"
    external = result.as_dict()
    assert external["age_result"] == "age_21_plus"
    assert "failure_class" not in external
    # The staged selfie is destroyed on decision, every outcome.
    assert staging.refs() == []
    # Exactly one evidence record, references and hashes only.
    (scan,) = door_store.scans
    assert scan["result"] == "valid"
    assert scan["flow_mode"] == "reliance_biometric"
    assert scan["venue_state_code"] == "AZ"
    assert scan["state_profile_version"] == 1
    assert scan["match_band"] == "match"
    assert scan["biometric_transaction_ref_hash"]
    assert scan["enrollment_ref_hash"] != "anchor-ama"


def test_no_match_is_not_valid():
    service, door_store, staging = _service()
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, STRANGER_SELFIE, _answering_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "face_no_match"
    assert staging.refs() == []


def test_review_band_match_fails_closed_at_the_door():
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, REVIEW_SELFIE, _answering_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "face_review_band"


def test_liveness_failure_is_not_valid():
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, AMA_SELFIE, _still_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "liveness_failed"


def test_weak_passive_liveness_never_manufactures_a_pass():
    service, door_store, _ = _service(ports=_ports(liveness=FakeLiveness(0.2)))
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "liveness_passive_weak"


def test_banned_anchor_is_refused():
    service, door_store, _ = _service(banned=True)
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "banned_anchor"


def test_model_unavailable_fails_closed_to_unavailable():
    service, door_store, staging = _service(
        ports=_ports(liveness=FakeLiveness(unavailable=True))
    )
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "unavailable"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "stage_unavailable"
    assert staging.refs() == []


# ------------------------------------------------------- profile gating


def test_kill_state_refuses_transaction_creation():
    service, door_store, _ = _service()
    with pytest.raises(DoorRefused) as excinfo:
        service.create_transaction(
            venue_state="TX", purpose="alcohol_service", user_id="ama"
        )
    assert "kill" in str(excinfo.value)
    assert door_store.scans == []


def test_disabled_state_refuses_transaction_creation():
    # WA fixture carries a full profile with an incomplete approval
    # chain (no justynn_enable record): resolver returns disabled.
    service, _, _ = _service()
    with pytest.raises(DoorRefused):
        service.create_transaction(
            venue_state="WA", purpose="alcohol_service", user_id="ama"
        )


def test_partial_state_refuses_reliance_and_never_blends():
    service, door_store, _ = _service()
    with pytest.raises(DoorRefused):
        service.create_transaction(
            venue_state="CO", purpose="alcohol_service", user_id="ama"
        )
    # The lane flow exists in CO, and it carries no biometric step.
    txn = service.create_transaction(
        venue_state="CO", purpose="entry", user_id="ama"
    )
    assert txn.flow_mode == "lane_qr_photo"
    assert txn.challenges == []
    with pytest.raises(TransactionStateError):
        service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    result = service.complete_lane(txn.id)
    assert result.result == "valid"
    assert result.as_dict()["age_result"] is None
    (scan,) = door_store.scans
    assert scan["flow_mode"] == "lane_qr_photo"
    assert scan["legal_effect"] == "house_rule"
    assert scan["biometric_transaction_ref_hash"] is None
    assert "lane_not_identification" in scan["claim_keys"]
    assert "lane_physical_id_required" in scan["claim_keys"]


def test_client_supplied_flow_mode_is_rejected():
    service, _, _ = _service()
    with pytest.raises(ClientFlowSelection):
        service.create_transaction(
            venue_state="CO",
            purpose="entry",
            user_id="ama",
            flow_mode="reliance_biometric",
        )


def test_full_state_entry_also_rides_the_biometric_flow():
    service, _, _ = _service()
    txn = service.create_transaction(venue_state="CA", purpose="entry", user_id="ama")
    assert txn.flow_mode == "reliance_biometric"
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "valid"


# ---------------------------------------------------------------- consent


def test_missing_door_consent_refuses_reliance():
    service, _, _ = _service(with_consent=False)
    with pytest.raises(DoorConsentRequired):
        _reliance_txn(service)


def test_onboarding_consent_alone_does_not_satisfy_the_door():
    # A vfy biometric_capture consent is a different instrument from
    # the separate explicit door capture consent (TW-647 S6.3).
    from app.verify.engine.consent import grant_consent

    service, door_store, _ = _service(with_consent=False)
    grant_consent(service.verify_store, "ama", "passport")
    with pytest.raises(DoorConsentRequired):
        _reliance_txn(service)


def test_consent_withdrawn_mid_transaction_refuses_capture():
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    door_store.revoke_door_consents("ama", at=NOW + timedelta(minutes=1))
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "consent_not_current"


# ------------------------------------------------------------- D3 hard gate


def test_production_enrollment_is_refused_while_capture_flag_off():
    service, _, _ = _service(fixture=False)
    assert service.config.production_capture_enabled is False
    with pytest.raises(DoorCaptureDisabled):
        _reliance_txn(service)


def test_under_21_band_cannot_transact_for_service():
    service, _, _ = _service(band="age_18_20")
    with pytest.raises(DoorRefused):
        _reliance_txn(service)


# ------------------------------------------------------------- evidence


def test_evidence_record_carries_no_biometric_material():
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    (scan,) = door_store.scans
    forbidden = ("template", "image", "score", "name", "dob", "birth", "photo")
    for key in scan:
        assert not any(word in key for word in forbidden), key
    # CA's defense is proven after the fact: the record must be able
    # to prove the transaction happened under a named profile.
    for required in (
        "state_profile_hash",
        "proof_bundle_key",
        "authorization_status_at_scan",
        "venue_state_code",
        "purpose",
        "created_at",
    ):
        assert scan[required], required


def test_unknown_transaction_is_an_honest_error():
    service, _, _ = _service()
    with pytest.raises(UnknownTransaction):
        service.submit_selfie("nope", AMA_SELFIE, _answering_frames())


def test_a_finalized_transaction_cannot_be_replayed():
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    with pytest.raises(TransactionStateError):
        service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert len(door_store.scans) == 1


def test_profile_hash_binds_the_evidence_to_the_reviewed_version():
    resolver = ProfileResolver(build_fixture_profiles(now=NOW))
    decision = resolver.resolve("NC", NOW)
    assert decision.mode == "full"
    assert decision.flow_for("alcohol_service") == "reliance_biometric"
    assert len(decision.profile_hash) == 64
    assert decision.profile_hash != resolver.resolve("AZ", NOW).profile_hash


def test_a_tampered_profile_fails_integrity_and_disables():
    profiles = build_fixture_profiles(now=NOW)
    profile, approvals = profiles["CA"]
    object.__setattr__(profile, "diego_score", 41)
    resolver = ProfileResolver(profiles)
    decision = resolver.resolve("CA", NOW)
    assert decision.mode == "disabled"
    assert decision.reason == "profile_integrity"
