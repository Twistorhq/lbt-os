"""TW-651: payload redaction at the bronze boundary (spec 10.6).

Bronze keeps the raw item for extraction and audit only where the
source's terms posture allows retention, and even then the source's
expression never lands: description bodies, photos, images, and
excerpts are stripped before hashing and storage, on every
connector, so no downstream code can serve what was never kept.

Contact details are stripped by class, not by spelling list. The
exact-name set below covers the expression and price keys; any key
whose name tokenizes to a contact token (organizer_phone,
contact_email, venue_phone, telephone) drops too, because real API
payloads hide contact data under compound keys as often as under
the plain ones. The ICS boundary makes the matching cut by property
name in ics.py (ORGANIZER, CONTACT, ATTENDEE).
"""

from __future__ import annotations

import json
import re
from typing import Any

NEVER_STORE_KEYS = frozenset(
    {
        "description",
        "description_text",
        "body",
        "excerpt",
        "photo",
        "photo_url",
        "image",
        "images",
        "logo",
        "ticket_price",
        "price",
        "organizer_email",
        "email",
        "phone",
    }
)

# A key carrying any of these name tokens carries personal contact
# data, whatever the surrounding words are.
CONTACT_KEY_TOKENS = frozenset(
    {"email", "phone", "telephone", "mobile", "cell", "fax", "contact"}
)

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _key_tokens(key: str) -> set[str]:
    spaced = _CAMEL_RE.sub("_", key)
    return {t for t in _TOKEN_SPLIT_RE.split(spaced.lower()) if t}


def _is_never_store_key(key: str) -> bool:
    if key.lower() in NEVER_STORE_KEYS:
        return True
    return bool(_key_tokens(key) & CONTACT_KEY_TOKENS)


def redact_json(value: Any) -> Any:
    """Recursively drop never-store keys from a decoded JSON item."""
    if isinstance(value, dict):
        return {
            key: redact_json(val)
            for key, val in value.items()
            if not _is_never_store_key(key)
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
