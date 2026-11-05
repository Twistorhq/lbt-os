"""TW-941 Twistor Social rewards loop (catalog ladder): TDD.

The tier ladder from twistor-social-reward-catalog.md (Regular,
Fixture, Pillar, Keyholder) computed from the existing check-in /
standing machinery (completed nights, show rate, hosted rooms,
would-meet-again, streaks, vouches, tenure, strikes), exposed as
progress toward the next rung for the profile display.

Rules pinned here:

- thresholds are the catalog's planning values, the same numbers
  trust.py enforces (one source of truth, never a second ladder);
- the Verified rung is a future-phase rung (verification phase is
  parked): it is returned as future_phase and never as earned,
  whatever the record shows;
- a member in watch or restricted shows no standing tier until
  their standing restores (catalog Section 1): the read API masks
  the display tier while suspended, and the earned tier is still
  reported privately to the member;
- show rate displays only after 5 or more decided RSVPs (catalog
  Section 5 honesty rule).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from test_social_core_loop import NOW, FakeDb, add_member

from app.social import rewards, service, trust


@pytest.fixture()
def db():
    return FakeDb()


def _seed_night(db, member, *, when, host=None):
    ev = db.seed(
        "rsn_events",
        {
            "group_id": str(uuid4()),
            "host_user_id": host or str(uuid4()),
            "title": "Seeded night",
            "starts_at": when,
            "ends_at": when + timedelta(hours=2),
            "status": "completed",
            "series_id": None,
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
            "proof_type": "host_confirm",
            "arrived_at": when + timedelta(minutes=5),
        },
    )
    return ev


def _seed_hosted_clean(db, host_id, *, when):
    ev = db.seed(
        "rsn_events",
        {
            "group_id": str(uuid4()),
            "host_user_id": host_id,
            "title": "Hosted room",
            "starts_at": when,
            "ends_at": when + timedelta(hours=2),
            "status": "completed",
            "series_id": None,
            "min_age_band": "age_18_plus",
            "venue_type": "venue",
            "audience_scope": "group",
        },
    )
    guest = add_member(db)
    db.seed(
        "rsn_checkins",
        {
            "event_id": ev["id"],
            "user_id": guest,
            "proof_type": "host_confirm",
            "arrived_at": when + timedelta(minutes=5),
        },
    )
    return ev


def _seed_review(db, subject, *, would_meet_again=True):
    return db.seed(
        "rsn_reviews",
        {
            "event_id": str(uuid4()),
            "reviewer_id": str(uuid4()),
            "subject_id": subject,
            "subject_type": "person",
            "would_meet_again": would_meet_again,
            "created_at": NOW,
        },
    )


def _req(progress, tier, key):
    rung = next(r for r in progress["ladder"] if r["tier"] == tier)
    return next(r for r in rung["requirements"] if r["key"] == key)


# ---------------------------------------------------------------------------
# Pure progress projection (trust.MemberStats in, ladder out)
# ---------------------------------------------------------------------------


class TestPureProgress:
    def test_zero_record_is_base_with_regular_next(self):
        out = rewards.progress(trust.MemberStats())
        assert out["tier"] == "verified"
        assert out["next_tier"] == "regular"
        nights = _req(out, "regular", "completed_nights")
        assert nights == {
            "key": "completed_nights",
            "current": 0,
            "required": trust.REGULAR_NIGHTS,
            "met": False,
            "remaining": trust.REGULAR_NIGHTS,
        }

    def test_verified_rung_is_future_phase_never_earned(self):
        stats = trust.MemberStats(
            completed_nights=80,
            reviews_received=30,
            would_meet_again_positive=30,
            hosted_clean=15,
            best_series_streak=8,
            tenure_days=200,
        )
        out = rewards.progress(stats)
        verified = next(r for r in out["ladder"] if r["tier"] == "verified")
        assert verified["phase"] == "future_verification"
        assert verified["earned"] is False
        assert verified["is_current"] is False
        # The activity ladder still tops out honestly at Keyholder.
        assert out["tier"] == "keyholder"
        assert out["next_tier"] is None

    def test_regular_thresholds(self):
        stats = trust.MemberStats(completed_nights=4, no_shows=1)
        out = rewards.progress(stats)
        assert out["tier"] == "verified"
        rate = _req(out, "regular", "show_rate")
        assert rate["current"] == 0.8 and rate["met"] is True
        assert _req(out, "regular", "completed_nights")["met"] is False
        stats = trust.MemberStats(completed_nights=5, no_shows=1)
        out = rewards.progress(stats)
        # 5 of 6 decided = 0.833 show rate: Regular earned.
        assert out["tier"] == "regular"
        assert out["next_tier"] == "fixture"

    def test_fixture_corroboration_either_or(self):
        base = trust.MemberStats(completed_nights=20)
        out = rewards.progress(base)
        assert out["tier"] == "regular"
        streak = _req(out, "fixture", "best_series_streak")
        vouches = _req(out, "fixture", "distinct_vouches")
        assert streak["met"] is False and vouches["met"] is False
        by_streak = rewards.progress(
            trust.MemberStats(completed_nights=20, best_series_streak=4)
        )
        assert by_streak["tier"] == "fixture"
        by_vouch = rewards.progress(
            trust.MemberStats(completed_nights=20, distinct_vouches=3)
        )
        assert by_vouch["tier"] == "fixture"

    def test_pillar_needs_reviews_and_hosting(self):
        stats = trust.MemberStats(
            completed_nights=40,
            best_series_streak=4,
            reviews_received=15,
            would_meet_again_positive=14,
            hosted_clean=5,
        )
        out = rewards.progress(stats)
        assert out["tier"] == "fixture"
        hosted = _req(out, "pillar", "hosted_clean")
        assert hosted["current"] == 5 and hosted["remaining"] == 1
        stats = trust.MemberStats(
            completed_nights=40,
            best_series_streak=4,
            reviews_received=15,
            would_meet_again_positive=14,
            hosted_clean=6,
        )
        assert rewards.progress(stats)["tier"] == "pillar"

    def test_watch_band_suspends_display_tier(self):
        stats = trust.MemberStats(completed_nights=5, identity_strikes=1)
        out = rewards.progress(stats)
        assert out["earned_tier"] == "regular"
        assert out["suspended"] is True
        assert out["tier"] is None


# ---------------------------------------------------------------------------
# Service read API over the loop records
# ---------------------------------------------------------------------------


class TestRewardsService:
    def test_new_member_progress(self, db):
        uid = add_member(db)
        out = service.get_my_rewards(db, uid, now=NOW)
        assert out["user_id"] == uid
        assert out["tier"] == "verified"
        assert out["next_tier"] == "regular"
        assert out["counts"]["completed_nights"] == 0
        assert out["show_rate"] is None
        assert out["show_rate_display"] is False
        ladder = {r["tier"]: r for r in out["ladder"]}
        assert ladder["verified"]["phase"] == "future_verification"
        assert ladder["verified"]["earned"] is False
        assert ladder["regular"]["earned"] is False

    def test_five_nights_earn_regular_with_fixture_next(self, db):
        uid = add_member(db)
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=10 - i))
        out = service.get_my_rewards(db, uid, now=NOW)
        assert out["tier"] == "regular"
        assert out["earned_tier"] == "regular"
        assert out["counts"]["completed_nights"] == 5
        assert out["show_rate"] == 1.0
        assert out["show_rate_display"] is True
        assert out["next_tier"] == "fixture"
        nights = next(
            r for r in out["next_requirements"] if r["key"] == "completed_nights"
        )
        assert nights["current"] == 5 and nights["required"] == 20
        assert nights["remaining"] == 15

    def test_twenty_nights_and_vouches_earn_fixture(self, db):
        uid = add_member(db)
        for i in range(20):
            _seed_night(db, uid, when=NOW - timedelta(days=25 - i))
        for _ in range(3):
            db.seed(
                "rsn_vouches",
                {
                    "voucher_id": str(uuid4()),
                    "vouchee_id": uid,
                    "event_id": str(uuid4()),
                },
            )
        out = service.get_my_rewards(db, uid, now=NOW)
        assert out["tier"] == "fixture"
        assert out["next_tier"] == "pillar"

    def test_pillar_from_full_record(self, db):
        uid = add_member(db)
        for i in range(40):
            _seed_night(db, uid, when=NOW - timedelta(days=45 - (i % 40)))
        for _ in range(3):
            db.seed(
                "rsn_vouches",
                {
                    "voucher_id": str(uuid4()),
                    "vouchee_id": uid,
                    "event_id": str(uuid4()),
                },
            )
        for _ in range(15):
            _seed_review(db, uid)
        for i in range(6):
            _seed_hosted_clean(db, uid, when=NOW - timedelta(days=50 - i))
        out = service.get_my_rewards(db, uid, now=NOW)
        assert out["counts"]["completed_nights"] == 40
        assert out["counts"]["hosted_clean"] == 6
        assert out["would_meet_again_rate"] == 1.0
        assert out["tier"] == "pillar"
        assert out["next_tier"] == "keyholder"

    def test_strike_suspends_display_but_keeps_private_progress(self, db):
        uid = add_member(db)
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=10 - i))
        db.seed(
            "rsn_trust_events",
            {
                "user_id": uid,
                "event_type": "identity_strike",
                "weight": -10,
                "ref_type": "encounter_confirm",
                "ref_id": str(uuid4()),
                "created_at": NOW,
            },
        )
        out = service.get_my_rewards(db, uid, now=NOW)
        assert out["suspended"] is True
        assert out["tier"] is None
        assert out["earned_tier"] == "regular"
        assert out["band"] == "watch"

    def test_banned_account_is_refused(self, db):
        uid = add_member(db, banned=True)
        with pytest.raises(service.ForbiddenError):
            service.get_my_rewards(db, uid, now=NOW)


class TestDisplayContract:
    """The shape Sofia's TW-941 display client consumes
    (frontend/src/lib/socialStanding.js, fail-closed normalizer):
    standing_tier in the tier vocabulary, stats with decided_rsvps,
    progress in the display key vocabulary with `target`, and
    last_movement distinguishing dormancy from conduct."""

    def test_payload_matches_display_normalizer_vocabulary(self, db):
        uid = add_member(db, band="age_18_20")
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=10 - i))
        out = service.get_my_rewards(db, uid, now=NOW)
        assert out["standing_tier"] == "regular"
        assert out["age_band"] == "18-20"
        assert out["stats"]["decided_rsvps"] == 5
        assert out["stats"]["completed_nights"] == 5
        assert out["stats"]["show_rate"] == 1.0
        assert out["last_movement"] is None
        progress = {p["key"]: p for p in out["progress"]}
        assert set(progress) == {"nights", "showRate", "corroboration"}
        assert progress["nights"] == {
            "key": "nights",
            "current": 5,
            "target": 20,
            "met": False,
        }
        assert progress["showRate"]["target"] == 0.85
        corr = progress["corroboration"]
        assert corr["met"] is False
        assert {p["key"] for p in corr["parts"]} == {"streak", "vouches"}

    def test_suspended_payload_keeps_tier_and_marks_conduct(self, db):
        uid = add_member(db)
        for i in range(5):
            _seed_night(db, uid, when=NOW - timedelta(days=10 - i))
        db.seed(
            "rsn_trust_events",
            {
                "user_id": uid,
                "event_type": "identity_strike",
                "weight": -10,
                "ref_type": "encounter_confirm",
                "ref_id": str(uuid4()),
                "created_at": NOW,
            },
        )
        out = service.get_my_rewards(db, uid, now=NOW)
        # The display normalizer masks on suspended/band itself, so
        # standing_tier still names the member's tier (never null).
        assert out["standing_tier"] == "regular"
        assert out["suspended"] is True
        assert out["progress"] is None
        assert out["last_movement"] == "conduct"
