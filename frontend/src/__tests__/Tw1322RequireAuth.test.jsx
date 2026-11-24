// Prepared by Twistor Holdings LLC.
//
// TW-1322: RequireAuth swallowed getToken() failures, stranding signed-in
// users on an endless "Loading..." with no retry and no way back to
// sign-in. Pin the fix: a token failure renders an error panel with a
// retry; the happy path still renders children.
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('@clerk/clerk-react', () => ({
  useAuth: vi.fn(),
  SignIn: () => null,
  SignUp: () => null,
}))

import { useAuth } from '@clerk/clerk-react'
import { RequireAuth } from '../App'

function renderGuarded() {
  return render(
    <MemoryRouter initialEntries={['/onboarding']}>
      <Routes>
        <Route
          path="/onboarding"
          element={(
            <RequireAuth>
              <div>protected content</div>
            </RequireAuth>
          )}
        />
        <Route path="/sign-in" element={<div>sign in page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RequireAuth token failure (TW-1322)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('a rejected getToken shows the error panel, not endless Loading', async () => {
    useAuth.mockReturnValue({
      isLoaded: true,
      isSignedIn: true,
      getToken: vi.fn().mockRejectedValue(new Error('clerk down')),
    })
    renderGuarded()

    expect(
      await screen.findByText('We could not finish signing you in'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to sign in' })).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  test('retry re-runs the token fetch and recovers', async () => {
    const getToken = vi.fn()
      .mockRejectedValueOnce(new Error('clerk down'))
      .mockResolvedValue('token-abc')
    useAuth.mockReturnValue({ isLoaded: true, isSignedIn: true, getToken })
    renderGuarded()

    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('protected content')).toBeInTheDocument()
    expect(getToken).toHaveBeenCalledTimes(2)
  })

  test('a resolved getToken renders children', async () => {
    useAuth.mockReturnValue({
      isLoaded: true,
      isSignedIn: true,
      getToken: vi.fn().mockResolvedValue('token-abc'),
    })
    renderGuarded()

    expect(await screen.findByText('protected content')).toBeInTheDocument()
  })
})
