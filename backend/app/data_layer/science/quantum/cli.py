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

from . import models
from .adapters import AdapterUnavailable, get_adapter
from .bronze_store import FileBronzeStore
from .normalize import normalize_properties
from .poller import QuantumPoller
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
    result = QuantumPoller(transport, store, account_basis=args.basis).run(devices)
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
                        measured_at=datetime.fromisoformat(obs["measured_at"]),
                        source_metadata={"bronze_hash": row["content_hash"]},
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
