// Prepared by Twistor Holdings LLC
// National Weather Service overlay (TW-446). Zero dependencies.
//
// Source: api.weather.gov — free, no key, CORS-enabled. NWS asks clients to
// identify via User-Agent; browsers send their own automatically (the header
// is forbidden to set from fetch), so nothing to configure.

import { fetchJson, cached, assertLatLon } from './http.js';

const NWS = 'https://api.weather.gov';

export const NWS_SOURCE = Object.freeze({
  name: 'National Weather Service',
  url: 'https://www.weather.gov',
});

const ALERTS_TTL_MS = 5 * 60 * 1000;
const CONDITIONS_TTL_MS = 10 * 60 * 1000;
const POINTS_TTL_MS = 60 * 60 * 1000;

function normalizeAlert(feature) {
  const p = feature.properties || {};
  return {
    id: String(feature.id || p.id || ''),
    event: String(p.event || 'Weather alert'),
    severity: String(p.severity || 'Unknown'),
    certainty: String(p.certainty || ''),
    headline: String(p.headline || ''),
    description: String(p.description || ''),
    instruction: String(p.instruction || ''),
    onset: p.onset || null,
    expires: p.expires || null,
    geometry: feature.geometry || null, // GeoJSON, may be null for some alerts
  };
}

/**
 * Active NWS alerts for a point.
 * @returns {Promise<{ alerts, fetchedAt, source }>} — alerts is REAL public
 *   data; label it as such. Empty array is a valid answer (no active alerts).
 */
export async function fetchAlerts({ lat, lon } = {}) {
  assertLatLon(lat, lon);
  const key = `nws-alerts:${lat.toFixed(4)},${lon.toFixed(4)}`;
  return cached(key, ALERTS_TTL_MS, async () => {
    const data = await fetchJson(`${NWS}/alerts/active?point=${lat},${lon}`);
    const features = Array.isArray(data.features) ? data.features : [];
    return {
      alerts: features.map(normalizeAlert),
      fetchedAt: new Date().toISOString(),
      source: NWS_SOURCE,
    };
  });
}

async function resolveStation({ lat, lon }) {
  const key = `nws-points:${lat.toFixed(4)},${lon.toFixed(4)}`;
  return cached(key, POINTS_TTL_MS, async () => {
    const point = await fetchJson(`${NWS}/points/${lat},${lon}`);
    const stationsUrl = point?.properties?.observationStations;
    if (!stationsUrl) throw new Error('NWS points response missing observationStations');
    const stations = await fetchJson(stationsUrl);
    const first = stations?.features?.[0];
    const stationId = first?.properties?.stationIdentifier;
    if (!stationId) throw new Error('NWS returned no observation stations');
    return stationId;
  });
}

/**
 * Current conditions from the nearest NWS observation station.
 * @returns {Promise<{ tempF, humidityPct, windMph, windDirDeg, condition,
 *   observedAt, stationId, fetchedAt, source }>} — REAL public data.
 *   Any field may be null when the station doesn't report it.
 */
export async function fetchCurrentConditions({ lat, lon } = {}) {
  assertLatLon(lat, lon);
  const key = `nws-conditions:${lat.toFixed(4)},${lon.toFixed(4)}`;
  return cached(key, CONDITIONS_TTL_MS, async () => {
    const stationId = await resolveStation({ lat, lon });
    const obs = await fetchJson(`${NWS}/stations/${stationId}/observations/latest`);
    const p = obs?.properties || {};
    const cToF = (c) => (typeof c === 'number' ? Math.round((c * 9) / 5 + 32) : null);
    const kmhToMph = (k) => (typeof k === 'number' ? Math.round(k * 0.621371) : null);
    return {
      tempF: cToF(p.temperature?.value),
      humidityPct:
        typeof p.relativeHumidity?.value === 'number' ? Math.round(p.relativeHumidity.value) : null,
      windMph: kmhToMph(p.windSpeed?.value),
      windDirDeg: typeof p.windDirection?.value === 'number' ? p.windDirection.value : null,
      condition: String(p.textDescription || ''),
      observedAt: p.timestamp || null,
      stationId,
      fetchedAt: new Date().toISOString(),
      source: NWS_SOURCE,
    };
  });
}
