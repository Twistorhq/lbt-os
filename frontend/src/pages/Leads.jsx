import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { leadsApi } from '../lib/api'
import { SAMPLE_WRITE_BLOCKED } from '../lib/sampleMode'
import DataTable from '../components/ui/DataTable'
import Modal from '../components/ui/Modal'
import { format } from 'date-fns'

const STATUS_COLORS = {
  new:       'badge bg-blue-50 text-blue-700',
  contacted: 'badge bg-purple-50 text-purple-700',
  qualified: 'badge bg-indigo-50 text-indigo-700',
  proposal:  'badge bg-yellow-50 text-yellow-700',
  won:       'badge bg-green-50 text-green-700',
  lost:      'badge bg-red-50 text-red-700',
}

const STATUSES = ['new', 'contacted', 'qualified', 'proposal', 'won', 'lost']
const SOURCES  = ['google', 'referral', 'social', 'yelp', 'cold_call', 'walk_in', 'website', 'other']

const EMPTY_FORM = { name: '', email: '', phone: '', source: '', status: 'new', service_interest: '', estimated_value: '', notes: '' }

export default function Leads() {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

  const { data: leads = [], isLoading } = useQuery({
    queryKey: ['leads'],
    queryFn: () => leadsApi.list({ limit: 100 }).then((r) => r.data),
  })

  const save = useMutation({
    mutationFn: (data) => editing ? leadsApi.update(editing.id, data) : leadsApi.create(data),
    onSuccess: () => { qc.invalidateQueries(['leads']); qc.invalidateQueries(['metrics']); closeModal() },
  })

  const convert = useMutation({
    mutationFn: (id) => leadsApi.convert(id),
    onSuccess: () => { qc.invalidateQueries(['leads']); qc.invalidateQueries(['customers']) },
  })

  const openNew = () => { setEditing(null); setForm(EMPTY_FORM); setOpen(true) }
  const openEdit = (row) => { setEditing(row); setForm({ ...row, estimated_value: row.estimated_value || '' }); setOpen(true) }
  const closeModal = () => { setOpen(false); setEditing(null) }

  const handleSubmit = (e) => {
    e.preventDefault()
    const payload = { ...form }
    if (!payload.estimated_value) delete payload.estimated_value
    else payload.estimated_value = parseFloat(payload.estimated_value)
    save.mutate(payload)
  }

  const columns = [
    { key: 'name',    label: 'Name', render: (v) => <span className="font-medium">{v}</span> },
    { key: 'source',  label: 'Source', render: (v) => <span className="capitalize">{v || '—'}</span> },
    { key: 'service_interest', label: 'Service' },
    { key: 'estimated_value',  label: 'Est. Value', render: (v) => v ? `$${Number(v).toLocaleString()}` : '—' },
    { key: 'status',  label: 'Status', render: (v) => <span className={STATUS_COLORS[v] || 'badge bg-gray-50 text-gray-600'}>{v}</span> },
    { key: 'follow_up_at', label: 'Follow-Up', render: (v) => v ? format(new Date(v), 'MMM d') : '—' },
    { key: 'id', label: '', render: (_, row) => row.status !== 'won' && row.status !== 'lost' ? (
      <button className="text-xs text-brand-600 hover:underline" onClick={(e) => { e.stopPropagation(); convert.mutate(row.id) }}>
        Convert →
      </button>
    ) : null },
  ]

  return (
    <div className="page-shell">
      <section className="page-command">
        <div>
          <div className="section-kicker">Pipeline</div>
          <h1 className="page-title">Leads</h1>
          <p className="page-copy">Track demand, follow-up, and conversion without leaving the operating flow.</p>
        </div>
        <button className="btn-primary" onClick={openNew}>+ Add Lead</button>
      </section>

      <section className="metric-strip grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        {STATUSES.map((s) => {
          const count = leads.filter((l) => l.status === s).length
          return (
            <div key={s} className="metric-chip">
              <div className="metric-label">{s}</div>
              <div className="metric-value">{count}</div>
            </div>
          )
        })}
      </section>

      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold tracking-tight text-slate-950">Lead records</h2>
        <span className="text-sm text-slate-400">{leads.length} total</span>
      </div>

      {convert.isError && (
        <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
          {convert.error?.response?.data?.detail || 'Could not convert that lead. Nothing was changed. Try again.'}
        </div>
      )}

      <DataTable
        columns={columns}
        data={leads}
        onRowClick={openEdit}
        emptyMessage="No leads yet. Add your first lead to get started."
      />

      <Modal open={open} onClose={closeModal} title={editing ? 'Edit Lead' : 'Add Lead'}>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Name *</label>
              <input required className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="label">Phone</label>
              <input className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </div>
            <div>
              <label className="label">Email</label>
              <input type="email" className="input" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div>
              <label className="label">Source</label>
              <select className="input" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                <option value="">Select...</option>
                {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Status</label>
              <select className="input" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Est. Value ($)</label>
              <input type="number" min="0" step="0.01" className="input" value={form.estimated_value} onChange={(e) => setForm({ ...form, estimated_value: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="label">Service Interest</label>
            <input className="input" value={form.service_interest} onChange={(e) => setForm({ ...form, service_interest: e.target.value })} />
          </div>
          <div>
            <label className="label">Notes</label>
            <textarea rows={3} className="input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
          {/* TW-1322: a failed save used to be silent — the modal stayed
              open with no message. Show the backend's reason; the typed
              entries stay in the form either way. Sample-mode blocks are
              skipped: the app is already routing to sign-in. */}
          {save.isError && save.error?.code !== SAMPLE_WRITE_BLOCKED && (
            <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
              {save.error?.response?.data?.detail || 'Could not save. Your entries are still here. Check your connection and try again.'}
            </div>
          )}
          <div className="flex gap-3 justify-end pt-2">
            <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={save.isPending}>
              {save.isPending ? 'Saving...' : editing ? 'Save Changes' : 'Add Lead'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
