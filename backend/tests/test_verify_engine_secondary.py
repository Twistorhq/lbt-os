"""TW-678: the Arizona secondary identity verification stage (RED).

A.R.S. 4-101 condition (b) (Angela's TW-672 Finding 2) requires,
beyond the document authentication of condition (a), a SECONDARY
identity verification at enrollment: a secondary electronic
authentication process using commercially available data (public
records query, knowledge based authentication) or a state or
federal government system of records. The TW-658 engine had no
such stage.

The stage built here sits behind the engine's port pattern and is
hash-only by contract: the request that crosses the port carries
HMAC field hashes and the identity anchor, never plaintext, and
the result that comes back is a verdict plus reference hashes.

The FOSS survey verdict (evidence doc TW-678): no production data
source exists at $0. KBA and public-records data are commercial
per-use products; AAMVA DLDV charges a setup fee plus a per
transaction fee and is reached directly or through paid gateway
partners. So production wiring configures NO secondary port
today, the stage reports unavailable, AZ-required enrollments
fail closed into review, and the door resolver's handler gate
counts the handler as built only when a production-grade adapter
is actually configured. The fixture adapter in this file is a
records-registry test double: it performs a real hash-equality
membership check, and it is production_grade False, so the gate
ignores it.

Fixtures are family, as in the pipeline tests: Ama Serwaa
(passport, b. 1990) is the enrollment under test.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone

import pytest

from app.door.profiles import (
    BUILT_ENROLLMENT_HANDLERS,
    ProfileResolver,
    author_profile,
    build_fixture_profiles,
    built_enrollment_handlers,
)
from app.verify.benchmark.documents import mrz_check_digit
from app.verify.benchmark.stages import StageUnavailable, parse_mrz_lines
from app.verify.engine.config import EngineConfig
from app.verify.engine.consent import grant_consent
from app.verify.engine.crypto import AesGcmEncryptor
from app.verify.engine.hashing import FieldHasher
from app.verify.engine.pipeline import VerificationPipeline
from app.verify.engine.ports import PipelinePorts
from app.verify.engine.secondary import (
    FixtureRecordsSecondaryAdapter,
    SecondaryCheckRequest,
    SecondaryCheckResult,
    evaluate_secondary_check,
)
from app.verify.engine.staging import InMemoryStaging
from app.verify.engine.store import InMemoryVerifyStore

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
KEY = b"tw678-test-hmac-key"
ENC_KEY = bytes(range(32))
HASHER = FieldHasher(KEY)

AMA_DOC = b"ama-doc-photo"
AMA_SELFIE = b"ama-selfie-photo"

_VECTORS = {
    AMA_DOC: (1.0, 0.05, 0.0),
    AMA_SELFIE: (0.99, 0.10, 0.0),
}

AMA_NAME = "AMA SERWAA"
AMA_DOB = "1990-07-27"
AMA_DOC_NUMBER = "P12345678"


def _ama_request() -> SecondaryCheckRequest:
    return SecondaryCheckRequest(
        anchor_hash=HASHER.anchor_hash(AMA_NAME, AMA_DOB, AMA_DOC_NUMBER),
        name_hash=HASHER.field_hash(AMA_NAME),
        dob_hash=HASHER.field_hash(AMA_DOB),
        doc_number_hash=HASHER.field_hash(AMA_DOC_NUMBER),
        document_type="passport",
    )


def _ama_fixture(*, registered: bool = True) -> FixtureRecordsSecondaryAdapter:
    records = {}
    if registered:
        req = _ama_request()
        records[req.anchor_hash] = {
            "name_hash": req.name_hash,
            "dob_hash": req.dob_hash,
            "doc_number_hash": req.doc_number_hash,
            "record_ref_hash": HASHER.field_hash("fixture-record-ama"),
        }
    return FixtureRecordsSecondaryAdapter(records)


# ------------------------------------------------------------- port level


class TestEvaluateSecondaryCheck:
    def test_no_port_is_unavailable_never_passed(self):
        result = evaluate_secondary_check(None, _ama_request())
        assert result.status == "unavailable"
        assert "secondary_source_not_configured" in result.reasons

    def test_port_unavailable_is_unavailable(self):
        class Down:
            production_grade = True

            def check(self, request):
                raise StageUnavailable("records source down")

        result = evaluate_secondary_check(Down(), _ama_request())
        assert result.status == "unavailable"

    def test_fixture_pass_is_a_real_membership_check(self):
        result = evaluate_secondary_check(_ama_fixture(), _ama_request())
        assert result.status == "passed"
        assert result.source_kind == "public_records"
        assert result.source_ref_hash == HASHER.field_hash("fixture-record-ama")
        assert {"name", "dob"} <= set(result.matched_fields)

    def test_fixture_miss_fails(self):
        result = evaluate_secondary_check(
            _ama_fixture(registered=False), _ama_request()
        )
        assert result.status == "failed"

    def test_fixture_wrong_dob_fails(self):
        adapter = _ama_fixture()
        req = _ama_request()
        bad = SecondaryCheckRequest(
            anchor_hash=req.anchor_hash,
            name_hash=req.name_hash,
            dob_hash=HASHER.field_hash("1991-01-01"),
            doc_number_hash=req.doc_number_hash,
            document_type="passport",
        )
        assert evaluate_secondary_check(adapter, bad).status == "failed"

    def test_lying_adapter_cannot_pass_without_evidence(self):
        # The Rosa probe shape: an adapter that answers "passed"
        # with no source reference and no matched fields has
        # verified nothing. The stage downgrades it, fail closed.
        class Liar:
            production_grade = True

            def check(self, request):
                return SecondaryCheckResult(
                    status="passed",
                    source_kind="public_records",
                    source_ref_hash=None,
                    matched_fields=(),
                    reasons=(),
                )

        result = evaluate_secondary_check(Liar(), _ama_request())
        assert result.status == "failed"
        assert "adapter_result_missing_evidence" in result.reasons

    def test_unknown_status_is_failed_not_passed(self):
        class Weird:
            production_grade = True

            def check(self, request):
                return SecondaryCheckResult(
                    status="probably",
                    source_kind="public_records",
                    source_ref_hash="x",
                    matched_fields=("name", "dob"),
                    reasons=(),
                )

        assert evaluate_secondary_check(Weird(), _ama_request()).status == "failed"


# ------------------------------------------------------- pipeline fakes


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
    def detect(self, image_ref: str) -> list[tuple]:
        return [(0, 0, 10, 10)]


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
        return _open_landmarks()


class FakeLiveness:
    def bona_fide_probability(self, image_ref: str) -> float:
        return 0.97


def _ports() -> PipelinePorts:
    return PipelinePorts(
        ocr=FakeOcr(),
        mrz=FakeMrz(),
        pdf417=FakePdf417(),
        face_detect=FakeDetect(),
        embed=FakeEmbed(),
        landmarks=FakeLandmarks(),
        liveness=FakeLiveness(),
    )


def _frames_for(challenges: list[str]):
    frames = [_open_landmarks()]
    frames.append(_open_landmarks(nose_x=0.36))
    frames.append(_open_landmarks(nose_x=0.64))
    frames.append(_open_landmarks(eyes_open=False))
    frames.append(_open_landmarks())
    return frames


def _pipeline(*, config=None, secondary_port=None) -> VerificationPipeline:
    return VerificationPipeline(
        store=InMemoryVerifyStore(),
        staging=InMemoryStaging(),
        hasher=FieldHasher(KEY),
        encryptor=AesGcmEncryptor(ENC_KEY),
        ports=_ports(),
        config=config or EngineConfig(production_capture_enabled=True),
        secondary_port=secondary_port,
        rng=random.Random(7),
        clock=lambda: NOW,
    )


def _run(pipe: VerificationPipeline, user_id: str = "u-ama"):
    grant_consent(pipe.store, user_id, "passport")
    session = pipe.start_session(user_id, "passport")
    pipe.submit_document(session.id, user_id, AMA_DOC)
    challenges = pipe.begin_liveness(session.id, user_id)
    verdict = pipe.submit_selfie(
        session.id, user_id, AMA_SELFIE, _frames_for(challenges)
    )
    return session, verdict


def _secondary_signals(store) -> list[dict]:
    return [s for s in store.signals if s["signal_type"] == "secondary_identity_check"]


# --------------------------------------------------- pipeline integration


class TestPipelineSecondaryStage:
    def test_required_with_records_hit_passes_and_signals_hashes_only(self):
        pipe = _pipeline(
            config=EngineConfig(
                production_capture_enabled=True, secondary_identity_required=True
            ),
            secondary_port=_ama_fixture(),
        )
        _, verdict = _run(pipe)
        assert verdict.status == "passed"
        assert verdict.tier_granted == 2
        signals = _secondary_signals(pipe.store)
        assert len(signals) == 1
        detail = signals[0]["detail"]
        assert detail["status"] == "passed"
        blob = str(detail)
        assert "AMA" not in blob and "1990" not in blob and "P12345678" not in blob
        assert pipe.store.list_live_biometrics()

    def test_required_with_records_miss_cannot_complete(self):
        pipe = _pipeline(
            config=EngineConfig(
                production_capture_enabled=True, secondary_identity_required=True
            ),
            secondary_port=_ama_fixture(registered=False),
        )
        _, verdict = _run(pipe)
        assert verdict.status == "review"
        assert verdict.tier_granted == 0
        assert pipe.store.list_live_biometrics() == []
        state = pipe.store.get_verification_state("u-ama")
        assert state is None or state.get("status") != "verified"

    def test_required_with_no_port_cannot_complete(self):
        # The production posture today: no source configured, so an
        # AZ-purpose enrollment fails closed into review and the
        # signal records the unavailability honestly.
        pipe = _pipeline(
            config=EngineConfig(
                production_capture_enabled=True, secondary_identity_required=True
            ),
            secondary_port=None,
        )
        _, verdict = _run(pipe)
        assert verdict.status == "review"
        signals = _secondary_signals(pipe.store)
        assert len(signals) == 1
        assert signals[0]["detail"]["status"] == "unavailable"

    def test_required_with_lying_adapter_cannot_complete(self):
        class Liar:
            production_grade = True

            def check(self, request):
                return SecondaryCheckResult(
                    status="passed",
                    source_kind="public_records",
                    source_ref_hash=None,
                    matched_fields=(),
                    reasons=(),
                )

        pipe = _pipeline(
            config=EngineConfig(
                production_capture_enabled=True, secondary_identity_required=True
            ),
            secondary_port=Liar(),
        )
        _, verdict = _run(pipe)
        assert verdict.status == "review"
        assert pipe.store.list_live_biometrics() == []

    def test_not_required_runs_no_secondary_stage(self):
        pipe = _pipeline()  # default config: not required, no port
        _, verdict = _run(pipe)
        assert verdict.status == "passed"
        assert _secondary_signals(pipe.store) == []


# --------------------------------------------------------- handler gate


class _ProductionGradeDouble:
    """A wiring double that declares itself production grade, the
    way a real records adapter will when one is licensed and built.
    Used only to prove the gate opens for a genuinely wired stage;
    the fixture adapter below proves it stays shut otherwise."""

    production_grade = True

    def check(self, request):  # pragma: no cover - gate test only
        raise StageUnavailable("gate double performs no checks")


class TestHandlerGateTruthfulness:
    def test_default_built_handlers_exclude_secondary(self):
        assert "secondary_identity_check" not in BUILT_ENROLLMENT_HANDLERS
        assert "secondary_identity_check" not in built_enrollment_handlers()
        assert "secondary_identity_check" not in built_enrollment_handlers(None)

    def test_fixture_adapter_does_not_open_the_gate(self):
        handlers = built_enrollment_handlers(_ama_fixture())
        assert "secondary_identity_check" not in handlers

    def test_production_grade_adapter_opens_the_gate(self):
        handlers = built_enrollment_handlers(_ProductionGradeDouble())
        assert "secondary_identity_check" in handlers

    def test_arizona_fixture_still_disabled_today(self):
        # Two independent grounds (DLLC authorization pending, and
        # the default handler set) keep Arizona off; this build
        # changes neither shipped outcome.
        resolver = ProfileResolver(build_fixture_profiles(now=NOW))
        decision = resolver.resolve("AZ", NOW)
        assert decision.mode == "disabled"

    def test_resolver_honors_wired_handler_set(self):
        from app.door.profiles import LANE_QR_PHOTO, RELIANCE_BIOMETRIC

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
        # Default resolver: handler unwired, profile cannot activate.
        from app.door.profiles import ApprovalRecord, APPROVAL_KINDS

        chain = [
            ApprovalRecord(
                kind,
                profile.canonical_hash,
                expires_at=None,
            )
            for kind in APPROVAL_KINDS
        ]
        shut = ProfileResolver({"CA": (profile, chain)}).resolve("CA", NOW)
        assert shut.mode == "disabled"
        assert shut.reason == "enrollment_handler_unavailable"
        # Wired resolver: the same profile activates on the handler
        # ground (the gate follows the wiring, not a constant).
        wired = ProfileResolver(
            {"CA": (profile, chain)},
            available_handlers=built_enrollment_handlers(_ProductionGradeDouble()),
        ).resolve("CA", NOW)
        assert wired.mode == "full"
        assert wired.reason == "active"


# ---------------------------------------------------------------- config


class TestConfigPosture:
    def test_secondary_not_required_by_default(self):
        assert EngineConfig().secondary_identity_required is False

    def test_d3_capture_flag_untouched(self):
        assert EngineConfig().production_capture_enabled is False
        assert EngineConfig.from_env({}).production_capture_enabled is False
