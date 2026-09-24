import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth, SignIn, SignUp } from '@clerk/clerk-react'
import { setAuthToken } from './lib/api'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/layout/Layout'
import MarketingHome from './pages/MarketingHome'
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

export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ErrorBoundary>
      <AuthSync />
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
                fallbackRedirectUrl="/onboarding"
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
                fallbackRedirectUrl="/onboarding"
                signInUrl="/sign-in"
              />
            </div>
          }
        />

        {/* Onboarding — shown once after first sign-up */}
        <Route
          path="/onboarding"
          element={<RequireAuth><Onboarding /></RequireAuth>}
        />

        {/* Admin panel */}
        <Route path="/admin" element={<Admin />} />

        {/* Main app */}
        <Route path="/app" element={<RequireAuth><Layout /></RequireAuth>}>
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
          <Route path="messages" element={<Messages />} />
        </Route>

        {/* 404 catch-all */}
        <Route path="*" element={<NotFound />} />
      </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  )
}
