/**
 * TW-295 — persistent banner for unauthenticated app browsing.
 * Keyboard-reachable CTA with visible focus; no motion.
 *
 * Prepared by Twistor Holdings LLC.
 */
import { Link, useLocation } from 'react-router-dom'
import { trackSignupIntent } from '../lib/funnel'

export default function SampleBanner() {
  const location = useLocation()

  return (
    <div
      role="region"
      aria-label="Sample data notice"
      className="mb-5 flex flex-wrap items-center gap-3 rounded-2xl border border-amber-300/40 bg-amber-50 px-5 py-3"
    >
      <span aria-hidden="true" className="text-lg text-amber-500">
        ◈
      </span>
      <p className="text-sm text-amber-900">
        <span className="font-semibold">Sample data</span> — you&apos;re browsing a
        demo workspace. Nothing here is real and nothing is saved.
      </p>
      <Link
        to="/sign-in"
        onClick={() => trackSignupIntent('sample_banner_sign_in', location.pathname)}
        className="ml-auto inline-flex items-center rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
      >
        Sign in to connect your business
      </Link>
    </div>
  )
}
