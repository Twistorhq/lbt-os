import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { roofingApi } from '../lib/api'
import Modal from '../components/ui/Modal'

const MATERIALS = [
  'architectural_shingle',
  'metal_standing_seam',
  'tile',
  'tpo',
]

const TABS = [
  { id: 'takeoff', label: 'Takeoff' },
  { id: 'suppliers', label: 'Suppliers' },
  { id: 'storms', label: 'Storms' },
]

function TakeoffTab() {
  const [form, setForm] = useState({ quoteId: '', squares: '', pitch: '6/12', waste_pct: 10, material: 'architectural_shingle' })
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const run = async () => {
    setError(null); setResult(null)
    try {
      const r = await roofingApi.takeoff(form.quoteId, {
        squares: parseFloat(form.squares),
        pitch: form.pitch,
        waste_pct: parseFloat(form.waste_pct),
        material: form.material,
      })
      setResult(r.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Takeoff failed')
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <p className="text-sm text-slate-500">
        Measurements in, line items out. Exact-cent math, stored on the quote.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-sm">Quote ID
          <input className="mt-1 w-full rounded border px-2 py-1" value={form.quoteId}
            onChange={(e) => setForm({ ...form, quoteId: e.target.value })} placeholder="quote uuid" />
        </label>
        <label className="block text-sm">Squares
          <input type="number" step="0.1" className="mt-1 w-full rounded border px-2 py-1" value={form.squares}
            onChange={(e) => setForm({ ...form, squares: e.target.value })} />
        </label>
        <label className="block text-sm">Pitch
          <input className="mt-1 w-full rounded border px-2 py-1" value={form.pitch}
            onChange={(e) => setForm({ ...form, pitch: e.target.value })} />
        </label>
        <label className="block text-sm">Waste %
          <input type="number" step="1" className="mt-1 w-full rounded border px-2 py-1" value={form.waste_pct}
            onChange={(e) => setForm({ ...form, waste_pct: e.target.value })} />
        </label>
        <label className="block text-sm col-span-2">Material
          <select className="mt-1 w-full rounded border px-2 py-1" value={form.material}
            onChange={(e) => setForm({ ...form, material: e.target.value })}>
            {MATERIALS.map((m) => <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>)}
          </select>
        </label>
      </div>
      <button onClick={run} className="rounded bg-slate-900 px-4 py-2 text-sm text-white">Compute takeoff</button>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {result && (
        <div className="rounded border p-4 text-sm">
          <p className="font-semibold">Total: ${Number(result.total).toFixed(2)}</p>
          <ul className="mt-2 space-y-1">
            {result.line_items.map((li, i) => (
              <li key={i}>{li.description} — {li.quantity} × ${Number(li.unit_price).toFixed(2)} = ${Number(li.amount).toFixed(2)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function SuppliersTab() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ name: '', branch: '', phone: '', ordering_email: '', account_number: '' })
  const { data: suppliers = [] } = useQuery({ queryKey: ['suppliers'], queryFn: () => roofingApi.suppliers.list().then((r) => r.data) })
  const { data: pos = [] } = useQuery({ queryKey: ['pos'], queryFn: () => roofingApi.purchaseOrders.list().then((r) => r.data) })

  const save = useMutation({
    mutationFn: (d) => roofingApi.suppliers.create(d),
    onSuccess: () => { qc.invalidateQueries(['suppliers']); setOpen(false); setForm({ name: '', branch: '', phone: '', ordering_email: '', account_number: '' }) },
  })
  const send = useMutation({
    mutationFn: (id) => roofingApi.purchaseOrders.send(id),
    onSuccess: () => qc.invalidateQueries(['pos']),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Suppliers</h3>
        <button onClick={() => setOpen(true)} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">Add supplier</button>
      </div>
      <ul className="space-y-2 text-sm">
        {suppliers.map((s) => (
          <li key={s.id} className="rounded border p-3">
            <span className="font-medium">{s.name}</span>
            {s.branch && <span className="text-slate-500"> — {s.branch}</span>}
            <span className="ml-3 text-slate-400">{s.ordering_email} · {s.phone}</span>
          </li>
        ))}
        {suppliers.length === 0 && <li className="text-slate-400">No suppliers yet.</li>}
      </ul>
      <h3 className="font-semibold">Purchase orders</h3>
      <ul className="space-y-2 text-sm">
        {pos.map((p) => (
          <li key={p.id} className="flex items-center justify-between rounded border p-3">
            <span>{p.line_items?.length || 0} items · ${Number(p.total_cost || 0).toFixed(2)} · <span className="text-slate-500">{p.status}</span></span>
            {p.status === 'draft' && (
              <button onClick={() => send.mutate(p.id)} className="rounded border px-3 py-1 text-sm">Send</button>
            )}
          </li>
        ))}
        {pos.length === 0 && <li className="text-slate-400">No purchase orders yet.</li>}
      </ul>
      <Modal open={open} onClose={() => setOpen(false)} title="Add supplier">
        <div className="space-y-3 p-6">
          {['name', 'branch', 'phone', 'ordering_email', 'account_number'].map((f) => (
            <label key={f} className="block text-sm capitalize">{f.replace('_', ' ')}
              <input className="mt-1 w-full rounded border px-2 py-1" value={form[f]}
                onChange={(e) => setForm({ ...form, [f]: e.target.value })} />
            </label>
          ))}
          <button onClick={() => save.mutate(form)} className="rounded bg-slate-900 px-4 py-2 text-sm text-white">Save</button>
        </div>
      </Modal>
    </div>
  )
}

function StormsTab() {
  const [zip, setZip] = useState('')
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const [campaign, setCampaign] = useState(null)

  const check = async () => {
    setError(null); setEvents([]); setCampaign(null)
    try {
      const r = await roofingApi.storms.recent(zip)
      setEvents(r.data.events)
    } catch (e) {
      setError(e.response?.data?.detail || 'Weather service unreachable')
    }
  }

  const launch = async (ev) => {
    setError(null)
    try {
      const r = await roofingApi.storms.campaign({
        event: ev,
        message_body: 'Hail reported near you — free storm inspection this week?',
      })
      setCampaign(r.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Campaign failed')
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <p className="text-sm text-slate-500">
        Keyless weather.gov alerts. Campaigns message existing customers only.
      </p>
      <div className="flex gap-2">
        <input className="rounded border px-2 py-1 text-sm" placeholder="ZIP" value={zip}
          onChange={(e) => setZip(e.target.value)} />
        <button onClick={check} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">Check storms</button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <ul className="space-y-2 text-sm">
        {events.map((ev) => (
          <li key={ev.nws_event_id} className="flex items-center justify-between rounded border p-3">
            <span><span className="font-medium">{ev.event_type}</span> — {ev.headline}</span>
            <button onClick={() => launch(ev)} className="rounded border px-3 py-1 text-sm">Launch campaign</button>
          </li>
        ))}
      </ul>
      {campaign && (
        <p className="text-sm text-emerald-700">
          Campaign launched: {campaign.recipient_count} customer(s) messaged.
        </p>
      )}
    </div>
  )
}

export default function Roofing() {
  const [tab, setTab] = useState('takeoff')
  return (
    <div className="p-6">
      <h1 className="text-xl font-bold">Roofing Pro Pack</h1>
      <div className="mt-4 flex gap-2 border-b pb-2">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`rounded px-3 py-1.5 text-sm ${tab === t.id ? 'bg-slate-900 text-white' : 'text-slate-600'}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="mt-6">
        {tab === 'takeoff' && <TakeoffTab />}
        {tab === 'suppliers' && <SuppliersTab />}
        {tab === 'storms' && <StormsTab />}
      </div>
    </div>
  )
}
