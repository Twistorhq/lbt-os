import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClerkProviderWithNavigate } from './auth/ClerkProviderWithNavigate'
import App from './App'
import './styles/index.css'
import './styles/twistor.css' // TW-159: Twistor Trades marketing design system

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

if (!PUBLISHABLE_KEY) {
  throw new Error('VITE_CLERK_PUBLISHABLE_KEY is not set. Copy .env.example to .env and fill in your key.')
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,       // 30 seconds
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

// TW-249: basename for the GitHub Pages subpath (/lbt-os). The Router must
// wrap ClerkProvider (TW-294) so Clerk's post-sign-in redirects go through
// React Router and keep the basename instead of full-page-loading to /onboarding.
const basename = import.meta.env.BASE_URL.replace(/\/$/, '')

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter basename={basename} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ClerkProviderWithNavigate publishableKey={PUBLISHABLE_KEY}>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </ClerkProviderWithNavigate>
    </BrowserRouter>
  </React.StrictMode>
)
