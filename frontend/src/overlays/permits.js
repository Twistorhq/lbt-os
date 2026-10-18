// Prepared by Twistor Holdings LLC
// Denver building-permit overlay (TW-446). Zero dependencies.
//
// Source: City and County of Denver, ArcGIS Hub — free, no key, CORS-enabled.
// NOTE: Denver retired its Socrata catalog (~2025). Socrata dataset IDs for
// Denver permits found in old docs are dead; these FeatureServers are the
// live source (verified 2026-10-12 — newest rows dated 2026-10-09).

import { fetchJson, cached, assertLatLon, OverlayError } from './http.js';

const ARCGIS = 'https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/ArcGIS/rest/services';
const LAYERS = Object.freeze({
  residential: `${ARCGIS}/ODC_DEV_RESIDENTIALCONSTPERMIT_P/FeatureServer/316/query`,
  commercial: `${ARCGIS}/ODC_DEV_COMMERCIALCONSTPERMIT_P/FeatureServer/317/query`,
});

export const PERMIT_SOURCE = Object.freeze({
  name: 'City and County of Denver — Open Data',
  url: 'https://opendata-geospatialdenver.hub.arcgis.com',
});

const PERMIT_TTL_MS = 15 * 60 * 1000;
const FIELDS = 'PERMIT_NUM,ADDRESS,CLASS,VALUATION,DATE_ISSUED,CONTRACTOR_NAME';

function buildQuery({ days, limit }) {
  const sinceEpochMs = Date.now() - days * 24 * 60 * 60 * 1000;
  const params = new URLSearchParams({
    where: `DATE_ISSUED >= ${sinceEpochMs}`,
    outFields: FIELDS,
    orderByFields: 'DATE_ISSUED DESC',
    resultRecordCount: String(limit),
    outSR: '4326', // WGS84 lat/lon — the service natively uses CO State Plane ft
    f: 'json',
  });
  return params.toString();
}

/**
 * Normalize one ArcGIS feature into a permit pin.
 * Permits without valid city addresses are geocoded by the city to 0,0 —
 * those are dropped (a pin in the Gulf of Guinea helps nobody).
 */
function normalizeFeature(feature, kind) {
  const a = feature.attributes || {};
  const g = feature.geometry || {};
  const lon = Number(g.x);
  const lat = Number(g.y);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || (lat === 0 && lon === 0)) {
    return null;
  }
  const issuedMs = Number(a.DATE_ISSUED);
  return {
    id: `${kind}:${a.PERMIT_NUM}`,
    permitNum: String(a.PERMIT_NUM ?? ''),
    address: String(a.ADDRESS ?? '').trim(),
    permitClass: String(a.CLASS ?? '').trim(),
    valuationUsd: Number.isFinite(Number(a.VALUATION)) ? Number(a.VALUATION) : null,
    issuedAt: Number.isFinite(issuedMs) ? new Date(issuedMs).toISOString() : null,
    contractor: String(a.CONTRACTOR_NAME ?? '').trim() || null,
    kind, // 'residential' | 'commercial'
    lat,
    lon,
  };
}

async function queryLayer(kind, queryString) {
  const url = `${LAYERS[kind]}?${queryString}`;
  const data = await fetchJson(url);
  if (data.error) {
    throw new OverlayError(`Denver permits (${kind}) query failed: ${data.error.message || 'unknown'}`, {
      source: PERMIT_SOURCE.name,
    });
  }
  const features = Array.isArray(data.features) ? data.features : [];
  return features.map((f) => normalizeFeature(f, kind)).filter(Boolean);
}

/**
 * Fetch recent Denver construction permits (residential + commercial).
 *
 * @param {object} opts
 * @param {number} [opts.days=30]   lookback window
 * @param {number} [opts.limit=150] max rows per layer (service caps at 2000)
 * @returns {Promise<{ permits, fetchedAt, source }>}
 *   permits: normalized pins, newest first. This is REAL public data —
 *   label it as such in the UI, with the "as of" timestamp.
 */
export async function fetchRecentPermits({ days = 30, limit = 150 } = {}) {
  const d = Math.min(Math.max(Math.floor(days) || 30, 1), 365);
  const l = Math.min(Math.max(Math.floor(limit) || 150, 1), 1000);
  const key = `permits:${d}:${l}`;
  return cached(key, PERMIT_TTL_MS, async () => {
    const qs = buildQuery({ days: d, limit: l });
    const [residential, commercial] = await Promise.all([
      queryLayer('residential', qs),
      queryLayer('commercial', qs),
    ]);
    const permits = [...residential, ...commercial].sort((a, b) =>
      String(b.issuedAt || '').localeCompare(String(a.issuedAt || ''))
    );
    return {
      permits,
      fetchedAt: new Date().toISOString(),
      source: PERMIT_SOURCE,
    };
  });
}

/**
 * Permits near a point, for "what's being built around here" questions.
 * Pure filter over fetchRecentPermits — no extra upstream calls.
 */
export async function fetchPermitsNear({ lat, lon, radiusMiles = 5, days = 90, limit = 400 } = {}) {
  assertLatLon(lat, lon);
  const r = Math.min(Math.max(Number(radiusMiles) || 5, 0.1), 50);
  const { permits, fetchedAt, source } = await fetchRecentPermits({ days, limit });
  const near = permits.filter((p) => haversineMiles(lat, lon, p.lat, p.lon) <= r);
  return { permits: near, fetchedAt, source, center: { lat, lon }, radiusMiles: r };
}

function haversineMiles(lat1, lon1, lat2, lon2) {
  const R = 3958.8;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}
