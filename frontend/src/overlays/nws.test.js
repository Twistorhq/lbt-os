// Prepared by Twistor Holdings LLC
// Tests for overlays/nws.js (TW-446).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchAlerts, fetchCurrentConditions, NWS_SOURCE } from './nws.js';
import { clearOverlayCache } from './http.js';

beforeEach(() => {
  clearOverlayCache();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ALERT = {
  id: 'https://api.weather.gov/alerts/urn:oid:1',
  geometry: { type: 'Polygon', coordinates: [] },
  properties: {
    event: 'Winter Storm Warning',
    severity: 'Severe',
    certainty: 'Likely',
    headline: 'Heavy snow expected tonight',
    description: 'Snow accumulations 6-12 inches.',
    instruction: 'Stay home.',
    onset: '2026-10-12T18:00:00-06:00',
    expires: '2026-10-13T12:00:00-06:00',
  },
};

describe('fetchAlerts', () => {
  it('normalizes alerts with attribution and fetchedAt', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        expect(url).toMatch(/^https:\/\/api\.weather\.gov\/alerts\/active\?point=39\.73/);
        return new Response(JSON.stringify({ features: [ALERT] }), { status: 200 });
      })
    );
    const { alerts, fetchedAt, source } = await fetchAlerts({ lat: 39.7392, lon: -104.9903 });
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toMatchObject({
      event: 'Winter Storm Warning',
      severity: 'Severe',
      headline: 'Heavy snow expected tonight',
    });
    expect(alerts[0].geometry).not.toBeNull();
    expect(typeof fetchedAt).toBe('string');
    expect(source).toEqual(NWS_SOURCE);
  });

  it('empty features is a valid answer, not an error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ features: [] }), { status: 200 }))
    );
    const { alerts } = await fetchAlerts({ lat: 39.7392, lon: -104.9903 });
    expect(alerts).toEqual([]);
  });

  it('one malformed feature does not kill the list (self-healing)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ features: [null, ALERT, undefined] }), { status: 200 })
      )
    );
    const { alerts } = await fetchAlerts({ lat: 39.7392, lon: -104.9903 });
    expect(alerts).toHaveLength(1);
    expect(alerts[0].event).toBe('Winter Storm Warning');
  });

  it('rejects invalid coords before fetching', async () => {
    const spy = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', spy);
    await expect(fetchAlerts({ lat: 0, lon: 999 })).rejects.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('fetchCurrentConditions', () => {
  const OBS = {
    properties: {
      timestamp: '2026-10-12T20:00:00+00:00',
      textDescription: 'Clear',
      temperature: { value: 10 }, // 10C = 50F
      relativeHumidity: { value: 42.5 },
      windSpeed: { value: 16.0934 }, // 10 mph
      windDirection: { value: 270 },
    },
  };

  function stubChain() {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (url.includes('/points/')) {
          return new Response(
            JSON.stringify({
              properties: { observationStations: 'https://api.weather.gov/stations' },
            }),
            { status: 200 }
          );
        }
        if (url === 'https://api.weather.gov/stations') {
          return new Response(
            JSON.stringify({
              features: [{ properties: { stationIdentifier: 'KDEN' } }],
            }),
            { status: 200 }
          );
        }
        if (url.includes('/observations/latest')) {
          expect(url).toBe('https://api.weather.gov/stations/KDEN/observations/latest');
          return new Response(JSON.stringify(OBS), { status: 200 });
        }
        throw new Error(`unexpected url ${url}`);
      })
    );
  }

  it('resolves station then converts units, nulls stay null', async () => {
    stubChain();
    const c = await fetchCurrentConditions({ lat: 39.7392, lon: -104.9903 });
    expect(c).toMatchObject({
      tempF: 50,
      humidityPct: 43,
      windMph: 10,
      windDirDeg: 270,
      condition: 'Clear',
      stationId: 'KDEN',
    });
    expect(c.source).toEqual(NWS_SOURCE);
  });

  it('missing station fields become null instead of NaN', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (url.includes('/points/'))
          return new Response(
            JSON.stringify({ properties: { observationStations: 'https://api.weather.gov/s' } }),
            { status: 200 }
          );
        if (url.endsWith('/s'))
          return new Response(
            JSON.stringify({ features: [{ properties: { stationIdentifier: 'KDEN' } }] }),
            { status: 200 }
          );
        return new Response(JSON.stringify({ properties: {} }), { status: 200 });
      })
    );
    const c = await fetchCurrentConditions({ lat: 39.7, lon: -105 });
    expect(c.tempF).toBeNull();
    expect(c.windMph).toBeNull();
    expect(c.condition).toBe('');
  });
});
