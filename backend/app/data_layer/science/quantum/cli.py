# Prepared by Twistor Holdings LLC
"""TW-838 quantum lane CLI: batch capture, normalize, and inspect.

    python -m app.data_layer.science.quantum.cli poll \
        --root /data/bronze --basis payg
    python -m app.data_layer.science.quantum.cli normalize \
        --root /data/bronze --silver-db /data/silver/quantum.db
    python -m app.data_layer.science.quantum.cli stats --root /data/bronze

Batch/async only, no resident daemon: a scheduler invokes `poll` daily
(and again on calibration-change checks); `normalize` promotes new
bronze captures into silver. Exit codes: 0 ok, 2 fail-closed (missing
credentials or gated adapter) with operator instructions on stderr.

Credentials: the IBM token is read from the IBM_QUANTUM_TOKEN
environment variable at runtime only (see transport.py). The --fixture
file path exists for offline code-testing of the pipeline itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Table, create_engine
from sqlalchemy.orm import Session

from . import custody_models, models
from .adapters import AdapterUnavailable, get_adapter
from .bronze_store import FileBronzeStore
from .custody import CustodyAuthAlert, CustodyCollector
from .job_transports import (
    FixtureJobTransport,
    get_job_transport,
)
from .normalize import normalize_properties
from .poller import QuantumPoller
from .published import SOURCE_PUBLISHED, PublishedSnapshotTransport
from .transport import BackendPayload, FixtureTransport, MissingTokenError

_UUID_NAMESPACE = uuid.UUID("83838383-3838-4383-8838-383838383838")


def _ensure_core_stubs() -> None:
    """Miniature shared-core tables so the quantum mirrors can create
    their schema standalone (SQLite working DB). Production silver lives
    in Supabase, where migration_sci_core_v1.sql owns the real tables."""
    md = models.Base.metadata
    if "sci_labs" not in md.tables:
        Table(
            "sci_labs",
            md,
            Column("id", models.Uuid, primary_key=True),
            Column("name", models.Text, nullable=False),
        )
    if "sci_bronze_artifacts" not in md.tables:
        Table(
            "sci_bronze_artifacts",
            md,
            Column("id", models.Uuid, primary_key=True),
            Column("lane", models.Text, nullable=False),
        )
    if "sci_calibrations" not in md.tables:
        Table(
            "sci_calibrations",
            md,
            Column("id", models.Uuid, primary_key=True),
            Column("lab_id", models.Uuid, nullable=False),
        )


def _artifact_uuid(content_hash: str) -> uuid.UUID:
    """Deterministic silver->bronze link for the file store. In the
    Supabase sink this is the sci_bronze_artifacts row id instead."""
    return uuid.uuid5(_UUID_NAMESPACE, content_hash)


# ---------------------------------------------------------------------------
# poll
# ---------------------------------------------------------------------------
def _fixture_transport(path: str) -> FixtureTransport:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    payloads = {
        name: BackendPayload(
            name=name,
            configuration=doc.get("configuration", {}),
            properties=doc.get("properties", {}),
            status=doc.get("status", {}),
        )
        for name, doc in raw.items()
    }
    return FixtureTransport(payloads)


def cmd_poll(args: argparse.Namespace) -> int:
    store = FileBronzeStore(args.root)
    if args.fixture_file:
        transport = _fixture_transport(args.fixture_file)
    else:
        try:
            transport = get_adapter("ibm").transport()
        except (MissingTokenError, AdapterUnavailable) as exc:
            print(f"poll: fail-closed: {exc}", file=sys.stderr)
            return 2
    devices = args.devices.split(",") if args.devices else None
    # Fixture data is synthetic by definition. Never let a caller-supplied
    # basis stamp it as production: fixture runs are always Open-basis
    # code tests, regardless of --basis or the environment default.
    account_basis = "open" if args.fixture_file else args.basis
    result = QuantumPoller(transport, store, account_basis=account_basis).run(devices)
    manifest_path = os.path.join(args.root, "quantum", "_runs.jsonl")
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result.manifest(), sort_keys=True) + "\n")
    print(json.dumps(result.manifest(), sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------
def _normalized_hashes(root: str) -> set[str]:
    path = os.path.join(root, "quantum", "_normalized.jsonl")
    done: set[str] = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done.add(json.loads(line)["content_hash"])
    return done


def _mark_normalized(root: str, content_hash: str, rows: int) -> None:
    path = os.path.join(root, "quantum", "_normalized.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"content_hash": content_hash, "rows": rows}) + "\n")


def cmd_normalize(args: argparse.Namespace) -> int:
    store = FileBronzeStore(args.root)
    index = list(store.iter_index())
    configs: dict[str, dict[str, Any]] = {}
    for row in index:
        meta = row.get("source_metadata", {})
        if meta.get("kind") == "configuration":
            doc = json.loads(store.read_capture(row))
            configs.setdefault(meta["device"], []).append((row["collected_at"], doc))
    for device in configs:
        configs[device].sort(key=lambda pair: pair[0])

    done = _normalized_hashes(args.root)
    _ensure_core_stubs()
    engine = create_engine(f"sqlite:///{args.silver_db}")
    models.Base.metadata.create_all(engine)
    total_rows = 0
    captures_done = 0
    with Session(engine) as session:
        for row in index:
            meta = row.get("source_metadata", {})
            if meta.get("kind") != "properties":
                continue
            if row["content_hash"] in done:
                continue
            device = meta["device"]
            config_doc: dict[str, Any] = {}
            if configs.get(device):
                config_doc = configs[device][-1][1]
            payload = BackendPayload(
                name=device,
                configuration=config_doc,
                properties=json.loads(store.read_capture(row)),
                status={},
            )
            artifact_id = _artifact_uuid(row["content_hash"])
            observations = normalize_properties(
                payload,
                artifact_ref=str(artifact_id),
                vendor=meta.get("vendor", "ibm"),
            )
            for obs in observations:
                measured_at = obs["measured_at"]
                session.add(
                    models.SciQuantumObservation(
                        id=uuid.uuid4(),
                        artifact_id=artifact_id,
                        vendor=obs["vendor"],
                        device=obs["device"],
                        device_generation=obs["device_generation"],
                        qubit_or_edge=obs["qubit_or_edge"],
                        metric=obs["metric"],
                        value=obs["value"],
                        unit=obs["unit"],
                        measured_at=(
                            datetime.fromisoformat(measured_at) if measured_at else None
                        ),
                        source_metadata={
                            "bronze_hash": row["content_hash"],
                            "bronze_collected_at": row["collected_at"],
                            "measurement_time_missing": obs.get(
                                "measurement_time_missing", measured_at is None
                            ),
                        },
                    )
                )
            session.commit()
            _mark_normalized(args.root, row["content_hash"], len(observations))
            total_rows += len(observations)
            captures_done += 1
    print(
        json.dumps(
            {"normalized_captures": captures_done, "silver_rows": total_rows},
            sort_keys=True,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# custody (TW-1329): job lifecycle, reconciliation, snapshots, drill
# ---------------------------------------------------------------------------
def _custody_engine(args: argparse.Namespace):
    url = args.custody_url or os.environ.get("QUANTUM_CUSTODY_DATABASE_URL")
    if not url:
        if not args.custody_db:
            raise SystemExit(
                "custody: set --custody-db (local working store) or "
                "QUANTUM_CUSTODY_DATABASE_URL (Supabase Postgres, production)"
            )
        url = f"sqlite:///{args.custody_db}"
    _ensure_core_stubs()
    engine = create_engine(url)
    models.Base.metadata.create_all(engine)
    return engine


def _alert_sink(root: str):
    """The named alert channel (TW-1328 Rosa nit, TW-1329 build).

    Channel: the Twistor family board (agentbus), watched by the
    founder. Owner: Marcus. Technical responder: Dre Coleman. The sink
    appends the alert to <root>/quantum/_alerts.jsonl (durable) and
    posts to the board best-effort; the alert ROW in the store plus
    the nonzero exit are the record if the board post itself fails.
    """
    path = os.path.join(root, "quantum", "_alerts.jsonl")
    ab_py = os.path.expanduser("~/workspace/twistor-lab/agent-ops/agentbus/ab.py")

    def sink(alert: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert, sort_keys=True) + "\n")
        try:
            import subprocess

            subprocess.run(
                [
                    "python3",
                    ab_py,
                    "post",
                    "--agent",
                    "dre",
                    "--text",
                    "QUANTUM CUSTODY ALERT "
                    f"({alert['kind']}) provider={alert['provider']}: "
                    "provider auth or custody gap detected. Alert owner "
                    "Marcus; technical responder Dre Coleman. "
                    f"Alert id {alert['alert_id']}.",
                ],
                timeout=15,
                capture_output=True,
            )
        except Exception:
            pass  # the row, the file, and the exit code are the record

    return sink


def _collector(args: argparse.Namespace, session: Session) -> CustodyCollector:
    store = FileBronzeStore(args.root)
    sink = _alert_sink(args.root)
    if args.provider == "published":
        transport = PublishedSnapshotTransport()
        return CustodyCollector(
            session,
            store,
            provider="ibm",
            telemetry_transport=transport,
            telemetry_source=SOURCE_PUBLISHED,
            account_basis="published",
            alert_sink=sink,
        )
    if args.provider == "fixture":
        return CustodyCollector(
            session,
            store,
            provider="fixture",
            job_transport=FixtureJobTransport(),
            account_basis="open",
            alert_sink=sink,
        )
    return CustodyCollector(
        session,
        store,
        provider=args.provider,
        job_transport=get_job_transport(args.provider),
        account_basis="payg",
        alert_sink=sink,
    )


def cmd_custody_snapshot(args: argparse.Namespace) -> int:
    engine = _custody_engine(args)
    with Session(engine) as session:
        collector = _collector(args, session)
        out = collector.snapshot_backends()
    print(json.dumps(out, sort_keys=True))
    return 0


def cmd_custody_submit(args: argparse.Namespace) -> int:
    engine = _custody_engine(args)
    with open(args.qasm_file, encoding="utf-8") as f:
        qasm = f.read()
    with Session(engine) as session:
        collector = _collector(args, session)
        try:
            job = collector.submit_new(
                args.backend,
                qasm,
                args.shots,
                provenance={"submitted_via": "cli"},
            )
        except AdapterUnavailable as exc:
            print(f"custody-submit: fail-closed: {exc}", file=sys.stderr)
            return 2
        except CustodyAuthAlert as exc:
            print(f"custody-submit: ALERT PAGED: {exc}", file=sys.stderr)
            return 3
        print(
            json.dumps(
                {
                    "job_id": str(job.id),
                    "provider_job_id": job.provider_job_id,
                    "status": job.status,
                },
                sort_keys=True,
            )
        )
    return 0


def cmd_custody_reconcile(args: argparse.Namespace) -> int:
    engine = _custody_engine(args)
    with Session(engine) as session:
        collector = _collector(args, session)
        try:
            out = collector.reconcile()
        except CustodyAuthAlert as exc:
            print(f"custody-reconcile: ALERT PAGED: {exc}", file=sys.stderr)
            return 3
    print(json.dumps(out, sort_keys=True))
    return 0


def cmd_custody_diff(args: argparse.Namespace) -> int:
    engine = _custody_engine(args)
    with Session(engine) as session:
        collector = _collector(args, session)
        try:
            out = collector.daily_diff()
        except AdapterUnavailable as exc:
            print(f"custody-diff: fail-closed: {exc}", file=sys.stderr)
            return 2
        except CustodyAuthAlert as exc:
            print(f"custody-diff: ALERT PAGED: {exc}", file=sys.stderr)
            return 3
    print(json.dumps(out, sort_keys=True))
    return 0


def cmd_custody_stats(args: argparse.Namespace) -> int:
    # Stats is a pure store read: it must work with no provider
    # transport constructible (no credentials, no packages), because
    # it is the read-back Rex and the drill use to prove custody.
    engine = _custody_engine(args)
    provider = "ibm" if args.provider == "published" else args.provider
    with Session(engine) as session:
        collector = CustodyCollector(
            session, FileBronzeStore(args.root), provider=provider
        )
        out = collector.counts()
    print(json.dumps(out, sort_keys=True))
    return 0


def cmd_custody_drill(args: argparse.Namespace) -> int:
    """Deactivation drill (TW-1329 deliverable 5), proven end to end.

    Lifecycle against the fixture provider, then the provider revokes
    us mid-life (the IBM shape). Pass conditions: everything custodied
    before the revocation is still in our store, queryable, counts
    unchanged; the in-flight job stays NAMED with its circuit intact;
    the auth failure produced an alert row and a page on the named
    channel. Prints the proof as JSON.
    """
    import tempfile

    tmp = tempfile.mkdtemp(prefix="quantum-drill-")
    root = os.path.join(tmp, "bronze")
    db = os.path.join(tmp, "custody.db")
    engine = create_engine(f"sqlite:///{db}")
    _ensure_core_stubs()
    models.Base.metadata.create_all(engine)
    store = FileBronzeStore(root)
    pages: list[dict[str, Any]] = []
    jobs_transport = FixtureJobTransport(polls_to_complete=2)
    telemetry = FixtureTransport(
        {
            "fixture-qpu-1": BackendPayload(
                name="fixture-qpu-1",
                configuration={"n_qubits": 2},
                properties={
                    "qubits": [[{"name": "T1", "value": 1.0e-4, "unit": "s"}]],
                    "gates": [],
                    "last_update_date": "2026-11-24T00:00:00Z",
                },
            )
        }
    )
    qasm = "OPENQASM 3.0;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
    with Session(engine) as session:
        collector = CustodyCollector(
            session,
            store,
            provider="fixture",
            job_transport=jobs_transport,
            telemetry_transport=telemetry,
            account_basis="open",
            alert_sink=pages.append,
        )
        a = collector.submit_new("fixture-qpu-1", qasm, 100)
        b = collector.submit_new("fixture-qpu-1", qasm, 200)
        collector.reconcile()  # both still running (2 polls to complete)
        collector.reconcile()  # both complete -> custodied
        before = collector.counts()
        c = collector.submit_new("fixture-qpu-1", qasm, 50)  # in flight
        jobs_transport.revoke()  # the provider deactivates us, mid-life
        alert_seen = False
        try:
            collector.reconcile()
        except CustodyAuthAlert:
            alert_seen = True
        after = collector.counts()
        inflight = session.get(custody_models.SciQuantumJob, c.id)
        proof = {
            "custodied_before": before["custodied"],
            "custodied_after_revocation": after["custodied"],
            "data_loss": before["custodied"] - after["custodied"],
            "inflight_job_named": inflight.provider_job_id is not None,
            "inflight_circuit_intact": inflight.circuit_qasm == qasm,
            "inflight_status": inflight.status,
            "auth_alert_paged": alert_seen and len(pages) == 1,
            "alert_channel": pages[0]["channel"] if pages else None,
            "alert_owner": pages[0]["owner"] if pages else None,
            "alert_rows": after["alerts"],
            "jobs_a_b": [str(a.id), str(b.id)],
        }
    proof["drill"] = (
        "PASS"
        if proof["data_loss"] == 0
        and proof["custodied_after_revocation"] == 2
        and proof["inflight_job_named"]
        and proof["inflight_circuit_intact"]
        and proof["auth_alert_paged"]
        and proof["alert_rows"] == 1
        else "FAIL"
    )
    print(json.dumps(proof, sort_keys=True, indent=2))
    return 0 if proof["drill"] == "PASS" else 1


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def cmd_stats(args: argparse.Namespace) -> int:
    store = FileBronzeStore(args.root)
    index = list(store.iter_index())
    devices = sorted(
        {r.get("source_metadata", {}).get("device") for r in index} - {None}
    )
    out: dict[str, Any] = {
        "bronze_captures": len(index),
        "devices": devices,
        "device_count": len(devices),
        "total_compressed_bytes": sum(
            os.path.getsize(os.path.join(args.root, r["storage_path"]))
            for r in index
            if os.path.exists(os.path.join(args.root, r["storage_path"]))
        ),
    }
    if args.silver_db and os.path.exists(args.silver_db):
        _ensure_core_stubs()
        engine = create_engine(f"sqlite:///{args.silver_db}")
        with Session(engine) as session:
            out["silver_observations"] = session.query(
                models.SciQuantumObservation
            ).count()
    print(json.dumps(out, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantum-lane")
    sub = parser.add_subparsers(dest="command", required=True)

    p_poll = sub.add_parser("poll", help="capture one fleet snapshot into bronze")
    p_poll.add_argument("--root", required=True, help="bronze storage root")
    p_poll.add_argument(
        "--basis",
        choices=("payg", "open"),
        default=os.environ.get("QUANTUM_ACCOUNT_BASIS", "open"),
        help="account basis: payg = production (CSA), open = code-test scale only",
    )
    p_poll.add_argument("--devices", help="comma-separated device filter")
    p_poll.add_argument(
        "--fixture-file",
        help="offline fixture JSON for pipeline code-testing (no network)",
    )
    p_poll.set_defaults(func=cmd_poll)

    p_norm = sub.add_parser("normalize", help="promote new bronze captures to silver")
    p_norm.add_argument("--root", required=True)
    p_norm.add_argument("--silver-db", required=True, help="SQLite silver working DB")
    p_norm.set_defaults(func=cmd_normalize)

    p_stats = sub.add_parser("stats", help="bronze/silver accumulation counts")
    p_stats.add_argument("--root", required=True)
    p_stats.add_argument("--silver-db")
    p_stats.set_defaults(func=cmd_stats)

    def _custody_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", required=True, help="bronze storage root")
        p.add_argument("--custody-db", help="SQLite working store path")
        p.add_argument(
            "--custody-url",
            help="SQLAlchemy URL (Supabase Postgres in production)",
        )
        p.add_argument(
            "--provider",
            choices=("published", "braket", "ibm", "fixture"),
            default="published",
        )

    p_csnap = sub.add_parser(
        "custody-snapshot", help="daily backend telemetry snapshot (TW-1329)"
    )
    _custody_args(p_csnap)
    p_csnap.set_defaults(func=cmd_custody_snapshot)

    p_csub = sub.add_parser("custody-submit", help="intent-first job submission")
    _custody_args(p_csub)
    p_csub.add_argument("--backend", required=True)
    p_csub.add_argument("--qasm-file", required=True)
    p_csub.add_argument("--shots", type=int, default=1024)
    p_csub.set_defaults(func=cmd_custody_submit)

    p_crec = sub.add_parser("custody-reconcile", help="15-minute reconciliation sweep")
    _custody_args(p_crec)
    p_crec.set_defaults(func=cmd_custody_reconcile)

    p_cdiff = sub.add_parser("custody-diff", help="daily provider-side diff")
    _custody_args(p_cdiff)
    p_cdiff.set_defaults(func=cmd_custody_diff)

    p_cstats = sub.add_parser("custody-stats", help="custody store counts")
    _custody_args(p_cstats)
    p_cstats.set_defaults(func=cmd_custody_stats)

    p_drill = sub.add_parser(
        "custody-drill", help="deactivation drill: revoke mid-life, prove zero loss"
    )
    p_drill.set_defaults(func=cmd_custody_drill)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
