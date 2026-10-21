// Prepared by Twistor Holdings LLC
// Tests for overlays/http.js (TW-446).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchJson,
  cached,
  clearOverlayCache,
  safeFetch,
  assertAllowedUrl,
  assertLatLon,
  OverlayError,
} from './http.js';

beforeEach(() => {
  clearOverlayCache();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('assertAllowedUrl', () => {
  it('accepts the two allowlisted hosts over https', () => {
    expect(() => assertAllowedUrl('https://api.weather.gov/alerts/active')).not.toThrow();
    expect(() => assertAllowedUrl('https://services1.arcgis.com/x/y/query?f=json')).not.toThrow();
  });
  it('rejects non-allowlisted hosts, including lookalikes', () => {
    expect(() => assertAllowedUrl('https://evil.com/x')).toThrow(OverlayError);
    expect(() => assertAllowedUrl('https://evilapi.weather.gov/x')).toThrow(OverlayError);
    expect(() => assertAllowedUrl('https://api.weather.gov.evil.com/x')).toThrow(OverlayError);
  });
  it('rejects non-https and malformed urls', () => {
    expect(() => assertAllowedUrl('http://api.weather.gov/x')).toThrow(OverlayError);
    expect(() => assertAllowedUrl('not a url')).toThrow(OverlayError);
  });
});

describe('assertLatLon', () => {
  it('accepts valid Denver coords', () => {
    expect(assertLatLon(39.7392, -104.9903)).toEqual({ lat: 39.7392, lon: -104.9903 });
  });
  it('rejects out-of-range, NaN, and non-numbers (the URL injection boundary)', () => {
    expect(() => assertLatLon(91, 0)).toThrow(OverlayError);
    expect(() => assertLatLon(0, 181)).toThrow(OverlayError);
    expect(() => assertLatLon(NaN, 0)).toThrow(OverlayError);
    expect(() => assertLatLon('39.7', -104)).toThrow(OverlayError);
    expect(() => assertLatLon(Infinity, 0)).toThrow(OverlayError);
  });
});

describe('fetchJson', () => {
  it('parses json on 200', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ a: 1 }), { status: 200 })));
    await expect(fetchJson('https://api.weather.gov/x')).resolves.toEqual({ a: 1 });
  });
  it('throws OverlayError on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 500 })));
    await expect(fetchJson('https://api.weather.gov/x')).rejects.toThrow(OverlayError);
  });
  it('throws OverlayError on invalid json', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not json{{{', { status: 200 })));
    await expect(fetchJson('https://api.weather.gov/x')).rejects.toThrow(OverlayError);
  });
  it('aborts on timeout', async () => {
    // Real timers: a 50ms timeout against a fetch that ignores everything
    // except the abort signal.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, { signal }) =>
          new Promise((_, reject) => {
            if (signal.aborted) {
              reject(new DOMException('Aborted', 'AbortError'));
              return;
            }
            signal.addEventListener(
              'abort',
              () => reject(new DOMException('Aborted', 'AbortError')),
              { once: true }
            );
          })
      )
    );
    await expect(fetchJson('https://api.weather.gov/x', { timeoutMs: 50 })).rejects.toThrow(
      OverlayError
    );
  });
  it('re-checks the final URL after redirects (TW-472)', async () => {
    const sneaky = new Response(JSON.stringify({ a: 1 }), { status: 200 });
    Object.defineProperty(sneaky, 'url', { value: 'https://evil.com/stolen' });
    vi.stubGlobal('fetch', vi.fn(async () => sneaky));
    await expect(fetchJson('https://api.weather.gov/x')).rejects.toThrow(/allowlist/);
  });
  it('accepts same-host redirect targets', async () => {
    const redir = new Response(JSON.stringify({ a: 1 }), { status: 200 });
    Object.defineProperty(redir, 'url', { value: 'https://api.weather.gov/other' });
    vi.stubGlobal('fetch', vi.fn(async () => redir));
    await expect(fetchJson('https://api.weather.gov/x')).resolves.toEqual({ a: 1 });
  });
  it('rejects oversize bodies', async () => {
    const big = 'x'.repeat(100);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(big, { status: 200 })));
    await expect(fetchJson('https://api.weather.gov/x', { maxBytes: 10 })).rejects.toThrow(
      /exceeded/
    );
  });
});

describe('cached', () => {
  it('serves from cache within TTL, refetches after', async () => {
    vi.useFakeTimers();
    const fn = vi.fn(async () => ({ v: 1 }));
    await cached('k', 1000, fn);
    await cached('k', 1000, fn);
    expect(fn).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1001);
    await cached('k', 1000, fn);
    expect(fn).toHaveBeenCalledTimes(2);
  });
  it('does not cache failures', async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new Error('down'))
      .mockResolvedValue({ v: 1 });
    await expect(cached('k2', 60000, fn)).rejects.toThrow('down');
    await expect(cached('k2', 60000, fn)).resolves.toEqual({ v: 1 });
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe('safeFetch', () => {
  it('wraps success and failure without throwing', async () => {
    await expect(safeFetch(async () => 42)).resolves.toEqual({ ok: true, data: 42 });
    const r = await safeFetch(async () => {
      throw new OverlayError('boom');
    });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch('boom');
  });
});
