// Prepared by Twistor Holdings LLC.
//
// Twistor Trade View — CLIENT edition (TW-304).
//
// The client-facing side of Trade View. Hard rules for this surface:
// zero SQL/code anywhere, talk-or-type interaction, ONE primary button,
// marketing output only. The internal copilot split is preserved: the
// internal edition (TradeView.jsx) shows SQL and the read-only sandbox;
// this page never does. Faceless: no people imagery — map and motion only.
// Data: clearly-labeled SAMPLE records; storm alerts are live NWS data.

import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './tradeview.css'
import { SAMPLE_COMPANIES, SAMPLE_PROVENANCE, TRADE_FILTERS } from './tradeviewSample'
import { opportunityFor, OPPORTUNITY_ESTIMATE_NOTE } from './stormOpportunity'

const DENVER = [39.7392, -104.9903]
const NWS_URL = 'https://api.weather.gov/alerts/active?point=39.7392,-104.9903'
const TOUR_STEP_MS = 6500

// Cinematic scroll: sections scale and shift depth as they enter the viewport.
function useCinematic(deep = false) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') {
      el.classList.add('cv-inview')
      return
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            el.classList.add('cv-inview')
            io.disconnect()
          }
        })
      },
      { threshold: 0.15 }
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return ref
}

function Cine({ as: Tag = 'section', deep = false, className = '', children, ...rest }) {
  const ref = useCinematic(deep)
  return (
    <Tag ref={ref} className={`cv-cine${deep ? ' cv-cine-deep' : ''} ${className}`} {...rest}>
      {children}
    </Tag>
  )
}

// Plain-language answers over the sample dataset. No code, no SQL —
// this is the client voice: warm, direct, honest about demo data.
function answerQuestion(q, stormState) {
  const t = q.trim().toLowerCase()
  if (!t) return null
  const prospects = SAMPLE_COMPANIES.filter((c) => c.kind === 'prospect').length
  const clients = SAMPLE_COMPANIES.filter((c) => c.kind === 'client').length
  if (/how many|prospect|client|companies|business/.test(t)) {
    return `In this demo view we're tracking ${SAMPLE_COMPANIES.length} companies — ${prospects} prospects and ${clients} client. With your real data connected, this is your whole market, live.`
  }
  if (/storm|weather|alert|hail|tornado|wind|rain|opportunit/.test(t)) {
    if (stormState.loading) return 'Checking the latest National Weather Service alerts for your area…'
    if (stormState.error) return "I couldn't reach the weather service just now — try again in a moment."
    if (stormState.alerts.length === 0) {
      return 'No active storm alerts near Denver right now. Quiet skies — a good day to get ahead on follow-ups.'
    }
    const first = stormState.alerts[0]
    const opp = opportunityFor(first.event)
    return `Heads up: ${first.event} is active near Denver. ${opp.blurb} ${stormState.alerts.length > 1 ? `There are ${stormState.alerts.length} active alerts in total.` : ''} Want Twistor watching your territory around the clock?`
  }
  if (/hvac/.test(t)) {
    return 'HVAC is one of the three trades in this demo. The sample shows a classic leak: quoted jobs going cold after 48 hours with no follow-up system. That is money walking out the door — and it is exactly what Twistor finds first.'
  }
  if (/plumb/.test(t)) {
    return 'Plumbing is in the demo mix. The sample shop loses after-hours emergency calls to voicemail — and emergency work does not wait. Twistor answers when you cannot.'
  }
  if (/electric/.test(t)) {
    return 'Electrical rounds out the demo trades. The sample client here is on the What Happened tier — daily brief, leak detection, and a health score, with review velocity up since September.'
  }
  if (/report|diagnos|free/.test(t)) {
    return 'The free trade report is one click away — hit the button below and you will get a plain-English read on your market, where the leaks are, and what to do first.'
  }
  if (/price|cost|much|pay/.test(t)) {
    return 'Every shop is different, so we do not do one-size pricing on a demo page. The honest starting point is the free report — then we scope your pilot around what it finds.'
  }
  if (/tour|siteview|site view|fly/.test(t)) {
    return 'The SiteView tour is right below — a cinematic flythrough of a job site, before, during, and after. Take it for a spin.'
  }
  if (/who|twistor|about|company/.test(t)) {
    return 'Twistor builds the operating system for home-service businesses — we find the gaps before you notice the gaps are there. Starting with the trades: HVAC, plumbing, electrical.'
  }
  return 'Good question. In this demo I can talk prospects and clients, the three trades, live storm alerts, the SiteView tour, and your free trade report — try one of the suggestions below.'
}

const ASK_SUGGESTIONS = [
  'How many prospects are nearby?',
  'Any storm alerts right now?',
  'What does the free report cover?',
  'Take me on the SiteView tour',
]

function AskTwistor({ stormState }) {
  const [value, setValue] = useState('')
  const [answer, setAnswer] = useState(null)
  const ask = (text) => {
    const a = answerQuestion(text, stormState)
    if (a) setAnswer(a)
  }
  return (
    <div className="cv-ask">
      <div className="cv-ask-row">
        <label htmlFor="cv-ask-input" className="tv-note" style={{ position: 'absolute', left: '-9999px' }}>
          Ask about your market in plain words
        </label>
        <input
          id="cv-ask-input"
          type="text"
          placeholder="Ask in plain words — no jargon needed…"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') ask(value)
          }}
        />
        <button type="button" className="tv-btn tv-btn-big" onClick={() => ask(value)}>
          Ask
        </button>
      </div>
      <div className="cv-chips" role="group" aria-label="Suggested questions">
        {ASK_SUGGESTIONS.map((s) => (
          <button key={s} type="button" className="cv-chip" onClick={() => { setValue(s); ask(s) }}>
            {s}
          </button>
        ))}
      </div>
      {answer && (
        <div className="cv-answer" role="status" aria-live="polite">
          <p>{answer}</p>
        </div>
      )}
    </div>
  )
}

function TradeReportModal({ onClose }) {
  const headingRef = useRef(null)
  useEffect(() => {
    headingRef.current && headingRef.current.focus()
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const prospects = SAMPLE_COMPANIES.filter((c) => c.kind === 'prospect')
  return (
    <div className="cv-modal-veil" onClick={onClose} role="presentation">
      <div
        className="cv-modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cv-report-title"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="cv-report-tag">Your free trade report</p>
        <h2 id="cv-report-title" ref={headingRef} tabIndex={-1}>
          Your market, at a glance
        </h2>
        <p className="tv-note">Prepared by Twistor Holdings LLC · Demo edition — sample data, not real businesses.</p>
        <h3>Where the money is walking out</h3>
        <ul>
          {prospects.map((c) => (
            <li key={c.id}>
              <strong>{c.name}</strong> ({c.trade}) — {c.pitch}
            </li>
          ))}
        </ul>
        <h3>What we would do first</h3>
        <ul>
          <li>
            <strong>What Happened:</strong> daily brief, leak detection, and a health score — so no quoted job
            goes cold unnoticed.
          </li>
          <li>
            <strong>What Will Happen:</strong> forecasting and lead scoring — know which doors to knock before
            the competition does.
          </li>
          <li>
            <strong>What Should We Do:</strong> the prescriptive layer — next best action for every prospect,
            every morning.
          </li>
        </ul>
        <h3>The honest fine print</h3>
        <p>
          This report was generated from demo data. Connect your real data and the same report runs on
          your actual market — your prospects, your leaks, your bottom line.
        </p>
        <div className="cv-modal-actions">
          <button type="button" className="tv-btn tv-btn-big" onClick={() => window.print()}>
            Print report
          </button>
          <button type="button" className="tv-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

// SiteView — cinematic before/during/after job-site flythrough.
// Repurposes the gods-eye-view Director pattern (TW-279) as a guided
// camera tour over the trade map. Demo flythrough over sample data.
const TOUR_CHAPTERS = [
  {
    id: 'before',
    label: 'Before',
    caption: 'Before — the territory at dawn. Every pin is a door that could open.',
    view: { center: DENVER, zoom: 11 },
  },
  {
    id: 'during',
    label: 'During',
    caption: 'During — the crew on site. The job, documented as it happens.',
    view: { center: [SAMPLE_COMPANIES[0].lat, SAMPLE_COMPANIES[0].lng], zoom: 15 },
  },
  {
    id: 'after',
    label: 'After',
    caption: 'After — the week that was won. Proof of work, ready to share.',
    view: { center: DENVER, zoom: 10 },
  },
]

function SiteViewTour({ onClose }) {
  const [chapter, setChapter] = useState(0)
  const mapDivRef = useRef(null)
  const mapRef = useRef(null)
  const overlayRef = useRef(null)
  const isTest = typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.MODE === 'test'
  const reducedMotion =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  useEffect(() => {
    overlayRef.current && overlayRef.current.focus()
  }, [])

  useEffect(() => {
    if (isTest || !mapDivRef.current || mapRef.current) return
    const map = L.map(mapDivRef.current, { zoomControl: false, attributionControl: true }).setView(
      TOUR_CHAPTERS[0].view.center,
      TOUR_CHAPTERS[0].view.zoom
    )
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri',
      maxZoom: 19,
      maxNativeZoom: 16,
    }).addTo(map)
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [isTest])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const v = TOUR_CHAPTERS[chapter].view
    map.flyTo(v.center, v.zoom, { duration: reducedMotion ? 0 : 2.4 })
  }, [chapter, reducedMotion])

  useEffect(() => {
    if (reducedMotion || isTest) return
    const t = setTimeout(() => setChapter((c) => (c + 1) % TOUR_CHAPTERS.length), TOUR_STEP_MS)
    return () => clearTimeout(t)
  }, [chapter, reducedMotion, isTest])

  const onKey = (e) => {
    if (e.key === 'Escape') onClose()
    if (e.key === 'ArrowRight') setChapter((c) => (c + 1) % TOUR_CHAPTERS.length)
    if (e.key === 'ArrowLeft') setChapter((c) => (c - 1 + TOUR_CHAPTERS.length) % TOUR_CHAPTERS.length)
  }

  const ch = TOUR_CHAPTERS[chapter]
  return (
    <div
      className="cv-tour"
      ref={overlayRef}
      tabIndex={-1}
      onKeyDown={onKey}
      role="dialog"
      aria-modal="true"
      aria-label={`SiteView tour — ${ch.label}`}
    >
      <div className="cv-tour-map">
        <div ref={mapDivRef} className="tv-map" aria-hidden="true" />
        <div className="tv-vignette" aria-hidden="true" />
        <div className="cv-tour-top">
          <div className="cv-tour-progress" aria-hidden="true">
            {TOUR_CHAPTERS.map((c, i) => (
              <span key={c.id} className={i === chapter ? 'cv-active' : i < chapter ? 'cv-done' : ''} />
            ))}
          </div>
          <button type="button" className="cv-tour-close" onClick={onClose} aria-label="End tour">
            End tour ✕
          </button>
        </div>
        <button
          type="button"
          className="cv-tour-nav cv-tour-prev"
          aria-label="Previous chapter"
          onClick={() => setChapter((c) => (c - 1 + TOUR_CHAPTERS.length) % TOUR_CHAPTERS.length)}
        >
          ‹
        </button>
        <button
          type="button"
          className="cv-tour-nav cv-tour-next"
          aria-label="Next chapter"
          onClick={() => setChapter((c) => (c + 1) % TOUR_CHAPTERS.length)}
        >
          ›
        </button>
        <div className="cv-tour-caption" aria-live="polite">
          <p className="cv-tour-chapter">
            SiteView demo · {ch.label} · {chapter + 1} of {TOUR_CHAPTERS.length}
          </p>
          <p>{ch.caption}</p>
        </div>
      </div>
    </div>
  )
}

function StormAlertCard() {
  const [state, setState] = useState({ loading: true, error: false, alerts: [] })
  const [subscribed, setSubscribed] = useState(false)
  const isTest = typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.MODE === 'test'

  useEffect(() => {
    if (isTest) {
      setState({ loading: false, error: false, alerts: [] })
      return
    }
    let cancelled = false
    fetch(NWS_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`NWS ${r.status}`)
        return r.json()
      })
      .then((data) => {
        if (!cancelled) setState({ loading: false, error: false, alerts: data.features || [] })
      })
      .catch(() => {
        if (!cancelled) setState({ loading: false, error: true, alerts: [] })
      })
    return () => {
      cancelled = true
    }
  }, [isTest])

  const first = state.alerts[0]
  const opp = first ? opportunityFor(first.properties?.event) : null
  return (
    <div className="cv-alert" aria-live="polite">
      <h3>⛈ Storm opportunity alert</h3>
      {state.loading && <p>Checking live National Weather Service alerts…</p>}
      {!state.loading && state.error && (
        <p>Couldn&apos;t reach the weather service just now — check back shortly.</p>
      )}
      {!state.loading && !state.error && state.alerts.length === 0 && (
        <p>
          No active storm alerts near Denver right now. When weather moves in, this card becomes
          your early warning — storms are revenue for the trades, and Twistor spots them first.
        </p>
      )}
      {!state.loading && !state.error && first && (
        <p>
          <span className="cv-alert-event">{first.properties?.event}</span> is active near Denver.{' '}
          {opp.blurb} {OPPORTUNITY_ESTIMATE_NOTE}
        </p>
      )}
      {!subscribed ? (
        <button type="button" className="tv-btn" onClick={() => setSubscribed(true)}>
          Notify me about storm work
        </button>
      ) : (
        <p role="status" style={{ color: '#6fd3ff' }}>
          You&apos;re on the list — demo only, no alerts will actually be sent from this page.
        </p>
      )}
    </div>
  )
}

export default function ClientTradeView() {
  const [reportOpen, setReportOpen] = useState(false)
  const [tourOpen, setTourOpen] = useState(false)
  const [stormState, setStormState] = useState({ loading: true, error: false, alerts: [] })
  const isTest = typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.MODE === 'test'

  useEffect(() => {
    if (isTest) {
      setStormState({ loading: false, error: false, alerts: [] })
      return
    }
    let cancelled = false
    fetch(NWS_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`NWS ${r.status}`)
        return r.json()
      })
      .then((data) => {
        if (!cancelled) setStormState({ loading: false, error: false, alerts: data.features || [] })
      })
      .catch(() => {
        if (!cancelled) setStormState({ loading: false, error: true, alerts: [] })
      })
    return () => {
      cancelled = true
    }
  }, [isTest])

  const prospects = useMemo(() => SAMPLE_COMPANIES.filter((c) => c.kind === 'prospect'), [])

  return (
    <div className="tv-root -mx-5 xl:-mx-7 -my-6">
      <a href="#cv-ask" className="tv-skip">
        Skip to ask box
      </a>

      <Cine as="header" className="cv-hero" deep>
        <p className="cv-kicker">Twistor Trades · Client edition</p>
        <h2>
          See your market <span className="tv-glow">the way we do.</span>
        </h2>
        <p className="cv-lede">
          Trade View watches your territory — the prospects, the storms, the jobs walking out the
          door — and tells you in plain words what to do next. No dashboards to decode. No code.
          Just answers.
        </p>
        <div className="cv-badges" style={{ justifyContent: 'center', display: 'flex' }}>
          <span className="tv-badge cv-badge-client">Client edition</span>
          <span className="tv-badge tv-badge-sample">Sample data</span>
        </div>
      </Cine>

      <Cine className="cv-section" id="cv-ask">
        <h2>Ask in plain words</h2>
        <p className="cv-sub">
          Talk or type — ask about your market like you would ask a person. We answer like one.
        </p>
        <AskTwistor stormState={stormState} />
      </Cine>

      <Cine className="cv-section" deep>
        <h2>What this sees for you</h2>
        <p className="cv-sub">
          Demo numbers from our sample set — connect your data and every number below becomes yours.
        </p>
        <div className="cv-grid">
          <div className="cv-card">
            <span className="cv-stat">{prospects.length}</span>
            <h3>Prospects on the map</h3>
            <p>Shops in your territory with visible gaps — follow-up leaks, thin listings, missed calls.</p>
          </div>
          <div className="cv-card">
            <span className="cv-stat">{TRADE_FILTERS.length}</span>
            <h3>Trades covered</h3>
            <p>HVAC, plumbing, and electrical today — the same engine serves any trade with this shape.</p>
          </div>
          <div className="cv-card">
            <span className="cv-stat">24/7</span>
            <h3>Storm watch</h3>
            <p>Live weather alerts translated into opportunity — storms are revenue for the trades.</p>
          </div>
        </div>
      </Cine>

      <Cine className="cv-section">
        <h2>Storms are revenue</h2>
        <p className="cv-sub">
          For roofers, HVAC, and plumbers, a storm cell is a surge of repair calls. We watch the
          sky so you can staff the phones.
        </p>
        <StormAlertCard />
      </Cine>

      <Cine className="cv-section" deep>
        <h2>Walk the job site from your couch</h2>
        <p className="cv-sub">
          SiteView flies you through a job — before, during, and after — so every job tells its
          own story. Take the tour.
        </p>
        <button type="button" className="tv-btn tv-btn-big" onClick={() => setTourOpen(true)}>
          ▶ Take the SiteView tour
        </button>
      </Cine>

      <Cine className="cv-section">
        <h2>One button. Your whole market.</h2>
        <p className="cv-sub">
          No logins to learn, no reports to build. One click and you get a plain-English read on
          your territory — where the leaks are and what to do first.
        </p>
        <div className="cv-report-cta">
          <button type="button" className="tv-btn tv-btn-big" onClick={() => setReportOpen(true)}>
            Get my free trade report
          </button>
        </div>
      </Cine>

      <Cine as="footer" className="cv-section">
        <p className="tv-note" style={{ textAlign: 'center' }}>
          {SAMPLE_PROVENANCE} Storm alerts are live data from the National Weather Service
          (api.weather.gov). Prepared by Twistor Holdings LLC.
        </p>
      </Cine>

      {reportOpen && <TradeReportModal onClose={() => setReportOpen(false)} />}
      {tourOpen && <SiteViewTour onClose={() => setTourOpen(false)} />}
    </div>
  )
}
