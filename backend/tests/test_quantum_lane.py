"""Quantum lane tests (TW-838).

Covers: IBM properties normalizer (silver observation schema), the file
bronze store (hashing, change-triggered dedupe, daily snapshot), poller
account-basis guard (Angela ruling 2026-10-30: Open Plan = code-test scale
only, production accumulation on the IBM Cloud PAYG / CSA basis), per-item
fault isolation (TW-208), the toll SKUs as re-scoped by the DERIVED-ONLY
ruling (raw bulk export on HOLD), the Braket adapter gate, the IBM Cloud
transport auth flow (token never logged), and the CLI end to end on a
fixture. No network, no real DB: SQLite via the SQLAlchemy mirrors,
fixture transports, fake HTTP sessions.
"""

import gzip
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import Column, Table, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data_layer.science.quantum import models
from app.data_layer.science.quantum.adapters import AdapterUnavailable, BraketAdapter
from app.data_layer.science.quantum.bronze_store import FileBronzeStore
from app.data_layer.science.quantum.normalize import (
    KNOWN_METRICS,
    normalize_properties,
)
from app.data_layer.science.quantum.poller import OPEN_TEST_MAX_BACKENDS, QuantumPoller
from app.data_layer.science.quantum.tolls import (
    HARD_CAP_CENTS,
    SKU_CURRENT_AGGREGATES,
    SKU_DERIVED_HISTORY,
    SKU_DRIFT_ANALYTICS,
    SKU_RAW_BULK_EXPORT,
    TollHoldError,
    meter_quantum_read,
    quote,
)
from app.data_layer.science.quantum.transport import (
    BackendPayload,
    FixtureTransport,
    IBMCloudTransport,
    MissingTokenError,
)

# ---------------------------------------------------------------------------
# Shared-core stubs (owned by the core migration; miniature FK targets so
# the quantum mirrors resolve in SQLite, same pattern as test_high_tc.py).
# ---------------------------------------------------------------------------
_StubLab = Table(
    "sci_labs",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("name", models.Text, nullable=False),
)
_StubBronze = Table(
    "sci_bronze_artifacts",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("lane", models.Text, nullable=False),
)
_StubCal = Table(
    "sci_calibrations",
    models.Base.metadata,
    Column("id", models.Uuid, primary_key=True),
    Column("lab_id", models.Uuid, nullable=False),
)


# ---------------------------------------------------------------------------
# Fixture: a 3-qubit IBM backend in the Qiskit properties().to_dict() shape.
# ---------------------------------------------------------------------------
def _nduv(name, value, unit, date="2026-10-30T04:00:00Z"):
    return {"date": date, "name": name, "unit": unit, "value": value}


def fixture_configuration(name="ibm_fixture"):
    return {
        "backend_name": name,
        "backend_version": "1.2.0",
        "n_qubits": 3,
        "basis_gates": ["rz", "sx", "x", "cz"],
        "coupling_map": [[0, 1], [1, 0], [1, 2], [2, 1]],
        "processor_type": {"family": "Eagle", "revision": "r3"},
        "simulator": False,
    }


def fixture_properties(name="ibm_fixture"):
    qubits = []
    for q in range(3):
        qubits.append(
            [
                _nduv("T1", 250.0 + q * 10, "us"),
                _nduv("T2", 180.0 + q * 5, "us"),
                _nduv("frequency", 5.0 + q * 0.1, "GHz"),
                _nduv("readout_error", 0.01 + q * 0.002, ""),
                _nduv("readout_length", 1200.0, "ns"),
                _nduv("prob_meas0_prep1", 0.008, ""),
                _nduv("prob_meas1_prep0", 0.012, ""),
            ]
        )
    gates = [
        {
            "gate": "sx",
            "name": "sx0",
            "qubits": [0],
            "parameters": [
                _nduv("gate_error", 0.0004, ""),
                _nduv("gate_length", 60.0, "ns"),
            ],
        },
        {
            "gate": "cz",
            "name": "cz0_1",
            "qubits": [0, 1],
            "parameters": [
                _nduv("gate_error", 0.008, ""),
                _nduv("gate_length", 320.0, "ns"),
            ],
        },
        {
            "gate": "cz",
            "name": "cz1_2",
            "qubits": [1, 2],
            "parameters": [
                _nduv("gate_error", 0.011, ""),
                _nduv("gate_length", 340.0, "ns"),
            ],
        },
    ]
    return {
        "backend_name": name,
        "backend_version": "1.2.0",
        "last_update_date": "2026-10-30T04:00:00Z",
        "qubits": qubits,
        "gates": gates,
        "general": [_nduv("last_update_date", "2026-10-30T04:00:00Z", "")],
    }


def fixture_payload(name="ibm_fixture"):
    return BackendPayload(
        name=name,
        configuration=fixture_configuration(name),
        properties=fixture_properties(name),
        status={"operational": True, "status_msg": "active"},
    )


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------
def test_normalize_qubit_metrics():
    rows = normalize_properties(fixture_payload(), artifact_ref="art-1")
    t1_q0 = [r for r in rows if r["metric"] == "t1" and r["qubit_or_edge"] == "q0"]
    assert len(t1_q0) == 1
    row = t1_q0[0]
    assert row["value"] == 250.0
    assert row["unit"] == "us"
    assert row["vendor"] == "ibm"
    assert row["device"] == "ibm_fixture"
    assert row["device_generation"] == "Eagle"
    assert row["source_record_ref"] == "art-1"
    assert row["measured_at"].startswith("2026-10-30T04:00:00")


def test_normalize_gate_metrics_split_1q_2q():
    rows = normalize_properties(fixture_payload(), artifact_ref="art-1")
    err_1q = [r for r in rows if r["metric"] == "gate_error_1q"]
    err_2q = [r for r in rows if r["metric"] == "gate_error_2q"]
    assert len(err_1q) == 1 and err_1q[0]["qubit_or_edge"] == "q0"
    assert len(err_2q) == 2
    assert {r["qubit_or_edge"] for r in err_2q} == {"q0-q1", "q1-q2"}
    assert all(r["metric"] in KNOWN_METRICS for r in rows)


def test_normalize_row_count_and_fault_isolation():
    payload = fixture_payload()
    # Corrupt one entry: a value-less NDUV must be skipped, never crush.
    payload.properties["qubits"][1].append({"name": "T1", "unit": "us"})
    rows = normalize_properties(payload, artifact_ref="art-1")
    # 3 qubits x 7 metrics + 3 gates x 2 params = 27
    assert len(rows) == 27


# ---------------------------------------------------------------------------
# Bronze store: hashing, dedupe, daily snapshot
# ---------------------------------------------------------------------------
def _capture(store, payload_bytes, day):
    return store.capture(
        vendor="ibm",
        device="ibm_fixture",
        kind="properties",
        payload=payload_bytes,
        retrieved_at=day,
        source="ibm-quantum-platform",
        endpoint="/api/v1/backends/ibm_fixture",
        account_basis="payg",
    )


def test_bronze_store_writes_gzipped_partitioned(tmp_path):
    store = FileBronzeStore(str(tmp_path))
    day = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    res = _capture(store, b'{"a": 1}', day)
    assert res.stored is True
    assert res.path.endswith(".json.gz")
    assert "vendor=ibm" in res.path and "device=ibm_fixture" in res.path
    assert "date=2026-10-30" in res.path
    with gzip.open(res.path, "rb") as f:
        stored_bytes = f.read()
    assert json.loads(stored_bytes) == {"a": 1}
    import hashlib

    # Hash is over the canonical form (sorted keys, compact separators):
    # identical device state hashes identically however the vendor
    # happened to serialize it.
    assert stored_bytes == b'{"a":1}'
    assert res.content_hash == hashlib.sha256(b'{"a":1}').hexdigest()
    res2 = store.capture(
        vendor="ibm",
        device="ibm_fixture",
        kind="properties",
        payload=b'{ "a" : 1 }',
        retrieved_at=day,
        source="ibm-quantum-platform",
        endpoint="/api/v1/backends/ibm_fixture",
        account_basis="payg",
    )
    assert res2.content_hash == res.content_hash
    assert res2.stored is False


def test_bronze_store_change_trigger_and_daily_snapshot(tmp_path):
    store = FileBronzeStore(str(tmp_path))
    d1_am = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
    d1_pm = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)
    assert _capture(store, b'{"v": 1}', d1_am).stored is True
    # Unchanged, same day: skipped (no duplicate bytes accumulate).
    assert _capture(store, b'{"v": 1}', d1_pm).stored is False
    # Unchanged, next day: stored (the daily snapshot is the asset).
    assert _capture(store, b'{"v": 1}', d2).stored is True
    # Changed payload: stored immediately (change-triggered recapture).
    assert _capture(store, b'{"v": 2}', d2).stored is True


# ---------------------------------------------------------------------------
# Poller: basis guard + fault isolation
# ---------------------------------------------------------------------------
def _fixture_transport(n=4):
    return FixtureTransport(
        {f"ibm_b{i}": fixture_payload(f"ibm_b{i}") for i in range(n)}
    )


def test_poller_open_basis_is_code_test_scale(tmp_path):
    store = FileBronzeStore(str(tmp_path))
    poller = QuantumPoller(_fixture_transport(4), store, account_basis="open")
    result = poller.run()
    assert result.purpose == "code-test"
    assert result.backends_polled == OPEN_TEST_MAX_BACKENDS
    assert OPEN_TEST_MAX_BACKENDS == 2


def test_poller_payg_basis_polls_fleet(tmp_path):
    store = FileBronzeStore(str(tmp_path))
    poller = QuantumPoller(_fixture_transport(4), store, account_basis="payg")
    result = poller.run()
    assert result.purpose == "production"
    assert len(result.captured) == 8  # 4 backends x (configuration + properties)
    assert result.failures == []


def test_poller_fault_isolation(tmp_path):
    class FlakyTransport(FixtureTransport):
        def fetch_backend(self, name):
            if name == "ibm_b1":
                raise RuntimeError("boom")
            return super().fetch_backend(name)

    store = FileBronzeStore(str(tmp_path))
    transport = FlakyTransport(
        {f"ibm_b{i}": fixture_payload(f"ibm_b{i}") for i in range(3)}
    )
    result = QuantumPoller(transport, store, account_basis="payg").run()
    assert len(result.failures) == 1
    assert result.failures[0]["device"] == "ibm_b1"
    assert len(result.captured) == 4  # the other two backends still landed


# ---------------------------------------------------------------------------
# Silver models (SQLite mirrors)
# ---------------------------------------------------------------------------
@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_silver_observation_insert_and_constraints(session):
    artifact_id = uuid.uuid4()
    obs = models.SciQuantumObservation(
        id=uuid.uuid4(),
        artifact_id=artifact_id,
        vendor="ibm",
        device="ibm_fixture",
        device_generation="Eagle",
        qubit_or_edge="q0",
        metric="t1",
        value=250.0,
        unit="us",
        measured_at=datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc),
    )
    session.add(obs)
    session.commit()
    assert session.query(models.SciQuantumObservation).count() == 1

    bad = models.SciQuantumObservation(
        id=uuid.uuid4(),
        artifact_id=artifact_id,
        vendor="ibm",
        device="ibm_fixture",
        qubit_or_edge="q0",
        metric="",
        value=1.0,
        measured_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()


def test_spec_snapshot_provenance_flag(session):
    snap = models.SciQuantumSpecSnapshot(
        id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        vendor="ionq",
        device="forte-1",
        metric="gate_error_2q",
        value=0.004,
        provenance="marketing",
        source_url="https://example.invalid/specs",
        retrieved_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )
    session.add(snap)
    session.commit()
    row = session.query(models.SciQuantumSpecSnapshot).one()
    assert row.provenance == "marketing"


# ---------------------------------------------------------------------------
# Tolls (DERIVED-ONLY re-scope, Angela ruling 2026-10-30)
# ---------------------------------------------------------------------------
def test_toll_quotes_inside_cap():
    assert quote(SKU_CURRENT_AGGREGATES) == 1
    assert quote(SKU_DERIVED_HISTORY) == 3
    assert quote(SKU_DRIFT_ANALYTICS) == 5
    for sku in (SKU_CURRENT_AGGREGATES, SKU_DERIVED_HISTORY, SKU_DRIFT_ANALYTICS):
        assert quote(sku) < HARD_CAP_CENTS == 10


def test_toll_raw_bulk_export_on_hold():
    with pytest.raises(TollHoldError):
        quote(SKU_RAW_BULK_EXPORT)
    with pytest.raises(TollHoldError):
        meter_quantum_read(None, "org-1", SKU_RAW_BULK_EXPORT, "req-1")


def test_toll_meter_emits_explicit_amount(monkeypatch):
    calls = []

    def fake_record(db, **kwargs):
        calls.append(kwargs)
        return {"id": "toll-1"}

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", fake_record)
    out = meter_quantum_read(object(), "org-1", SKU_DERIVED_HISTORY, "req-9")
    assert out == {"id": "toll-1"}
    assert calls[0]["amount_cents"] == 3
    assert calls[0]["event_type"] == SKU_DERIVED_HISTORY
    assert calls[0]["source_type"] == "sci_quantum"


def test_toll_meter_failure_swallowed(monkeypatch):
    def boom(db, **kwargs):
        raise RuntimeError("ledger down")

    import app.services.toll_service as toll_service

    monkeypatch.setattr(toll_service, "record_toll_event", boom)
    assert meter_quantum_read(object(), "org-1", SKU_DRIFT_ANALYTICS, "req-2") is None


# ---------------------------------------------------------------------------
# Braket adapter gate
# ---------------------------------------------------------------------------
def test_braket_adapter_unavailable_until_aws_account():
    adapter = BraketAdapter()
    assert adapter.available is False
    with pytest.raises(AdapterUnavailable):
        adapter.transport()


# ---------------------------------------------------------------------------
# IBM Cloud transport: auth flow, token hygiene
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        return _FakeResp({"access_token": "ACCESS-TOKEN-XYZ"})

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        if url.endswith("/backends"):
            return _FakeResp([{"name": "ibm_fixture"}])
        return _FakeResp(
            {
                "name": "ibm_fixture",
                "configuration": fixture_configuration(),
                "properties": fixture_properties(),
                "status": {"operational": True},
            }
        )


def test_ibm_transport_requires_token(monkeypatch):
    monkeypatch.delenv("IBM_QUANTUM_TOKEN", raising=False)
    with pytest.raises(MissingTokenError):
        IBMCloudTransport.from_env(session=_FakeSession())


def test_ibm_transport_auth_flow_and_token_hygiene():
    session = _FakeSession()
    transport = IBMCloudTransport(token="SECRET-APIKEY-123", session=session)
    assert transport.list_backends() == ["ibm_fixture"]
    payload = transport.fetch_backend("ibm_fixture")
    assert payload.configuration["n_qubits"] == 3
    assert payload.properties["backend_name"] == "ibm_fixture"
    # IAM exchange happened, and the API key itself never rides a GET header.
    assert session.calls[0][0] == "POST"
    get_headers = session.calls[1][2]
    assert get_headers["Authorization"] == "Bearer ACCESS-TOKEN-XYZ"
    assert "SECRET-APIKEY-123" not in json.dumps(get_headers)
    assert "SECRET-APIKEY-123" not in repr(transport)


# ---------------------------------------------------------------------------
# CLI end to end on a fixture (offline code-test path)
# ---------------------------------------------------------------------------
def test_cli_poll_normalize_stats(tmp_path):
    from app.data_layer.science.quantum import cli

    fixture_file = tmp_path / "fixture.json"
    fixture_file.write_text(
        json.dumps(
            {
                "ibm_fixture": {
                    "configuration": fixture_configuration(),
                    "properties": fixture_properties(),
                    "status": {"operational": True},
                }
            }
        )
    )
    root = str(tmp_path / "bronze")
    silver = str(tmp_path / "silver.db")
    rc = cli.main(
        [
            "poll",
            "--root",
            root,
            "--basis",
            "payg",
            "--fixture-file",
            str(fixture_file),
        ]
    )
    assert rc == 0
    rc = cli.main(["normalize", "--root", root, "--silver-db", silver])
    assert rc == 0
    engine = create_engine(f"sqlite:///{silver}")
    with Session(engine) as s:
        assert s.query(models.SciQuantumObservation).count() == 27
    # Re-normalize is idempotent: no duplicate silver rows.
    rc = cli.main(["normalize", "--root", root, "--silver-db", silver])
    assert rc == 0
    with Session(engine) as s:
        assert s.query(models.SciQuantumObservation).count() == 27


def test_cli_poll_without_token_or_fixture_fails_closed(tmp_path, monkeypatch):
    from app.data_layer.science.quantum import cli

    monkeypatch.delenv("IBM_QUANTUM_TOKEN", raising=False)
    rc = cli.main(["poll", "--root", str(tmp_path / "bronze"), "--basis", "payg"])
    assert rc == 2
