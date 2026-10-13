// Prepared by Twistor Holdings LLC
//
// Dev unlock flag (TW-480) — lets the builder test every Pro-gated feature
// without paying. Reversible at any time; no rebuild required.
//
// Usage:
//   ?dev_unlock=1   — unlock all Pro features in this browser (persists)
//   ?dev_unlock=0   — re-lock (back to real plan checks)
//
// The flag lives in localStorage ('twistor-dev-unlock'). It only affects
// client-side feature gates — real billing/entitlements still run through
// Stripe server-side. Remove this file before public launch.

const FLAG_KEY = 'twistor-dev-unlock'

function readFlag() {
  try {
    return window.localStorage.getItem(FLAG_KEY) === '1'
  } catch {
    return false
  }
}

function writeFlag(on) {
  try {
    if (on) window.localStorage.setItem(FLAG_KEY, '1')
    else window.localStorage.removeItem(FLAG_KEY)
  } catch {
    /* storage unavailable — flag silently inert */
  }
}

// Call once at app boot (e.g. in main.jsx). Reads ?dev_unlock=1|0,
// persists the choice, and strips the param from the URL.
export function applyDevUnlockFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search)
    if (!params.has('dev_unlock')) return
    writeFlag(params.get('dev_unlock') === '1')
    params.delete('dev_unlock')
    const qs = params.toString()
    const clean = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash
    window.history.replaceState(null, '', clean)
  } catch {
    /* non-browser or malformed URL — ignore */
  }
}

export function isDevUnlocked() {
  try {
    return readFlag()
  } catch {
    return false
  }
}

// Drop-in replacement for every inline `org?.plan === 'pro' || ...` check.
// Dev flag wins; otherwise falls back to the real plan.
export function isProUnlocked(org) {
  if (isDevUnlocked()) return true
  const plan = org?.plan
  return plan === 'pro' || plan === 'premium' || plan === 'enterprise'
}
