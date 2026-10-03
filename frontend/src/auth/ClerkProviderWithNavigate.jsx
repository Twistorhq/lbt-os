// Prepared by Twistor Holdings LLC.
//
// TW-294: Clerk must navigate through React Router so the /lbt-os basename
// is preserved. Without the `navigate` prop, Clerk falls back to
// window.location with the raw path (e.g. /onboarding), which drops the
// basename and 404s on GitHub Pages. This component must be rendered INSIDE
// a Router (BrowserRouter lives in main.jsx) so useNavigate() resolves.
import { ClerkProvider } from '@clerk/clerk-react'
import { useNavigate } from 'react-router-dom'

export function ClerkProviderWithNavigate({ publishableKey, children }) {
  const navigate = useNavigate()
  return (
    <ClerkProvider
      publishableKey={publishableKey}
      navigate={(to) => navigate(to)}
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      fallbackRedirectUrl="/onboarding"
    >
      {children}
    </ClerkProvider>
  )
}
