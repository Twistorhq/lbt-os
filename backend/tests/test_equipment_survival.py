# Prepared by Twistor Holdings LLC.
"""Tests for the ML survival adapter (TW-213 wire-in, TW-470 surge).

Covers: closed-form math vs independent reimplementation, interval sanity,
all labeled-None cases, artifact schema validation, brand-tier fallback,
metro default labeling.
"""

import json
import math

import pytest

from app.services.equipment_survival import (
    SurvivalScorer,
    brand_tier_for,
    load_artifact,
)

ARTIFACT = "app/data/survival_artifact.v1.json"


@pytest.fixture(scope="module")
def artifact():
    return load_artifact(ARTIFACT)


@pytest.fixture(scope="module")
def scorer(artifact):
    return SurvivalScorer(artifact)


def _independent_median(age, rho, lam):
    """Independent reimplementation of the closed form (spec §math)."""
    return max((age**rho + (lam**rho) * math.log(2)) ** (1.0 / rho) - age, 0.0)


def test_closed_form_matches_independent_math(scorer, artifact):
    g = artifact["groups"]["denver|furnace"]
    lam = g["lambda0_years"] * g["brand_mult"]["mid"]
    for age in (0.0, 5.0, 12.5, 22.0, 30.0):
        est = scorer.score_asset(age_years=age, equipment_type="furnace")
        want = _independent_median(age, g["rho"], lam)
        assert est is not None
        assert est.median_remaining_years == pytest.approx(round(want, 2), abs=0.01)


def test_brand_tier_shifts_scale(scorer):
    lo = scorer.score_asset(age_years=10.0, equipment_type="furnace", brand_tier="value")
    hi = scorer.score_asset(age_years=10.0, equipment_type="furnace", brand_tier="premium")
    assert lo is not None and hi is not None
    # premium stretches the clock: longer median remaining
    assert hi.median_remaining_years > lo.median_remaining_years


def test_interval_sanity(scorer):
    est = scorer.score_asset(age_years=8.0, equipment_type="ac", brand_tier="mid")
    assert est is not None
    assert est.remaining_lo_years <= est.median_remaining_years <= est.remaining_hi_years
    assert est.remaining_lo_years >= 0.0
    assert 0.0 <= est.p_fail_within_2y <= 1.0


def test_p_fail_monotone_in_age(scorer):
    young = scorer.score_asset(age_years=2.0, equipment_type="furnace")
    old = scorer.score_asset(age_years=20.0, equipment_type="furnace")
    assert young is not None and old is not None
    assert old.p_fail_within_2y > young.p_fail_within_2y
    assert old.median_remaining_years < young.median_remaining_years


def test_provenance_labels(scorer):
    est = scorer.score_asset(age_years=8.0, equipment_type="boiler")
    assert est is not None
    assert est.provenance == "modeled:synthetic-calibration"
    assert "denver (org metro unknown)" in est.metro_basis


@pytest.mark.parametrize(
    "kwargs",
    [
        {"age_years": 8.0, "equipment_type": "water_heater"},  # outside v1 schema
        {"age_years": 8.0, "equipment_type": "springs"},
        {"age_years": -1.0, "equipment_type": "furnace"},  # negative age
        {"age_years": float("nan"), "equipment_type": "furnace"},
        {"age_years": 8.0, "equipment_type": "furnace", "horizon_years": 0},
    ],
)
def test_labeled_none_cases(scorer, kwargs):
    assert scorer.score_asset(**kwargs) is None


def test_brand_tier_mapping():
    assert brand_tier_for("Trane") == ("premium", False)
    assert brand_tier_for("goodman") == ("mid", False)
    assert brand_tier_for("SomeUnknownBrand") == ("mid", True)
    assert brand_tier_for(None) == ("mid", True)


def test_artifact_schema_rejection(tmp_path):
    good = json.loads(open(ARTIFACT).read())
    bad_version = dict(good, schema_version=999)
    p = tmp_path / "a.json"
    p.write_text(json.dumps(bad_version))
    with pytest.raises(ValueError, match="schema_version"):
        load_artifact(p)
    bad_groups = dict(good, groups={})
    p.write_text(json.dumps(bad_groups))
    with pytest.raises(ValueError, match="groups"):
        load_artifact(p)
    broken = json.loads(json.dumps(good))
    del broken["groups"]["denver|furnace"]["rho"]
    p.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="rho"):
        load_artifact(p)


def test_known_metro_scores(scorer):
    # exact metro hit still works and is labeled "metro"
    est = scorer.score_asset(age_years=8.0, equipment_type="ac", metro_key="phoenix")
    assert est is not None
    assert est.metro_basis == "metro"
    # phoenix ACs wear faster: shorter median than denver at same age/brand
    den = scorer.score_asset(age_years=8.0, equipment_type="ac", metro_key="denver")
    assert den is not None
    assert est.median_remaining_years < den.median_remaining_years
