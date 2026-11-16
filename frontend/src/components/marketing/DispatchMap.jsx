import { useMemo, useState } from 'react'
import { Lightning } from '../icons'
import demoData from './demo-map-data.json'

/* TW-1149: the dispatch map is the hero of the marketing data section.
 * Founder directive 2026-11-16: the map is the number one feature of the
 * Twistor Trades concept, so the demo page draws the whole day on one map:
 * job pins and crew routes (the joints), the market coverage polygon, and
 * the storm cell with the jobs in its path.
 *
 * Data: Maya's demo data layer (twistor-lab/projects/twistor-trades/
 * DEMO-MAP-DATA.json, copied verbatim to demo-map-data.json beside this
 * file). One fictional shop, Front Range Comfort Co. Every pin,
 * route, and dollar figure on this block is read from that file as
 * given; nothing is recomputed client side beyond formatting. The
 * labeling line under the map is meta.labelingLine, verbatim.
 *
 * Rendering: self-contained SVG on purpose. The preview snapshot ships
 * as a single file with no network, so the map cannot depend on tile
 * servers. GeoJSON coordinates are projected with a fitted
 * equirectangular projection (longitude shrunk by cos(39.7 deg), the
 * Denver metro latitude). The live product map (TradeView) runs Leaflet
 * with keyless basemaps; this demo map keeps the same dark dispatch
 * register.
 */

const { meta, kpis, geojson } = demoData

const money = (n) => `$${n.toLocaleString('en-US')}`
const money2 = (n) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const STATUS_FILL = {
  completed: '#34d399',
  on_site: '#f5b942',
  en_route: '#818cf8',
  scheduled: '#0d1526',
}
const STATUS_LABEL = {
  completed: 'Completed',
  on_site: 'On site',
  en_route: 'En route',
  scheduled: 'Scheduled',
}

/* Stylized street context at real coordinates, projected with the data. */
const CITIES = [
  { at: [-104.8203, 39.9853], label: 'BRIGHTON' },
  { at: [-105.0708, 39.9205], label: 'BROOMFIELD' },
  { at: [-104.9719, 39.868], label: 'THORNTON' },
  { at: [-105.0875, 39.8028], label: 'ARVADA' },
  { at: [-104.9339, 39.8083], label: 'COMMERCE CITY' },
  { at: [-104.9903, 39.7392], label: 'DENVER' },
  { at: [-105.0815, 39.7047], label: 'LAKEWOOD' },
  { at: [-104.8319, 39.7294], label: 'AURORA' },
  { at: [-105.0166, 39.6133], label: 'LITTLETON' },
  { at: [-104.8769, 39.5804], label: 'CENTENNIAL' },
  { at: [-104.969, 39.5539], label: 'HIGHLANDS RANCH' },
]

const HIGHWAYS = [
  { path: [[-104.987, 39.43], [-104.99, 39.62], [-104.985, 39.74], [-104.975, 39.87], [-104.965, 40.02]], shields: [{ at: [-104.995, 39.9], t: '25' }, { at: [-104.978, 39.52], t: '25' }] },
  { path: [[-105.26, 39.745], [-105.05, 39.745], [-104.9, 39.75], [-104.7, 39.755], [-104.6, 39.76]], shields: [{ at: [-105.19, 39.735], t: '70' }, { at: [-104.66, 39.745], t: '70' }] },
  { path: [[-104.885, 39.63], [-104.845, 39.68], [-104.83, 39.73], [-104.815, 39.79]], shields: [{ at: [-104.822, 39.755], t: '225' }] },
  { path: [[-104.985, 39.8], [-104.9, 39.85], [-104.8, 39.93], [-104.72, 40.0]], shields: [{ at: [-104.86, 39.885], t: '76' }] },
  { path: [[-104.72, 39.55], [-104.68, 39.7], [-104.68, 39.88], [-104.75, 40.02]], shields: [{ at: [-104.665, 39.8], t: '470' }] },
  { path: [[-105.1, 39.55], [-105.09, 39.62], [-105.05, 39.68]], shields: [{ at: [-105.075, 39.575], t: '470' }] },
]

const W = 800
const H = 660
const PAD = 24
const KX = 0.769 // cos(39.7 deg): longitude degrees are shorter than latitude degrees here

function buildProjection(features) {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  const visit = (c) => {
    const x = c[0] * KX
    const y = -c[1]
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const walk = (g) => {
    if (g.type === 'Point') visit(g.coordinates)
    else if (g.type === 'LineString') g.coordinates.forEach(visit)
    else if (g.type === 'Polygon') g.coordinates[0].forEach(visit)
  }
  features.forEach((f) => walk(f.geometry))
  const scale = Math.min((W - 2 * PAD) / (maxX - minX), (H - 2 * PAD) / (maxY - minY))
  const ox = (W - (maxX - minX) * scale) / 2
  const oy = (H - (maxY - minY) * scale) / 2
  return ([lng, lat]) => [ox + (lng * KX - minX) * scale, oy + (-lat - minY) * scale]
}

function LayerToggle({ label, pressed, onToggle }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onToggle}
      className={`rounded-full px-4 py-2 text-[13px] font-semibold transition-colors duration-200 ${
        pressed
          ? 'bg-gold-400 text-sofrito-950'
          : 'border border-white/20 text-white/60 hover:border-white/45 hover:text-white'
      }`}
    >
      {label}
    </button>
  )
}

/**
 * DispatchMap: the demo map panel for the marketing live preview:
 * coverage, routes, job pins by status, at-risk pins flagged, storm
 * overlay with its callout, and the same day summarized as text beside
 * the map. The demo KPI strip (DemoKpiStrip) renders above the charts,
 * ahead of this block, per the founder's ordering (TW-1149, Rosa N1).
 */
export default function DispatchMap() {
  const [layers, setLayers] = useState({ jobs: true, coverage: true, storm: true })
  const toggle = (key) => setLayers((prev) => ({ ...prev, [key]: !prev[key] }))
  const layerStyle = (on) => ({ opacity: on ? 1 : 0, transition: 'opacity 500ms ease', pointerEvents: 'none' })

  const model = useMemo(() => {
    const project = buildProjection(geojson.features)
    const byLayer = (layer) => geojson.features.filter((f) => f.properties.layer === layer)
    const jobs = byLayer('job').map((f) => ({ ...f.properties, at: project(f.geometry.coordinates) }))
    const routes = byLayer('route').map((f) => ({
      ...f.properties,
      points: f.geometry.coordinates.map((c) => project(c).join(',')).join(' '),
    }))
    const coverage = byLayer('coverage')[0]
    const storm = byLayer('storm')[0]
    const ring = (feature) => feature.geometry.coordinates[0].map((c) => project(c).join(',')).join(' ')
    const ringBounds = (points) => {
      const pts = points.split(' ').map((p) => p.split(',').map(Number))
      const xs = pts.map((p) => p[0])
      const ys = pts.map((p) => p[1])
      return { cx: (Math.min(...xs) + Math.max(...xs)) / 2, top: Math.min(...ys) }
    }
    const stormRing = ring(storm)
    const sb = ringBounds(stormRing)
    return {
      project,
      jobs,
      routes,
      coverageRing: ring(coverage),
      stormRing,
      stormProps: storm.properties,
      stormChipAt: [sb.cx - 124, Math.max(6, sb.top - 36)],
      hq: project(meta.shop.hq.coordinates),
      atRiskJobs: jobs.filter((j) => j.atRisk),
    }
  }, [])

  return (
    <div className="mt-14">
      <div className="overflow-hidden rounded-[1.75rem] border border-sofrito-800 bg-sofrito-950 text-white shadow-[0_40px_90px_-40px_rgba(10,10,24,0.55)]">
        <div className="flex flex-wrap items-end justify-between gap-5 px-7 pt-8 sm:px-10">
          <div className="max-w-xl">
            <div className="tt-kicker text-gold-400">Dispatch map</div>
            <h3 className="mt-3 text-3xl font-bold tracking-tight">The whole day, on one map.</h3>
            <p className="mt-3 text-[15px] leading-7 text-white/60">
              Every job pinned, every crew routed, the service area drawn, and the storm cell that puts{' '}
              {money(kpis.revenueAtRiskUsd)} of today&rsquo;s work at risk, already on the board. This is
              the screen the morning runs on.
            </p>
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Map layers">
            <LayerToggle label="Jobs & routes" pressed={layers.jobs} onToggle={() => toggle('jobs')} />
            <LayerToggle label="Coverage" pressed={layers.coverage} onToggle={() => toggle('coverage')} />
            <LayerToggle label="Storm watch" pressed={layers.storm} onToggle={() => toggle('storm')} />
          </div>
        </div>

        <div className="grid gap-0 lg:grid-cols-[1.62fr_1fr]">
          <div className="px-4 pb-4 pt-5 sm:px-6 sm:pb-6">
            <svg
              viewBox={`0 0 ${W} ${H}`}
              className="h-auto w-full rounded-3xl border border-white/10"
              role="img"
              aria-label={`Demo dispatch map of the Denver metro for ${meta.shop.name}: ${kpis.jobsToday} job pins across ${kpis.crewsOut} crew routes inside one service area, with a fictional storm cell over the northeast metro putting ${kpis.jobsAtRisk} jobs at risk. The same day is summarized as text beside the map.`}
            >
              <rect x="0" y="0" width={W} height={H} fill="#0d1526" />
              <g stroke="#ffffff" strokeOpacity="0.045">
                {Array.from({ length: 19 }, (_, i) => (
                  <line key={`v${i}`} x1={40 * (i + 1)} y1="0" x2={40 * (i + 1)} y2={H} />
                ))}
                {Array.from({ length: 14 }, (_, i) => (
                  <line key={`h${i}`} x1="0" y1={40 * (i + 1)} x2={W} y2={40 * (i + 1)} />
                ))}
              </g>

              <g style={layerStyle(layers.coverage)} className="motion-reduce:transition-none">
                <polygon points={model.coverageRing} fill="#818cf8" fillOpacity="0.07" stroke="#818cf8" strokeOpacity="0.45" strokeWidth="1.5" strokeDasharray="7 6" />
              </g>

              <g fill="none" strokeLinecap="round">
                {HIGHWAYS.map((h) => (
                  <g key={h.shields[0].t + h.path[0][0]}>
                    <polyline points={h.path.map((c) => model.project(c).join(',')).join(' ')} stroke="#54708f" strokeOpacity="0.35" strokeWidth="8" strokeLinejoin="round" />
                    <polyline points={h.path.map((c) => model.project(c).join(',')).join(' ')} stroke="#dbeafe" strokeOpacity="0.16" strokeWidth="1.4" strokeDasharray="10 12" strokeLinejoin="round" />
                  </g>
                ))}
              </g>
              {HIGHWAYS.flatMap((h) =>
                h.shields.map((s) => {
                  const [x, y] = model.project(s.at)
                  return (
                    <g key={`${s.t}-${s.at[0]}`} transform={`translate(${x},${y})`}>
                      <rect x="-13" y="-9" width="26" height="18" rx="5" fill="#1d2c44" stroke="#54708f" strokeOpacity="0.6" />
                      <text x="0" y="3.5" textAnchor="middle" fontSize="9" fontWeight="700" fill="#cbd5e1">
                        {s.t}
                      </text>
                    </g>
                  )
                }),
              )}
              {CITIES.map((c) => {
                const [x, y] = model.project(c.at)
                return (
                  <text key={c.label} x={x} y={y} textAnchor="middle" fontSize="12" fontWeight="600" letterSpacing="2.2" fill="#8fa3c0" fillOpacity="0.8">
                    {c.label}
                  </text>
                )
              })}

              <g style={layerStyle(layers.storm)} className="motion-reduce:transition-none">
                <polygon points={model.stormRing} fill="#f59e0b" fillOpacity="0.13" stroke="#f59e0b" strokeOpacity="0.65" strokeWidth="1.8" strokeDasharray="7 6" />
                <g transform={`translate(${model.stormChipAt[0]},${model.stormChipAt[1]})`}>
                  <rect x="0" y="0" width="248" height="24" rx="12" fill="#f59e0b" />
                  <text x="124" y="16" textAnchor="middle" fontSize="10" fontWeight="800" letterSpacing="1.1" fill="#1a1207">
                    STORM CELL DEMO-1 · {model.stormProps.arrivalWindowStart.replace(' PM', '')}–{model.stormProps.arrivalWindowEnd}
                  </text>
                </g>
              </g>

              <g style={layerStyle(layers.jobs)} className="motion-reduce:transition-none">
                {model.routes.map((route) => (
                  <g key={route.id}>
                    <polyline points={route.points} fill="none" stroke={route.color} strokeOpacity="0.18" strokeWidth="7" strokeLinejoin="round" strokeLinecap="round" />
                    <polyline points={route.points} fill="none" stroke={route.color} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
                  </g>
                ))}
                {model.jobs.map((job) => (
                  <g key={job.id} transform={`translate(${job.at[0]},${job.at[1]})`}>
                    <title>{`${job.id} · ${job.jobType} · ${job.area} · ${money(job.ticketUsd)} · ${job.windowStart} window · ${STATUS_LABEL[job.status]}${job.atRisk ? ' · at risk in the storm cell' : ''}${job.recovered ? ' · recovered by Twistor' : ''}`}</title>
                    {job.atRisk && layers.storm && <circle r="11.5" fill="none" stroke="#f59e0b" strokeWidth="2.2" />}
                    <circle r="7" fill={STATUS_FILL[job.status]} stroke={job.status === 'scheduled' ? '#94a3b8' : '#0d1526'} strokeWidth="1.6" />
                    {job.recovered && <circle r="2.4" fill="#f5b942" stroke="#0d1526" strokeWidth="0.8" />}
                  </g>
                ))}
                <g transform={`translate(${model.hq[0]},${model.hq[1]})`}>
                  <rect x="-11" y="-11" width="22" height="22" rx="6" fill="#f5b942" stroke="#0d1526" strokeWidth="2" />
                  <text y="3.5" textAnchor="middle" fontSize="8.5" fontWeight="900" fill="#0d1526">
                    HQ
                  </text>
                  <text x="18" y="4" fontSize="12" fontWeight="700" fill="#f8fafc">
                    Shop HQ · Denver Tech Center
                  </text>
                </g>
              </g>
            </svg>
            <p className="px-2 pt-4 text-[13px] leading-6 text-white/55">{meta.labelingLine}</p>
            <ul className="flex flex-wrap gap-x-6 gap-y-2.5 px-2 pt-5 text-[13px] font-medium text-white/55" aria-label="Map legend">
              <li className="flex items-center gap-2">
                <span className="h-[10px] w-[10px] rounded-full bg-emerald-400" aria-hidden="true" /> Completed
              </li>
              <li className="flex items-center gap-2">
                <span className="h-[10px] w-[10px] rounded-full bg-gold-400" aria-hidden="true" /> On site
              </li>
              <li className="flex items-center gap-2">
                <span className="h-[10px] w-[10px] rounded-full bg-indigo-400" aria-hidden="true" /> En route
              </li>
              <li className="flex items-center gap-2">
                <span className="h-[10px] w-[10px] rounded-full border border-slate-400 bg-sofrito-950" aria-hidden="true" /> Scheduled
              </li>
              <li className="flex items-center gap-2">
                <span className="h-[14px] w-[14px] rounded-full border-2 border-amber-500" aria-hidden="true" /> In the storm&rsquo;s path
              </li>
              <li className="flex items-center gap-2">
                <span className="h-[8px] w-[8px] rounded-full bg-gold-400 ring-1 ring-sofrito-950" aria-hidden="true" /> Recovered by Twistor
              </li>
            </ul>
          </div>

          <div className="border-t border-white/10 px-7 py-7 sm:px-8 lg:border-l lg:border-t-0">
            <div className="tt-kicker text-white/50">Today&rsquo;s board</div>
            <div className="mt-5 space-y-5">
              {model.routes.map((route) => (
                <div key={route.id} className="flex gap-3">
                  <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: route.color }} aria-hidden="true" />
                  <div>
                    <p className="text-sm font-bold tracking-tight">
                      {route.crewName} <span className="font-medium text-white/40">· {route.lead}</span>
                    </p>
                    <p className="text-[13px] leading-5 text-white/50">
                      {route.stops} stops · {route.routeMiles.toFixed(1)} route miles · {money(route.revenueUsd)}
                    </p>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-6 rounded-2xl border border-white/10 bg-white/[0.04] p-5">
              <p className="text-sm font-semibold leading-6">
                {kpis.jobsRecoveredToday} jobs recovered today · {money(kpis.recoveredRevenueUsd)}
              </p>
              <p className="mt-1 text-[13px] leading-6 text-white/55">
                Caught from missed calls and stalled follow-ups before they became somebody else&rsquo;s
                work. That is the leak this page exists to close.
              </p>
            </div>

            {layers.storm && (
              <div className="mt-6 rounded-2xl border border-amber-400/25 bg-amber-400/[0.07] p-5">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-amber-400/20 text-amber-300">
                    <Lightning className="h-4 w-4" />
                  </span>
                  <span className="tt-kicker text-amber-300">Storm opportunity</span>
                </div>
                <h4 className="mt-3 text-base font-bold tracking-tight">{model.stormProps.name}</h4>
                <p className="mt-2 text-sm leading-6 text-white/65">
                  {model.stormProps.severity}. Arrives {model.stormProps.arrivalWindowStart} to{' '}
                  {model.stormProps.arrivalWindowEnd}; {model.stormProps.movement.toLowerCase()}.
                </p>
                <p className="mt-3 text-sm font-semibold text-amber-200">
                  {money(model.stormProps.revenueInPathUsd)} across {model.stormProps.jobsInPath} jobs in the path
                </p>
                <ul className="mt-3 space-y-2.5">
                  {model.atRiskJobs.map((job) => (
                    <li key={job.id} className="text-[13px] leading-5">
                      <span className="font-semibold text-white">
                        {job.windowStart} · {job.jobType}
                      </span>
                      <span className="block text-white/50">
                        {job.area} · {money(job.ticketUsd)} · {job.crewName}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
