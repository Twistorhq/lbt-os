"""TW-650 Twistor Social — geo pure functions (TDD RED).

Spec Section 7.1: the device evaluates the geofence; the server receives
a verdict, never coordinates. Spec Section 8 + the TW-648 design delta:
venue events reveal inside a 48-hour window; home events inside the
3-hour home window (opens 3 hours before start, closes at event end) —
the most sensitive address class gets the tightest window.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.social import geo

START = datetime(2026, 10, 24, 19, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=3)


class TestHaversine:
    def test_same_point_zero(self):
        assert geo.haversine_m(39.7392, -104.9903, 39.7392, -104.9903) == 0.0

    def test_known_distance_denver_to_boulder_approx(self):
        # Denver (39.7392,-104.9903) to Boulder (40.0150,-105.2705) ~ 38.7 km
        d = geo.haversine_m(39.7392, -104.9903, 40.0150, -105.2705)
        assert 37_000 < d < 40_500

    def test_within_radius(self):
        assert geo.is_within_radius(120.0, 150.0) is True
        assert geo.is_within_radius(151.0, 150.0) is False


class TestRevealWindows:
    def test_venue_window_opens_48h_before(self):
        w = geo.reveal_window("venue", START, END, START - timedelta(hours=49))
        assert w.is_open is False
        w = geo.reveal_window("venue", START, END, START - timedelta(hours=48))
        assert w.is_open is True
        assert w.opens_at == START - timedelta(hours=48)
        assert w.closes_at == END

    def test_home_window_opens_3h_before(self):
        w = geo.reveal_window("home", START, END, START - timedelta(hours=4))
        assert w.is_open is False
        w = geo.reveal_window("home", START, END, START - timedelta(hours=3))
        assert w.is_open is True
        assert w.opens_at == START - timedelta(hours=3)

    def test_window_closes_at_event_end(self):
        for venue_type in ("venue", "home"):
            w = geo.reveal_window(venue_type, START, END, END + timedelta(minutes=1))
            assert w.is_open is False

    def test_unknown_venue_type_fails_closed(self):
        with pytest.raises(geo.GeoError):
            geo.reveal_window("castle", START, END, START)

    def test_home_window_is_strictly_tighter(self):
        venue = geo.reveal_window("venue", START, END, START)
        home = geo.reveal_window("home", START, END, START)
        assert home.opens_at > venue.opens_at


class TestArrivalPayload:
    def test_verdict_payload_ok(self):
        v = geo.validate_arrival_payload(
            {"within_geofence": True, "accuracy_bucket": "fine"}
        )
        assert v.within_geofence is True
        assert v.accuracy_bucket == "fine"

    def test_coordinates_in_payload_rejected(self):
        with pytest.raises(geo.GeoError):
            geo.validate_arrival_payload(
                {
                    "within_geofence": True,
                    "accuracy_bucket": "fine",
                    "latitude": 39.7392,
                    "longitude": -104.9903,
                }
            )

    def test_any_extra_key_rejected(self):
        with pytest.raises(geo.GeoError):
            geo.validate_arrival_payload(
                {"within_geofence": True, "accuracy_bucket": "fine", "device": "x"}
            )

    def test_bad_bucket_rejected(self):
        with pytest.raises(geo.GeoError):
            geo.validate_arrival_payload(
                {"within_geofence": True, "accuracy_bucket": "exact"}
            )
