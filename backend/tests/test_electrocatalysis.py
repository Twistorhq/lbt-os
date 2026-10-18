"""Tests for the TW-593 electrocatalysis lane (Dre Coleman, reference implementer).

Covers, per the TW-593 build contract:
  1. schema constraints (pydantic + sqlalchemy),
  2. provenance-chain walk (gold → silver → bronze artifact → lab → calibration),
  3. dataset version immutability (UPDATE/DELETE blocked by trigger),
  4. correction supersession (new version, never a mutation),
  5. toll emission attempted on query (mocked record_toll_event, never 500 on toll),
  6. synthetic V1 seed shape (30 rows, synthetic stamped, conditions complete).

No network, no live database: sqlalchemy models run on sqlite; the router
runs against a fake supabase query-builder.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data_layer.science.electrocatalysis import models, schemas, seed
from app.data_layer.science.electrocatalysis import router as electro_router

_TEST_API_KEY = "test-key-electro"
_TEST_API_KEY_HASH = hashlib.sha256(_TEST_API_KEY.encode()).hexdigest()
_TEST_HEADERS = {"X-API-Key": _TEST_API_KEY}

MIGRATION_CORE = os.path.join(
    os.path.dirname(__file__), "..", "..", "supabase", "migration_sci_core_v1.sql"
)
MIGRATION_LANE = os.path.join(
    os.path.dirname(__file__), "..", "..", "supabase", "migration_sci_electrocatalysis_v1.sql"
)

CORE_SQL = open(MIGRATION_CORE).read()
LANE_SQL = open(MIGRATION_LANE).read()

CONDITIONS = {
    "temp_c": 25.0,
    "pressure_bar": 1.0,
    "electrolyte": "1M KOH",
    "ph": 14.0,
    "solvent": "aqueous",
    "cell_type": "H-cell",
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters: list = []
        self._limit = None
        self._order = None

    def select(self, *a):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def ilike(self, col, pattern):
        self._filters.append(("ilike", col, pattern))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, set(vals)))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, rows):
        if isinstance(rows, dict):
            rows = [rows]
        self._db.inserts.setdefault(self._table, []).extend(rows)
        return self

    def execute(self):
        rows = list(self._db.data.get(self._table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "gte":
                rows = [r for r in rows if r.get(col) is not None and r.get(col) >= val]
            elif op == "in":
                rows = [r for r in rows if r.get(col) in val]
            elif op == "ilike":
                needle = val.strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(col, "")).lower()]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _FakeDB:
    def __init__(self):
        self.data: dict = {}
        self.inserts: dict = {}

    def table(self, name):
        return _FakeQuery(self, name)


def _seeded_fake() -> _FakeDB:
    db = _FakeDB()
    lab_id = str(uuid.uuid4())
    cal_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    db.data["sci_dataset_versions"] = [
        {
            "id": version_id,
            "lane": "electrocatalysis",
            "dataset_name": "twistor-electro-screening",
            "semver": "1.0.0",
            "row_count": 3,
            "content_root_hash": "abc123",
            "benchmark_pin": "catbench_v1",
            "doi": None,
            "supersedes_id": None,
            "released_at": "2026-10-18T00:00:00+00:00",
            "release_notes": "v1 synthetic seed",
        }
    ]
    silver_ids = [str(uuid.uuid4()) for _ in range(3)]
    db.data["sci_electro_measurements"] = [
        {"id": sid, "lab_id": lab_id, "calibration_id": cal_id} for sid in silver_ids
    ]
    db.data["sci_electro_gold"] = [
        {
            "id": str(uuid.uuid4()),
            "silver_id": silver_ids[0],
            "composition": "Fe70Bi15Ni10Sn3Zn2",
            "surface_facet": "(111)",
            "adsorbate": "*N2",
            "faradaic_efficiency": 97.5,
            "experimental_tof": 1.23,
            "attestation_summary": {"reproduced": 0, "partial": 0, "failed": 0},
            "row_hash": "h1",
            "synthetic": True,
        },
        {
            "id": str(uuid.uuid4()),
            "silver_id": silver_ids[1],
            "composition": "Cu85Al15",
            "surface_facet": "(100)",
            "adsorbate": "*CO2",
            "faradaic_efficiency": 80.0,
            "experimental_tof": 0.9,
            "attestation_summary": {"reproduced": 1, "partial": 0, "failed": 0},
            "row_hash": "h2",
            "synthetic": True,
        },
        {
            "id": str(uuid.uuid4()),
            "silver_id": silver_ids[2],
            "composition": "LaNiO3",
            "surface_facet": "perovskite-001",
            "adsorbate": "*O",
            "faradaic_efficiency": 62.0,
            "experimental_tof": 0.3,
            "attestation_summary": {"reproduced": 0, "partial": 1, "failed": 0},
            "row_hash": "h3",
            "synthetic": True,
        },
    ]
    db.data["sci_labs"] = [{"id": lab_id, "verification_status": "verified"}]
    db.data["sci_api_keys"] = [
        {
            "id": str(uuid.uuid4()),
            "key_hash": _TEST_API_KEY_HASH,
            "key_prefix": "test-key",
            "label": "test key",
            "lab_id": None,  # unbound: may write for any lab
            "scopes": ["science:electrocatalysis:write"],
            "rate_limit_per_min": 60,
            "is_active": True,
        }
    ]
    db.data["sci_benchmarks"] = []
    db.data["sci_corrections"] = []
    return db


@pytest.fixture()
def app_client():
    app = FastAPI()
    app.include_router(electro_router.router)
    fake = _seeded_fake()
    app.dependency_overrides[electro_router.get_db] = lambda: fake
    return TestClient(app), fake


# ---------------------------------------------------------------------------
# 1. Schema constraints
# ---------------------------------------------------------------------------

def test_faradaic_efficiency_bounds_rejected():
    with pytest.raises(ValidationError):
        schemas.IngestRecordIn(
            lab_id=uuid.uuid4(),
            content_hash="x",
            collected_at=datetime.now(timezone.utc),
            conditions=schemas.ElectroConditions(**CONDITIONS),
            faradaic_efficiency=101.0,
        )


def test_conditions_missing_required_key_rejected():
    bad = dict(CONDITIONS)
    del bad["electrolyte"]
    with pytest.raises(ValidationError):
        schemas.ElectroConditions(**bad)


def test_blank_composition_rejected():
    with pytest.raises(ValidationError):
        schemas.ElectroMeasurementIn(
            artifact_id=uuid.uuid4(),
            lab_id=uuid.uuid4(),
            conditions=schemas.ElectroConditions(**CONDITIONS),
            composition="  ",
        )


def test_version_semver_uniqueness_enforced():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        v1 = models.SciDatasetVersion(
            id=uuid.uuid4(), lane="electrocatalysis", dataset_name="d",
            semver="1.0.0", row_count=1, content_root_hash="h", release_notes="n",
        )
        s.add(v1)
        s.commit()
        s.add(
            models.SciDatasetVersion(
                id=uuid.uuid4(), lane="electrocatalysis", dataset_name="d",
                semver="1.0.0", row_count=2, content_root_hash="h2", release_notes="n2",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_lane_migration_enforces_conditions_keys_and_fe_range():
    for key in ("temp_c", "pressure_bar", "electrolyte", "ph", "solvent", "cell_type"):
        assert f"CHECK (conditions ? '{key}')" in LANE_SQL
    assert "faradaic_efficiency >= 0 AND faradaic_efficiency <= 100" in LANE_SQL


# ---------------------------------------------------------------------------
# 2. Provenance-chain walk: gold → silver → bronze → lab → calibration
# ---------------------------------------------------------------------------

def test_provenance_chain_walk():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        lab = models.SciLab(id=uuid.uuid4(), name="Synthetic Lab", kind="independent",
                            verification_status="verified")
        cal = models.SciCalibration(id=uuid.uuid4(), lab_id=lab.id, instrument_id="pot-01",
                                    instrument_kind="potentiostat",
                                    calibrated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    standard_ref="Ag/AgCl")
        art = models.SciBronzeArtifact(id=uuid.uuid4(), lane="electrocatalysis",
                                       lab_id=lab.id, storage_path="s/p.json",
                                       content_hash="h", collected_at=datetime.now(timezone.utc),
                                       schema_version=1)
        silver = models.SciElectroMeasurement(
            id=uuid.uuid4(), artifact_id=art.id, lab_id=lab.id, calibration_id=cal.id,
            conditions=dict(CONDITIONS), composition="Fe70Bi15Ni10Sn3Zn2",
            faradaic_efficiency=97.5, synthetic=True,
        )
        ver = models.SciDatasetVersion(id=uuid.uuid4(), lane="electrocatalysis",
                                       dataset_name="twistor-electro-screening",
                                       semver="1.0.0", row_count=1, content_root_hash="root",
                                       release_notes="seed")
        gold = models.SciElectroGold(id=uuid.uuid4(), silver_id=silver.id,
                                     dataset_version_id=ver.id,
                                     composition="Fe70Bi15Ni10Sn3Zn2",
                                     faradaic_efficiency=97.5,
                                     attestation_summary={"reproduced": 0, "partial": 0, "failed": 0},
                                     row_hash="rh", synthetic=True)
        s.add_all([lab, cal, art, silver, ver, gold])
        s.commit()

        g = s.get(models.SciElectroGold, gold.id)
        assert g.silver.composition == "Fe70Bi15Ni10Sn3Zn2"
        assert g.silver.artifact.content_hash == "h"
        assert g.silver.artifact.lab.name == "Synthetic Lab"
        assert g.silver.calibration.instrument_kind == "potentiostat"
        assert g.version.semver == "1.0.0"


# ---------------------------------------------------------------------------
# 3. Dataset version immutability
# ---------------------------------------------------------------------------

def test_dataset_versions_immutable_trigger_in_migration():
    # sci_dataset_versions is append-only: new version = new row, never UPDATE.
    assert re.search(
        r"CREATE TRIGGER trg_sci_dataset_versions_immutable\s+BEFORE UPDATE OR DELETE ON sci_dataset_versions",
        CORE_SQL,
    ), "immutability trigger missing"
    assert "append-only" in CORE_SQL
    assert "RAISE EXCEPTION" in CORE_SQL


# ---------------------------------------------------------------------------
# 4. Correction supersession creates a new version
# ---------------------------------------------------------------------------

def test_correction_supersession_links_new_version():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        v1 = models.SciDatasetVersion(id=uuid.uuid4(), lane="electrocatalysis",
                                      dataset_name="twistor-electro-screening",
                                      semver="1.0.0", row_count=10, content_root_hash="r1",
                                      release_notes="seed")
        v2 = models.SciDatasetVersion(id=uuid.uuid4(), lane="electrocatalysis",
                                      dataset_name="twistor-electro-screening",
                                      semver="1.0.1", row_count=10, content_root_hash="r2",
                                      release_notes="FE erratum on one row",
                                      supersedes_id=v1.id)
        corr = models.SciCorrection(
            id=uuid.uuid4(), lane="electrocatalysis",
            corrects_silver_table="sci_electro_measurements",
            corrects_silver_id=uuid.uuid4(),
            reason="transcription error in FE",
            corrected_values={"faradaic_efficiency": 94.2},
            new_dataset_version_id=v2.id,
        )
        s.add_all([v1, v2, corr])
        s.commit()
        c = s.get(models.SciCorrection, corr.id)
        assert c.new_dataset_version_id == v2.id
        assert v2.supersedes_id == v1.id  # history preserved, never mutated
        assert "new_dataset_version_id" in CORE_SQL


# ---------------------------------------------------------------------------
# 5. Toll emission on query (mocked; never 500 on toll)
# ---------------------------------------------------------------------------

def test_query_emits_toll_and_returns_envelope(app_client, monkeypatch):
    client, _fake = app_client
    calls = []
    monkeypatch.setattr(
        electro_router, "_default_toll_impl",
        lambda: (lambda db, **kw: calls.append(kw) or {"ok": True}),
    )
    r = client.get("/data/v1/science/electrocatalysis/query", headers={"X-Request-ID": "req-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"]["semver"] == "1.0.0"
    assert body["provenance"]["lab_count"] == 3  # one per returned row's lab
    assert body["provenance"]["attestation_summary"]["reproduced"] == 1
    assert body["synthetic"] is True
    assert len(body["rows"]) == 3
    assert len(calls) == 1
    assert calls[0]["event_type"] == "data.science.query"
    assert calls[0]["idempotency_key"] == "sci:req-1"


def test_query_filters_composition_and_fe(app_client, monkeypatch):
    client, _fake = app_client
    monkeypatch.setattr(electro_router, "_default_toll_impl", lambda: (lambda db, **kw: {"ok": True}))
    r = client.get(
        "/data/v1/science/electrocatalysis/query",
        params={"composition": "Cu85Al15", "min_faradaic_efficiency": 70},
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["composition"] == "Cu85Al15"


def test_toll_valueerror_never_500s(app_client, monkeypatch):
    # Dre wires TOLL_RATES in the integration pass; unknown event types raise
    # ValueError today. The request must still succeed.
    client, _fake = app_client

    def boom(db, **kw):
        raise ValueError("Unknown toll event type: data.science.query")

    monkeypatch.setattr(electro_router, "_default_toll_impl", lambda: boom)
    r = client.get("/data/v1/science/electrocatalysis/query")
    assert r.status_code == 200
    assert r.json()["rows"]


def test_ingest_accepts_signed_quarantines_unsigned(app_client, monkeypatch):
    client, fake = app_client
    toll_calls = []
    monkeypatch.setattr(
        electro_router, "_default_toll_impl",
        lambda: (lambda db, **kw: toll_calls.append(kw) or {"ok": True}),
    )
    lab_id = fake.data["sci_labs"][0]["id"]
    payload = {
        "storage_path_prefix": "synthetic/test",
        "schema_version": 1,
        "records": [
            {
                "lab_id": lab_id,
                "content_hash": "signed-hash",
                "collector_signature": "ed25519:sig",
                "collected_at": "2026-10-18T12:00:00+00:00",
                "conditions": CONDITIONS,
                "composition": "Fe70Bi15Ni10Sn3Zn2",
            },
            {
                "lab_id": lab_id,  # unsigned → quarantined, never promoted
                "content_hash": "unsigned-hash",
                "collected_at": "2026-10-18T12:00:00+00:00",
                "conditions": CONDITIONS,
                "composition": "Cu85Al15",
            },
        ],
    }
    r = client.post(
        "/data/v1/science/electrocatalysis/ingest", json=payload, headers=_TEST_HEADERS
    )
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 1
    assert body["quarantined"] == 1
    assert body["dead_letters"] == 0
    assert toll_calls[0]["event_type"] == "data.science.ingest"
    assert toll_calls[0]["idempotency_key"].startswith("sci:")


def test_ingest_requires_api_key(app_client):
    client, fake = app_client
    lab_id = fake.data["sci_labs"][0]["id"]
    r = client.post(
        "/data/v1/science/electrocatalysis/ingest",
        json={
            "storage_path_prefix": "x",
            "schema_version": 1,
            "records": [
                {
                    "lab_id": lab_id,
                    "content_hash": "h",
                    "collector_signature": "sig",
                    "collected_at": "2026-10-18T12:00:00+00:00",
                    "conditions": CONDITIONS,
                }
            ],
        },
    )
    assert r.status_code == 401


def test_ingest_rejects_unknown_lab(app_client):
    client, _fake = app_client
    payload = {
        "storage_path_prefix": "x",
        "schema_version": 1,
        "records": [
            {
                "lab_id": str(uuid.uuid4()),
                "content_hash": "h",
                "collector_signature": "ed25519:sig",
                "collected_at": "2026-10-18T12:00:00+00:00",
                "conditions": CONDITIONS,
            }
        ],
    }
    r = client.post(
        "/data/v1/science/electrocatalysis/ingest", json=payload, headers=_TEST_HEADERS
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 6. Synthetic V1 seed shape
# ---------------------------------------------------------------------------

def test_seed_thirty_rows_synthetic_stamped():
    labs = seed.seed_labs()
    cals = seed.seed_calibrations(labs)
    records = seed.build_bronze_records(labs, cals)
    assert len(records) == 30
    assert all(r["synthetic"] is True for r in records)
    for r in records:
        for key in ("temp_c", "pressure_bar", "electrolyte", "ph", "solvent", "cell_type"):
            assert key in r["conditions"], key
        fe = r["faradaic_efficiency"]
        assert fe is None or 0 <= fe <= 100
    comps = [r["composition"] for r in records]
    assert any("Fe" in c and "Bi" in c for c in comps)      # UNSW-style alloys
    assert any(c.startswith("Cu") and "Al" in c for c in comps)  # Toronto-style Cu-Al
    assert any("O3" in c or "O5" in c for c in comps)       # perovskites


def test_seed_dataset_version_shape():
    v = seed.dataset_version(30)
    assert v["semver"] == "1.0.0"
    assert v["row_count"] == 30
    assert v["content_root_hash"]
    assert v["release_notes"]
