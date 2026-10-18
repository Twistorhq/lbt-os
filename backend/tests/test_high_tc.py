"""High-Tc lane tests (TW-593 Track 2).

Covers: schema constraints, provenance-chain walk (gold -> silver -> bronze
-> lab -> calibration), gold version immutability, correction supersession,
toll emission (mocked), request schemas, and the synthetic seed.

Data-logic tests run against SQLite via the SQLAlchemy mirrors in
app/data_layer/science/high_tc/models.py; router tests use a fake Supabase
client injected through dependency_overrides. No network, no real DB.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, Table, create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data_layer.science.high_tc import models
from app.data_layer.science.high_tc import router as hightc_router
from app.data_layer.science.high_tc import schemas as hightc_schemas
from app.data_layer.science.high_tc import seed as hightc_seed
from app.data_layer.science.high_tc.models import (
    HighTcGold,
    HighTcMeasurement,
    supersede_gold,
)
from app.database import get_db

# ---------------------------------------------------------------------------
# Shared-core stubs (owned by the electrocatalysis lane's core migration;
# recreated here in miniature so the FK chain is exercisable in tests).
# Defined on the lane's metadata so ForeignKey("sci_labs.id") etc. resolve.
# ---------------------------------------------------------------------------
_StubLab = Table(
    "sci_labs",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("name", models.Text, nullable=False),
    Column("kind", models.Text, nullable=False),
    Column("verification_status", models.Text, nullable=False),
)
_StubCal = Table(
    "sci_calibrations",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("lab_id", models.Uuid),
    Column("instrument_id", models.Text, nullable=False),
    Column("instrument_kind", models.Text, nullable=False),
    Column("calibrated_at", models.Text, nullable=False),
    Column("standard_ref", models.Text, nullable=False),
)
_StubBronze = Table(
    "sci_bronze_artifacts",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("lane", models.Text, nullable=False),
    Column("lab_id", models.Uuid),
    Column("storage_path", models.Text, nullable=False),
    Column("content_hash", models.Text, nullable=False),
    Column("collector_signature", models.Text),
    Column("collected_at", models.Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
)
_StubVersion = Table(
    "sci_dataset_versions",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("lane", models.Text, nullable=False),
    Column("dataset_name", models.Text, nullable=False),
    Column("semver", models.Text, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("content_root_hash", models.Text, nullable=False),
    Column("released_at", models.Text, nullable=True),  # stub: nullable in tests
    Column("release_notes", models.Text, nullable=False),
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")

    # SQLite parity with Postgres: enforce foreign keys (PRAGMA is
    # connection-scoped; Postgres enforces FKs unconditionally).
    @event.listens_for(engine, "connect")
    def _enforce_fk(dbapi_conn, _record):  # noqa: ARG001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _ids():
    return {k: str(uuid.uuid4()) for k in ("lab", "cal", "art", "ver", "ver2")}


def _seed_chain(s: Session, ids, tc_k=92.0):
    """Insert lab -> calibration -> bronze -> silver -> gold; return silver id."""
    # NOTE: raw stub inserts use dashless hex to match SQLAlchemy Uuid storage.
    hx = {k: uuid.UUID(v).hex for k, v in ids.items()}
    s.execute(
        text(
            "INSERT INTO sci_labs (id, name, kind, verification_status) "
            "VALUES (:id, 'Aalto Superconductivity Lab', 'academic', 'verified')"
        ),
        {"id": hx["lab"]},
    )
    s.execute(
        text(
            "INSERT INTO sci_calibrations (id, lab_id, instrument_id, "
            "instrument_kind, calibrated_at, standard_ref) VALUES "
            "(:id, :lab, 'PPMS-14T', 'magnetometer', :now, 'NIST SRM 2853')"
        ),
        {"id": hx["cal"], "lab": hx["lab"], "now": "2026-10-01T00:00:00+00:00"},
    )
    s.execute(
        text(
            "INSERT INTO sci_bronze_artifacts (id, lane, lab_id, storage_path, "
            "content_hash, collector_signature, collected_at, schema_version) "
            "VALUES (:id, 'high_tc', :lab, 's3://twistor-bronze/high_tc/r1.dat', "
            "'sha256:abc123', 'ed25519:sig-abc', :now, 1)"
        ),
        {"id": hx["art"], "lab": hx["lab"], "now": "2026-10-02T00:00:00+00:00"},
    )
    s.execute(
        text(
            "INSERT INTO sci_dataset_versions (id, lane, dataset_name, semver, "
            "row_count, content_root_hash, release_notes) VALUES "
            "(:id, 'high_tc', 'twistor-hightc-screening', '1.0.0', 1, "
            "'merkle:root1', 'V1 synthetic seed')"
        ),
        {"id": hx["ver"]},
    )
    silver = HighTcMeasurement(
        artifact_id=uuid.UUID(ids["art"]),
        lab_id=uuid.UUID(ids["lab"]),
        calibration_id=uuid.UUID(ids["cal"]),
        conditions={"temp_c": 22.0, "field_t": 0.0, "sample_form": "pellet"},
        measured_at=datetime.now(timezone.utc),
        composition="YBa2Cu3O7-delta",
        structure="cuprate",
        synthesis_route="solid-state reaction",
        tc_k=tc_k,
        measurement_method="resistivity",
        pressure_gpa=0,
        synthetic=True,
    )
    s.add(silver)
    s.flush()
    gold = HighTcGold(
        dataset_version_id=uuid.UUID(ids["ver"]),
        silver_id=silver.id,
        composition="YBa2Cu3O7-delta",
        structure="cuprate",
        tc_k=tc_k,
        pressure_gpa=0,
        measurement_method="resistivity",
        synthetic=True,
        provenance_hash="sha256:prov1",
        attestation_summary={"reproduced": 2, "labs": ["aalto", "uflorida"]},
    )
    s.add(gold)
    s.commit()
    return silver.id


@pytest.fixture()
def bronze_id(session):
    """A real bronze artifact row so silver FK constraints resolve."""
    bid, lid = uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO sci_labs (id, name, kind, verification_status) "
            "VALUES (:id, 'stub', 'academic', 'verified')"
        ),
        {"id": lid.hex},
    )
    session.execute(
        text(
            "INSERT INTO sci_bronze_artifacts (id, lane, lab_id, storage_path, "
            "content_hash, collected_at, schema_version) VALUES "
            "(:id, 'high_tc', :lab, 'p', 'h', :now, 1)"
        ),
        {"id": bid.hex, "lab": lid.hex, "now": "2026-01-01T00:00:00+00:00"},
    )
    session.commit()
    return bid


def _bad_measurement(**kw):
    base = dict(
        artifact_id=None,  # tests must supply a real bronze id (NOT NULL + FK)
        conditions={"temp_c": 22.0},
        measured_at=datetime.now(timezone.utc),
        composition="YBa2Cu3O7-delta",
        structure="cuprate",
        tc_k=92.0,
        measurement_method="resistivity",
        pressure_gpa=0,
        synthetic=True,
    )
    base.update(kw)
    return HighTcMeasurement(**base)


# ---------------------------------------------------------------------------
# 1. Schema constraints
# ---------------------------------------------------------------------------
class TestConstraints:
    def test_happy_path(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=bronze_id))
        session.commit()

    @pytest.mark.parametrize("tc", [0, -5, 400, 1000])
    def test_tc_k_range_rejected(self, session, bronze_id, tc):
        session.add(_bad_measurement(artifact_id=bronze_id, tc_k=tc))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_negative_pressure_rejected(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=bronze_id, pressure_gpa=-1))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_zero_pressure_ok(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=bronze_id, pressure_gpa=0))
        session.commit()

    def test_bad_structure_rejected(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=bronze_id, structure="unobtainium"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_bad_method_rejected(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=bronze_id, measurement_method="vibes"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_empty_composition_rejected(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=bronze_id, composition=""))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_null_artifact_rejected(self, session, bronze_id):
        session.add(_bad_measurement(artifact_id=None))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_dangling_artifact_rejected(self, session):
        # FK to sci_bronze_artifacts enforced: no orphan silver rows.
        session.add(_bad_measurement(artifact_id=uuid.uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_gold_unique_version_silver(self, session):
        ids = _ids()
        _seed_chain(session, ids)
        dup = HighTcGold(
            dataset_version_id=uuid.UUID(ids["ver"]),
            silver_id=session.query(HighTcMeasurement).first().id,
            composition="YBa2Cu3O7-delta",
            structure="cuprate",
            tc_k=92.0,
            pressure_gpa=0,
            measurement_method="resistivity",
            synthetic=True,
            provenance_hash="sha256:prov2",
            attestation_summary={},
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.commit()


# ---------------------------------------------------------------------------
# 2. Provenance-chain walk: gold -> silver -> bronze -> lab -> calibration
# ---------------------------------------------------------------------------
class TestProvenanceChain:
    def test_full_chain_resolves(self, session):
        ids = _ids()
        silver_id = _seed_chain(session, ids)
        chain = session.execute(
            text(
                """
                SELECT g.provenance_hash, m.composition, m.tc_k,
                       b.content_hash, b.collector_signature,
                       l.name, l.verification_status,
                       c.instrument_id, c.standard_ref
                FROM sci_hightc_gold g
                JOIN sci_hightc_measurements m ON m.id = g.silver_id
                JOIN sci_bronze_artifacts b ON b.id = m.artifact_id
                JOIN sci_labs l ON l.id = b.lab_id
                JOIN sci_calibrations c ON c.id = m.calibration_id
                WHERE g.silver_id = :sid
                """
            ),
            {"sid": silver_id.hex},
        ).one()
        assert chain[0] == "sha256:prov1"
        assert chain[1] == "YBa2Cu3O7-delta"
        assert float(chain[2]) == 92.0
        assert chain[3] == "sha256:abc123"  # bronze content hash
        assert chain[4] == "ed25519:sig-abc"  # lab signature
        assert chain[5] == "Aalto Superconductivity Lab"
        assert chain[6] == "verified"
        assert chain[7] == "PPMS-14T"
        assert chain[8] == "NIST SRM 2853"  # calibration standard

    def test_orphan_gold_rejected(self, session):
        ids = _ids()
        _seed_chain(session, ids)
        orphan = HighTcGold(
            dataset_version_id=uuid.UUID(ids["ver"]),
            silver_id=uuid.uuid4(),  # no such silver row
            composition="X",
            structure="cuprate",
            tc_k=10,
            pressure_gpa=0,
            measurement_method="resistivity",
            synthetic=True,
            provenance_hash="sha256:orphan",
            attestation_summary={},
        )
        session.add(orphan)
        with pytest.raises(IntegrityError):
            session.commit()


# ---------------------------------------------------------------------------
# 3. Gold version immutability
# ---------------------------------------------------------------------------
class TestGoldImmutability:
    def test_update_blocked(self, session):
        ids = _ids()
        _seed_chain(session, ids)
        gold = session.query(HighTcGold).first()
        gold.tc_k = 95.0
        with pytest.raises(RuntimeError, match="immutable"):
            session.commit()
        session.rollback()

    def test_delete_blocked(self, session):
        ids = _ids()
        _seed_chain(session, ids)
        gold = session.query(HighTcGold).first()
        session.delete(gold)
        with pytest.raises(RuntimeError, match="immutable"):
            session.commit()
        session.rollback()

    def test_silver_stays_mutable(self, session):
        ids = _ids()
        _seed_chain(session, ids)
        m = session.query(HighTcMeasurement).first()
        m.synthesis_route = "updated route"
        session.commit()  # silver corrections flow through, gold does not


# ---------------------------------------------------------------------------
# 4. Correction supersession: new version, history intact
# ---------------------------------------------------------------------------
class TestCorrectionSupersession:
    def test_supersede_copies_forward_with_correction(self, session):
        ids = _ids()
        silver_id = _seed_chain(session, ids)
        session.execute(
            text(
                "INSERT INTO sci_dataset_versions (id, lane, dataset_name, semver,"
                " row_count, content_root_hash, release_notes) VALUES "
                "(:id, 'high_tc', 'twistor-hightc-screening', '1.0.1', 1,"
                " 'merkle:root2', 'correction: YBCO Tc 92.0 -> 91.5')"
            ),
            {"id": uuid.UUID(ids["ver2"]).hex},
        )
        session.commit()

        n = supersede_gold(
            session,
            gold_table=HighTcGold.__table__,
            old_version_id=uuid.UUID(ids["ver"]),
            new_version_id=uuid.UUID(ids["ver2"]),
            overrides={silver_id: {"tc_k": 91.5}},
        )
        session.commit()
        assert n == 1

        old_rows = (
            session.query(HighTcGold)
            .filter_by(dataset_version_id=uuid.UUID(ids["ver"]))
            .all()
        )
        new_rows = (
            session.query(HighTcGold)
            .filter_by(dataset_version_id=uuid.UUID(ids["ver2"]))
            .all()
        )
        assert len(old_rows) == 1 and float(old_rows[0].tc_k) == 92.0  # history intact
        assert len(new_rows) == 1 and float(new_rows[0].tc_k) == 91.5  # correction lands
        assert new_rows[0].silver_id == old_rows[0].silver_id
        assert new_rows[0].provenance_hash == old_rows[0].provenance_hash

    def test_supersede_empty_version_returns_zero(self, session):
        ids = _ids()
        _seed_chain(session, ids)
        n = supersede_gold(
            session,
            gold_table=HighTcGold.__table__,
            old_version_id=uuid.uuid4(),
            new_version_id=uuid.UUID(ids["ver2"]),
            overrides={},
        )
        assert n == 0


# ---------------------------------------------------------------------------
# 5. Router + toll emission (fake Supabase client, toll service mocked)
# ---------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self._rows = store.setdefault(name, [])
        self._filters = []
        self._order = None
        self._limit = None
        self._single = False
        self._fail_on = None

    # -- chainable query builder (only the verbs the router uses) --
    def select(self, *cols):
        return self

    def insert(self, rows):
        if isinstance(rows, dict):
            rows = [rows]
        for r in rows:
            if r.get("composition") == "BOOM":
                raise RuntimeError("simulated insert failure")
            row = dict(r)
            row.setdefault("id", str(uuid.uuid4()))
            self._rows.append(row)
        self._inserted = rows
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col, val):
        self._filters.append(("lte", col, val))
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

    def maybe_single(self):
        self._single = True
        return self

    def execute(self):
        if hasattr(self, "_inserted"):
            return _FakeResult([dict(r) for r in self._inserted])
        rows = list(self._rows)
        for kind, col, val in self._filters:
            if kind == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif kind == "gte":
                rows = [r for r in rows if r.get(col) is not None and r.get(col) >= val]
            elif kind == "lte":
                rows = [r for r in rows if r.get(col) is not None and r.get(col) <= val]
            elif kind == "in":
                rows = [r for r in rows if r.get(col) in val]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._single:
            return _FakeResult(rows[0] if rows else None)
        return _FakeResult(rows)


class _FakeSupabase:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _FakeTable(self._store, name)


def _make_client(monkeypatch, store, toll_calls, toll_exc=None):
    """Build a TestClient with fake DB + toll service replaced."""
    fake_db = _FakeSupabase(store)

    def fake_record_toll(db, org_id, event_type, quantity=1.0,
                         idempotency_key=None, **kw):
        toll_calls.append(
            {
                "org_id": org_id,
                "event_type": event_type,
                "quantity": quantity,
                "idempotency_key": idempotency_key,
            }
        )
        if toll_exc is not None:
            raise toll_exc
        return {"ok": True}

    monkeypatch.setattr(hightc_router, "record_toll_event", fake_record_toll)

    app = FastAPI()
    app.include_router(hightc_router.router)
    app.dependency_overrides[get_db] = lambda: fake_db
    return TestClient(app)


@pytest.fixture()
def lane_store():
    version = {
        "id": str(uuid.uuid4()),
        "lane": "high_tc",
        "dataset_name": "twistor-hightc-screening",
        "semver": "1.0.0",
        "row_count": 2,
        "content_root_hash": "merkle:root1",
        "benchmark_pin": "htsc2025_v1",
        "doi": None,
        "supersedes_id": None,
        "released_at": "2026-10-18T00:00:00+00:00",
        "release_notes": "V1 synthetic seed",
    }
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    gold = [
        {
            "id": str(uuid.uuid4()),
            "dataset_version_id": version["id"],
            "silver_id": s1,
            "composition": "YBa2Cu3O7-delta",
            "structure": "cuprate",
            "tc_k": 92.0,
            "pressure_gpa": 0,
            "measurement_method": "resistivity",
            "synthetic": True,
            "provenance_hash": "sha256:prov1",
            "attestation_summary": {"reproduced": 2},
        },
        {
            "id": str(uuid.uuid4()),
            "dataset_version_id": version["id"],
            "silver_id": s2,
            "composition": "LaH10",
            "structure": "hydride",
            "tc_k": 250.0,
            "pressure_gpa": 170.0,
            "measurement_method": "resistivity",
            "synthetic": True,
            "provenance_hash": "sha256:prov2",
            "attestation_summary": {"reproduced": 0},
        },
    ]
    meas = [
        {"id": s1, "lab_id": "lab-a"},
        {"id": s2, "lab_id": "lab-b"},
    ]
    bench = {
        "id": str(uuid.uuid4()),
        "lane": "high_tc",
        "name": "htsc2025_v1",
        "metric": "tc_mae_k",
        "status": "open",
    }
    return {
        "sci_dataset_versions": [version],
        "sci_hightc_gold": gold,
        "sci_hightc_measurements": meas,
        "sci_benchmarks": [bench],
        "sci_benchmark_runs": [],
        "sci_corrections": [],
    }


class TestRouterTolling:
    def test_query_emits_toll_and_envelope(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        r = client.get("/data/v1/science/high_tc/query",
                       headers={"X-Request-Id": "req-123"})
        assert r.status_code == 200
        body = r.json()
        assert body["as_of"] == "1.0.0"
        assert body["row_count"] == 2
        assert body["synthetic"] is True
        assert body["provenance"]["lab_count"] == 2
        assert body["provenance"]["attested_rows"] == 1
        assert body["provenance"]["unattested_rows"] == 1
        assert len(toll_calls) == 1
        assert toll_calls[0]["event_type"] == "data.science.query"
        assert toll_calls[0]["idempotency_key"] == "sci:req-123"

    def test_query_toll_failure_does_not_fail_read(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(
            monkeypatch, lane_store, toll_calls, toll_exc=ValueError("Unknown toll event type")
        )
        r = client.get("/data/v1/science/high_tc/query")
        assert r.status_code == 200  # logged and continued
        assert r.json()["row_count"] == 2
        assert toll_calls[0]["event_type"] == "data.science.query"

    def test_query_filters(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        r = client.get(
            "/data/v1/science/high_tc/query",
            params={"structure": "hydride", "min_pressure_gpa": 100},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["row_count"] == 1
        assert body["rows"][0]["composition"] == "LaH10"

        r = client.get("/data/v1/science/high_tc/query",
                       params={"min_tc_k": 200})
        assert r.json()["row_count"] == 1

    def test_bulk_query_emits_bulk_toll(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        r = client.get("/data/v1/science/high_tc/query", params={"bulk": "true"})
        assert r.status_code == 200
        assert r.json()["bulk"] is True
        events = [c["event_type"] for c in toll_calls]
        assert events == ["data.science.query", "data.science.bulk"]
        assert toll_calls[1]["quantity"] >= 1.0  # per-1k-rows quantum

    def test_ingest_emits_ingest_toll_and_dead_letters(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        payload = {
            "records": [
                {
                    "artifact_id": str(uuid.uuid4()),
                    "conditions": {"temp_c": 22.0},
                    "composition": "MgB2",
                    "structure": "mgb2",
                    "tc_k": 39.0,
                    "measurement_method": "susceptibility",
                    "synthetic": True,
                },
                {
                    "artifact_id": str(uuid.uuid4()),
                    "conditions": {},
                    "composition": "BOOM",  # fake client raises -> dead letter
                    "structure": "cuprate",
                    "tc_k": 90.0,
                    "measurement_method": "resistivity",
                    "synthetic": True,
                },
            ]
        }
        r = client.post("/data/v1/science/high_tc/ingest", json=payload)
        assert r.status_code == 201
        body = r.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 1
        assert len(body["dead_letters"]) == 1
        assert toll_calls[0]["event_type"] == "data.science.ingest"
        assert toll_calls[0]["quantity"] == 2.0

    def test_ingest_rejects_unprovenanced_real_record(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        payload = {
            "records": [
                {
                    "artifact_id": str(uuid.uuid4()),
                    "conditions": {},
                    "composition": "YBa2Cu3O7-delta",
                    "structure": "cuprate",
                    "tc_k": 92.0,
                    "measurement_method": "resistivity",
                    "synthetic": False,  # no lab_id/calibration_id -> 422
                }
            ]
        }
        r = client.post("/data/v1/science/high_tc/ingest", json=payload)
        assert r.status_code == 422
        assert toll_calls == []  # never got far enough to toll

    def test_benchmark_submit_emits_benchmark_toll(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        r = client.post(
            "/data/v1/science/high_tc/benchmarks/runs",
            json={
                "benchmark_name": "htsc2025_v1",
                "submitter": "twistor-baseline-gnn",
                "score": 12.4,
                "manifest_id": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 201
        assert toll_calls[0]["event_type"] == "data.science.benchmark"

    def test_benchmark_submit_unknown_benchmark_404(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        r = client.post(
            "/data/v1/science/high_tc/benchmarks/runs",
            json={
                "benchmark_name": "nope",
                "submitter": "x",
                "score": 1.0,
                "manifest_id": str(uuid.uuid4()),
            },
        )
        assert r.status_code == 404
        assert toll_calls == []

    def test_datasets_and_benchmarks_list(self, monkeypatch, lane_store):
        toll_calls = []
        client = _make_client(monkeypatch, lane_store, toll_calls)
        r = client.get("/data/v1/science/high_tc/datasets")
        assert r.status_code == 200
        assert r.json()["versions"][0]["content_root_hash"] == "merkle:root1"

        r = client.get("/data/v1/science/high_tc/datasets/1.0.0")
        assert r.status_code == 200
        assert r.json()["version"]["semver"] == "1.0.0"

        r = client.get("/data/v1/science/high_tc/datasets/9.9.9")
        assert r.status_code == 404

        r = client.get("/data/v1/science/high_tc/benchmarks")
        assert r.status_code == 200
        assert r.json()["benchmarks"][0]["name"] == "htsc2025_v1"

        r = client.get("/data/v1/science/high_tc/corrections")
        assert r.status_code == 200
        assert r.json()["corrections"] == []


# ---------------------------------------------------------------------------
# 6. Request schemas
# ---------------------------------------------------------------------------
class TestSchemas:
    def _rec(self, **kw):
        base = dict(
            artifact_id=uuid.uuid4(),
            conditions={"temp_c": 22.0},
            composition="YBa2Cu3O7-delta",
            structure="cuprate",
            tc_k=92.0,
            measurement_method="resistivity",
            synthetic=True,
        )
        base.update(kw)
        return hightc_schemas.MeasurementIngest(**base)

    def test_synthetic_without_lab_ok(self):
        assert self._rec().synthetic is True

    def test_bad_structure_rejected(self):
        with pytest.raises(ValueError, match="structure"):
            self._rec(structure="unobtainium")

    def test_bad_method_rejected(self):
        with pytest.raises(ValueError, match="measurement_method"):
            self._rec(measurement_method="vibes")

    def test_nonsynthetic_requires_provenance(self):
        with pytest.raises(ValueError, match="lab_id"):
            self._rec(synthetic=False)

    def test_nonsynthetic_with_provenance_ok(self):
        rec = self._rec(synthetic=False, lab_id=uuid.uuid4(),
                        calibration_id=uuid.uuid4())
        assert rec.lab_id is not None

    def test_benchmark_run_requires_manifest(self):
        with pytest.raises(Exception):
            hightc_schemas.BenchmarkRunSubmit(
                benchmark_name="htsc2025_v1", submitter="x", score=1.0
            )


# ---------------------------------------------------------------------------
# 7. Synthetic seed
# ---------------------------------------------------------------------------
class TestSeed:
    def test_thirty_records(self):
        recs = hightc_seed.synthetic_records()
        assert len(recs) == 30

    def test_all_synthetic_and_conditioned(self):
        recs = hightc_seed.synthetic_records()
        assert all(r["synthetic"] is True for r in recs)
        assert all(r["conditions"] for r in recs)
        assert all(r["artifact_id"] for r in recs)

    def test_structure_coverage(self):
        structs = {r["structure"] for r in hightc_seed.synthetic_records()}
        assert {
            "cuprate", "iron_pnictide", "iron_chalcogenide", "hydride",
            "nickelate", "perovskite", "mgb2", "conventional",
        } <= structs

    def test_hydrides_at_pressure_ambient_at_zero(self):
        recs = hightc_seed.synthetic_records()
        hydrides = [r for r in recs if r["structure"] == "hydride"]
        assert len(hydrides) >= 5
        assert all(r["pressure_gpa"] >= 100 for r in hydrides)
        cuprates = [r for r in recs if r["structure"] == "cuprate"]
        assert all(r["pressure_gpa"] == 0 for r in cuprates)

    def test_tc_ranges_sane(self):
        recs = hightc_seed.synthetic_records()
        assert all(0 < r["tc_k"] < 400 for r in recs)
        assert max(r["tc_k"] for r in recs) >= 200  # hydride regime present

    def test_deterministic_ids(self):
        a = hightc_seed.synthetic_records()
        b = hightc_seed.synthetic_records()
        assert [r["id"] for r in a] == [r["id"] for r in b]
