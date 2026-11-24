"""Quantum custody tests (TW-1329).

Covers the TW-1328 custody mechanics as built:
  1. intent-first: the job row exists in OUR store before submission,
     and the provider job ID is recorded the moment submission returns;
  2. pull on completion: result payload verbatim in bronze, fresh
     backend snapshot captured, job flips to custodied in the same run;
  3. reconciliation sweep: idempotent, custodies what completed;
  4. daily diff: a provider-side job we never submitted is recovered
     and the gap is alerted;
  5. daily telemetry snapshots write the snapshot ledger;
  6. auth failure pages same-day (alert row + sink page on the named
     channel with the named owner) and NEVER pauses silently;
  7. the deactivation drill: provider revokes us mid-life, zero data
     loss, in-flight job stays named with its circuit intact;
  8. Braket is the first real adapter and fails closed (AdapterUnavailable
     naming the founder unlock) without AWS prerequisites; with fake
     boto clients it submits, maps status, and reads staged S3 results;
  9. IBM job transport is a retired stub (founder decision, TW-1329);
 10. the credential-free published source yields real IBM-published
     snapshots, honestly provenanced (skipped when the package is not
     installed in the test environment).

No network, no real DB: SQLite via the SQLAlchemy mirrors, fixture
transports, fake boto clients.
"""

import hashlib
import json
import os  # noqa: F401  (kept for env-shaped tests)
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data_layer.science.quantum import custody_models as cm
from app.data_layer.science.quantum import models
from app.data_layer.science.quantum.adapters import AdapterUnavailable
from app.data_layer.science.quantum.bronze_store import FileBronzeStore
from app.data_layer.science.quantum.cli import _ensure_core_stubs
from app.data_layer.science.quantum.custody import (
    ALERT_CHANNEL,
    ALERT_OWNER,
    CustodyAuthAlert,
    CustodyCollector,
)
from app.data_layer.science.quantum.job_transports import (
    BraketJobTransport,
    FixtureJobTransport,
    IBMJobTransport,
    ProviderAuthError,
)
from app.data_layer.science.quantum.transport import BackendPayload, FixtureTransport

QASM = "OPENQASM 3.0;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"

PROPERTIES = {
    "qubits": [[{"name": "T1", "value": 1.2e-4, "unit": "s", "date": "2026-11-24"}]],
    "gates": [],
    "last_update_date": "2026-11-24T00:00:00Z",
}


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/custody.db")
    _ensure_core_stubs()
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def bronze(tmp_path):
    return FileBronzeStore(str(tmp_path / "bronze"))


def _collector(session, bronze, transport, pages=None, **kw):
    return CustodyCollector(
        session,
        bronze,
        provider="fixture",
        job_transport=transport,
        account_basis="open",
        alert_sink=(pages.append if pages is not None else None),
        **kw,
    )


# ---------------------------------------------------------------------------
# Mechanic 1: intent first
# ---------------------------------------------------------------------------
def test_intent_row_exists_before_submission(session, bronze):
    transport = FixtureJobTransport()
    collector = _collector(session, bronze, transport)
    seen = {}

    def hook(backend, qasm, shots):
        rows = session.query(cm.SciQuantumJob).all()
        seen["rows"] = [
            (r.status, r.circuit_sha256) for r in rows if r.provider_job_id is None
        ]

    transport.on_submit = hook
    job = collector.submit_new("fixture-qpu-1", QASM, 512)
    assert seen["rows"], "no intent row was visible at submission time"
    assert seen["rows"][0][0] == "intent"
    # and the provider ID was recorded immediately on return
    assert job.provider_job_id == "fixture-job-0001"
    assert job.status == "submitted"
    assert job.submitted_at is not None


def test_intent_alone_is_recoverable(session, bronze):
    collector = _collector(session, bronze, FixtureJobTransport())
    job = collector.record_intent("fixture-qpu-1", QASM, 100)
    assert job.status == "intent"
    assert job.circuit_qasm == QASM


# ---------------------------------------------------------------------------
# Mechanic 2: pull on completion
# ---------------------------------------------------------------------------
def test_pull_on_completion_custodies_result_and_snapshot(session, bronze):
    transport = FixtureJobTransport()
    telemetry = FixtureTransport(
        {"fixture-qpu-1": BackendPayload("fixture-qpu-1", {"n_qubits": 2}, PROPERTIES)}
    )
    collector = _collector(session, bronze, transport, telemetry_transport=telemetry)
    job = collector.submit_new("fixture-qpu-1", QASM, 100)
    collector.pull_job(job)
    assert job.status == "custodied"
    assert job.custodied_at is not None
    assert job.result_artifact_id is not None
    assert job.snapshot_artifact_id is not None
    # the result payload is in bronze verbatim, readable back
    kinds = {}
    for row in bronze.iter_index():
        kinds[row["source_metadata"]["kind"]] = json.loads(bronze.read_capture(row))
    assert kinds["job_result"]["counts"] == {"00": 50, "11": 50}
    assert kinds["properties"]["last_update_date"] == "2026-11-24T00:00:00Z"


# ---------------------------------------------------------------------------
# Mechanic 3: reconciliation sweep
# ---------------------------------------------------------------------------
def test_reconcile_is_idempotent(session, bronze):
    transport = FixtureJobTransport(polls_to_complete=2)
    collector = _collector(session, bronze, transport)
    collector.submit_new("fixture-qpu-1", QASM, 100)
    collector.submit_new("fixture-qpu-1", QASM, 100)
    first = collector.reconcile()
    assert first["custodied"] == 0  # still running after one poll
    second = collector.reconcile()
    assert second["custodied"] == 2
    third = collector.reconcile()
    assert third["checked"] == 0  # nothing pre-custodied remains
    assert collector.counts()["jobs_total"] == 2


# ---------------------------------------------------------------------------
# Mechanic 4: daily diff recovers provider-side gaps
# ---------------------------------------------------------------------------
def test_daily_diff_recovers_unsubmitted_provider_job(session, bronze):
    transport = FixtureJobTransport()
    pages = []
    collector = _collector(session, bronze, transport, pages=pages)
    collector.submit_new("fixture-qpu-1", QASM, 100)
    transport.submit("fixture-qpu-1", QASM, 777)  # provider-side only
    out = collector.daily_diff()
    assert out["missing"] == ["fixture-job-0002"]
    assert out["recovered"] == 1
    recovered = (
        session.query(cm.SciQuantumJob)
        .filter_by(provider_job_id="fixture-job-0002")
        .one()
    )
    assert recovered.status == "custodied"
    assert recovered.provenance["recovered_by"] == "daily_diff"
    assert recovered.shots == 0  # unknown, never invented
    assert pages and pages[0]["kind"] == "diff_gap"


# ---------------------------------------------------------------------------
# Mechanic 5: daily telemetry snapshots
# ---------------------------------------------------------------------------
def test_snapshot_backends_writes_ledger(session, bronze):
    telemetry = FixtureTransport(
        {"fixture-qpu-1": BackendPayload("fixture-qpu-1", {"n_qubits": 2}, PROPERTIES)}
    )
    collector = _collector(
        session, bronze, FixtureJobTransport(), telemetry_transport=telemetry
    )
    out = collector.snapshot_backends()
    assert out["backends_polled"] == 1
    rows = session.query(cm.SciQuantumBackendSnapshot).all()
    assert {r.kind for r in rows} == {"properties", "configuration"}
    assert all(r.provider == "fixture" for r in rows)


# ---------------------------------------------------------------------------
# Mechanic 6 + drill: auth failure pages; deactivation loses nothing
# ---------------------------------------------------------------------------
def test_auth_failure_on_submit_pages_and_keeps_intent(session, bronze):
    transport = FixtureJobTransport()
    transport.revoke()
    pages = []
    collector = _collector(session, bronze, transport, pages=pages)
    with pytest.raises(CustodyAuthAlert):
        collector.submit_new("fixture-qpu-1", QASM, 100)
    alerts = session.query(cm.SciQuantumAlert).all()
    assert len(alerts) == 1 and alerts[0].kind == "auth_failure"
    assert pages[0]["channel"] == ALERT_CHANNEL
    assert pages[0]["owner"] == ALERT_OWNER
    job = session.query(cm.SciQuantumJob).one()
    assert job.status == "intent"  # named, circuit intact, re-runnable
    assert job.circuit_qasm == QASM


def test_deactivation_drill_zero_data_loss(session, bronze):
    transport = FixtureJobTransport(polls_to_complete=2)
    telemetry = FixtureTransport(
        {"fixture-qpu-1": BackendPayload("fixture-qpu-1", {"n_qubits": 2}, PROPERTIES)}
    )
    pages = []
    collector = _collector(
        session, bronze, transport, pages=pages, telemetry_transport=telemetry
    )
    collector.submit_new("fixture-qpu-1", QASM, 100)
    collector.submit_new("fixture-qpu-1", QASM, 200)
    collector.reconcile()
    collector.reconcile()
    before = collector.counts()
    assert before["custodied"] == 2
    inflight = collector.submit_new("fixture-qpu-1", QASM, 50)
    transport.revoke()  # the provider deactivates us, mid-life
    with pytest.raises(CustodyAuthAlert):
        collector.reconcile()
    after = collector.counts()
    assert after["custodied"] == before["custodied"]  # zero data loss
    assert after["alerts"] == 1
    row = session.get(cm.SciQuantumJob, inflight.id)
    assert row.provider_job_id is not None  # still named
    assert row.circuit_qasm == QASM  # re-runnable on the fallback


# ---------------------------------------------------------------------------
# Braket: first real adapter, un-fired without the founder unlock
# ---------------------------------------------------------------------------
def test_braket_fails_closed_without_bucket(monkeypatch):
    monkeypatch.delenv("BRAKET_S3_BUCKET", raising=False)
    transport = BraketJobTransport()
    with pytest.raises(AdapterUnavailable) as exc:
        transport.submit("arn:aws:braket:us-east-1::device/qpu/ionq/Forte", QASM, 100)
    assert "AWS account" in str(exc.value)


class _FakeBraket:
    def __init__(self):
        self.tasks = {}

    def create_quantum_task(self, **kw):
        arn = "arn:aws:braket:us-east-1:123:quantum-task/fake-1"
        self.tasks[arn] = {"status": "COMPLETED", "kw": kw}
        return {"quantumTaskArn": arn, "status": "QUEUED"}

    def get_quantum_task(self, quantumTaskArn):
        task = self.tasks[quantumTaskArn]
        return {
            "status": task["status"],
            "outputS3Directory": {"bucket": "stage-bucket", "keyPrefix": "qc/fake-1"},
        }

    def search_quantum_tasks(self, **kw):
        return {"quantumTasks": [{"quantumTaskArn": a} for a in self.tasks]}


class _FakeS3:
    def get_object(self, Bucket, Key):
        import io

        assert Bucket == "stage-bucket"
        assert Key == "qc/fake-1/results.json"
        return {"Body": io.BytesIO(json.dumps({"counts": {"0": 10}}).encode())}


def test_braket_with_injected_clients(monkeypatch):
    monkeypatch.setenv("BRAKET_S3_BUCKET", "stage-bucket")
    transport = BraketJobTransport(braket_client=_FakeBraket(), s3_client=_FakeS3())
    arn = transport.submit("arn:device", QASM, 100)
    assert arn.endswith("fake-1")
    assert transport.status(arn) == "completed"
    assert transport.fetch_result(arn) == {"counts": {"0": 10}}
    assert transport.list_job_ids() == [arn]


def test_braket_auth_error_maps_to_provider_auth_error(monkeypatch):
    class FakeClientError(Exception):
        """Duck-typed botocore ClientError shape (response dict)."""

        def __init__(self):
            super().__init__("AccessDenied")
            self.response = {
                "Error": {"Code": "AccessDeniedException"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            }

    class DenyingBraket(_FakeBraket):
        def get_quantum_task(self, quantumTaskArn):
            raise FakeClientError()

    monkeypatch.setenv("BRAKET_S3_BUCKET", "stage-bucket")
    transport = BraketJobTransport(braket_client=DenyingBraket(), s3_client=_FakeS3())
    with pytest.raises(ProviderAuthError):
        transport.status("arn:aws:braket:us-east-1:123:quantum-task/fake-1")


# ---------------------------------------------------------------------------
# IBM: retired stub (founder decision, TW-1329)
# ---------------------------------------------------------------------------
def test_ibm_job_transport_is_retired_stub():
    transport = IBMJobTransport()
    for call in (
        lambda: transport.submit("ibm_brisbane", QASM, 1),
        lambda: transport.status("x"),
        lambda: transport.fetch_result("x"),
        lambda: transport.list_job_ids(),
    ):
        with pytest.raises(AdapterUnavailable) as exc:
            call()
        assert "retired" in str(exc.value)


# ---------------------------------------------------------------------------
# Published source: real IBM-published snapshots, honest provenance
# ---------------------------------------------------------------------------
def test_published_source_real_snapshots():
    pytest.importorskip("qiskit_ibm_runtime")
    from app.data_layer.science.quantum.published import (
        SOURCE_PUBLISHED,
        PublishedSnapshotTransport,
    )

    transport = PublishedSnapshotTransport()
    names = transport.list_backends()
    assert len(names) >= 5
    payload = transport.fetch_backend(names[0])
    assert payload.properties.get("last_update_date")  # the device's own date
    assert payload.properties.get("qubits")  # real calibration entries
    assert SOURCE_PUBLISHED == "qiskit-fake-provider"
    assert transport.package_version  # provenance carries the version


def test_published_poll_stamps_published_snapshot(session, bronze, tmp_path):
    pytest.importorskip("qiskit_ibm_runtime")
    from app.data_layer.science.quantum.poller import QuantumPoller
    from app.data_layer.science.quantum.published import (
        SOURCE_PUBLISHED,
        PublishedSnapshotTransport,
    )

    transport = PublishedSnapshotTransport(backends=("FakeManilaV2",))
    poller = QuantumPoller(
        transport,
        bronze,
        account_basis="published",
        vendor="ibm",
        source=SOURCE_PUBLISHED,
        endpoint_prefix="qiskit-fake-provider",
    )
    result = poller.run()
    assert result.purpose == "published-snapshot"
    assert result.captured  # something real landed
    meta = list(bronze.iter_index())[0]["source_metadata"]
    assert meta["source"] == "qiskit-fake-provider"
    assert meta["purpose"] == "published-snapshot"


# ---------------------------------------------------------------------------
# Model constraints
# ---------------------------------------------------------------------------
def test_job_status_constraint(session):
    bad = cm.SciQuantumJob(
        provider="fixture",
        backend="b",
        circuit_qasm=QASM,
        circuit_sha256="x",
        shots=1,
        status="bogus",
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_alert_kind_constraint(session):
    session.add(cm.SciQuantumAlert(provider="fixture", kind="nope"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------------------
# Mechanic 11: weekly export verification fails on a SHORT dump
# (TW-1329 Rosa fix round: the count check was one-sided — it failed
# only when the export held MORE jobs than the live store, so a dump
# missing rows that existed at export time passed silently.)
# ---------------------------------------------------------------------------
def _write_export(root: Path, stamp: str, job_rows: int) -> Path:
    export_dir = root / f"export-{stamp}"
    export_dir.mkdir(parents=True)
    lines = ["COPY public.sci_quantum_jobs (id) FROM stdin;"]
    lines += [f"job-{i}" for i in range(job_rows)]
    lines.append("\\.")
    dump = export_dir / "dump.sql"
    dump.write_text("\n".join(lines) + "\n")
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    (export_dir / "SHA256SUMS").write_text(f"{digest}  dump.sql\n")
    return export_dir


def _run_verify_export(root: Path, *, as_of: int, live: int, dump_jobs: int):
    root.mkdir(parents=True, exist_ok=True)
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "psql"
    stub.write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        '    *created_at*) echo "$FAKE_AS_OF_COUNT"; exit 0 ;;\n'
        "  esac\n"
        "done\n"
        'echo "$FAKE_LIVE_COUNT"\n'
    )
    stub.chmod(0o755)
    export_root = root / "exports"
    _write_export(export_root, "20261124T120000Z", dump_jobs)
    alerts = root / "_alerts.jsonl"
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "quantum_custody_verify_export.sh"
    )
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        QUANTUM_EXPORT_ROOT=str(export_root),
        QUANTUM_ALERTS_JSONL=str(alerts),
        QUANTUM_CUSTODY_DATABASE_URL="postgres://stub.invalid/custody",
        FAKE_AS_OF_COUNT=str(as_of),
        FAKE_LIVE_COUNT=str(live),
    )
    proc = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True
    )
    return proc, alerts


def test_verify_export_fails_on_short_dump(tmp_path):
    # 5 jobs existed when the export was taken; the dump holds only 3.
    # The old one-sided check passed this silently (3 <= live). It must
    # exit nonzero and raise the export_failure alert.
    proc, alerts = _run_verify_export(tmp_path / "short", as_of=5, live=5, dump_jobs=3)
    assert proc.returncode != 0
    assert "export_failure" in alerts.read_text()
    # Control: a complete dump (5 of 5 as of export) that merely trails
    # the CURRENT live count (8; the store grew after the nightly
    # export) is healthy and must pass.
    proc, _ = _run_verify_export(tmp_path / "full", as_of=5, live=8, dump_jobs=5)
    assert proc.returncode == 0


def test_no_hardcoded_credentials_in_job_transports():
    # Credentials come from the environment at runtime only. Guard the
    # module source against anyone pasting a key in later.
    import inspect

    from app.data_layer.science.quantum import job_transports

    src = inspect.getsource(job_transports)
    assert "AKIA" not in src  # AWS access-key-id prefix
    assert "BEGIN RSA" not in src
    assert "BEGIN PRIVATE KEY" not in src
