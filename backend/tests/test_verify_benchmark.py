"""TW-649: verification engine benchmark harness, metric definitions.

These tests define the launch-gate math (TW-646 spec Sections 5.3 and 5.5)
before the implementation exists. Groups are named for the family, per
tradition; in real runs a group label is whatever demographic grouping the
registered dataset provides, and the harness never invents one.
"""

from __future__ import annotations

import pytest

from app.verify.benchmark.documents import (
    agreement_rate,
    field_accuracy,
    mrz_check_digit,
    mrz_field_valid,
)
from app.verify.benchmark.metrics import (
    GateCriteria,
    GateStatus,
    ScoredAttempt,
    evaluate_gate,
    liveness_rates,
    max_gap,
    per_group,
    rates_for,
)
from app.verify.benchmark.registry import DATASETS, get
from app.verify.benchmark.runner import run_benchmark, synthetic_attempts

THRESHOLD = 0.55


def genuine(group: str, *scores: float) -> list[ScoredAttempt]:
    return [ScoredAttempt(group=group, genuine=True, score=s) for s in scores]


def impostor(group: str, *scores: float) -> list[ScoredAttempt]:
    return [ScoredAttempt(group=group, genuine=False, score=s) for s in scores]


def bulk(group: str, n_genuine_ok: int, n_genuine_bad: int,
         n_impostor_ok: int, n_impostor_bad: int) -> list[ScoredAttempt]:
    """Attempts with exact known outcomes at THRESHOLD."""
    out = []
    out += genuine(group, *([0.90] * n_genuine_ok))
    out += genuine(group, *([0.10] * n_genuine_bad))
    out += impostor(group, *([0.10] * n_impostor_ok))
    out += impostor(group, *([0.90] * n_impostor_bad))
    return out


# --- rates ---------------------------------------------------------------


def test_rates_exact_counts():
    attempts = (
        genuine("ama", 0.9, 0.8, 0.2, 0.7)  # 1 false non-match of 4
        + impostor("ama", 0.1, 0.2, 0.9, 0.3, 0.4)  # 1 false match of 5
    )
    rates = rates_for(attempts, THRESHOLD)
    assert rates.genuine_count == 4
    assert rates.impostor_count == 5
    assert rates.false_non_matches == 1
    assert rates.false_matches == 1
    assert rates.fnmr == pytest.approx(0.25)
    assert rates.fmr == pytest.approx(0.20)


def test_score_equal_to_threshold_is_an_accept():
    rates = rates_for(genuine("ama", THRESHOLD) + impostor("ama", THRESHOLD),
                      THRESHOLD)
    assert rates.false_non_matches == 0
    assert rates.false_matches == 1


def test_missing_denominator_is_none_not_zero():
    rates = rates_for(genuine("ama", 0.9), THRESHOLD)
    assert rates.fmr is None  # no impostor attempts: unmeasured, not perfect
    assert rates.fnmr == 0.0


def test_per_group_breakdown():
    attempts = bulk("ama", 9, 1, 10, 0) + bulk("kofi", 8, 2, 10, 1)
    groups = per_group(attempts, THRESHOLD)
    assert set(groups) == {"ama", "kofi"}
    assert groups["ama"].fnmr == pytest.approx(0.10)
    assert groups["kofi"].fnmr == pytest.approx(0.20)
    assert groups["kofi"].fmr == pytest.approx(1 / 11)


def test_max_gap_uses_groups_with_data_only():
    groups = per_group(bulk("ama", 9, 1, 10, 0) + bulk("kofi", 8, 2, 10, 0),
                       THRESHOLD)
    assert max_gap(groups, "fnmr") == pytest.approx(0.10)
    assert max_gap(groups, "fmr") == pytest.approx(0.0)


# --- liveness (ISO 30107-3 vocabulary) ------------------------------------


def test_liveness_rates_apcer_bpcer():
    presentations = (
        [("attack", False)] * 9 + [("attack", True)]  # 1 of 10 attacks accepted
        + [("bonafide", True)] * 19 + [("bonafide", False)]  # 1 of 20 rejected
    )
    rates = liveness_rates(presentations)
    assert rates.apcer == pytest.approx(0.10)
    assert rates.bpcer == pytest.approx(0.05)


# --- the launch gate ---------------------------------------------------------

CRITERIA = GateCriteria(
    fmr_max=0.01,
    fnmr_max=0.10,
    fnmr_gap_max=0.05,
    min_genuine_per_group=10,
    min_impostor_per_group=10,
)


def test_gate_passes_uniform_groups():
    attempts = bulk("ama", 19, 1, 200, 0) + bulk("kofi", 18, 2, 200, 0)
    report = evaluate_gate(attempts, THRESHOLD, CRITERIA)
    assert report.status is GateStatus.PASS
    assert report.failures == []


def test_gate_fails_on_demographic_fnmr_gap():
    # kofi's group is measured far worse: 40 percent false rejects vs 5.
    attempts = bulk("ama", 19, 1, 200, 0) + bulk("kofi", 12, 8, 200, 0)
    report = evaluate_gate(attempts, THRESHOLD, CRITERIA)
    assert report.status is GateStatus.FAIL
    assert any("gap" in f for f in report.failures)


def test_gate_fails_on_fmr_ceiling():
    attempts = bulk("ama", 20, 0, 90, 10)  # 10 percent false matches
    report = evaluate_gate(attempts, THRESHOLD, CRITERIA)
    assert report.status is GateStatus.FAIL
    assert any("fmr" in f for f in report.failures)


def test_gate_fails_on_group_fnmr_ceiling():
    attempts = bulk("ama", 15, 5, 200, 0)  # 25 percent false rejects, one group
    report = evaluate_gate(attempts, THRESHOLD, CRITERIA)
    assert report.status is GateStatus.FAIL
    assert any("fnmr" in f for f in report.failures)


def test_gate_insufficient_data_is_not_a_pass():
    attempts = bulk("ama", 4, 0, 4, 0)  # clean but far too small to measure
    report = evaluate_gate(attempts, THRESHOLD, CRITERIA)
    assert report.status is GateStatus.INSUFFICIENT_DATA
    assert report.insufficient  # names what is missing


def test_gate_failure_beats_insufficient_data():
    # One group is both too small AND measurably failing: the failure rules.
    attempts = bulk("ama", 19, 1, 200, 0) + bulk("kofi", 2, 2, 4, 0)
    report = evaluate_gate(attempts, THRESHOLD, CRITERIA)
    assert report.status is GateStatus.FAIL


def test_default_criteria_carry_the_spec_planning_values():
    defaults = GateCriteria()
    assert defaults.fmr_max == pytest.approx(1e-4)  # spec S5.3: 1 in 10,000


# --- documents ---------------------------------------------------------------


def test_mrz_check_digit_known_vectors():
    # Hand-computed per ICAO 9303 (weights 7,3,1 repeating; A=10..Z=35, <=0).
    assert mrz_check_digit("520727") == 3
    assert mrz_check_digit("AB2134") == 5


def test_mrz_field_valid_round_trip_and_corruption():
    field = "520727"
    with_digit = field + str(mrz_check_digit(field))
    assert mrz_field_valid(with_digit)
    corrupted = with_digit[:-1] + ("0" if with_digit[-1] != "0" else "1")
    assert not mrz_field_valid(corrupted)


def test_field_accuracy_is_exact_match_per_field():
    predicted = {"surname": "OKAfor".upper(), "doc_number": "X123", "dob": "x"}
    gold = {"surname": "OKAFOR", "doc_number": "X123", "dob": "y"}
    result = field_accuracy(predicted, gold)
    assert result == {"surname": True, "doc_number": True, "dob": False}


def test_agreement_rate_front_vs_barcode():
    front = [{"surname": "A", "doc_number": "1"}, {"surname": "B", "doc_number": "2"}]
    barcode = [{"surname": "A", "doc_number": "1"}, {"surname": "C", "doc_number": "2"}]
    rates = agreement_rate(front, barcode, ["surname", "doc_number"])
    assert rates["surname"] == pytest.approx(0.5)
    assert rates["doc_number"] == pytest.approx(1.0)


# --- dataset registry contract ------------------------------------------------


def test_registry_entries_carry_license_and_consent():
    assert DATASETS, "registry must not be empty"
    ids = [d.id for d in DATASETS]
    assert len(ids) == len(set(ids)), "dataset ids must be unique"
    for entry in DATASETS:
        assert entry.license.strip(), f"{entry.id}: license note required"
        assert entry.consent_basis.strip(), f"{entry.id}: consent basis required"
        assert entry.status in {"available", "registered", "planned"}


# --- synthetic smoke data -------------------------------------------------------


def test_synthetic_attempts_are_deterministic():
    first = synthetic_attempts(seed=7, per_group_genuine=10, per_group_impostor=10)
    second = synthetic_attempts(seed=7, per_group_genuine=10, per_group_impostor=10)
    assert first == second
    assert {a.group for a in first}  # at least one group


def test_run_benchmark_returns_a_serializable_report():
    report = run_benchmark(
        synthetic_attempts(seed=7, per_group_genuine=20, per_group_impostor=20),
        THRESHOLD,
        CRITERIA,
    )
    assert report["threshold"] == THRESHOLD
    assert report["gate"]["status"] in {"pass", "fail", "insufficient_data"}
    assert "groups" in report and "overall" in report


# --- expected-group coverage (TW-649 fix round, Rosa M1) ---------------------


def test_gate_missing_expected_group_is_insufficient_never_pass():
    # Both present groups pass cleanly at the floors; a third expected
    # group is absent from the run entirely. Silence is not a pass.
    attempts = bulk("ama", 19, 1, 200, 0) + bulk("kofi", 18, 2, 200, 0)
    report = evaluate_gate(
        attempts, THRESHOLD, CRITERIA, expected_groups=("ama", "kofi", "ada")
    )
    assert report.status is GateStatus.INSUFFICIENT_DATA
    assert any("ada" in note for note in report.insufficient)


def test_gate_missing_expected_group_does_not_mask_a_measured_failure():
    # A present group breaches the FMR ceiling while an expected group
    # is absent: the measured failure rules (FAIL outranks insufficient).
    attempts = bulk("ama", 20, 0, 90, 10)  # 10 percent false matches
    report = evaluate_gate(
        attempts, THRESHOLD, CRITERIA, expected_groups=("ama", "ada")
    )
    assert report.status is GateStatus.FAIL
    assert any("ada" in note for note in report.insufficient)


def test_gate_with_all_expected_groups_present_can_pass():
    attempts = bulk("ama", 19, 1, 200, 0) + bulk("kofi", 18, 2, 200, 0)
    report = evaluate_gate(
        attempts, THRESHOLD, CRITERIA, expected_groups=("ama", "kofi")
    )
    assert report.status is GateStatus.PASS


def test_run_benchmark_flags_a_missing_expected_group():
    attempts = bulk("ama", 19, 1, 200, 0) + bulk("kofi", 18, 2, 200, 0)
    report = run_benchmark(
        attempts, THRESHOLD, CRITERIA, expected_groups=("ama", "kofi", "ada")
    )
    assert report["gate"]["status"] == "insufficient_data"
    assert any("ada" in note for note in report["gate"]["insufficient"])


def test_registry_expected_groups_contract():
    for entry in DATASETS:
        labels = entry.expected_groups
        assert len(labels) == len(set(labels)), f"{entry.id}: duplicate labels"
        assert all(label.strip() for label in labels), f"{entry.id}: blank label"
    # FairFace feeds the per-group breakout: the group labels it
    # publishes are declared, so a run missing one cannot pass by
    # silence.
    assert get("fairface").expected_groups
    # LFW carries no group labels the harness may use; it declares
    # none and cannot feed the per-group gate.
    assert get("lfw").expected_groups == ()


# --- front-to-barcode agreement evidence rules (Rosa m1) -----------------------


def test_agreement_rate_both_sides_missing_is_unmeasured():
    # Neither read carries "dob" anywhere: no evidence, so the field
    # is None (unmeasured), never 1.0 on silence.
    front = [{"surname": "A"}, {"surname": "B"}]
    barcode = [{"surname": "A"}, {"surname": "B"}]
    rates = agreement_rate(front, barcode, ["surname", "dob"])
    assert rates["surname"] == pytest.approx(1.0)
    assert rates["dob"] is None


def test_agreement_rate_counts_only_evidenced_pairs():
    # Pair 1: "dob" missing on both sides (not evidence).
    # Pair 2: "dob" on the front only (evidence, and a disagreement).
    # Pair 3: "dob" on both sides, equal (evidence, agreement).
    front = [
        {"surname": "A"},
        {"surname": "B", "dob": "1990"},
        {"surname": "C", "dob": "1985"},
    ]
    barcode = [{"surname": "A"}, {"surname": "B"}, {"surname": "C", "dob": "1985"}]
    rates = agreement_rate(front, barcode, ["dob"])
    assert rates["dob"] == pytest.approx(0.5)


def test_agreement_rate_genuine_agreement_rates_one():
    front = [{"dob": "1990"}, {"dob": "1985"}]
    barcode = [{"dob": "1990"}, {"dob": "1985"}]
    rates = agreement_rate(front, barcode, ["dob"])
    assert rates["dob"] == pytest.approx(1.0)
