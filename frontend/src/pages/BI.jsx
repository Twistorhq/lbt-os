import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { biApi } from '../lib/api'

const THEME_DEFAULTS = {
  primary_color: '#4f46e5',
  background_color: '#ffffff',
  font_family: 'system-ui, sans-serif',
  logo_url: '',
}

function WidgetCard({ widget }) {
  if (widget.error) {
    return (
      <div className="bi-card">
        <div className="bi-title">{widget.title}</div>
        <div className="bi-muted">Unavailable</div>
      </div>
    )
  }
  if (widget.status === 'updating') {
    return (
      <div className="bi-card">
        <div className="bi-title">{widget.title}</div>
        <div className="bi-muted">Updating…</div>
      </div>
    )
  }
  const value = widget.value || {}
  if (value.points) {
    return (
      <div className="bi-card bi-wide">
        <div className="bi-title">{widget.title}</div>
        <table className="bi-table">
          <tbody>
            {value.points.map((p, i) => (
              <tr key={i}>
                <td>{p.label}</td>
                <td className="bi-num">{p.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  return (
    <div className="bi-card">
      <div className="bi-title">{widget.title}</div>
      <div className="bi-num">{value.value}</div>
    </div>
  )
}

function DashboardView({ dashboard, onBack }) {
  const qc = useQueryClient()
  const [embedCode, setEmbedCode] = useState('')
  const theme = { ...THEME_DEFAULTS, ...(dashboard.theme || {}) }

  const { data: preview } = useQuery({
    queryKey: ['bi-preview', dashboard.id],
    queryFn: () => biApi.preview(dashboard.id).then((r) => r.data),
  })
  const widgets = preview?.widgets || []

  const issueEmbed = useMutation({
    mutationFn: () => biApi.issueEmbed(dashboard.id).then((r) => r.data),
    onSuccess: (data) => {
      const base = window.location.origin
      setEmbedCode(
        `<iframe src="${base}/api/v1/bi/embed/${data.token}/view" ` +
        `width="960" height="640" frameborder="0"></iframe>`
      )
    },
  })

  const togglePublic = useMutation({
    mutationFn: () =>
      biApi.patch(dashboard.id, { is_public: !dashboard.is_public }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bi-dashboards'] }),
  })

  return (
    <div>
      <button className="bi-back" onClick={onBack}>← All dashboards</button>
      <div
        className="bi-themed"
        style={{
          '--bi-primary': theme.primary_color,
          background: theme.background_color,
          fontFamily: theme.font_family,
        }}
      >
        <div className="bi-masthead">
          {theme.logo_url ? <img className="bi-logo" src={theme.logo_url} alt="logo" /> : null}
          <h2>{dashboard.name}</h2>
        </div>
        <div className="bi-grid">
          {widgets.map((w) => (
            <WidgetCard key={w.id} widget={w} />
          ))}
        </div>
        <div className="bi-actions">
          <button onClick={() => togglePublic.mutate()} disabled={togglePublic.isPending}>
            {dashboard.is_public ? 'Disable public embeds' : 'Enable public embeds'}
          </button>
          <button onClick={() => issueEmbed.mutate()} disabled={issueEmbed.isPending || !dashboard.is_public}>
            Get embed code
          </button>
        </div>
        {!dashboard.is_public ? (
          <p className="bi-muted">Enable public embeds before issuing an embed token.</p>
        ) : null}
        {embedCode ? (
          <div className="bi-embed">
            <p className="bi-muted">Paste this snippet where the dashboard should appear. The token is shown once.</p>
            <textarea readOnly value={embedCode} rows={3} onFocus={(e) => e.target.select()} />
          </div>
        ) : null}
      </div>
    </div>
  )
}

export default function BI() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState(null)
  const [form, setForm] = useState({ name: '', slug: '', primary_color: '#4f46e5' })
  const [formError, setFormError] = useState('')

  const { data: dashboards } = useQuery({
    queryKey: ['bi-dashboards'],
    queryFn: () => biApi.list().then((r) => r.data),
  })
  const { data: connectors } = useQuery({
    queryKey: ['bi-connectors'],
    queryFn: () => biApi.connectors().then((r) => r.data),
  })

  const createDash = useMutation({
    mutationFn: () =>
      biApi.create({
        name: form.name,
        slug: form.slug,
        theme: { primary_color: form.primary_color },
        layout: { widgets: [] },
      }).then((r) => r.data),
    onSuccess: (dash) => {
      setForm({ name: '', slug: '', primary_color: '#4f46e5' })
      setFormError('')
      qc.invalidateQueries({ queryKey: ['bi-dashboards'] })
      setSelectedId(dash.id)
    },
    onError: (err) => setFormError(err?.response?.data?.detail || 'Could not create dashboard'),
  })

  const syncConnector = useMutation({
    mutationFn: (key) => biApi.syncConnector(key).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bi-dashboards'] }),
  })

  const selected = (dashboards || []).find((d) => d.id === selectedId)
  if (selected) {
    return <DashboardView dashboard={selected} onBack={() => setSelectedId(null)} />
  }

  return (
    <div className="bi-page">
      <h1>White-label BI</h1>
      <p className="bi-muted">Org-branded dashboards from your connected data. Embed tokens serve aggregates only.</p>

      <h2>Connectors</h2>
      <div className="bi-row">
        {(connectors || []).map((c) => (
          <div key={c.key} className="bi-card">
            <div className="bi-title">{c.label}</div>
            <div className="bi-muted">{c.metrics.length} metrics</div>
            <button onClick={() => syncConnector.mutate(c.key)} disabled={syncConnector.isPending}>
              Sync now
            </button>
          </div>
        ))}
      </div>

      <h2>Dashboards</h2>
      <div className="bi-row">
        {(dashboards || []).map((d) => (
          <button key={d.id} className="bi-card bi-click" onClick={() => setSelectedId(d.id)}>
            <div className="bi-title">{d.name}</div>
            <div className="bi-muted">/{d.slug} {d.is_public ? '· public' : '· private'}</div>
          </button>
        ))}
      </div>

      <h2>New dashboard</h2>
      {formError ? <p className="bi-error">{formError}</p> : null}
      <div className="bi-form">
        <input placeholder="Name" value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input placeholder="slug" value={form.slug}
          onChange={(e) => setForm({ ...form, slug: e.target.value })} />
        <input type="color" value={form.primary_color}
          onChange={(e) => setForm({ ...form, primary_color: e.target.value })} />
        <button onClick={() => createDash.mutate()} disabled={createDash.isPending || !form.name || !form.slug}>
          Create
        </button>
      </div>
    </div>
  )
}
