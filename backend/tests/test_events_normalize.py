"""TW-651: normalization, venue fingerprints, and the dedupe fingerprint.

Spec 10.3: the fingerprint is SHA-256 over the normalized title
(lowercased, punctuation and whitespace collapsed), the venue
fingerprint (coordinates rounded to four decimal places, or the
address fingerprint while coordinates are pending), and the start
time bucketed to five minutes. Two sources listing the same event
must land on the same fingerprint or dedupe is fiction.
"""

from datetime import datetime, timezone

from app.social.events_index import normalize


def test_normalize_title_lowercases_and_collapses():
    assert (
        normalize.normalize_title("  Preschool   Storytime! ") == "preschool storytime"
    )
    assert (
        normalize.normalize_title("Art Walk & Night-Market") == "art walk night market"
    )
    assert normalize.normalize_title("Café con Leche Night") == "cafe con leche night"


def test_normalize_title_equates_source_variants():
    a = normalize.normalize_title("First Friday Art Walk!")
    b = normalize.normalize_title("first  friday   art walk")
    assert a == b


def test_start_bucket_floors_to_five_minutes():
    dt = datetime(2026, 10, 24, 10, 34, 59, tzinfo=timezone.utc)
    assert normalize.start_bucket(dt) == "2026-10-24T10:30:00+00:00"
    dt2 = datetime(2026, 10, 24, 10, 35, 0, tzinfo=timezone.utc)
    assert normalize.start_bucket(dt2) == "2026-10-24T10:35:00+00:00"


def test_start_bucket_converts_to_utc_first():
    from zoneinfo import ZoneInfo

    dt = datetime(2026, 10, 24, 4, 32, tzinfo=ZoneInfo("America/Denver"))
    assert normalize.start_bucket(dt) == "2026-10-24T10:30:00+00:00"


def test_geohash_encode_known_value():
    # Canonical geohash example point (Wikipedia): u4pruydqqvj area.
    h = normalize.geohash_encode(57.64911, 10.40744, precision=11)
    assert h.startswith("u4pruydqqv")


def test_geohash_nearby_points_share_prefix():
    a = normalize.geohash_encode(39.75891, -104.99988, precision=7)
    b = normalize.geohash_encode(39.75902, -104.99971, precision=7)
    assert a[:5] == b[:5]


def test_venue_fingerprint_prefers_rounded_coordinates():
    fp = normalize.venue_fingerprint(39.758912345, -104.99988765, "a" * 64)
    assert fp == "geo:39.7589,-105.0000" or fp == "geo:39.7589,-104.9999"


def test_venue_fingerprint_falls_back_to_address():
    addr = normalize.address_fingerprint("2901 Inca St", "Denver")
    fp = normalize.venue_fingerprint(None, None, addr)
    assert fp == f"addr:{addr}"


def test_address_fingerprint_normalizes_variants():
    a = normalize.address_fingerprint("2901  Inca St.", "Denver")
    b = normalize.address_fingerprint("2901 inca st", "DENVER")
    assert a == b


def test_address_fingerprint_uses_venue_name_without_street():
    fp = normalize.address_fingerprint(None, "Denver", venue_name="Confluence Park")
    assert fp == normalize.address_fingerprint(
        None, "Denver", venue_name="confluence park"
    )
    assert fp != normalize.address_fingerprint(None, "Denver", venue_name="Other Park")


def test_listing_fingerprint_stable_across_source_variants():
    start = datetime(2026, 10, 30, 18, 2, tzinfo=timezone.utc)
    venue_fp = normalize.venue_fingerprint(39.76012, -105.00011, "b" * 64)
    a = normalize.listing_fingerprint(
        "First Friday Art Walk & Night Market", start, venue_fp
    )
    b = normalize.listing_fingerprint(
        "first friday art walk night market", start, venue_fp
    )
    assert a == b
    assert len(a) == 64


def test_listing_fingerprint_changes_with_start_or_venue():
    start = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    later = datetime(2026, 10, 30, 19, 0, tzinfo=timezone.utc)
    fp1 = normalize.venue_fingerprint(39.76012, -105.00011, "b" * 64)
    fp2 = normalize.venue_fingerprint(39.10000, -105.00011, "b" * 64)
    base = normalize.listing_fingerprint("Art Walk", start, fp1)
    assert base != normalize.listing_fingerprint("Art Walk", later, fp1)
    assert base != normalize.listing_fingerprint("Art Walk", start, fp2)
    # Same five-minute bucket: a two-minute source disagreement dedupes.
    near = datetime(2026, 10, 30, 18, 4, tzinfo=timezone.utc)
    assert base == normalize.listing_fingerprint("Art Walk", near, fp1)
