// Prepared by Twistor Holdings LLC.
import {
  opportunityFor,
  OPPORTUNITY_FALLBACK,
  OPPORTUNITY_ESTIMATE_NOTE,
  pointInRing,
  featureContains,
  companiesInAlert,
  buildOpportunities,
} from './stormOpportunity'
import { SAMPLE_COMPANIES } from './tradeviewSample'

// Square around central Denver in GeoJSON order [lng, lat].
const DENVER_BOX = [
  [-105.1, 39.7],
  [-104.9, 39.7],
  [-104.9, 39.8],
  [-105.1, 39.8],
  [-105.1, 39.7],
]

const polyFeature = (event, coords = DENVER_BOX) => ({
  type: 'Feature',
  properties: { event, headline: `${event} headline` },
  geometry: { type: 'Polygon', coordinates: [coords] },
})

describe('opportunityFor', () => {
  test('maps NWS event types to revenue opportunities', () => {
    expect(opportunityFor('Tornado Warning').id).toBe('tornado')
    expect(opportunityFor('Severe Thunderstorm Warning').id).toBe('wind')
    expect(opportunityFor('Hail Storm Alert').id).toBe('hail')
    expect(opportunityFor('Flash Flood Warning').id).toBe('flood')
  })
  test('falls back to generic severe weather for unknown events', () => {
    expect(opportunityFor('Dense Fog Advisory')).toBe(OPPORTUNITY_FALLBACK)
    expect(opportunityFor('')).toBe(OPPORTUNITY_FALLBACK)
    expect(opportunityFor(null)).toBe(OPPORTUNITY_FALLBACK)
  })
  test('every opportunity carries trades, a ticket range, and an estimate disclaimer exists', () => {
    ;['Tornado Warning', 'Hail Alert', 'High Wind Warning', 'Flood Watch', 'Something Else'].forEach(
      (e) => {
        const o = opportunityFor(e)
        expect(o.trades.length).toBeGreaterThan(0)
        expect(o.ticketRange).toMatch(/\$/)
        expect(o.blurb).toBeTruthy()
      }
    )
    expect(OPPORTUNITY_ESTIMATE_NOTE).toMatch(/not quotes/i)
  })
})

describe('pointInRing / featureContains', () => {
  test('detects inside vs outside of a polygon ring', () => {
    expect(pointInRing(39.75, -105.0, DENVER_BOX)).toBe(true) // Mile High Air Pros
    expect(pointInRing(39.74, -104.88, DENVER_BOX)).toBe(false) // east of the box
    expect(pointInRing(40.0, -105.0, DENVER_BOX)).toBe(false) // north of the box
  })
  test('rejects degenerate rings', () => {
    expect(pointInRing(39.75, -105.0, [])).toBe(false)
    expect(pointInRing(39.75, -105.0, null)).toBe(false)
  })
  test('handles Polygon and MultiPolygon geometries', () => {
    expect(featureContains(polyFeature('Hail'), 39.75, -105.0)).toBe(true)
    expect(featureContains(polyFeature('Hail'), 40.0, -105.0)).toBe(false)
    const multi = {
      type: 'Feature',
      properties: {},
      geometry: { type: 'MultiPolygon', coordinates: [[DENVER_BOX]] },
    }
    expect(featureContains(multi, 39.75, -105.0)).toBe(true)
    expect(featureContains({ type: 'Feature', properties: {} }, 39.75, -105.0)).toBe(false)
  })
})

describe('companiesInAlert / buildOpportunities', () => {
  test('finds sample companies inside the swath', () => {
    const found = companiesInAlert(SAMPLE_COMPANIES, polyFeature('Hail'))
    const names = found.map((c) => c.name)
    expect(names).toContain('Mile High Air Pros')
    expect(names).toContain('Denver Current Electric')
    expect(names).not.toContain('Front Range Flow Plumbing')
  })
  test('buildOpportunities skips geometry-less features and computes surge', () => {
    const features = [
      polyFeature('Tornado Warning'),
      { type: 'Feature', properties: { event: 'Hail' }, geometry: null },
    ]
    const opps = buildOpportunities(features, SAMPLE_COMPANIES)
    expect(opps).toHaveLength(1)
    expect(opps[0].opportunity.id).toBe('tornado')
    expect(opps[0].affectedCount).toBe(2)
    expect(opps[0].affectedNames).toContain('Mile High Air Pros')
  })
  test('empty inputs produce empty output', () => {
    expect(buildOpportunities([], SAMPLE_COMPANIES)).toEqual([])
    expect(buildOpportunities(null, SAMPLE_COMPANIES)).toEqual([])
  })
})
