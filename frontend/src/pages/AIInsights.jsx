import { isProUnlocked } from '../lib/pro'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { auditApi, metricsApi, orgApi } from '../lib/api'
import { format } from 'date-fns'

const SEVERITY_STYLES = {
  high:   { bar: 'bg-red-500',    badge: 'bg-red-50 text-red-700 border border-red-200' },
  medium: { bar: 'bg-yellow-500', badge: 'bg-amber-50 text-amber-700 border border-amber-200' },
  low:    { bar: 'bg-blue-400',   badge: 'bg-blue-50 text-blue-700 border border-blue-200' },
}

const CONFIDENCE_STYLES = {
  high:   'bg-emerald-50 text-emerald-700 border border-emerald-100',
  medium: 'bg-amber-50 text-amber-700 border border-amber-100',
  low:    'bg-slate-100 text-slate-500',
}

const TYPE_ICONS = {
  revenue_leak:       '⚠',
  missed_opportunity: '◎',
  inefficiency:       '↻',
  strength:           '✓',
}

const TYPE_LABELS = {
  revenue_leak:       'Revenue Leak',
  missed_opportunity: 'Missed Opportunity',
  inefficiency:       'Inefficiency',
  strength:           'Strength',
}

const EFFORT_STYLES = {
  low:    'bg-emerald-50 text-emerald-700',
  medium: 'bg-amber-50 text-amber-700',
  high:   'bg-rose-50 text-rose-700',
}

const fmt$ = (n) => `$${(n || 0).toLocaleString('en-US', { minimumFractionDigits: 0 })}`

function HealthScore({ score }) {
  const color = score >= 70 ? '#22c55e' : score >= 45 ? '#f59e0b' : '#ef4444'
  const label = score >= 70 ? 'Healthy' : score >= 45 ? 'Needs Attention' : 'Critical'
  return (
    <div className="flex items-center gap-6">
      <div className="relative w-24 h-24 shrink-0">
        <svg viewBox="0 0 36 36" className="w-24 h-24 -rotate-90">
          <circle cx="18" cy="18" r="15.9" fill="none" stroke="#f1f5f9" strokeWidth="3" />
          <circle cx="18" cy="18" r="15.9" fill="none" stroke={color} strokeWidth="3"
            strokeDasharray={`${score} 100`} strokeLinecap="round" />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-2xl font-bold text-gray-900">{score}</span>
        </div>
      </div>
      <div>
        <div className="text-xl font-bold" style={{ color }}>{label}</div>
        <div className="text-sm text-slate-500 mt-0.5">Business Health Score</div>
      </div>
    </div>
  )
}

function HealthSparkline({ history }) {
  if (!history || history.length < 2) return null
  const scores = history.map((h) => h.health_score).reverse()
  const min = Math.min(...scores)
  const max = Math.max(...scores)
  const range = max - min || 1
  const w = 160
  const h = 40
  const pts = scores.map((s, i) => {
    const x = (i / (scores.length - 1)) * w
    const y = h - ((s - min) / range) * h
    return `${x},${y}`
  }).join(' ')
  const lastScore = scores[scores.length - 1]
  const prevScore = scores[scores.length - 2]
  const delta = lastScore - prevScore
  return (
    <div className="flex items-center gap-4">
      <div>
        <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-slate-400">Score trend ({history.length} audits)</div>
        <div className={`mt-1 text-sm font-semibold ${delta > 0 ? 'text-emerald-700' : delta < 0 ? 'text-rose-700' : 'text-slate-500'}`}>
          {delta > 0 ? '↑' : delta < 0 ? '↓' : '→'} {delta > 0 ? '+' : ''}{delta} pts vs previous
        </div>
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="shrink-0">
        <polyline points={pts} fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        {scores.map((s, i) => {
          const x = (i / (scores.length - 1)) * w
          const y = h - ((s - min) / range) * h
          return <circle key={i} cx={x} cy={y} r="2.5" fill={i === scores.length - 1 ? '#2563eb' : '#bfdbfe'} />
        })}
      </svg>
    </div>
  )
}

// Audit caps mirror PLAN_AUDIT_LIMITS in backend/app/services/ai_audit.py
const PLAN_AUDIT_CAPS = { basic: 3, pro: 20, premium: null, enterprise: null }

function PlanBadge({ plan }) {
  if (plan === 'enterprise') return <span className="rounded-full border border-slate-300 bg-slate-950 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-white">Enterprise · Unlimited · GPT-4o</span>
  if (plan === 'premium')    return <span className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-violet-700">Scale · Unlimited · GPT-4o</span>
  if (plan === 'pro')        return <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-emerald-700">Growth · 20 audits / mo</span>
  return <span className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-brand-700">Starter · 3 audits / mo</span>
}

export default function AIInsights() {
  const qc = useQueryClient()

  const { data: org } = useQuery({
    queryKey: ['org-me'],
    queryFn: () => orgApi.getMe().then((r) => r.data),
  })
  const plan  = org?.plan || 'basic'
  const isPro = isProUnlocked(org) // TW-484: owner SSO + dev flag

  const { data: report, isLoading, error } = useQuery({
    queryKey: ['audit-latest'],
    queryFn:  () => auditApi.latest().then((r) => r.data),
    retry: false,
  })

  const { data: history = [] } = useQuery({
    queryKey: ['audit-history'],
    queryFn:  () => auditApi.history(20).then((r) => r.data),
    retry: false,
    enabled: isPro,
  })

  const { data: segments } = useQuery({
    queryKey: ['metrics-segments'],
    queryFn:  () => metricsApi.segments(30).then((r) => r.data),
  })

  const { data: forecast } = useQuery({
    queryKey: ['metrics-forecast'],
    queryFn:  () => metricsApi.forecast(16).then((r) => r.data),
  })

  const trigger = useMutation({
    mutationFn: () => auditApi.run(),
    onSuccess:  (res) => {
      qc.setQueryData(['audit-latest'], res.data)
      qc.invalidateQueries({ queryKey: ['audit-history'] })
    },
  })

  function downloadPdf() {
    auditApi.exportPdf().then((res) => {
      const url = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }))
      const a = document.createElement('a')
      a.href = url
      a.download = 'lbt-audit-report.pdf'
      a.click()
      URL.revokeObjectURL(url)
    })
  }

  const is404       = error?.response?.status === 404
  const isMonthly   = error?.response?.status === 402
  const isTruncated = report?.is_truncated === true

  return (
    <div className="page-shell">

      {/* Header */}
      <section className="page-command">
        <div>
          <div className="section-kicker">AI Audit</div>
          <h1 className="page-title">Business Audit</h1>
          <p className="page-copy">Root-cause diagnosis and dollar-impact estimates from the last 30 days of operating data.</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
              <PlanBadge plan={plan} />
              {report && (
                <span className="text-xs text-slate-400">
                  Last run: {format(new Date(report.generated_at), 'MMM d, yyyy h:mm a')} ·
                  Period: {report.period_start} → {report.period_end}
                  {report.model_used && ` · ${report.model_used}`}
                </span>
              )}
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
            {report && isPro && (
              <button className="btn-secondary" onClick={downloadPdf}>Export PDF</button>
            )}
            <button
              className="btn-primary"
              onClick={() => trigger.mutate()}
              disabled={trigger.isPending || isMonthly}
              title={isMonthly ? `${PLAN_AUDIT_CAPS[plan] ?? '?'} audit cap reached this month` : undefined}
            >
              {trigger.isPending ? '⟳ Analyzing…' : isMonthly ? 'Monthly Cap Reached' : '✦ Run AI Audit'}
            </button>
        </div>
      </section>

      {/* Monthly cap reached */}
      {isMonthly && (() => {
        const cap = PLAN_AUDIT_CAPS[plan] ?? 0
        const nextPlan = plan === 'basic' ? 'Growth ($129/mo — 20 audits/mo)' : 'Scale ($299/mo — unlimited)'
        return (
          <section className="card-surface p-8 text-center space-y-4">
            <div className="text-4xl">✦</div>
            <h2 className="text-xl font-bold text-slate-950">
              {cap} audit{cap !== 1 ? 's' : ''} used this month
            </h2>
            <p className="text-slate-500 max-w-md mx-auto text-sm leading-6">
              Your current plan includes {cap} AI audit{cap !== 1 ? 's' : ''}/month. Upgrade to {nextPlan} for more audits, full action plans, and audit history.
            </p>
            <Link to="/app/billing" className="btn-primary mx-auto inline-flex">See upgrade options →</Link>
          </section>
        )
      })()}

      {/* No report */}
      {!isMonthly && (is404 || (!report && !isLoading)) && (
        <section className="card-surface p-10 text-center space-y-4">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-brand-50 text-3xl">◎</div>
          <h2 className="text-lg font-semibold text-slate-950">No audit report yet</h2>
          <p className="text-slate-500 text-sm max-w-sm mx-auto">Run your first AI audit to get root-cause diagnosis of where your business is losing money — with specific dollar estimates.</p>
          {!isPro && <p className="text-xs text-slate-400">Free plan: 1 audit per month included.</p>}
        </section>
      )}

      {isLoading && (
        <div className="animate-pulse space-y-4">
          <div className="h-32 bg-slate-100 rounded-3xl" />
          <div className="h-48 bg-slate-100 rounded-3xl" />
        </div>
      )}

      {trigger.isError && (
        <section className="card-surface border border-rose-200 bg-rose-50 p-5">
          <p className="text-sm text-rose-700 font-medium">
            {trigger.error?.response?.data?.detail || 'Audit failed. Make sure Ollama is running or your OpenAI key is set.'}
          </p>
        </section>
      )}

      {report && (
        <>
          {/* Health score + rationale */}
          <section className="card-surface p-6 space-y-4">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <HealthScore score={report.health_score} />
              {isPro && <HealthSparkline history={history} />}
            </div>
            {report.health_rationale && (
              <div className="rounded-[1.35rem] border border-slate-200 bg-slate-50/80 p-4 text-sm leading-6 text-slate-700">
                <span className="font-semibold text-slate-900">Why this score: </span>{report.health_rationale}
              </div>
            )}
            {report.biggest_leverage_point && (
              <div className="rounded-[1.35rem] border border-brand-100 bg-brand-50/60 p-4">
                <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-brand-700">Biggest Leverage Point</div>
                <p className="mt-2 text-sm leading-6 text-slate-800">{report.biggest_leverage_point}</p>
              </div>
            )}
          </section>

          {/* Insights — enhanced with root cause + confidence */}
          <section className="card-surface p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="section-kicker">Findings</div>
                <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Root-cause analysis</h2>
              </div>
              {isTruncated && (
                <span className="text-xs font-semibold uppercase tracking-[0.16em] text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-3 py-1">
                  Showing 3 of all insights
                </span>
              )}
            </div>
            {(report.insights || []).map((ins, i) => (
              <div key={i} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_12px_28px_-22px_rgba(15,23,42,0.3)]">
                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <span className="text-lg">{TYPE_ICONS[ins.type] || '•'}</span>
                  <span className="font-semibold text-slate-900 text-sm">{ins.title}</span>
                  <div className="ml-auto flex flex-wrap gap-1.5">
                    {ins.severity && (
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-[0.14em] ${SEVERITY_STYLES[ins.severity]?.badge || 'bg-slate-100 text-slate-600'}`}>
                        {ins.severity}
                      </span>
                    )}
                    {ins.confidence && (
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-[0.14em] ${CONFIDENCE_STYLES[ins.confidence] || ''}`}>
                        {ins.confidence} confidence
                      </span>
                    )}
                    {ins.estimated_impact && (
                      <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2.5 py-0.5">
                        {ins.estimated_impact}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-400 mb-2">
                  {TYPE_LABELS[ins.type] || ins.type}
                </div>

                {ins.root_cause && (
                  <div className="mb-3 rounded-xl border border-amber-100 bg-amber-50/60 px-3.5 py-2.5">
                    <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-amber-600">Root Cause · </span>
                    <span className="text-xs leading-5 text-slate-700">{ins.root_cause}</span>
                  </div>
                )}

                <p className="text-sm leading-6 text-slate-600">{ins.detail}</p>

                {ins.confidence_note && (
                  <p className="mt-2 text-xs text-slate-400 italic">{ins.confidence_note}</p>
                )}
              </div>
            ))}
          </section>

          {/* Recommendations or upsell */}
          {isTruncated ? (
            <section className="card-surface p-8 text-center space-y-4">
              <div className="text-3xl">✦</div>
              <h2 className="text-lg font-semibold text-slate-950">Unlock the full action plan</h2>
              <p className="text-slate-500 text-sm max-w-md mx-auto leading-6">
                Pro gives you the complete ranked action plan with dollar-value estimates, interdependency mapping, effort scoring, unlimited audits per month, audit history, and PDF exports.
              </p>
              <div className="flex flex-wrap justify-center gap-3">
                <Link to="/app/billing" className="btn-primary">Upgrade to Pro →</Link>
              </div>
              <p className="text-xs text-slate-400">No credit card required to see pricing.</p>
            </section>
          ) : (report.recommendations || []).length > 0 && (
            <section className="card-surface p-6 space-y-4">
              <div>
                <div className="section-kicker">Action Plan</div>
                <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Recommended actions</h2>
                <p className="mt-1 text-sm text-slate-500">Ranked by expected ROI. Some actions depend on earlier ones — follow the priority order.</p>
              </div>
              {(report.recommendations || []).map((rec, i) => (
                <div key={i} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_12px_28px_-22px_rgba(15,23,42,0.3)]">
                  <div className="flex gap-4">
                    <div className="w-9 h-9 rounded-full bg-brand-600 text-white flex items-center justify-center text-sm font-bold shrink-0 shadow-[0_12px_24px_-14px_rgba(37,99,235,0.7)]">
                      {rec.priority}
                    </div>
                    <div className="flex-1">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="font-semibold text-slate-900 text-sm leading-5">{rec.action}</div>
                        <div className="flex flex-wrap gap-1.5 shrink-0">
                          {rec.effort && (
                            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${EFFORT_STYLES[rec.effort] || 'bg-slate-100 text-slate-500'}`}>
                              {rec.effort} effort
                            </span>
                          )}
                          <span className="rounded-full bg-brand-50 text-brand-700 border border-brand-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em]">
                            ⏱ {rec.timeframe}
                          </span>
                        </div>
                      </div>
                      <div className="text-sm text-slate-500 mt-1.5 leading-6">{rec.why}</div>
                      {rec.expected_impact && (
                        <div className="mt-2 text-xs font-semibold text-emerald-700">
                          Expected impact: {rec.expected_impact}
                        </div>
                      )}
                      {rec.depends_on && (
                        <div className="mt-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
                          Depends on: {rec.depends_on}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {/* Source drill-down from segment analysis */}
          {segments && segments.segments && segments.segments.length > 0 && (
            <section className="card-surface p-6">
              <div className="section-kicker">Channel Breakdown</div>
              <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Source-level conversion analysis</h2>
              <p className="mt-1 text-sm text-slate-500 mb-4">Which acquisition channels are actually producing paid work.</p>
              {segments.channel_insight && (
                <div className="mb-4 rounded-[1.35rem] border border-brand-100 bg-brand-50/60 p-4 text-sm leading-6 text-slate-700">
                  {segments.channel_insight}
                </div>
              )}
              <div className="space-y-3">
                {segments.segments.map((seg) => {
                  const isBestConv = seg.source === segments.best_by_conversion?.source
                  const isBestRev  = seg.source === segments.best_by_revenue?.source
                  const convColor  = seg.conversion_rate_pct >= 30 ? 'bg-emerald-500' : seg.conversion_rate_pct >= 15 ? 'bg-amber-400' : 'bg-rose-400'
                  return (
                    <div key={seg.source} className="rounded-[1.35rem] border border-slate-200 bg-slate-50/80 p-4">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold capitalize text-slate-900">{seg.source.replaceAll('_', ' ')}</span>
                          {isBestConv && <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">Best conv.</span>}
                          {isBestRev && !isBestConv && <span className="rounded-full bg-brand-50 border border-brand-100 px-2 py-0.5 text-[10px] font-semibold text-brand-700">Top revenue</span>}
                        </div>
                        <div className="flex flex-wrap gap-4 text-sm">
                          <span className="text-slate-500">{seg.leads} leads · {seg.won} won</span>
                          <span className="font-semibold text-slate-900">{fmt$(seg.revenue)}</span>
                          <span className="text-slate-500">{fmt$(seg.avg_deal_size)} avg deal</span>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center gap-3">
                        <div className="text-xs font-semibold text-slate-400 w-24">Conversion</div>
                        <div className="flex-1 h-2 rounded-full bg-slate-200 overflow-hidden">
                          <div className={`h-full rounded-full ${convColor}`} style={{ width: `${Math.min(100, seg.conversion_rate_pct * 2)}%` }} />
                        </div>
                        <span className={`text-xs font-semibold w-12 text-right ${
                          seg.conversion_rate_pct >= 30 ? 'text-emerald-700' :
                          seg.conversion_rate_pct >= 15 ? 'text-amber-700' : 'text-rose-700'
                        }`}>{seg.conversion_rate_pct.toFixed(1)}%</span>
                      </div>
                      <div className="mt-2 flex items-center gap-3">
                        <div className="text-xs font-semibold text-slate-400 w-24">Rev share</div>
                        <div className="flex-1 h-2 rounded-full bg-slate-200 overflow-hidden">
                          <div className="h-full rounded-full bg-brand-400" style={{ width: `${seg.revenue_share_pct}%` }} />
                        </div>
                        <span className="text-xs font-semibold text-slate-600 w-12 text-right">{seg.revenue_share_pct.toFixed(1)}%</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          {/* Forecast panel */}
          {forecast?.status === 'ok' && (
            <section className="card-surface p-6">
              <div className="section-kicker">Revenue Forecast</div>
              <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">30/60/90-day outlook</h2>
              <p className="mt-1 text-sm text-slate-500 mb-5">Linear trend extrapolation from the last 16 weeks of paid sales. Use as a directional signal, not a guarantee.</p>

              <div className="grid gap-4 sm:grid-cols-3 mb-5">
                {[
                  { label: 'Next 30 days', value: forecast.summary?.next_30_days },
                  { label: 'Next 60 days', value: forecast.summary?.next_60_days },
                  { label: 'Next 90 days', value: forecast.summary?.next_90_days },
                ].map(({ label, value }) => (
                  <div key={label} className="rounded-[1.35rem] border border-slate-200 bg-slate-50/80 p-4 text-center">
                    <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-slate-400">{label}</div>
                    <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{fmt$(value)}</div>
                  </div>
                ))}
              </div>

              <div className={`rounded-[1.35rem] border p-4 text-sm leading-6 ${
                forecast.trend_direction === 'growing' ? 'border-emerald-100 bg-emerald-50/60 text-emerald-900' :
                forecast.trend_direction === 'declining' ? 'border-rose-100 bg-rose-50/60 text-rose-900' :
                'border-slate-200 bg-slate-50/80 text-slate-700'
              }`}>
                <span className="font-semibold">
                  {forecast.trend_direction === 'growing' ? '↑ Growing trend · ' :
                   forecast.trend_direction === 'declining' ? '↓ Declining trend · ' : '→ Flat trend · '}
                </span>
                {forecast.narrative}
              </div>
            </section>
          )}

          {/* Audit history */}
          {isPro && history.length >= 2 && <AuditHistory history={history} />}
        </>
      )}
    </div>
  )
}

function AuditHistory({ history }) {
  return (
    <section className="card-surface p-6">
      <div className="section-kicker">History</div>
      <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950 mb-4">Audit history</h2>
      <div className="overflow-x-auto rounded-2xl border border-slate-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/80">
              {['Period', 'Health score', 'Model', 'Date'].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {history.map((h, idx) => {
              const score = h.health_score
              const prev  = history[idx + 1]?.health_score
              const delta = prev !== undefined ? score - prev : null
              const color = score >= 70 ? 'text-emerald-700' : score >= 45 ? 'text-amber-600' : 'text-red-600'
              return (
                <tr key={h.id} className="bg-white hover:bg-slate-50/60">
                  <td className="px-4 py-3 text-slate-600">{h.period_start} → {h.period_end}</td>
                  <td className={`px-4 py-3 font-bold ${color}`}>
                    {score}
                    {delta !== null && (
                      <span className={`ml-2 text-xs font-semibold ${delta > 0 ? 'text-emerald-500' : delta < 0 ? 'text-rose-500' : 'text-slate-400'}`}>
                        {delta > 0 ? `+${delta}` : delta}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs">{h.model_used || '—'}</td>
                  <td className="px-4 py-3 text-slate-400 whitespace-nowrap">
                    {format(new Date(h.generated_at), 'MMM d, yyyy')}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
