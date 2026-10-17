"""TW-569: data API v1 — niche 1 serving (trades benchmarks).

One FastAPI router family (TW-564 §4). This ticket ships:
  GET /data/v1/benchmarks/{cohort_key}

Contract (TW-564 Major 5b):
- Auth: X-API-Key header. key_hash = HMAC(raw_key, server secret).
  The key's niche_scope must include 'trades_benchmarks'.
- Idempotency-Key header REQUIRED. Missing -> 400, not a silent fallback.
- Rate limit: in-memory token bucket per key; 429 with Retry-After.
  data_api_usage is the reconciliation record (flushed, not enforced).
- Serve the read FIRST from gold (published=true only), then enqueue the
  toll to toll_emit_queue. A failed toll write never fails the read.
- Response carries as_of (gold refresh watermark) + provenance
  (cohort n, source, contract version). Honest uncertainty: n=5 says so.

Class 2 RLS: the API (service_role) is the policy. No JWT role reads the
gold tables directly; this router enforces key.niche_scope before serving.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query

from ..database import get_db
from ..services.benchmarks import METRIC_ALLOWLIST

log = logging.getLogger("twistor.data_api")

router = APIRouter(prefix="/data/v1", tags=["data-api"])

NICHE = "trades_benchmarks"
EVENT_TYPE = "data.benchmark.query"

# Metrics this endpoint serves (benchmark cohort stats vocabulary).
BENCHMARK_METRICS = frozenset(METRIC_ALLOWLIST) | frozenset(
    {
        "missed_call_recovery_rate",
        "time_to_first_response_min",
        "toll_spend_per_booked_job_cents",
        "estimate_close_rate",
        "avg_job_value_cents",
    }
)


def _hmac_secret() -> bytes:
    # Vault path: datalayer/signing/api_key_hmac (versioned).
    # Env-configured here; the Vault mount is an ops step, documented.
    return os.environ.get("DATALAYER_API_KEY_HMAC_SECRET", "dev-only-insecure").encode()


def hash_api_key(raw_key: str) -> str:
    """HMAC-SHA256 of the raw key. The raw key is shown once at creation
    and never stored; only this hash lives in data_api_keys.key_hash."""
    return hmac.new(_hmac_secret(), raw_key.encode(), hashlib.sha256).hexdigest()


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)


# --- In-memory token buckets (enforcement path; see TW-564 Minor 9) ---
_buckets: dict[str, dict] = {}
_buckets_lock = threading.Lock()
_usage_pending: dict[str, int] = {}
_usage_lock = threading.Lock()


def _check_rate_limit(key_id: str, per_min: int) -> None:
    now = time.monotonic()
    with _buckets_lock:
        b = _buckets.get(key_id)
        if b is None or now - b["window_start"] >= 60:
            b = {"tokens": float(per_min), "window_start": now}
            _buckets[key_id] = b
        if b["tokens"] < 1:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": "60"},
            )
        b["tokens"] -= 1
    with _usage_lock:
        _usage_pending[key_id] = _usage_pending.get(key_id, 0) + 1


def flush_usage(db) -> int:
    """Reconciliation flush: write pending counters to data_api_usage.

    Runs every 60s (scheduler). NOT the enforcement path — a flush failure
    never blocks serving.
    """
    with _usage_lock:
        pending = dict(_usage_pending)
        _usage_pending.clear()
    if not pending:
        return 0
    window = time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime())
    flushed = 0
    for key_id, count in pending.items():
        try:
            db.table("data_api_usage").upsert(
                {"key_id": key_id, "window_start": window, "count": count},
                on_conflict="key_id,window_start",
            ).execute()
            flushed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("usage flush failed for %s: %s", key_id, exc)
    return flushed


def _authenticate(db, raw_key: str | None) -> dict:
    if not raw_key:
        raise HTTPException(status_code=401, detail="X-API-Key required")
    # Indexed exact match on the deterministic HMAC (TW-569 MAJOR 3):
    # key_hash is HMAC(raw_key, server_secret), so the lookup is a single
    # indexed row fetch, never a full-table scan. compare_digest kills
    # timing oracles on the (impossible-by-index) multi-match path.
    digest = hash_api_key(raw_key)
    rows = (
        db.table("data_api_keys")
        .select(
            "id, key_hash, partner_id, billing_org_id, niche_scope,"
            " plan_id, rate_limit_per_min, is_active"
        )
        .eq("key_hash", digest)
        .eq("is_active", True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows or not verify_api_key(raw_key, rows[0]["key_hash"]):
        raise HTTPException(status_code=401, detail="invalid API key")
    row = rows[0]
    if NICHE not in (row.get("niche_scope") or []):
        raise HTTPException(status_code=403, detail="key not scoped for this niche")
    return row


@router.get("/benchmarks/{cohort_key}")
def get_benchmarks(
    cohort_key: str,
    metric: str | None = Query(default=None),
    period: str = Query(default="30d", pattern="^(30d|90d)$"),
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Serve published cohort benchmarks for one cohort key.

    Idempotency-Key is REQUIRED (TW-564 Major 5b): clients that omit it
    get a 400 with a clear error, not a silent fallback.
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required on tollable endpoints",
        )
    if metric is not None and metric not in BENCHMARK_METRICS:
        raise HTTPException(status_code=400, detail=f"unknown metric: {metric}")

    db = get_db()
    key = _authenticate(db, x_api_key)
    _check_rate_limit(key["id"], int(key["rate_limit_per_min"]))

    q = (
        db.table("benchmark_cohort_stats")
        .select(
            "metric_name, p50, mean, n_orgs, period, computed_at, consecutive_passes"
        )
        .eq("cohort_key", cohort_key)
        .eq("period", period)
        .eq("published", True)
    )
    if metric:
        q = q.eq("metric_name", metric)
    rows = q.execute().data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "insufficient_cohort_data",
                "cohort_key": cohort_key,
                "detail": "Cohort benchmarks appear once at least 5 consenting "
                "shops contribute for 3 consecutive refreshes.",
            },
        )

    as_of = max(str(r["computed_at"]) for r in rows)
    n_orgs = max(int(r["n_orgs"]) for r in rows)
    body = {
        "cohort_key": cohort_key,
        "period": period,
        "metrics": [
            {
                "metric_name": r["metric_name"],
                "p50": float(r["p50"]),
                "mean": float(r["mean"]),
                "n_orgs": int(r["n_orgs"]),
            }
            for r in rows
        ],
        "as_of": as_of,
        "provenance": {
            "n_orgs": n_orgs,
            "source": "byproduct",
            "source_kind": "lbt-os product",
            "contract_version": 1,
            "period": period,
        },
    }

    # Serve first, toll out-of-band. Enqueue is UNIQUE on idempotency_key:
    # a retried request with the same key cannot double-enqueue.
    try:
        db.table("toll_emit_queue").insert(
            {
                "idempotency_key": idempotency_key,
                "key_id": key["id"],
                "billing_org_id": key["billing_org_id"],
                "endpoint": f"GET /data/v1/benchmarks/{cohort_key}",
                "plan_id": key.get("plan_id"),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001 - never fail the read
        log.error("toll enqueue failed (read already served): %s", exc)

    return body
