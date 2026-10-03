import { useEffect, useRef, useState } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { useAuth, SignIn, SignUp } from '@clerk/clerk-react'
import { useQueryClient } from '@tanstack/react-query'
import { setAuthToken } from './lib/api'
import { setSampleMode, isSampleMode, setSampleWriteBlockedHandler } from './lib/sampleMode'
import { trackAppViewOnce, POST_AUTH_RETURN_KEY } from './lib/funnel'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/layout/Layout'
import MarketingHome from './pages/MarketingHome'
import AuthCallback from './pages/AuthCallback'
import Dashboard from './pages/Dashboard'
import Leads from './pages/Leads'
import Sales from './pages/Sales'
import Customers from './pages/Customers'
import Expenses from './pages/Expenses'
import AIInsights from './pages/AIInsights'
import Onboarding from './pages/Onboarding'
import Connections from './pages/Connections'
import Billing from './pages/Billing'
import Admin from './pages/Admin'
import Strategy from './pages/Strategy'
import Messages from './pages/Messages'
import RevenueIntelligence from './pages/RevenueIntelligence'
import TradeView from './pages/TradeView'
import MorningBrief from './components/brief/MorningBrief'
import AgentUIDemo from './pages/AgentUIDemo'
import NotFound from './pages/NotFound'

function AuthSync() {
  const { getToken, isSignedIn } = useAuth()

  useEffect(() => {
    if (!isSignedIn) return
    const sync = async () => {
      const token = await getToken()
      setAuthToken(token)
    }
    sync()
    const interval = setInterval(sync, 55_000)
    return () => clearInterval(interval)
  }, [getToken, isSignedIn])

  return null
}

function RequireAuth({ children }) {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const [tokenReady, setTokenReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    setTokenReady(false)

    if (!isLoaded || !isSignedIn) return

    getToken()
      .then((token) => {
        if (cancelled) return
        setAuthToken(token)
        setTokenReady(true)
      })
      .catch(() => {
        if (!cancelled) setTokenReady(false)
      })

    return () => {
      cancelled = true
    }
  }, [getToken, isLoaded, isSignedIn])

  if (!isLoaded) {
    return (
      <div className="flex h-screen items-center justify-center text-gray-400 text-sm">
        Loading...
      </div>
    )
  }
  if (!isSignedIn) return <Navigate to="/sign-in" replace />
  if (!tokenReady) {
    return (
      <div className="flex h-screen items-center justify-center text-gray-400 text-sm">
        Loading...
      </div>
    )
  }
  return children
}

/**
 * TW-295 — public-first auth. Sample mode follows Clerk state: signed out
 * visitors browse the app on fictional sample data (axios interceptor in
 * lib/sampleMode.js). Flipping the mode invalidates react-query caches so
 * no sample rows leak into a signed-in session and vice versa.
 */
function SampleModeSync() {
  const { isLoaded, isSignedIn } = useAuth()
  const queryClient = useQueryClient()
  const prevRef = useRef(null)

  useEffect(() => {
    if (!isLoaded) return
    const sample = !isSignedIn
    setSampleMode(sample)
    if (!isSignedIn) setAuthToken(null)
    if (prevRef.current !== null && prevRef.current !== sample) {
      queryClient.invalidateQueries()
    }
    prevRef.current = sample
  }, [isLoaded, isSignedIn, queryClient])

  return null
}

/**
 * TW-295 — gated writes: the sample-mode interceptor blocks POST/PATCH/PUT/
 * DELETE while unauthenticated. This bridge routes the visitor to sign-in
 * (with a post-auth return) instead of failing silently.
 */
function SampleWriteBridge() {
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    setSampleWriteBlockedHandler(() => {
      const here = `${location.pathname}${location.search}`
      try {
        sessionStorage.setItem(POST_AUTH_RETURN_KEY, here)
      } catch {
        /* private mode — return-to is best-effort */
      }
      navigate('/sign-in', { state: { returnTo: here } })
    })
    return () => setSampleWriteBlockedHandler(null)
  }, [navigate, location.pathname, location.search])

  return null
}

/** TW-295 — funnel: one app_view per session for sample-mode /app browsing. */
function AppViewTracker() {
  const location = useLocation()
  const { isLoaded, isSignedIn } = useAuth()

  useEffect(() => {
    if (isLoaded && !isSignedIn && location.pathname.startsWith('/app')) {
      trackAppViewOnce()
    }
  }, [isLoaded, isSignedIn, location.pathname])

  return null
}

export default function App() {
  // TW-294: BrowserRouter now lives in main.jsx (wrapping ClerkProvider) so
  // Clerk's post-sign-in redirects go through React Router and keep the
  // /lbt-os basename. App renders the route tree inside that Router.
  return (
    <ErrorBoundary>
      <AuthSync />
      <SampleModeSync />
      <SampleWriteBridge />
      <AppViewTracker />
      <Routes>
        <Route path="/" element={<MarketingHome />} />

        {/* TW-181: agent-generated dashboard demo (sample data, public) */}
        <Route path="/agent-ui-demo" element={<AgentUIDemo />} />

        {/* Clerk auth pages — hash routing avoids redirect loop in dev */}
        <Route
          path="/sign-in"
          element={
            <div className="flex h-screen items-center justify-center bg-gray-50">
              <SignIn
                routing="hash"
                fallbackRedirectUrl="/auth-callback"
                signUpUrl="/sign-up"
              />
            </div>
          }
        />
        <Route
          path="/sign-up"
          element={
            <div className="flex h-screen items-center justify-center bg-gray-50">
              <SignUp
                routing="hash"
                fallbackRedirectUrl="/auth-callback"
                signInUrl="/sign-in"
              />
            </div>
          }
        />

        {/* TW-295: post-auth landing pad — first-timers to onboarding,
            returning users back to their gated action */}
        <Route path="/auth-callback" element={<AuthCallback />} />

        {/* Onboarding — shown once after first sign-up */}
        <Route
          path="/onboarding"
          element={<RequireAuth><Onboarding /></RequireAuth>}
        />

        {/* Admin panel */}
        <Route path="/admin" element={<RequireAuth><Admin /></RequireAuth>} />

        {/* Main app — TW-295: public browsing on sample data, no auth wall */}
        <Route path="/app" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="connections" element={<Connections />} />
          <Route path="leads" element={<Leads />} />
          <Route path="sales" element={<Sales />} />
          <Route path="customers" element={<Customers />} />
          <Route path="expenses" element={<Expenses />} />
          <Route path="insights" element={<AIInsights />} />
          <Route path="brief" element={<MorningBrief />} />
          <Route path="billing" element={<Billing />} />
          <Route path="strategy" element={<Strategy />} />
          <Route path="revenue-intelligence" element={<RevenueIntelligence />} />
          <Route path="tradeview" element={<TradeView />} />
          <Route path="messages" element={<Messages />} />
        </Route>

        {/* 404 catch-all */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  )
}
