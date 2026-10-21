"""TW-658 verification engine: pure-core unit tests (TDD RED).

The engine core is pure stdlib logic over stage outputs: config and
the D3 capture flag, field normalization, keyed HMAC hashing, the
document cross-checks (spec Section 5.2 stages 1-2), face-match math
(stage 3), liveness challenge geometry (stage 4), velocity limits
and the verdict combiner (stage 5). Heavy model libraries sit behind
ports and are never imported here.

Fixture names are family names, per repo tradition: Ama Serwaa is
our passport holder, born 27 July 1990.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timezone

import pytest

from app.verify.benchmark.documents import mrz_check_digit
from app.verify.engine.config import (
    CONSENT_POLICY_VERSION,
    ENGINE_VERSION,
    RETENTION_SCHEDULE_VERSION,
    EngineConfig,
    production_capture_enabled,
)
from app.verify.engine.crosschecks import evaluate_document
from app.verify.engine.face import (
    TemplateRecord,
    band_for_similarity,
    cosine_similarity,
    find_duplicates,
)
from app.verify.engine.hashing import FieldHasher
from app.verify.engine.liveness import (
    evaluate_challenge,
    eye_aspect_ratio,
    generate_challenges,
    head_yaw,
)
from app.verify.engine.normalize import (
    age_band_for,
    next_band_date_for,
    normalize_dob,
    normalize_doc_number,
    normalize_name,
)
from app.verify.engine.velocity import AttemptRecord, check_velocity
from app.verify.engine.verdict import VerdictInputs, decide

TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- config


class TestCaptureFlag:
    def test_flag_defaults_off(self):
        assert production_capture_enabled({}) is False
        assert EngineConfig().production_capture_enabled is False

    def test_flag_requires_an_explicit_affirmative(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            assert (
                production_capture_enabled({"VFY_PRODUCTION_CAPTURE_ENABLED": value})
                is True
            )
        for value in ("0", "false", "off", "", "enabled-ish"):
            assert (
                production_capture_enabled({"VFY_PRODUCTION_CAPTURE_ENABLED": value})
                is False
            )

    def test_config_from_env(self):
        cfg = EngineConfig.from_env({"VFY_PRODUCTION_CAPTURE_ENABLED": "true"})
        assert cfg.production_capture_enabled is True

    def test_planning_thresholds_are_the_spec_figures(self):
        cfg = EngineConfig()
        assert cfg.match_threshold == pytest.approx(0.55)
        assert cfg.review_floor < cfg.match_threshold
        assert cfg.duplicate_threshold > cfg.match_threshold
        assert cfg.staging_ttl_hours == 72

    def test_versions_are_named(self):
        assert ENGINE_VERSION
        assert CONSENT_POLICY_VERSION
        assert RETENTION_SCHEDULE_VERSION


# ------------------------------------------------------------- normalize


class TestNormalize:
    def test_name(self):
        assert normalize_name("  ama   serwaa ") == "AMA SERWAA"
        assert normalize_name("Kofi Mensah") == "KOFI MENSAH"

    def test_doc_number(self):
        assert normalize_doc_number(" p123 456-78 ") == "P12345678"

    def test_dob_formats(self):
        assert normalize_dob("07/27/1990") == "1990-07-27"
        assert normalize_dob("1990-07-27") == "1990-07-27"
        assert normalize_dob("07271990") == "1990-07-27"  # AAMVA MMDDYYYY

    def test_dob_mrz_century_rule(self):
        # MRZ YYMMDD: 900727 is 1990 against a 2026 reference; a YY
        # that would land in the future rolls back a century.
        assert normalize_dob("900727", reference=TODAY) == "1990-07-27"
        assert normalize_dob("520727", reference=TODAY) == "1952-07-27"
        assert normalize_dob("150115", reference=TODAY) == "2015-01-15"

    def test_dob_garbage_is_none_not_a_guess(self):
        assert normalize_dob("not-a-date") is None
        assert normalize_dob("") is None
        assert normalize_dob("13/40/1990") is None

    def test_age_bands(self):
        assert age_band_for(date(1990, 7, 27), TODAY) == "age_21_plus"
        assert age_band_for(date(2007, 5, 1), TODAY) == "age_18_20"
        assert age_band_for(date(2009, 5, 1), TODAY) == "age_16_17"
        assert age_band_for(date(2012, 5, 1), TODAY) == "under_16"
        # Birthday boundary: turns 18 exactly today.
        assert age_band_for(date(2008, 10, 20), TODAY) == "age_18_20"
        assert age_band_for(date(2008, 10, 21), TODAY) == "age_16_17"

    def test_next_band_dates(self):
        assert next_band_date_for(date(2009, 5, 1), TODAY) == date(2027, 5, 1)
        assert next_band_date_for(date(2007, 5, 1), TODAY) == date(2028, 5, 1)
        assert next_band_date_for(date(1990, 7, 27), TODAY) is None


# -------------------------------------------------------------- hashing


class TestHashing:
    def test_field_hash_is_keyed_and_deterministic(self):
        ama = FieldHasher(b"ama-key")
        kofi = FieldHasher(b"kofi-key")
        assert ama.field_hash("AMA SERWAA") == ama.field_hash("AMA SERWAA")
        assert ama.field_hash("AMA SERWAA") != kofi.field_hash("AMA SERWAA")
        assert ama.field_hash("AMA SERWAA") != ama.field_hash("KOFI MENSAH")

    def test_anchor_binds_name_dob_and_doc_number(self):
        h = FieldHasher(b"anchor-key")
        base = h.anchor_hash("AMA SERWAA", "1990-07-27", "P12345678")
        assert base == h.anchor_hash("AMA SERWAA", "1990-07-27", "P12345678")
        assert base != h.anchor_hash("AMA SERWAA", "1990-07-28", "P12345678")
        assert base != h.anchor_hash("AMA SERWAA", "1990-07-27", "P87654321")
        assert base != h.anchor_hash("KOFI MENSAH", "1990-07-27", "P12345678")


# ----------------------------------------------------------- crosschecks


def _td3_mrz(doc="P12345678", dob="900727", expiry="301231", personal="A12345"):
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
    composite_src = line[0:10] + line[13:20] + line[21:28] + line[28:43]
    line += str(mrz_check_digit(composite_src))
    from app.verify.benchmark.stages import parse_mrz_lines

    return parse_mrz_lines(["P<UTO SERWAA<<AMA<<<<<<<<<<<<<<<<<<<<<", line])


class TestCrosschecks:
    def test_passport_mrz_is_canonical_and_valid(self):
        ev = evaluate_document(
            document_type="passport",
            ocr_fields={"doc_number": "P12345678", "dob": "07/27/1990"},
            mrz=_td3_mrz(),
            pdf417_fields=None,
            today=TODAY,
        )
        assert ev.fields["doc_number"] == "P12345678"
        assert ev.fields["dob"] == "1990-07-27"
        assert ev.fields["expiry"] == "2030-12-31"
        assert ev.mrz_valid is True
        assert ev.tamper_signals == []
        assert ev.expired(TODAY) is False

    def test_passport_corrupt_mrz_check_digit_is_a_tamper_signal(self):
        mrz = _td3_mrz()
        mrz["valid"]["doc_number"] = False
        ev = evaluate_document(
            document_type="passport",
            ocr_fields={},
            mrz=mrz,
            pdf417_fields=None,
            today=TODAY,
        )
        assert ev.mrz_valid is False
        assert any("mrz" in s for s in ev.tamper_signals)

    def test_license_front_barcode_disagreement_is_a_tamper_signal(self):
        ev = evaluate_document(
            document_type="drivers_license",
            ocr_fields={"doc_number": "X1234567", "dob": "07/27/1990"},
            mrz=None,
            pdf417_fields={"doc_number": "X9999999", "dob": "07271990"},
            today=TODAY,
        )
        # Barcode wins for the canonical value on a US license.
        assert ev.fields["doc_number"] == "X9999999"
        assert ev.agreement["doc_number"] is False
        assert ev.agreement["dob"] is True
        assert any("doc_number" in s for s in ev.tamper_signals)

    def test_license_front_barcode_agreement_is_clean(self):
        ev = evaluate_document(
            document_type="drivers_license",
            ocr_fields={"doc_number": "X1234567", "dob": "07/27/1990"},
            mrz=None,
            pdf417_fields={"doc_number": "X1234567", "dob": "07271990"},
            today=TODAY,
        )
        assert ev.tamper_signals == []
        assert ev.agreement["doc_number"] is True

    def test_expired_document(self):
        ev = evaluate_document(
            document_type="passport",
            ocr_fields={},
            mrz=_td3_mrz(expiry="201231"),
            pdf417_fields=None,
            today=TODAY,
        )
        assert ev.expired(TODAY) is True


# ------------------------------------------------------------------ face


class TestFaceMath:
    def test_cosine(self):
        assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
        assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
        assert cosine_similarity((1.0, 1.0), (1.0, 0.0)) == pytest.approx(
            0.7071, abs=1e-3
        )

    def test_bands(self):
        cfg = EngineConfig()
        assert band_for_similarity(0.95, cfg) == "match"
        assert band_for_similarity(0.50, cfg) == "review"
        assert band_for_similarity(0.10, cfg) == "no_match"

    def test_duplicate_search_threshold_and_order(self):
        cfg = EngineConfig()
        corpus = [
            TemplateRecord("anchor-kofi", "u-kofi", False, (0.0, 1.0)),
            TemplateRecord("anchor-ama", "u-ama", False, (0.99, 0.14)),
            TemplateRecord("anchor-weak", "u-weak", False, (0.92, 0.39)),
        ]
        hits = find_duplicates((1.0, 0.05), corpus, cfg)
        assert [h.anchor_hash for h in hits] == ["anchor-ama", "anchor-weak"]
        assert hits[0].similarity >= hits[1].similarity

    def test_duplicate_search_empty_corpus(self):
        assert find_duplicates((1.0, 0.0), [], EngineConfig()) == []


# -------------------------------------------------------------- liveness


def _blank_landmarks() -> list[tuple[float, float, float]]:
    return [(0.5, 0.5, 0.0)] * 468


def _set(lms, idx, x, y):
    lms[idx] = (x, y, 0.0)


def _open_face() -> list[tuple[float, float, float]]:
    """A frontal face with eyes open, in MediaPipe index space."""
    lms = _blank_landmarks()
    # Eye corner anchors: left eye outer 33 / inner 133, right 362 / 263.
    _set(lms, 33, 0.35, 0.5)
    _set(lms, 133, 0.45, 0.5)
    _set(lms, 362, 0.55, 0.5)
    _set(lms, 263, 0.65, 0.5)
    _set(lms, 1, 0.50, 0.55)  # nose tip centered
    # Left eye verticals wide open (EAR ~0.3): 159/145, 160/144.
    _set(lms, 159, 0.40, 0.47)
    _set(lms, 145, 0.40, 0.53)
    _set(lms, 160, 0.42, 0.47)
    _set(lms, 144, 0.42, 0.53)
    _set(lms, 158, 0.38, 0.50)
    _set(lms, 153, 0.38, 0.50)
    # Right eye verticals wide open: 385/373... use 386/374, 387/373.
    _set(lms, 386, 0.60, 0.47)
    _set(lms, 374, 0.60, 0.53)
    _set(lms, 387, 0.62, 0.47)
    _set(lms, 373, 0.62, 0.53)
    _set(lms, 385, 0.58, 0.50)
    _set(lms, 380, 0.58, 0.50)
    return lms


class TestLiveness:
    def test_challenges_are_randomized_but_seeded_deterministic(self):
        a = generate_challenges(random.Random(7), count=3)
        b = generate_challenges(random.Random(7), count=3)
        assert a == b
        assert len(a) == 3 and len(set(a)) == 3
        assert set(a) <= {"turn_left", "turn_right", "blink", "follow_target"}

    def test_eye_aspect_ratio_open_vs_closed(self):
        open_lms = _open_face()
        assert eye_aspect_ratio(open_lms, "left") > 0.25
        closed = _open_face()
        _set(closed, 159, 0.40, 0.495)
        _set(closed, 145, 0.40, 0.505)
        _set(closed, 160, 0.42, 0.495)
        _set(closed, 144, 0.42, 0.505)
        assert eye_aspect_ratio(closed, "left") < 0.15

    def test_head_yaw_sign(self):
        frontal = _open_face()
        assert abs(head_yaw(frontal)) < 0.1
        turned = _open_face()
        _set(turned, 1, 0.60, 0.55)  # nose toward frame right
        assert head_yaw(turned) > 0.3

    def test_blink_challenge_needs_a_blink(self):
        frames = [_open_face(), _open_face()]
        assert evaluate_challenge("blink", frames) is False
        closed = _open_face()
        _set(closed, 159, 0.40, 0.495)
        _set(closed, 145, 0.40, 0.505)
        _set(closed, 160, 0.42, 0.495)
        _set(closed, 144, 0.42, 0.505)
        _set(closed, 386, 0.60, 0.495)
        _set(closed, 374, 0.60, 0.505)
        _set(closed, 387, 0.62, 0.495)
        _set(closed, 373, 0.62, 0.505)
        assert evaluate_challenge("blink", [_open_face(), closed, _open_face()])

    def test_turn_challenges(self):
        left = _open_face()
        _set(left, 1, 0.38, 0.55)
        right = _open_face()
        _set(right, 1, 0.62, 0.55)
        assert evaluate_challenge("turn_left", [left]) is True
        assert evaluate_challenge("turn_right", [right]) is True
        assert evaluate_challenge("turn_left", [right]) is False
        assert evaluate_challenge("follow_target", [left, _open_face(), right])


# -------------------------------------------------------------- velocity


class TestVelocity:
    def test_within_limits(self):
        recs = [
            AttemptRecord("document", "doc-ama", NOW),
            AttemptRecord("device", "dev-1", NOW),
        ]
        exceeded = check_velocity(
            recs,
            document_key="doc-ama",
            device_key="dev-1",
            network_key="net-1",
            now=NOW,
            config=EngineConfig(),
        )
        assert exceeded == []

    def test_document_velocity_trips(self):
        cfg = EngineConfig()
        recs = [
            AttemptRecord("document", "doc-ama", NOW)
            for _ in range(cfg.velocity_max_per_document)
        ]
        exceeded = check_velocity(
            recs,
            document_key="doc-ama",
            device_key=None,
            network_key=None,
            now=NOW,
            config=cfg,
        )
        assert exceeded == ["document"]

    def test_old_attempts_fall_out_of_the_window(self):
        from datetime import timedelta

        old = NOW - timedelta(days=2)
        recs = [AttemptRecord("document", "doc-ama", old) for _ in range(50)]
        exceeded = check_velocity(
            recs,
            document_key="doc-ama",
            device_key=None,
            network_key=None,
            now=NOW,
            config=EngineConfig(),
        )
        assert exceeded == []


# --------------------------------------------------------------- verdict


def _clean_inputs(**overrides) -> VerdictInputs:
    base = dict(
        document_extracted=True,
        mrz_valid=True,
        tamper_signals=[],
        document_expired=False,
        dob_band="age_21_plus",
        face_band="match",
        face_similarity=0.93,
        selfie_face_count=1,
        document_face_count=1,
        liveness_active_ok=True,
        liveness_passive=0.97,
        duplicate_hit=False,
        banned_anchor=False,
        velocity_exceeded=[],
        ai_face_score=0.05,
    )
    base.update(overrides)
    return VerdictInputs(**base)


class TestVerdict:
    def test_clean_pass_is_high_confidence(self):
        v = decide(_clean_inputs(), EngineConfig())
        assert v.status == "pass"
        assert v.confidence_band == "high"

    def test_banned_anchor_is_a_refusal(self):
        v = decide(_clean_inputs(banned_anchor=True), EngineConfig())
        assert v.status == "fail"
        assert "banned_anchor" in v.reasons

    def test_under_16_fails(self):
        v = decide(_clean_inputs(dob_band="under_16"), EngineConfig())
        assert v.status == "fail"

    def test_expired_document_fails(self):
        v = decide(_clean_inputs(document_expired=True), EngineConfig())
        assert v.status == "fail"

    def test_face_no_match_fails(self):
        v = decide(
            _clean_inputs(face_band="no_match", face_similarity=0.2),
            EngineConfig(),
        )
        assert v.status == "fail"

    def test_no_face_in_selfie_fails(self):
        v = decide(_clean_inputs(selfie_face_count=0), EngineConfig())
        assert v.status == "fail"

    def test_tamper_signal_routes_to_review_never_silent_pass(self):
        v = decide(
            _clean_inputs(tamper_signals=["front_barcode_disagreement:doc_number"]),
            EngineConfig(),
        )
        assert v.status == "review"

    def test_bad_mrz_routes_to_review(self):
        v = decide(_clean_inputs(mrz_valid=False), EngineConfig())
        assert v.status == "review"

    def test_duplicate_hit_routes_to_review(self):
        v = decide(_clean_inputs(duplicate_hit=True), EngineConfig())
        assert v.status == "review"

    def test_face_review_band_routes_to_review(self):
        v = decide(
            _clean_inputs(face_band="review", face_similarity=0.5),
            EngineConfig(),
        )
        assert v.status == "review"

    def test_failed_active_liveness_routes_to_review(self):
        v = decide(_clean_inputs(liveness_active_ok=False), EngineConfig())
        assert v.status == "review"

    def test_weak_passive_liveness_is_a_signal_not_a_gate(self):
        cfg = EngineConfig()
        v = decide(_clean_inputs(liveness_passive=0.2), cfg)
        assert v.status == "review"  # routed for a human look
        # ...but passive liveness alone can never produce a FAIL.
        assert v.status != "fail"

    def test_ai_face_signal_alone_never_fails(self):
        v = decide(_clean_inputs(ai_face_score=0.99), EngineConfig())
        assert v.status == "review"

    def test_velocity_routes_to_review(self):
        v = decide(_clean_inputs(velocity_exceeded=["document"]), EngineConfig())
        assert v.status == "review"

    def test_unreadable_document_routes_to_review(self):
        v = decide(_clean_inputs(document_extracted=False), EngineConfig())
        assert v.status == "review"
