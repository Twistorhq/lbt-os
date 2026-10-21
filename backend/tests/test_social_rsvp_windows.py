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
- TW-661 fix round (Rosa M1): a couple RSVP gates on the PARTNER's
  own wave window too, computed from the partner's standing, and
  each row records the wave that admitted THAT member (RPC and
  fallback alike). A pair is refused, zero rows written, while the
  partner's window has not opened, even when the requester's has.
  A failed RPC call logs a warning before the fallback runs (m1).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from test_social_core_loop import NOW, FakeDb, add_member, make_event, make_group, rsvp

from app.social import models, service


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def host(db):
    return add_member(db, standing="regular")


@pytest.fixture()
def group(db, host):
    return make_group(db, host)


def _join(db, group, user_id):
    service.join_group(db, user_id, group["id"], now=NOW)
    return user_id


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
        member = _join(db, group, add_member(db))
        with pytest.raises(service.ConflictError):
            rsvp(db, member, event)

    def test_keyholder_rsvps_in_early_window(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        keyholder = _join(db, group, add_member(db, standing="keyholder"))
        out = rsvp(db, keyholder, event)
        assert out["status"] == "going"
        assert out["rsvp_window"] == "keyholder"

    def test_regular_window_is_12h_early_not_more(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        regular = _join(db, group, add_member(db, standing="regular"))
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
        member = _join(db, group, add_member(db))
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
        member = _join(db, group, add_member(db))
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
        first = _join(db, group, add_member(db))
        second = _join(db, group, add_member(db))
        assert rsvp(db, first, event)["status"] == "going"
        assert rsvp(db, second, event)["status"] == "waitlist"

    def test_expired_hold_releases_and_stops_counting(self, db, host, group):
        event = make_event(db, host, group, capacity=1)
        hold = _seed_hold(db, event, host, seats=1, release_at=NOW - timedelta(hours=1))
        member = _join(db, group, add_member(db))
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
        member = _join(db, group, add_member(db))
        assert rsvp(db, member, event)["status"] == "going"

    def test_hold_counts_against_host_approval(self, db, host, group):
        event = make_event(
            db,
            host,
            group,
            capacity=1,
            venue_type="home",
            min_tier_rsvp="host_approved",
        )
        _seed_hold(db, event, host, seats=1, release_at=NOW + timedelta(hours=6))
        member = _join(db, group, add_member(db))
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
                # Resolve both windows before any write: like the
                # real RPC, the double writes both rows or neither.
                windows = {
                    params["p_user_id"]: params["p_rsvp_window"],
                    params["p_partner_id"]: params["p_partner_rsvp_window"],
                }
                existing = {(r["event_id"], r["user_id"]) for r in db.rows("rsn_rsvps")}
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
                                "plus_one_of_rsvp_id": params.get("p_user_plus_one_of")
                                if user_id == params["p_user_id"]
                                else None,
                                "rsvp_window": windows[user_id],
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


def _linked_couple(db, group, *, requester_standing, partner_standing):
    """A couple link where the two partners hold different standing
    tiers. Returns (requester, partner, link); both joined the group."""
    requester = add_member(db, standing=requester_standing)
    partner = add_member(db, standing=partner_standing)
    low, high = sorted([requester, partner])
    link = db.seed(
        "rsn_couple_links",
        {"user_low_id": low, "user_high_id": high, "status": "active"},
    )
    for user_id in (requester, partner):
        service.join_group(db, user_id, group["id"], now=NOW)
    return requester, partner, link


class TestCoupleWaves:
    """Rosa TW-661 M1: the partner's own wave window gates the pair,
    and each row records the wave that admitted THAT member. Her
    probe: event opens at NOW + 24h, Keyholder requester, Verified
    partner; solo the partner is refused at NOW, yet the couple
    path landed the partner's row as ('going', 'keyholder')."""

    def test_pair_refused_before_partner_window_fallback(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        requester, partner, link = _linked_couple(
            db, group, requester_standing="keyholder", partner_standing="verified"
        )
        # Solo-refusal parity: the partner is refused alone at NOW.
        with pytest.raises(service.ConflictError):
            rsvp(db, partner, event)
        # So the pair is refused too, and writes zero rows.
        with pytest.raises(service.ConflictError):
            rsvp(db, requester, event, couple_link_id=link["id"])
        assert db.rows("rsn_rsvps") == []

    def test_pair_refused_before_partner_window_rpc(self, db, host, group):
        rpc_db = _RpcDb()
        rpc_host = add_member(rpc_db, standing="regular")
        rpc_group = make_group(rpc_db, rpc_host)
        requester, partner, link = _linked_couple(
            rpc_db,
            rpc_group,
            requester_standing="keyholder",
            partner_standing="verified",
        )
        event = _set_opens_at(
            rpc_db, make_event(rpc_db, rpc_host, rpc_group), NOW + timedelta(days=1)
        )
        with pytest.raises(service.ConflictError):
            rsvp(rpc_db, requester, event, couple_link_id=link["id"])
        # Refused before any write attempt: the RPC never fires.
        assert rpc_db.rpc_calls == []
        assert rpc_db.rows("rsn_rsvps") == []

    def test_pair_rows_carry_each_members_own_wave_fallback(self, db, host, group):
        # Opens at NOW + 24h; at NOW + 13h the Keyholder requester
        # is inside the keyholder wave and the Regular partner is
        # inside the regular wave (opened at +12h). Neither label
        # may be borrowed from the other partner.
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        requester, partner, link = _linked_couple(
            db, group, requester_standing="keyholder", partner_standing="regular"
        )
        out = service.request_rsvp(
            db,
            requester,
            models.RsvpCreate(event_id=event["id"], couple_link_id=link["id"]),
            now=NOW + timedelta(hours=13),
        )
        assert out["rsvp_window"] == "keyholder"
        rows = {r["user_id"]: r for r in db.rows("rsn_rsvps")}
        assert rows[requester]["rsvp_window"] == "keyholder"
        assert rows[partner]["rsvp_window"] == "regular"

    def test_pair_rows_carry_each_members_own_wave_rpc(self, db, host, group):
        rpc_db = _RpcDb()
        rpc_host = add_member(rpc_db, standing="regular")
        rpc_group = make_group(rpc_db, rpc_host)
        requester, partner, link = _linked_couple(
            rpc_db,
            rpc_group,
            requester_standing="keyholder",
            partner_standing="regular",
        )
        event = _set_opens_at(
            rpc_db, make_event(rpc_db, rpc_host, rpc_group), NOW + timedelta(days=1)
        )
        out = service.request_rsvp(
            rpc_db,
            requester,
            models.RsvpCreate(event_id=event["id"], couple_link_id=link["id"]),
            now=NOW + timedelta(hours=13),
        )
        assert out["rsvp_window"] == "keyholder"
        assert [c[0] for c in rpc_db.rpc_calls] == ["rsn_rsvp_couple"]
        params = rpc_db.rpc_calls[0][1]
        assert params["p_rsvp_window"] == "keyholder"
        assert params["p_partner_rsvp_window"] == "regular"
        rows = {r["user_id"]: r for r in rpc_db.rows("rsn_rsvps")}
        assert rows[requester]["rsvp_window"] == "keyholder"
        assert rows[partner]["rsvp_window"] == "regular"

    def test_pair_admitted_at_general_opening_labels_general(self, db, host, group):
        # Rosa's probe pair, later: once the general opening has
        # passed, both rows land and both record 'general' (the
        # requester's early wave no longer labels either row).
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        requester, partner, link = _linked_couple(
            db, group, requester_standing="keyholder", partner_standing="verified"
        )
        service.request_rsvp(
            db,
            requester,
            models.RsvpCreate(event_id=event["id"], couple_link_id=link["id"]),
            now=NOW + timedelta(days=1, minutes=1),
        )
        rows = db.rows("rsn_rsvps")
        assert len(rows) == 2
        assert all(r["rsvp_window"] == "general" for r in rows)


class _BrokenRpcDb(FakeDb):
    """FakeDb whose RPC always raises, as an undeployed function does."""

    def rpc(self, name, params):
        class _Query:
            def execute(self):
                raise RuntimeError(f"function {name} does not exist")

        return _Query()


class TestCoupleRpcFailureLogging:
    """Rosa TW-661 m1: a swallowed RPC exception would leave a
    broken or undeployed RPC invisible indefinitely while the
    fallback quietly carries production. The failure must log."""

    def test_rpc_failure_logs_a_warning_and_falls_back(self, caplog):
        broken = _BrokenRpcDb()
        broken_host = add_member(broken, standing="regular")
        broken_group = make_group(broken, broken_host)
        a, b, link = _couple(broken, broken_host, broken_group)
        event = make_event(broken, broken_host, broken_group)
        with caplog.at_level(logging.WARNING, logger="app.social.service"):
            out = rsvp(broken, a, event, couple_link_id=link["id"])
        # The fallback still lands the pair, observably identical.
        assert out["user_id"] == a
        assert len(broken.rows("rsn_rsvps")) == 2
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("rsn_rsvp_couple" in r.getMessage() for r in warnings)
