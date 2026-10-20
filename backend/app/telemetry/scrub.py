"""TW-622 Twistor Telemetry — PII scrubbing at ingestion.

Doctrine TW-552: PII is hashed at ingestion, stored ONLY in the raw
events table, never raw in any queryable gold surface. This module is
pure: same input + org -> same output, no I/O, no secrets.

Matching is on whole key SEGMENTS (split on case/snake/kebab/dot
boundaries), not substrings: "user_email" and "emailAddress" match the
"email" hint; "hostname", "filename", "shipping", "adobe" do NOT match
" name"/"ip"/"dob". Over-matching silently corrupts analytics; under-
matching leaks PII. When in doubt, prefer the org-configured
`extra_pii_keys` list.

V1 LIMITATION (documented, not hidden): this is a key-NAME heuristic.
Free-text values under non-hint keys ("note", "description", "city")
are stored and returned as-is. Orgs with PII-shaped custom fields must
declare them via `extra_pii_keys`; a schema-declared PII-field registry
is the V2 hardening.

V1 note: HMAC key is the org_id (deterministic per org, documented).
V2 hardening: dedicated per-org salt with rotation.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from typing import Any

# Key-name hints whose VALUES are treated as PII and hashed. Compared as
# whole segments (see _segments), never substrings.
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

_HINT_SEQS = [tuple(h.split("_")) for h in DEFAULT_PII_KEY_HINTS]


def _segments(key: str) -> list[str]:
    """Split a key into whole segments: camelCase, snake_case,
    kebab-case, and dotted keys all split. 'emailAddress' ->
    ['email', 'address']; 'hostname' -> ['hostname']."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return [s for s in re.split(r"[^a-z0-9]+", spaced.lower()) if s]


def _hmac_hex(value: str, org_id: str) -> str:
    return hmac.new(
        org_id.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _is_pii_key(key: str, extra_hints: tuple[str, ...] = ()) -> bool:
    segs = _segments(key)
    seqs = _HINT_SEQS + [tuple(_segments(h)) for h in extra_hints]
    for hs in seqs:
        n = len(hs)
        if n and any(tuple(segs[i : i + n]) == hs for i in range(len(segs) - n + 1)):
            return True
    return False


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
