/**
 * TW-295 — gated actions: auth happens only on explicit user click.
 *
 * Wrap any action that reads/writes real data (Connect your tools, Save,
 * checkout…). Unauthenticated click → tracks signup_intent_click, stores a
 * post-auth return path, and routes to sign-in. Authenticated → runs it.
 *
 * Prepared by Twistor Holdings LLC.
 */
import { useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '@clerk/clerk-react'
import { trackSignupIntent, POST_AUTH_RETURN_KEY } from '../lib/funnel'

export function useGatedAction() {
  const { isSignedIn, isLoaded } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  return useCallback(
    (action, intentLabel = 'gated_action') => {
      const page = location.pathname
      if (isLoaded && !isSignedIn) {
        trackSignupIntent(intentLabel, page)
        try {
          sessionStorage.setItem(POST_AUTH_RETURN_KEY, page)
        } catch {
          /* private mode — return-to is best-effort */
        }
        navigate('/sign-in', { state: { returnTo: page } })
        return
      }
      if (typeof action === 'function') action()
    },
    [isSignedIn, isLoaded, navigate, location.pathname],
  )
}
