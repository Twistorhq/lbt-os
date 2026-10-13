// Prepared by Twistor Holdings LLC
// Hardened fetch primitives for the public overlay modules (TW-446).
// Zero dependencies. No framework imports.

/**
 * Exact hosts the overlay modules are allowed to fetch from.
 * SSRF hardening: endpoints are module constants, never caller-supplied URLs,
 * and even those constants must resolve to one of these hosts over HTTPS.
 */
export const ALLOWED_HOSTS = Object.freeze(
  new Set(['api.weather.gov', 'services1.arcgis.com'])
);

export class OverlayError extends Error {
  constructor(message, { source, cause } = {}) {
    super(message);
    this.name = 'OverlayError';
    this.source = source ?? null;
    this.cause = cause ?? null;
  }
}

/**
 * Validate that a URL string is safe to fetch: HTTPS + allowlisted host.
 * Throws OverlayError otherwise.
 */
export function assertAllowedUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new OverlayError(`Refusing to fetch malformed URL: ${url}`);
  }
  if (parsed.protocol !== 'https:') {
    throw new OverlayError(`Refusing non-HTTPS URL: ${parsed.protocol}//${parsed.host}`);
  }
  if (!ALLOWED_HOSTS.has(parsed.hostname)) {
    throw new OverlayError(`Host not on the overlay allowlist: ${parsed.hostname}`);
  }
  return parsed;
}

/**
 * Validate a lat/lon pair. Throws OverlayError on anything non-numeric or
 * out of range — coordinates are interpolated into URLs, so this is the
 * injection boundary.
 */
export function assertLatLon(lat, lon) {
  for (const [name, v, min, max] of [
    ['lat', lat, -90, 90],
    ['lon', lon, -180, 180],
  ]) {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) {
      throw new OverlayError(`Invalid ${name}: ${String(v)}`);
    }
  }
  return { lat, lon };
}

/**
 * fetch() with timeout, host allowlist, and a response byte cap.
 * Returns parsed JSON. Throws OverlayError on network failure, timeout,
 * non-2xx, oversize body, or invalid JSON.
 */
export async function fetchJson(url, { timeoutMs = 10_000, maxBytes = 2_000_000 } = {}) {
  assertAllowedUrl(url);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(url, { signal: controller.signal });
  } catch (err) {
    throw new OverlayError(`Fetch failed: ${url}`, { cause: err });
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    throw new OverlayError(`Upstream responded ${res.status} for ${url}`);
  }
  const text = await res.text();
  if (text.length > maxBytes) {
    throw new OverlayError(`Upstream response exceeded ${maxBytes} bytes for ${url}`);
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new OverlayError(`Upstream returned invalid JSON for ${url}`, { cause: err });
  }
}

// ---------------------------------------------------------------------------
// Tiny TTL cache. In-memory, per-process. Keeps us polite to free public APIs:
// repeated map toggles within the TTL don't re-hit the upstream.
// ---------------------------------------------------------------------------

const _cache = new Map(); // key -> { expiresAt, value }

/**
 * Run `fn` at most once per `ttlMs` for a given key; serve cached after that.
 * Failures are NOT cached — a down source is retried on the next call.
 */
export async function cached(key, ttlMs, fn) {
  const now = Date.now();
  const hit = _cache.get(key);
  if (hit && hit.expiresAt > now) return hit.value;
  const value = await fn();
  _cache.set(key, { expiresAt: now + ttlMs, value });
  return value;
}

/** Test seam: drop the whole cache (used by unit tests). */
export function clearOverlayCache() {
  _cache.clear();
}

/**
 * Graceful-degradation wrapper. Resolves to
 *   { ok: true, data }  on success
 *   { ok: false, error } on OverlayError (or anything else)
 * so UI layers can render a clearly-labeled fallback instead of throwing.
 * One bad source never kills the map — the self-healing rule.
 */
export async function safeFetch(fn) {
  try {
    return { ok: true, data: await fn() };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}
