import { useCallback, useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@clerk/clerk-react'
import Sidebar from './Sidebar'
import SampleBanner from '../SampleBanner'
import { TwistorMark } from '../icons'

export default function Layout() {
  // TW-295: unauthenticated visitors browse the app shell on sample data.
  const { isLoaded, isSignedIn } = useAuth()
  const showSampleBanner = isLoaded && !isSignedIn

  // TW-899: on phones the sidebar becomes an off-canvas drawer behind a
  // top bar; at md and up it is the same sticky column as always.
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const closeMobileNav = useCallback(() => setMobileNavOpen(false), [])
  const location = useLocation()

  useEffect(() => {
    closeMobileNav()
  }, [location.pathname, closeMobileNav])

  useEffect(() => {
    if (!mobileNavOpen) return
    const onKeyDown = (event) => {
      if (event.key === 'Escape') closeMobileNav()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mobileNavOpen, closeMobileNav])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[linear-gradient(180deg,#fbfdff_0%,#eef3f8_100%)] md:flex-row">
      {/* TW-899: mobile top bar (phones only) */}
      <header className="flex items-center gap-3 bg-slate-950 px-4 py-3 text-white md:hidden">
        <button
          type="button"
          onClick={() => setMobileNavOpen(true)}
          aria-expanded={mobileNavOpen}
          aria-controls="app-sidebar"
          aria-label="Open navigation"
          className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/10 text-lg text-white transition-colors hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gold-300"
        >
          <span aria-hidden="true">☰</span>
        </button>
        <div className="flex items-center gap-2.5">
          <TwistorMark className="h-8 w-8" />
          <div className="text-[15px] font-bold tracking-tight">
            Twistor <span className="font-medium text-white/60">Trades</span>
          </div>
        </div>
      </header>

      {mobileNavOpen && (
        <div
          aria-hidden="true"
          onClick={closeMobileNav}
          className="fixed inset-0 z-30 bg-slate-950/50 md:hidden"
        />
      )}

      <Sidebar mobileOpen={mobileNavOpen} onClose={closeMobileNav} />

      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[86rem] px-4 py-6 sm:px-5 xl:px-7">
          {showSampleBanner && <SampleBanner />}
          <Outlet />
        </div>
      </main>
    </div>
  )
}
