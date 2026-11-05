/**
 * Standing client (TW-941, display half).
 *
 * The one live path to the tier and progress payload: the shared api
 * client (base /api/v1, Clerk token already injected by api.js) and
 * the contract documented in socialStanding.js. A response that does
 * not match the contract is an error here, never a silent fallback
 * to fixture data; the screen shows its error state instead.
 *
 * Until Zeke's backend branch (zeke/tw-941-feed-rewards) merges, the
 * endpoint is not live and callers that need a working surface today
 * pass createFixtureStandingClient explicitly. That choice stays
 * visible at the call site; nothing in production wiring uses it.
 *
 * Prepared by Twistor Holdings LLC
 */
import api from './api'
import { FIXTURE_STANDING, normalizeStanding } from './socialStanding'

export const STANDING_PATH = '/social/me/standing'

export async function fetchMyStanding(client = api) {
  const response = await client.get(STANDING_PATH)
  const standing = normalizeStanding(response.data)
  if (!standing) {
    throw new Error('standing response did not match the TW-941 contract')
  }
  return standing
}

/** Explicit fixture-backed client for tests and previews. */
export function createFixtureStandingClient(fixtureKey = 'regularClimbing') {
  return {
    async get(path) {
      if (path !== STANDING_PATH) throw new Error(`unknown fixture path ${path}`)
      const raw = FIXTURE_STANDING[fixtureKey]
      if (!raw) throw new Error(`unknown standing fixture ${fixtureKey}`)
      return { data: raw }
    },
  }
}
