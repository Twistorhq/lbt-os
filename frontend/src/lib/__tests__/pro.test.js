// Prepared by Twistor Holdings LLC
// TW-480: tests for the dev unlock flag (frontend/src/lib/pro.js)

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { isProUnlocked, isDevUnlocked, applyDevUnlockFromUrl } from '../pro.js'

const FLAG_KEY = 'twistor-dev-unlock'

function setUrl(search) {
  delete window.location
  window.location = {
    search,
    pathname: '/app/tradeview',
    hash: '',
  }
  window.history.replaceState = vi.fn()
}

describe('isProUnlocked', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('returns true when the dev flag is set, regardless of plan', () => {
    window.localStorage.setItem(FLAG_KEY, '1')
    expect(isProUnlocked({ plan: 'basic' })).toBe(true)
    expect(isProUnlocked(null)).toBe(true)
    expect(isProUnlocked(undefined)).toBe(true)
  })

  it('returns false for basic plan when flag is unset', () => {
    expect(isProUnlocked({ plan: 'basic' })).toBe(false)
    expect(isProUnlocked(null)).toBe(false)
    expect(isProUnlocked(undefined)).toBe(false)
  })

  it('returns true for pro/premium/enterprise plans without the flag', () => {
    expect(isProUnlocked({ plan: 'pro' })).toBe(true)
    expect(isProUnlocked({ plan: 'premium' })).toBe(true)
    expect(isProUnlocked({ plan: 'enterprise' })).toBe(true)
  })
})

describe('isDevUnlocked', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('reflects the flag only', () => {
    expect(isDevUnlocked()).toBe(false)
    window.localStorage.setItem(FLAG_KEY, '1')
    expect(isDevUnlocked()).toBe(true)
  })
})

describe('applyDevUnlockFromUrl', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('sets the flag on ?dev_unlock=1 and strips the param', () => {
    setUrl('?dev_unlock=1')
    applyDevUnlockFromUrl()
    expect(window.localStorage.getItem(FLAG_KEY)).toBe('1')
    expect(window.history.replaceState).toHaveBeenCalled()
    const cleanUrl = window.history.replaceState.mock.calls[0][2]
    expect(cleanUrl).not.toContain('dev_unlock')
  })

  it('clears the flag on ?dev_unlock=0', () => {
    window.localStorage.setItem(FLAG_KEY, '1')
    setUrl('?dev_unlock=0')
    applyDevUnlockFromUrl()
    expect(window.localStorage.getItem(FLAG_KEY)).toBeNull()
  })

  it('does nothing when the param is absent', () => {
    setUrl('?foo=bar')
    applyDevUnlockFromUrl()
    expect(window.localStorage.getItem(FLAG_KEY)).toBeNull()
    expect(window.history.replaceState).not.toHaveBeenCalled()
  })
})
