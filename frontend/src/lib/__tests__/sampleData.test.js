// Prepared by Twistor Holdings LLC.
//
// TW-295: sample datasets must be complete (every GET the browse pages call
// is covered) and fictional (no real-looking PII anywhere).
import { describe, test, expect } from 'vitest'
import { SAMPLE_GETS } from '../sampleData'

const REQUIRED_PATHS = [
  '/organizations/me',
  '/organizations/workspace-status',
  '/metrics/dashboard',
  '/metrics/revenue-trend',
  '/metrics/segments',
  '/metrics/forecast',
  '/leads',
  '/customers',
  '/sales',
  '/expenses',
  '/revenue-intelligence/ltv',
  '/revenue-intelligence/stage-velocity',
  '/revenue-intelligence/win-loss',
  '/revenue-intelligence/data-quality',
  '/revenue-intelligence/expansion',
  '/revenue-intelligence/speed-to-lead',
  '/revenue-intelligence/stage-aging',
  '/messages/bots',
  '/messages/channels',
  '/messages/channels/abc123/messages',
  '/integrations/overview',
  '/leaks/brief',
  '/audit/latest',
  '/audit/history',
  '/strategy/briefing',
]

function matches(path) {
  for (const [matcher, payload] of SAMPLE_GETS) {
    const hit =
      typeof matcher === 'string' ? path === matcher : matcher.test(path)
    if (hit) return payload
  }
  return undefined
}

function collectStrings(value, out = []) {
  if (typeof value === 'string') out.push(value)
  else if (Array.isArray(value)) value.forEach((v) => collectStrings(v, out))
  else if (value && typeof value === 'object')
    Object.values(value).forEach((v) => collectStrings(v, out))
  return out
}

describe('sampleData (TW-295)', () => {
  test('every browse-page GET is covered by the sample map', () => {
    const missing = REQUIRED_PATHS.filter((p) => matches(p) === undefined)
    expect(missing).toEqual([])
  })

  test('payloads carry the is_demo convention flag', () => {
    const flagged = [
      '/organizations/me',
      '/organizations/workspace-status',
      '/metrics/dashboard',
      '/metrics/segments',
      '/metrics/forecast',
      '/revenue-intelligence/ltv',
      '/leaks/brief',
      '/audit/latest',
      '/strategy/briefing',
    ]
    for (const p of flagged) {
      expect(matches(p).is_demo, p).toBe(true)
    }
  })

  test('no real-looking PII: phones are 555, emails are .example', () => {
    const strings = collectStrings(SAMPLE_GETS.map(([, p]) => p))
    const phones = strings.filter((s) => /\(\d{3}\)/.test(s))
    expect(phones.length).toBeGreaterThan(0)
    for (const phone of phones) {
      expect(phone, `non-555 phone: ${phone}`).toMatch(/\(555\)/)
    }
    const emails = strings.filter((s) => s.includes('@'))
    expect(emails.length).toBeGreaterThan(0)
    for (const email of emails) {
      expect(email, `non-example email: ${email}`).toMatch(/@example\.com$/)
    }
  })

  test('list payloads are arrays with ids', () => {
    for (const p of ['/leads', '/customers', '/sales', '/expenses']) {
      const payload = matches(p)
      expect(Array.isArray(payload), p).toBe(true)
      expect(payload.length, p).toBeGreaterThan(0)
      for (const row of payload) expect(row.id, p).toBeTruthy()
    }
  })
})
