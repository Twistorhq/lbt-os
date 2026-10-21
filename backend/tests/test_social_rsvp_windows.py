"""TW-661 service wiring for the v2 RSVP schema (TDD RED).

Behavior pinned here, over the TW-660 core-loop surface:

- rsvp_opens_at anchors the Section 7.7 standing waves: Keyholder
  opens 72h before the general opening, Pillar 48h, Fixture 24h,
  Regular 12h, Verified at the general opening. Before a member's
  window the RSVP is refused; inside an early window the row records
  rsvp_window = the standing tier; at/after the general opening it
  records 'general'. Events with no rsvp_opens_at behave exactly as
  TW-660 left them (open at publish, 'general').
- Held seats (rsn_event_seat_holds) count against capacity exactly
  once, alongside going RSVPs, in every capacity decision: initial
  RSVP, host approval, waitlist promotion. Expired holds are lazily
  released (status flips to 'expired') and stop counting; released
  and consumed holds never count.
- Couple RSVP prefers the rsn_rsvp_couple RPC (DB atomicity); when
  the RPC is unavailable (e.g. the in-memory client), the TW-660
  service-level both-validated-then-both-written path runs and the
  observable behavior is identical.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.social import models, service
from test_social_core_loop import NOW, FakeDb, add_member, make_event, make_group, rsvp


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def host(db):
    return add_member(db, standing="regular")


@pytest.fixture()
def group(db, host):
    return make_group(db, host)


def _set_opens_at(db, event, opens_at):
    for row in db.rows("rsn_events"):
        if row["id"] == event["id"]:
            row["rsvp_opens_at"] = opens_at
    return event


def _seed_hold(db, event, host_id, *, seats, release_at, status="held"):
    row = {
        "id": str(uuid4()),
        "event_id": event["id"],
        "held_by": host_id,
        "seats": seats,
        "status": status,
        "release_at": release_at,
        "released_at": None if status == "held" else release_at,
        "created_at": NOW,
        "updated_at": NOW,
    }
    db.seed("rsn_event_seat_holds", row)
    return row


# ---------------------------------------------------------------------------
# Standing waves anchored on rsvp_opens_at
# ---------------------------------------------------------------------------


class TestRsvpWaves:
    def test_verified_member_refused_before_general_open(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        member = add_member(db)
        with pytest.raises(service.ConflictError):
            rsvp(db, member, event)

    def test_keyholder_rsvps_in_early_window(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        keyholder = add_member(db, standing="keyholder")
        out = rsvp(db, keyholder, event)
        assert out["status"] == "going"
        assert out["rsvp_window"] == "keyholder"

    def test_regular_window_is_12h_early_not_more(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        regular = add_member(db, standing="regular")
        with pytest.raises(service.ConflictError):
            rsvp(db, regular, event)
        out = service.request_rsvp(
            db,
            regular,
            models.RsvpCreate(event_id=event["id"]),
            now=NOW + timedelta(hours=13),
        )
        assert out["status"] == "going"
        assert out["rsvp_window"] == "regular"

    def test_general_opening_records_general_window(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        member = add_member(db)
        out = service.request_rsvp(
            db,
            member,
            models.RsvpCreate(event_id=event["id"]),
            now=NOW + timedelta(days=1, minutes=1),
        )
        assert out["status"] == "going"
        assert out["rsvp_window"] == "general"

    def test_no_opens_at_behaves_like_tw660(self, db, host, group):
        event = make_event(db, host, group)
        member = add_member(db)
        out = rsvp(db, member, event)
        assert out["status"] == "going"
        assert out["rsvp_window"] == "general"

    def test_event_payload_carries_rsvp_opens_at(self, db, host, group):
        opens = NOW + timedelta(days=1)
        event = make_event(db, host, group)
        out = service.create_event(
            db,
            host,
            models.EventCreate(
                group_id=group["id"],
                title="Wave night",
                description=None,
                starts_at=NOW + timedelta(days=7),
                ends_at=NOW + timedelta(days=7, hours=2),
                min_age_band="age_18_plus",
                rsvp_opens_at=opens,
            ),
            now=NOW,
        )
        assert out["rsvp_opens_at"] is not None
        assert service._parse_ts(out["rsvp_opens_at"]) == opens
        assert event is not None  # fixture event unused beyond setup


# ---------------------------------------------------------------------------
# Held-seat accounting
# ---------------------------------------------------------------------------


class TestHeldSeats:
    def test_active_hold_reduces_available_seats(self, db, host, group):
        event = make_event(db, host, group, capacity=2)
        _seed_hold(db, event, host, seats=1, release_at=NOW + timedelta(hours=6))
        first = add_member(db)
        second = add_member(db)
        assert rsvp(db, first, event)["status"] == "going"
        assert rsvp(db, second, event)["status"] == "waitlist"

    def test_expired_hold_releases_and_stops_counting(self, db, host, group):
        event = make_event(db, host, group, capacity=1)
        hold = _seed_hold(db, event, host, seats=1, release_at=NOW - timedelta(hours=1))
        member = add_member(db)
        assert rsvp(db, member, event)["status"] == "going"
        stored = [r for r in db.rows("rsn_event_seat_holds") if r["id"] == hold["id"]]
        assert stored[0]["status"] == "expired"

    def test_released_hold_never_counts(self, db, host, group):
        event = make_event(db, host, group, capacity=1)
        _seed_hold(
            db,
            event,
            host,
            seats=1,
            release_at=NOW + timedelta(hours=6),
            status="released",
        )
        member = add_member(db)
        assert rsvp(db, member, event)["status"] == "going"

    def test_hold_counts_against_host_approval(self, db, host, group):
        event = make_event(
            db, host, group, capacity=1, venue_type="home", min_tier_rsvp="host_approved"
        )
        _seed_hold(db, event, host, seats=1, release_at=NOW + timedelta(hours=6))
        member = add_member(db)
        requested = rsvp(db, member, event)
        assert requested["status"] == "requested"
        out = service.decide_rsvp(
            db,
            host,
            requested["id"],
            models.RsvpDecision(decision="approve"),
            now=NOW,
        )
        assert out["status"] == "waitlist"


# ---------------------------------------------------------------------------
# Couple RSVP: RPC preferred, service fallback identical
# ---------------------------------------------------------------------------


class _RpcDb(FakeDb):
    """FakeDb with an rsn_rsvp_couple RPC double over the tables."""

    def __init__(self):
        super().__init__()
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        db = self

        class _RpcQuery:
            def execute(self):
                if name != "rsn_rsvp_couple":
                    raise ValueError(name)
                existing = {
                    (r["event_id"], r["user_id"]) for r in db.rows("rsn_rsvps")
                }
                pair = [
                    (params["p_user_id"], params["p_user_status"]),
                    (params["p_partner_id"], params["p_partner_status"]),
                ]
                for user_id, _ in pair:
                    if (params["p_event_id"], user_id) in existing:
                        raise RuntimeError("duplicate key value")
                rows = []
                for user_id, status in pair:
                    rows.append(
                        db.seed(
                            "rsn_rsvps",
                            {
                                "event_id": params["p_event_id"],
                                "user_id": user_id,
                                "status": status,
                                "couple_link_id": params["p_couple_link_id"],
                                "plus_one_of_rsvp_id": params.get(
                                    "p_user_plus_one_of"
                                )
                                if user_id == params["p_user_id"]
                                else None,
                                "rsvp_window": params["p_rsvp_window"],
                                "host_decided_by": None,
                                "host_decided_at": None,
                                "created_at": params["p_now"],
                            },
                        )
                    )

                class _Res:
                    data = rows

                return _Res()

        return _RpcQuery()


def _couple(db, host, group):
    """Two linked partners, both group members, ready to pair-RSVP."""
    a = add_member(db, standing="regular")
    b = add_member(db)
    low, high = sorted([a, b])
    link = db.seed(
        "rsn_couple_links",
        {"user_low_id": low, "user_high_id": high, "status": "active"},
    )
    for user_id in (a, b):
        service.join_group(db, user_id, group["id"], now=NOW)
    return a, b, link


class TestCoupleRpc:
    def test_couple_rsvp_uses_rpc_and_writes_both_rows(self, db, host, group):
        rpc_db = _RpcDb()
        rpc_host = add_member(rpc_db, standing="regular")
        rpc_group = make_group(rpc_db, rpc_host)
        a, b, link = _couple(rpc_db, rpc_host, rpc_group)
        event = make_event(rpc_db, rpc_host, rpc_group)
        out = rsvp(rpc_db, a, event, couple_link_id=link["id"])
        assert [c[0] for c in rpc_db.rpc_calls] == ["rsn_rsvp_couple"]
        rows = rpc_db.rows("rsn_rsvps")
        assert len(rows) == 2
        assert {r["user_id"] for r in rows} == {a, b}
        assert all(r["couple_link_id"] == link["id"] for r in rows)
        assert out["user_id"] == a

    def test_couple_rsvp_falls_back_when_rpc_unavailable(self, db, host, group):
        a, b, link = _couple(db, host, group)
        event = make_event(db, host, group)
        out = rsvp(db, a, event, couple_link_id=link["id"])
        rows = db.rows("rsn_rsvps")
        assert len(rows) == 2
        assert {r["user_id"] for r in rows} == {a, b}
        assert out["user_id"] == a
