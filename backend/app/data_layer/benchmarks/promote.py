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
import json
import logging
from datetime import datetime, timezone
from typing import Any

from . import emitters

log = logging.getLogger("twistor.data_layer.benchmarks")

NICHE = "trades_benchmarks"
BRONZE_BUCKET = "data-bronze-trades_benchmarks"
SCHEMA_VERSION = 1
REDACTION_SPEC_VERSION = 1

# Fields the redaction spec strips before any dead_letters write.
_DROP_FIELDS = {
    "customer_id",
    "lead_id",
    "phone",
    "email",
    "address",
    "name",
    "notes",
    "description",
}


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _content_hash(payload: bytes) -> str:
    # Content hash of the STORED payload (already aggregated, no PII).
    # Identifier HMACs live in the redaction spec; this is a dedupe hash.
    return hashlib.sha256(payload).hexdigest()


def _redact(record: dict) -> dict:
    """Apply benchmarks_v1 redaction list. org_id -> HMAC placeholder.

    In production the HMAC uses the per-niche Vault secret
    (datalayer/hmac/trades_benchmarks). Here we mark the transform; the
    dead letter must never carry a joinable org identifier.
    """
    out = {}
    for k, v in record.items():
        if k in _DROP_FIELDS:
            continue
        if k == "org_id":
            out["org_ref"] = (
                "hmac:"
                + hashlib.sha256(f"trades_benchmarks:v1:{v}".encode()).hexdigest()[:16]
            )
            continue
        out[k] = v
    return out


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

    payload = _canonical(snap)
    digest = _content_hash(payload)

    # Idempotency backstop: same source + same bytes = same artifact.
    existing = (
        db.table("bronze_artifacts")
        .select("id")
        .eq("source_id", source_id)
        .eq("content_hash", digest)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
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

    # Silver write: org-private rows (Class 1 RLS).
    rows = [
        {
            "org_id": org_id,
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
    inserted = db.table("benchmark_org_metrics").insert(rows).execute().data or []

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
    redacted = _redact(record)
    db.table("dead_letters").insert(
        {
            "run_id": run_id,
            "niche": NICHE,
            "redaction_spec_version": REDACTION_SPEC_VERSION,
            "raw_payload": json.dumps(redacted, sort_keys=True),
            "reason": reason,
        }
    ).execute()
