// Prepared by Twistor Holdings LLC
//
// Pro/owner gating (TW-480 dev flag + TW-484 owner SSO).
//
// Two ways to unlock every Pro-gated feature:
//   1. Owner SSO (TW-484): sign in with an owner email (OWNER_EMAILS
//      allowlist, server-side). Backend sets org.is_owner; every gate opens,
//      frontend AND backend. This is the real path.
//   2. Dev flag (TW-480): ?dev_unlock=1 unlocks Pro UI in this browser for
//      testing (persists in localStorage); ?dev_unlock=0 re-locks.
//      Client-side only — Pro API calls still 403 without a Pro plan.
//
// Usage: replace every inline `org?.plan === 'pro' || ...` with isProUnlocked(org).

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
// Owner SSO wins first, then dev flag, then the real plan.
export function isProUnlocked(org) {
  if (org?.is_owner) return true // TW-484
  if (isDevUnlocked()) return true // TW-480
  const plan = org?.plan
  return plan === 'pro' || plan === 'premium' || plan === 'enterprise'
}
