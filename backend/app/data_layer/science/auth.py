"""TW-593 Track 2 · Shared API-key auth for the science data layer.

Prepared by Twistor Holdings LLC.

V1 trust rule (Rosa): reads are open on the synthetic-only prototype;
every MUTATING endpoint (ingest, corrections, benchmark submissions) requires
a valid API key from sci_api_keys. Key scope is limited by lane scopes;
lab-bound keys can only write for their own lab.

Design note: this is a plain function, not a FastAPI Depends sub-dependency,
so lane tests can drive it through their existing fake-DB dependency_overrides
without any override surgery. Endpoints read the X-API-Key header themselves
and call require_science_key().
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import HTTPException

log = logging.getLogger(__name__)

_KEYS_TABLE = "sci_api_keys"


def hash_key(raw: str) -> str:
    """sha256 of the raw key. The raw key is shown once at creation and never stored."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_science_key(x_api_key: str | None, db: Any, lane: str) -> dict[str, Any]:
    """Enforce a valid API key with write scope for `lane`.

    Required scope: f"science:{lane}:write" or the wildcard "science:*:write".
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
    return key


def enforce_lab_binding(key: dict[str, Any], lab_ids: list[str]) -> None:
    """A lab-bound key may only write records for its own lab."""
    bound = key.get("lab_id")
    if bound and any(str(lid) != str(bound) for lid in lab_ids):
        raise HTTPException(
            status_code=403, detail="key is bound to a different lab"
        )
