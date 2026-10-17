"""TW-569: silver promotion job for the trades benchmark layer (byproduct).

Bronze -> silver, idempotent and replayable. For byproduct niches the
product itself is the witness: emitters read the operational tables, so
attestation is skipped and data_events carry actor='product'.

Per-record fault isolation (TW-208): one bad org's snapshot never kills
the run. Rejects go to dead_letters with the redaction spec applied
(TW-564 Major 3) — never raw PII, never silently dropped.

Idempotency: bronze_artifacts has UNIQUE (source_id, content_hash). A
replayed run re-computes the same canonical JSON, hits the same hash, and
skips the insert — then still ensures the silver rows exist.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from . import emitters

log = logging.getLogger("twistor.data_layer.benchmarks")

NICHE = "trades_benchmarks"
BRONZE_BUCKET = "data-bronze-trades_benchmarks"
SCHEMA_VERSION = 1

_DATA_LAYER_DIR = Path(__file__).resolve().parent.parent


def _hmac_secret() -> bytes:
    # Vault path: datalayer/hmac/trades_benchmarks (versioned).
    # Env-configured here; the Vault mount is an ops step, documented.
    return os.environ.get("DATALAYER_HMAC_SECRET", "dev-only-insecure").encode()


@lru_cache(maxsize=1)
def _redaction_spec() -> dict:
    """Load the canonical per-niche redaction list (TW-564 Major 3).

    backend/app/data_layer/redaction/benchmarks_v1.yaml — Dre owns it.
    No list, no niche ships: _dead_letter refuses to write without it.
    """
    path = _DATA_LAYER_DIR / "redaction" / "benchmarks_v1.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def _batch_schema() -> dict:
    """Load the schema-registry contract for metric batches (TW-569 MAJOR 1)."""
    path = _DATA_LAYER_DIR / "schemas" / "benchmarks" / "v1.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _hmac_id(value: str) -> str:
    """Real HMAC-SHA256 of an identifier (TW-564 Major 2: never bare sha256).

    Keyed by the per-niche Vault secret; the digest is unlinkable without it.
    """
    return hmac.new(_hmac_secret(), value.encode(), hashlib.sha256).hexdigest()


def _redact(record: dict) -> dict:
    """Apply the YAML redaction spec. org_id -> HMAC (non-joinable);
    identifier/PII fields -> dropped. Unlisted fields are dropped loudly
    rather than leaked quietly."""
    spec = _redaction_spec()
    rules = {r["field"]: r["action"] for r in spec.get("rules", [])}
    out = {}
    for k, v in record.items():
        action = rules.get(k)
        if action == "drop":
            continue
        if action == "hmac":
            out[k] = "hmac:" + _hmac_id(str(v))
            continue
        if k in ("cohort_key", "period", "computed_at", "metrics"):
            out[k] = v
        else:
            log.warning("redact: unlisted field %s dropped", k)
    return out


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _content_hash(payload: bytes) -> str:
    # Content hash of the STORED payload (already aggregated, no PII).
    # Identifier HMACs live in the redaction spec; this is a dedupe hash.
    return hashlib.sha256(payload).hexdigest()


def _validate_snapshot(snap: dict) -> None:
    """Enforce the schema-registry contract (TW-569 MAJOR 1).

    Raises jsonschema.ValidationError on contract violation; the caller
    routes rejects to dead_letters — never silently dropped, never
    promoted unvalidated.
    """
    jsonschema.validate(instance=snap, schema=_batch_schema())


def _get_source_id(db) -> str | None:
    rows = (
        db.table("data_sources")
        .select("id")
        .eq("niche", NICHE)
        .eq("source_kind", "byproduct")
        .eq("status", "active")
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


def _consenting_org_ids(db) -> list[str]:
    rows = (
        db.table("organizations")
        .select("id")
        .eq("benchmark_consent", True)
        .execute()
        .data
        or []
    )
    return [r["id"] for r in rows]


def promote_run(db, run_idempotency_key: str | None = None) -> dict[str, Any]:
    """Run one bronze->silver promotion. Returns the run receipt."""
    source_id = _get_source_id(db)
    if not source_id:
        return {"status": "failed", "reason": "no_active_source"}

    # Idempotent run: a duplicate idempotency key returns the original receipt.
    if run_idempotency_key:
        existing = (
            db.table("data_ingest_runs")
            .select("id, status, records_in, records_accepted, records_rejected")
            .eq("source_id", source_id)
            .eq("idempotency_key", run_idempotency_key)
            .limit(1)
            .execute()
            .data
            or []
        )
        if existing:
            r = existing[0]
            return {
                "status": r["status"],
                "run_id": r["id"],
                "accepted": r["records_accepted"],
                "rejected": r["records_rejected"],
                "duplicate": True,
            }

    run_id = (
        db.table("data_ingest_runs")
        .insert(
            {
                "source_id": source_id,
                "idempotency_key": run_idempotency_key,
                "status": "running",
            }
        )
        .execute()
        .data[0]["id"]
    )

    accepted = rejected = 0
    for org_id in _consenting_org_ids(db):
        try:
            ok = _promote_org(db, source_id, run_id, org_id)
            accepted += 1 if ok else 0
            rejected += 0 if ok else 1
        except Exception as exc:  # noqa: BLE001 - per-record isolation
            log.error("promote: org %s failed, continuing: %s", org_id, exc)
            _dead_letter(
                db,
                run_id,
                {"org_id": org_id},
                f"promote_exception: {type(exc).__name__}",
            )
            rejected += 1

    status = "ok" if rejected == 0 else ("partial" if accepted else "failed")
    db.table("data_ingest_runs").update(
        {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "records_in": accepted + rejected,
            "records_accepted": accepted,
            "records_rejected": rejected,
        }
    ).eq("id", run_id).execute()
    return {
        "status": status,
        "run_id": run_id,
        "accepted": accepted,
        "rejected": rejected,
    }


def _promote_org(db, source_id: str, run_id: str, org_id: str) -> bool:
    snap = emitters.emit_org_snapshot(db, org_id)
    if not snap.get("metrics"):
        # No consent or no data: not a failure, just nothing to promote.
        # (Consent was already checked by the emitter; empty metrics =
        #  org has no activity in-window.)
        return True

    # Schema-registry enforcement (TW-569 MAJOR 1): the contract is law.
    # Violations go to dead_letters — never silently dropped, never promoted.
    try:
        _validate_snapshot(snap)
    except jsonschema.ValidationError as exc:
        log.warning(
            "promote: snapshot failed schema contract for %s: %s", org_id, exc.message
        )
        _dead_letter(db, run_id, snap, f"schema_violation: {exc.message[:200]}")
        return False

    payload = _canonical(snap)
    digest = _content_hash(payload)

    # Idempotency: same source + same bytes = same artifact. A replay that
    # finds an already-promoted artifact is a no-op (TW-569 MAJOR 4).
    existing = (
        db.table("bronze_artifacts")
        .select("id, promotion_status")
        .eq("source_id", source_id)
        .eq("content_hash", digest)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        if existing[0].get("promotion_status") == "promoted":
            return True
        artifact_id = existing[0]["id"]
    else:
        storage_path = f"{run_id}/{org_id}.json"
        try:
            db.storage.from_(BRONZE_BUCKET).upload(storage_path, payload)
        except Exception as exc:  # noqa: BLE001
            log.error("promote: bronze upload failed for %s: %s", org_id, exc)
            _dead_letter(db, run_id, snap, "bronze_upload_failed")
            return False
        artifact_id = (
            db.table("bronze_artifacts")
            .insert(
                {
                    "run_id": run_id,
                    "source_id": source_id,
                    "storage_path": f"{BRONZE_BUCKET}/{storage_path}",
                    "content_hash": digest,
                    "source_name": "lbt-os product (byproduct)",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "collector": "product",
                    "schema_version": SCHEMA_VERSION,
                    "promotion_status": "pending",
                }
            )
            .execute()
            .data[0]["id"]
        )

    # Silver write: org-private rows (Class 1 RLS). Keyed by artifact_id so
    # a replayed promotion upserts instead of duplicating (MAJOR 4).
    rows = [
        {
            "org_id": org_id,
            "artifact_id": artifact_id,
            "vertical": snap["cohort_key"].split(":")[0],
            "cohort_key": snap["cohort_key"],
            "metric_name": m["metric_name"],
            "metric_value": m["metric_value"],
            "sample_size": m.get("sample_size"),
            "period": snap["period"],
            "computed_at": snap["computed_at"],
        }
        for m in snap["metrics"]
    ]
    inserted = (
        db.table("benchmark_org_metrics")
        .upsert(rows, on_conflict="artifact_id,metric_name")
        .execute()
        .data
        or []
    )

    # Lineage: org-private rows MAY carry artifact_id (the org reads its own
    # lineage via data_events_org_lineage). Shared-asset events never do.
    for row in inserted:
        db.table("data_events").insert(
            {
                "event_type": "promoted",
                "silver_table": "benchmark_org_metrics",
                "silver_id": row["id"],
                "promotion_batch_id": run_id,
                "artifact_id": artifact_id,
                "actor": "product",
                "contract_version": SCHEMA_VERSION,
                "note": f"byproduct promotion, metric={row['metric_name']}",
            }
        ).execute()

    db.table("bronze_artifacts").update(
        {
            "promotion_status": "promoted",
            "promoted_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", artifact_id).execute()
    return True


def _dead_letter(db, run_id: str, record: dict, reason: str) -> None:
    spec = _redaction_spec()  # raises if the list is missing: no list, no write
    redacted = _redact(record)
    db.table("dead_letters").insert(
        {
            "run_id": run_id,
            "niche": NICHE,
            "redaction_spec_version": int(spec.get("version", 0)),
            "raw_payload": json.dumps(redacted, sort_keys=True),
            "reason": reason,
        }
    ).execute()
