// Prepared by Twistor Holdings LLC.
//
// TW-295: sample-mode axios interceptor — unauthenticated browsing serves
// fictional payloads; writes are blocked and bounced to sign-in.
import axios from 'axios'
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('../analytics', () => ({
  trackVisitorEvent: vi.fn(),
}))

import {
  installSampleInterceptor,
  setSampleMode,
  isSampleMode,
  setSampleWriteBlockedHandler,
  SAMPLE_WRITE_BLOCKED,
  __resetSampleModeForTests,
} from '../sampleMode'
import { SAMPLE_GETS, SAMPLE_LEADS } from '../sampleData'
import { trackVisitorEvent } from '../analytics'

function makeClient() {
  __resetSampleModeForTests()
  const client = axios.create({ baseURL: '/api/v1' })
  installSampleInterceptor([client], SAMPLE_GETS)
  return client
}

describe('sampleMode (TW-295)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('isSampleMode defaults to false', () => {
    __resetSampleModeForTests()
    expect(isSampleMode()).toBe(false)
  })

  test('mapped GET serves the sample payload with a 200', async () => {
    const client = makeClient()
    setSampleMode(true)
    const res = await client.get('/leads')
    expect(res.status).toBe(200)
    expect(res.data).toEqual(SAMPLE_LEADS)
    expect(res.data.length).toBeGreaterThan(0)
  })

  test('regex-mapped GET serves sample payloads', async () => {
    const client = makeClient()
    setSampleMode(true)
    const res = await client.get('/messages/channels/sample-ch-1/messages')
    expect(res.status).toBe(200)
    expect(Array.isArray(res.data.messages)).toBe(true)
  })

  test('unmapped GET passes through untouched in sample mode', async () => {
    const client = makeClient()
    setSampleMode(true)
    // Instance default adapter records real network attempts: the
    // interceptor must NOT short-circuit unmapped paths.
    client.defaults.adapter = () => Promise.reject(new Error('real-adapter-hit'))
    await expect(client.get('/admin/stats')).rejects.toThrow('real-adapter-hit')
  })

  test('writes are blocked in sample mode with SAMPLE_WRITE_BLOCKED', async () => {
    const client = makeClient()
    setSampleMode(true)
    const err = await client.post('/leads', { name: 'x' }).catch((e) => e)
    expect(err.code).toBe(SAMPLE_WRITE_BLOCKED)
  })

  test('blocked write fires the registered handler (sign-in bounce)', async () => {
    const client = makeClient()
    setSampleMode(true)
    const handler = vi.fn()
    setSampleWriteBlockedHandler(handler)
    await client.delete('/leads/abc').catch(() => {})
    expect(handler).toHaveBeenCalledTimes(1)
  })

  test('blocked write tracks signup_intent_click funnel event', async () => {
    const client = makeClient()
    setSampleMode(true)
    await client.post('/sales', {}).catch(() => {})
    expect(trackVisitorEvent).toHaveBeenCalledWith(
      'signup_intent_click',
      expect.objectContaining({ element: 'gated_write', endpoint: '/sales' }),
    )
  })

  test('sample mode off: requests pass through unmodified', async () => {
    const client = makeClient()
    setSampleMode(false)
    const handler = vi.fn()
    setSampleWriteBlockedHandler(handler)
    client.defaults.adapter = () => Promise.reject(new Error('real-adapter-hit'))
    await expect(client.post('/leads', {})).rejects.toThrow('real-adapter-hit')
    expect(handler).not.toHaveBeenCalled()
  })

  test('install is idempotent (no double interception)', async () => {
    __resetSampleModeForTests()
    const client = axios.create({ baseURL: '/api/v1' })
    installSampleInterceptor([client], SAMPLE_GETS)
    installSampleInterceptor([client], SAMPLE_GETS)
    setSampleMode(true)
    const res = await client.get('/customers')
    expect(res.status).toBe(200)
    expect(Array.isArray(res.data)).toBe(true)
  })
})
