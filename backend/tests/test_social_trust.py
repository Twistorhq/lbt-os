"""TW-650 Twistor Social — trust pure functions (TDD RED).

Pins the reward catalog's planning thresholds (Verified → Regular →
Fixture → Pillar → Keyholder), the dormancy step-down (tier only, never
the band, never verification), and the safety band mapping. Tiers are
cumulative: each tier requires every lower tier's conditions plus its
own. Standing is computed and retained but suspended from display and
entitlement while the band is watch or restricted (spec Section 4,
rsn_trust_scores).
"""

from app.social.trust import (
    MemberStats,
    compute_band,
    compute_standing,
    show_rate,
    would_meet_again_rate,
)


def stats(**over) -> MemberStats:
    base = dict(
        completed_nights=0,
        no_shows=0,
        reviews_received=0,
        would_meet_again_positive=0,
        hosted_clean=0,
        best_series_streak=0,
        tenure_days=0,
        substantiated_reports=0,
        identity_strikes=0,
    )
    base.update(over)
    return MemberStats(**base)


class TestRates:
    def test_show_rate_none_without_rsvps(self):
        assert show_rate(stats()) is None

    def test_show_rate_math(self):
        s = stats(completed_nights=8, no_shows=2)
        assert show_rate(s) == 0.8

    def test_wma_rate_none_without_reviews(self):
        assert would_meet_again_rate(stats()) is None

    def test_wma_rate_math(self):
        s = stats(reviews_received=20, would_meet_again_positive=19)
        assert would_meet_again_rate(s) == 0.95


class TestStandingTiers:
    def test_fresh_member_is_verified(self):
        assert compute_standing(stats()).tier == "verified"

    def test_regular_at_5_nights_80_percent(self):
        s = stats(completed_nights=5, no_shows=1)  # 5/6 = 0.833
        assert compute_standing(s).tier == "regular"
        s2 = stats(completed_nights=4, no_shows=1)
        assert compute_standing(s2).tier == "verified"

    def test_regular_show_rate_floor(self):
        s = stats(completed_nights=5, no_shows=2)  # 5/7 = 0.714
        assert compute_standing(s).tier == "verified"

    def test_fixture_needs_streak(self):
        s = stats(completed_nights=20, no_shows=3, best_series_streak=3)
        assert compute_standing(s).tier == "regular"
        s = stats(completed_nights=20, no_shows=3, best_series_streak=4)
        assert compute_standing(s).tier == "fixture"

    def test_pillar_full_bar(self):
        s = stats(
            completed_nights=40,
            no_shows=4,  # 40/44 = 0.909
            reviews_received=15,
            would_meet_again_positive=14,  # 0.933
            hosted_clean=6,
            best_series_streak=4,
        )
        assert compute_standing(s).tier == "pillar"

    def test_pillar_wma_sample_floor(self):
        # 100% WMA on 3 reviews does not clear the 15-review floor.
        s = stats(
            completed_nights=40,
            no_shows=0,
            reviews_received=3,
            would_meet_again_positive=3,
            hosted_clean=6,
            best_series_streak=4,
        )
        assert compute_standing(s).tier == "fixture"

    def test_keyholder_full_bar(self):
        s = stats(
            completed_nights=75,
            no_shows=5,  # 75/80 = 0.9375
            reviews_received=30,
            would_meet_again_positive=29,  # 0.966
            hosted_clean=15,
            best_series_streak=8,
            tenure_days=200,
        )
        assert compute_standing(s).tier == "keyholder"

    def test_keyholder_tenure_gate(self):
        s = stats(
            completed_nights=75,
            no_shows=0,
            reviews_received=30,
            would_meet_again_positive=30,
            hosted_clean=15,
            best_series_streak=8,
            tenure_days=90,
        )
        assert compute_standing(s).tier == "pillar"


class TestDormancyDecay:
    def test_grace_period_no_decay(self):
        s = stats(completed_nights=20, no_shows=0, best_series_streak=4)
        assert compute_standing(s, dormant_days=90).tier == "fixture"

    def test_one_step_after_grace_plus_60(self):
        s = stats(completed_nights=20, no_shows=0, best_series_streak=4)
        assert compute_standing(s, dormant_days=150).tier == "regular"

    def test_decay_floors_at_verified(self):
        s = stats(completed_nights=5, no_shows=0)
        assert compute_standing(s, dormant_days=5000).tier == "verified"

    def test_decay_never_touches_band(self):
        s = stats(completed_nights=20, no_shows=0, best_series_streak=4)
        decayed = compute_standing(s, dormant_days=150)
        fresh = compute_standing(s)
        assert decayed.tier != fresh.tier  # the tier stepped down
        assert decayed.band == fresh.band == "trusted"  # the band did not


class TestBand:
    def test_clean_new_member_good_standing(self):
        assert compute_band(stats()) == "good_standing"

    def test_trusted_needs_track_record(self):
        assert compute_band(stats(completed_nights=10)) == "trusted"
        assert compute_band(stats(completed_nights=9)) == "good_standing"

    def test_one_strike_is_watch(self):
        assert compute_band(stats(identity_strikes=1)) == "watch"
        assert compute_band(stats(substantiated_reports=1)) == "watch"

    def test_two_strikes_restricted(self):
        assert compute_band(stats(identity_strikes=2)) == "restricted"
        assert compute_band(stats(substantiated_reports=2)) == "restricted"

    def test_standing_suspended_while_watch_or_restricted(self):
        s = stats(
            completed_nights=20, no_shows=0, best_series_streak=4, identity_strikes=1
        )
        result = compute_standing(s)
        assert result.tier == "fixture"  # computed and retained
        assert result.suspended is True  # but not displayable/entitling
        clean = compute_standing(
            stats(completed_nights=20, no_shows=0, best_series_streak=4)
        )
        assert clean.suspended is False
