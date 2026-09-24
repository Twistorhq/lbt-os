"""Manual CSV import service — TW-208 self-healing retrofit.

Standing rule: one error never crushes the full process. A poison row is
skipped loudly (row number + reason, counted in the result and the import
log) instead of aborting the whole import with a 400. Bulk inserts go in
chunks with transient retries; a chunk that still won't insert falls back to
per-row inserts so one DB-level poison row can't sink the other 499.

Whole-file problems (too large, not UTF-8, no headers, no rows, unknown
entity type) are still honest 400s — those are bad requests, not poison rows.

At-least-once insert semantics (explicit trade-off): chunk retries and the
per-row fallback can re-insert rows when a failure is ambiguous — e.g. the
server committed the chunk but the response was lost, and the timeout was
(classified) transient. A retried duplicate is visible (in the `imported`
count and the table) and dedupe-able; a dropped batch is silent data loss,
so this pipeline retries rather than risks dropping. Rows rejected by
validation before insert are never retried.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from supabase import Client

from .. import self_healing as sh

log = logging.getLogger("twistor.self_healing")

MAX_CSV_FILE_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 5_000
FORMULA_PREFIXES = ("=", "+", "-", "@")

# Bulk inserts go in chunks; a chunk that fails even after retries falls back
# to per-row inserts to isolate the poison row.
INSERT_CHUNK_ROWS = 500
# How many skipped rows to include inline in the result + import log.
SKIPPED_SAMPLE_SIZE = 20

SUPPORTED_ENTITIES = ("leads", "customers", "sales", "expenses")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal(value: str | None, field: str, required: bool = False) -> float | None:
    """Coerce a CSV cell to float. Raises ValueError (row-level) on garbage.

    TW-208: row-level validation failures are ValueError so the per-row
    isolator can skip the row loudly; whole-file problems stay HTTPException.
    """
    if value is None or value == "":
        if required:
            raise ValueError(f"missing required numeric field '{field}'")
        return None
    try:
        return float(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid numeric value for '{field}': {value}")


async def _read_upload_bytes(upload, max_bytes: int = MAX_CSV_FILE_BYTES) -> bytes:
    file_bytes = await upload.read(max_bytes + 1)
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"CSV file is too large. Maximum size is {max_bytes // (1024 * 1024)} MB.",
        )
    return file_bytes


def _neutralize_formula(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    stripped = value.lstrip()
    if stripped.startswith(FORMULA_PREFIXES) and field not in {"phone"}:
        return f"'{value}"
    return value


def _csv_rows(file_bytes: bytes) -> list[dict[str, str]]:
    if b"\x00" in file_bytes:
        raise HTTPException(status_code=400, detail="CSV file must be plain text.")
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must be UTF-8 encoded text.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file is missing headers.")
    rows: list[dict[str, str]] = []
    for idx, row in enumerate(reader, start=1):
        if idx > MAX_CSV_ROWS:
            raise HTTPException(status_code=400, detail=f"CSV file exceeds the {MAX_CSV_ROWS} row limit.")
        rows.append({
            (k or "").strip(): _neutralize_formula((v or "").strip(), (k or "").strip())
            for k, v in row.items()
        })
    return rows


def _log_import(
    db: Client,
    org_id: str,
    entity_type: str,
    filename: str | None,
    rows_imported: int,
    row_count: int,
    skipped_rows: int = 0,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write to csv_import_logs. Silently skips if the table doesn't exist yet.

    TW-208: persists skipped_rows + a details JSONB dead-letter summary so the
    morning standup can read exactly what was skipped. Falls back to the
    legacy column set when the migration hasn't been applied yet.
    """
    status = "failed" if error else ("partial" if skipped_rows else "success")
    extended = {
        "org_id": org_id,
        "entity_type": entity_type,
        "filename": filename,
        "rows_imported": rows_imported,
        "row_count": row_count,
        "skipped_rows": skipped_rows,
        "details": details,
        "status": status,
        "error": error,
    }
    try:
        db.table("csv_import_logs").insert(extended).execute()
        return
    except Exception as exc:
        # Any failure of the extended insert (missing columns on a
        # pre-migration DB, or a transient DB blip) falls through to the
        # legacy shape — an import log that loses its details is better
        # than no import log at all.
        log.warning(
            "self-healing: extended import-log insert failed (%s); "
            "trying legacy shape", type(exc).__name__)
    try:
        legacy = {k: v for k, v in extended.items() if k not in ("skipped_rows", "details")}
        db.table("csv_import_logs").insert(legacy).execute()
    except Exception as exc:
        # Both shapes failed: log loudly. A lost import log must never be
        # silent — the morning standup reads these.
        log.error("self-healing: import logging failed entirely: %s", exc)


def list_import_history(db: Client, org_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Return recent CSV import logs. Returns empty list if table doesn't exist yet."""
    try:
        result = (
            db.table("csv_import_logs")
            .select("*")
            .eq("org_id", org_id)
            .order("imported_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        return []


def _row_payload(entity: str, org_id: str, row: dict[str, str], created_at: str) -> dict[str, Any]:
    """Build one insert payload. Raises ValueError naming why the row is poison."""
    if entity == "leads":
        name = (row.get("name") or "").strip()
        if not name:
            raise ValueError("missing required 'name'")
        return {
            "org_id": org_id,
            "name": name,
            "email": row.get("email") or None,
            "phone": row.get("phone") or None,
            "source": row.get("source") or "manual_csv",
            "status": row.get("status") or "new",
            "service_interest": row.get("service_interest") or row.get("service") or None,
            "estimated_value": _to_decimal(row.get("estimated_value"), "estimated_value"),
            "notes": row.get("notes") or "Imported from CSV.",
            "created_at": created_at,
            "updated_at": created_at,
        }
    if entity == "customers":
        name = (row.get("name") or "").strip()
        if not name:
            raise ValueError("missing required 'name'")
        return {
            "org_id": org_id,
            "name": name,
            "email": row.get("email") or None,
            "phone": row.get("phone") or None,
            "address": row.get("address") or None,
            "notes": row.get("notes") or "Imported from CSV.",
            "created_at": created_at,
            "updated_at": created_at,
        }
    if entity == "sales":
        service = (row.get("service") or "").strip()
        if not service:
            raise ValueError("missing required 'service'")
        return {
            "org_id": org_id,
            "service": service,
            "amount": _to_decimal(row.get("amount"), "amount", required=True),
            "cost": _to_decimal(row.get("cost"), "cost") or 0,
            "payment_method": row.get("payment_method") or None,
            "payment_status": row.get("payment_status") or "paid",
            "source": row.get("source") or "manual_csv",
            "invoice_number": row.get("invoice_number") or None,
            "notes": row.get("notes") or "Imported from CSV.",
            "sold_at": row.get("sold_at") or created_at,
            "created_at": created_at,
            "updated_at": created_at,
        }
    if entity == "expenses":
        category = (row.get("category") or "").strip()
        description = (row.get("description") or "").strip()
        if not category or not description:
            raise ValueError("missing required 'category' and 'description'")
        return {
            "org_id": org_id,
            "category": category,
            "description": description,
            "amount": _to_decimal(row.get("amount"), "amount", required=True),
            "vendor": row.get("vendor") or None,
            "is_recurring": (row.get("is_recurring") or "").lower() in {"true", "1", "yes", "y"},
            "recurrence_period": row.get("recurrence_period") or None,
            "expense_date": row.get("expense_date") or created_at[:10],
            "created_at": created_at,
            "updated_at": created_at,
        }
    raise ValueError(f"unsupported import type '{entity}'")


def _insert_chunk(db: Client, table: str, chunk: list[dict[str, Any]]) -> None:
    db.table(table).insert(chunk).execute()


async def import_csv_rows(db: Client, org_id: str, entity_type: str, upload_file, filename: str | None = None) -> dict[str, Any]:
    file_bytes = await _read_upload_bytes(upload_file)
    rows = _csv_rows(file_bytes)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file contains no data rows.")

    entity = entity_type.lower()
    if entity not in SUPPORTED_ENTITIES:
        raise HTTPException(status_code=400, detail=f"Unsupported import type '{entity_type}'.")
    created_at = _now_iso()

    # Per-row fault isolation: a poison row is skipped loudly, never aborts
    # the import. Row numbers are 1-based data rows (header excluded).
    dlq = sh.DeadLetterQueue("csv-import")
    indexed: list[tuple[int, dict[str, Any]]] = []
    for i, row in enumerate(rows):
        row_no = i + 1
        try:
            indexed.append((row_no, _row_payload(entity, org_id, row, created_at)))
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            dlq.collect(row_no, row, exc)

    # Chunked bulk inserts with transient retries; a chunk that still won't go
    # in falls back to per-row inserts so one DB-level poison row can't sink
    # the other 499.
    imported = 0
    for start in range(0, len(indexed), INSERT_CHUNK_ROWS):
        chunk = indexed[start:start + INSERT_CHUNK_ROWS]
        payloads = [p for _, p in chunk]
        try:
            sh.retry_with_backoff(
                lambda: _insert_chunk(db, entity, payloads),
                attempts=3, base_delay=0.3,
            )
            imported += len(payloads)
        except Exception:  # noqa: BLE001 - per-row fallback is the recovery
            log.warning(
                "self-healing: chunk [%d:%d] failed as a batch; "
                "falling back to per-row inserts", start, start + len(chunk))
            for row_no, payload in chunk:
                try:
                    db.table(entity).insert(payload).execute()
                    imported += 1
                except Exception as exc:  # noqa: BLE001 - isolation is the point
                    dlq.collect(row_no, payload, exc)

    skipped_rows = len(dlq.items)
    details = dlq.summarize(sample_size=SKIPPED_SAMPLE_SIZE)
    _log_import(db, org_id, entity, filename, rows_imported=imported,
                row_count=len(rows), skipped_rows=skipped_rows, details=details)
    return {
        "entity_type": entity,
        "imported": imported,
        "row_count": len(rows),
        "skipped_rows": skipped_rows,
        "skipped": [
            {"row": e["index"], "reason": e["error"]} for e in details["sample"]
        ],
    }
