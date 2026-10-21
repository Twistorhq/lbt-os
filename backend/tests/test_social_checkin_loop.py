"""TW-673 Twistor Social check-in loop (Phase 5): TDD.

The safety check-in loop (spec Section 7) over the TW-660 core loop:

- Stage 1 geo arrival: the verdict is computed ON-DEVICE; the wire
  carries a verdict + accuracy bucket + timestamp and never a
  coordinate (the CheckinCreate model has no coordinate field, and
  this file proves no coordinate ever lands in a stored row);
- Stage 2 encounter confirms: "Did you meet them?" / "Were they who
  they said they were?" A no on identity is a strike on the subject's
  trust ledger and opens a verification review; a no on met feeds
  the no-show input;
- Stage 3 welfare: prompt + response records are enums and
  timestamps only; need-help, silent alert, and two unanswered
  prompts produce the escalation record shape the safety console
  (Phase 6) will consume;
- Stage 4 write-ups: structured reviews feed the subject's
  reputation; members see their own aggregate only, never a
  reviewer's identity or free text;
- Standing (Section 7.5 + reward catalog): recompute projects the
  ledger + loop records into tier/band/score. Standing accrues only
  from showing up, decays on dormancy (tier only, floor Verified),
  and a pre-existing trust row claiming a tier the records do not
  support is overwritten by the records, never believed.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from test_social_core_loop import (
    NOW,
    FakeDb,
    add_member,
    join,
    make_event,
    make_group,
    rsvp,
)

from app.social import models, service, sessions

START = NOW + timedelta(hours=1)
END = START + timedelta(hours=2)


@pytest.fixture()
def db():
    return FakeDb()


@pytest.fixture()
def host(db):
    return add_member(db, standing="regular")


@pytest.fixture()
def group(db, host):
    return make_group(db, host)


@pytest.fixture()
def event(db, host, group):
    return make_event(db, host, group, starts_at=START)


def _attendee(db, group, event, **kw):
    uid = add_member(db, **kw)
    join(db, group, uid)
    rsvp(db, uid, event)
    return uid


def _geo_payload(event, *, within=True, observed=None):
    return models.CheckinCreate(
        event_id=event["id"],
        proof_type="geo_soft",
        within_geofence=within,
        accuracy_bucket="fine",
        observed_at=observed or (START + timedelta(minutes=5)),
    )


def _checkin(db, uid, event, *, now=None, **kw):
    return service.create_checkin(
        db, uid, _geo_payload(event, **kw), now=now or (START + timedelta(minutes=5))
    )


def _ledger(db, user_id, event_type=None):
    rows = [
        r
        for r in db.rows("rsn_trust_events")
        if r["user_id"] == user_id
        and (event_type is None or r["event_type"] == event_type)
    ]
    return rows


# ---------------------------------------------------------------------------
# Stage 1: geo arrival (verdict only, never coordinates)
# ---------------------------------------------------------------------------


class TestGeoArrival:
    def test_inside_verdict_creates_checkin_and_ledger_event(
        self, db, host, group, event
    ):
        uid = _attendee(db, group, event)
        out = _checkin(db, uid, event)
        assert out["proof_type"] == "geo_soft"
        assert out["user_id"] == uid
        rows = db.rows("rsn_checkins")
        assert len(rows) == 1
        # The stored row is a verdict record: no coordinate-shaped
        # key exists on it, by schema and by this assertion.
        assert not any(
            k in rows[0] for k in ("latitude", "longitude", "lat", "lon", "coords")
        )
        ledger = _ledger(db, uid, "checkin_completed")
        assert len(ledger) == 1
        assert ledger[0]["weight"] == 2  # geo_soft is the weak proof

    def test_arrival_class_marks_late_arrival(self, db, host, group, event):
        uid = _attendee(db, group, event)
        out = _checkin(db, uid, event, now=START + timedelta(minutes=45))
        assert out["arrival_class"] == "late"

    def test_outside_verdict_is_not_an_arrival(self, db, host, group, event):
        uid = _attendee(db, group, event)
        with pytest.raises(service.ForbiddenError):
            _checkin(db, uid, event, within=False)
        assert db.rows("rsn_checkins") == []

    def test_coordinate_payload_cannot_be_constructed(self, event):
        with pytest.raises(Exception):
            models.CheckinCreate(
                event_id=event["id"],
                proof_type="geo_soft",
                within_geofence=True,
                accuracy_bucket="fine",
                observed_at=START,
                latitude=39.7,
                longitude=-105.0,
            )

    def test_unverified_ghost_cannot_check_in(self, db, host, group, event):
        ghost = add_member(db, tier=None, trust_row=False)
        with pytest.raises(service.ForbiddenError):
            _checkin(db, ghost, event)

    def test_member_without_rsvp_cannot_check_in(self, db, host, group, event):
        uid = add_member(db)
        join(db, group, uid)
        with pytest.raises(service.ForbiddenError):
            _checkin(db, uid, event)

    def test_waitlisted_member_cannot_check_in(self, db, host, group):
        small = make_event(db, host, group, starts_at=START, capacity=1)
        _attendee(db, group, small)
        second = add_member(db)
        join(db, group, second)
        row = rsvp(db, second, small)
        assert row["status"] == "waitlist"
        with pytest.raises(service.ForbiddenError):
            service.create_checkin(
                db, second, _geo_payload(small), now=START + timedelta(minutes=5)
            )

    def test_too_early_and_after_end_refused(self, db, host, group, event):
        uid = _attendee(db, group, event)
        with pytest.raises(service.ConflictError):
            _checkin(db, uid, event, now=START - timedelta(minutes=31))
        with pytest.raises(service.ConflictError):
            _checkin(db, uid, event, now=END + timedelta(minutes=1))

    def test_duplicate_checkin_is_idempotent(self, db, host, group, event):
        uid = _attendee(db, group, event)
        first = _checkin(db, uid, event)
        second = _checkin(db, uid, event, now=START + timedelta(minutes=9))
        assert second["id"] == first["id"]
        assert len(db.rows("rsn_checkins")) == 1
        assert len(_ledger(db, uid, "checkin_completed")) == 1

    def test_qr_self_assertion_is_refused_until_the_registry_lands(
        self, db, host, group, event
    ):
        # Rosa TW-673 M3 probe, quoted: "two different members
        # presented the same arbitrary string ('literally-any-string')
        # as a qr_token at the same event; both were recorded as
        # qr_strong check-ins at weight 3, the top proof weight, equal
        # to a host's own door confirmation." A self-typed string is
        # not an issued venue token (Section 7.1 defines the strong
        # proof as the host's venue QR code), so until the venue
        # token registry lands the service refuses qr_strong outright
        # rather than recording an unvalidated assertion as proof.
        a = _attendee(db, group, event)
        b = _attendee(db, group, event)
        for uid in (a, b):
            with pytest.raises(service.ForbiddenError):
                service.create_checkin(
                    db,
                    uid,
                    models.CheckinCreate(
                        event_id=event["id"],
                        proof_type="qr_strong",
                        qr_token="literally-any-string",
                    ),
                    now=START + timedelta(minutes=5),
                )
        assert db.rows("rsn_checkins") == []
        assert _ledger(db, a, "checkin_completed") == []
        assert _ledger(db, b, "checkin_completed") == []

    def test_stale_geo_verdict_observed_before_the_window_is_refused(
        self, db, host, group, event
    ):
        # Rosa TW-673 m1 probe: a geo verdict observed three hours
        # before the arrival window opened was accepted when
        # submitted inside the window. A verdict observed before the
        # window opened is not an arrival verdict; observed_at now
        # carries a freshness bound on both ends.
        uid = _attendee(db, group, event)
        with pytest.raises(service.ConflictError):
            service.create_checkin(
                db,
                uid,
                _geo_payload(event, observed=START - timedelta(hours=3)),
                now=START + timedelta(minutes=5),
            )
        assert db.rows("rsn_checkins") == []

    def test_banned_member_cannot_check_in(self, db, host, group, event):
        uid = _attendee(db, group, event)
        # The ban lands after the RSVP (banned accounts are refused
        # at join time too, so the ban flip is the realistic path).
        for row in db.tables["vfy_identity_anchors"]:
            row["is_banned"] = True
        with pytest.raises(service.ForbiddenError):
            _checkin(db, uid, event)

    def test_under_band_member_sees_no_event_to_check_into(
        self, db, host, group, event
    ):
        teen = add_member(db, band="age_16_17")
        with pytest.raises(service.NotFoundError):
            _checkin(db, teen, event)


class TestHostConfirm:
    def test_host_confirm_via_member_endpoint_refused(self, db, host, group, event):
        uid = _attendee(db, group, event)
        with pytest.raises(service.ForbiddenError):
            service.create_checkin(
                db,
                uid,
                models.CheckinCreate(event_id=event["id"], proof_type="host_confirm"),
                now=START + timedelta(minutes=5),
            )

    def test_host_confirms_attendee(self, db, host, group, event):
        uid = _attendee(db, group, event)
        out = service.host_confirm_checkin(
            db, host, event["id"], uid, now=START + timedelta(minutes=5)
        )
        assert out["proof_type"] == "host_confirm"
        assert out["user_id"] == uid
        assert _ledger(db, uid, "checkin_completed")[0]["weight"] == 3

    def test_non_host_cannot_confirm(self, db, host, group, event):
        uid = _attendee(db, group, event)
        other = _attendee(db, group, event)
        with pytest.raises(service.ForbiddenError):
            service.host_confirm_checkin(
                db, other, event["id"], uid, now=START + timedelta(minutes=5)
            )

    def test_host_confirm_requires_going_rsvp(self, db, host, group, event):
        uid = add_member(db)
        join(db, group, uid)
        with pytest.raises(service.ForbiddenError):
            service.host_confirm_checkin(
                db, host, event["id"], uid, now=START + timedelta(minutes=5)
            )

    def test_restricted_band_member_arrives_by_host_confirm_only(
        self, db, host, group, event
    ):
        # RSVP'd in good standing; the band fell to restricted
        # before the event (a restricted member cannot RSVP at all).
        uid = _attendee(db, group, event)
        for row in db.tables["rsn_trust_scores"]:
            if row["user_id"] == uid:
                row["band"] = "restricted"
        with pytest.raises(service.ForbiddenError):
            _checkin(db, uid, event)
        out = service.host_confirm_checkin(
            db, host, event["id"], uid, now=START + timedelta(minutes=5)
        )
        assert out["proof_type"] == "host_confirm"


# ---------------------------------------------------------------------------
# Stage 2: encounter confirms
# ---------------------------------------------------------------------------


def _confirm(db, confirmer, event, subject, *, met=True, matched=True):
    return service.record_encounter_confirm(
        db,
        confirmer,
        models.EncounterConfirmCreate(
            event_id=event["id"],
            subject_id=subject,
            met_them=met,
            identity_matched=matched,
        ),
        now=START + timedelta(minutes=20),
    )


class TestEncounterConfirms:
    def _pair(self, db, group, event):
        a = _attendee(db, group, event)
        b = _attendee(db, group, event)
        _checkin(db, a, event)
        _checkin(db, b, event)
        return a, b

    def test_positive_confirm_writes_positive_ledger_for_subject(
        self, db, host, group, event
    ):
        a, b = self._pair(db, group, event)
        out = _confirm(db, a, event, b)
        assert out["identity_review_opened"] is False
        ledger = _ledger(db, b, "identity_confirm_positive")
        assert len(ledger) == 1 and ledger[0]["weight"] == 1

    def test_confirmer_must_be_checked_in(self, db, host, group, event):
        a = _attendee(db, group, event)
        b = _attendee(db, group, event)
        _checkin(db, b, event)
        with pytest.raises(service.ForbiddenError):
            _confirm(db, a, event, b)

    def test_self_confirm_refused(self, db, host, group, event):
        a, _ = self._pair(db, group, event)
        with pytest.raises(service.ForbiddenError):
            _confirm(db, a, event, a)

    def test_subject_must_be_on_the_roster(self, db, host, group, event):
        a, _ = self._pair(db, group, event)
        stranger = add_member(db)
        with pytest.raises(service.NotFoundError):
            _confirm(db, a, event, stranger)

    def test_met_no_without_checkin_writes_no_show(self, db, host, group, event):
        a = _attendee(db, group, event)
        b = _attendee(db, group, event)
        _checkin(db, a, event)
        out = _confirm(db, a, event, b, met=False, matched=None)
        assert out["met_them"] is False
        assert len(_ledger(db, b, "no_show")) == 1

    def test_not_met_confirm_cannot_carry_an_identity_answer(
        self, db, host, group, event
    ):
        # Rosa TW-673 M2 probe, quoted: "met_them=false +
        # identity_matched=false against an RSVP'd subject with no
        # check-in wrote BOTH the no-show (-3) and the identity_strike
        # (-10), and moved the subject's band, from a confirmer who
        # by their own answer never encountered the person." Section
        # 7.2 is a sequence: the identity question is asked about
        # someone you met. The model now refuses the incoherent pair
        # (and its mirror, met=false + identity=true) at the
        # boundary, so the double-fire is unconstructable.
        for matched in (False, True):
            with pytest.raises(Exception):
                models.EncounterConfirmCreate(
                    event_id=event["id"],
                    subject_id=uuid4(),
                    met_them=False,
                    identity_matched=matched,
                )

    def test_not_met_confirm_writes_no_show_and_never_a_strike(
        self, db, host, group, event
    ):
        # The service-level half of the M2 probe: a didn't-meet
        # confirm writes only the no-show input. The identity strike
        # (the heaviest single negative in the trust model) never
        # fires on a non-encounter.
        a = _attendee(db, group, event)
        b = _attendee(db, group, event)
        _checkin(db, a, event)
        _confirm(db, a, event, b, met=False, matched=None)
        assert len(_ledger(db, b, "no_show")) == 1
        assert _ledger(db, b, "identity_strike") == []
        assert _ledger(db, b, "identity_confirm_positive") == []

    def test_identity_no_is_a_strike_and_opens_review(self, db, host, group, event):
        a, b = self._pair(db, group, event)
        out = _confirm(db, a, event, b, matched=False)
        assert out["identity_review_opened"] is True
        strikes = _ledger(db, b, "identity_strike")
        assert len(strikes) == 1 and strikes[0]["weight"] == -10
        # One strike moves the subject's band to watch on recompute.
        scores = [r for r in db.rows("rsn_trust_scores") if r["user_id"] == b]
        assert scores[0]["band"] == "watch"

    def test_two_strikes_restrict_the_band(self, db, host, group, event):
        a, b = self._pair(db, group, event)
        c = _attendee(db, group, event)
        _checkin(db, c, event)
        _confirm(db, a, event, b, matched=False)
        _confirm(db, c, event, b, matched=False)
        scores = [r for r in db.rows("rsn_trust_scores") if r["user_id"] == b]
        assert scores[0]["band"] == "restricted"

    def test_duplicate_confirm_idempotent_then_conflict(self, db, host, group, event):
        a, b = self._pair(db, group, event)
        first = _confirm(db, a, event, b)
        again = _confirm(db, a, event, b)
        assert again["id"] == first["id"]
        assert len(_ledger(db, b, "identity_confirm_positive")) == 1
        with pytest.raises(service.ConflictError):
            _confirm(db, a, event, b, matched=False)


# ---------------------------------------------------------------------------
# Stage 3: welfare checks
# ---------------------------------------------------------------------------


class TestWelfare:
    def _checked_in(self, db, group, event):
        uid = _attendee(db, group, event)
        _checkin(db, uid, event)
        return uid

    def test_prompt_then_fine_records_enums_only(self, db, host, group, event):
        uid = self._checked_in(db, group, event)
        service.prompt_welfare_check(
            db, event["id"], uid, now=START + timedelta(minutes=60)
        )
        out = service.respond_welfare_check(
            db,
            uid,
            models.WelfareResponseIn(event_id=event["id"], response="fine"),
            now=START + timedelta(minutes=61),
        )
        assert out["response"] == "fine"
        assert out["escalation"] is None
        row = db.rows("rsn_welfare_checks")[0]
        assert row["responded_at"] is not None
        assert not any(k in row for k in ("latitude", "longitude", "free_text"))

    def test_welfare_requires_checkin(self, db, host, group, event):
        uid = _attendee(db, group, event)
        with pytest.raises(service.ForbiddenError):
            service.respond_welfare_check(
                db,
                uid,
                models.WelfareResponseIn(event_id=event["id"], response="fine"),
                now=START + timedelta(minutes=61),
            )

    def test_need_help_surfaces_escalation_with_help_options(
        self, db, host, group, event
    ):
        uid = self._checked_in(db, group, event)
        out = service.respond_welfare_check(
            db,
            uid,
            models.WelfareResponseIn(
                event_id=event["id"],
                response="need_help",
                help_action="trusted_contact_notified",
            ),
            now=START + timedelta(minutes=61),
        )
        esc = out["escalation"]
        assert esc is not None and esc["reason"] == "need_help"
        assert "notify_trusted_contact" in esc["help_options"]
        assert "emergency_services" in esc["help_options"]
        # The system surfaces help; it never claims to dispatch rescue.
        assert esc["dispatches_rescue"] is False

    def test_silent_alert_requests_evidence_freeze(self, db, host, group, event):
        uid = self._checked_in(db, group, event)
        out = service.respond_welfare_check(
            db,
            uid,
            models.WelfareResponseIn(
                event_id=event["id"], response="fine", help_action="silent_alert"
            ),
            now=START + timedelta(minutes=61),
        )
        esc = out["escalation"]
        assert esc is not None and esc["reason"] == "silent_alert"
        assert esc["evidence_packet_freeze_requested"] is True

    def test_two_unanswered_prompts_escalate(self, db, host, group, event):
        uid = self._checked_in(db, group, event)
        service.prompt_welfare_check(
            db, event["id"], uid, now=START + timedelta(minutes=60)
        )
        service.prompt_welfare_check(
            db, event["id"], uid, now=START + timedelta(minutes=75)
        )
        esc = service.welfare_escalation(db, event["id"], uid, now=END)
        assert esc is not None and esc["reason"] == "no_response"
        assert "notify_trusted_contact" in esc["help_options"]


# ---------------------------------------------------------------------------
# Stage 4 + completion: write-ups feed reputation; nights close out
# ---------------------------------------------------------------------------


def _completed_event(db, host, group, *, attendee_ids=()):
    """An event that ran: attendees checked in, host completed it."""
    ev = make_event(db, host, group, starts_at=START)
    checked = []
    for uid in attendee_ids:
        rsvp(db, uid, ev)
        _checkin(db, uid, ev)
        checked.append(uid)
    service.complete_event(db, host, ev["id"], now=END + timedelta(minutes=30))
    return ev, checked


class TestCompletionAndReviews:
    def test_complete_event_marks_no_shows(self, db, host, group):
        ghost_rsvp = add_member(db)
        join(db, group, ghost_rsvp)
        ev = make_event(db, host, group, starts_at=START)
        rsvp(db, ghost_rsvp, ev)
        out = service.complete_event(
            db, host, ev["id"], now=END + timedelta(minutes=30)
        )
        assert out["status"] == "completed"
        rows = [r for r in db.rows("rsn_rsvps") if r["user_id"] == ghost_rsvp]
        assert rows[0]["status"] == "no_show"
        assert len(_ledger(db, ghost_rsvp, "no_show")) == 1

    def test_only_host_completes(self, db, host, group, event):
        other = _attendee(db, group, event)
        with pytest.raises(service.ForbiddenError):
            service.complete_event(db, other, event["id"], now=END)

    def test_review_requires_completed_event(self, db, host, group, event):
        a = _attendee(db, group, event)
        b = _attendee(db, group, event)
        _checkin(db, a, event)
        _checkin(db, b, event)
        with pytest.raises(service.ConflictError):
            service.create_review(
                db,
                a,
                models.ReviewCreate(
                    event_id=event["id"],
                    subject_type="person",
                    subject_id=b,
                    would_meet_again=True,
                ),
                now=END + timedelta(minutes=40),
            )

    def test_person_review_feeds_subject_reputation(self, db, host, group):
        a = add_member(db)
        b = add_member(db)
        join(db, group, a)
        join(db, group, b)
        ev, _ = _completed_event(db, host, group, attendee_ids=(a, b))
        out = service.create_review(
            db,
            a,
            models.ReviewCreate(
                event_id=ev["id"],
                subject_type="person",
                subject_id=b,
                interaction_rating=5,
                good_person=True,
                would_meet_again=True,
            ),
            now=END + timedelta(hours=1),
        )
        assert out["would_meet_again"] is True
        assert len(_ledger(db, b, "review_received")) == 1
        summary = service.my_review_summary(db, b, now=END + timedelta(hours=2))
        assert summary["reviews_received"] == 1
        assert summary["would_meet_again_rate"] == 1.0
        assert summary["good_person_rate"] == 1.0
        # The subject's aggregate never carries reviewer identity or text.
        assert "reviewer" not in str(summary).lower()
        assert "free_text" not in summary

    def test_latest_review_for_a_triple_counts_once(self, db, host, group):
        a = add_member(db)
        b = add_member(db)
        join(db, group, a)
        join(db, group, b)
        ev, _ = _completed_event(db, host, group, attendee_ids=(a, b))
        for wma in (True, False):
            service.create_review(
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
        rows = [
            r
            for r in db.rows("rsn_reviews")
            if r["reviewer_id"] == a and r["subject_id"] == b
        ]
        assert len(rows) == 1
        summary = service.my_review_summary(db, b, now=END + timedelta(hours=2))
        assert summary["reviews_received"] == 1
        assert summary["would_meet_again_rate"] == 0.0
        # The ledger asserts too, not just rows and summary: this is
        # the assertion whose absence let TW-673 M1 through (the edit
        # was invisible to the ledger, so the score kept the first
        # submission). The True submission (+1) must be corrected by
        # a compensating event (-2) when the edit lands on False.
        weights = [r["weight"] for r in _ledger(db, b, "review_received")]
        assert weights == [1, -2]

    def test_review_edit_reconciles_ledger_score_and_summary(self, db, host, group):
        # Rosa TW-673 M1 probe, quoted: "reviewer files
        # would_meet_again=True (subject ledger gains review_received
        # +1, stored score 3), then edits the same triple to False.
        # The row and the summary now say the latest answer is no
        # (rate 0.0), but the ledger still holds only the original
        # +1, the stored score still counts it, and an explicit
        # recompute still returns score 3 against a would-meet-again
        # rate of 0.0." After the fix the three projections of the
        # same fact agree: ledger sum, stored score, recompute, and
        # summary rate.
        a = add_member(db)
        b = add_member(db)
        join(db, group, a)
        join(db, group, b)
        ev, _ = _completed_event(db, host, group, attendee_ids=(a, b))

        def _review(wma):
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

        def _stored_score():
            rows = [r for r in db.rows("rsn_trust_scores") if r["user_id"] == b]
            return rows[0]["score"]

        _review(True)
        assert _stored_score() == 3  # geo check-in 2 + review_received 1
        _review(False)
        ledger = _ledger(db, b)
        assert sum(r["weight"] for r in ledger) == 1
        assert _stored_score() == 1
        recomputed = service.recompute_member_standing(
            db, b, now=END + timedelta(hours=2)
        )
        assert recomputed["score"] == 1
        summary = service.my_review_summary(db, b, now=END + timedelta(hours=2))
        assert summary["reviews_received"] == 1
        assert summary["would_meet_again_rate"] == 0.0

    def test_person_review_of_rsvp_only_subject_refused(self, db, host, group):
        # Rosa TW-673 m2: Section 7.5's would-meet-again rate is
        # defined over reviews "where reviewer and subject both
        # completed the session." A subject who only RSVP'd (and so
        # no-showed at completion) is not reviewable as a person.
        a = add_member(db)
        b = add_member(db)
        join(db, group, a)
        join(db, group, b)
        ev = make_event(db, host, group, starts_at=START)
        rsvp(db, a, ev)
        rsvp(db, b, ev)
        _checkin(db, a, ev)
        service.complete_event(db, host, ev["id"], now=END + timedelta(minutes=30))
        with pytest.raises(service.NotFoundError):
            service.create_review(
                db,
                a,
                models.ReviewCreate(
                    event_id=ev["id"],
                    subject_type="person",
                    subject_id=b,
                    would_meet_again=True,
                ),
                now=END + timedelta(hours=1),
            )
        assert db.rows("rsn_reviews") == []
        assert _ledger(db, b, "review_received") == []

    def test_review_requires_reviewer_checkin(self, db, host, group):
        a = add_member(db)
        b = add_member(db)
        join(db, group, a)
        join(db, group, b)
        ev = make_event(db, host, group, starts_at=START)
        rsvp(db, a, ev)
        rsvp(db, b, ev)
        _checkin(db, b, ev)
        service.complete_event(db, host, ev["id"], now=END + timedelta(minutes=30))
        with pytest.raises(service.ForbiddenError):
            service.create_review(
                db,
                a,
                models.ReviewCreate(
                    event_id=ev["id"],
                    subject_type="person",
                    subject_id=b,
                    would_meet_again=True,
                ),
                now=END + timedelta(hours=1),
            )

    def test_self_review_refused(self, db, host, group):
        a = add_member(db)
        join(db, group, a)
        ev, _ = _completed_event(db, host, group, attendee_ids=(a,))
        with pytest.raises(service.ForbiddenError):
            service.create_review(
                db,
                a,
                models.ReviewCreate(
                    event_id=ev["id"],
                    subject_type="person",
                    subject_id=a,
                    would_meet_again=True,
                ),
                now=END + timedelta(hours=1),
            )

    def test_event_review_records_join_again(self, db, host, group):
        a = add_member(db)
        join(db, group, a)
        ev, _ = _completed_event(db, host, group, attendee_ids=(a,))
        out = service.create_review(
            db,
            a,
            models.ReviewCreate(
                event_id=ev["id"], subject_type="event", would_join_again=True
            ),
            now=END + timedelta(hours=1),
        )
        assert out["would_join_again"] is True
        assert out["subject_id"] is None


# ---------------------------------------------------------------------------
# Standing recompute: earned only by showing up (Section 7.5)
# ---------------------------------------------------------------------------


def _seed_night(db, member, *, when, proof="host_confirm", series_id=None, host=None):
    ev = db.seed(
        "rsn_events",
        {
            "group_id": str(uuid4()),
            "host_user_id": host or str(uuid4()),
            "title": "Seeded night",
            "starts_at": when,
            "ends_at": when + timedelta(hours=2),
            "status": "completed",
            "series_id": series_id,
            "min_age_band": "age_18_plus",
            "venue_type": "venue",
            "audience_scope": "group",
        },
    )
    db.seed(
        "rsn_rsvps",
        {"event_id": ev["id"], "user_id": member, "status": "going"},
    )
    db.seed(
        "rsn_checkins",
        {
            "event_id": ev["id"],
            "user_id": member,
            "proof_type": proof,
            "arrived_at": when + timedelta(minutes=5),
        },
    )
    return ev


class TestStandingRecompute:
    def test_records_overwrite_a_claimed_tier(self, db):
        uid = add_member(db, standing="keyholder")
        out = service.recompute_member_standing(db, uid, now=NOW)
        assert out["standing_tier"] == "verified"
        assert out["completed_nights"] == 0

    def test_five_corroborated_nights_earn_regular(self, db):
        uid = add_member(db)
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=30 - i))
        out = service.recompute_member_standing(db, uid, now=NOW)
        assert out["completed_nights"] == 5
        assert out["show_rate"] == 1.0
        assert out["standing_tier"] == "regular"

    def test_uncorroborated_checkin_is_not_a_night(self, db):
        uid = add_member(db)
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=30 - i), proof="geo_soft")
        out = service.recompute_member_standing(db, uid, now=NOW)
        assert out["completed_nights"] == 0
        assert out["standing_tier"] == "verified"

    def test_encounter_confirm_corroborates_a_geo_night(self, db):
        uid = add_member(db)
        other = add_member(db)
        for i in range(5):
            ev = _seed_night(
                db, uid, when=NOW - timedelta(days=30 - i), proof="geo_soft"
            )
            db.seed(
                "rsn_encounter_confirms",
                {
                    "event_id": ev["id"],
                    "confirmer_id": other,
                    "subject_id": uid,
                    "met_them": True,
                    "identity_matched": True,
                    "created_at": NOW - timedelta(days=29 - i),
                },
            )
        out = service.recompute_member_standing(db, uid, now=NOW)
        assert out["completed_nights"] == 5

    def test_no_shows_drag_the_show_rate(self, db):
        uid = add_member(db)
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=30 - i))
        for i in range(2):
            ev = db.seed(
                "rsn_events",
                {
                    "group_id": str(uuid4()),
                    "host_user_id": str(uuid4()),
                    "title": "Missed",
                    "starts_at": NOW - timedelta(days=10 - i),
                    "ends_at": NOW - timedelta(days=10 - i) + timedelta(hours=2),
                    "status": "completed",
                    "series_id": None,
                    "min_age_band": "age_18_plus",
                    "venue_type": "venue",
                    "audience_scope": "group",
                },
            )
            db.seed(
                "rsn_rsvps",
                {"event_id": ev["id"], "user_id": uid, "status": "no_show"},
            )
        out = service.recompute_member_standing(db, uid, now=NOW)
        assert out["completed_nights"] == 5
        assert out["no_shows"] == 2
        assert out["show_rate"] == pytest.approx(5 / 7)
        # 71% show rate is below the Regular bar of 80%.
        assert out["standing_tier"] == "verified"

    def test_dormancy_steps_tier_down_but_never_verification(self, db):
        uid = add_member(db)
        member = uid
        for i in range(20):
            _seed_night(db, member, when=NOW - timedelta(days=210 - i))
        for v in range(3):
            db.seed(
                "rsn_vouches",
                {
                    "voucher_id": str(uuid4()),
                    "vouchee_id": member,
                    "event_id": str(uuid4()),
                    "created_at": NOW - timedelta(days=200),
                },
            )
        out = service.recompute_member_standing(db, member, now=NOW)
        # Earned Fixture (20 nights, 100% show, 3 vouches); last
        # signal ~190 days ago: 90-day grace, then one step per 60
        # days -> (190-90)//60 = 1 step down to Regular.
        assert out["earned_tier"] == "fixture"
        assert out["standing_tier"] == "regular"
        ctx = service.get_member_context(db, member)
        assert ctx.verified is True and ctx.tier == 2

    def test_score_is_the_ledger_sum(self, db):
        uid = add_member(db)
        db.seed(
            "rsn_trust_events",
            {
                "user_id": uid,
                "event_type": "checkin_completed",
                "weight": 3,
                "ref_type": "event",
                "ref_id": str(uuid4()),
                "created_at": NOW,
            },
        )
        db.seed(
            "rsn_trust_events",
            {
                "user_id": uid,
                "event_type": "identity_confirm_positive",
                "weight": 1,
                "ref_type": "event",
                "ref_id": str(uuid4()),
                "created_at": NOW,
            },
        )
        out = service.recompute_member_standing(db, uid, now=NOW)
        assert out["score"] == 4


# ---------------------------------------------------------------------------
# Scheduler tick: welfare prompts at the midpoint (Section 7.6)
# ---------------------------------------------------------------------------


class TestTick:
    def test_tick_prompts_welfare_at_midpoint_once(self, db, host, group, event):
        uid = _attendee(db, group, event)
        _checkin(db, uid, event)
        mid = START + timedelta(minutes=60)
        out = sessions.run_tick(db, now=mid)
        assert out["welfare_prompts_created"] == 1
        again = sessions.run_tick(db, now=mid + timedelta(minutes=5))
        assert again["welfare_prompts_created"] == 0
        rows = db.rows("rsn_welfare_checks")
        assert len(rows) == 1 and rows[0]["response"] is None

    def test_tick_before_midpoint_prompts_nothing(self, db, host, group, event):
        uid = _attendee(db, group, event)
        _checkin(db, uid, event)
        out = sessions.run_tick(db, now=START + timedelta(minutes=10))
        assert out["welfare_prompts_created"] == 0
