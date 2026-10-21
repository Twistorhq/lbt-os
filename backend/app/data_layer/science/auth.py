"""TW-593 Track 2 · Shared API-key auth for the science data layer.

Prepared by Twistor Holdings LLC.

V1 trust rule (Rosa): reads are open on the synthetic-only prototype;
every MUTATING endpoint (ingest, corrections, benchmark submissions) requires
a valid API key from sci_api_keys. Key scope is limited by lane scopes;
lab-bound keys can only write for their own lab. Every authenticated call is
rate-limited per key (rate_limit_per_min on the key row, 429 on excess).

Design note: this is a plain function, not a FastAPI Depends sub-dependency,
so lane tests can drive it through their existing fake-DB dependency_overrides
without any override surgery. Endpoints read the X-API-Key header themselves
and call require_science_key().
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from typing import Any

from fastapi import HTTPException

log = logging.getLogger(__name__)

_KEYS_TABLE = "sci_api_keys"
_DEFAULT_RATE_LIMIT_PER_MIN = 60


class _KeyRateLimiter:
    """Per-process in-memory sliding-window limiter, keyed on API key id.

    Mirrors backend/app/limiter.py semantics (per-process, same as slowapi's
    default storage). A multi-worker deployment should move to shared storage.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, max_calls: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= max_calls:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        """Clear all buckets (tests)."""
        with self._lock:
            self._hits.clear()


_key_limiter = _KeyRateLimiter()


def check_key_rate_limit(key: dict[str, Any]) -> None:
    """Enforce the key's rate_limit_per_min. Raises 429 on excess."""
    limit = key.get("rate_limit_per_min") or _DEFAULT_RATE_LIMIT_PER_MIN
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_RATE_LIMIT_PER_MIN
    if limit <= 0:
        return  # non-positive = unlimited (explicit operator choice)
    if not _key_limiter.allow(str(key.get("id")), limit):
        raise HTTPException(
            status_code=429,
            detail=f"API key rate limit exceeded ({limit}/min)",
        )


def hash_key(raw: str) -> str:
    """sha256 of the raw key. The raw key is shown once at creation and never stored."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_science_key(x_api_key: str | None, db: Any, lane: str) -> dict[str, Any]:
    """Enforce a valid API key with write scope for `lane`.

    Required scope: f"science:{lane}:write" or the wildcard "science:*:write".
    Also enforces the key's per-minute rate limit (429 on excess).
    Returns the key record (id, lab_id, scopes). Raises 401/403 otherwise.
    `db` is the lane's database handle (real Supabase client or test fake) —
    looked up via the same .table().select().eq().execute() contract.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key required")
    res = db.table(_KEYS_TABLE).select("*").eq("key_hash", hash_key(x_api_key)).execute()
    rows = getattr(res, "data", []) or []
    if not rows:
        raise HTTPException(status_code=401, detail="unknown API key")
    key = rows[0]
    if not key.get("is_active", True):
        raise HTTPException(status_code=401, detail="API key revoked")
    scopes = set(key.get("scopes") or [])
    if f"science:{lane}:write" not in scopes and "science:*:write" not in scopes:
        raise HTTPException(
            status_code=403, detail=f"key lacks scope science:{lane}:write"
        )
    check_key_rate_limit(key)
    return key


def enforce_lab_binding(key: dict[str, Any], lab_ids: list[str]) -> None:
    """A lab-bound key may only write records for its own lab."""
    bound = key.get("lab_id")
    if bound and any(str(lid) != str(bound) for lid in lab_ids):
        raise HTTPException(
            status_code=403, detail="key is bound to a different lab"
        )
