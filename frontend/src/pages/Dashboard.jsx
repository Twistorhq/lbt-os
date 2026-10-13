import { isProUnlocked } from '../lib/pro'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { metricsApi, orgApi } from '../lib/api'
import StatCard from '../components/ui/StatCard'
import RevenueChart from '../components/charts/RevenueChart'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  PieChart,
  Pie,
  Cell,
  RadialBarChart,
  RadialBar,
  PolarAngleAxis,
} from 'recharts'

const fmt$ = (n) => `$${(n || 0).toLocaleString('en-US', { minimumFractionDigits: 0 })}`
const fmtPct = (n) => `${(n || 0).toFixed(1)}%`
const CHART_COLORS = ['#2563eb', '#0f766e', '#f59e0b', '#dc2626', '#7c3aed', '#64748b']

export default function Dashboard() {
  const qc = useQueryClient()

  const { data: metrics, isLoading } = useQuery({
    queryKey: ['metrics', 30],
    queryFn: () => metricsApi.dashboard(30).then((r) => r.data),
  })
  const { data: trend } = useQuery({
    queryKey: ['revenue-trend'],
    queryFn: () => metricsApi.revenueTrend(12).then((r) => r.data),
  })
  const { data: segments } = useQuery({
    queryKey: ['metrics-segments'],
    queryFn: () => metricsApi.segments(30).then((r) => r.data),
  })
  const { data: forecast } = useQuery({
    queryKey: ['metrics-forecast'],
    queryFn: () => metricsApi.forecast(16).then((r) => r.data),
  })
  const { data: org } = useQuery({
    queryKey: ['org-me'],
    queryFn: () => orgApi.getMe().then((r) => r.data),
  })
  const { data: workspaceStatus } = useQuery({
    queryKey: ['workspace-status'],
    queryFn: () => orgApi.workspaceStatus().then((r) => r.data),
  })

  const reseedDemo = useMutation({
    mutationFn: (industry) => orgApi.reseedDemo({ industry }).then((r) => r.data),
    onSuccess: () => {
      ;['workspace-status', 'metrics', 'revenue-trend', 'org-me', 'metrics-segments', 'metrics-forecast'].forEach((key) =>
        qc.invalidateQueries({ queryKey: [key] }),
      )
      qc.invalidateQueries({ queryKey: ['metrics', 30] })
      qc.invalidateQueries({ queryKey: ['revenue-trend'] })
    },
  })
  const clearDemo = useMutation({
    mutationFn: () => orgApi.clearDemo().then((r) => r.data),
    onSuccess: () => {
      ;['workspace-status', 'metrics', 'revenue-trend', 'org-me', 'metrics-segments', 'metrics-forecast'].forEach((key) =>
        qc.invalidateQueries({ queryKey: [key] }),
      )
    },
  })

  if (isLoading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="rounded-[2rem] border border-white/70 bg-white/80 p-7 shadow-[0_24px_60px_-32px_rgba(15,23,42,0.35)]">
          <div className="h-4 w-28 rounded-full bg-slate-200" />
          <div className="mt-4 h-10 w-72 rounded-full bg-slate-200" />
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            {[...Array(3)].map((_, i) => <div key={i} className="h-24 rounded-[1.5rem] bg-slate-100" />)}
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[...Array(4)].map((_, i) => <div key={i} className="h-40 rounded-[1.5rem] bg-slate-200" />)}
        </div>
      </div>
    )
  }

  const r = metrics?.revenue   || {}
  const l = metrics?.leads     || {}
  const c = metrics?.customers || {}
  const e = metrics?.expenses  || {}
  const brief = metrics?.analyst_brief || {}
  const isPro = isProUnlocked(org) // TW-484: owner SSO + dev flag
  const hasActivity = [r.total, l.total, c.total, e.total].some((value) => Number(value) > 0)
  const template = workspaceStatus?.template
  const latestSync = workspaceStatus?.latest_sync
  const latestAudit = workspaceStatus?.latest_audit
  const workspaceMode = workspaceStatus?.workspace_mode || 'blank'
  const revenueMix = Object.entries(r.by_source || {}).sort(([, a], [, b]) => b - a)
  const expenseMix = Object.entries(e.by_category || {}).sort(([, a], [, b]) => b - a)
  const variance = brief.variance_breakdown || {}
  const dataConfidence = brief.data_confidence ?? 0
  const channelChartData = (segments?.segments || [])
    .slice()
    .sort((a, b) => b.revenue - a.revenue)
    .slice(0, 6)
    .map((seg) => ({
      source: formatLabel(seg.source),
      revenue: Math.round(seg.revenue || 0),
      conversion: Number(seg.conversion_rate_pct || 0),
      leads: seg.leads || 0,
    }))
  const expenseChartData = expenseMix.slice(0, 6).map(([name, value]) => ({
    name: formatLabel(name),
    value: Number(value || 0),
  }))
  const funnelData = [
    { label: 'Leads', value: l.total || 0, detail: 'Created' },
    { label: 'Resolved', value: (l.won || 0) + (l.lost || 0), detail: 'Won or lost' },
    { label: 'Won', value: l.won || 0, detail: 'Closed' },
  ]
  const marginPct = Math.max(0, Math.min(100, Number(r.margin_pct || 0)))
  const operatingMapData = buildOperatingMapData({ revenue: r, leads: l, customers: c, expenses: e })

  const highlightItems = [
    {
      label: 'Pipeline health',
      value: l.total ? `${l.total} active lead${l.total === 1 ? '' : 's'}` : 'No leads yet',
      detail: l.total ? `${l.won} marked won in this window` : 'Add your first inbound lead to unlock conversion reporting.',
    },
    {
      label: 'Cash position',
      value: e.total ? `${fmt$(e.total)} tracked spend` : 'Expenses are clear',
      detail: e.total ? 'Margin and profit update automatically as you add sales and costs.' : 'Your operating margin will appear once expenses are logged.',
    },
    {
      label: 'Retention signal',
      value: c.total ? `${fmtPct(c.repeat_pct)} repeat rate` : 'No customer history yet',
      detail: c.total ? `${c.repeat} returning customer${c.repeat === 1 ? '' : 's'} identified in the last 30 days.` : 'Won leads and customer orders will build this view.',
    },
  ]

  // Alerts — smarter with confidence gating
  const alerts = []
  if (l.missed_follow_ups > 0) {
    alerts.push({
      type: 'danger',
      message: `${l.missed_follow_ups} lead${l.missed_follow_ups > 1 ? 's' : ''} have a follow-up due but haven't been contacted — this is leaking warm demand right now.`,
    })
  }
  if (l.conversion_rate_pct < 20 && l.total >= 5) {
    alerts.push({ type: 'warning', message: `Conversion rate is ${fmtPct(l.conversion_rate_pct)} — well below healthy range. Review follow-up speed and quoting process.` })
  }
  if (c.repeat_pct < 20 && c.total >= 10) {
    alerts.push({ type: 'warning', message: `Only ${fmtPct(c.repeat_pct)} of customers are returning. A simple reactivation offer could lift this meaningfully.` })
  }
  if (e.total > r.total && r.total > 0) {
    alerts.push({ type: 'danger', message: `Expenses ($${fmt$(e.total)}) exceed revenue ($${fmt$(r.total)}) this period — the business is burning cash, not building it.` })
  }
  if (alerts.length === 0) {
    alerts.push({ type: 'success', message: hasActivity ? 'No critical issues in the current window.' : 'Workspace is ready. Add leads, sales, and expenses to light up insights.' })
  }

  return (
    <div className="space-y-6">

      <section className="page-command">
        <div>
          <div className="section-kicker">Operating snapshot</div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-copy">One lightweight view of revenue, pipeline, margin, and customer momentum.</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <WorkspaceModeBadge mode={workspaceMode} />
            {dataConfidence > 0 && (
              <span className="inline-flex rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                {dataConfidence >= 1 ? 'High confidence' : dataConfidence >= 0.5 ? 'Moderate data' : 'Thin data'}
              </span>
            )}
          </div>
        </div>
        <HealthPill score={brief.health_score} label={brief.health_label} fallback={hasActivity ? 'Live activity flowing' : 'Ready for first entries'} />
      </section>

      {/* KPI stat cards */}
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total Revenue" value={fmt$(r.total)} sub={`${fmtPct(r.margin_pct)} margin · ${fmt$(r.avg_deal_size || 0)} avg deal`} color="blue" />
        <StatCard label="Gross Profit" value={fmt$(r.profit)} sub={`${fmt$(e.total)} in tracked operating expenses`} color="green" />
        <StatCard label="Lead Conversion" value={fmtPct(l.conversion_rate_pct)} sub={`${l.won || 0} won out of ${l.total || 0} tracked leads`} color="violet" />
        <StatCard label="Repeat Customers" value={fmtPct(c.repeat_pct)} sub={`${c.repeat || 0} of ${c.total || 0} customers came back`} color="amber" />
      </section>

      {hasActivity && (
        <section className="card-surface overflow-hidden p-0">
          <div className="grid gap-0 xl:grid-cols-[1.25fr_0.75fr]">
            <div className="border-b border-slate-100 p-6 xl:border-b-0 xl:border-r">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Performance Command Center</div>
                  <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">All dashboard data in one view</h2>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
                    A normalized line graph for dollars, counts, and percentages with exact values on hover.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {['Money', 'Pipeline', 'Customers', 'Rates'].map((label, idx) => (
                    <span key={label} className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-600">
                      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: CHART_COLORS[idx] }} />
                      {label}
                    </span>
                  ))}
                </div>
              </div>
              <div className="mt-6 h-[340px]">
                <OperatingMapChart data={operatingMapData} />
              </div>
              <div className="mt-7 border-t border-slate-100 pt-6">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <h3 className="text-lg font-semibold tracking-tight text-slate-950">Channel revenue bars</h3>
                    <p className="mt-1 text-sm leading-6 text-slate-500">Revenue and conversion by source for the same operating window.</p>
                  </div>
                  <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                    Last 30 days
                  </div>
                </div>
                <div className="mt-5 h-[280px]">
                  <ChannelRevenueChart data={channelChartData} />
                </div>
              </div>
            </div>

            <div className="grid gap-0 sm:grid-cols-2 xl:grid-cols-1">
              <div className="border-b border-slate-100 p-6 sm:border-r xl:border-r-0">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-semibold tracking-tight text-slate-950">Expense Mix</h3>
                    <p className="mt-1 text-sm leading-6 text-slate-500">Top spend categories shaping margin.</p>
                  </div>
                  <span className="text-sm font-semibold text-slate-900">{fmt$(e.total)}</span>
                </div>
                <div className="mt-4 h-[210px]">
                  <ExpenseDonut data={expenseChartData} />
                </div>
              </div>

              <div className="grid gap-0 sm:grid-cols-2 xl:grid-cols-1">
                <div className="border-b border-slate-100 p-6">
                  <h3 className="text-lg font-semibold tracking-tight text-slate-950">Pipeline Shape</h3>
                  <p className="mt-1 text-sm leading-6 text-slate-500">Lead flow through resolved outcomes.</p>
                  <div className="mt-5">
                    <PipelineFunnel data={funnelData} total={l.total || 0} />
                  </div>
                </div>
                <div className="p-6">
                  <h3 className="text-lg font-semibold tracking-tight text-slate-950">Margin Gauge</h3>
                  <p className="mt-1 text-sm leading-6 text-slate-500">Profit efficiency after job cost.</p>
                  <div className="mt-4 h-[170px]">
                    <MarginGauge value={marginPct} />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Blank workspace onboarding */}
      {!hasActivity && (
        <section className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
          <div className="card-surface p-6">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Starter Workspace</div>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
              {template?.label ? `${template.label} starter view` : 'Your workspace is ready'}
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-600">
              {template
                ? `We'll shape your first audit around ${template.label.toLowerCase()} workflows, the lead sources you actually use, and the KPIs that matter most in this industry.`
                : 'Add real records or launch a demo workspace to light up the analyst brief, action board, and industry benchmarks.'}
            </p>
            <div className="mt-5 grid gap-3 md:grid-cols-3">
              <PreviewStrip title="Services" items={(template?.services || []).slice(0, 4)} />
              <PreviewStrip title="Lead Sources" items={(template?.lead_sources || []).slice(0, 4).map(formatLabel)} />
              <PreviewStrip title="KPI Focus" items={(template?.key_metrics || []).slice(0, 4).map(formatLabel)} />
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <Link className="btn-primary justify-center text-center" to="/app/connections">Connect a data source</Link>
              <button
                className="btn-secondary justify-center text-center"
                onClick={() => reseedDemo.mutate(org?.industry || 'hvac')}
                disabled={reseedDemo.isPending}
              >
                {reseedDemo.isPending ? 'Loading demo...' : 'Load demo workspace'}
              </button>
              <Link className="btn-secondary justify-center text-center" to="/app/leads">Add your first lead</Link>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              {['hvac', 'plumbing', 'gig_worker', 'restaurant'].map((industry) => (
                <button
                  key={industry}
                  className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600 transition-colors hover:border-brand-300 hover:text-brand-700"
                  onClick={() => reseedDemo.mutate(industry)}
                  disabled={reseedDemo.isPending}
                >
                  {reseedDemo.isPending ? 'Loading…' : `Try ${formatLabel(industry)}`}
                </button>
              ))}
            </div>
            {reseedDemo.isError && (
              <div className="mt-4 rounded-[1.2rem] border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
                {reseedDemo.error?.response?.data?.detail || 'Could not load demo data into this workspace.'}
              </div>
            )}
          </div>
          <div className="card-surface p-6">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Industry Quick Wins</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-slate-950">What this audit will look for first</h2>
            <div className="mt-4 space-y-3">
              {(template?.quick_wins || [
                'Connect one source system or load a demo to generate your first audit.',
                'Add a few leads, expenses, and sales so the dashboard can separate signal from setup.',
              ]).slice(0, 3).map((item) => (
                <div key={item} className="rounded-[1.25rem] border border-slate-200 bg-slate-50/80 p-4 text-sm leading-6 text-slate-600">
                  {item}
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Demo controls */}
      {workspaceMode === 'demo' && (
        <section className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr]">
          <div className="card-surface p-6">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Demo Workspace</div>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">Switch industries or refresh your sample data</h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-600">
              Reseed with a different industry to see how the analyst brief and segment analysis adapt to different business models.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {['hvac', 'plumbing', 'gig_worker', 'restaurant'].map((industry) => (
                <button key={industry} className="btn-secondary" onClick={() => reseedDemo.mutate(industry)} disabled={reseedDemo.isPending}>
                  {reseedDemo.isPending ? 'Reseeding...' : `Switch to ${formatLabel(industry)}`}
                </button>
              ))}
            </div>
          </div>
          <div className="card-surface p-6">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Demo Controls</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-slate-950">Manage this sandbox</h2>
            <div className="mt-4 space-y-3">
              <button className="btn-primary w-full justify-center" onClick={() => reseedDemo.mutate(org?.industry || 'hvac')} disabled={reseedDemo.isPending}>
                {reseedDemo.isPending ? 'Refreshing demo...' : 'Reseed current industry'}
              </button>
              <button className="btn-danger w-full justify-center" onClick={() => clearDemo.mutate()} disabled={clearDemo.isPending}>
                {clearDemo.isPending ? 'Clearing demo...' : 'Clear demo data'}
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Analyst brief + action board */}
      <section className="grid gap-5 xl:grid-cols-[1.4fr_0.9fr]">
        <div className="card-surface p-6">
          <div className="flex flex-col gap-4 border-b border-slate-100 pb-5 sm:flex-row sm:items-start sm:justify-between">
            <div className="max-w-2xl">
              <p className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Analyst Brief</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">Executive Summary</h2>
              <p className="mt-3 text-sm leading-7 text-slate-600">
                {brief.executive_summary || 'Once activity is flowing, LBT OS will summarize what changed, what matters, and where to focus next.'}
              </p>
            </div>
            <div className="rounded-[1.4rem] border border-slate-200 bg-slate-50/90 px-5 py-4 text-right">
              <div className="text-[0.7rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Health Score</div>
              <div className="mt-2 text-4xl font-semibold tracking-tight text-slate-950">{brief.health_score ?? '--'}</div>
              <div className="mt-1 text-sm font-medium text-slate-500">{brief.health_label || 'Pending data'}</div>
            </div>
          </div>

          {/* What changed — now 4 items including avg deal size */}
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(brief.what_changed || []).map((item) => (
              <ChangeTile key={item.label} item={item} />
            ))}
          </div>

          {/* Variance decomposition — only show when revenue changed */}
          {variance.explanation && variance.total_revenue_change !== 0 && (
            <div className="mt-5 rounded-[1.5rem] border border-brand-100 bg-brand-50/50 p-4">
              <div className="text-[0.72rem] font-semibold uppercase tracking-[0.22em] text-brand-700">Why Revenue Changed</div>
              <p className="mt-2 text-sm leading-6 text-slate-700">{variance.explanation}</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {[
                  { label: 'Lead volume', value: variance.lead_volume_effect },
                  { label: 'Conversion rate', value: variance.conversion_effect },
                  { label: 'Deal size', value: variance.deal_size_effect },
                ].map(({ label, value }) => (
                  <div key={label} className="rounded-xl border border-brand-100 bg-white/80 px-3 py-2 text-center">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">{label}</div>
                    <div className={`mt-1 text-sm font-semibold ${(value || 0) >= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
                      {(value || 0) >= 0 ? '+' : ''}{fmt$(Math.abs(value || 0))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-5 rounded-[1.5rem] border border-dashed border-slate-200 bg-slate-50/80 p-4">
            <div className="text-[0.72rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Benchmark Note</div>
            <div className="mt-2 text-sm leading-6 text-slate-600">{brief.benchmark_note}</div>
          </div>
        </div>

        <div className="card-surface p-6">
          <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Action Board</div>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">Where To Focus This Week</h2>
          <div className="mt-5 space-y-3">
            {(brief.focus_this_week || []).map((item) => (
              <FocusCard key={item.priority} item={item} />
            ))}
          </div>
        </div>
      </section>

      {/* Risks + Opportunities */}
      <section className="grid gap-5 lg:grid-cols-2">
        <InsightList
          title="Top Risks"
          subtitle="Where money, speed, or margin is leaking right now."
          items={brief.top_risks || []}
          tone="risk"
          emptyMessage="No critical risks are standing out yet. Keep the data flowing so the audit can stay honest."
        />
        <InsightList
          title="Top Opportunities"
          subtitle="The quickest ways to create better performance with the data already coming in."
          items={brief.top_opportunities || []}
          tone="opportunity"
          emptyMessage="Add more operating activity and the system will start highlighting the most recoverable wins."
        />
      </section>

      {/* Source segment analysis — new panel */}
      {segments && segments.segments && segments.segments.length > 0 && (
        <section className="card-surface p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Channel Analysis</div>
              <h2 className="mt-2 text-xl font-semibold tracking-tight text-slate-950">Conversion & Revenue by Lead Source</h2>
              <p className="mt-1 text-sm leading-6 text-slate-500">Which acquisition channels actually convert and which just generate volume.</p>
            </div>
            {segments.channel_insight && (
              <div className="max-w-sm rounded-[1.35rem] border border-brand-100 bg-brand-50/60 p-4 text-sm leading-6 text-slate-700">
                {segments.channel_insight}
              </div>
            )}
          </div>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100">
                  {['Source', 'Leads', 'Won', 'Conversion', 'Revenue', 'Avg Deal', 'Rev / Lead'].map((h) => (
                    <th key={h} className="pb-3 text-left text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-slate-400">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {segments.segments.map((seg) => {
                  const isBestConv = seg.source === segments.best_by_conversion?.source
                  const isBestRev  = seg.source === segments.best_by_revenue?.source
                  return (
                    <tr key={seg.source} className="hover:bg-slate-50/70">
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2">
                          <span className="font-medium capitalize text-slate-900">{seg.source.replaceAll('_', ' ')}</span>
                          {isBestConv && <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">Best conv.</span>}
                          {isBestRev && !isBestConv && <span className="rounded-full bg-brand-50 border border-brand-100 px-2 py-0.5 text-[10px] font-semibold text-brand-700">Top revenue</span>}
                        </div>
                      </td>
                      <td className="py-3 pr-4 text-slate-600">{seg.leads}</td>
                      <td className="py-3 pr-4 text-slate-600">{seg.won}</td>
                      <td className="py-3 pr-4">
                        <ConversionBar pct={seg.conversion_rate_pct} />
                      </td>
                      <td className="py-3 pr-4 font-semibold text-slate-900">{fmt$(seg.revenue)}</td>
                      <td className="py-3 pr-4 text-slate-600">{fmt$(seg.avg_deal_size)}</td>
                      <td className="py-3 text-slate-600">{fmt$(seg.revenue_per_lead)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Revenue trend + forecast */}
      <section className="grid gap-5 xl:grid-cols-[1.55fr_1fr]">
        <div className="card-surface p-6">
          <div className="mb-5">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Deep Dive</div>
          </div>
          <div className="flex flex-col gap-2 border-b border-slate-100 pb-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight text-slate-950">Revenue trend</h2>
              <p className="mt-1 text-sm leading-6 text-slate-500">Weekly motion across the last 12 weeks.</p>
            </div>
            <div className="text-sm font-medium text-slate-500">Updated from live sales data</div>
          </div>
          <div className="mt-5">
            <RevenueChart data={trend || []} />
          </div>
        </div>

        <div className="card-surface p-6 space-y-4">
          {/* Revenue mix */}
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-slate-950">Revenue mix</h2>
            <p className="mt-1 text-sm leading-6 text-slate-500">Where closed revenue is coming from.</p>
          </div>
          {revenueMix.length === 0 ? (
            <div className="rounded-[1.5rem] border border-dashed border-slate-200 bg-slate-50/80 p-5">
              <div className="text-sm font-semibold text-slate-700">No source attribution yet</div>
              <div className="mt-2 text-sm leading-6 text-slate-500">Tag lead sources on sales to see which channels actually convert into revenue.</div>
            </div>
          ) : (
            <div className="space-y-4">
              {revenueMix.map(([src, amt]) => {
                const pct = r.total > 0 ? (amt / r.total) * 100 : 0
                return (
                  <div key={src}>
                    <div className="mb-2 flex items-center justify-between text-sm">
                      <span className="font-medium capitalize text-slate-700">{src.replaceAll('_', ' ')}</span>
                      <span className="font-semibold text-slate-950">{fmt$(amt)}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full rounded-full bg-[linear-gradient(90deg,#2563eb_0%,#60a5fa_100%)]" style={{ width: `${pct}%` }} />
                    </div>
                    <div className="mt-1 text-xs font-medium uppercase tracking-[0.18em] text-slate-400">{pct.toFixed(1)}% of revenue</div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Forecast summary */}
          {forecast?.status === 'ok' && (
            <div className="mt-2 rounded-[1.5rem] border border-slate-200 bg-slate-50/80 p-4">
              <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-slate-400">Revenue Forecast</div>
              <div className={`mt-1 text-xs font-semibold ${
                forecast.trend_direction === 'growing' ? 'text-emerald-700' :
                forecast.trend_direction === 'declining' ? 'text-rose-700' : 'text-slate-500'
              }`}>
                Trend: {forecast.trend_direction} ({forecast.weekly_change_pct > 0 ? '+' : ''}{forecast.weekly_change_pct?.toFixed(1)}%/wk)
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {[
                  { label: '30 days', value: forecast.summary?.next_30_days },
                  { label: '60 days', value: forecast.summary?.next_60_days },
                  { label: '90 days', value: forecast.summary?.next_90_days },
                ].map(({ label, value }) => (
                  <div key={label} className="text-center">
                    <div className="text-sm font-semibold text-slate-950">{fmt$(value)}</div>
                    <div className="text-[10px] text-slate-400">{label}</div>
                  </div>
                ))}
              </div>
              {forecast.narrative && (
                <p className="mt-3 text-xs leading-5 text-slate-500">{forecast.narrative}</p>
              )}
              <Link to="/app/insights" className="mt-3 inline-flex text-xs font-semibold text-brand-600 hover:underline">
                Full forecast in AI Insights →
              </Link>
            </div>
          )}
        </div>
      </section>

      {/* Business signals + alerts */}
      <section className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="card-surface p-6">
          <h2 className="text-lg font-semibold tracking-tight text-slate-950">Business signals</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500">Operational context pulled from your current KPI mix.</p>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <SignalTile title="Lead velocity" value={l.total || 0} caption={l.total ? 'Leads tracked in the active window' : 'Start logging new opportunities to measure velocity'} />
            <SignalTile title="Won work" value={l.won || 0} caption={l.won ? 'Closed deals feeding revenue and customer history' : 'Won deals will unlock retention and revenue insights'} />
            <SignalTile title="Customer base" value={c.total || 0} caption={c.total ? 'Known customers tied to this org' : 'Customer records appear as leads are converted or added directly'} />
            <SignalTile title="Avg deal size" value={fmt$(r.avg_deal_size || 0)} caption={r.avg_deal_size ? 'Revenue per closed deal this period' : 'Track won leads to see average deal size'} />
          </div>
        </div>

        <div className="card-surface p-6">
          <div className="mb-6">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-400">Pipeline Status</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-slate-950">Sync and audit freshness</h2>
          </div>
          <div className="grid gap-3">
            <StatusPanelCard
              label="Last sync"
              title={latestSync ? `${formatLabel(latestSync.provider)} sync ${latestSync.status}` : 'No sync has run yet'}
              detail={latestSync ? `${formatDateTime(latestSync.started_at)} · ${latestSync.trigger_source} trigger` : 'Connect QuickBooks or HubSpot to pull live records.'}
              tone={latestSync?.status === 'success' ? 'good' : latestSync?.status === 'failed' ? 'danger' : 'neutral'}
            />
            <StatusPanelCard
              label="Last audit"
              title={latestAudit ? `Health score ${latestAudit.health_score}` : isPro ? 'No AI audit has run yet' : 'Free audit layer is active'}
              detail={latestAudit ? `${formatDateTime(latestAudit.generated_at)} · ${latestAudit.model_used}` : isPro ? 'Run your first audit after data lands.' : 'Your free analyst brief updates automatically.'}
              tone={latestAudit ? 'good' : 'neutral'}
            />
          </div>
          <div className="mt-6">
            <h2 className="text-lg font-semibold tracking-tight text-slate-950">Alerts</h2>
            <div className="mt-4 space-y-3">
              {alerts.map((alert, i) => (
                <Alert key={i} type={alert.type} message={alert.message} />
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Pro upsell */}
      {!isPro && (
        <section className="relative overflow-hidden rounded-[2rem] border border-brand-100 bg-[linear-gradient(135deg,#eff6ff_0%,#ffffff_45%,#dbeafe_100%)] p-6 shadow-[0_22px_65px_-35px_rgba(37,99,235,0.4)]">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(37,99,235,0.12),transparent_30%)]" />
          <div className="relative grid gap-5 lg:grid-cols-[1.1fr_0.9fr] lg:items-start">
            <div>
              <div className="text-[0.72rem] font-semibold uppercase tracking-[0.24em] text-brand-700">Pro Analyst Pod</div>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{brief.pro_positioning?.headline}</h2>
              <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-600">{brief.pro_positioning?.detail}</p>
            </div>
            <div className="space-y-3">
              {(brief.pro_positioning?.features || []).map((feature) => (
                <div key={feature} className="rounded-[1.25rem] border border-white/80 bg-white/80 px-4 py-3 text-sm leading-6 text-slate-700">
                  {feature}
                </div>
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatLabel(value) {
  return value.replaceAll('_', ' ')
}

function formatDateTime(value) {
  if (!value) return 'Pending'
  return new Date(value).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function buildOperatingMapData({ revenue, leads, customers, expenses }) {
  const groups = {
    money: { color: CHART_COLORS[0], values: [revenue.total, revenue.profit, expenses.total, revenue.avg_deal_size] },
    pipeline: { color: CHART_COLORS[1], values: [leads.total, leads.won, leads.lost, leads.missed_follow_ups] },
    customers: { color: CHART_COLORS[2], values: [customers.total, customers.repeat] },
    rates: { color: CHART_COLORS[3], values: [revenue.margin_pct, leads.conversion_rate_pct, customers.repeat_pct] },
  }

  const maxByGroup = Object.fromEntries(
    Object.entries(groups).map(([group, meta]) => [
      group,
      Math.max(...meta.values.map((value) => Number(value || 0)), 1),
    ]),
  )

  const metricRows = [
    { label: 'Revenue', shortLabel: 'Rev', value: revenue.total, group: 'money', unit: 'currency' },
    { label: 'Gross Profit', shortLabel: 'Profit', value: revenue.profit, group: 'money', unit: 'currency' },
    { label: 'Expenses', shortLabel: 'Spend', value: expenses.total, group: 'money', unit: 'currency' },
    { label: 'Avg Deal', shortLabel: 'Avg', value: revenue.avg_deal_size, group: 'money', unit: 'currency' },
    { label: 'Leads', shortLabel: 'Leads', value: leads.total, group: 'pipeline', unit: 'count' },
    { label: 'Won Leads', shortLabel: 'Won', value: leads.won, group: 'pipeline', unit: 'count' },
    { label: 'Lost Leads', shortLabel: 'Lost', value: leads.lost, group: 'pipeline', unit: 'count' },
    { label: 'Missed Follow-ups', shortLabel: 'Missed', value: leads.missed_follow_ups, group: 'pipeline', unit: 'count' },
    { label: 'Customers', shortLabel: 'Cust.', value: customers.total, group: 'customers', unit: 'count' },
    { label: 'Repeat Customers', shortLabel: 'Repeat', value: customers.repeat, group: 'customers', unit: 'count' },
    { label: 'Margin', shortLabel: 'Margin', value: revenue.margin_pct, group: 'rates', unit: 'percent' },
    { label: 'Conversion', shortLabel: 'Conv.', value: leads.conversion_rate_pct, group: 'rates', unit: 'percent' },
    { label: 'Repeat Rate', shortLabel: 'Repeat %', value: customers.repeat_pct, group: 'rates', unit: 'percent' },
  ]

  return metricRows.map((metric) => {
    const value = Number(metric.value || 0)
    const normalized = metric.unit === 'percent'
      ? Math.max(0, Math.min(100, value))
      : Math.round((value / maxByGroup[metric.group]) * 100)

    return {
      ...metric,
      value,
      normalized,
      color: groups[metric.group].color,
      formattedValue: formatMetricValue(value, metric.unit),
      groupLabel: formatLabel(metric.group),
    }
  })
}

function formatMetricValue(value, unit) {
  if (unit === 'currency') return fmt$(value)
  if (unit === 'percent') return fmtPct(value)
  return Number(value || 0).toLocaleString('en-US')
}

// ── Sub-components ──────────────────────────────────────────────────────────

function ChartEmpty({ title, detail }) {
  return (
    <div className="flex h-full min-h-[180px] flex-col items-center justify-center rounded-[1.5rem] border border-dashed border-slate-200 bg-slate-50/70 px-6 text-center">
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      <div className="mt-2 max-w-xs text-sm leading-6 text-slate-500">{detail}</div>
    </div>
  )
}

function OperatingMapTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const point = payload[0]?.payload
  if (!point) return null
  return (
    <div className="rounded-2xl border border-slate-200 bg-white/95 px-4 py-3 text-xs shadow-[0_18px_50px_-28px_rgba(15,23,42,0.45)] backdrop-blur">
      <div className="font-semibold text-slate-950">{label}</div>
      <div className="mt-1 text-slate-500">{point.groupLabel}</div>
      <div className="mt-3 flex items-center justify-between gap-8">
        <span className="text-slate-500">Actual value</span>
        <span className="font-semibold text-slate-950">{point.formattedValue}</span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-8">
        <span className="text-slate-500">Graph position</span>
        <span className="font-semibold text-slate-950">{point.normalized}/100</span>
      </div>
    </div>
  )
}

function OperatingMapChart({ data }) {
  if (!data.length) {
    return <ChartEmpty title="No operating data yet" detail="Add sales, leads, customers, and expenses to see every dashboard data point in one view." />
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 20, right: 18, left: -18, bottom: 18 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis
          dataKey="shortLabel"
          interval={0}
          tick={{ fontSize: 11, fill: '#64748b' }}
          axisLine={false}
          tickLine={false}
          angle={-18}
          textAnchor="end"
          height={56}
        />
        <YAxis
          domain={[0, 100]}
          ticks={[0, 25, 50, 75, 100]}
          tickFormatter={(value) => `${value}`}
          tick={{ fontSize: 11, fill: '#64748b' }}
          axisLine={false}
          tickLine={false}
          width={48}
        />
        <Tooltip content={<OperatingMapTooltip />} />
        <Line
          type="monotone"
          dataKey="normalized"
          stroke="#0f172a"
          strokeWidth={3}
          dot={({ cx, cy, payload }) => (
            <circle cx={cx} cy={cy} r={6} fill={payload.color} stroke="#ffffff" strokeWidth={2.5} />
          )}
          activeDot={{ r: 8, stroke: '#ffffff', strokeWidth: 3 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-2xl border border-slate-200 bg-white/95 px-4 py-3 text-xs shadow-[0_18px_50px_-28px_rgba(15,23,42,0.45)] backdrop-blur">
      <div className="mb-2 font-semibold text-slate-950">{label}</div>
      {payload.map((item) => (
        <div key={item.dataKey} className="flex items-center justify-between gap-5 py-0.5 text-slate-600">
          <span>{item.name}</span>
          <span className="font-semibold text-slate-950">
            {item.dataKey === 'conversion' ? `${Number(item.value || 0).toFixed(1)}%` : item.dataKey === 'leads' ? item.value : fmt$(item.value)}
          </span>
        </div>
      ))}
    </div>
  )
}

function ChannelRevenueChart({ data }) {
  if (!data.length) {
    return <ChartEmpty title="No channel revenue yet" detail="Add sales with source attribution to compare channel quality and conversion." />
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 12, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis dataKey="source" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} dy={10} />
        <YAxis yAxisId="left" tickFormatter={fmt$} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={72} />
        <YAxis yAxisId="right" orientation="right" tickFormatter={(v) => `${v}%`} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={42} />
        <Tooltip content={<ChartTooltip />} />
        <Bar yAxisId="left" dataKey="revenue" name="Revenue" radius={[12, 12, 4, 4]} fill="#2563eb" />
        <Bar yAxisId="right" dataKey="conversion" name="Conversion" radius={[12, 12, 4, 4]} fill="#0f766e" />
      </BarChart>
    </ResponsiveContainer>
  )
}

function ExpenseDonut({ data }) {
  if (!data.length) {
    return <ChartEmpty title="No expenses yet" detail="Track cost categories to see where margin is being consumed." />
  }
  const total = data.reduce((sum, item) => sum + item.value, 0)
  return (
    <div className="grid h-full grid-cols-[0.95fr_1.05fr] items-center gap-3">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} innerRadius="62%" outerRadius="86%" paddingAngle={3} dataKey="value" stroke="none">
            {data.map((entry, idx) => (
              <Cell key={entry.name} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(value) => [fmt$(value), 'Spend']} contentStyle={{ borderRadius: 16, border: '1px solid #e2e8f0', fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
      <div className="space-y-2">
        {data.slice(0, 4).map((item, idx) => (
          <div key={item.name} className="flex items-center justify-between gap-2 text-xs">
            <div className="flex min-w-0 items-center gap-2">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: CHART_COLORS[idx % CHART_COLORS.length] }} />
              <span className="truncate font-medium text-slate-700">{item.name}</span>
            </div>
            <span className="font-semibold text-slate-950">{total ? Math.round((item.value / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function PipelineFunnel({ data, total }) {
  const max = Math.max(total, ...data.map((item) => item.value), 1)
  return (
    <div className="space-y-3">
      {data.map((item, idx) => {
        const pct = Math.max(6, (item.value / max) * 100)
        return (
          <div key={item.label}>
            <div className="mb-1.5 flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-[0.16em] text-slate-400">{item.label}</span>
              <span className="font-semibold text-slate-950">{item.value}</span>
            </div>
            <div className="h-3 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${CHART_COLORS[idx]} 0%, #93c5fd 100%)` }}
              />
            </div>
            <div className="mt-1 text-xs text-slate-400">{item.detail}</div>
          </div>
        )
      })}
    </div>
  )
}

function MarginGauge({ value }) {
  const data = [{ name: 'margin', value, fill: value >= 30 ? '#0f766e' : value >= 15 ? '#f59e0b' : '#dc2626' }]
  return (
    <div className="relative h-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart innerRadius="72%" outerRadius="100%" data={data} startAngle={180} endAngle={0}>
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
          <RadialBar dataKey="value" cornerRadius={16} background={{ fill: '#e2e8f0' }} />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="absolute inset-x-0 bottom-5 text-center">
        <div className="text-3xl font-semibold tracking-tight text-slate-950">{value.toFixed(1)}%</div>
        <div className="mt-1 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">gross margin</div>
      </div>
    </div>
  )
}

function ConversionBar({ pct }) {
  const color = pct >= 30 ? 'bg-emerald-500' : pct >= 15 ? 'bg-amber-400' : 'bg-rose-400'
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(100, pct * 2)}%` }} />
      </div>
      <span className={`text-xs font-semibold ${pct >= 30 ? 'text-emerald-700' : pct >= 15 ? 'text-amber-700' : 'text-rose-700'}`}>
        {pct.toFixed(1)}%
      </span>
    </div>
  )
}

function PreviewStrip({ title, items }) {
  return (
    <div className="rounded-[1.4rem] border border-slate-200/80 bg-slate-50/80 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        {items.length ? items.map((item) => (
          <span key={item} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700">{item}</span>
        )) : <span className="text-sm text-slate-500">Waiting for setup</span>}
      </div>
    </div>
  )
}

function WorkspaceModeBadge({ mode }) {
  const styles = { live: 'border-emerald-200 bg-emerald-50 text-emerald-700', demo: 'border-amber-200 bg-amber-50 text-amber-700', blank: 'border-slate-200 bg-slate-50 text-slate-500' }
  const labels = { live: 'Live workspace', demo: 'Demo workspace', blank: 'Setup in progress' }
  return <span className={`inline-flex rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${styles[mode] || styles.blank}`}>{labels[mode] || labels.blank}</span>
}

function StatusPanelCard({ label, title, detail, tone }) {
  const styles = { good: 'border-emerald-200 bg-emerald-50/70', danger: 'border-rose-200 bg-rose-50/70', neutral: 'border-slate-200 bg-slate-50/80' }
  return (
    <div className={`rounded-[1.35rem] border p-4 ${styles[tone] || styles.neutral}`}>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.22em] text-slate-400">{label}</div>
      <div className="mt-2 text-sm font-semibold text-slate-900">{title}</div>
      <div className="mt-1 text-sm leading-6 text-slate-600">{detail}</div>
    </div>
  )
}

function SignalTile({ title, value, caption }) {
  return (
    <div className="rounded-[1.4rem] border border-slate-200/80 bg-slate-50/80 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
      <div className="mt-3 text-2xl font-semibold tracking-tight text-slate-950">{value}</div>
      <div className="mt-2 text-sm leading-6 text-slate-500">{caption}</div>
    </div>
  )
}

function Alert({ type, message }) {
  const styles = { warning: 'border-amber-200 bg-amber-50 text-amber-900', danger: 'border-rose-200 bg-rose-50 text-rose-900', success: 'border-emerald-200 bg-emerald-50 text-emerald-900' }
  const icons  = { warning: 'Watch', danger: 'Urgent', success: 'Clear' }
  return (
    <div className={`rounded-[1.35rem] border p-4 ${styles[type]}`}>
      <div className="text-[11px] font-semibold uppercase tracking-[0.24em] opacity-70">{icons[type]}</div>
      <div className="mt-2 text-sm leading-6">{message}</div>
    </div>
  )
}

function HealthPill({ score, label, fallback }) {
  if (score === undefined) {
    return <div className="inline-flex items-center rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-500">{fallback}</div>
  }
  return (
    <div className="rounded-[1.2rem] border border-slate-200 bg-white px-4 py-3 text-right shadow-sm">
      <div className="text-[0.66rem] font-semibold uppercase tracking-[0.18em] text-slate-400">Health</div>
      <div className="mt-1 text-3xl font-semibold text-slate-950">{score}</div>
      <div className="text-sm text-slate-500">{label}</div>
    </div>
  )
}

function ChangeTile({ item }) {
  const directionStyles = { up: 'text-emerald-700 bg-emerald-50 border-emerald-100', down: 'text-rose-700 bg-rose-50 border-rose-100', flat: 'text-slate-600 bg-slate-50 border-slate-100' }
  const directionLabel  = item.direction === 'up' ? 'Improved' : item.direction === 'down' ? 'Declined' : 'Flat'
  return (
    <div className={`rounded-[1.35rem] border p-4 ${directionStyles[item.direction] || directionStyles.flat}`}>
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.22em] opacity-70">{item.label}</div>
      <div className="mt-3 text-lg font-semibold">{directionLabel}</div>
      <div className="mt-1 text-sm leading-6 opacity-80">{item.detail}</div>
    </div>
  )
}

function FocusCard({ item }) {
  return (
    <div className="rounded-[1.35rem] border border-slate-200 bg-slate-50/85 p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-sm font-semibold text-white">{item.priority}</div>
        <div className="text-sm font-semibold text-slate-900">{item.title}</div>
      </div>
      <div className="mt-3 text-sm leading-6 text-slate-600">{item.detail}</div>
    </div>
  )
}

function InsightList({ title, subtitle, items, tone, emptyMessage }) {
  const toneStyles = {
    risk:        { shell: 'border-rose-100 bg-rose-50/60',    badge: 'text-rose-700 bg-rose-100' },
    opportunity: { shell: 'border-emerald-100 bg-emerald-50/60', badge: 'text-emerald-700 bg-emerald-100' },
  }
  const selected = toneStyles[tone]
  return (
    <div className="card-surface p-6">
      <h2 className="text-lg font-semibold tracking-tight text-slate-950">{title}</h2>
      <p className="mt-1 text-sm leading-6 text-slate-500">{subtitle}</p>
      {items.length === 0 ? (
        <div className="mt-5 rounded-[1.35rem] border border-dashed border-slate-200 bg-slate-50/80 p-4 text-sm leading-6 text-slate-500">{emptyMessage}</div>
      ) : (
        <div className="mt-5 space-y-3">
          {items.map((item) => (
            <div key={item.title} className={`rounded-[1.35rem] border p-4 ${selected.shell}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="text-sm font-semibold text-slate-900">{item.title}</div>
                {item.severity && (
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${selected.badge}`}>{item.severity}</span>
                )}
                {item.impact_hint && (
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${selected.badge}`}>{item.impact_hint}</span>
                )}
              </div>
              <div className="mt-2 text-sm leading-6 text-slate-600">{item.detail}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
