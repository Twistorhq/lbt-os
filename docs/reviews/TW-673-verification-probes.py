"""Rosa Delgado independent spot-verification probes for TW-673 fix round.

Written by the reviewer, not the builder. Temporary file: deleted
after the verification run; results live in the verification note.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_social_checkin_loop import (
    END,
    START,
    _checkin,
    _completed_event,
    _confirm,
    _geo_payload,
    _ledger,
)
from test_social_core_loop import FakeDb, add_member, join, make_event, rsvp

from app.social import models, service


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def host(db):
    return add_member(db, standing="regular")


@pytest.fixture()
def group(db, host):
    from test_social_core_loop import make_group

    return make_group(db, host)


@pytest.fixture()
def event(db, host, group):
    return make_event(db, host, group, starts_at=START)


def _pair_reviewed(db, host, group):
    a = add_member(db)
    b = add_member(db)
    join(db, group, a)
    join(db, group, b)
    ev, _ = _completed_event(db, host, group, attendee_ids=(a, b))
    return a, b, ev


def _review(db, a, ev, b, wma):
    return service.create_review(
        db,
        a,
        models.ReviewCreate(
            event_id=ev["id"],
            subject_type="person",
            subject_id=b,
            would_meet_again=wma,
        ),
        now=END + timedelta(hours=1),
    )


def _score(db, b):
    rows = [r for r in db.rows("rsn_trust_scores") if r["user_id"] == b]
    return rows[0]["score"]


# --- M1: edits reconcile in every direction -------------------------------


def test_m1_false_to_true_edit(db, host, group):
    a, b, ev = _pair_reviewed(db, host, group)
    _review(db, a, ev, b, False)
    assert _score(db, b) == 1  # geo 2 + review -1
    _review(db, a, ev, b, True)
    weights = [r["weight"] for r in _ledger(db, b, "review_received")]
    assert weights == [-1, 2]
    assert sum(weights) == 1
    assert _score(db, b) == 3
    rec = service.recompute_member_standing(db, b, now=END + timedelta(hours=2))
    assert rec["score"] == 3
    summary = service.my_review_summary(db, b, now=END + timedelta(hours=2))
    assert summary["reviews_received"] == 1
    assert summary["would_meet_again_rate"] == 1.0


def test_m1_same_value_edit_writes_no_compensation(db, host, group):
    a, b, ev = _pair_reviewed(db, host, group)
    _review(db, a, ev, b, True)
    before = list(_ledger(db, b, "review_received"))
    _review(db, a, ev, b, True)
    after = _ledger(db, b, "review_received")
    assert after == before  # delta 0: no phantom ledger event
    assert _score(db, b) == 3
    summary = service.my_review_summary(db, b, now=END + timedelta(hours=2))
    assert summary["reviews_received"] == 1
    assert summary["would_meet_again_rate"] == 1.0


def test_m1_none_to_true_edit(db, host, group):
    # A weight-0 person review exists only with another structured
    # answer set (an all-empty person review is refused at insert).
    a, b, ev = _pair_reviewed(db, host, group)
    service.create_review(
        db,
        a,
        models.ReviewCreate(
            event_id=ev["id"],
            subject_type="person",
            subject_id=b,
            good_person=True,
            would_meet_again=None,
        ),
        now=END + timedelta(hours=1),
    )
    _review(db, a, ev, b, True)
    weights = [r["weight"] for r in _ledger(db, b, "review_received")]
    assert weights == [0, 1]
    assert _score(db, b) == 3


# --- M2: all four combinations --------------------------------------------


def test_m2_model_refuses_both_incoherent_mirrors(db, host, group, event):
    for matched in (False, True):
        with pytest.raises(Exception):
            models.EncounterConfirmCreate(
                event_id=event["id"],
                subject_id=add_member(db),
                met_them=False,
                identity_matched=matched,
            )


def test_m2_met_true_identity_false_still_strikes(db, host, group, event):
    from test_social_checkin_loop import _attendee

    a = _attendee(db, group, event)
    b = _attendee(db, group, event)
    _checkin(db, a, event)  # confirmer checked in; subject RSVP-only
    out = _confirm(db, a, event, b, met=True, matched=False)
    assert out["identity_review_opened"] is True
    strikes = _ledger(db, b, "identity_strike")
    assert len(strikes) == 1 and strikes[0]["weight"] == -10


def test_m2_didnt_meet_writes_only_no_show(db, host, group, event):
    from test_social_checkin_loop import _attendee

    a = _attendee(db, group, event)
    b = _attendee(db, group, event)
    _checkin(db, a, event)
    _confirm(db, a, event, b, met=False, matched=None)
    assert len(_ledger(db, b, "no_show")) == 1
    assert _ledger(db, b, "identity_strike") == []
    # band untouched by a non-encounter
    rows = [r for r in db.rows("rsn_trust_scores") if r["user_id"] == b]
    assert rows == [] or rows[0]["band"] == "good_standing"


# --- M3: refusal is total, and the weight table cannot weight it ----------


def test_m3_weights_table_has_no_qr_entry():
    assert "qr_strong" not in service.CHECKIN_WEIGHTS
    assert service.CHECKIN_WEIGHTS == {"geo_soft": 2, "host_confirm": 3}


def test_m3_refusal_names_live_paths(db, host, group, event):
    from test_social_checkin_loop import _attendee

    uid = _attendee(db, group, event)
    with pytest.raises(service.ForbiddenError) as exc:
        service.create_checkin(
            db,
            uid,
            models.CheckinCreate(
                event_id=event["id"],
                proof_type="qr_strong",
                qr_token="literally" + "-any-string",
            ),
            now=START + timedelta(minutes=5),
        )
    msg = str(exc.value).lower()
    assert "geo" in msg and "host" in msg
    assert db.rows("rsn_checkins") == []


# --- m1: boundary of the freshness bound -----------------------------------


def test_m1_observed_exactly_at_window_open_is_accepted(db, host, group, event):
    from test_social_checkin_loop import _attendee

    uid = _attendee(db, group, event)
    out = service.create_checkin(
        db,
        uid,
        _geo_payload(event, observed=START - timedelta(minutes=30)),
        now=START + timedelta(minutes=5),
    )
    assert out["proof_type"] == "geo_soft"


def test_m1_observed_one_minute_before_window_is_refused(db, host, group, event):
    from test_social_checkin_loop import _attendee

    uid = _attendee(db, group, event)
    with pytest.raises(service.ConflictError):
        service.create_checkin(
            db,
            uid,
            _geo_payload(event, observed=START - timedelta(minutes=31)),
            now=START + timedelta(minutes=5),
        )
    assert db.rows("rsn_checkins") == []
