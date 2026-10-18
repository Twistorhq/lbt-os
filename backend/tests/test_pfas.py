"""Tests for the TW-593 PFAS lane (TW-595). Prepared by Twistor Holdings LLC.

Covers, per the lane build contract:
- schema constraints (lane vocabularies, ranges, conditions keys)
- provenance chain walk (gold -> silver -> bronze -> lab -> calibration)
- version immutability (released versions are append-only)
- correction supersession (errata supersede, never mutate)
- toll emission attempted on serve paths (mocked; ValueError -> log+continue)

Unit-level with fakes: no live database, no network.
"""
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data_layer.science.pfas import models, router, schemas, seed

MIGRATION_SQL = Path(__file__).resolve().parent.parent.parent / "supabase" / "migration_sci_pfas_v1.sql"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]
        self._single = False
        self.inserted = []

    def select(self, *_a):
        return self

    def eq(self, col, value):
        self._rows = [r for r in self._rows if r.get(col) == value]
        return self

    def gte(self, col, value):
        self._rows = [r for r in self._rows if (r.get(col) or 0) >= value]
        return self

    def order(self, col, desc=False):
        self._rows = sorted(self._rows, key=lambda r: r.get(col) or "", reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def maybe_single(self):
        self._single = True
        return self

    def insert(self, row):
        if getattr(self, "_fail_insert", False):
            raise RuntimeError("fake insert boom")
        self.inserted.append(dict(row))
        return self

    def upsert(self, row, on_conflict=None):
        self.inserted.append(dict(row))
        return self

    def execute(self):
        if self._single:
            return FakeResult(self._rows[0] if self._rows else None)
        return FakeResult(list(self._rows))


class FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        if name == "sci_benchmarks_missing":
            raise RuntimeError("no such table")
        return FakeTable(self._tables.get(name, []))


def _version_row():
    return {
        "id": "ver-1",
        "lane": "pfas",
        "dataset_name": "twistor-pfas-destruction",
        "semver": "1.0.0",
        "row_count": 30,
        "content_root_hash": "abc123",
        "benchmark_pin": "pfas_method_bench_v1",
        "doi": None,
        "supersedes_id": None,
        "released_at": "2026-10-18T00:00:00+00:00",
        "release_notes": "V1 synthetic seed",
    }


def _gold_row(i=0):
    return {
        "id": f"gold-{i}",
        "measurement_id": f"meas-{i}",
        "dataset_version_id": "ver-1",
        "compound": "PFOA",
        "destruction_method": "thermal",
        "destruction_pct": 97.5,
        "attestation_summary": {"reproduced": 2, "partial": 0, "failed": 0},
        "synthetic": True,
    }


def _chain_rows():
    silver = {
        "id": "meas-0", "artifact_id": "art-0", "lab_id": "lab-0",
        "calibration_id": "cal-0",
        "compound": "PFOA", "destruction_method": "thermal",
        "destruction_pct": 97.5, "measured_at": "2025-01-01T00:00:00+00:00",
        "synthetic": True,
    }
    bronze = {
        "id": "art-0", "lab_id": "lab-0", "content_hash": "sha256:deadbeef",
        "collector_signature": "ed25519:sig", "collected_at": "2025-01-01T00:00:00+00:00",
    }
    lab = {"id": "lab-0", "name": "Twistor Synthetic Lab", "kind": "independent",
           "verification_status": "verified"}
    cal = {"id": "cal-0", "instrument_kind": "gc_ms",
           "calibrated_at": "2024-12-01T00:00:00+00:00", "standard_ref": "NIST SRM 2779"}
    return silver, bronze, lab, cal


def _valid_measurement_dict(i=0):
    return {
        "artifact_id": f"art-{i}",
        "lab_id": f"lab-{i}",
        "calibration_id": None,
        "conditions": {"temp_c": 900.0, "residence_s": 10.0, "matrix": "groundwater"},
        "measured_at": "2025-06-01T00:00:00+00:00",
        "compound": "PFOA",
        "destruction_method": "thermal",
        "destruction_pct": 97.5,
        "analytical_method": "LC-MS/MS",
        "detection_limit_ppt": 0.5,
        "synthetic": True,
    }


# ---------------------------------------------------------------------------
# Schema constraints
# ---------------------------------------------------------------------------

def test_schemas_reject_out_of_range_pct():
    with pytest.raises(ValidationError):
        schemas.PFASMeasurementIn(**{**_valid_measurement_dict(), "destruction_pct": 101})


def test_schemas_reject_unknown_compound():
    with pytest.raises(ValidationError):
        schemas.PFASMeasurementIn(**{**_valid_measurement_dict(), "compound": "XYZ"})


def test_schemas_reject_unknown_method():
    with pytest.raises(ValidationError):
        schemas.PFASMeasurementIn(**{**_valid_measurement_dict(), "destruction_method": "microwave"})


def test_schemas_reject_missing_condition_key():
    bad = _valid_measurement_dict()
    bad["conditions"] = {"temp_c": 900.0, "residence_s": 10.0}  # no matrix
    with pytest.raises(ValidationError):
        schemas.PFASMeasurementIn(**bad)


def test_schemas_reject_nonpositive_detection_limit():
    with pytest.raises(ValidationError):
        schemas.PFASMeasurementIn(**{**_valid_measurement_dict(), "detection_limit_ppt": 0})


def test_models_validate_row_catches_everything():
    row = _valid_measurement_dict()
    row.update({"compound": "NOPE", "destruction_pct": -5, "detection_limit_ppt": 0,
                "artifact_id": "", "conditions": {"temp_c": "hot"}})
    errors = models.validate_measurement_row(row)
    assert len(errors) >= 5


def test_models_validate_row_accepts_good_row():
    assert models.validate_measurement_row(_valid_measurement_dict()) == []


# ---------------------------------------------------------------------------
# Per-record ingestion isolation (TW-208)
# ---------------------------------------------------------------------------

def test_split_valid_records_isolates_poison():
    good = _valid_measurement_dict(0)
    poison = _valid_measurement_dict(1)
    poison["destruction_pct"] = 150  # out of range
    missing = _valid_measurement_dict(2)
    del missing["conditions"]["matrix"]
    valid, dead = models.split_valid_records([good, poison, missing])
    assert len(valid) == 1
    assert len(dead) == 2
    assert dead[0]["index"] == 1
    assert dead[1]["index"] == 2
    assert all(dead[d]["errors"] for d in (0, 1))


# ---------------------------------------------------------------------------
# Provenance chain walk
# ---------------------------------------------------------------------------

def test_provenance_chain_walk_full():
    silver, bronze, lab, cal = _chain_rows()
    gold = {"id": "gold-0", "measurement_id": "meas-0", "dataset_version_id": "ver-1"}
    chain = models.walk_provenance(
        gold,
        {"meas-0": silver}, {"art-0": bronze}, {"lab-0": lab}, {"cal-0": cal},
    )
    assert chain["measurement"]["compound"] == "PFOA"
    assert chain["bronze_artifact"]["content_hash"] == "sha256:deadbeef"
    assert chain["bronze_artifact"]["collector_signature"] == "ed25519:sig"
    assert chain["lab"]["name"] == "Twistor Synthetic Lab"
    assert chain["calibration"]["standard_ref"] == "NIST SRM 2779"


def test_provenance_chain_walk_broken_link_raises():
    silver, _bronze, lab, cal = _chain_rows()
    gold = {"id": "gold-0", "measurement_id": "meas-0", "dataset_version_id": "ver-1"}
    with pytest.raises(models.ProvenanceBrokenError):
        models.walk_provenance(gold, {"meas-0": silver}, {}, {"lab-0": lab}, {"cal-0": cal})


def test_provenance_chain_walk_missing_silver_raises():
    gold = {"id": "gold-0", "measurement_id": "ghost", "dataset_version_id": "ver-1"}
    with pytest.raises(models.ProvenanceBrokenError):
        models.walk_provenance(gold, {}, {}, {}, {})


# ---------------------------------------------------------------------------
# Version immutability
# ---------------------------------------------------------------------------

def test_version_immutability_guard_raises():
    with pytest.raises(models.ImmutableVersionError):
        models.guard_version_immutable(_version_row(), attempted="update")


def test_migration_declares_gold_immutable_trigger():
    sql = MIGRATION_SQL.read_text()
    assert "sci_pfas_gold_no_mutation" in sql
    assert "BEFORE UPDATE OR DELETE ON public.sci_pfas_gold" in sql


def test_migration_references_shared_core():
    sql = MIGRATION_SQL.read_text()
    for table in ("sci_bronze_artifacts", "sci_labs", "sci_calibrations", "sci_dataset_versions"):
        assert table in sql


def test_migration_enforces_lane_vocabularies():
    sql = MIGRATION_SQL.read_text()
    assert "sci_pfas_compound_ck" in sql
    assert "sci_pfas_method_ck" in sql
    assert "sci_pfas_conditions_ck" in sql
    assert "'PFOA'" in sql and "'thermal'" in sql


# ---------------------------------------------------------------------------
# Correction supersession
# ---------------------------------------------------------------------------

def test_correction_supersession_never_mutates():
    original = dict(_valid_measurement_dict(0), id="meas-0", superseded_by=None)
    marker, corrected, record = models.apply_correction(
        original,
        {"destruction_pct": 98.1},
        new_dataset_version_id="ver-2",
        corrected_by_lab_id="lab-9",
        reason="re-analysis with LC-HRMS lowered the blank correction",
    )
    # Original stays queryable, now pointing at its successor.
    assert marker["id"] == "meas-0"
    assert marker["destruction_pct"] == 97.5
    assert marker["superseded_by"] == corrected["id"]
    # New row carries the correction.
    assert corrected["id"] != "meas-0"
    assert corrected["destruction_pct"] == 98.1
    assert corrected["superseded_by"] is None
    assert corrected["compound"] == "PFOA"  # untouched fields carry over
    # Correction record links the chain.
    assert record["lane"] == "pfas"
    assert record["corrects_silver_table"] == "sci_pfas_measurements"
    assert record["corrects_silver_id"] == "meas-0"
    assert record["new_dataset_version_id"] == "ver-2"


def test_correction_rejects_invalid_corrected_values():
    original = dict(_valid_measurement_dict(0), id="meas-0", superseded_by=None)
    with pytest.raises(ValueError):
        models.apply_correction(
            original, {"destruction_pct": 120},
            new_dataset_version_id="ver-2",
            corrected_by_lab_id="lab-9", reason="bad",
        )


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def test_seed_30_rows_deterministic_and_labeled():
    first = seed.generate_measurements()
    second = seed.generate_measurements()
    assert len(first) == 30
    assert first == second  # deterministic
    assert all(m["synthetic"] is True for m in first)
    compounds = {m["compound"] for m in first}
    methods = {m["destruction_method"] for m in first}
    assert compounds == {"PFOA", "PFOS", "GenX", "PFHxS", "PFNA"}
    assert len(methods) == 6
    for m in first:
        assert models.validate_measurement_row(m) == []
        assert {"temp_c", "residence_s", "matrix"} <= set(m["conditions"])
        assert 0 <= m["destruction_pct"] <= 100


def test_seed_dataset_version_merkle_stable():
    v1 = seed.build_dataset_version(30)
    v2 = seed.build_dataset_version(30)
    assert v1["content_root_hash"] == v2["content_root_hash"]
    assert len(v1["content_root_hash"]) == 64
    assert v1["benchmark_pin"] == seed.PFAS_METHOD_BENCHMARK["name"]


def test_seed_gold_rows_cover_measurements():
    version_id = "ver-1"
    gold = seed.build_gold_rows(version_id)
    measurements = seed.generate_measurements()
    assert len(gold) == len(measurements) == 30
    assert {g["measurement_id"] for g in gold} == {m["id"] for m in measurements}
    assert all(g["dataset_version_id"] == version_id for g in gold)


# ---------------------------------------------------------------------------
# Toll emission
# ---------------------------------------------------------------------------

def test_toll_emission_attempted_with_sci_idempotency_key(monkeypatch):
    calls = []

    def fake_record(db, org_id, event_type, quantity=1, idempotency_key=None,
                    source_type=None, source_id=None):
        calls.append({
            "org_id": org_id, "event_type": event_type, "quantity": quantity,
            "idempotency_key": idempotency_key, "source_type": source_type,
        })
        return {"ok": True}

    monkeypatch.setattr(router.toll_service, "record_toll_event", fake_record)
    router._emit_toll(object(), event_type="data.science.query",
                      request_id="req-123", quantity=1.0)
    assert len(calls) == 1
    assert calls[0]["event_type"] == "data.science.query"
    assert calls[0]["idempotency_key"] == "sci:req-123"
    assert calls[0]["source_type"] == "science:pfas"


def test_toll_value_error_logs_and_continues(monkeypatch, caplog):
    def boom(*a, **k):
        raise ValueError("Unknown toll event type: data.science.query")

    monkeypatch.setattr(router.toll_service, "record_toll_event", boom)
    with caplog.at_level(logging.WARNING, logger="app.data_layer.science.pfas.router"):
        result = router._emit_toll(object(), event_type="data.science.query",
                                   request_id="req-9")
    assert result is None  # serving continues
    assert any("toll emission skipped" in r.message for r in caplog.records)


def test_toll_generic_failure_never_breaks_serving(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(router.toll_service, "record_toll_event", boom)
    assert router._emit_toll(object(), event_type="data.science.query",
                             request_id="req-9") is None


# ---------------------------------------------------------------------------
# Router endpoints (FakeDB, tolls mocked)
# ---------------------------------------------------------------------------

def _patch_toll(monkeypatch):
    calls = []

    def fake_record(db, org_id, event_type, quantity=1, idempotency_key=None,
                    source_type=None, source_id=None):
        calls.append({"event_type": event_type, "quantity": quantity,
                      "idempotency_key": idempotency_key})
        return {"ok": True}

    monkeypatch.setattr(router.toll_service, "record_toll_event", fake_record)
    return calls


def test_query_endpoint_filters_and_tolls(monkeypatch):
    db = FakeDB({
        models.VERSIONS_TABLE: [_version_row()],
        models.GOLD_TABLE: [_gold_row(0), _gold_row(1), {**_gold_row(2), "compound": "PFOS"}],
    })
    calls = _patch_toll(monkeypatch)
    resp = router.query_gold(db=db, compound="PFOA", destruction_method=None,
                             min_destruction_pct=None, synthetic=None,
                             limit=200, bulk=False, x_request_id="q-1")
    assert resp.row_count == 2
    assert resp.as_of == "twistor-pfas-destruction 1.0.0"
    assert all(r.synthetic for r in resp.rows)
    assert calls and calls[0]["event_type"] == "data.science.query"
    assert calls[0]["idempotency_key"] == "sci:q-1"


def test_query_endpoint_bulk_emits_bulk_toll(monkeypatch):
    db = FakeDB({models.VERSIONS_TABLE: [_version_row()], models.GOLD_TABLE: [_gold_row(0)]})
    calls = _patch_toll(monkeypatch)
    router.query_gold(db=db, compound=None, destruction_method=None,
                      min_destruction_pct=None, synthetic=None,
                      limit=200, bulk=True, x_request_id="q-2")
    assert calls[0]["event_type"] == "data.science.bulk"


def test_query_endpoint_404_without_versions(monkeypatch):
    from fastapi import HTTPException
    db = FakeDB({models.VERSIONS_TABLE: [], models.GOLD_TABLE: []})
    _patch_toll(monkeypatch)
    with pytest.raises(HTTPException):
        router.query_gold(db=db, compound=None, destruction_method=None,
                          min_destruction_pct=None, synthetic=None,
                          limit=200, bulk=False, x_request_id="q-3")


def test_ingest_accepts_valid_and_dead_letters_db_failures(monkeypatch):
    db = FakeDB({models.SILVER_TABLE: []})
    table = db.table(models.SILVER_TABLE)
    table._fail_insert = True
    # Patch db.table to return the failing table for silver inserts.
    real_table = db.table
    monkeypatch.setattr(db, "table", lambda name: table if name == models.SILVER_TABLE else real_table(name))
    calls = _patch_toll(monkeypatch)

    payload = schemas.IngestRequest(records=[
        schemas.PFASMeasurementIn(**_valid_measurement_dict(0)),
        schemas.PFASMeasurementIn(**_valid_measurement_dict(1)),
    ])
    resp = router.ingest_batch(payload, db=db, x_request_id="ing-1")
    assert resp.accepted == 0
    assert resp.dead_letters == 2
    assert calls and calls[0]["event_type"] == "data.science.ingest"


def test_ingest_accepts_valid_records(monkeypatch):
    db = FakeDB({models.SILVER_TABLE: []})
    calls = _patch_toll(monkeypatch)
    payload = schemas.IngestRequest(
        records=[schemas.PFASMeasurementIn(**_valid_measurement_dict(0))])
    resp = router.ingest_batch(payload, db=db, x_request_id="ing-2")
    assert resp.accepted == 1
    assert resp.dead_letters == 0
    assert resp.dataset_version_pending is True


def test_benchmarks_lists_concept_when_table_missing(monkeypatch):
    class NoBenchDB(FakeDB):
        def table(self, name):
            if name == models.BENCHMARKS_TABLE:
                raise RuntimeError("relation does not exist")
            return super().table(name)

    db = NoBenchDB({})
    resp = router.list_benchmarks(db=db, x_request_id="b-1")
    names = [b.name for b in resp.benchmarks]
    assert seed.PFAS_METHOD_BENCHMARK["name"] in names
    concept = next(b for b in resp.benchmarks if b.name == seed.PFAS_METHOD_BENCHMARK["name"])
    assert concept.metric == "destruction_pct_mae"
    assert concept.status == "concept"


def test_datasets_endpoints(monkeypatch):
    db = FakeDB({models.VERSIONS_TABLE: [_version_row()]})
    listed = router.list_datasets(db=db, x_request_id="d-1")
    assert len(listed.datasets) == 1
    assert listed.datasets[0].content_root_hash == "abc123"
    one = router.get_dataset_version("1.0.0", db=db, x_request_id="d-2")
    assert one.semver == "1.0.0"

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        router.get_dataset_version("9.9.9", db=db, x_request_id="d-3")
