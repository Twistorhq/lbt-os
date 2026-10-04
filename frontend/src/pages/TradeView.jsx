// Prepared by Twistor Holdings LLC.
//
// Twistor Trade View — internal command edition, first-class app page.
// Map with prospect/client pins, dossier panel, one-button diagnostics,
// read-only SQL sandbox, and insight-layer toggles.
// Data: clearly-labeled SAMPLE records (no real businesses, no invented
// metrics). Storm layer is LIVE NWS data (keyless, api.weather.gov).
// Basemap: Leaflet + Esri Dark Gray canvas (dark) / OSM standard (terrain).
// Both keyless — no API keys, no accounts. Esri tiles require attribution.

import { useEffect, useMemo, useRef, useState, Fragment } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './tradeview/tradeview.css'
import {
  SAMPLE_COMPANIES,
  SAMPLE_PROVENANCE,
  TRADE_FILTERS,
  KIND_FILTERS,
  INSIGHT_PANELS,
} from './tradeview/tradeviewSample'
import { safeHttpsUrl } from './tradeview/urlSafe'
import { tradeviewApi } from '../lib/api'
import { isSampleMode } from '../lib/sampleMode'

const DENVER = [39.7392, -104.9903]
const STORM_COLORS = { tornado: '#FF4D5E', wind: '#FFB020', hail: '#6FD3FF', other: '#7C5CFF' }
const SQL_COLUMNS = ['id', 'name', 'kind', 'trade', 'address', 'phone', 'owner', 'email']

function stormColor(event) {
  const e = String(event || '').toLowerCase()
  if (e.includes('tornado')) return STORM_COLORS.tornado
  if (e.includes('thunderstorm') || e.includes('wind')) return STORM_COLORS.wind
  if (e.includes('hail')) return STORM_COLORS.hail
  return STORM_COLORS.other
}

function PinIcon({ company }) {
  const color = company.kind === 'client' ? '#F0B35D' : '#7C5CFF'
  const shape = company.kind === 'client' ? 'tv-pin-client' : 'tv-pin-prospect'
  const glyph = company.kind === 'client' ? '◆' : '◈'
  return (
    <span className={`tv-pin tv-pin-enter ${shape}`} style={{ '--pin': color }} aria-hidden="true">
      <span>{glyph}</span>
    </span>
  )
}

function PublicRecords({ records }) {
  if (!records || records.length === 0) {
    return <p className="tv-empty">No public-record links yet — check back soon.</p>
  }
  return (
    <ul style={{ margin: '0.5rem 0', paddingLeft: '1.1rem', fontSize: '0.9rem' }}>
      {records.map((r, i) => {
        const href = safeHttpsUrl(r.url)
        return (
          <li key={i} style={{ margin: '0.35rem 0' }}>
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">{r.label}</a>
            ) : (
              <span style={{ color: '#a8a29a' }}>{r.label} (link unavailable)</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function InsightPanels() {
  // Sample records carry no assessed insights — honest empty states only.
  return (
    <>
      {INSIGHT_PANELS.map((p) => (
        <div key={p.id}>
          <h3>{p.title}</h3>
          <p className="tv-empty">Not assessed yet — add data. {p.desc}</p>
        </div>
      ))}
    </>
  )
}

// Read-only SQL sandbox over a company dataset.
// Genuinely read-only: only a SELECT subset is parsed and executed in-memory.
// Anything else is rejected with a clear message.
// `rows` defaults to the sample dataset; live mode passes the live pins.
export function runSandboxQuery(sql, dataset = SAMPLE_COMPANIES) {
  const text = String(sql || '').trim()
  if (!text) return { error: 'Type a SELECT query to run against the sample dataset.' }
  if (/;/.test(text)) return { error: 'One statement at a time — no semicolons.' }
  if (!/^\s*(select|with)\b/i.test(text)) {
    return { error: 'Read-only sandbox: only SELECT queries are allowed.' }
  }
  const m = text.match(
    /^\s*select\s+(.+?)\s+from\s+companies\s*(?:where\s+(.+?))?\s*(?:limit\s+(\d+))?\s*$/i
  )
  if (!m) {
    return {
      error:
        'Supported shape: SELECT <columns|*> FROM companies [WHERE trade = \'x\' | kind = \'x\' | name LIKE \'%x%\'] [LIMIT n]',
    }
  }
  const colsRaw = m[1].trim()
  const cols = colsRaw === '*' ? SQL_COLUMNS : colsRaw.split(',').map((c) => c.trim().toLowerCase())
  const bad = cols.filter((c) => !SQL_COLUMNS.includes(c))
  if (bad.length > 0) {
    return { error: `Unknown column(s): ${bad.join(', ')}. Available: ${SQL_COLUMNS.join(', ')}` }
  }
  let rows = dataset.slice()
  const where = (m[2] || '').trim()
  if (where) {
    const wm = where.match(/^(trade|kind|name)\s*(=|like)\s*'(.*)'$/i)
    if (!wm) return { error: 'WHERE supports: trade = \'x\', kind = \'x\', name LIKE \'%x%\'' }
    const [, field, op, value] = wm
    const f = field.toLowerCase()
    if (op.toLowerCase() === '=') {
      rows = rows.filter((r) => String(r[f]).toLowerCase() === value.toLowerCase())
    } else {
      const needle = value.replace(/%/g, '').toLowerCase()
      rows = rows.filter((r) => String(r[f]).toLowerCase().includes(needle))
    }
  }
  const limit = m[3] ? parseInt(m[3], 10) : 100
  rows = rows.slice(0, Math.min(limit, 100))
  return { columns: cols, rows: rows.map((r) => cols.map((c) => r[c] ?? '')) }
}

function SqlSandbox({ rows, datasetLabel }) {
  const [query, setQuery] = useState("SELECT name, kind, trade FROM companies WHERE trade = 'HVAC'")
  const [result, setResult] = useState(null)
  const run = () => setResult(runSandboxQuery(query, rows))
  return (
    <div className="tv-sandbox">
      <h3>SQL sandbox — read-only</h3>
      <p className="tv-note">SELECT-only subset over the {datasetLabel}. Nothing is written anywhere.</p>
      <label htmlFor="tv-sql" className="tv-note" style={{ display: 'block', marginBottom: '0.35rem' }}>
        Query
      </label>
      <textarea
        id="tv-sql"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        spellCheck={false}
        aria-describedby="tv-sql-help"
      />
      <p id="tv-sql-help" className="tv-note">
        Example: SELECT name, kind FROM companies WHERE kind = &apos;prospect&apos; LIMIT 5
      </p>
      <button type="button" className="tv-btn" onClick={run} style={{ marginTop: '0.5rem' }}>
        Run query
      </button>
      {result?.error && (
        <p className="tv-sql-error" role="alert">{result.error}</p>
      )}
      {result && !result.error && (
        <table className="tv-table">
          <thead>
            <tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {result.rows.map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j}>{v}</td>)}</tr>
            ))}
          </tbody>
        </table>
      )}
      {result && !result.error && result.rows.length === 0 && (
        <p className="tv-empty">No rows matched.</p>
      )}
    </div>
  )
}

// TW-301: live dossier facts + leak findings for the selected pin.
// Rendered only when signed in; sample mode keeps the static Facts block.
export function LiveDossier({ dossier }) {
  if (!dossier) return <p className="tv-empty">Loading live dossier…</p>
  const facts = dossier.facts || {}
  const findings = dossier.leak_findings || []
  return (
    <>
      <h3>Facts</h3>
      {Object.keys(facts).length > 0 ? (
        <dl className="tv-facts">
          {Object.entries(facts).map(([k, v]) => (
            <Fragment key={k}>
              <dt>{k}</dt>
              <dd>{String(v)}</dd>
            </Fragment>
          ))}
        </dl>
      ) : (
        <p className="tv-empty">No facts on file yet.</p>
      )}
      <h3>Leak findings</h3>
      {findings.length > 0 ? (
        <ul style={{ margin: '0.5rem 0', paddingLeft: '1.1rem', fontSize: '0.9rem' }}>
          {findings.map((f, i) => (
            <li key={i} style={{ margin: '0.35rem 0' }}>
              <strong>{f.title}</strong>
              {f.estimated_value != null && (
                <> — <span className="tv-kind">${Number(f.estimated_value).toLocaleString()}</span></>
              )}
              {f.recommended_action && <div className="tv-note">{f.recommended_action}</div>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="tv-empty">No leak findings for this record yet.</p>
      )}
      <p className="tv-provenance">{dossier.provenance}</p>
    </>
  )
}

// TW-301: server-generated diagnostics from live data.
export function LiveDiagnosticsReport({ data, onClose }) {
  if (!data) return <p className="tv-empty">Loading diagnostics…</p>
  const findings = data.leak_findings || []
  return (
    <div className="tv-report" role="region" aria-label={`Diagnostics report for ${data.entity_name}`}>
      <h4>Diagnostics — {data.entity_name}</h4>
      <p className="tv-provenance">LIVE report — generated from your connected data.</p>
      <p><strong>Snapshot:</strong> {data.snapshot}</p>
      {findings.length > 0 ? (
        <>
          <p><strong>Leak findings ({findings.length}):</strong></p>
          <ul style={{ margin: '0.5rem 0', paddingLeft: '1.1rem', fontSize: '0.9rem' }}>
            {findings.map((f, i) => (
              <li key={i} style={{ margin: '0.35rem 0' }}>
                {f.title}
                {f.estimated_value != null && (
                  <> — <span className="tv-kind">${Number(f.estimated_value).toLocaleString()}</span></>
                )}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p><strong>Leak findings:</strong> none for this record.</p>
      )}
      <p><strong>Recommended next step:</strong> {data.recommended_next_step}</p>
      <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.7rem' }}>
        <button type="button" className="tv-btn" onClick={() => window.print()}>Print report</button>
        <button type="button" className="tv-btn" onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

export function DiagnosticsReport({ company, onClose }) {  return (
    <div className="tv-report" role="region" aria-label={`Diagnostics report for ${company.name}`}>
      <h4>Prospect diagnostics — {company.name}</h4>
      <p className="tv-provenance">SAMPLE report — generated from demo data, not a real assessment.</p>
      <p><strong>Snapshot:</strong> {company.trade} · {company.kind} · {company.address}</p>
      <p><strong>Pitch angle:</strong> {company.pitch}</p>
      <p><strong>Marketing gap:</strong> {company.marketing}</p>
      <p><strong>Suggested next step:</strong> Run a What-Happened leak check on their follow-up
        flow, then bring the one-page leave-behind.</p>
      <p className="tv-provenance">Provenance: {company.provenance}</p>
      <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.7rem' }}>
        <button type="button" className="tv-btn" onClick={() => window.print()}>Print report</button>
        <button type="button" className="tv-btn" onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

export default function TradeView() {
  const [bootDone, setBootDone] = useState(false)
  const [search, setSearch] = useState('')
  const [kinds, setKinds] = useState({ prospect: true, client: true })
  const [trades, setTrades] = useState({ HVAC: true, Plumbing: true, Electrical: true })
  const [basemap, setBasemap] = useState('dark')
  const [stormOn, setStormOn] = useState(false)
  const [stormNote, setStormNote] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [showReport, setShowReport] = useState(false)
  // TW-301: live backend state. Signed-in users get real pins/dossiers/
  // diagnostics from /api/v1/tradeview; signed-out visitors stay on the
  // clearly-labeled sample dataset (TW-295).
  const [livePins, setLivePins] = useState(null)
  const [liveNote, setLiveNote] = useState('')
  const [liveDossier, setLiveDossier] = useState(null)
  const [liveDiagnostics, setLiveDiagnostics] = useState(null)
  const mapRef = useRef(null)
  const mapDivRef = useRef(null)
  const markersRef = useRef({})
  const stormLayerRef = useRef(null)
  const baseLayersRef = useRef({})
  const isTest = typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.MODE === 'test'

  useEffect(() => {
    const t = setTimeout(() => setBootDone(true), 1400)
    return () => clearTimeout(t)
  }, [])

  // TW-301: fetch live pins when signed in. Any failure (no backend on the
  // static Pages deploy, 401, network) falls back to the sample dataset —
  // the map never renders empty. (Not gated on isTest: the fetch is plain
  // axios and is exactly what the live-mode tests exercise.)
  useEffect(() => {
    if (isSampleMode()) return
    let cancelled = false
    tradeviewApi.pins()
      .then((r) => {
        if (cancelled) return
        // Normalize backend kinds (customer|lead) to the map's display kinds
        // (client|prospect); apiKind is kept for dossier/diagnostics calls.
        // Idempotent: already-normalized kinds pass through untouched.
        const pins = (r.data.pins || []).map((p) => ({
          ...p,
          apiKind: p.kind,
          kind: p.kind === 'customer' ? 'client' : p.kind === 'lead' ? 'prospect' : p.kind,
        }))
        setLivePins(pins)
        const n = r.data.unlocated_count || 0
        if (n > 0) setLiveNote(`${n} record${n === 1 ? '' : 's'} ha${n === 1 ? 's' : 've'} no mappable address yet.`)
      })
      .catch(() => {
        if (!cancelled) setLivePins(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const liveMode = livePins !== null
  const companies = liveMode ? livePins : SAMPLE_COMPANIES

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return companies.filter(
      (c) =>
        kinds[c.kind] &&
        // Live records without a trade classification stay visible — real
        // data is never hidden by a filter it can't match.
        (trades[c.trade] || (liveMode && !c.trade)) &&
        (!q || c.name.toLowerCase().includes(q) || (c.address || '').toLowerCase().includes(q))
    )
  }, [search, kinds, trades, companies, liveMode])

  const selected = useMemo(
    () => companies.find((c) => c.id === selectedId) || null,
    [selectedId, companies]
  )

  // TW-301: live dossier (facts + leak findings) for the selected pin.
  useEffect(() => {
    setLiveDossier(null)
    if (!liveMode || !selected) return
    let cancelled = false
    tradeviewApi.dossier(selected.apiKind || selected.kind, selected.id)
      .then((r) => {
        if (!cancelled) setLiveDossier(r.data)
      })
      .catch(() => {
        if (!cancelled) setLiveDossier(null)
      })
    return () => {
      cancelled = true
    }
  }, [liveMode, selected])

  // TW-301: live diagnostics report, fetched on demand.
  useEffect(() => {
    setLiveDiagnostics(null)
    if (!liveMode || !selected || !showReport) return
    let cancelled = false
    tradeviewApi.diagnostics(selected.apiKind || selected.kind, selected.id)
      .then((r) => {
        if (!cancelled) setLiveDiagnostics(r.data)
      })
      .catch(() => {
        if (!cancelled) setLiveDiagnostics(null)
      })
    return () => {
      cancelled = true
    }
  }, [liveMode, selected, showReport])

  // Map init (skipped under vitest — no layout engine there).
  useEffect(() => {
    if (isTest || !mapDivRef.current || mapRef.current) return
    const map = L.map(mapDivRef.current, { zoomControl: false }).setView(DENVER, 11)
    L.control.zoom({ position: 'bottomright' }).addTo(map)
    // Dark: Esri World Dark Gray canvas (keyless public ArcGIS Online
    // service — no API key, no account). CARTO's basemaps.cartocdn.com began
    // requiring an API key in Aug 2026 and now stamps "API KEY REQUIRED" on
    // anonymous tiles, so it is out. The Dark Gray Base carries no place
    // names, so the Dark Gray Reference labels layer rides on top to match
    // the old look. NOTE: Esri tile paths are {z}/{y}/{x} (row before
    // column), not the usual {z}/{x}/{y}.
    const darkBase = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
      maxZoom: 19,
      maxNativeZoom: 16,
    })
    const darkLabels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
      maxZoom: 19,
      maxNativeZoom: 16,
    })
    const dark = L.layerGroup([darkBase, darkLabels])
    const terrain = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
      maxZoom: 19,
    })
    dark.addTo(map)
    baseLayersRef.current = { dark, terrain }
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [isTest])

  // Basemap toggle.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const { dark, terrain } = baseLayersRef.current
    if (basemap === 'dark') {
      if (map.hasLayer(terrain)) map.removeLayer(terrain)
      if (!map.hasLayer(dark)) dark.addTo(map)
    } else {
      if (map.hasLayer(dark)) map.removeLayer(dark)
      if (!map.hasLayer(terrain)) terrain.addTo(map)
    }
  }, [basemap])

  // Pins.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    Object.values(markersRef.current).forEach((m) => m.remove())
    markersRef.current = {}
    filtered.forEach((c) => {
      const color = c.kind === 'client' ? '#F0B35D' : '#7C5CFF'
      const shape = c.kind === 'client' ? 'tv-pin-client' : 'tv-pin-prospect'
      const glyph = c.kind === 'client' ? '◆' : '◈'
      const icon = L.divIcon({
        className: '',
        html: `<span class="tv-pin tv-pin-enter ${shape}" style="--pin:${color}"><span>${glyph}</span></span>`,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      })
      const marker = L.marker([c.lat, c.lng], { icon, title: c.name, keyboard: true })
      marker.on('click', () => {
        setSelectedId(c.id)
        setShowReport(false)
        map.flyTo([c.lat, c.lng], 13, { duration: 1.1 })
      })
      marker.addTo(map)
      markersRef.current[c.id] = marker
    })
  }, [filtered])

  // Storm layer — live NWS alerts (keyless, CORS-enabled).
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!stormOn) {
      if (stormLayerRef.current) {
        map.removeLayer(stormLayerRef.current)
        stormLayerRef.current = null
      }
      setStormNote('')
      return
    }
    let cancelled = false
    setStormNote('Loading NWS alerts…')
    fetch('https://api.weather.gov/alerts/active?point=39.7392,-104.9903')
      .then((r) => {
        if (!r.ok) throw new Error(`NWS responded ${r.status}`)
        return r.json()
      })
      .then((data) => {
        if (cancelled) return
        const features = (data.features || []).filter((f) => f.geometry)
        const layer = L.geoJSON(features, {
          style: (f) => ({
            color: stormColor(f.properties?.event),
            weight: 2,
            dashArray: '6 4',
            fillOpacity: 0.12,
          }),
          pointToLayer: (f, latlng) =>
            L.circleMarker(latlng, {
              radius: 8,
              color: stormColor(f.properties?.event),
              fillOpacity: 0.3,
            }),
          onEachFeature: (f, l) => {
            const p = f.properties || {}
            const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
              '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
            }[c]))
            l.bindPopup(
              `<strong>${esc(p.event)}</strong><br>${esc(p.headline)}<br>` +
              `<small>Onset: ${esc(p.onset)} · Expires: ${esc(p.expires)}</small>`
            )
          },
        })
        layer.addTo(map)
        stormLayerRef.current = layer
        setStormNote(
          features.length > 0
            ? `${features.length} active alert${features.length === 1 ? '' : 's'} · live from NWS api.weather.gov`
            : 'No active storm alerts right now · live from NWS api.weather.gov'
        )
      })
      .catch(() => {
        if (!cancelled) setStormNote("Couldn't reach NWS — check connection and try again.")
      })
    return () => {
      cancelled = true
    }
  }, [stormOn])

  const toggleKind = (k) => setKinds((p) => ({ ...p, [k]: !p[k] }))
  const toggleTrade = (t) => setTrades((p) => ({ ...p, [t]: !p[t] }))

  return (
    <div className="tv-root -mx-5 xl:-mx-7 -my-6">
      <a href="#tv-map" className="tv-skip">Skip to trade map</a>
      {!bootDone && (
        <div className="tv-boot" aria-hidden="true">
          <div className="tv-boot-inner">
            <div className="tv-boot-mark">TWISTOR</div>
            <div className="tv-boot-title">TRADE VIEW</div>
            <div className="tv-boot-bar"><span /></div>
            <div className="tv-boot-sub">INITIALIZING TRADE MAP</div>
          </div>
        </div>
      )}

      <div className="tv-topbar">
        <div className="tv-brand">
          <h1>
            TRADE <span className="tv-glow">VIEW</span>
            <span className="tv-badges">
              <span className="tv-badge tv-badge-internal">Internal</span>
              {liveMode ? (
                <span className="tv-badge">Live data</span>
              ) : (
                <span className="tv-badge tv-badge-sample">Sample data</span>
              )}
            </span>
          </h1>
          <p className="tv-sub">
            <span className="tv-counts" id="tv-counts">
              {filtered.length} companies · {filtered.filter((c) => c.kind === 'prospect').length} prospects ·{' '}
              {filtered.filter((c) => c.kind === 'client').length} clients
            </span>
            {liveNote && <span className="tv-note" style={{ display: 'block' }}>{liveNote}</span>}
          </p>
        </div>
        <div className="tv-basemap" role="group" aria-label="Basemap style">
          <button type="button" aria-pressed={basemap === 'dark'} onClick={() => setBasemap('dark')}>
            Dark
          </button>
          <button type="button" aria-pressed={basemap === 'terrain'} onClick={() => setBasemap('terrain')}>
            Terrain
          </button>
        </div>
      </div>

      <div className="tv-stage">
        <div
          id="tv-map"
          ref={mapDivRef}
          className="tv-map"
          role="application"
          aria-label="Twistor Trade View trade map"
          tabIndex={0}
        />
        <div className="tv-vignette" aria-hidden="true" />

        <aside className="tv-hud" aria-label="Map controls">
          <h2 className="tv-hud-title">Command HUD</h2>
          <label htmlFor="tv-search" className="tv-note" style={{ fontWeight: 700, letterSpacing: '0.18em' }}>
            Search
          </label>
          <input
            id="tv-search"
            type="search"
            placeholder="Company or address…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <fieldset>
            <legend>Kind</legend>
            {KIND_FILTERS.map((k) => (
              <label key={k} className="tv-check">
                <input type="checkbox" checked={kinds[k]} onChange={() => toggleKind(k)} />
                {k === 'prospect' ? 'Prospects' : 'Clients'}
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>Trade</legend>
            {TRADE_FILTERS.map((t) => (
              <label key={t} className="tv-check">
                <input type="checkbox" checked={trades[t]} onChange={() => toggleTrade(t)} />
                {t}
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>Insight layers</legend>
            <label className="tv-check">
              <input type="checkbox" checked={stormOn} onChange={(e) => setStormOn(e.target.checked)} />
              NWS storm alerts (live)
            </label>
            <label className="tv-check" title="Not assessed in sample data">
              <input type="checkbox" disabled />
              Ghost-web presence
            </label>
            <label className="tv-check" title="Not assessed in sample data">
              <input type="checkbox" disabled />
              Competitor ad pressure
            </label>
            <label className="tv-check" title="Not assessed in sample data">
              <input type="checkbox" disabled />
              Digital maturity grade
            </label>
            {stormNote && <p className="tv-note">{stormNote}</p>}
          </fieldset>
          <p className="tv-note">
            Map pins: ◈ prospect · ◆ client.{' '}
            {liveMode ? 'Live records from your connected data.' : 'Sample records — not real businesses.'}
          </p>
        </aside>

        <section className="tv-dossier" aria-label="Company dossier" aria-live="polite">
          {!selected && (
            <div className="tv-dossier-empty">
              <span style={{ fontSize: '1.7rem', display: 'block', marginBottom: '0.7rem' }}>◈</span>
              Select a pin to open the dossier.
            </div>
          )}
          {selected && (
            <article key={selected.id}>
              <div style={{ display: 'flex', gap: '0.9rem', alignItems: 'flex-start' }}>
                <PinIcon company={selected} />
                <div>
                  <h2 className="tv-detail-title">{selected.name}</h2>
                  <p className="tv-detail-sub">
                    <span className="tv-kind">{selected.kind}</span> · {selected.trade}
                  </p>
                  <p className="tv-detail-sub">{selected.address}</p>
                </div>
              </div>
              <h3>Facts</h3>
              {liveMode ? (
                <LiveDossier dossier={liveDossier} />
              ) : (
                <>
                  <dl className="tv-facts">
                    <dt>Phone</dt><dd>{selected.phone}</dd>
                    <dt>Owner</dt><dd>{selected.owner}</dd>
                    <dt>Email</dt><dd>{selected.email}</dd>
                  </dl>
                  <h3>Pitch notes</h3>
                  <p className="tv-detail-sub">{selected.pitch}</p>
                  <p className="tv-detail-sub">{selected.marketing}</p>
                  <h3>Public records</h3>
                  <PublicRecords records={selected.publicRecords} />
                </>
              )}
              <h3>Insights</h3>
              <InsightPanels />
              {!liveMode && (
                <p className="tv-provenance">Provenance: {selected.provenance}</p>
              )}
              <div style={{ marginTop: '1rem', display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="tv-btn"
                  onClick={() => setShowReport((v) => !v)}
                  aria-expanded={showReport}
                >
                  {showReport ? 'Hide diagnostics' : 'Run diagnostics'}
                </button>
              </div>
              {showReport && liveMode && (
                <LiveDiagnosticsReport data={liveDiagnostics} onClose={() => setShowReport(false)} />
              )}
              {showReport && !liveMode && (
                <DiagnosticsReport company={selected} onClose={() => setShowReport(false)} />
              )}
              <SqlSandbox rows={companies} datasetLabel={liveMode ? 'live dataset' : 'sample dataset'} />
            </article>
          )}
        </section>
      </div>
    </div>
  )
}
