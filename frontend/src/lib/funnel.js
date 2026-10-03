/**
 * TW-295 — public-first funnel events. All events flow through the existing
 * TW-209 visitor-events pipeline (lib/analytics.js trackVisitorEvent).
 * No parallel pipeline.
 *
 * Prepared by Twistor Holdings LLC.
 */
import { trackVisitorEvent } from './analytics'

export const POST_AUTH_RETURN_KEY = 'lbt_post_auth_return'

/** Fired on explicit signup CTAs: nav Sign in, Get started, gated actions. */
export function trackSignupIntent(element, page) {
  trackVisitorEvent('signup_intent_click', { element, page })
}

let appViewTracked = false

/** Fired once per session when an unauthenticated visitor browses /app/*. */
export function trackAppViewOnce() {
  if (appViewTracked) return
  appViewTracked = true
  trackVisitorEvent('app_view', { mode: 'sample' })
}

/** Fired when a brand-new user completes sign-up. */
export function trackSignupCompleted(returnTo) {
  trackVisitorEvent('signup_completed', { return_to: returnTo || '/onboarding' })
}

/** Test-only: reset module state between test cases. */
export function __resetFunnelForTests() {
  appViewTracked = false
}
