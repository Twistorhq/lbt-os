import { useState, useEffect, useCallback } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '@clerk/clerk-react'
import { adminApi, visitorEventsApi } from '../lib/api'
import { getVisitorContext } from '../lib/analytics'

const PLAN_OPTIONS = ['basic', 'pro', 'premium']
const STATUS_OPTIONS = ['active', 'trialing', 'past_due', 'canceled', 'unpaid']

const PLAN_COLORS = {
  basic:   'bg-slate-100 text-slate-700',
  pro:     'bg-brand-50 text-brand-700 border border-brand-100',
  premium: 'bg-emerald-50 text-emerald-700 border border-emerald-100',
}

const STATUS_COLORS = {
  active:    'bg-emerald-50 text-emerald-700',
  trialing:  'bg-blue-50 text-blue-700',
  past_due:  'bg-amber-50 text-amber-700',
  canceled:  'bg-slate-100 text-slate-500',
  unpaid:    'bg-rose-50 text-rose-700',
}

function StatCard({ label, value, sub }) {
  return (
    <div className="rounded-[1.5rem] border border-white/75 bg-white/90 p-6 shadow-[0_20px_50px_-30px_rgba(15,23,42,0.2)] backdrop-blur">
      <div className="text-[0.7rem] font-semibold uppercase tracking-[0.22em] text-slate-400">{label}</div>
      <div className="mt-3 text-4xl font-semibold tracking-tight text-slate-950">{value ?? '—'}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

function PlanBadge({ plan }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${PLAN_COLORS[plan] ?? 'bg-slate-100 text-slate-600'}`}>
      {plan ?? 'basic'}
    </span>
  )
}

function StatusBadge({ status }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_COLORS[status] ?? 'bg-slate-100 text-slate-500'}`}>
      {status ?? '—'}
    </span>
  )
}

function PlanEditor({ org, onSave }) {
  const [plan, setPlan] = useState(org.plan ?? 'basic')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    setSaving(true)
    setError('')
    try {
      await adminApi.updatePlan(org.id, plan)
      onSave(org.id, { plan })
    } catch {
      // TW-1322: the old comment claimed the parent surfaces this error —
      // it never did, and the select kept showing the unsaved plan as if
      // it had stuck. Roll the select back to the server's plan and say
      // the save failed, inline.
      setPlan(org.plan ?? 'basic')
      setError('Save failed. Plan unchanged.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2">
        <select
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500"
          value={plan}
          onChange={(e) => setPlan(e.target.value)}
        >
          {PLAN_OPTIONS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <button
          onClick={save}
          disabled={saving || plan === org.plan}
          className="rounded-lg bg-brand-600 px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-40 hover:bg-brand-700 transition-colors"
        >
          {saving ? '…' : 'Save'}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs font-medium text-rose-600">{error}</p>
      )}
    </div>
  )
}

export default function Admin() {
  const { isLoaded, isSignedIn } = useAuth()

  const [access, setAccess]     = useState(null) // null=loading, true=ok, false=denied
  const [stats, setStats]       = useState(null)
  const [integrationHealth, setIntegrationHealth] = useState(null)
  const [orgs, setOrgs]         = useState([])
  const [total, setTotal]       = useState(0)
  const [search, setSearch]     = useState('')
  const [planFilter, setPlanFilter] = useState('')
  const [loading, setLoading]   = useState(false)
  const [visitorSummary, setVisitorSummary] = useState(null)
  const [visitorEvents, setVisitorEvents] = useState([])
  const [visitorLoading, setVisitorLoading] = useState(false)
  const [error, setError]       = useState(null)
  const [editingId, setEditingId] = useState(null)

  // Check admin access
  useEffect(() => {
    if (!isLoaded || !isSignedIn) return
    adminApi.me()
      .then(() => setAccess(true))
      .catch(() => setAccess(false))
  }, [isLoaded, isSignedIn])

  // Load stats
  useEffect(() => {
    if (access !== true) return
    adminApi.stats().then((r) => setStats(r.data)).catch(() => {})
  }, [access])

  useEffect(() => {
    if (access !== true) return
    adminApi.integrationHealth({ limit: 12 }).then((r) => setIntegrationHealth(r.data)).catch(() => {})
  }, [access])

  const fetchVisitorEvents = useCallback(() => {
    if (access !== true) return
    setVisitorLoading(true)
    Promise.all([
      visitorEventsApi.summary(7),
      visitorEventsApi.recent(12),
    ])
      .then(([summary, recent]) => {
        setVisitorSummary(summary.data)
        setVisitorEvents(recent.data.events || [])
      })
      .catch(() => {})
      .finally(() => setVisitorLoading(false))
  }, [access])

  useEffect(() => { fetchVisitorEvents() }, [fetchVisitorEvents])

  // Load orgs
  const fetchOrgs = useCallback(() => {
    if (access !== true) return
    setLoading(true)
    setError(null)
    adminApi.orgs({ search: search || undefined, plan: planFilter || undefined, limit: 100 })
      .then((r) => { setOrgs(r.data.organizations); setTotal(r.data.total) })
      .catch(() => setError('Failed to load organizations.'))
      .finally(() => setLoading(false))
  }, [access, search, planFilter])

  useEffect(() => { fetchOrgs() }, [fetchOrgs])

  function handleOrgUpdate(id, patch) {
    setOrgs((prev) => prev.map((o) => o.id === id ? { ...o, ...patch } : o))
    setEditingId(null)
  }

  async function sendTestVisitorEvent() {
    await visitorEventsApi.capture({
      event_type: 'test_ping',
      ...getVisitorContext(),
      path: '/admin',
      source: 'admin_test',
      metadata: {
        note: 'Manual admin test event',
        created_from: 'admin_panel',
      },
      occurred_at: new Date().toISOString(),
    })
    fetchVisitorEvents()
  }

  if (!isLoaded) {
    return (
      <div className="flex h-screen items-center justify-center text-slate-400 text-sm">Loading…</div>
    )
  }
  if (!isSignedIn) return <Navigate to="/sign-in" replace />
  if (access === false) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4">
        <div className="text-2xl font-semibold text-slate-950">Access Denied</div>
        <p className="text-sm text-slate-500">Your account is not authorised for the admin panel.</p>
        <Link to="/app" className="btn-primary mt-2">Go to Dashboard</Link>
      </div>
    )
  }
  if (access === null) {
    return (
      <div className="flex h-screen items-center justify-center text-slate-400 text-sm">Verifying access…</div>
    )
  }

  const planBreakdown = stats?.plan_breakdown ?? {}

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,rgba(37,99,235,0.10),transparent_22%),linear-gradient(180deg,#f8fafc_0%,#e8edf5_100%)]">

      {/* Top bar */}
      <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 xl:px-8">
          <div className="flex items-center gap-4">
            <Link to="/" className="text-lg font-semibold tracking-tight text-slate-950">LBT OS</Link>
            <span className="text-slate-300">/</span>
            <span className="rounded-full bg-rose-50 px-3 py-0.5 text-xs font-semibold uppercase tracking-[0.18em] text-rose-700 border border-rose-100">
              Admin
            </span>
          </div>
          <Link to="/app" className="btn-secondary text-sm">Back to App</Link>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-10 xl:px-8 space-y-10">

        {/* Stats */}
        <section>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-950">Platform Overview</h1>
          <p className="mt-1 text-sm text-slate-500">Live data from Supabase. Refresh the page for latest.</p>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Total Organizations" value={stats?.total_organizations} />
            <StatCard
              label="Pro + Premium"
              value={(planBreakdown.pro ?? 0) + (planBreakdown.premium ?? 0)}
              sub={`Pro: ${planBreakdown.pro ?? 0} · Premium: ${planBreakdown.premium ?? 0}`}
            />
            <StatCard label="New Orgs (30 days)" value={stats?.new_orgs_last_30_days} />
            <StatCard label="Total Audits Run" value={stats?.total_audits_run} />
          </div>

          {/* Plan breakdown mini bar */}
          {stats && (
            <div className="mt-5 flex flex-wrap gap-3">
              {Object.entries(planBreakdown).map(([p, count]) => (
                <div key={p} className="rounded-[1rem] border border-white/75 bg-white/85 px-4 py-3 shadow-sm backdrop-blur flex items-center gap-3">
                  <PlanBadge plan={p} />
                  <span className="text-sm font-semibold text-slate-950">{count}</span>
                  <span className="text-xs text-slate-400">org{count !== 1 ? 's' : ''}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
          <div className="rounded-[1.5rem] border border-white/75 bg-white/90 p-6 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold tracking-tight text-slate-950">Visitor Capture</h2>
                <p className="mt-1 text-sm text-slate-500">Arrivals, CTA clicks, and onboarding information from the last 7 days.</p>
              </div>
              <button onClick={sendTestVisitorEvent} className="btn-secondary text-sm">Send Test</button>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <StatCard label="Events" value={visitorSummary?.total_events ?? 0} />
              <StatCard label="Visitors" value={visitorSummary?.unique_visitors ?? 0} />
              <StatCard label="Info Submitted" value={visitorSummary?.by_type?.info_submitted ?? 0} />
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              {Object.entries(visitorSummary?.by_type ?? {}).map(([type, count]) => (
                <span key={type} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-600">
                  {type}: {count}
                </span>
              ))}
              {!visitorLoading && Object.keys(visitorSummary?.by_type ?? {}).length === 0 && (
                <span className="text-sm text-slate-400">No captured events yet. Use Send Test or visit the landing page.</span>
              )}
            </div>
          </div>

          <div className="rounded-[1.5rem] border border-white/75 bg-white/90 p-6 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold tracking-tight text-slate-950">Recent Visitor Events</h2>
                <p className="mt-1 text-sm text-slate-500">Latest capture records for QA and launch monitoring.</p>
              </div>
              <button onClick={fetchVisitorEvents} className="btn-secondary text-sm">Refresh</button>
            </div>
            <div className="mt-5 space-y-3">
              {visitorLoading && <div className="text-sm text-slate-400">Loading visitor events…</div>}
              {!visitorLoading && visitorEvents.length === 0 && (
                <div className="rounded-[1rem] border border-slate-100 bg-slate-50/80 px-4 py-4 text-sm text-slate-400">
                  No visitor events captured yet.
                </div>
              )}
              {!visitorLoading && visitorEvents.map((event) => (
                <div key={event.id || `${event.event_type}-${event.occurred_at}`} className="rounded-[1rem] border border-slate-100 bg-slate-50/70 px-4 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="rounded-full bg-white px-2.5 py-0.5 text-xs font-semibold text-slate-700 shadow-sm">
                      {event.event_type}
                    </span>
                    <span className="text-xs text-slate-400">
                      {event.occurred_at ? new Date(event.occurred_at).toLocaleString() : '—'}
                    </span>
                  </div>
                  <div className="mt-2 text-sm font-medium text-slate-800">{event.path || '/'}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    visitor {shortId(event.visitor_id)} · session {shortId(event.session_id)}
                  </div>
                  {event.metadata && Object.keys(event.metadata).length > 0 && (
                    <pre className="mt-3 max-h-28 overflow-auto rounded-lg bg-white/80 p-3 text-[11px] leading-5 text-slate-500">
                      {JSON.stringify(event.metadata, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="grid gap-5 xl:grid-cols-[1.05fr_0.95fr]">
          <div className="rounded-[1.5rem] border border-white/75 bg-white/90 p-6 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur">
            <h2 className="text-xl font-semibold tracking-tight text-slate-950">Integration Health</h2>
            <p className="mt-1 text-sm text-slate-500">Provider-level connection health and recent sync results.</p>
            <div className="mt-5 flex flex-wrap gap-3">
              {Object.entries(integrationHealth?.provider_breakdown ?? {}).map(([provider, bucket]) => (
                <div key={provider} className="rounded-[1rem] border border-slate-100 bg-slate-50/80 px-4 py-3">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{provider}</div>
                  <div className="mt-2 text-lg font-semibold text-slate-950">{bucket.total}</div>
                  <div className="mt-1 text-xs text-slate-500">connected {bucket.connected ?? 0} · error {bucket.error ?? 0}</div>
                </div>
              ))}
              {(!integrationHealth || Object.keys(integrationHealth.provider_breakdown ?? {}).length === 0) && (
                <div className="text-sm text-slate-400">No integration data yet.</div>
              )}
            </div>
          </div>

          <div className="rounded-[1.5rem] border border-white/75 bg-white/90 p-6 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur">
            <h2 className="text-xl font-semibold tracking-tight text-slate-950">Recent Failures</h2>
            <p className="mt-1 text-sm text-slate-500">Latest failed syncs surfaced for platform triage.</p>
            <div className="mt-5 space-y-3">
              {(integrationHealth?.recent_failures ?? []).length === 0 ? (
                <div className="rounded-[1rem] border border-slate-100 bg-slate-50/80 px-4 py-4 text-sm text-slate-400">
                  No failed syncs in the latest window.
                </div>
              ) : (
                integrationHealth.recent_failures.map((run) => (
                  <div key={run.id} className="rounded-[1rem] border border-rose-100 bg-rose-50/70 px-4 py-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-slate-950">{run.provider}</div>
                      <div className="text-xs uppercase tracking-[0.16em] text-rose-700">{run.trigger_source}</div>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">{run.started_at ? new Date(run.started_at).toLocaleString() : '—'}</div>
                    <div className="mt-2 text-sm text-rose-700">{run.error || 'Unknown sync failure.'}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>

        {/* Organizations */}
        <section>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-xl font-semibold tracking-tight text-slate-950">Organizations</h2>
              <p className="mt-0.5 text-sm text-slate-500">{total} total · click a row to edit plan</p>
            </div>
            <div className="flex flex-wrap gap-3">
              <input
                type="text"
                placeholder="Search by name…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="input w-48 text-sm"
              />
              <select
                value={planFilter}
                onChange={(e) => setPlanFilter(e.target.value)}
                className="input w-36 text-sm"
              >
                <option value="">All plans</option>
                {PLAN_OPTIONS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <button onClick={fetchOrgs} className="btn-secondary text-sm">Refresh</button>
            </div>
          </div>

          <div className="mt-5 overflow-hidden rounded-[1.5rem] border border-white/75 bg-white/90 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur">
            {loading && (
              <div className="px-6 py-10 text-center text-sm text-slate-400">Loading…</div>
            )}
            {error && (
              <div className="px-6 py-10 text-center text-sm text-rose-600">{error}</div>
            )}
            {!loading && !error && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 bg-slate-50/80">
                      {['Name', 'Industry', 'Location', 'Plan', 'Status', 'Joined', 'Actions'].map((h) => (
                        <th key={h} className="px-5 py-3 text-left text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-slate-400">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {orgs.length === 0 && (
                      <tr>
                        <td colSpan={7} className="px-5 py-10 text-center text-slate-400">No organizations found.</td>
                      </tr>
                    )}
                    {orgs.map((org) => (
                      <tr
                        key={org.id}
                        className="group transition-colors hover:bg-slate-50/80"
                      >
                        <td className="px-5 py-4">
                          <div className="font-semibold text-slate-950">{org.name ?? '—'}</div>
                          <div className="mt-0.5 font-mono text-[0.65rem] text-slate-400 truncate max-w-[160px]">{org.id}</div>
                        </td>
                        <td className="px-5 py-4 text-slate-600">{org.industry ?? '—'}</td>
                        <td className="px-5 py-4 text-slate-600">
                          {[org.city, org.state].filter(Boolean).join(', ') || '—'}
                        </td>
                        <td className="px-5 py-4">
                          {editingId === org.id ? (
                            <PlanEditor org={org} onSave={handleOrgUpdate} />
                          ) : (
                            <button onClick={() => setEditingId(org.id)}>
                              <PlanBadge plan={org.plan} />
                            </button>
                          )}
                        </td>
                        <td className="px-5 py-4">
                          <StatusBadge status={org.subscription_status} />
                        </td>
                        <td className="px-5 py-4 text-slate-500">
                          {org.created_at ? new Date(org.created_at).toLocaleDateString() : '—'}
                        </td>
                        <td className="px-5 py-4">
                          <button
                            onClick={() => setEditingId(editingId === org.id ? null : org.id)}
                            className="text-xs font-medium text-brand-600 hover:underline"
                          >
                            {editingId === org.id ? 'Cancel' : 'Edit plan'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>

        {/* Admin setup hint */}
        <section className="rounded-[1.5rem] border border-amber-100 bg-amber-50/60 p-5 text-sm text-amber-800">
          <strong>Setup:</strong> To grant admin access, add Clerk user IDs (comma-separated) to the{' '}
          <code className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-xs">ADMIN_USER_IDS</code>{' '}
          environment variable on the backend. Your current user ID is available via the Clerk dashboard.
        </section>

      </main>
    </div>
  )
}

function shortId(value) {
  if (!value) return '—'
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}
