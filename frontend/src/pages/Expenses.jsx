import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { expensesApi } from '../lib/api'
import { SAMPLE_WRITE_BLOCKED } from '../lib/sampleMode'
import DataTable from '../components/ui/DataTable'
import Modal from '../components/ui/Modal'
import { format } from 'date-fns'

const CATEGORIES = ['payroll', 'materials', 'marketing', 'rent', 'utilities', 'equipment', 'insurance', 'software', 'misc']

const EMPTY_FORM = {
  category: 'misc', description: '', amount: '', vendor: '',
  is_recurring: false, recurrence_period: '', expense_date: new Date().toISOString().split('T')[0],
}

const CAT_COLORS = {
  payroll:    'bg-blue-50 text-blue-700',
  materials:  'bg-orange-50 text-orange-700',
  marketing:  'bg-purple-50 text-purple-700',
  rent:       'bg-red-50 text-red-700',
  utilities:  'bg-yellow-50 text-yellow-700',
  equipment:  'bg-indigo-50 text-indigo-700',
  insurance:  'bg-teal-50 text-teal-700',
  software:   'bg-cyan-50 text-cyan-700',
  misc:       'bg-slate-100 text-slate-500',
}

export default function Expenses() {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

  const { data: expenses = [] } = useQuery({
    queryKey: ['expenses'],
    queryFn: () => expensesApi.list({ limit: 100 }).then((r) => r.data),
  })

  const save = useMutation({
    mutationFn: (d) => editing ? expensesApi.update(editing.id, d) : expensesApi.create(d),
    onSuccess: () => { qc.invalidateQueries(['expenses']); qc.invalidateQueries(['metrics']); closeModal() },
  })

  const openNew  = () => { setEditing(null); setForm(EMPTY_FORM); setOpen(true) }
  const openEdit = (row) => { setEditing(row); setForm({ ...row, amount: row.amount || '' }); setOpen(true) }
  const closeModal = () => { setOpen(false); setEditing(null) }

  const byCategory = expenses.reduce((acc, e) => {
    acc[e.category] = (acc[e.category] || 0) + e.amount
    return acc
  }, {})
  const total      = expenses.reduce((s, e) => s + e.amount, 0)
  const topCats    = Object.entries(byCategory).sort(([, a], [, b]) => b - a).slice(0, 3)

  const columns = [
    { key: 'description', label: 'Description', render: (v) => <span className="font-medium">{v}</span> },
    { key: 'category',    label: 'Category',    render: (v) => <span className={`badge ${CAT_COLORS[v] || 'bg-slate-100 text-slate-500'}`}>{v}</span> },
    { key: 'amount',      label: 'Amount',      render: (v) => `$${Number(v).toLocaleString()}` },
    { key: 'vendor',      label: 'Vendor' },
    { key: 'is_recurring',label: 'Recurring',   render: (v) => v ? <span className="badge bg-indigo-50 text-indigo-700">recurring</span> : '' },
    { key: 'expense_date',label: 'Date',        render: (v) => format(new Date(v), 'MMM d, yyyy') },
  ]

  return (
    <div className="page-shell">
      <section className="page-command">
        <div>
          <div className="section-kicker">Cost Control</div>
          <h1 className="page-title">Expenses</h1>
          <p className="page-copy">Track spend and recurring obligations while keeping margin pressure visible.</p>
        </div>
        <button className="btn-primary" onClick={openNew}>+ Add Expense</button>
      </section>

      <section className="metric-strip md:grid-cols-4">
        <div className="metric-chip">
          <div className="metric-label">Total Expenses</div>
          <div className="metric-value text-rose-600">${total.toLocaleString()}</div>
          <p className="mt-2 text-xs text-slate-400">{expenses.length} entries</p>
        </div>
        {topCats.map(([cat, amt]) => (
          <div key={cat} className="metric-chip">
            <div className="metric-label capitalize">{cat}</div>
            <div className="metric-value">${Number(amt).toLocaleString()}</div>
            <p className="mt-2 text-xs text-slate-400">top category</p>
          </div>
        ))}
      </section>

      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold tracking-tight text-slate-950">Expense log</h2>
        <span className="text-sm text-slate-400">{expenses.length} total</span>
      </div>

      <DataTable columns={columns} data={expenses} onRowClick={openEdit} emptyMessage="No expenses recorded yet." />

      <Modal open={open} onClose={closeModal} title={editing ? 'Edit Expense' : 'Add Expense'}>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate({ ...form, amount: parseFloat(form.amount) }) }} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <label className="label">Description *</label>
              <input required className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="e.g. Facebook Ads — June" />
            </div>
            <div>
              <label className="label">Category *</label>
              <select className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Amount ($) *</label>
              <input required type="number" min="0.01" step="0.01" className="input" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </div>
            <div>
              <label className="label">Vendor</label>
              <input className="input" value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
            </div>
            <div>
              <label className="label">Date</label>
              <input type="date" className="input" value={form.expense_date} onChange={(e) => setForm({ ...form, expense_date: e.target.value })} />
            </div>
            <div className="col-span-2 flex items-center gap-3">
              <input type="checkbox" id="recurring" checked={form.is_recurring} onChange={(e) => setForm({ ...form, is_recurring: e.target.checked })} className="rounded" />
              <label htmlFor="recurring" className="text-sm text-slate-700">Recurring expense</label>
              {form.is_recurring && (
                <select className="input w-auto" value={form.recurrence_period} onChange={(e) => setForm({ ...form, recurrence_period: e.target.value })}>
                  <option value="">Period...</option>
                  {['weekly', 'monthly', 'quarterly', 'annual'].map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              )}
            </div>
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
