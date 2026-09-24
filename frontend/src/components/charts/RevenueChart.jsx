import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts'
import { format, parseISO } from 'date-fns'

const fmt = (v) => `$${(v || 0).toLocaleString()}`

// TW-195 (Rosa nit): recharts animations are JS-driven, so the CSS
// prefers-reduced-motion kill-switch does not cover them. Disable the Area
// animation for reduced-motion users; API unchanged.
const reduceMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

export default function RevenueChart({ data = [] }) {
  if (!data.length) {
    return (
      <div className="flex h-60 flex-col items-center justify-center rounded-[1.5rem] border border-dashed border-slate-200 bg-slate-50/80 px-6 text-center">
        <div className="text-sm font-semibold text-slate-700">No revenue data yet</div>
        <div className="mt-1 max-w-sm text-sm leading-6 text-slate-500">
          Closed sales will start shaping this trend automatically once your team logs them.
        </div>
      </div>
    )
  }

  const formatted = data.map((d) => ({
    ...d,
    label: format(parseISO(d.date), 'MMM d'),
  }))

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={formatted} margin={{ top: 12, right: 12, left: -12, bottom: 0 }}>
        <defs>
          <linearGradient id="revGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#2563eb" stopOpacity={0.32} />
            <stop offset="55%" stopColor="#3b82f6" stopOpacity={0.12} />
            <stop offset="95%" stopColor="#2563eb" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} dy={10} />
        <YAxis tickFormatter={fmt} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={76} />
        <Tooltip
          formatter={(v) => [fmt(v), 'Revenue']}
          labelStyle={{ color: '#0f172a', fontWeight: 600 }}
          contentStyle={{
            borderRadius: 16,
            border: '1px solid #dbe4f0',
            fontSize: 12,
            background: 'rgba(255,255,255,0.96)',
            boxShadow: '0 16px 40px -24px rgba(15,23,42,0.45)',
          }}
        />
        <Area type="monotone" dataKey="revenue" stroke="#2563eb" strokeWidth={2.5} fill="url(#revGrad)" isAnimationActive={!reduceMotion} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
