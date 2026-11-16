import demoData from './demo-map-data.json'

/* TW-1149: the demo KPI strip for the marketing live preview.
 * Founder ordering (2026-11-16, Rosa gate note N1): the KPI strip
 * renders above the charts in the data section, then the dispatch map
 * as the hero. The strip was originally the top of the DispatchMap
 * block; it moved here so the page can order it ahead of the charts.
 *
 * Data: Maya's demo data layer (demo-map-data.json beside this file,
 * copied verbatim). Every figure is read from that file as given;
 * nothing is recomputed client side beyond formatting and the
 * route-mile total the schema note defines.
 */

const { meta, kpis, geojson } = demoData

const money = (n) => `$${n.toLocaleString('en-US')}`

export default function DemoKpiStrip() {
  const routes = geojson.features
    .filter((f) => f.properties.layer === 'route')
    .map((f) => f.properties)
  const routeMilesTotal = routes.reduce((n, r) => n + r.routeMiles, 0)

  const kpiTiles = [
    {
      label: 'Jobs today',
      value: String(kpis.jobsToday),
      note: `${kpis.jobsCompleted} done · ${kpis.jobsOnSite} on site · ${kpis.jobsEnRoute} en route · ${kpis.jobsScheduled} scheduled`,
    },
    {
      label: 'Crews out',
      value: String(kpis.crewsOut),
      note: `${routes.map((r) => r.stops).join(' · ')} stops · ${routeMilesTotal.toFixed(1)} route miles`,
    },
    {
      label: 'Revenue scheduled',
      value: money(kpis.revenueScheduledUsd),
      note: `Average ticket ${money(Math.round(kpis.averageTicketUsd))}`,
    },
    {
      label: 'Revenue at risk',
      value: money(kpis.revenueAtRiskUsd),
      note: `${kpis.jobsAtRisk} jobs inside the storm cell`,
    },
  ]

  return (
    <div className="mt-14">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="tt-kicker text-slate-500">
          {meta.shop.name} · {meta.day}
        </p>
        <span className="tt-sample-badge on-light">Demo data</span>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Demo shop key numbers from the demo data layer">
        {kpiTiles.map((kpi) => (
          <div key={kpi.label} className="rounded-3xl border border-slate-200/80 bg-white p-5 shadow-[0_18px_44px_-30px_rgba(10,10,24,0.25)]">
            <dt className="tt-kicker text-slate-500">{kpi.label}</dt>
            <dd className="mt-2 text-3xl font-bold tracking-tight text-slate-950">{kpi.value}</dd>
            <dd className="mt-1 text-[13px] leading-5 text-slate-500">{kpi.note}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
