"""TW-651: normalization and the dedupe fingerprint (spec 10.3).

The same event arrives from an ICS feed, an API, and a venue
calendar, spelled three slightly different ways. Dedupe works only
if all three land on one fingerprint: SHA-256 over the normalized
title (lowercased, punctuation and whitespace collapsed), the venue
fingerprint (coordinates rounded to four decimal places, roughly an
11 meter cell, or the address fingerprint while coordinates are
pending), and the start time bucketed to five minutes, so a small
cross-source clock disagreement still merges.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_text(text: str | None) -> str:
    """Lowercase, strip accents, drop punctuation, collapse spaces."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_text.lower()
    cleaned = _NON_ALNUM_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", cleaned).strip()


def normalize_title(title: str) -> str:
    return normalize_text(title)


def address_fingerprint(
    street_address: str | None,
    city: str | None,
    venue_name: str | None = None,
) -> str:
    """Stable identity for a venue address (evt_venues UNIQUE key).

    Street address when the source publishes one; otherwise the venue
    name, because many community listings publish a place, not an
    address. City is part of the identity either way.
    """
    basis = normalize_text(street_address) or normalize_text(venue_name)
    city_part = normalize_text(city)
    return hashlib.sha256(f"{basis}|{city_part}".encode("utf-8")).hexdigest()


_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(latitude: float, longitude: float, precision: int = 7) -> str:
    """Standard geohash. Precision 7 cells are about 150m across:
    close enough to say 'same venue block', never a person's address.
    """
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    bits: list[int] = []
    even = True
    while len(bits) < precision * 5:
        if even:
            mid = (lng_range[0] + lng_range[1]) / 2
            if longitude >= mid:
                bits.append(1)
                lng_range[0] = mid
            else:
                bits.append(0)
                lng_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if latitude >= mid:
                bits.append(1)
                lat_range[0] = mid
            else:
                bits.append(0)
                lat_range[1] = mid
        even = not even
    chars = []
    for i in range(0, len(bits), 5):
        value = 0
        for bit in bits[i : i + 5]:
            value = (value << 1) | bit
        chars.append(_GEOHASH_ALPHABET[value])
    return "".join(chars)


def venue_fingerprint(
    latitude: float | None,
    longitude: float | None,
    addr_fingerprint: str,
) -> str:
    if latitude is not None and longitude is not None:
        return f"geo:{latitude:.4f},{longitude:.4f}"
    return f"addr:{addr_fingerprint}"


def start_bucket(starts_at: datetime) -> str:
    utc = starts_at.astimezone(timezone.utc)
    floored = utc.replace(minute=(utc.minute // 5) * 5, second=0, microsecond=0)
    return floored.isoformat()


def listing_fingerprint(title: str, starts_at: datetime, venue_fp: str) -> str:
    basis = f"{normalize_title(title)}|{start_bucket(starts_at)}|{venue_fp}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
