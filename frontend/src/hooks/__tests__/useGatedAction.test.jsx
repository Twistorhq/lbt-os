// Prepared by Twistor Holdings LLC.
//
// TW-295: useGatedAction — auth only on explicit click. Unauthenticated
// click → signup_intent_click + bounce to /sign-in with post-auth return.
// Authenticated click → the action runs.
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('../../lib/funnel', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, trackSignupIntent: vi.fn() }
})
vi.mock('@clerk/clerk-react', () => ({
  useAuth: vi.fn(),
}))

import { useAuth } from '@clerk/clerk-react'
import { useGatedAction } from '../useGatedAction'
import { trackSignupIntent } from '../../lib/funnel'

function Probe({ label }) {
  const gated = useGatedAction()
  const location = useLocation()
  return (
    <>
      <span data-testid="path">{location.pathname}</span>
      <button
        type="button"
        data-testid="cta"
        onClick={() => gated(() => window.__actionRan?.(), label)}
      >
        gated
      </button>
    </>
  )
}

function renderProbe(authState, initialPath = '/app/leads') {
  useAuth.mockReturnValue(authState)
  window.__actionRan = vi.fn()
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Probe label="connect_quickbooks" />
    </MemoryRouter>,
  )
}

describe('useGatedAction (TW-295)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    delete window.__actionRan
  })

  test('unauthenticated click → tracks intent, stores return, routes to /sign-in', () => {
    renderProbe({ isLoaded: true, isSignedIn: false })
    fireEvent.click(screen.getByTestId('cta'))
    expect(trackSignupIntent).toHaveBeenCalledWith('connect_quickbooks', '/app/leads')
    expect(sessionStorage.getItem('lbt_post_auth_return')).toBe('/app/leads')
    expect(screen.getByTestId('path').textContent).toBe('/sign-in')
    expect(window.__actionRan).not.toHaveBeenCalled()
  })

  test('authenticated click → runs the action, no redirect, no intent event', () => {
    renderProbe({ isLoaded: true, isSignedIn: true })
    fireEvent.click(screen.getByTestId('cta'))
    expect(window.__actionRan).toHaveBeenCalledTimes(1)
    expect(trackSignupIntent).not.toHaveBeenCalled()
    expect(screen.getByTestId('path').textContent).toBe('/app/leads')
  })

  test('auth not loaded yet → action runs (fail-open; interceptor still guards writes)', () => {
    renderProbe({ isLoaded: false, isSignedIn: false })
    fireEvent.click(screen.getByTestId('cta'))
    expect(window.__actionRan).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('path').textContent).toBe('/app/leads')
  })
})
