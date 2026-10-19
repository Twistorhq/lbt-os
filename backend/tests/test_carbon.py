# Prepared by Twistor Holdings LLC
"""Carbon lane tests (TW-599 Wave 1, CARBON-TRUST v1).

Green tests: seed validity + coverage, migration constraint presence,
method-code comparability gating, provenance-chain walk, version
immutability, correction supersession, MRV-BLIND scoring gates and
submission eligibility, toll emission attempted (mocked) incl. the
ValueError-swallow path.

No live DB, no network, no supabase package: a FakeDB mimics the
supabase-py call pattern (table().select().eq().execute()).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.data_layer.science import paywall
from app.data_layer.science.carbon import models, router, schemas
from app.data_layer.science.carbon import seed as seed_mod

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migration_sci_carbon_v1.sql"
)


def _u(name: str) -> str:
    """Deterministic fixture UUIDs (schemas require valid UUIDs)."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.test.carbon.{name}"))


# ------------------------------------------------------------------
# Fakes
# ------------------------------------------------------------------
class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._single = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col) or "") >= str(val)]
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def single(self):
        self._single = True
        return self

    def insert(self, payload):
        self._rows.append(payload)
        return self

    def execute(self):
        if self._single:
            return FakeResponse(self._rows[0] if self._rows else None)
        return FakeResponse(self._rows)


class FakeDB:
    def __init__(self, tables):
        self._tables = {k: list(v) for k, v in tables.items()}
        self.inserts: list[tuple[str, dict]] = []

    def table(self, name):
        db = self

        class T(FakeQuery):
            def __init__(self):
                super().__init__(db._tables.get(name, []))

            def insert(self, payload):
                db.inserts.append((name, payload))
                return self

        return T()


class TollSpy:
    def __init__(self, fail_with=None):
        self.calls: list[dict] = []
        self.fail_with = fail_with

    def __call__(self, db, org_id=None, event_type=None, request_id=None,
                 quantity=1.0, **kwargs):
        if self.fail_with:
            raise self.fail_with
        self.calls.append({
            "org_id": org_id,
            "event_type": event_type,
            "request_id": request_id,
            "quantity": quantity,
            "idempotency_key": f"sci:{request_id}",
            **kwargs,
        })
        return {"ok": True}


def _versions():
    v1 = _u("version-1.0.0")
    return [
        {
            "id": v1,
            "lane": "carbon",
            "dataset_name": "carbon-trust",
            "semver": "1.0.0",
            "row_count": 36,
            "content_root_hash": "root0",
            "benchmark_pin": "mrv-blind_v1",
            "doi": None,
            "supersedes_id": None,
            "released_at": "2026-10-18T00:00:00+00:00",
            "release_notes": "V1 synthetic seed",
        },
    ]


def _gold_rows():
    rows = []
    for i, rec in enumerate(seed_mod.SEED_MEASUREMENTS[:10]):
        rows.append({
            "id": _u(f"gold-{i}"),
            "dataset_version_id": _u("version-1.0.0"),
            "silver_id": rec["id"],
            "record_class": rec["record_class"],
            "carbon_pool": rec["carbon_pool"],
            "analytical_method": rec["analytical_method"],
            "method_comparable_group": (
                "dry-combustion-equivalent"
                if rec["analytical_method"] in ("dry-combustion", "spectroscopy")
                else ("walkley-black-family" if rec["analytical_method"] == "walkley-black" else None)
            ),
            "region": rec["region"],
            "site_id": rec.get("site_id"),
            "depth_top_cm": rec.get("depth_top_cm"),
            "depth_bottom_cm": rec.get("depth_bottom_cm"),
            "soc_g_kg": rec.get("soc_g_kg"),
            "bulk_density_g_cm3": rec.get("bulk_density_g_cm3"),
            "agb_mg_ha": rec.get("agb_mg_ha"),
            "agb_uncertainty_mg_ha": rec.get("agb_uncertainty_mg_ha"),
            "uncertainty_framework": rec.get("uncertainty_framework"),
            "h_c_org_ratio": rec.get("h_c_org_ratio"),
            "aromatic_c_pct": rec.get("aromatic_c_pct"),
            "persistence_100yr_pct": rec.get("persistence_100yr_pct"),
            "mrv_method_class": rec.get("mrv_method_class"),
            "baseline_credibility": rec.get("baseline_credibility"),
            "permanence_evidence": rec.get("permanence_evidence"),
            "synthetic": True,
        })
    return rows


# ------------------------------------------------------------------
# Seed
# ------------------------------------------------------------------
def test_seed_has_36_valid_synthetic_records():
    assert len(seed_mod.SEED_MEASUREMENTS) == 36
    assert seed_mod.validate_seed() == []


def test_seed_covers_all_record_classes():
    classes = {r["record_class"] for r in seed_mod.SEED_MEASUREMENTS}
    assert classes == {
        "soil-profile", "flux-tower", "biomass-pixel",
        "mrv-method-run", "registry-crosswalk",
    }


def test_seed_method_code_spread():
    methods = {r["analytical_method"] for r in seed_mod.SEED_MEASUREMENTS}
    assert "dry-combustion" in methods
    assert "walkley-black" in methods
    assert "spectroscopy" in methods
    assert "registry-reported" in methods
    assert "unknown" not in methods


def test_seed_biomass_framework_tags_recorded():
    frameworks = {
        r["uncertainty_framework"]
        for r in seed_mod.SEED_MEASUREMENTS
        if r["record_class"] == "biomass-pixel"
    }
    assert frameworks == {"gedi-v2", "cci-v6"}


def test_seed_benchmark_plots_valid():
    bench = seed_mod.build_benchmark_seed()
    assert bench["benchmark"]["name"] == "mrv-blind_v1"
    assert len(bench["plots"]) == 4
    dc = [p for p in bench["plots"] if p["dry_combustion_endpoints"]]
    assert len(dc) == 3
    rev = [p for p in bench["plots"] if p["known_reversion"]]
    assert len(rev) == 1 and rev[0]["plot_code"] == "BLIND-FL-02"


# ------------------------------------------------------------------
# Migration
# ------------------------------------------------------------------
def test_migration_creates_lane_tables():
    sql = MIGRATION.read_text()
    for table in (
        "sci_carbon_measurements",
        "sci_carbon_gold",
        "sci_carbon_blind_plots",
        "sci_carbon_blind_submissions",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_migration_method_comparability_gate():
    sql = MIGRATION.read_text()
    # gold rows can never carry 'unknown'
    assert "CHECK (analytical_method <> 'unknown')" in sql
    # Walkley-Black vs dry-combustion never silently pooled: documented
    assert "never silently pooled" in sql or "never silently mixed" in sql


def test_migration_gold_immutability_trigger():
    sql = MIGRATION.read_text()
    assert "sci_carbon_block_gold_mutation" in sql
    assert "BEFORE UPDATE OR DELETE ON sci_carbon_gold" in sql


def test_migration_blind_scoring_gate():
    sql = MIGRATION.read_text()
    assert "dry_combustion_endpoints" in sql
    assert "known_reversion" in sql
    assert "training_lineage_hash" in sql


# ------------------------------------------------------------------
# Models: comparability + provenance + immutability
# ------------------------------------------------------------------
def test_method_comparable_same_method():
    assert models.method_comparable("dry-combustion", "dry-combustion")


def test_method_comparable_cross_calibrated_group():
    assert models.method_comparable("dry-combustion", "spectroscopy")
    assert models.method_comparable("walkley-black", "loi")


def test_method_comparable_walkley_black_vs_dry_combustion_never_pooled():
    assert not models.method_comparable("walkley-black", "dry-combustion")


def test_method_comparable_unknown_never_comparable():
    assert not models.method_comparable("unknown", "unknown")
    assert not models.method_comparable("unknown", "dry-combustion")
    assert not models.method_comparable("registry-reported", "dry-combustion")


def test_provenance_chain_walk():
    silver_id = _u("silver-1")
    db = FakeDB({
        "sci_carbon_measurements": [{"id": silver_id, "artifact_id": _u("art-1"),
                                     "lab_id": _u("lab-1"), "calibration_id": None}],
        "sci_bronze_artifacts": [{"id": _u("art-1"), "content_hash": "abc"}],
        "sci_labs": [{"id": _u("lab-1"), "name": "Synthetic Lab"}],
    })
    link = models.walk_provenance(db, silver_id)
    assert link.silver["id"] == silver_id
    assert link.bronze_artifact["content_hash"] == "abc"
    assert link.lab["name"] == "Synthetic Lab"
    assert link.calibration is None


def test_provenance_walk_missing_silver_raises():
    db = FakeDB({})
    with pytest.raises(LookupError):
        models.walk_provenance(db, _u("nope"))


def test_version_mutation_attempt_fails():
    with pytest.raises(models.DatasetImmutableError):
        models.guard_dataset_version_mutation()


def test_correction_supersedes_never_mutates():
    row = models.build_correction(
        silver_id=_u("silver-1"),
        corrected_values={"soc_g_kg": 12.4},
        new_dataset_version_id=_u("version-1.1.0"),
        reason="recalibrated dry-combustion run",
    )
    assert row["lane"] == "carbon"
    assert row["corrects_silver_table"] == "sci_carbon_measurements"
    assert row["corrected_values"] == {"soc_g_kg": 12.4}


def test_correction_requires_values():
    with pytest.raises(ValueError):
        models.build_correction(_u("s"), {}, _u("v"), "reason")


# ------------------------------------------------------------------
# MRV-BLIND scoring
# ------------------------------------------------------------------
def _blind_fixtures():
    plots = [
        models.BlindPlot(plot_code="P1", dry_combustion_endpoints=True,
                         known_reversion=False, actual_soc_change_mg_ha=10.0),
        models.BlindPlot(plot_code="P2", dry_combustion_endpoints=True,
                         known_reversion=True, actual_soc_change_mg_ha=-4.0),
        models.BlindPlot(plot_code="P3", dry_combustion_endpoints=False,
                         known_reversion=False, actual_soc_change_mg_ha=6.0),
    ]
    preds = [
        models.BlindPrediction(plot_code="P1", soc_change_mg_ha=9.0,
                               interval_lo=7.0, interval_hi=11.0,
                               nominal_confidence=0.9),
        models.BlindPrediction(plot_code="P2", soc_change_mg_ha=-3.0,
                               interval_lo=-6.0, interval_hi=0.0,
                               nominal_confidence=0.9,
                               flags_additionality_failure=True),
        models.BlindPrediction(plot_code="P3", soc_change_mg_ha=100.0,
                               interval_lo=99.0, interval_hi=101.0,
                               nominal_confidence=0.9),
    ]
    return preds, plots


def test_blind_scoring_gates_non_dry_combustion():
    preds, plots = _blind_fixtures()
    score = models.score_mrv_blind(preds, plots)
    # P3 has no dry-combustion anchor: excluded even though its prediction
    # is wildly wrong.
    assert score.plots_scored == 2


def test_blind_scoring_reversion_bonus():
    preds, plots = _blind_fixtures()
    score = models.score_mrv_blind(preds, plots)
    # P2 correctly flagged the known reversion: +0.5 bonus.
    assert score.reversion_bonus == pytest.approx(0.5)


def test_blind_scoring_false_flag_penalized():
    preds, plots = _blind_fixtures()
    preds[0] = preds[0].model_copy(update={"flags_additionality_failure": True})
    score = models.score_mrv_blind(preds, plots)
    assert score.reversion_bonus == pytest.approx(-0.5)  # +0.5 - 1.0


def test_blind_scoring_no_scorable_plots_raises():
    preds, plots = _blind_fixtures()
    sealed = [p.model_copy(update={"actual_soc_change_mg_ha": None}) for p in plots]
    with pytest.raises(ValueError, match="no scorable plots"):
        models.score_mrv_blind(preds, sealed)


def test_blind_prediction_validation_rejects_bad_interval():
    with pytest.raises(ValueError, match="strictly inside"):
        models.validate_blind_prediction({
            "plot_code": "P1", "soc_change_mg_ha": 20.0,
            "interval_lo": 7.0, "interval_hi": 11.0,
            "nominal_confidence": 0.9,
        })


# ------------------------------------------------------------------
# Router: tolls, paywall, gating
# ------------------------------------------------------------------
def test_query_emits_toll_with_idempotency_key(monkeypatch):
    db = FakeDB({"sci_carbon_gold": _gold_rows()})
    spy = TollSpy()
    monkeypatch.setattr(paywall, "metered_toll", spy)
    router.query_gold(db, "org-1", "req-1", record_class="soil-profile")
    assert spy.calls
    assert spy.calls[0]["idempotency_key"] == "sci:req-1"
    assert spy.calls[0]["event_type"] == "data.science.query"


def test_query_comparable_group_filter(monkeypatch):
    db = FakeDB({"sci_carbon_gold": _gold_rows()})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    env = router.query_gold(db, "org-1", "req-1", comparable_group="dry-combustion-equivalent")
    methods = {r["analytical_method"] for r in env.data}
    assert methods <= {"dry-combustion", "spectroscopy"}
    assert "walkley-black" not in methods


def test_query_rejects_bad_record_class(monkeypatch):
    db = FakeDB({})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    with pytest.raises(ValueError, match="record_class"):
        router.query_gold(db, "org-1", "req-1", record_class="nonsense")


def test_gold_query_neutral_toll(monkeypatch):
    # TW-606: bulk pull is tolled at the neutral per-row rate, not flat bulk.
    # No version row in FakeDB -> falls back to legacy flat query toll.
    db = FakeDB({"sci_carbon_gold": _gold_rows()})
    spy = TollSpy()
    monkeypatch.setattr(paywall, "metered_toll", spy)
    router.query_gold(db, "org-1", "req-1", limit=2000)
    kinds = {c["event_type"] for c in spy.calls}
    assert "data.science.bulk" not in kinds  # flat bulk toll is gone
    assert "data.science.query" in kinds  # fallback flat toll fires


def test_gold_query_neutral_toll_with_version(monkeypatch):
    # With a version row, the neutral api_rows toll fires instead.
    import app.services.toll_service as toll_service

    calls = []

    def fake_record(db, org_id=None, event_type=None, quantity=1.0,
                    amount_cents=None, idempotency_key=None, **kw):
        calls.append({"event_type": event_type, "amount_cents": amount_cents,
                      "quantity": quantity})
        return {"ok": True}

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    db = FakeDB({
        "sci_carbon_gold": _gold_rows(),
        "sci_dataset_versions": [
            {"id": "ver-1", "lane": "carbon", "price_floor_cents": 1,
             "row_count": 10,
             "released_at": "2026-10-18T00:00:00+00:00"},
        ],
        "toll_events": [],
    })
    rows = _gold_rows()
    router.query_gold(db, "org-1", "req-1", limit=2000)
    kinds = {c["event_type"] for c in calls}
    assert "data.science.api_rows" in kinds
    # full pull of 10/10 rows at $0.01 download price -> 1c, neutral
    api_call = next(c for c in calls if c["event_type"] == "data.science.api_rows")
    assert api_call["quantity"] == float(len(rows))
    assert api_call["amount_cents"] == 1


def test_toll_valueerror_is_logged_not_raised(monkeypatch):
    db = FakeDB({"sci_carbon_gold": _gold_rows()})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy(fail_with=ValueError("boom")))
    # must not raise
    router.query_gold(db, "org-1", "req-1")


def test_toll_unavailable_context_continues(monkeypatch):
    db = FakeDB({"sci_carbon_gold": _gold_rows()})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy(fail_with=RuntimeError("down")))
    env = router.query_gold(db, "org-1", "req-1")
    assert env.as_of["dataset"] == "carbon-trust"


def test_envelope_contract():
    env = schemas.envelope(data=[], dataset_name="carbon-trust",
                           semver="1.0.0", lab_count=3, synthetic=True)
    assert env.as_of == {"dataset": "carbon-trust", "semver": "1.0.0"}
    assert env.provenance.lab_count == 3
    assert env.synthetic is True


def test_http_query_requires_subscription(monkeypatch):
    db = FakeDB({"organizations": [{"id": "org-9", "subscription_status": "past_due"}]})
    with pytest.raises(Exception) as exc:
        router._gate(db, "org-9")
    assert getattr(exc.value, "status_code", None) == 402


def test_http_query_unscoped_org_fails_closed():
    db = FakeDB({})
    with pytest.raises(Exception) as exc:
        router._gate(db, "unscoped")
    assert getattr(exc.value, "status_code", None) == 402


def test_http_download_excel_small_tier(monkeypatch):
    # TW-606 Rev 3: the download fee is the dataset's live curve price
    # (pennies, $0.10 cap) -- same for every audience category
    tier = paywall.dataset_tier(36)
    assert tier == "small"
    db = FakeDB({})
    assert paywall.download_fee_cents(db, "org-1", "ver-1") == 1
    assert paywall.delivery_for_tier(tier)["excel"] is True


def test_http_download_large_tier_api_only():
    tier = paywall.dataset_tier(2_000_000)
    assert tier == "large"
    with pytest.raises(Exception) as exc:
        paywall.check_excel_delivery(tier, 2_000_000)
    assert getattr(exc.value, "status_code", None) == 403


def test_ingest_self_healing_and_toll(monkeypatch):
    db = FakeDB({})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    good = dict(seed_mod.SEED_MEASUREMENTS[0])
    bad = dict(seed_mod.SEED_MEASUREMENTS[1])
    bad.pop("artifact_id")
    batch = schemas.IngestBatch(measurements=[good, bad], content_hash="x" * 64)
    result = router.ingest_batch(db, batch, "org-1", "req-1")
    assert result.accepted == 1
    assert len(result.dead_letters) == 1
    assert len(result.quarantined) == 0


def test_ingest_quarantines_unknown_method(monkeypatch):
    db = FakeDB({})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    rec = dict(seed_mod.SEED_MEASUREMENTS[0])
    rec["analytical_method"] = "unknown"
    batch = schemas.IngestBatch(measurements=[rec], content_hash="x" * 64)
    result = router.ingest_batch(db, batch, "org-1", "req-1")
    assert result.accepted == 0
    assert len(result.quarantined) == 1
    assert result.quarantined[0]["partition"] == "provenance_incomplete"


def test_ingest_assigns_comparable_group(monkeypatch):
    db = FakeDB({})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    rec = dict(seed_mod.SEED_MEASUREMENTS[2])  # dry-combustion soil profile
    batch = schemas.IngestBatch(measurements=[rec], content_hash="x" * 64)
    router.ingest_batch(db, batch, "org-1", "req-1")
    table, payload = db.inserts[0]
    assert table == "sci_carbon_measurements"
    assert payload["method_comparable_group"] == "dry-combustion-equivalent"


def test_ingest_quarantines_unsigned_payload(monkeypatch):
    db = FakeDB({})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    rec = dict(seed_mod.SEED_MEASUREMENTS[0])
    rec["collector_signature"] = None
    batch = schemas.IngestBatch(measurements=[rec], content_hash="x" * 64)
    result = router.ingest_batch(db, batch, "org-1", "req-1")
    assert result.accepted == 0
    assert len(result.quarantined) == 1


def test_blind_submit_requires_lineage_hash(monkeypatch):
    db = FakeDB({"sci_benchmarks": []})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    sub = schemas.BlindSubmissionIn(
        submitter="team-x",
        training_lineage_hash="not-a-hash",
        holdout_declaration_hash="h" * 64,
        predictions=[schemas.BlindPredictionIn(
            plot_code="BLIND-FL-01", soc_change_mg_ha=5.0,
            interval_lo=3.0, interval_hi=7.0, nominal_confidence=0.9,
        )],
    )
    with pytest.raises(ValueError, match="training_lineage_hash"):
        router.submit_blind(db, sub, "org-1", "req-1")


def test_blind_submit_requires_open_benchmark(monkeypatch):
    db = FakeDB({"sci_benchmarks": []})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    sub = schemas.BlindSubmissionIn(
        submitter="team-x",
        training_lineage_hash="a" * 64,
        holdout_declaration_hash="h" * 64,
        predictions=[schemas.BlindPredictionIn(
            plot_code="BLIND-FL-01", soc_change_mg_ha=5.0,
            interval_lo=3.0, interval_hi=7.0, nominal_confidence=0.9,
        )],
    )
    with pytest.raises(LookupError, match="no open"):
        router.submit_blind(db, sub, "org-1", "req-1")


def test_blind_submit_rejects_duplicate_plot(monkeypatch):
    bench = {"id": _u("bench-1"), "lane": "carbon", "name": "mrv-blind_v1",
             "status": "open", "dataset_version_id": _u("version-1.0.0"),
             "metric": "interval_calibrated_error_soc_change"}
    db = FakeDB({"sci_benchmarks": [bench]})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    pred = schemas.BlindPredictionIn(
        plot_code="BLIND-FL-01", soc_change_mg_ha=5.0,
        interval_lo=3.0, interval_hi=7.0, nominal_confidence=0.9,
    )
    sub = schemas.BlindSubmissionIn(
        submitter="team-x", training_lineage_hash="a" * 64,
        holdout_declaration_hash="h" * 64, predictions=[pred, pred],
    )
    with pytest.raises(ValueError, match="duplicate prediction"):
        router.submit_blind(db, sub, "org-1", "req-1")


def test_blind_submit_accepts_valid(monkeypatch):
    bench = {"id": _u("bench-1"), "lane": "carbon", "name": "mrv-blind_v1",
             "status": "open", "dataset_version_id": _u("version-1.0.0"),
             "metric": "interval_calibrated_error_soc_change"}
    db = FakeDB({"sci_benchmarks": [bench]})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    sub = schemas.BlindSubmissionIn(
        submitter="team-x", training_lineage_hash="a" * 64,
        holdout_declaration_hash="h" * 64,
        predictions=[schemas.BlindPredictionIn(
            plot_code="BLIND-FL-01", soc_change_mg_ha=5.0,
            interval_lo=3.0, interval_hi=7.0, nominal_confidence=0.9,
        )],
    )
    out = router.submit_blind(db, sub, "org-1", "req-1")
    assert out.status == "submitted"
    assert out.predictions_accepted == 1


def test_blind_plots_sealed(monkeypatch):
    plots = seed_mod.build_benchmark_seed()["plots"]
    db = FakeDB({"sci_carbon_blind_plots": plots})
    monkeypatch.setattr(paywall, "metered_toll", TollSpy())
    env = router.list_blind_plots(db, "org-1", "req-1")
    assert len(env.data) == 4
    for p in env.data:
        assert "sealed_measurement_hash" not in p  # measurements stay sealed


def test_http_ingest_requires_api_key(monkeypatch):
    db = FakeDB({"sci_api_keys": []})
    with pytest.raises(Exception) as exc:
        from app.data_layer.science.auth import require_science_key
        require_science_key("bad-key", db, "carbon")
    assert getattr(exc.value, "status_code", None) == 401
