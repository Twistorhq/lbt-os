// Prepared by Twistor Holdings LLC.
//
// TW-941 (display half): contract tests for the standing client
// module. Thresholds asserted here mirror backend/app/social/
// trust.py; if the backend thresholds move, this file must move
// with them, because the member-facing progress facts are stated
// against those numbers.
import { describe, it, expect } from 'vitest'
import {
  FIXTURE_STANDING,
  THRESHOLDS,
  deriveProgress,
  nextTierFor,
  normalizeStanding,
  normalizeStats,
} from '../socialStanding'
import {
  STANDING_PATH,
  createFixtureStandingClient,
  fetchMyStanding,
} from '../socialStandingClient'

describe('socialStanding contract (TW-941)', () => {
  it('derives Regular progress from catalog thresholds', () => {
    // At 5+ decided RSVPs the show rate may be stated as a fact.
    const stats = normalizeStats({
      ...FIXTURE_STANDING.newMember.stats,
      decided_rsvps: 5,
      show_rate: 0.6,
    })
    const progress = deriveProgress(stats, 'regular')
    expect(progress).toEqual([
      { key: 'nights', kind: 'count', current: 3, target: 5, met: false },
      { key: 'showRate', kind: 'rate', current: 0.6, target: 0.8, met: false },
    ])
  })

  it('omits the showRate condition below 5 decided RSVPs (no rate, no Done)', () => {
    // Rosa probe, fix round 1: the newMember fixture has 4 decided
    // RSVPs at 75 percent; that rate must not appear in progress.
    const stats = normalizeStats(FIXTURE_STANDING.newMember.stats)
    expect(stats.decidedRsvps).toBe(4)
    expect(deriveProgress(stats, 'regular')).toEqual([
      { key: 'nights', kind: 'count', current: 3, target: 5, met: false },
    ])
    const standing = normalizeStanding(FIXTURE_STANDING.newMember)
    expect(standing.progress.map((c) => c.key)).not.toContain('showRate')
  })

  it('drops a server-supplied showRate condition below 5 decided RSVPs', () => {
    const standing = normalizeStanding({
      ...FIXTURE_STANDING.newMember,
      progress: [
        { key: 'nights', current: 3, target: 5, met: false },
        { key: 'showRate', current: 0.75, target: 0.8, met: false },
      ],
    })
    expect(standing.progress).toEqual([
      { key: 'nights', kind: 'count', current: 3, target: 5, met: false },
    ])
  })

  it('derives Fixture progress with the streak or vouches corroboration', () => {
    const stats = normalizeStats(FIXTURE_STANDING.regularClimbing.stats)
    const progress = deriveProgress(stats, 'fixture')
    const corroboration = progress.find((c) => c.key === 'corroboration')
    expect(progress.find((c) => c.key === 'nights')).toMatchObject({
      current: 12,
      target: 20,
      met: false,
    })
    expect(progress.find((c) => c.key === 'showRate').met).toBe(true)
    expect(corroboration.met).toBe(false)
    expect(corroboration.parts).toEqual([
      { key: 'streak', current: 2, target: 4 },
      { key: 'vouches', current: 1, target: 3 },
    ])
    const vouched = deriveProgress(
      normalizeStats({ ...FIXTURE_STANDING.regularClimbing.stats, distinct_vouches: 3 }),
      'fixture',
    )
    expect(vouched.find((c) => c.key === 'corroboration').met).toBe(true)
  })

  it('derives Keyholder progress with the sustained 90 percent show rate, hosting, streak, and tenure bars', () => {
    // Catalog Section 1 + trust.py: _meets_keyholder chains through
    // _meets_pillar, so Keyholder requires showRate 0.90 sustained.
    expect(THRESHOLDS.keyholder.showRate).toBe(0.9)
    const stats = normalizeStats(FIXTURE_STANDING.keyholder.stats)
    const progress = deriveProgress(stats, 'keyholder')
    expect(progress.map((c) => c.key)).toEqual([
      'nights',
      'showRate',
      'wmaRate',
      'wmaReviews',
      'hostedClean',
      'streak',
      'tenureDays',
    ])
    expect(progress.find((c) => c.key === 'showRate')).toMatchObject({
      current: 82 / 88,
      target: 0.9,
      met: true,
    })
  })

  it('walks the ladder in order and stops at Keyholder', () => {
    expect(nextTierFor(null)).toBe('regular')
    expect(nextTierFor('regular')).toBe('fixture')
    expect(nextTierFor('fixture')).toBe('pillar')
    expect(nextTierFor('pillar')).toBe('keyholder')
    expect(nextTierFor('keyholder')).toBeNull()
  })

  it('normalizes a verified payload to no activity tier, never an earned Verified', () => {
    const standing = normalizeStanding(FIXTURE_STANDING.newMember)
    expect(standing.activityTier).toBeNull()
    expect(standing.nextTier).toBe('regular')
    expect(standing.suspended).toBe(false)
  })

  it('fails closed on an unrecognized tier or malformed payload', () => {
    expect(normalizeStanding(null)).toBeNull()
    expect(normalizeStanding({ standing_tier: 'legend' })).toBeNull()
    expect(normalizeStanding({})).toBeNull()
  })

  it('fails closed on an unknown or missing band instead of defaulting to good standing', () => {
    // Rosa probe, fix round 1: no default band (TW-652 M1 doctrine).
    expect(
      normalizeStanding({ ...FIXTURE_STANDING.regularClimbing, band: 'vip' }),
    ).toBeNull()
    const { band: _band, ...noBand } = FIXTURE_STANDING.regularClimbing
    expect(normalizeStanding(noBand)).toBeNull()
    expect(
      normalizeStanding({ ...FIXTURE_STANDING.regularClimbing, band: undefined }),
    ).toBeNull()
  })

  it('caps a teen at Pillar in the normalizer: tier keyholder becomes pillar, next is never keyholder', () => {
    // Rosa probes, fix round 1 (catalog Section 7).
    const capped = normalizeStanding({
      ...FIXTURE_STANDING.keyholder,
      age_band: 'teen',
    })
    expect(capped.activityTier).toBe('pillar')
    expect(capped.nextTier).toBeNull()
    const atCap = normalizeStanding({
      ...FIXTURE_STANDING.regularClimbing,
      standing_tier: 'pillar',
      next_tier: 'keyholder',
      age_band: 'teen',
    })
    expect(atCap.activityTier).toBe('pillar')
    expect(atCap.nextTier).toBeNull()
    expect(atCap.progress).toEqual([])
    const climbing = normalizeStanding({
      ...FIXTURE_STANDING.regularClimbing,
      standing_tier: 'fixture',
      next_tier: 'keyholder',
      age_band: 'teen',
    })
    expect(climbing.nextTier).toBe('pillar')
  })

  it('accepts only the immediate successor as next_tier and disregards a skipping declaration', () => {
    // Rosa probe, fix round 1: verified + next keyholder skips Regular.
    const skipped = normalizeStanding({
      ...FIXTURE_STANDING.newMember,
      next_tier: 'keyholder',
    })
    expect(skipped.activityTier).toBeNull()
    expect(skipped.nextTier).toBe('regular')
    const backwards = normalizeStanding({
      ...FIXTURE_STANDING.regularClimbing,
      standing_tier: 'pillar',
      next_tier: 'regular',
    })
    expect(backwards.nextTier).toBe('keyholder')
    const atTop = normalizeStanding({
      ...FIXTURE_STANDING.keyholder,
      next_tier: 'regular',
    })
    expect(atTop.nextTier).toBeNull()
  })

  it('disregards server progress that arrived with a mismatched next_tier', () => {
    const standing = normalizeStanding({
      ...FIXTURE_STANDING.regularClimbing,
      next_tier: 'keyholder',
      progress: [{ key: 'nights', current: 12, target: 75, met: false }],
    })
    expect(standing.nextTier).toBe('fixture')
    // Derived Fixture progress (20 nights), not the server 75-night
    // progress that was stated against the wrong rung.
    expect(standing.progress.find((c) => c.key === 'nights')).toMatchObject({
      current: 12,
      target: 20,
    })
    expect(standing.progress.map((c) => c.key)).toContain('corroboration')
  })

  it('treats watch and restricted bands as suspended even if the flag is absent', () => {
    const standing = normalizeStanding({
      ...FIXTURE_STANDING.suspendedFixture,
      suspended: false,
    })
    expect(standing.suspended).toBe(true)
    expect(standing.nextTier).toBeNull()
  })

  it('prefers server-computed progress when the payload carries it', () => {
    const standing = normalizeStanding({
      ...FIXTURE_STANDING.regularClimbing,
      progress: [{ key: 'nights', current: 12, target: 20, met: false }],
    })
    expect(standing.progress).toEqual([
      { key: 'nights', kind: 'count', current: 12, target: 20, met: false },
    ])
  })

  it('rejects server progress with an unknown condition key', () => {
    const standing = normalizeStanding({
      ...FIXTURE_STANDING.regularClimbing,
      progress: [{ key: 'points', current: 1, target: 2, met: false }],
    })
    // Unknown keys fall back to the derived progress, never render raw.
    expect(standing.progress.map((c) => c.key)).toContain('nights')
    expect(standing.progress.map((c) => c.key)).not.toContain('points')
  })

  it('fetches through the documented contract path and normalizes', async () => {
    const seen = []
    const client = {
      async get(path) {
        seen.push(path)
        return { data: FIXTURE_STANDING.regularClimbing }
      },
    }
    const standing = await fetchMyStanding(client)
    expect(seen).toEqual([STANDING_PATH])
    expect(STANDING_PATH).toBe('/social/me/standing')
    expect(standing.activityTier).toBe('regular')
  })

  it('throws on an off-contract response instead of falling back to fixtures', async () => {
    const client = { async get() { return { data: { standing_tier: 'legend' } } } }
    await expect(fetchMyStanding(client)).rejects.toThrow(/contract/)
    const badBand = {
      async get() {
        return { data: { ...FIXTURE_STANDING.regularClimbing, band: 'vip' } }
      },
    }
    await expect(fetchMyStanding(badBand)).rejects.toThrow(/contract/)
  })

  it('serves fixtures only through the explicit fixture client', async () => {
    const standing = await fetchMyStanding(createFixtureStandingClient('keyholder'))
    expect(standing.activityTier).toBe('keyholder')
    expect(standing.nextTier).toBeNull()
  })
})
