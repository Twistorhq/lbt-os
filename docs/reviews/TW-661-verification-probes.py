"""Rosa TW-661 re-verification probes (temporary; removed after review).

Independent re-runs of the M1 probe plus variations Dre's tests do
not pin: reversed gate direction, mid-wave refusal, and the m1
non-2-row warning branch.
"""

from __future__ import annotations

import logging
from datetime import timedelta

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


def _set_opens_at(db, event, opens_at):
    for row in db.rows("rsn_events"):
        if row["id"] == event["id"]:
            row["rsvp_opens_at"] = opens_at
    return event


def _linked(db, group, *, requester_standing, partner_standing):
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


def _pair(db, requester, event, link, *, now):
    return service.request_rsvp(
        db,
        requester,
        models.RsvpCreate(event_id=event["id"], couple_link_id=link["id"]),
        now=now,
    )


class _OneRowRpcDb(FakeDb):
    """RPC double that returns a single row without writing
    (malformed result; isolates the non-2-row warning branch)."""

    def rpc(self, name, params):
        class _Query:
            def execute(self):
                row = {
                    "event_id": params["p_event_id"],
                    "user_id": params["p_user_id"],
                    "status": params["p_user_status"],
                    "rsvp_window": params["p_rsvp_window"],
                }

                class _Res:
                    data = [row]

                return _Res()

        return _Query()


class TestRosaM1Probes:
    def test_round1_probe_fallback(self, db, host, group):
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        requester, partner, link = _linked(
            db, group, requester_standing="keyholder", partner_standing="verified"
        )
        with pytest.raises(service.ConflictError):
            rsvp(db, partner, event)  # solo parity
        with pytest.raises(service.ConflictError):
            _pair(db, requester, event, link, now=NOW)
        assert db.rows("rsn_rsvps") == []

    def test_reverse_gate_requester_window_closed(self, db, host, group):
        # Partner's window is open (Keyholder) but the REQUESTER is
        # Verified and the general opening is 24h out: the pair must
        # still refuse with zero rows.
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        requester, partner, link = _linked(
            db, group, requester_standing="verified", partner_standing="keyholder"
        )
        with pytest.raises(service.ConflictError):
            _pair(db, requester, event, link, now=NOW)
        assert db.rows("rsn_rsvps") == []

    def test_midwave_refusal_then_general_labels(self, db, host, group):
        # Regular requester (window opened +12h), Verified partner
        # (opens +24h). At +13h the pair refuses; partner solo at
        # +13h also refuses. At +24h both land, both 'general'.
        event = _set_opens_at(db, make_event(db, host, group), NOW + timedelta(days=1))
        requester, partner, link = _linked(
            db, group, requester_standing="regular", partner_standing="verified"
        )
        with pytest.raises(service.ConflictError):
            service.request_rsvp(
                db,
                partner,
                models.RsvpCreate(event_id=event["id"]),
                now=NOW + timedelta(hours=13),
            )
        with pytest.raises(service.ConflictError):
            _pair(db, requester, event, link, now=NOW + timedelta(hours=13))
        assert db.rows("rsn_rsvps") == []
        _pair(db, requester, event, link, now=NOW + timedelta(hours=24))
        rows = db.rows("rsn_rsvps")
        assert len(rows) == 2
        assert all(r["rsvp_window"] == "general" for r in rows)


class TestRosaM1Logging:
    def test_non_two_row_result_logs_and_falls_back(self, caplog):
        db = _OneRowRpcDb()
        h = add_member(db, standing="regular")
        g = make_group(db, h)
        requester, partner, link = _linked(
            db, g, requester_standing="regular", partner_standing="verified"
        )
        event = make_event(db, h, g)
        with caplog.at_level(logging.WARNING, logger="app.social.service"):
            out = rsvp(db, requester, event, couple_link_id=link["id"])
        assert out["user_id"] == requester
        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("expected 2" in m for m in msgs), msgs
        # The double wrote nothing, so the fallback pair is all that
        # lands: exactly one row per partner.
        rows = db.rows("rsn_rsvps")
        assert len(rows) == 2
        assert {r["user_id"] for r in rows} == {requester, partner}
