"""TW-622 Twistor Telemetry — sampling-degradation budget contract (TDD RED).

The TW-630 wedge: over the tier, ingestion SAMPLES (recorded rate) instead
of dropping or surprise-billing. Sampling, not silence.
"""

from app.telemetry import budgets


def test_under_80_percent_ingests_no_warn():
    d = budgets.ingest_decision(used=100, cap=1000)
    assert d["action"] == "ingest"
    assert d["rate"] == 1.0
    assert d["warn"] is False


def test_at_80_percent_warns_but_ingests():
    d = budgets.ingest_decision(used=800, cap=1000)
    assert d["action"] == "ingest"
    assert d["rate"] == 1.0
    assert d["warn"] is True


def test_over_cap_samples_with_warn():
    d = budgets.ingest_decision(used=1500, cap=1000)
    assert d["action"] == "sample"
    assert d["warn"] is True
    assert 0.0 < d["rate"] < 1.0


def test_sample_rate_shrinks_as_overage_grows():
    mild = budgets.ingest_decision(used=1100, cap=1000)["rate"]
    wild = budgets.ingest_decision(used=10000, cap=1000)["rate"]
    assert wild < mild


def test_unlimited_cap_always_ingests():
    d = budgets.ingest_decision(used=10**9, cap=None)
    assert d["action"] == "ingest"
    assert d["warn"] is False


def test_sampling_is_deterministic():
    key = "org-1|h-u1|signup|2026-10-01T12:00:00+00:00"
    assert budgets.should_keep(key, 0.1) == budgets.should_keep(key, 0.1)


def test_sampling_rate_roughly_holds():
    keys = [f"key-{i}" for i in range(2000)]
    kept = sum(1 for k in keys if budgets.should_keep(k, 0.1))
    assert 100 < kept < 300  # ~10%, generous tolerance


def test_rate_one_keeps_everything_and_zero_keeps_nothing():
    assert budgets.should_keep("k", 1.0) is True
    assert budgets.should_keep("k", 0.0) is False


def test_invalid_rate_rejected():
    try:
        budgets.should_keep("k", 1.5)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    try:
        budgets.should_keep("k", -0.1)
        raise AssertionError("should have raised")
    except ValueError:
        pass
