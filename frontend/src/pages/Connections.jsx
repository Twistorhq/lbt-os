import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { integrationsApi, orgApi } from '../lib/api'
import { useGatedAction } from '../hooks/useGatedAction'
import api from '../lib/api'

export default function Connections() {
  const qc = useQueryClient()
  const gated = useGatedAction()
  const [importType, setImportType] = useState('leads')
  const [importFile, setImportFile] = useState(null)
  const [manualCredentials, setManualCredentials] = useState({})

  const { data: overview } = useQuery({
    queryKey: ['integration-overview'],
    queryFn: () => integrationsApi.overview().then((r) => r.data),
  })
  const { data: org } = useQuery({
    queryKey: ['org-me'],
    queryFn: () => orgApi.getMe().then((r) => r.data),
  })
  const { data: workspaceStatus } = useQuery({
    queryKey: ['workspace-status'],
    queryFn: () => orgApi.workspaceStatus().then((r) => r.data),
  })
  const providers = overview?.providers || []
  const connections = overview?.connections || []
  const syncRuns = overview?.sync_runs || []
  const importHistory = overview?.import_history || []
  const integrationSummary = overview?.summary || {}

  const startOAuth = useMutation({
    mutationFn: (provider) => integrationsApi.startOAuth(provider).then((r) => r.data),
    onSuccess: (data) => {
      window.location.href = data.authorization_url
    },
  })
  const syncAll = useMutation({
    mutationFn: () => integrationsApi.syncAll().then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integration-overview'] })
      qc.invalidateQueries({ queryKey: ['metrics', 30] })
    },
  })
  const syncOne = useMutation({
    mutationFn: (connectionId) => integrationsApi.syncOne(connectionId).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integration-overview'] })
      qc.invalidateQueries({ queryKey: ['metrics', 30] })
    },
  })
  const connectManual = useMutation({
    mutationFn: ({ provider, credentials, external_account_name }) =>
      integrationsApi.createConnection({
        provider,
        credentials,
        external_account_name,
      }).then((r) => r.data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['integration-overview'] })
      setManualCredentials((current) => ({ ...current, [variables.provider]: {} }))
    },
  })

  const disconnect = useMutation({
    mutationFn: (connectionId) => integrationsApi.disconnect(connectionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integration-overview'] })
      qc.invalidateQueries({ queryKey: ['workspace-status'] })
    },
  })

  function exportWorkspace() {
    integrationsApi.exportWorkspace().then((res) => {
      const url = URL.createObjectURL(new Blob([res.data], { type: 'application/zip' }))
      const a = document.createElement('a')
      a.href = url
      a.download = 'workspace-export.zip'
      a.click()
      URL.revokeObjectURL(url)
    })
  }
  const manualImport = useMutation({
    mutationFn: ({ entityType, file }) => integrationsApi.manualImport(entityType, file).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['metrics', 30] })
      qc.invalidateQueries({ queryKey: ['workspace-status'] })
      qc.invalidateQueries({ queryKey: ['integration-overview'] })
      setImportFile(null)
    },
  })

  function downloadTemplate(entityType) {
    api.get(`/integrations/import-template/${entityType}`, { responseType: 'blob' }).then((res) => {
      const url = URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `lbt-${entityType}-template.csv`
      a.click()
      URL.revokeObjectURL(url)
    })
  }

  const providerMap = useMemo(
    () => Object.fromEntries(providers.map((provider) => [provider.key, provider])),
    [providers],
  )
  const isPro = org?.plan === 'pro' || org?.plan === 'premium'
  const workspaceMode = workspaceStatus?.workspace_mode || 'blank'

  function updateManualCredential(provider, key, value) {
    setManualCredentials((current) => ({
      ...current,
      [provider]: {
        ...(current[provider] || {}),
        [key]: value,
      },
    }))
  }

  return (
    <div className="page-shell">
      <section className="page-command">
        <div>
          <div className="section-kicker">Sources</div>
          <h1 className="page-title">Connections</h1>
          <p className="page-copy">Bring accounting, CRM, Stripe, CSV, and sheet data into one operating schema.</p>
          <div className="mt-3"><WorkspaceModeChip mode={workspaceMode} /></div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-slate-500">Healthy {integrationSummary.connections_healthy || 0}/{integrationSummary.connections_total || 0}</span>
          <button className="btn-secondary" onClick={exportWorkspace}>
            Export All Data
          </button>
          <button className="btn-secondary" onClick={() => syncAll.mutate()} disabled={syncAll.isPending}>
            {syncAll.isPending ? 'Syncing...' : 'Run Sync'}
          </button>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[1fr_1fr]">
        <div className="card-surface p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold tracking-tight text-slate-950">Manual CSV Import</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                For businesses that are not ready to connect QuickBooks or HubSpot yet, import a lightweight CSV directly into the operating tables.
              </p>
            </div>
            <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              Fallback path
            </div>
          </div>
          <div className="mt-5 grid gap-4 sm:grid-cols-[0.8fr_1.2fr]">
            <div>
              <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Import type</label>
              <select
                value={importType}
                onChange={(e) => setImportType(e.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 focus:border-brand-400 focus:outline-none"
              >
                <option value="leads">Leads CSV</option>
                <option value="customers">Customers CSV</option>
                <option value="sales">Sales CSV</option>
                <option value="expenses">Expenses CSV</option>
              </select>
              <button
                type="button"
                onClick={() => downloadTemplate(importType)}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600 transition-colors hover:border-brand-300 hover:text-brand-700"
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Download {formatLabel(importType)} template
              </button>
              <label className="mt-3 block rounded-[1.35rem] border border-dashed border-slate-200 bg-slate-50/80 px-4 py-5 text-center text-sm text-slate-500 transition-colors hover:border-brand-300 hover:bg-brand-50/50">
                <input type="file" accept=".csv" className="hidden" onChange={(e) => setImportFile(e.target.files?.[0] || null)} />
                {importFile ? importFile.name : 'Choose a CSV file'}
              </label>
              <button
                className="btn-primary mt-4 w-full justify-center"
                disabled={!importFile || manualImport.isPending}
                onClick={() => manualImport.mutate({ entityType: importType, file: importFile })}
              >
                {manualImport.isPending ? 'Importing CSV...' : `Import ${formatLabel(importType)}`}
              </button>
              {manualImport.isError && (
                <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                  {manualImport.error?.response?.data?.detail || 'CSV import failed.'}
                </div>
              )}
              {manualImport.isSuccess && (
                <div className="mt-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">
                  Imported {manualImport.data?.imported} {manualImport.data?.entity_type}.
                </div>
              )}
            </div>
            <div className="rounded-[1.4rem] border border-slate-200 bg-slate-50/80 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Expected columns</div>
              <div className="mt-3 text-sm leading-6 text-slate-600">
                {CSV_HINTS[importType].description}
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {CSV_HINTS[importType].columns.map((column) => (
                  <span key={column} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700">
                    {column}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="card-surface p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold tracking-tight text-slate-950">Recurring Scans</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                Automated syncs and recurring audit runs are reserved for Pro so the platform can monitor the business continuously.
              </p>
            </div>
            <span className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
              Pro
            </span>
          </div>
          {isPro ? (
            <div className="mt-5 rounded-[1.4rem] border border-emerald-200 bg-emerald-50/70 p-5">
              <div className="text-sm font-semibold text-emerald-900">Pro workspace detected</div>
              <div className="mt-2 text-sm leading-6 text-emerald-800">
                Recurring scans are gated correctly. The scheduling endpoint is reserved for Pro and ready to be connected to background jobs.
              </div>
            </div>
          ) : (
            <div className="mt-5 rounded-[1.4rem] border border-slate-200 bg-slate-50/80 p-5">
              <div className="text-sm font-semibold text-slate-900">Free gives you the audit. Pro keeps watching.</div>
              <div className="mt-2 text-sm leading-6 text-slate-600">
                Upgrade to unlock recurring scan scheduling, ongoing AI monitoring, and deeper drill-down analysis.
              </div>
              <Link to="/app/billing" className="btn-primary mt-4 inline-flex">Upgrade to Pro</Link>
            </div>
          )}
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[1.08fr_0.92fr]">
        <div className="card-surface p-6">
          <h2 className="text-xl font-semibold tracking-tight text-slate-950">Connector Catalog</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Start with a real OAuth connection. Credentials stay server-side and are encrypted before storage.
          </p>
          <div className="mt-5 space-y-4">
            {providers.map((provider) => {
              const connection = connections.find((item) => item.provider === provider.key)
              const manualFields = provider.credential_fields || []
              const manualState = manualCredentials[provider.key] || {}
              return (
                <div key={provider.key} className="group relative overflow-hidden rounded-[1.65rem] border border-slate-200 bg-[linear-gradient(180deg,rgba(248,250,252,0.95)_0%,rgba(255,255,255,0.95)_100%)] p-5 shadow-[0_18px_40px_-34px_rgba(15,23,42,0.45)] transition-all duration-300 hover:-translate-y-1 hover:shadow-[0_28px_60px_-36px_rgba(15,23,42,0.42)]">
                  <div className="absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(37,99,235,0.75),transparent)] opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="flex items-center gap-3">
                        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,#dbeafe_0%,#bfdbfe_100%)] text-lg text-brand-700 shadow-inner">
                          {provider.category === 'accounting' ? '◫' : provider.category === 'payments' ? '$' : '◎'}
                        </div>
                        <div className="text-lg font-semibold text-slate-950">{provider.label}</div>
                      </div>
                      <div className="mt-1 text-sm leading-6 text-slate-500">{provider.description}</div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <div className="inline-flex rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 shadow-sm">
                          {provider.category}
                        </div>
                        <div className="inline-flex rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500 shadow-sm">
                          {provider.connection_mode === 'oauth' ? 'OAuth' : 'API key'}
                        </div>
                      </div>
                      {connection && (
                        <div className="mt-4 space-y-2 rounded-[1.2rem] border border-slate-200 bg-white/80 p-4 text-sm text-slate-600">
                          <div className="flex flex-wrap gap-x-5 gap-y-1">
                            <span>Last status: <strong className="text-slate-900">{connection.last_sync_status || connection.status}</strong></span>
                            <span>Last success: <strong className="text-slate-900">{formatRelativeTime(connection.last_successful_run?.finished_at || connection.last_synced_at)}</strong></span>
                          </div>
                          <div className="text-slate-500">
                            Latest run: {formatRunStats(connection.latest_run?.stats)}
                          </div>
                          {connection.last_sync_error && (
                            <div className="text-rose-600">{connection.last_sync_error}</div>
                          )}
                        </div>
                      )}
                      {!connection && provider.connection_mode === 'manual' && (
                        <div className="mt-4 space-y-3 rounded-[1.2rem] border border-slate-200 bg-white/85 p-4">
                          {manualFields.map((field) => (
                            <label key={field.key} className="block">
                              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{field.label}</div>
                              <input
                                type={field.type === 'password' ? 'password' : 'text'}
                                value={manualState[field.key] || ''}
                                onChange={(e) => updateManualCredential(provider.key, field.key, e.target.value)}
                                placeholder={field.placeholder || ''}
                                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 focus:border-brand-400 focus:outline-none"
                              />
                              {field.help_text && <div className="mt-2 text-xs leading-5 text-slate-400">{field.help_text}</div>}
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {connection ? (
                        <>
                          <span className="inline-flex items-center rounded-full bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700">
                            Connected
                          </span>
                          <button
                            className="btn-primary"
                            onClick={() => syncOne.mutate(connection.id)}
                            disabled={syncOne.isPending}
                          >
                            Sync Now
                          </button>
                          <button
                            className="rounded-full border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-medium text-rose-700 hover:bg-rose-100 transition-colors disabled:opacity-50"
                            onClick={() => {
                              if (window.confirm(`Disconnect ${provider.label}? Your imported data will be kept.`)) {
                                disconnect.mutate(connection.id)
                              }
                            }}
                            disabled={disconnect.isPending}
                          >
                            Disconnect
                          </button>
                        </>
                      ) : (
                        provider.connection_mode === 'oauth' ? (
                          <div className="flex flex-col gap-2">
                            <button
                              className="btn-primary"
                              onClick={() =>
                                gated(() => startOAuth.mutate(provider.key), `connect_${provider.key}`)
                              }
                              disabled={startOAuth.isPending}
                            >
                              {startOAuth.isPending ? 'Redirecting…' : `Connect ${provider.label}`}
                            </button>
                            {startOAuth.isError && startOAuth.variables === provider.key && (
                              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                                {startOAuth.error?.response?.data?.detail?.includes('not configured')
                                  ? `${provider.label} OAuth credentials are not yet configured on this server. Add the client ID and redirect URI to your environment variables to enable this connection.`
                                  : startOAuth.error?.response?.data?.detail || 'Could not start OAuth flow.'}
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="flex flex-col gap-2">
                            <button
                              className="btn-primary"
                              onClick={() => connectManual.mutate({
                                provider: provider.key,
                                credentials: Object.fromEntries(
                                  manualFields
                                    .filter((field) => field.key !== 'account_name')
                                    .map((field) => [field.key, manualState[field.key] || ''])
                                ),
                                external_account_name: manualState.account_name || null,
                              })}
                              disabled={connectManual.isPending}
                            >
                              {connectManual.isPending ? 'Saving…' : `Connect ${provider.label}`}
                            </button>
                            {connectManual.isError && connectManual.variables?.provider === provider.key && (
                              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs leading-5 text-rose-700">
                                {connectManual.error?.response?.data?.detail || 'Connection failed.'}
                              </div>
                            )}
                          </div>
                        )
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="card-surface p-6">
          <h2 className="text-xl font-semibold tracking-tight text-slate-950">Visible Ingestion Flow</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            This is the pipeline we show the customer so the data journey stays transparent.
          </p>
          <div className="mt-5 space-y-3">
            {[
              ['Raw source data', 'Contacts, deals, invoices, purchases, and company records are pulled directly from the source system.'],
              ['Mapped fields', 'Provider fields are translated into your internal schema for leads, customers, sales, and expenses.'],
              ['Validation errors', 'Missing IDs, empty names, negative amounts, and unsupported shapes are flagged before they hit the dashboard.'],
              ['Cleaned records', 'Normalized rows are written into tenant-scoped tables with consistent categories and timestamps.'],
              ['Final dashboard output', 'Metrics, analyst brief, and AI audit layers read from the standardized tables.'],
            ].map(([title, detail], idx) => (
              <div key={title} className="rounded-[1.3rem] border border-slate-200 bg-white/92 px-4 py-4 shadow-[0_14px_30px_-28px_rgba(15,23,42,0.35)]">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[linear-gradient(135deg,#2563eb_0%,#1d4ed8_100%)] text-sm font-semibold text-white shadow-[0_14px_28px_-18px_rgba(37,99,235,0.75)]">{idx + 1}</div>
                  <div className="text-sm font-semibold text-slate-900">{title}</div>
                </div>
                <div className="mt-3 text-sm leading-6 text-slate-500">{detail}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {importHistory.length > 0 && (
        <section className="card-surface p-6">
          <h2 className="text-xl font-semibold tracking-tight text-slate-950">CSV Import History</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">Every manual upload is logged here so you can track what came in and when.</p>
          <div className="mt-5 overflow-x-auto rounded-2xl border border-slate-200">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/80">
                  {['Type', 'File', 'Rows imported', 'Status', 'When'].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {importHistory.map((log) => (
                  <tr key={log.id} className="bg-white hover:bg-slate-50/60 transition-colors">
                    <td className="px-4 py-3 font-medium text-slate-900 capitalize">{log.entity_type}</td>
                    <td className="px-4 py-3 text-slate-500 max-w-[200px] truncate" title={log.filename}>{log.filename || '—'}</td>
                    <td className="px-4 py-3 text-slate-700">
                      {log.rows_imported} <span className="text-slate-400">/ {log.row_count}</span>
                    </td>
                    <td className="px-4 py-3">
                      <ImportStatusBadge status={log.status} />
                    </td>
                    <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{formatRelativeTime(log.imported_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="grid gap-5 lg:grid-cols-[1.08fr_0.92fr]">
        <div className="card-surface p-6">
          <h2 className="text-xl font-semibold tracking-tight text-slate-950">Schema Mapper</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Every provider has a different shape. We normalize those shapes into the LBT OS operating schema below.
          </p>
          <div className="mt-5 space-y-4">
            {providers.map((provider) => (
              <div key={provider.key} className="rounded-[1.4rem] border border-slate-200 bg-slate-50/80 p-4 shadow-[0_16px_32px_-28px_rgba(15,23,42,0.28)]">
                <div className="text-sm font-semibold text-slate-950">{provider.label}</div>
                <div className="mt-3 space-y-2">
                  {(provider.schema_mapping || []).map((mapping, idx) => (
                    <div key={`${provider.key}-${mapping.raw_object}`} className="rounded-xl border border-white bg-white px-4 py-3 shadow-[0_12px_24px_-22px_rgba(15,23,42,0.25)]">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">{idx + 1}</div>
                          <div className="text-sm font-medium text-slate-900">{mapping.raw_object}</div>
                        </div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
                          {mapping.mapped_table}
                        </div>
                      </div>
                      <div className="mt-2 text-sm leading-6 text-slate-500">
                        Key fields: {(mapping.key_fields || []).join(', ')}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card-surface p-6">
          <h2 className="text-xl font-semibold tracking-tight text-slate-950">Recent Sync Activity</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Run logs make the pipeline debuggable without exposing secrets.
          </p>
          <div className="mt-5 space-y-3">
            {syncRuns.length === 0 ? (
              <div className="rounded-[1.35rem] border border-dashed border-slate-200 bg-slate-50/80 p-4 text-sm leading-6 text-slate-500">
                No syncs yet. Connect a provider and run the first import to populate this feed.
              </div>
            ) : (
              syncRuns.slice(0, 8).map((run) => (
                <div key={run.id} className="rounded-[1.35rem] border border-slate-200 bg-slate-50/80 p-4 shadow-[0_14px_30px_-28px_rgba(15,23,42,0.3)]">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-slate-900">{providerMap[run.provider]?.label || run.provider}</div>
                    <StatusBadge status={run.status} />
                  </div>
                  <div className="mt-2 text-sm leading-6 text-slate-500">
                    Trigger: {run.trigger_source} · Imported: {formatRunStats(run.stats)}
                  </div>
                  {run.error && <div className="mt-2 text-sm leading-6 text-rose-600">{run.error}</div>}
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  )
}

const CSV_HINTS = {
  leads: {
    description: 'Good for moving a spreadsheet pipeline into LBT OS quickly.',
    columns: ['name', 'email', 'phone', 'source', 'status', 'service_interest', 'estimated_value', 'notes'],
  },
  customers: {
    description: 'Use this when you already have a customer roster and want retention visibility fast.',
    columns: ['name', 'email', 'phone', 'address', 'notes'],
  },
  sales: {
    description: 'Best for simple invoice exports when no accounting integration is connected yet.',
    columns: ['service', 'amount', 'cost', 'payment_method', 'payment_status', 'source', 'invoice_number', 'sold_at', 'notes'],
  },
  expenses: {
    description: 'Bring in a spend log to light up margin and cost-control analysis.',
    columns: ['category', 'description', 'amount', 'vendor', 'expense_date', 'is_recurring', 'recurrence_period'],
  },
}

function WorkspaceModeChip({ mode }) {
  const styles = {
    live: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    demo: 'border-amber-200 bg-amber-50 text-amber-700',
    blank: 'border-slate-200 bg-slate-50 text-slate-600',
  }
  const labels = {
    live: 'Live data workspace',
    demo: 'Demo workspace',
    blank: 'Setup workspace',
  }
  return (
    <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${styles[mode] || styles.blank}`}>
      {labels[mode] || labels.blank}
    </span>
  )
}

function formatLabel(value) {
  return value.replaceAll('_', ' ')
}

function formatRunStats(stats) {
  const entries = Object.entries(stats || {}).filter(([, value]) => value !== null && value !== undefined)
  if (entries.length === 0) return 'No row counts yet'
  return entries.map(([key, value]) => `${key} ${value}`).join(', ')
}

function StatusBadge({ status }) {
  const styles = {
    success: 'bg-emerald-50 text-emerald-700 shadow-[0_10px_24px_-18px_rgba(16,185,129,0.8)]',
    partial: 'bg-amber-50 text-amber-700 shadow-[0_10px_24px_-18px_rgba(245,158,11,0.8)]',
    running: 'bg-blue-50 text-blue-700 shadow-[0_10px_24px_-18px_rgba(37,99,235,0.75)]',
    failed: 'bg-rose-50 text-rose-700 shadow-[0_10px_24px_-18px_rgba(244,63,94,0.75)]',
  }
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${styles[status] || 'bg-slate-100 text-slate-600'}`}>
      {status}
    </span>
  )
}

function ImportStatusBadge({ status }) {
  const styles = {
    success: 'bg-emerald-50 text-emerald-700',
    failed: 'bg-rose-50 text-rose-700',
  }
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${styles[status] || 'bg-slate-100 text-slate-600'}`}>
      {status}
    </span>
  )
}

function formatRelativeTime(isoString) {
  if (!isoString) return '—'
  const diff = Date.now() - new Date(isoString).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  return new Date(isoString).toLocaleDateString()
}
