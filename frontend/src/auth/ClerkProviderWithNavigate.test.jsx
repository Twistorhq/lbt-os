// Prepared by Twistor Holdings LLC.
//
// TW-294: Clerk must navigate through React Router so post-sign-in redirects
// keep the /lbt-os basename (no window.location full-page load to /onboarding).
import { render, act } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'

let capturedProps = null

vi.mock('@clerk/clerk-react', () => ({
  ClerkProvider: (props) => {
    capturedProps = props
    return <>{props.children}</>
  },
}))

import { ClerkProviderWithNavigate } from './ClerkProviderWithNavigate'

function LocationProbe({ onLocation }) {
  const location = useLocation()
  onLocation(location)
  return null
}

describe('ClerkProviderWithNavigate (TW-294)', () => {
  test('passes a navigate function to ClerkProvider', () => {
    render(
      <MemoryRouter initialEntries={['/sign-in']}>
        <ClerkProviderWithNavigate publishableKey="pk_test_dummy">
          <div>child</div>
        </ClerkProviderWithNavigate>
      </MemoryRouter>
    )
    expect(typeof capturedProps.navigate).toBe('function')
  })

  test('Clerk redirect navigates client-side (no full-page load, basename preserved)', () => {
    let seen = null
    const windowPathBefore = window.location.pathname
    render(
      <MemoryRouter initialEntries={['/lbt-os/sign-in']} basename="/lbt-os">
        <ClerkProviderWithNavigate publishableKey="pk_test_dummy">
          <LocationProbe onLocation={(loc) => { seen = loc }} />
        </ClerkProviderWithNavigate>
      </MemoryRouter>
    )
    // Simulate Clerk's post-sign-in redirect to fallbackRedirectUrl
    act(() => {
      capturedProps.navigate('/onboarding')
    })
    // Client-side route changed (basename stripped by the Router, as in the app)...
    expect(seen.pathname).toBe('/onboarding')
    // ...without touching window.location (no full-page load)
    expect(window.location.pathname).toBe(windowPathBefore)
  })

  test('keeps the Clerk URL props Clerk needs for auth routing', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <ClerkProviderWithNavigate publishableKey="pk_test_dummy">
          <div>child</div>
        </ClerkProviderWithNavigate>
      </MemoryRouter>
    )
    expect(capturedProps.publishableKey).toBe('pk_test_dummy')
    expect(capturedProps.signInUrl).toBe('/sign-in')
    expect(capturedProps.signUpUrl).toBe('/sign-up')
    expect(capturedProps.fallbackRedirectUrl).toBe('/onboarding')
  })
})
