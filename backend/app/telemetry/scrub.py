"""TW-622 Twistor Telemetry — PII scrubbing at ingestion.

Doctrine TW-552: PII is hashed at ingestion, stored ONLY in the raw
events table, never raw in any queryable gold surface. This module is
pure: same input + org -> same output, no I/O, no secrets.

V1 note: HMAC key is the org_id (deterministic per org, documented).
V2 hardening: dedicated per-org salt with rotation.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from typing import Any

# Key names whose VALUES are treated as PII and hashed. Matching is
# case-insensitive and substring-based: "user_email" matches "email".
DEFAULT_PII_KEY_HINTS = (
    "email",
    "phone",
    "mobile",
    "ssn",
    "social_security",
    "address",
    "street",
    "zip",
    "postal",
    "dob",
    "birthdate",
    "first_name",
    "firstname",
    "last_name",
    "lastname",
    "full_name",
    "fullname",
    "name",
    "ip",
    "credit_card",
    "card_number",
)


def _hmac_hex(value: str, org_id: str) -> str:
    return hmac.new(
        org_id.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _is_pii_key(key: str, extra_hints: tuple[str, ...] = ()) -> bool:
    lowered = key.lower()
    return any(h in lowered for h in DEFAULT_PII_KEY_HINTS + tuple(extra_hints))


def _scrub_value(
    value: Any, is_pii: bool, org_id: str, extra_hints: tuple[str, ...]
) -> Any:
    if isinstance(value, dict):
        return {
            k: _scrub_value(
                v, is_pii or _is_pii_key(k, extra_hints), org_id, extra_hints
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(v, is_pii, org_id, extra_hints) for v in value]
    if is_pii:
        return _hmac_hex(str(value), org_id)
    return value


def scrub_properties(
    properties: dict[str, Any],
    org_id: str,
    extra_pii_keys: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a scrubbed COPY of properties. Never mutates the input."""
    hints = tuple(k.lower() for k in extra_pii_keys)
    return _scrub_value(copy.deepcopy(properties), False, org_id, hints)


def hash_distinct_id(distinct_id: str, org_id: str) -> str:
    """distinct_id is ALWAYS hashed — raw identifiers never reach storage."""
    return _hmac_hex(distinct_id, org_id)
