/**
 * TW-295 — public-first auth: sample-data mode for unauthenticated browsing.
 *
 * When sample mode is on (visitor not signed in), an axios request
 * interceptor:
 *  - GETs: serves curated fictional payloads from the SAMPLE_GETS map.
 *    Unmapped GETs pass through (they 404 on static hosting; pages show
 *    their existing error states).
 *  - Writes (POST/PATCH/PUT/DELETE): blocked. A registered handler fires
 *    (the app routes to sign-in with a post-auth return), the request is
 *    rejected with code SAMPLE_WRITE_BLOCKED, and a signup_intent_click
 *    funnel event is tracked.
 *
 * Prepared by Twistor Holdings LLC.
 */
import { trackVisitorEvent } from './analytics'

export const SAMPLE_WRITE_BLOCKED = 'SAMPLE_WRITE_BLOCKED'

let sampleMode = false
let writeBlockedHandler = null
let interceptorInstalled = false

export function setSampleMode(on) {
  sampleMode = !!on
}

export function isSampleMode() {
  return sampleMode
}

/** App registers a callback (with router access) fired on blocked writes. */
export function setSampleWriteBlockedHandler(fn) {
  writeBlockedHandler = fn
}

function lookupSample(sampleGets, url) {
  if (!url) return undefined
  for (const [matcher, payload] of sampleGets) {
    const hit =
      typeof matcher === 'string'
        ? url === matcher
        : matcher instanceof RegExp
          ? matcher.test(url)
          : false
    if (hit) return typeof payload === 'function' ? payload(url) : payload
  }
  return undefined
}

function sampleResponse(config, data) {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: { 'x-twistor-sample': 'true' },
    config,
  }
}

/**
 * Install the interceptor on the given axios instances exactly once.
 * sampleGets: [[matcher, payload], ...] from sampleData.js.
 */
export function installSampleInterceptor(axiosInstances, sampleGets) {
  if (interceptorInstalled) return
  interceptorInstalled = true

  axiosInstances.forEach((instance) => {
    instance.interceptors.request.use((config) => {
      if (!isSampleMode()) return config

      const method = (config.method || 'get').toLowerCase()

      if (method === 'get') {
        const payload = lookupSample(sampleGets, config.url)
        if (payload !== undefined) {
          config.adapter = () => Promise.resolve(sampleResponse(config, payload))
        }
        return config
      }

      // Writes are gated actions: bounce to sign-in, block the call.
      trackVisitorEvent('signup_intent_click', {
        element: 'gated_write',
        endpoint: config.url,
        method,
      })
      if (writeBlockedHandler) {
        try {
          writeBlockedHandler(config)
        } catch {
          /* navigation must never break the rejection path */
        }
      }
      const err = new Error('Sign in to save changes — this demo runs on sample data.')
      err.code = SAMPLE_WRITE_BLOCKED
      err.config = config
      return Promise.reject(err)
    })
  })
}

/** Test-only: reset module state between test cases. */
export function __resetSampleModeForTests() {
  sampleMode = false
  writeBlockedHandler = null
  interceptorInstalled = false
}
