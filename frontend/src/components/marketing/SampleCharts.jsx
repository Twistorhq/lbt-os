import { useMemo } from 'react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import RevenueChart from '../charts/RevenueChart'

/* TW-195: sample-data charts for the marketing live-preview demo.
 * Justynn 2026-09-24: nothing paywalled gets overlooked — visitors see the
 * charts running on sample data so they know exactly what the product shows.
 * Everything here is synthetic sample data, badged as such; connecting a
 * real shop replaces it with their numbers.
 */

// 12 weeks of synthetic weekly figures per trade. Dates are generated
// relative to today so the demo always reads as current.
const SAMPLE_WEEKLY = {
  hvac: {
    revenue: [38200, 41500, 44800, 42900, 47200, 45100, 49800, 51300, 48900, 52600, 50200, 54100],
    sent: [22, 25, 21, 26, 24, 28, 27, 29, 25, 30, 28, 31],
    won: [8, 10, 9, 11, 10, 12, 11, 13, 10, 14, 12, 15],
  },
  plumbing: {
    revenue: [24500, 26800, 25900, 28400, 27200, 30100, 29500, 31200, 28900, 32600, 31800, 33900],
    sent: [16, 18, 15, 19, 17, 20, 19, 21, 18, 22, 20, 23],
    won: [6, 7, 6, 8, 7, 9, 8, 10, 7, 11, 9, 12],
  },
  electrical: {
    revenue: [31200, 33500, 32800, 35900, 34700, 37200, 36800, 39100, 38400, 40800, 39600, 42300],
    sent: [14, 16, 13, 17, 15, 18, 16, 19, 15, 20, 18, 21],
    won: [7, 8, 7, 9, 8, 10, 9, 11, 8, 12, 10, 13],
  },
}

function buildSeries(tradeKey) {
  const s = SAMPLE_WEEKLY[tradeKey] || SAMPLE_WEEKLY.hvac
  const now = new Date()
  // Anchor on Monday of the current week so labels stay tidy.
  const monday = new Date(now)
  monday.setDate(now.getDate() - ((now.getDay() + 6) % 7))
  return s.revenue.map((revenue, i) => {
    const d = new Date(monday)
    d.setDate(monday.getDate() - (11 - i) * 7)
    return {
      date: d.toISOString().slice(0, 10),
      revenue,
      sent: s.sent[i],
      won: s.won[i],
    }
  })
}

const money = (v) => `$${(v || 0).toLocaleString()}`

function EstimateBars({ data }) {
  const reduceMotion =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 12, right: 12, left: -18, bottom: 0 }} barGap={3}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 11, fill: '#64748b' }}
          axisLine={false}
          tickLine={false}
          dy={10}
          interval={1}
        />
        <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={40} />
        <Tooltip
          contentStyle={{
            borderRadius: 16,
            border: '1px solid #dbe4f0',
            fontSize: 12,
            background: 'rgba(255,255,255,0.96)',
            boxShadow: '0 16px 40px -24px rgba(15,23,42,0.45)',
          }}
          labelStyle={{ color: '#0f172a', fontWeight: 600 }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="sent" name="Estimates sent" fill="#cbd5e1" radius={[6, 6, 0, 0]} isAnimationActive={!reduceMotion} />
        <Bar dataKey="won" name="Estimates won" fill="#6366f1" radius={[6, 6, 0, 0]} isAnimationActive={!reduceMotion} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/**
 * SampleCharts — the product's chart surface running on synthetic sample
 * data, for the marketing live-preview. Switches with the trade picker.
 */
export default function SampleCharts({ tradeKey, tradeLabel }) {
  const series = useMemo(() => buildSeries(tradeKey), [tradeKey])
  const withLabels = useMemo(
    () =>
      series.map((p) => ({
        ...p,
        label: new Date(`${p.date}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      })),
    [series],
  )
  const first = series[0]
  const last = series[series.length - 1]
  const trend = last.revenue >= first.revenue ? 'trending up' : 'trending down'
  const totalWon = series.reduce((a, p) => a + p.won, 0)
  const totalSent = series.reduce((a, p) => a + p.sent, 0)

  return (
    <div className="mt-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="tt-kicker text-indigo-600">Full surface, sampled</div>
          <h3 className="tt-h2 mt-3 max-w-xl text-2xl text-slate-950 sm:text-3xl">
            The charts are the product.
          </h3>
          <p className="mt-3 max-w-xl text-[15px] leading-7 text-slate-600">
            No paywalled parts hiding. Every chart here runs on sample data for a {tradeLabel} shop —
            connect your tools and these become your numbers.
          </p>
        </div>
        <span className="tt-sample-badge on-light">Sample data</span>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <figure className="rounded-[1.75rem] border border-slate-200/80 bg-white p-6 shadow-[0_24px_60px_-40px_rgba(10,10,24,0.25)] sm:p-8">
          <div className="flex items-center justify-between gap-3">
            <figcaption className="text-base font-bold tracking-tight text-slate-900">
              Weekly revenue
            </figcaption>
            <span className="tt-sample-badge on-light">Sample data</span>
          </div>
          <div
            className="mt-4"
            role="img"
            aria-label={`Sample weekly revenue for the ${tradeLabel} demo shop, ${trend} over the last 12 weeks, from ${money(first.revenue)} to ${money(last.revenue)}.`}
          >
            <RevenueChart data={series} />
          </div>
        </figure>

        <figure className="rounded-[1.75rem] border border-slate-200/80 bg-white p-6 shadow-[0_24px_60px_-40px_rgba(10,10,24,0.25)] sm:p-8">
          <div className="flex items-center justify-between gap-3">
            <figcaption className="text-base font-bold tracking-tight text-slate-900">
              Estimates sent vs won
            </figcaption>
            <span className="tt-sample-badge on-light">Sample data</span>
          </div>
          <div
            className="mt-4"
            role="img"
            aria-label={`Sample estimates for the ${tradeLabel} demo shop: ${totalWon} won out of ${totalSent} sent over the last 12 weeks.`}
          >
            <EstimateBars data={withLabels} />
          </div>
        </figure>
      </div>
    </div>
  )
}
