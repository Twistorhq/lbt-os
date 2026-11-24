import { useEffect, useRef, useState } from 'react'
import { Routes, Route, Navigate, Link, useNavigate, useLocation } from 'react-router-dom'
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
import BI from './pages/BI'
import Billing from './pages/Billing'
import Admin from './pages/Admin'
import Strategy from './pages/Strategy'
import Messages from './pages/Messages'
import RevenueIntelligence from './pages/RevenueIntelligence'
import TradeView from './pages/TradeView'
import ClientTradeView from './pages/tradeview/ClientTradeView'
import TradeViewEditions from './pages/tradeview/TradeViewEditions'
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

export function RequireAuth({ children }) {
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const [tokenReady, setTokenReady] = useState(false)
  const [tokenError, setTokenError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setTokenReady(false)
    setTokenError(false)

    if (!isLoaded || !isSignedIn) return

    getToken()
      .then((token) => {
        if (cancelled) return
        setAuthToken(token)
        setTokenReady(true)
      })
      .catch(() => {
        // TW-1322: this catch used to just reset tokenReady, leaving the
        // user on an endless "Loading..." with no way out. Surface it.
        if (!cancelled) setTokenError(true)
      })

    return () => {
      cancelled = true
    }
  }, [getToken, isLoaded, isSignedIn, attempt])

  if (!isLoaded) {
    return (
      <div className="flex h-screen items-center justify-center text-gray-400 text-sm">
        Loading...
      </div>
    )
  }
  if (!isSignedIn) return <Navigate to="/sign-in" replace />
  if (tokenError) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50 p-6">
        <div className="w-full max-w-sm rounded-2xl border border-rose-200 bg-white p-6 text-center shadow-sm">
          <h1 className="text-base font-semibold text-gray-900">We could not finish signing you in</h1>
          <p className="mt-2 text-sm leading-6 text-gray-500">
            Your session token did not load. Nothing was changed. Try again, or sign in once more.
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-3">
            <button
              type="button"
              onClick={() => setAttempt((a) => a + 1)}
              className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
            >
              Try again
            </button>
            <Link
              to="/sign-in"
              className="rounded-full border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
            >
              Back to sign in
            </Link>
          </div>
        </div>
      </div>
    )
  }
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
        <Route path="/tradeview/lite" element={<TradeViewEditions />} />

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
          <Route path="bi" element={<BI />} />
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
          <Route path="tradeview/client" element={<ClientTradeView />} />
          <Route path="messages" element={<Messages />} />
        </Route>

        {/* 404 catch-all */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  )
}
