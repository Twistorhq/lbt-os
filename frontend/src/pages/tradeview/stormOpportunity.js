// Prepared by Twistor Holdings LLC.
//
// Storm-opportunity logic for Trade View (TW-309).
//
// Flips the NWS storm layer from risk to revenue: incoming storm cells are
// projected as job opportunities with illustrative ticket-value ranges, and
// sample companies whose pins fall inside alert geometries become
// prospect surge alerts.
//
// Everything here is computed locally from live NWS alert geometries plus
// the sample dataset. Ticket ranges are ILLUSTRATIVE estimates for demo
// purposes — never quotes, never real pricing, never real businesses.

export const OPPORTUNITY_ESTIMATE_NOTE =
  'Illustrative ticket ranges — demo estimates, not quotes or real pricing.'

export const STORM_OPPORTUNITIES = [
  {
    id: 'tornado',
    match: /tornado/i,
    label: 'Tornado activity',
    trades: ['HVAC', 'Electrical', 'Plumbing'],
    ticketRange: '$4,000–$12,000',
    urgency: 'Emergency calls likely',
    blurb:
      'Tornado warnings drive emergency HVAC, electrical, and plumbing calls — systems knocked offline need same-day crews.',
  },
  {
    id: 'hail',
    match: /hail/i,
    label: 'Hail',
    trades: ['HVAC'],
    ticketRange: '$1,500–$6,000',
    urgency: 'Repair surge within 48 hours',
    blurb:
      'Hail dents condenser coils and fins — HVAC repair and replacement calls surge in the two days after the cell passes.',
  },
  {
    id: 'wind',
    match: /thunderstorm wind|damaging wind|high wind|severe thunderstorm/i,
    label: 'Damaging wind',
    trades: ['HVAC', 'Electrical'],
    ticketRange: '$800–$4,000',
    urgency: 'Same-day service calls',
    blurb:
      'Wind knocks condensers offline and drops lines — HVAC and electrical service calls spike while the alert is active.',
  },
  {
    id: 'flood',
    match: /flood/i,
    label: 'Flooding',
    trades: ['Plumbing'],
    ticketRange: '$1,200–$8,000',
    urgency: 'Emergency calls likely',
    blurb:
      'Flooding means sump pumps, water heaters, and drain emergencies — plumbers stay booked through the event.',
  },
]

export const OPPORTUNITY_FALLBACK = {
  id: 'other',
  label: 'Severe weather',
  trades: ['HVAC', 'Plumbing', 'Electrical'],
  ticketRange: '$500–$3,000',
  urgency: 'Watch for call volume',
  blurb:
    'Severe weather generally lifts service-call volume across the trades — staff the phones.',
}

/** Map an NWS alert event string to its revenue opportunity. */
export function opportunityFor(event) {
  const e = String(event || '')
  return STORM_OPPORTUNITIES.find((o) => o.match.test(e)) || OPPORTUNITY_FALLBACK
}

/**
 * Ray-casting point-in-polygon. `ring` is GeoJSON order: [[lng, lat], ...].
 * Returns true when the point is strictly inside the ring.
 */
export function pointInRing(lat, lng, ring) {
  if (!Array.isArray(ring) || ring.length < 4) return false
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = Number(ring[i][0])
    const yi = Number(ring[i][1])
    const xj = Number(ring[j][0])
    const yj = Number(ring[j][1])
    if (yi > lat !== yj > lat) {
      const xIntersect = ((xj - xi) * (lat - yi)) / (yj - yi) + xi
      if (lng < xIntersect) inside = !inside
    }
  }
  return inside
}

/** True when a lat/lng falls inside a GeoJSON Polygon or MultiPolygon feature. */
export function featureContains(feature, lat, lng) {
  const g = feature && feature.geometry
  if (!g || typeof lat !== 'number' || typeof lng !== 'number') return false
  if (g.type === 'Polygon') {
    return (g.coordinates || []).some((ring) => pointInRing(lat, lng, ring))
  }
  if (g.type === 'MultiPolygon') {
    return (g.coordinates || []).some((poly) =>
      (poly || []).some((ring) => pointInRing(lat, lng, ring))
    )
  }
  return false
}

/** Sample companies whose pins fall inside an alert geometry. */
export function companiesInAlert(companies, feature) {
  return (companies || []).filter(
    (c) => typeof c.lat === 'number' && typeof c.lng === 'number' && featureContains(feature, c.lat, c.lng)
  )
}

/**
 * Build the revenue-opportunity view of NWS alert features:
 * per-cell opportunity + which sample companies sit in the swath.
 */
export function buildOpportunities(alertFeatures, companies) {
  return (alertFeatures || [])
    .filter((f) => f && f.geometry)
    .map((f, i) => {
      const props = f.properties || {}
      const opp = opportunityFor(props.event)
      const affected = companiesInAlert(companies, f)
      return {
        id: `opp-${i}`,
        event: props.event || 'Severe weather',
        headline: props.headline || '',
        opportunity: opp,
        affectedCount: affected.length,
        affectedNames: affected.map((c) => c.name),
      }
    })
}
