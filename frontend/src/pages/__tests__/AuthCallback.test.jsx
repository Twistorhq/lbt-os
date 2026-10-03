// Prepared by Twistor Holdings LLC.
//
// TW-295: AuthCallback — first-timers to /onboarding (as before), returning
// users back to their gated action; new sign-ups fire signup_completed.
import { render, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('../../lib/funnel', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, trackSignupCompleted: vi.fn() }
})
vi.mock('@clerk/clerk-react', () => ({
  useUser: vi.fn(),
}))

import { useUser } from '@clerk/clerk-react'
import AuthCallback, {
  isNewUser,
  resolvePostAuthDestination,
  NEW_USER_WINDOW_MS,
} from '../AuthCallback'
import { trackSignupCompleted } from '../../lib/funnel'

function LocationProbe({ onLocation }) {
  const location = useLocation()
  onLocation(location.pathname)
  return null
}

function renderCallback(userState) {
  useUser.mockReturnValue(userState)
  let seen = null
  render(
    <MemoryRouter initialEntries={['/auth-callback']}>
      <Routes>
        <Route path="/auth-callback" element={<AuthCallback />} />
        <Route path="/app/tradeview" element={<div>tradeview</div>} />
        <Route path="/app/leads" element={<div>leads</div>} />
        <Route path="/onboarding" element={<div>onboarding</div>} />
        <Route path="/sign-in" element={<div>signin</div>} />
      </Routes>
      <LocationProbe onLocation={(p) => { seen = p }} />
    </MemoryRouter>,
  )
  return () => seen
}

describe('AuthCallback pure logic (TW-295)', () => {
  test('isNewUser: created seconds ago → true', () => {
    const now = Date.now()
    expect(isNewUser({ createdAt: new Date(now - 30_000) }, now)).toBe(true)
  })

  test('isNewUser: created long ago → false', () => {
    const now = Date.now()
    expect(isNewUser({ createdAt: new Date(now - NEW_USER_WINDOW_MS - 1000) }, now)).toBe(false)
  })

  test('isNewUser: missing user → false', () => {
    expect(isNewUser(null)).toBe(false)
    expect(isNewUser({})).toBe(false)
  })

  test('resolvePostAuthDestination: new user → /onboarding even with returnTo', () => {
    expect(resolvePostAuthDestination({ isNew: true, returnTo: '/app/leads' })).toBe('/onboarding')
  })

  test('resolvePostAuthDestination: returning user → returnTo', () => {
    expect(resolvePostAuthDestination({ isNew: false, returnTo: '/app/tradeview' })).toBe('/app/tradeview')
  })

  test('resolvePostAuthDestination: returning user, no returnTo → /onboarding (today)', () => {
    expect(resolvePostAuthDestination({ isNew: false, returnTo: null })).toBe('/onboarding')
  })
})

describe('AuthCallback routing (TW-295)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  test('new user with stored returnTo → /onboarding + signup_completed', async () => {
    sessionStorage.setItem('lbt_post_auth_return', '/app/leads')
    const getPath = renderCallback({
      isLoaded: true,
      isSignedIn: true,
      user: { createdAt: new Date(Date.now() - 10_000) },
    })
    await act(async () => {})
    expect(trackSignupCompleted).toHaveBeenCalledWith('/app/leads')
    expect(getPath()).toBe('/onboarding')
    expect(sessionStorage.getItem('lbt_post_auth_return')).toBeNull()
  })

  test('returning user with stored returnTo → back to the gated page, no signup event', async () => {
    sessionStorage.setItem('lbt_post_auth_return', '/app/tradeview')
    const getPath = renderCallback({
      isLoaded: true,
      isSignedIn: true,
      user: { createdAt: new Date(Date.now() - 30 * 24 * 3600_000) },
    })
    await act(async () => {})
    expect(trackSignupCompleted).not.toHaveBeenCalled()
    expect(getPath()).toBe('/app/tradeview')
  })

  test('unauthenticated visitor → /sign-in', async () => {
    const getPath = renderCallback({ isLoaded: true, isSignedIn: false, user: null })
    await act(async () => {})
    expect(getPath()).toBe('/sign-in')
  })
})
