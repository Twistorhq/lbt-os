# Prepared by Twistor Holdings LLC
"""Subseasonal lane tests (TW-593 Track 2).

Green tests: seed validity + coverage, migration constraint presence,
provenance-chain walk, version immutability, correction supersession,
toll emission attempted (mocked) incl. the ValueError-swallow path.

No live DB, no network, no supabase package: a FakeDB mimics the
supabase-py call pattern (table().select().eq().execute()).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCIENCE_DIR = Path(__file__).resolve().parents[1] / "app" / "data_layer" / "science"
sys.path.insert(0, str(SCIENCE_DIR))

from subseasonal import models, router, schemas  # noqa: E402
from subseasonal import seed as seed_mod  # noqa: E402

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migration_sci_subseasonal_v1.sql"
)


def _u(name: str) -> str:
    """Deterministic fixture UUIDs (schemas require valid UUIDs)."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"twistor.test.s2s.{name}"))


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

    def __call__(self, db, **kwargs):
        if self.fail_with:
            raise self.fail_with
        self.calls.append(kwargs)
        return {"ok": True}


def _versions():
    v1, v2 = _u("version-1.0.0"), _u("version-1.1.0")
    return [
        {
            "id": v1,
            "lane": "subseasonal",
            "dataset_name": "twistor-s2s-forecasts",
            "semver": "1.0.0",
            "row_count": 30,
            "content_root_hash": "root0",
            "benchmark_pin": "s2s_quest_v1",
            "doi": None,
            "supersedes_id": None,
            "released_at": "2026-10-18T00:00:00+00:00",
            "release_notes": "V1 synthetic seed",
        },
        {
            "id": v2,
            "lane": "subseasonal",
            "dataset_name": "twistor-s2s-forecasts",
            "semver": "1.1.0",
            "row_count": 30,
            "content_root_hash": "root1",
            "benchmark_pin": "s2s_quest_v1",
            "doi": None,
            "supersedes_id": v1,
            "released_at": "2026-10-19T00:00:00+00:00",
            "release_notes": "correction propagation",
        },
    ]


def _provenance_tables():
    return {
        "sci_subseasonal_measurements": [
            {
                "id": _u("silver-1"),
                "artifact_id": _u("artifact-1"),
                "lab_id": _u("lab-1"),
                "calibration_id": None,
                "variable": "t2m",
            }
        ],
        "sci_bronze_artifacts": [
            {
                "id": _u("artifact-1"),
                "lane": "subseasonal",
                "content_hash": "abc123",
                "collector_signature": "sig-fixture",
                "lab_id": _u("lab-1"),
            }
        ],
        "sci_labs": [
            {
                "id": _u("lab-1"),
                "name": "Fixture Lab",
                "verification_status": "verified",
            }
        ],
        "sci_calibrations": [],
    }


# ------------------------------------------------------------------
# Seed: validity + coverage
# ------------------------------------------------------------------
def test_seed_has_30_valid_synthetic_records():
    assert len(seed_mod.SEED_MEASUREMENTS) == 30
    for i, rec in enumerate(seed_mod.SEED_MEASUREMENTS):
        m = schemas.IngestMeasurement(**{**rec, "collector_signature": "synthetic-fixture"})
        assert m.synthetic is True, f"record {i} not labeled synthetic"
        assert 2 <= m.lead_weeks <= 6
        assert m.crps >= 0
        assert m.conditions.bias_correction in ("pbc_v1", "none")
        assert m.calibration_id is None  # model runs: no instrument chain


def test_seed_covers_schema_range():
    recs = seed_mod.SEED_MEASUREMENTS
    dates = sorted(r["measured_at"] for r in recs)
    assert dates[0].startswith("2024-"), "seed must start in 2024"
    assert dates[-1].startswith("2026-"), "seed must reach 2026"
    assert {r["lead_weeks"] for r in recs} == {2, 3, 4, 5, 6}
    assert {r["variable"] for r in recs} == {"t2m", "pr", "mslp"}
    assert {r["conditions"]["bias_correction"] for r in recs} == {"pbc_v1", "none"}
    # Narrative check: corrected runs beat climatology; raw runs don't.
    for r in recs:
        if r["conditions"]["bias_correction"] == "pbc_v1":
            assert r["skill_vs_climatology"] > 0, "pbc_v1 must beat climatology (the breach)"
        else:
            assert r["skill_vs_climatology"] < 0, "raw runs must trail climatology (the desert)"


# ------------------------------------------------------------------
# Migration: constraint presence
# ------------------------------------------------------------------
def _sql() -> str:
    assert MIGRATION.exists(), f"migration missing: {MIGRATION}"
    return MIGRATION.read_text()


def test_migration_creates_lane_tables():
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS sci_subseasonal_measurements" in sql
    assert "CREATE TABLE IF NOT EXISTS sci_subseasonal_gold" in sql


def test_migration_silver_constraints():
    sql = _sql()
    assert "REFERENCES sci_bronze_artifacts(id)" in sql
    assert "CHECK (lead_weeks BETWEEN 2 AND 6)" in sql
    assert "CHECK (crps >= 0)" in sql
    assert "chk_sci_s2s_conditions_keys" in sql
    cal_line = next(line for line in sql.splitlines() if "calibration_id" in line and "UUID" in line)
    assert "REFERENCES sci_calibrations(id)" in cal_line
    assert "NOT NULL" not in cal_line, "calibration_id must stay NULL-able for model runs"


def test_migration_gold_immutability_trigger():
    sql = _sql()
    assert "trg_sci_s2s_gold_immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON sci_subseasonal_gold" in sql
    assert "RAISE EXCEPTION" in sql
    assert "UNIQUE (dataset_version_id, silver_id)" in sql


# ------------------------------------------------------------------
# Provenance chain walk
# ------------------------------------------------------------------
def test_provenance_chain_walk():
    chain = models.walk_provenance(FakeDB(_provenance_tables()), _u("silver-1"))
    assert chain.silver["id"] == _u("silver-1")
    assert chain.bronze_artifact["content_hash"] == "abc123"
    assert chain.bronze_artifact["collector_signature"] == "sig-fixture"
    assert chain.lab["name"] == "Fixture Lab"
    assert chain.calibration is None  # model run: no instrument chain


def test_provenance_walk_missing_silver_raises():
    with pytest.raises(LookupError):
        models.walk_provenance(FakeDB(_provenance_tables()), "nope")


# ------------------------------------------------------------------
# Version immutability + correction supersession
# ------------------------------------------------------------------
def test_version_mutation_attempt_fails():
    with pytest.raises(models.DatasetImmutableError):
        models.guard_dataset_version_mutation()


def test_correction_supersedes_never_mutates():
    original = {"id": "silv-1", "crps": 2.5, "skill_vs_climatology": -0.1}
    correction = models.build_correction(
        silver_id="silv-1",
        corrected_values={"crps": 2.1},
        new_dataset_version_id="v-2",
        reason="reprocessed with fixed land mask",
        corrected_by_lab_id="lab-1",
    )
    assert correction["lane"] == "subseasonal"
    assert correction["corrects_silver_table"] == "sci_subseasonal_measurements"
    assert correction["corrects_silver_id"] == "silv-1"
    assert correction["corrected_values"] == {"crps": 2.1}
    assert correction["new_dataset_version_id"] == "v-2"
    assert original == {"id": "silv-1", "crps": 2.5, "skill_vs_climatology": -0.1}


def test_correction_requires_values():
    with pytest.raises(ValueError):
        models.build_correction("silv-1", {}, "v-2", "empty")


# ------------------------------------------------------------------
# Toll emission (mocked)
# ------------------------------------------------------------------
def test_query_emits_toll_with_idempotency_key(monkeypatch):
    spy = TollSpy()
    monkeypatch.setattr(router, "_get_toll_recorder", lambda: spy)
    db = FakeDB({"sci_dataset_versions": _versions()})
    env = router.list_dataset_versions(db, "org-1", "req-1")
    assert spy.calls, "toll emission was not attempted"
    call = spy.calls[0]
    assert call["event_type"] == "data.science.query"
    assert call["idempotency_key"] == "sci:req-1"
    assert call["org_id"] == "org-1"
    assert env.as_of["dataset"] == "twistor-s2s-forecasts"
    assert env.synthetic is True


def test_gold_query_filters_and_bulk_toll(monkeypatch):
    spy = TollSpy()
    monkeypatch.setattr(router, "_get_toll_recorder", lambda: spy)
    rows = [
        {
            "id": _u(f"gold-{i}"),
            "model_source": "ecmwf_ifs",
            "init_date": "2025-01-15",
            "lead_weeks": 3,
            "ensemble_size": 51,
            "variable": "t2m",
            "region": "CONUS",
            "crps": 1.2,
            "skill_vs_climatology": 0.1,
            "synthetic": True,
            "conditions": {"bias_correction": "pbc_v1", "base_model": "ecmwf_ifs_cy48r1"},
        }
        for i in range(3)
    ]
    db = FakeDB({"sci_subseasonal_gold": rows})
    env = router.query_gold(
        db, "org-1", "req-2", variable="t2m", region="CONUS", lead_weeks=3, limit=2000
    )
    assert len(env.data) == 3
    assert env.data[0]["bias_correction"] == "pbc_v1"
    types = [c["event_type"] for c in spy.calls]
    assert "data.science.query" in types
    assert "data.science.bulk" in types  # limit > 1000 -> bulk toll per 1k rows
    bulk = next(c for c in spy.calls if c["event_type"] == "data.science.bulk")
    assert bulk["idempotency_key"] == "sci:req-2:bulk"


def test_query_rejects_out_of_range_lead_weeks():
    with pytest.raises(ValueError, match="lead_weeks"):
        router.query_gold(FakeDB({}), "org-1", "req-x", lead_weeks=8)


def test_benchmarks_emit_benchmark_toll(monkeypatch):
    spy = TollSpy()
    monkeypatch.setattr(router, "_get_toll_recorder", lambda: spy)
    db = FakeDB(
        {
            "sci_benchmarks": [
                {
                    "id": _u("benchmark-1"),
                    "lane": "subseasonal",
                    "name": "s2s_quest_v1",
                    "dataset_version_id": _u("version-1.0.0"),
                    "metric": "crps_skill",
                    "status": "open",
                    "sci_benchmark_runs": [
                        {"submitter": "twistor-pbc", "score": 0.12, "submitted_at": "2026-10-18T00:00:00+00:00"}
                    ],
                }
            ]
        }
    )
    env = router.list_benchmarks(db, "org-1", "req-3")
    assert spy.calls[0]["event_type"] == "data.science.benchmark"
    assert spy.calls[0]["idempotency_key"] == "sci:req-3"
    assert env.data[0]["leaderboard"][0]["submitter"] == "twistor-pbc"


def test_ingest_self_healing_and_toll(monkeypatch):
    spy = TollSpy()
    monkeypatch.setattr(router, "_get_toll_recorder", lambda: spy)
    db = FakeDB({})
    good = {
        **seed_mod.SEED_MEASUREMENTS[0],
        "collector_signature": "sig-ok",
    }
    unsigned = {**seed_mod.SEED_MEASUREMENTS[1]}  # no signature -> quarantine
    no_artifact = {**seed_mod.SEED_MEASUREMENTS[2], "artifact_id": None, "collector_signature": "sig-ok"}
    batch = schemas.IngestBatch(measurements=[good, unsigned, no_artifact], content_hash="h")
    result = router.ingest_batch(db, batch, "org-1", "req-9")
    assert result.accepted == 1
    assert len(result.quarantined) == 1
    assert len(result.dead_letters) == 1
    assert len(db.inserts) == 1
    assert db.inserts[0][0] == "sci_subseasonal_measurements"
    ingest_calls = [c for c in spy.calls if c["event_type"] == "data.science.ingest"]
    assert ingest_calls and ingest_calls[0]["idempotency_key"] == "sci:req-9"
    assert ingest_calls[0]["quantity"] == 1.0


def test_toll_valueerror_is_logged_not_raised(monkeypatch):
    """Unknown event type (rates not wired yet) must never break serving."""
    spy = TollSpy(fail_with=ValueError("unknown event type 'data.science.query'"))
    monkeypatch.setattr(router, "_get_toll_recorder", lambda: spy)
    env = router.list_dataset_versions(FakeDB({"sci_dataset_versions": _versions()}), "org-1", "req-4")
    assert len(env.data) == 2  # served fine despite toll failure


def test_toll_unavailable_context_continues():
    """When the toll stack can't even import (no supabase here), serving continues."""
    env = router.list_dataset_versions(FakeDB({"sci_dataset_versions": _versions()}), "org-1", "req-5")
    assert len(env.data) == 2


# ------------------------------------------------------------------
# Response contract
# ------------------------------------------------------------------
def test_envelope_contract():
    env = schemas.envelope(
        data=[1, 2],
        dataset_name="twistor-s2s-forecasts",
        semver="1.0.0",
        lab_count=3,
        synthetic=True,
        attestation_summary={"reproduced": 2, "failed": 0},
    )
    assert env.as_of == {"dataset": "twistor-s2s-forecasts", "semver": "1.0.0"}
    assert env.provenance.lab_count == 3
    assert env.provenance.attestation_summary["reproduced"] == 2
    assert env.synthetic is True
