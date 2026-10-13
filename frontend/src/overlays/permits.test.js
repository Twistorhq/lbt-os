// Prepared by Twistor Holdings LLC
// Tests for overlays/permits.js (TW-446).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchRecentPermits, fetchPermitsNear, PERMIT_SOURCE } from './permits.js';
import { clearOverlayCache } from './http.js';

const LAYER_URL =
  'https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/ArcGIS/rest/services/ODC_DEV_RESIDENTIALCONSTPERMIT_P/FeatureServer/316/query';

function arcgisResponse(features) {
  return { features };
}

const GOOD = {
  attributes: {
    PERMIT_NUM: '2026-RESCON-0003242',
    ADDRESS: '1645 N Locust ST',
    CLASS: 'Alteration/Tenant Finish',
    VALUATION: 4000,
    DATE_ISSUED: 1760486400000, // 2026-10-14T16:00:00Z
    CONTRACTOR_NAME: 'ACME HVAC LLC',
  },
  geometry: { x: -104.9143, y: 39.7427 },
};

const ZERO_ZERO = {
  attributes: { ...GOOD.attributes, PERMIT_NUM: 'BAD-1' },
  geometry: { x: 0, y: 0 },
};

beforeEach(() => {
  clearOverlayCache();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(handler) {
  vi.stubGlobal('fetch', vi.fn(handler));
}

describe('fetchRecentPermits', () => {
  it('normalizes both layers, newest first, drops 0,0 pins', async () => {
    stubFetch(async (url) => {
      const feats = url.includes('RESIDENTIALCONSTPERMIT') ? [GOOD, ZERO_ZERO] : [GOOD];
      return new Response(JSON.stringify(arcgisResponse(feats)), { status: 200 });
    });
    const { permits, fetchedAt, source } = await fetchRecentPermits({ days: 7, limit: 10 });
    expect(permits).toHaveLength(2); // 0,0 dropped
    expect(permits[0]).toMatchObject({
      permitNum: '2026-RESCON-0003242',
      address: '1645 N Locust ST',
      permitClass: 'Alteration/Tenant Finish',
      valuationUsd: 4000,
      contractor: 'ACME HVAC LLC',
      lat: 39.7427,
      lon: -104.9143,
    });
    expect(permits[0].issuedAt).toBe(new Date(1760486400000).toISOString());
    expect(typeof fetchedAt).toBe('string');
    expect(source).toEqual(PERMIT_SOURCE);
    expect(source.name).toMatch(/Denver/);
  });

  it('queries with epoch-ms where clause, WGS84 output, and desc ordering', async () => {
    const seen = [];
    stubFetch(async (url) => {
      seen.push(url);
      return new Response(JSON.stringify(arcgisResponse([])), { status: 200 });
    });
    await fetchRecentPermits({ days: 30, limit: 50 });
    expect(seen).toHaveLength(2);
    for (const url of seen) {
      // URLSearchParams encodes spaces as + and >= as %3E%3D
      expect(url).toMatch(/where=DATE_ISSUED\+%3E%3D\+\d+/);
      expect(url).toMatch(/outSR=4326/);
      expect(url).toMatch(/orderByFields=DATE_ISSUED\+DESC/);
      expect(url).toMatch(/resultRecordCount=50/);
      expect(url).not.toContain('undefined');
    }
    expect(seen[0]).toContain('RESIDENTIALCONSTPERMIT');
    expect(seen[1]).toContain('COMMERCIALCONSTPERMIT');
  });

  it('surfaces ArcGIS error payloads instead of returning empty', async () => {
    stubFetch(async () =>
      new Response(JSON.stringify({ error: { message: 'Invalid where clause' } }), { status: 200 })
    );
    await expect(fetchRecentPermits({ days: 7 })).rejects.toThrow(/Invalid where clause/);
  });

  it('clamps days/limit to sane bounds', async () => {
    const seen = [];
    stubFetch(async (url) => {
      seen.push(url);
      return new Response(JSON.stringify(arcgisResponse([])), { status: 200 });
    });
    await fetchRecentPermits({ days: 99999, limit: 99999 });
    expect(seen[0]).toMatch(/resultRecordCount=1000/);
  });
});

describe('fetchPermitsNear', () => {
  it('filters to the radius and validates coords', async () => {
    const near = { ...GOOD, geometry: { x: -104.99, y: 39.74 } };
    const far = { ...GOOD, attributes: { ...GOOD.attributes, PERMIT_NUM: 'FAR' }, geometry: { x: -105.5, y: 39.74 } };
    stubFetch(async (url) => {
      const feats = url.includes('RESIDENTIALCONSTPERMIT') ? [near, far] : [];
      return new Response(JSON.stringify(arcgisResponse(feats)), { status: 200 });
    });
    const { permits, center, radiusMiles } = await fetchPermitsNear({
      lat: 39.7392,
      lon: -104.9903,
      radiusMiles: 5,
      days: 30,
    });
    expect(permits.map((p) => p.permitNum)).toEqual(['2026-RESCON-0003242']);
    expect(center).toEqual({ lat: 39.7392, lon: -104.9903 });
    expect(radiusMiles).toBe(5);
  });

  it('rejects invalid coordinates before any fetch', async () => {
    const spy = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', spy);
    await expect(fetchPermitsNear({ lat: 999, lon: 0 })).rejects.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });
});
