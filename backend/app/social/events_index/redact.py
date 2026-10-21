"""TW-651: payload redaction at the bronze boundary (spec 10.6).

Bronze keeps the raw item for extraction and audit only where the
source's terms posture allows retention, and even then the source's
expression never lands: description bodies, photos, images, and
excerpts are stripped before hashing and storage, on every
connector, so no downstream code can serve what was never kept.
"""

from __future__ import annotations

import json
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


def redact_json(value: Any) -> Any:
    """Recursively drop never-store keys from a decoded JSON item."""
    if isinstance(value, dict):
        return {
            key: redact_json(val)
            for key, val in value.items()
            if key.lower() not in NEVER_STORE_KEYS
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
