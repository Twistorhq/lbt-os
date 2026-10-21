"""TW-671 Door Pass biometric re-scope: prototype transaction tests.

The door transaction runs end to end here against the TW-658
engine's primitives (face bands, cosine match, active liveness
challenges, consent and capture gates) with the model boundary
stubbed by deterministic fakes, exactly as the TW-658 pipeline
tests stub it. Every member is a consented test fixture; the D3
production capture flag stays off for the whole file.

Fixture family: Ama Serwaa is the enrolled 21+ member; a stranger
selfie is the no-match case; Kofi Mensah is the banned anchor.

State fixtures model the legally current facts: California is the
working FULL state; Arizona resolves disabled (DLLC authorization
still pending, and the secondary identity proofing handler the
4-101 device definition requires is not built, TW-672 Finding 2);
North Carolina authors no entry reliance at all, because
G.S. 18B-302(d)(3) is a sales only defense.
"""

from __future__ import annotations

import dataclasses
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
from app.door.profiles import (
    APPROVAL_KINDS,
    RELIANCE_BIOMETRIC,
    ApprovalRecord,
    ProfileResolver,
    author_profile,
    build_fixture_profiles,
)
from app.door.service import DoorPassService
from app.door.store import InMemoryDoorStore, grant_door_consent
from app.verify.benchmark.stages import StageUnavailable
from app.verify.engine.config import ENGINE_VERSION, EngineConfig
from app.verify.engine.crypto import AesGcmEncryptor
from app.verify.engine.face import cosine_similarity
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


# Venue fixtures: one venue per state used below, each FULL state
# venue holding a registered agreement reference (the seller agent
# agreement for NC; the venue agreement for AZ and CA).
_VENUES = {
    "AZ": ("venue-az-001", "agr-az-001"),
    "CA": ("venue-ca-001", "agr-ca-001"),
    "NC": ("venue-nc-001", "agr-nc-001"),
    "CO": ("venue-co-001", None),
    "NV": ("venue-nv-001", None),
    "TX": ("venue-tx-001", None),
    "WA": ("venue-wa-001", "agr-wa-001"),
}


def _register_default_agreements(door_store: InMemoryDoorStore) -> None:
    for state, (venue_id, agreement_ref) in _VENUES.items():
        if agreement_ref is not None:
            door_store.register_venue_agreement(
                agreement_ref, venue_id=venue_id, state_code=state
            )


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
    _register_default_agreements(door_store)
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


def _create(service, state, purpose, user_id="ama", **overrides):
    """Create a transaction at the state's fixture venue."""
    venue_id, agreement_ref = _VENUES[state]
    kwargs = {"venue_id": venue_id, "venue_agreement_ref": agreement_ref}
    kwargs.update(overrides)
    return service.create_transaction(
        venue_state=state, purpose=purpose, user_id=user_id, **kwargs
    )


def _reliance_txn(service, state="CA", purpose="alcohol_service", user_id="ama"):
    return _create(service, state, purpose, user_id)


def _chain_for(profile, *, skip=()):
    return [
        ApprovalRecord(
            kind,
            profile.canonical_hash,
            expires_at=(
                NOW + timedelta(days=365) if kind == "angela_signoff" else None
            ),
        )
        for kind in APPROVAL_KINDS
        if kind not in skip
    ]


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
    assert scan["venue_state_code"] == "CA"
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


def test_banned_anchor_is_refused_at_creation():
    # Rosa probe P4 / spec Section 3 step 3: standing, including the
    # ban, is checked at creation. A banned member never gets a
    # transaction, and no scan is written.
    service, door_store, _ = _service(banned=True)
    with pytest.raises(DoorRefused):
        _reliance_txn(service)
    assert door_store.scans == []
    assert door_store.transactions == {}


def test_ban_landing_mid_transaction_still_finalizes_not_valid():
    # Defense in depth behind the creation check: a ban that lands
    # after creation still bites at match time.
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    service.verify_store.upsert_anchor("anchor-ama", user_id="ama", is_banned=True)
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
        _create(service, "TX", "alcohol_service")
    assert "kill" in str(excinfo.value)
    assert door_store.scans == []


def test_disabled_state_refuses_transaction_creation():
    # WA fixture carries a full profile with an incomplete approval
    # chain (no justynn_enable record): resolver returns disabled.
    service, _, _ = _service()
    with pytest.raises(DoorRefused):
        _create(service, "WA", "alcohol_service")


def test_partial_state_refuses_reliance_and_never_blends():
    service, door_store, _ = _service()
    with pytest.raises(DoorRefused):
        _create(service, "CO", "alcohol_service")
    # The lane flow exists in CO, and it carries no biometric step.
    txn = _create(service, "CO", "entry")
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
        _create(service, "CO", "entry", flow_mode="reliance_biometric")


def test_full_state_entry_also_rides_the_biometric_flow():
    service, _, _ = _service()
    txn = _create(service, "CA", "entry")
    assert txn.flow_mode == "reliance_biometric"
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "valid"


# ------------------------------------------- M1: NC entry is not reliance


def test_nc_authors_no_entry_reliance():
    # Rosa probe P3: an NC entry transaction finalized valid with
    # legal_effect protected. G.S. 18B-302(d)(3) is a defense to
    # subsection (a) sales only; NC entry reliance is not authored.
    resolver = ProfileResolver(build_fixture_profiles(now=NOW))
    decision = resolver.resolve("NC", NOW)
    assert decision.mode == "full"
    assert decision.flow_for("entry") is None
    service, door_store, _ = _service()
    with pytest.raises(DoorRefused):
        _create(service, "NC", "entry")
    assert door_store.scans == []


def test_sales_only_profile_cannot_author_entry_reliance():
    # The resolver coherence rule behind M1: entry reliance authored
    # on a profile whose legal basis does not permit it disables the
    # profile instead of letting the claim through.
    profile = author_profile(
        state_code="NC",
        version=2,
        mode="full",
        diego_score=77,
        entry_lane_enabled=True,
        entry_flow_mode=RELIANCE_BIOMETRIC,
        entry_legal_effect="protected",
        entry_reliance_permitted=False,
        alcohol_service_reliance_enabled=True,
        alcohol_service_flow_mode=RELIANCE_BIOMETRIC,
        alcohol_service_legal_effect="protected",
        minimum_age_band="age_21_plus",
        authorization_required=False,
        authorization_status="not_required",
        liveness_required=True,
        enrollment_requirement_handlers=(
            "document_auth_electronic",
            "contemporaneous_biometric_link",
            "seller_agent_registration",
        ),
        venue_agreement_required=True,
        claim_key_set=("reliance_age_result",),
        contract_pack_key="nc_seller_agent_v1",
        proof_bundle_key="nc_two_prong_proof_v1",
        scan_retention_days=365,
    )
    resolver = ProfileResolver({"NC": (profile, _chain_for(profile))})
    decision = resolver.resolve("NC", NOW)
    assert decision.mode == "disabled"
    assert decision.reason == "profile_incoherent"


def test_nc_service_record_binds_the_venue_agreement():
    # TW-672 Section 4.3 item 1 plus TW-671 Section 8.3: the NC scan
    # must prove the registration prong traceable to the seller agent
    # agreement for THAT venue, so the contract pack key is bound to
    # the venue, not shared by every venue in the state.
    service, door_store, _ = _service()
    txn = _create(service, "NC", "alcohol_service")
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "valid"
    (scan,) = door_store.scans
    assert scan["legal_effect"] == "protected"
    assert scan["venue_id"] == "venue-nc-001"
    assert scan["venue_agreement_ref"] == "agr-nc-001"
    assert scan["contract_pack_key"] == "nc_seller_agent_v1:venue-nc-001"

    door_store.register_venue_agreement(
        "agr-nc-002", venue_id="venue-nc-002", state_code="NC"
    )
    txn2 = service.create_transaction(
        venue_state="NC",
        purpose="alcohol_service",
        user_id="ama",
        venue_id="venue-nc-002",
        venue_agreement_ref="agr-nc-002",
    )
    service.submit_selfie(txn2.id, AMA_SELFIE, _answering_frames())
    scan2 = door_store.scans[1]
    assert scan2["contract_pack_key"] == "nc_seller_agent_v1:venue-nc-002"
    assert scan2["contract_pack_key"] != scan["contract_pack_key"]


def test_nc_agreement_revoked_mid_transaction_fails_the_finalization_recheck():
    # Spec Section 8.3: the agreement binding is re-checked at scan
    # finalization. A revoked agreement ends the transaction.
    service, door_store, _ = _service()
    txn = _create(service, "NC", "alcohol_service")
    door_store.revoke_venue_agreement("agr-nc-001")
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "not_valid"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "agreement_not_current"


def test_reliance_requires_a_registered_venue_agreement():
    service, door_store, _ = _service()
    with pytest.raises(DoorRefused):
        _create(service, "CA", "alcohol_service", venue_agreement_ref=None)
    with pytest.raises(DoorRefused):
        _create(service, "CA", "alcohol_service", venue_agreement_ref="agr-nope")
    assert door_store.scans == []


# --------------------------------- m1: AZ gates are present tense facts


def test_arizona_pending_authorization_resolves_disabled():
    # Rosa nit n1: the legally current state of Arizona (DLLC
    # authorization not yet obtained) is the tested state.
    profiles = build_fixture_profiles(now=NOW)
    profile, approvals = profiles["AZ"]
    pending = dataclasses.replace(profile, authorization_status="pending")
    resolver = ProfileResolver({"AZ": (pending, approvals)})
    decision = resolver.resolve("AZ", NOW)
    assert decision.mode == "disabled"
    assert decision.reason == "authorization_not_obtained"


def test_arizona_fixture_is_disabled_today():
    resolver = ProfileResolver(build_fixture_profiles(now=NOW))
    assert resolver.resolve("AZ", NOW).mode == "disabled"
    service, door_store, _ = _service()
    with pytest.raises(DoorRefused):
        _create(service, "AZ", "alcohol_service")
    assert door_store.scans == []


def test_profile_requiring_an_unbuilt_enrollment_handler_cannot_activate():
    # TW-672 Finding 2: the TW-658 engine has no secondary identity
    # proofing stage today. A profile naming that handler cannot
    # activate until the stage exists (decision TW-672-D2).
    profile = author_profile(
        state_code="CA",
        version=2,
        mode="full",
        diego_score=87,
        entry_lane_enabled=True,
        entry_flow_mode=RELIANCE_BIOMETRIC,
        entry_legal_effect="protected",
        entry_reliance_permitted=True,
        alcohol_service_reliance_enabled=True,
        alcohol_service_flow_mode=RELIANCE_BIOMETRIC,
        alcohol_service_legal_effect="protected",
        minimum_age_band="age_21_plus",
        authorization_required=False,
        authorization_status="not_required",
        liveness_required=True,
        enrollment_requirement_handlers=(
            "document_auth_electronic",
            "secondary_identity_check",
            "contemporaneous_biometric_link",
        ),
        venue_agreement_required=True,
        claim_key_set=("reliance_age_result", "protected_entry"),
        contract_pack_key="ca_reliance_v1",
        proof_bundle_key="ca_evidence_record_v1",
        scan_retention_days=365,
    )
    resolver = ProfileResolver({"CA": (profile, _chain_for(profile))})
    decision = resolver.resolve("CA", NOW)
    assert decision.mode == "disabled"
    assert decision.reason == "enrollment_handler_unavailable"


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


def test_lane_consent_withdrawn_mid_transaction_finalizes_not_valid():
    # Rosa probe P7 / spec Section 6: the consent double check is
    # stated without flow qualification. The lane path re-checks at
    # finalization exactly like the reliance path.
    service, door_store, _ = _service()
    txn = _create(service, "CO", "entry")
    door_store.revoke_door_consents("ama", at=NOW + timedelta(minutes=1))
    result = service.complete_lane(txn.id)
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
    forbidden = ("template", "image", "name", "dob", "birth", "photo")
    for key in scan:
        assert not any(word in key for word in forbidden), key
    # CA's defense is proven after the fact: the record must be able
    # to prove the transaction happened under a named profile, at a
    # named venue, under a named agreement.
    for required in (
        "state_profile_hash",
        "proof_bundle_key",
        "authorization_status_at_scan",
        "venue_state_code",
        "venue_id",
        "venue_agreement_ref",
        "purpose",
        "created_at",
    ):
        assert scan[required], required


def test_score_threshold_and_engine_version_live_server_side_only():
    # Rosa M2: TW-671 Section 5 said scores are never recorded;
    # TW-672 Section 4.3 item 5 requires the preserved record to
    # carry the match score, the threshold in force, and the engine
    # version. Reconciled: preserved in the server side evidence
    # record, never rendered across the venue boundary.
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    (scan,) = door_store.scans
    expected = cosine_similarity(_VECTORS[AMA_SELFIE], AMA_TEMPLATE)
    assert scan["match_score"] == pytest.approx(expected)
    assert scan["match_threshold"] == EngineConfig().match_threshold
    assert scan["engine_version"] == ENGINE_VERSION
    external = result.as_dict()
    for key in ("match_score", "match_threshold", "engine_version", "venue_id"):
        assert key not in external


def test_session_initiation_and_staff_action_are_on_the_record():
    # TW-672 Section 4.3 items 2 and 7: how the session was started
    # (and who started it) is recorded at creation; the staff action
    # taken on the result is recorded on the same record.
    service, door_store, _ = _service()
    txn = _create(
        service,
        "CA",
        "alcohol_service",
        session_initiated_by="door_staff",
        staff_ref="staff-7",
    )
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    (scan,) = door_store.scans
    assert scan["session_initiated_by"] == "door_staff"
    assert scan["session_staff_ref"] == "staff-7"
    assert scan["staff_action"] is None
    assert scan["enrollment_auth_summary_ref"]
    service.record_staff_action(result.scan_id, "admitted")
    assert door_store.scans[0]["staff_action"] == "admitted"
    with pytest.raises(ValueError):
        service.record_staff_action(result.scan_id, "waved_through")


def test_abort_scan_keeps_the_creation_time_snapshot():
    # Rosa m4: a mid transaction downgrade abort must still name the
    # profile version, hash, pack, bundle, and authorization status
    # the transaction was created under, not the re-resolved disabled
    # decision's empty fields.
    service, door_store, _ = _service()
    txn = _reliance_txn(service)
    profiles = service.resolver._profiles
    profile, approvals = profiles["CA"]
    profiles["CA"] = (profile, approvals[:-1])  # chain now incomplete
    result = service.submit_selfie(txn.id, AMA_SELFIE, _answering_frames())
    assert result.result == "unavailable"
    (scan,) = door_store.scans
    assert scan["failure_class"] == "profile_changed"
    assert scan["contract_pack_key"] == "ca_reliance_v1:venue-ca-001"
    assert scan["proof_bundle_key"] == "ca_evidence_record_v1"
    assert scan["authorization_status_at_scan"] == "not_required"
    assert scan["legal_effect"] == "protected"
    assert scan["state_profile_hash"] == txn.profile_hash


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
    assert decision.profile_hash != resolver.resolve("CA", NOW).profile_hash


def test_a_tampered_profile_fails_integrity_and_disables():
    profiles = build_fixture_profiles(now=NOW)
    profile, approvals = profiles["CA"]
    object.__setattr__(profile, "diego_score", 41)
    resolver = ProfileResolver(profiles)
    decision = resolver.resolve("CA", NOW)
    assert decision.mode == "disabled"
    assert decision.reason == "profile_integrity"
