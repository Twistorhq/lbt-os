"""TW-650 Twistor Social — trust computation (pure functions).

The trust ledger (rsn_trust_events) is append-only; the score, band,
and standing tier in rsn_trust_scores are derived from it and
recomputable at any time (spec Section 7.5). This module is the pure
core of that derivation: aggregates in, standing out, no I/O.

Standing ladder (reward catalog, Imani; planning thresholds, tuned
by configuration later, never by hand grants):
  Regular   = 5 completed nights + 80% show rate
  Fixture   = 20 nights + 85% show + a 4-instance series streak
  Pillar    = 40 nights + 90% show + 90% would-meet-again across
              15+ reviews + 6 clean hosted events
  Keyholder = 75 nights + 15 clean hosted + 95% WMA across 30+
              reviews + 8+ streak + 6 months tenure
Tiers are cumulative: each tier requires every lower tier's bar.

Dormancy: standing DECAYS with inactivity (90-day grace, then one
tier step per 60 dormant days, floor at Verified). Decay steps the
tier only — never the safety band, never the verification itself
(TW-646 doctrine 4 / spec Section 7.5).

Band (planning defaults; Phase 6 tunes with case data): a clean
record with 10+ nights is trusted; one substantiated report or
identity strike is watch; two is restricted. While the band is watch
or restricted the standing tier is computed and retained but
suspended from display and entitlement (spec Section 4).
"""

from __future__ import annotations

from dataclasses import dataclass

TIER_ORDER = ("verified", "regular", "fixture", "pillar", "keyholder")

REGULAR_NIGHTS = 5
REGULAR_SHOW_RATE = 0.80
FIXTURE_NIGHTS = 20
FIXTURE_SHOW_RATE = 0.85
FIXTURE_STREAK = 4
PILLAR_NIGHTS = 40
PILLAR_SHOW_RATE = 0.90
PILLAR_WMA_RATE = 0.90
PILLAR_WMA_MIN_REVIEWS = 15
PILLAR_HOSTED_CLEAN = 6
KEYHOLDER_NIGHTS = 75
KEYHOLDER_WMA_RATE = 0.95
KEYHOLDER_WMA_MIN_REVIEWS = 30
KEYHOLDER_HOSTED_CLEAN = 15
KEYHOLDER_STREAK = 8
KEYHOLDER_TENURE_DAYS = 180

DORMANCY_GRACE_DAYS = 90
DORMANCY_STEP_DAYS = 60

TRUSTED_MIN_NIGHTS = 10


@dataclass(frozen=True)
class MemberStats:
    """Aggregates projected from rsn_trust_events + rsn_rsvps."""

    completed_nights: int = 0
    no_shows: int = 0
    reviews_received: int = 0
    would_meet_again_positive: int = 0
    hosted_clean: int = 0
    best_series_streak: int = 0
    tenure_days: int = 0
    substantiated_reports: int = 0
    identity_strikes: int = 0


@dataclass(frozen=True)
class StandingResult:
    tier: str
    band: str
    suspended: bool


def show_rate(stats: MemberStats) -> float | None:
    """Completed nights over decided RSVPs. None until the member has
    at least one decided RSVP (the catalog hides show rate below 5)."""
    decided = stats.completed_nights + stats.no_shows
    if decided == 0:
        return None
    return stats.completed_nights / decided


def would_meet_again_rate(stats: MemberStats) -> float | None:
    if stats.reviews_received == 0:
        return None
    return stats.would_meet_again_positive / stats.reviews_received


def _meets_regular(s: MemberStats) -> bool:
    rate = show_rate(s)
    return (
        s.completed_nights >= REGULAR_NIGHTS
        and rate is not None
        and rate >= REGULAR_SHOW_RATE
    )


def _meets_fixture(s: MemberStats) -> bool:
    rate = show_rate(s)
    return (
        _meets_regular(s)
        and s.completed_nights >= FIXTURE_NIGHTS
        and rate is not None
        and rate >= FIXTURE_SHOW_RATE
        and s.best_series_streak >= FIXTURE_STREAK
    )


def _meets_pillar(s: MemberStats) -> bool:
    rate = show_rate(s)
    wma = would_meet_again_rate(s)
    return (
        _meets_fixture(s)
        and s.completed_nights >= PILLAR_NIGHTS
        and rate is not None
        and rate >= PILLAR_SHOW_RATE
        and s.reviews_received >= PILLAR_WMA_MIN_REVIEWS
        and wma is not None
        and wma >= PILLAR_WMA_RATE
        and s.hosted_clean >= PILLAR_HOSTED_CLEAN
    )


def _meets_keyholder(s: MemberStats) -> bool:
    wma = would_meet_again_rate(s)
    return (
        _meets_pillar(s)
        and s.completed_nights >= KEYHOLDER_NIGHTS
        and s.reviews_received >= KEYHOLDER_WMA_MIN_REVIEWS
        and wma is not None
        and wma >= KEYHOLDER_WMA_RATE
        and s.hosted_clean >= KEYHOLDER_HOSTED_CLEAN
        and s.best_series_streak >= KEYHOLDER_STREAK
        and s.tenure_days >= KEYHOLDER_TENURE_DAYS
    )


def earned_tier(stats: MemberStats) -> str:
    """The tier the member's record earns, before dormancy decay."""
    if _meets_keyholder(stats):
        return "keyholder"
    if _meets_pillar(stats):
        return "pillar"
    if _meets_fixture(stats):
        return "fixture"
    if _meets_regular(stats):
        return "regular"
    return "verified"


def apply_dormancy_decay(tier: str, dormant_days: int) -> str:
    """Step the tier down for inactivity; floor at verified. The
    safety band and the verification itself never decay (doctrine)."""
    if dormant_days <= DORMANCY_GRACE_DAYS:
        return tier
    steps = (dormant_days - DORMANCY_GRACE_DAYS) // DORMANCY_STEP_DAYS
    idx = TIER_ORDER.index(tier)
    return TIER_ORDER[max(0, idx - steps)]


def compute_band(stats: MemberStats) -> str:
    strikes = stats.identity_strikes + stats.substantiated_reports
    if strikes >= 2:
        return "restricted"
    if strikes >= 1:
        return "watch"
    if stats.completed_nights >= TRUSTED_MIN_NIGHTS:
        return "trusted"
    return "good_standing"


def compute_standing(stats: MemberStats, *, dormant_days: int = 0) -> StandingResult:
    tier = apply_dormancy_decay(earned_tier(stats), dormant_days)
    band = compute_band(stats)
    return StandingResult(
        tier=tier,
        band=band,
        suspended=band in ("watch", "restricted"),
    )
