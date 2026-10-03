/**
 * TW-295 — post-auth landing pad.
 *
 * Clerk's SignIn/SignUp redirect here (fallbackRedirectUrl). First-timers
 * go to /onboarding (as before); returning users go back to where they
 * were when a gated action bounced them to sign-in. New sign-ups fire
 * signup_completed into the TW-209 funnel.
 *
 * Prepared by Twistor Holdings LLC.
 */
import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUser } from '@clerk/clerk-react'
import { trackSignupCompleted, POST_AUTH_RETURN_KEY } from '../lib/funnel'

/** Considered "new" within 5 minutes of Clerk user creation. */
export const NEW_USER_WINDOW_MS = 5 * 60 * 1000

export function isNewUser(user, now = Date.now()) {
  if (!user?.createdAt) return false
  return now - new Date(user.createdAt).getTime() < NEW_USER_WINDOW_MS
}

export function resolvePostAuthDestination({ isNew, returnTo }) {
  if (isNew) return '/onboarding'
  return returnTo || '/onboarding'
}

function readReturnTo() {
  try {
    const value = sessionStorage.getItem(POST_AUTH_RETURN_KEY)
    sessionStorage.removeItem(POST_AUTH_RETURN_KEY)
    return value
  } catch {
    return null
  }
}

export default function AuthCallback() {
  const { isLoaded, isSignedIn, user } = useUser()
  const navigate = useNavigate()
  // Idempotency guard: the component unmounts on navigation in the real
  // app, but StrictMode / re-renders must never double-navigate (the
  // stored returnTo is consumed on first read).
  const doneRef = useRef(false)

  useEffect(() => {
    if (!isLoaded || doneRef.current) return
    if (!isSignedIn) {
      doneRef.current = true
      navigate('/sign-in', { replace: true })
      return
    }
    const returnTo = readReturnTo()
    const isNew = isNewUser(user)
    if (isNew) trackSignupCompleted(returnTo)
    doneRef.current = true
    navigate(resolvePostAuthDestination({ isNew, returnTo }), { replace: true })
  }, [isLoaded, isSignedIn, user, navigate])

  return (
    <div className="flex h-screen items-center justify-center bg-gray-50">
      <p className="text-sm text-gray-400">Finishing sign in…</p>
    </div>
  )
}
