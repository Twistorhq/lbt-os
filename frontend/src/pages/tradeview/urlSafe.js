// Prepared by Twistor Holdings LLC.
//
// URL scheme allowlist for Trade View public-record link-outs.
// html-escaping is NOT scheme-safe (javascript: survives escaping), so only
// https: URLs become links — anything else renders as inert text.

export function safeHttpsUrl(raw) {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol === 'https:') return trimmed
    return null
  } catch {
    return null
  }
}
