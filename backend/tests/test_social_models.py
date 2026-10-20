"""TW-650 Twistor Social — fail-loud Pydantic models (TDD RED).

The models mirror the rsn_ schema contract (spec Section 4 + the
TW-648 fix-round deltas). Fail-loud means: unknown fields are refused,
not ignored; mandatory age ratings have no default; and a check-in can
never carry coordinates (spec Section 7.1: verdicts only, raw
coordinates never cross the wire).
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.social import models

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


class TestProfile:
    def test_minimal_profile_ok(self):
        p = models.ProfileIn(display_name="  Dre  ")
        assert p.display_name == "Dre"  # stripped, fail-loud base config
        assert p.locale == "en"
        assert p.show_attendance_default is True

    def test_blank_display_name_refused(self):
        with pytest.raises(ValidationError):
            models.ProfileIn(display_name="   ")

    def test_unknown_locale_refused(self):
        with pytest.raises(ValidationError):
            models.ProfileIn(display_name="Dre", locale="fr")

    def test_extra_field_refused(self):
        with pytest.raises(ValidationError):
            models.ProfileIn(display_name="Dre", date_of_birth="1990-01-01")


class TestGroupCreate:
    def test_min_age_band_is_mandatory(self):
        with pytest.raises(ValidationError):
            models.GroupCreate(
                name="Denver Hikers",
                slug="denver-hikers",
                category="outdoors",
                metro="denver",
            )

    def test_valid_group(self):
        g = models.GroupCreate(
            name="Denver Hikers",
            slug="denver-hikers",
            category="outdoors",
            metro="denver",
            min_age_band="age_18_plus",
        )
        assert g.visibility == "public"
        assert g.meetup_min_tier == "verified"

    def test_bad_slug_refused(self):
        with pytest.raises(ValidationError):
            models.GroupCreate(
                name="X",
                slug="Not A Slug!",
                category="c",
                metro="denver",
                min_age_band="age_21_plus",
            )


class TestEventCreate:
    def _kwargs(self, **over):
        kw = {
            "group_id": uuid4(),
            "title": "Saturday hike",
            "starts_at": NOW,
            "ends_at": NOW + timedelta(hours=3),
            "min_age_band": "age_18_plus",
        }
        kw.update(over)
        return kw

    def test_valid_event_defaults(self):
        e = models.EventCreate(**self._kwargs())
        assert e.venue_type == "venue"
        assert e.audience_scope == "group"
        assert e.guests_enabled is True
        assert e.min_tier_rsvp == "verified"

    def test_end_before_start_refused(self):
        with pytest.raises(ValidationError):
            models.EventCreate(
                **self._kwargs(ends_at=NOW - timedelta(hours=1))
            )

    def test_capacity_must_be_positive(self):
        with pytest.raises(ValidationError):
            models.EventCreate(**self._kwargs(capacity=0))

    def test_home_event_with_close_friends_scope(self):
        e = models.EventCreate(
            **self._kwargs(venue_type="home", audience_scope="close_friends")
        )
        assert e.venue_type == "home"
        assert e.audience_scope == "close_friends"

    def test_unrated_event_refused(self):
        kw = self._kwargs()
        del kw["min_age_band"]
        with pytest.raises(ValidationError):
            models.EventCreate(**kw)


class TestCheckinNoCoordinates:
    def test_verdict_only_ok(self):
        c = models.CheckinCreate(event_id=uuid4(), proof_type="geo_soft")
        assert c.proof_type == "geo_soft"

    def test_coordinates_refused_loudly(self):
        with pytest.raises(ValidationError):
            models.CheckinCreate(
                event_id=uuid4(),
                proof_type="geo_soft",
                latitude=39.7392,
                longitude=-104.9903,
            )

    def test_unknown_proof_type_refused(self):
        with pytest.raises(ValidationError):
            models.CheckinCreate(event_id=uuid4(), proof_type="selfie")


class TestRsvpAndDecision:
    def test_rsvp_create(self):
        r = models.RsvpCreate(event_id=uuid4())
        assert r.couple_link_id is None

    def test_decision_literals(self):
        d = models.RsvpDecision(decision="approve")
        assert d.decision == "approve"
        with pytest.raises(ValidationError):
            models.RsvpDecision(decision="maybe")


class TestWelfareAndReview:
    def test_welfare_defaults_help_action_none(self):
        w = models.WelfareResponseIn(event_id=uuid4(), response="fine")
        assert w.help_action == "none"

    def test_review_rating_bounds(self):
        with pytest.raises(ValidationError):
            models.ReviewCreate(
                event_id=uuid4(), subject_type="person", interaction_rating=9
            )

    def test_encounter_confirm_identity_optional(self):
        ec = models.EncounterConfirmCreate(
            event_id=uuid4(), subject_id=uuid4(), met_them=True
        )
        assert ec.identity_matched is None


class TestReport:
    def test_age_misrating_is_a_category(self):
        r = models.ReportCreate(
            target_type="event",
            target_id=uuid4(),
            category="age_misrating",
            description="Listed 18+ but serving drinks",
        )
        assert r.category == "age_misrating"

    def test_blank_description_refused(self):
        with pytest.raises(ValidationError):
            models.ReportCreate(
                target_type="user",
                target_id=uuid4(),
                category="fraud",
                description="  ",
            )


class TestTrustEvent:
    def test_event_types_match_schema(self):
        ev = models.TrustEventIn(
            user_id=uuid4(), event_type="identity_strike", weight=-25
        )
        assert ev.event_type == "identity_strike"
        with pytest.raises(ValidationError):
            models.TrustEventIn(
                user_id=uuid4(), event_type="good_vibes", weight=1
            )
