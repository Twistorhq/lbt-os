import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { customersApi } from '../lib/api'
import { SAMPLE_WRITE_BLOCKED } from '../lib/sampleMode'
import DataTable from '../components/ui/DataTable'
import Modal from '../components/ui/Modal'
import { format } from 'date-fns'

const TAGS = ['loyal', 'vip', 'at_risk', 'inactive', 'new']
const EMPTY_FORM = { name: '', email: '', phone: '', address: '', tags: [], notes: '' }

const TAG_COLORS = {
  loyal:    'bg-emerald-50 text-emerald-700',
  vip:      'bg-violet-50 text-violet-700',
  at_risk:  'bg-red-50 text-red-700',
  inactive: 'bg-slate-100 text-slate-500',
  new:      'bg-blue-50 text-blue-700',
}

export default function Customers() {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

  const { data: customers = [] } = useQuery({
    queryKey: ['customers'],
    queryFn: () => customersApi.list({ limit: 100 }).then((r) => r.data),
  })

  const save = useMutation({
    mutationFn: (d) => editing ? customersApi.update(editing.id, d) : customersApi.create(d),
    onSuccess: () => { qc.invalidateQueries(['customers']); closeModal() },
  })

  const openNew  = () => { setEditing(null); setForm(EMPTY_FORM); setOpen(true) }
  const openEdit = (row) => { setEditing(row); setForm({ ...row, tags: row.tags || [] }); setOpen(true) }
  const closeModal = () => { setOpen(false); setEditing(null) }

  const toggleTag = (t) => {
    setForm((f) => ({ ...f, tags: f.tags.includes(t) ? f.tags.filter((x) => x !== t) : [...f.tags, t] }))
  }

  const totalLTV  = customers.reduce((s, c) => s + (c.lifetime_value || 0), 0)
  const repeatPct = customers.length
    ? ((customers.filter((c) => c.total_orders > 1).length / customers.length) * 100).toFixed(0)
    : 0

  const columns = [
    { key: 'name',             label: 'Name',          render: (v) => <span className="font-medium">{v}</span> },
    { key: 'phone',            label: 'Phone' },
    { key: 'total_orders',     label: 'Orders' },
    { key: 'lifetime_value',   label: 'LTV',           render: (v) => `$${Number(v).toLocaleString()}` },
    { key: 'last_purchase_at', label: 'Last Purchase',  render: (v) => v ? format(new Date(v), 'MMM d, yyyy') : '—' },
    { key: 'tags',             label: 'Tags',           render: (v) => (v || []).map((t) => (
      <span key={t} className={`badge mr-1 ${TAG_COLORS[t] || 'bg-slate-100 text-slate-500'}`}>{t}</span>
    ))},
  ]

  return (
    <div className="page-shell">
      <section className="page-command">
        <div>
          <div className="section-kicker">Relationships</div>
          <h1 className="page-title">Customers</h1>
          <p className="page-copy">Keep customer value, repeat behavior, and account notes easy to scan.</p>
        </div>
        <button className="btn-primary" onClick={openNew}>+ Add Customer</button>
      </section>

      <section className="metric-strip md:grid-cols-3">
        <div className="metric-chip">
          <div className="metric-label">Customers</div>
          <div className="metric-value">{customers.length}</div>
          <p className="mt-2 text-xs text-slate-400">all time</p>
        </div>
        <div className="metric-chip">
          <div className="metric-label">Total LTV</div>
          <div className="metric-value">${totalLTV.toLocaleString()}</div>
          <p className="mt-2 text-xs text-slate-400">lifetime revenue</p>
        </div>
        <div className="metric-chip">
          <div className="metric-label">Repeat Rate</div>
          <div className="metric-value">{repeatPct}%</div>
          <p className="mt-2 text-xs text-slate-400">2+ orders</p>
        </div>
      </section>

      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold tracking-tight text-slate-950">Customer directory</h2>
        <span className="text-sm text-slate-400">{customers.length} total</span>
      </div>

      <DataTable columns={columns} data={customers} onRowClick={openEdit} emptyMessage="No customers yet. Convert a lead or add one manually." />

      <Modal open={open} onClose={closeModal} title={editing ? 'Edit Customer' : 'Add Customer'}>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(form) }} className="space-y-4">
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
              <label className="label">Address</label>
              <input className="input" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="label">Tags</label>
            <div className="flex gap-2 flex-wrap mt-1">
              {TAGS.map((t) => (
                <button
                  key={t} type="button"
                  className={`badge text-sm cursor-pointer border transition-colors ${form.tags.includes(t) ? 'bg-brand-500 text-white border-brand-500' : 'bg-slate-50 text-slate-600 border-slate-200 hover:border-brand-300'}`}
                  onClick={() => toggleTag(t)}
                >{t}</button>
              ))}
            </div>
          </div>
          <div>
            <label className="label">Notes</label>
            <textarea rows={2} className="input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
          {/* TW-1322: surface save failures (was silent). Entries stay in
              the form; sample-mode blocks route to sign-in instead. */}
          {save.isError && save.error?.code !== SAMPLE_WRITE_BLOCKED && (
            <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
              {save.error?.response?.data?.detail || 'Could not save. Your entries are still here. Check your connection and try again.'}
            </div>
          )}
          <div className="flex gap-3 justify-end pt-2">
            <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={save.isPending}>
              {save.isPending ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
