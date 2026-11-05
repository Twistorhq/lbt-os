/**
 * Twistor Social standing contract (TW-941, display half).
 *
 * The member profile reads one payload: the member's tier and their
 * progress toward the next rung, computed by the backend from the
 * check-in and standing data (Zeke, branch zeke/tw-941-feed-rewards).
 * This module is the client side of that contract: the tier ladder,
 * the thresholds the progress facts are stated against, a fail-closed
 * normalizer for the wire payload, and the progress derivation used
 * when the payload carries stats but no server-computed progress.
 *
 * Contract: GET /api/v1/social/me/standing (api client path
 * "/social/me/standing") answering:
 *   {
 *     standing_tier: "verified" | "regular" | "fixture" | "pillar"
 *                    | "keyholder",
 *     band: "trusted" | "good_standing" | "watch" | "restricted",
 *     suspended: boolean,
 *     age_band: "21+" | "18-20" | "teen" | null,
 *     stats: {
 *       completed_nights: int, decided_rsvps: int,
 *       show_rate: number 0..1 | null,
 *       reviews_received: int,
 *       would_meet_again_rate: number 0..1 | null,
 *       hosted_clean: int, best_series_streak: int,
 *       distinct_vouches: int, tenure_days: int
 *     },
 *     next_tier: same tier vocabulary | null,
 *     progress: [{ key, current, target, met }] | null,
 *     last_movement: "dormancy" | "conduct" | null
 *   }
 *
 * Thresholds mirror backend/app/social/trust.py exactly (Regular 5
 * nights at 80 percent, Fixture 20 at 85 percent plus corroboration,
 * Pillar 40 at 90 percent plus reviews and hosted rooms, Keyholder 75
 * at a 90 percent show rate sustained - trust.py _meets_keyholder
 * chains through _meets_pillar, which requires PILLAR_SHOW_RATE
 * 0.90 - plus the review, hosting, streak, and tenure bars). The
 * catalog (twistor-social-reward-catalog.md Section 1) is the
 * authority for the names and meanings; this module never invents
 * a number.
 *
 * The Verified rung is a future-phase rung at launch (parked
 * verification phase, TW-941 brief): it is part of the design, it is
 * not live, and no payload value can make this module present it as
 * earned or earnable now. A payload tier of "verified" therefore
 * normalizes to no activity tier, with progress toward Regular.
 *
 * Prepared by Twistor Holdings LLC
 */

export const TIER_ORDER = ['verified', 'regular', 'fixture', 'pillar', 'keyholder']

export const ACTIVITY_TIERS = ['regular', 'fixture', 'pillar', 'keyholder']

export const BANDS = ['trusted', 'good_standing', 'watch', 'restricted']

export const SHOW_RATE_MIN_RSVPS = 5

export const THRESHOLDS = {
  regular: { nights: 5, showRate: 0.8 },
  fixture: { nights: 20, showRate: 0.85, streak: 4, vouches: 3 },
  pillar: {
    nights: 40,
    showRate: 0.9,
    wmaRate: 0.9,
    wmaMinReviews: 15,
    hostedClean: 6,
  },
  keyholder: {
    nights: 75,
    showRate: 0.9,
    wmaRate: 0.95,
    wmaMinReviews: 30,
    hostedClean: 15,
    streak: 8,
    tenureDays: 180,
  },
}

const CONDITION_KEYS = [
  'nights',
  'showRate',
  'corroboration',
  'streak',
  'vouches',
  'wmaRate',
  'wmaReviews',
  'hostedClean',
  'tenureDays',
]

function intOrZero(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

function rateOrNull(value) {
  if (value === null || value === undefined) return null
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return Math.min(1, Math.max(0, n))
}

export function normalizeStats(raw) {
  const s = raw && typeof raw === 'object' ? raw : {}
  const completed = intOrZero(s.completed_nights)
  const decided =
    s.decided_rsvps === undefined || s.decided_rsvps === null
      ? completed + intOrZero(s.no_shows)
      : intOrZero(s.decided_rsvps)
  let showRate = rateOrNull(s.show_rate)
  if (showRate === null && decided > 0) showRate = completed / decided
  return {
    completedNights: completed,
    decidedRsvps: decided,
    showRate,
    reviewsReceived: intOrZero(s.reviews_received),
    wouldMeetAgainRate: rateOrNull(s.would_meet_again_rate),
    hostedClean: intOrZero(s.hosted_clean),
    bestSeriesStreak: intOrZero(s.best_series_streak),
    distinctVouches: intOrZero(s.distinct_vouches),
    tenureDays: intOrZero(s.tenure_days),
  }
}

/** The next activity rung above a normalized activity tier. */
export function nextTierFor(activityTier) {
  if (!activityTier) return 'regular'
  const idx = ACTIVITY_TIERS.indexOf(activityTier)
  if (idx < 0 || idx === ACTIVITY_TIERS.length - 1) return null
  return ACTIVITY_TIERS[idx + 1]
}

/**
 * Progress conditions for one target rung, stated as facts from the
 * member's stats (pattern 06: missing conditions as facts, never a
 * percentage bar and never an estimate).
 */
export function deriveProgress(stats, targetTier) {
  const t = THRESHOLDS[targetTier]
  if (!t) return []
  const conditions = []
  const count = (key, current, target) =>
    conditions.push({ key, kind: 'count', current, target, met: current >= target })
  const rate = (key, current, target) =>
    conditions.push({
      key,
      kind: 'rate',
      current,
      target,
      met: current !== null && current >= target,
    })
  // Catalog Section 5: the show rate is not stated until the member
  // has 5 or more decided RSVPs. Below that a showRate condition
  // would leak the rate (as a current fact, or as Done), so it is
  // omitted entirely - no current rate, no met flag - until the
  // record is large enough to state (TW-941 fix round 1, Rosa M2).
  const showRateStated = stats.decidedRsvps >= SHOW_RATE_MIN_RSVPS
  const showRate = (key, current, target) => {
    if (showRateStated) rate(key, current, target)
  }
  if (targetTier === 'regular') {
    count('nights', stats.completedNights, t.nights)
    showRate('showRate', stats.showRate, t.showRate)
  } else if (targetTier === 'fixture') {
    count('nights', stats.completedNights, t.nights)
    showRate('showRate', stats.showRate, t.showRate)
    conditions.push({
      key: 'corroboration',
      kind: 'either',
      met:
        stats.bestSeriesStreak >= t.streak || stats.distinctVouches >= t.vouches,
      parts: [
        { key: 'streak', current: stats.bestSeriesStreak, target: t.streak },
        { key: 'vouches', current: stats.distinctVouches, target: t.vouches },
      ],
    })
  } else if (targetTier === 'pillar') {
    count('nights', stats.completedNights, t.nights)
    showRate('showRate', stats.showRate, t.showRate)
    rate('wmaRate', stats.wouldMeetAgainRate, t.wmaRate)
    count('wmaReviews', stats.reviewsReceived, t.wmaMinReviews)
    count('hostedClean', stats.hostedClean, t.hostedClean)
  } else if (targetTier === 'keyholder') {
    count('nights', stats.completedNights, t.nights)
    showRate('showRate', stats.showRate, t.showRate)
    rate('wmaRate', stats.wouldMeetAgainRate, t.wmaRate)
    count('wmaReviews', stats.reviewsReceived, t.wmaMinReviews)
    count('hostedClean', stats.hostedClean, t.hostedClean)
    count('streak', stats.bestSeriesStreak, t.streak)
    count('tenureDays', stats.tenureDays, t.tenureDays)
  }
  return conditions
}

function normalizeServerProgress(raw) {
  if (!Array.isArray(raw)) return null
  const out = []
  for (const item of raw) {
    if (!item || typeof item !== 'object' || !CONDITION_KEYS.includes(item.key)) {
      return null
    }
    if (item.key === 'corroboration' && Array.isArray(item.parts)) {
      out.push({
        key: 'corroboration',
        kind: 'either',
        met: item.met === true,
        parts: item.parts.map((p) => ({
          key: p.key,
          current: intOrZero(p.current),
          target: intOrZero(p.target),
        })),
      })
    } else {
      const isRate = item.key === 'showRate' || item.key === 'wmaRate'
      out.push({
        key: item.key,
        kind: isRate ? 'rate' : 'count',
        current: isRate ? rateOrNull(item.current) : intOrZero(item.current),
        target: isRate ? rateOrNull(item.target) : intOrZero(item.target),
        met: item.met === true,
      })
    }
  }
  return out
}

/** The immediate successor of a tier, with the teen cap applied. */
function cappedNextTierFor(activityTier, isTeen) {
  const next = nextTierFor(activityTier)
  // Catalog Section 7: Keyholder does not exist in the teen tier;
  // Pillar is the top of the teen ladder, so it has no successor.
  if (isTeen && next === 'keyholder') return null
  return next
}

/**
 * Fail-closed normalizer: an unrecognized tier, an unrecognized or
 * missing safety band, or a malformed payload normalizes to null,
 * and the surface renders its error state rather than inventing
 * standing. There is no default band (TW-652 M1 no-default-band
 * doctrine, TW-941 fix round 1): a band off the trusted /
 * good_standing / watch / restricted vocabulary is off-contract,
 * exactly like an unrecognized tier. A payload tier of "verified"
 * is the base state before any activity rung; it is never an
 * earned display tier.
 *
 * Cross-field consistency is enforced here, not left to the
 * display (TW-941 fix round 1):
 * - Teen cap: a teen activity tier is capped at Pillar in the
 *   normalizer itself, and a teen next tier is never Keyholder.
 * - Ladder order: a declared next_tier is accepted only when it
 *   is the immediate successor of the normalized current tier
 *   (null at the top, and null while suspended). A declared next
 *   tier that skips, repeats, or reverses a rung is disregarded
 *   in favor of the computed successor, and any server progress
 *   that arrived with it is disregarded with it, because that
 *   progress was stated against the wrong rung.
 * - Show rate: server progress is gated the same way derived
 *   progress is - a showRate condition is dropped below 5 decided
 *   RSVPs, so the rate cannot leak through a server payload.
 */
export function normalizeStanding(raw) {
  if (!raw || typeof raw !== 'object') return null
  const tier = typeof raw.standing_tier === 'string' ? raw.standing_tier : null
  if (!TIER_ORDER.includes(tier)) return null
  if (!BANDS.includes(raw.band)) return null
  const band = raw.band
  const suspended =
    raw.suspended === true || band === 'watch' || band === 'restricted'
  const ageBand = typeof raw.age_band === 'string' ? raw.age_band : null
  const isTeen = ageBand === 'teen'
  let activityTier = ACTIVITY_TIERS.includes(tier) ? tier : null
  if (isTeen && activityTier === 'keyholder') activityTier = 'pillar'
  const stats = normalizeStats(raw.stats)
  const declaredNext = ACTIVITY_TIERS.includes(raw.next_tier) ? raw.next_tier : null
  const expectedNext = suspended ? null : cappedNextTierFor(activityTier, isTeen)
  const nextTier = expectedNext
  const declaredNextMatches =
    declaredNext === null || declaredNext === expectedNext
  const serverProgress =
    !suspended && declaredNextMatches
      ? normalizeServerProgress(raw.progress)
      : null
  const progress = suspended
    ? []
    : (serverProgress ?? (nextTier ? deriveProgress(stats, nextTier) : []))
  return {
    activityTier,
    band,
    suspended,
    ageBand,
    stats,
    nextTier,
    progress:
      stats.decidedRsvps >= SHOW_RATE_MIN_RSVPS
        ? progress
        : progress.filter((condition) => condition.key !== 'showRate'),
    lastMovement:
      raw.last_movement === 'dormancy' || raw.last_movement === 'conduct'
        ? raw.last_movement
        : null,
  }
}

/**
 * Test fixtures mirroring the documented contract. These are the
 * fixture-backed stand-in for the backend branch until it merges;
 * production code reaches standing only through
 * socialStandingClient.fetchMyStanding. Fixture values are chosen so
 * every display rule has a case: no tier yet, mid-climb, top rung,
 * and suspended.
 */
export const FIXTURE_STANDING = {
  newMember: {
    standing_tier: 'verified',
    band: 'good_standing',
    suspended: false,
    age_band: '21+',
    stats: {
      completed_nights: 3,
      decided_rsvps: 4,
      show_rate: 0.75,
      reviews_received: 0,
      would_meet_again_rate: null,
      hosted_clean: 0,
      best_series_streak: 1,
      distinct_vouches: 0,
      tenure_days: 21,
    },
    next_tier: 'regular',
    progress: null,
    last_movement: null,
  },
  regularClimbing: {
    standing_tier: 'regular',
    band: 'good_standing',
    suspended: false,
    age_band: '18-20',
    stats: {
      completed_nights: 12,
      decided_rsvps: 13,
      show_rate: 12 / 13,
      reviews_received: 4,
      would_meet_again_rate: 1,
      hosted_clean: 0,
      best_series_streak: 2,
      distinct_vouches: 1,
      tenure_days: 74,
    },
    next_tier: 'fixture',
    progress: null,
    last_movement: null,
  },
  keyholder: {
    standing_tier: 'keyholder',
    band: 'trusted',
    suspended: false,
    age_band: '21+',
    stats: {
      completed_nights: 82,
      decided_rsvps: 88,
      show_rate: 82 / 88,
      reviews_received: 34,
      would_meet_again_rate: 33 / 34,
      hosted_clean: 17,
      best_series_streak: 9,
      distinct_vouches: 6,
      tenure_days: 240,
    },
    next_tier: null,
    progress: null,
    last_movement: 'dormancy',
  },
  suspendedFixture: {
    standing_tier: 'fixture',
    band: 'watch',
    suspended: true,
    age_band: '21+',
    stats: {
      completed_nights: 26,
      decided_rsvps: 29,
      show_rate: 26 / 29,
      reviews_received: 9,
      would_meet_again_rate: 8 / 9,
      hosted_clean: 2,
      best_series_streak: 5,
      distinct_vouches: 3,
      tenure_days: 130,
    },
    next_tier: 'pillar',
    progress: null,
    last_movement: 'conduct',
  },
}
