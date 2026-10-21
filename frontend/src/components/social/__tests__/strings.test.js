// Prepared by Twistor Holdings LLC.
//
// TW-652: the English and Spanish catalogs ship together or not at
// all (TW-646 Section 3). Parity means identical key sets and a
// non-empty string at every leaf in both locales; a missing Spanish
// string is a build failure here, never a silent English fallback on
// a consent screen.
import { describe, it, expect } from 'vitest'
import {
  STRINGS,
  SUPPORTED_LOCALES,
  BIOMETRIC_POLICY_VERSION,
  RETENTION_SCHEDULE_VERSION,
} from '../strings'

function keyPaths(node, prefix = '') {
  return Object.entries(node).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object') return keyPaths(value, path)
    return [path]
  })
}

function leafAt(root, path) {
  return path.split('.').reduce((node, key) => (node ? node[key] : undefined), root)
}

describe('Social string catalog parity (TW-652)', () => {
  it('supports English and Spanish at launch', () => {
    expect(SUPPORTED_LOCALES).toEqual(['en', 'es'])
  })

  it('carries identical key sets in English and Spanish', () => {
    const en = keyPaths(STRINGS.en).sort()
    const es = keyPaths(STRINGS.es).sort()
    expect(es).toEqual(en)
    expect(en.length).toBeGreaterThan(50)
  })

  it('has a non-empty string at every leaf in both locales', () => {
    for (const path of keyPaths(STRINGS.en)) {
      for (const locale of SUPPORTED_LOCALES) {
        const value = leafAt(STRINGS[locale], path)
        expect(typeof value, `${locale}.${path}`).toBe('string')
        expect(value.trim().length, `${locale}.${path}`).toBeGreaterThan(0)
      }
    }
  })

  it('keeps the Spanish rendering distinct from the English source', () => {
    // A copied English string in the Spanish catalog is a stub wearing
    // a costume. Version lines and the badge are checked on content
    // keys where a translation must differ.
    for (const path of ['explain.title', 'schedule.title', 'grant.attest', 'declined.body']) {
      expect(leafAt(STRINGS.es, path)).not.toBe(leafAt(STRINGS.en, path))
    }
  })

  it('exports the consent and retention versions recorded in vfy_consents', () => {
    expect(BIOMETRIC_POLICY_VERSION).toBe('1.0')
    expect(RETENTION_SCHEDULE_VERSION).toBe('1.0')
  })

  it('states the legally load-bearing numbers in both locales', () => {
    // 72-hour photo ceiling and the 24-month dormancy rule (TW-654
    // Section 1.4 items 1 and 2) must survive translation intact.
    for (const locale of SUPPORTED_LOCALES) {
      expect(STRINGS[locale].schedule.photos).toContain('72')
      expect(STRINGS[locale].schedule.dormancy).toContain('24')
      expect(STRINGS[locale].dormant.body).toContain('24')
    }
  })
})
