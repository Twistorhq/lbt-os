"""TW-941 Twistor Social: rewards-loop progress projection (pure).

The member-facing side of trust.py: the same catalog thresholds
(twistor-social-reward-catalog.md, planning values set by Justynn),
projected as a ladder with per-rung progress toward the next rung,
so the profile display (Sofia, TW-652 lanes) renders facts instead
of inventing numbers (pattern 06: progress is missing conditions
stated as facts, never a percentage bar and never an estimate).

Rules that live here and nowhere else:

- Thresholds are read from trust.py constants. There is one
  ladder; this module never restates a number.
- The Verified rung is a future-phase rung (the verification phase
  is parked, TW-937): it is returned with phase
  "future_verification" and earned=False in every state, even for
  a Keyholder record. Activity rungs (Regular, Fixture, Pillar,
  Keyholder) are the earnable ladder.
- While the safety band is watch or restricted, the standing tier
  is computed and retained but not displayed (catalog Section 1):
  `tier` is None and `suspended` is True; `earned_tier` still
  reports the member's private position to the member.
"""

from __future__ import annotations

from . import trust

ACTIVITY_RUNGS = ("regular", "fixture", "pillar", "keyholder")


def _req(key, current, required, *, met=None, any_of=None):
    if met is None:
        met = current is not None and current >= required
    remaining = None
    if isinstance(current, (int, float)) and isinstance(required, (int, float)):
        remaining = max(0, required - current)
        if isinstance(remaining, float):
            remaining = round(remaining, 4)
    out = {
        "key": key,
        "current": current,
        "required": required,
        "met": bool(met),
        "remaining": remaining,
    }
    if any_of is not None:
        out["any_of"] = any_of
    return out


def rung_requirements(stats: trust.MemberStats) -> dict[str, list[dict]]:
    """Per-rung requirement lists, mirroring trust.py's predicates
    term for term. Fixture corroboration is an either/or (a series
    streak OR distinct vouches): both requirements carry
    any_of="corroboration" and each is met when either path is."""
    rate = trust.show_rate(stats)
    wma = trust.would_meet_again_rate(stats)
    corroborated = (
        stats.best_series_streak >= trust.FIXTURE_STREAK
        or stats.distinct_vouches >= trust.FIXTURE_VOUCHES
    )
    return {
        "regular": [
            _req("completed_nights", stats.completed_nights, trust.REGULAR_NIGHTS),
            _req("show_rate", rate, trust.REGULAR_SHOW_RATE),
        ],
        "fixture": [
            _req("completed_nights", stats.completed_nights, trust.FIXTURE_NIGHTS),
            _req("show_rate", rate, trust.FIXTURE_SHOW_RATE),
            _req(
                "best_series_streak",
                stats.best_series_streak,
                trust.FIXTURE_STREAK,
                met=corroborated,
                any_of="corroboration",
            ),
            _req(
                "distinct_vouches",
                stats.distinct_vouches,
                trust.FIXTURE_VOUCHES,
                met=corroborated,
                any_of="corroboration",
            ),
        ],
        "pillar": [
            _req("completed_nights", stats.completed_nights, trust.PILLAR_NIGHTS),
            _req("show_rate", rate, trust.PILLAR_SHOW_RATE),
            _req(
                "reviews_received",
                stats.reviews_received,
                trust.PILLAR_WMA_MIN_REVIEWS,
            ),
            _req("would_meet_again_rate", wma, trust.PILLAR_WMA_RATE),
            _req("hosted_clean", stats.hosted_clean, trust.PILLAR_HOSTED_CLEAN),
        ],
        "keyholder": [
            _req("completed_nights", stats.completed_nights, trust.KEYHOLDER_NIGHTS),
            _req("show_rate", rate, trust.PILLAR_SHOW_RATE),
            _req(
                "reviews_received",
                stats.reviews_received,
                trust.KEYHOLDER_WMA_MIN_REVIEWS,
            ),
            _req("would_meet_again_rate", wma, trust.KEYHOLDER_WMA_RATE),
            _req("hosted_clean", stats.hosted_clean, trust.KEYHOLDER_HOSTED_CLEAN),
            _req(
                "best_series_streak",
                stats.best_series_streak,
                trust.KEYHOLDER_STREAK,
            ),
            _req("tenure_days", stats.tenure_days, trust.KEYHOLDER_TENURE_DAYS),
        ],
    }


def progress(stats: trust.MemberStats, *, dormant_days: int = 0) -> dict:
    """The full ladder projection for one member's aggregates."""
    standing = trust.compute_standing(stats, dormant_days=dormant_days)
    earned = standing.tier
    requirements = rung_requirements(stats)
    earned_rank = trust.TIER_ORDER.index(earned)
    ladder = [
        {
            "tier": "verified",
            "phase": "future_verification",
            "earned": False,
            "is_current": False,
            "requirements": [],
        }
    ]
    for rung in ACTIVITY_RUNGS:
        # A rung counts as earned when the record's tier reaches it
        # (the verified base, rank 0, earns no activity rung).
        rung_earned = trust.TIER_ORDER.index(rung) <= earned_rank
        ladder.append(
            {
                "tier": rung,
                "phase": "activity",
                "earned": rung_earned,
                "is_current": rung == earned,
                "requirements": requirements[rung],
            }
        )
    next_tier = None
    if earned != "keyholder":
        next_tier = trust.TIER_ORDER[earned_rank + 1]
    return {
        "tier": None if standing.suspended else earned,
        "earned_tier": earned,
        "band": standing.band,
        "suspended": standing.suspended,
        "show_rate": trust.show_rate(stats),
        "would_meet_again_rate": trust.would_meet_again_rate(stats),
        "next_tier": next_tier,
        "next_requirements": requirements[next_tier] if next_tier else [],
        "ladder": ladder,
    }
