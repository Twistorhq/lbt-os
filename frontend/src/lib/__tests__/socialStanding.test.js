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
    const stats = normalizeStats(FIXTURE_STANDING.newMember.stats)
    const progress = deriveProgress(stats, 'regular')
    expect(progress).toEqual([
      { key: 'nights', kind: 'count', current: 3, target: 5, met: false },
      { key: 'showRate', kind: 'rate', current: 0.75, target: 0.8, met: false },
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

  it('derives Keyholder progress with hosting, streak, and tenure bars', () => {
    const stats = normalizeStats(FIXTURE_STANDING.keyholder.stats)
    const keys = deriveProgress(stats, 'keyholder').map((c) => c.key)
    expect(keys).toEqual(['nights', 'wmaRate', 'wmaReviews', 'hostedClean', 'streak', 'tenureDays'])
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
  })

  it('serves fixtures only through the explicit fixture client', async () => {
    const standing = await fetchMyStanding(createFixtureStandingClient('keyholder'))
    expect(standing.activityTier).toBe('keyholder')
    expect(standing.nextTier).toBeNull()
  })
})
