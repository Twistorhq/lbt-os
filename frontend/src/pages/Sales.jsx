import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { salesApi } from '../lib/api'
import { SAMPLE_WRITE_BLOCKED } from '../lib/sampleMode'
import DataTable from '../components/ui/DataTable'
import Modal from '../components/ui/Modal'
import { format } from 'date-fns'

const STATUS_COLORS = {
  pending:  'badge bg-yellow-50 text-yellow-700',
  paid:     'badge bg-green-50 text-green-700',
  refunded: 'badge bg-red-50 text-red-700',
}

const EMPTY_FORM = {
  service: '', amount: '', cost: '0', payment_method: 'card',
  payment_status: 'pending', source: '', invoice_number: '', notes: '',
}

export default function Sales() {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

  const { data: sales = [] } = useQuery({
    queryKey: ['sales'],
    queryFn: () => salesApi.list({ limit: 100 }).then((r) => r.data),
  })

  const save = useMutation({
    mutationFn: (d) => editing ? salesApi.update(editing.id, d) : salesApi.create(d),
    onSuccess: () => { qc.invalidateQueries(['sales']); qc.invalidateQueries(['metrics']); closeModal() },
  })

  const openNew  = () => { setEditing(null); setForm(EMPTY_FORM); setOpen(true) }
  const openEdit = (row) => { setEditing(row); setForm({ ...row, amount: row.amount || '', cost: row.cost || '0' }); setOpen(true) }
  const closeModal = () => { setOpen(false); setEditing(null) }

  const handleSubmit = (e) => {
    e.preventDefault()
    save.mutate({ ...form, amount: parseFloat(form.amount), cost: parseFloat(form.cost || 0) })
  }

  const paid    = sales.filter((s) => s.payment_status === 'paid')
  const revenue = paid.reduce((sum, s) => sum + s.amount, 0)
  const profit  = paid.reduce((sum, s) => sum + s.profit, 0)
  const margin  = revenue > 0 ? ((profit / revenue) * 100).toFixed(0) : 0

  const columns = [
    { key: 'service',        label: 'Service',  render: (v) => <span className="font-medium">{v}</span> },
    { key: 'amount',         label: 'Amount',   render: (v) => `$${Number(v).toLocaleString()}` },
    { key: 'profit',         label: 'Profit',   render: (v) => <span className={v < 0 ? 'text-red-600' : 'text-green-600'}>${Number(v).toLocaleString()}</span> },
    { key: 'payment_status', label: 'Status',   render: (v) => <span className={STATUS_COLORS[v]}>{v}</span> },
    { key: 'source',         label: 'Source',   render: (v) => <span className="capitalize">{v || '—'}</span> },
    { key: 'sold_at',        label: 'Date',     render: (v) => format(new Date(v), 'MMM d, yyyy') },
  ]

  return (
    <div className="page-shell">
      <section className="page-command">
        <div>
          <div className="section-kicker">Revenue</div>
          <h1 className="page-title">Sales</h1>
          <p className="page-copy">Record transactions and keep margin visible without turning the page into a spreadsheet.</p>
        </div>
        <button className="btn-primary" onClick={openNew}>+ Add Sale</button>
      </section>

      <section className="metric-strip md:grid-cols-3">
        <div className="metric-chip">
          <div className="metric-label">Revenue Paid</div>
          <div className="metric-value">${revenue.toLocaleString()}</div>
          <p className="mt-2 text-xs text-slate-400">{paid.length} paid transactions</p>
        </div>
        <div className="metric-chip">
          <div className="metric-label">Gross Profit</div>
          <div className="metric-value">${profit.toLocaleString()}</div>
          <p className="mt-2 text-xs text-slate-400">{margin}% margin</p>
        </div>
        <div className="metric-chip">
          <div className="metric-label">Transactions</div>
          <div className="metric-value">{sales.length}</div>
          <p className="mt-2 text-xs text-slate-400">all statuses</p>
        </div>
      </section>

      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold tracking-tight text-slate-950">Transaction history</h2>
        <span className="text-sm text-slate-400">{sales.length} total</span>
      </div>

      <DataTable columns={columns} data={sales} onRowClick={openEdit} emptyMessage="No sales yet. Record your first transaction." />

      <Modal open={open} onClose={closeModal} title={editing ? 'Edit Sale' : 'Record Sale'}>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <label className="label">Service / Product *</label>
              <input required className="input" value={form.service} onChange={(e) => setForm({ ...form, service: e.target.value })} placeholder="e.g. AC Installation" />
            </div>
            <div>
              <label className="label">Amount ($) *</label>
              <input required type="number" min="0" step="0.01" className="input" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </div>
            <div>
              <label className="label">Cost ($)</label>
              <input type="number" min="0" step="0.01" className="input" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} />
            </div>
            <div>
              <label className="label">Payment Method</label>
              <select className="input" value={form.payment_method} onChange={(e) => setForm({ ...form, payment_method: e.target.value })}>
                {['cash', 'card', 'check', 'bank_transfer', 'financing'].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Payment Status</label>
              <select className="input" value={form.payment_status} onChange={(e) => setForm({ ...form, payment_status: e.target.value })}>
                {['pending', 'paid', 'refunded'].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Lead Source</label>
              <input className="input" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} placeholder="google, referral..." />
            </div>
            <div>
              <label className="label">Invoice #</label>
              <input className="input" value={form.invoice_number} onChange={(e) => setForm({ ...form, invoice_number: e.target.value })} />
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
              {save.isPending ? 'Saving...' : editing ? 'Save Changes' : 'Record Sale'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
