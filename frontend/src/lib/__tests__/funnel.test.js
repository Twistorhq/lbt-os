// Prepared by Twistor Holdings LLC.
//
// TW-295: funnel events flow through the existing TW-209 visitor-events
// pipeline — signup_intent_click, app_view (once per session),
// signup_completed.
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('../analytics', () => ({
  trackVisitorEvent: vi.fn(),
}))

import {
  trackSignupIntent,
  trackAppViewOnce,
  trackSignupCompleted,
  POST_AUTH_RETURN_KEY,
  __resetFunnelForTests,
} from '../funnel'
import { trackVisitorEvent } from '../analytics'

describe('funnel (TW-295)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    __resetFunnelForTests()
  })

  test('trackSignupIntent fires signup_intent_click with element + page', () => {
    trackSignupIntent('sidebar_sign_in', '/app/tradeview')
    expect(trackVisitorEvent).toHaveBeenCalledWith('signup_intent_click', {
      element: 'sidebar_sign_in',
      page: '/app/tradeview',
    })
  })

  test('trackAppViewOnce fires app_view exactly once per session', () => {
    trackAppViewOnce()
    trackAppViewOnce()
    trackAppViewOnce()
    expect(trackVisitorEvent).toHaveBeenCalledTimes(1)
    expect(trackVisitorEvent).toHaveBeenCalledWith('app_view', { mode: 'sample' })
  })

  test('trackSignupCompleted fires signup_completed with return_to', () => {
    trackSignupCompleted('/app/leads')
    expect(trackVisitorEvent).toHaveBeenCalledWith('signup_completed', {
      return_to: '/app/leads',
    })
  })

  test('trackSignupCompleted defaults return_to to /onboarding', () => {
    trackSignupCompleted(null)
    expect(trackVisitorEvent).toHaveBeenCalledWith('signup_completed', {
      return_to: '/onboarding',
    })
  })

  test('POST_AUTH_RETURN_KEY is stable', () => {
    expect(POST_AUTH_RETURN_KEY).toBe('lbt_post_auth_return')
  })
})
