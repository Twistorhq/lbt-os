/**
 * Twistor-branded implementations for the agent-UI catalog (TW-181).
 * Near-black + indigo-violet, photographic-grade restraint, culture-first copy.
 * Every component assumes its props already passed zod validation in TwistorRenderer.
 */

const surface = 'rounded-2xl border border-white/10 bg-[#0d0d17]/90 shadow-[0_8px_30px_rgba(0,0,0,0.45)]'
const accent = 'text-violet-300'

export function DashboardSection({ props, children }) {
  return (
    <section aria-label={props.title} className="mb-8">
      <header className="mb-4">
        <h2 className="text-xl font-semibold tracking-tight text-white">{props.title}</h2>
        {props.subtitle ? <p className="mt-1 text-sm text-white/60">{props.subtitle}</p> : null}
      </header>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{children}</div>
    </section>
  )
}

export function MetricCard({ props }) {
  const trendColor =
    props.trend === 'up' ? 'text-emerald-300' : props.trend === 'down' ? 'text-rose-300' : 'text-white/50'
  const trendArrow = props.trend === 'up' ? '▲' : props.trend === 'down' ? '▼' : '●'
  return (
    <div className={`${surface} p-5`}>
      <p className="text-xs font-medium uppercase tracking-widest text-white/50">{props.label}</p>
      <p className="mt-2 text-3xl font-bold tracking-tight text-white">{props.value}</p>
      {props.delta ? (
        <p className={`mt-2 text-sm font-medium ${trendColor}`}>
          <span aria-hidden="true">{trendArrow}</span> {props.delta}
        </p>
      ) : null}
    </div>
  )
}

export function RevenueLeak({ props }) {
  return (
    <div className={`${surface} relative overflow-hidden p-5`}>
      <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-violet-400 to-fuchsia-500" />
      <p className="pl-3 text-xs font-medium uppercase tracking-widest text-white/50">Money walking out the door</p>
      <p className="mt-2 pl-3 text-lg font-semibold text-white">{props.title}</p>
      <p className={`mt-1 pl-3 text-2xl font-bold ${accent}`}>{props.amount}</p>
      {props.detail ? <p className="mt-2 pl-3 text-sm text-white/60">{props.detail}</p> : null}
    </div>
  )
}

const outcomeStyles = {
  answered: 'bg-emerald-400/15 text-emerald-300 border-emerald-300/30',
  missed: 'bg-rose-400/15 text-rose-300 border-rose-300/30',
  voicemail: 'bg-amber-400/15 text-amber-300 border-amber-300/30',
}

export function CallSummary({ props }) {
  const badge = outcomeStyles[props.outcome] ?? outcomeStyles.missed
  return (
    <article className={`${surface} p-5`}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-semibold text-white">{props.caller}</h3>
        <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${badge}`}>
          {props.outcome}
        </span>
      </div>
      <p className="mt-1 text-xs text-white/50">{props.time}</p>
      <p className="mt-3 text-sm leading-relaxed text-white/80">{props.summary}</p>
      {props.followUp ? (
        <p className="mt-3 rounded-lg bg-violet-400/10 px-3 py-2 text-sm text-violet-200">
          <span className="font-medium">Next step: </span>{props.followUp}
        </p>
      ) : null}
    </article>
  )
}

export function FollowUpQueue({ props }) {
  const items = Array.isArray(props.items) ? props.items : []
  return (
    <div className={`${surface} p-5`}>
      <h3 className="font-semibold text-white">Follow-up queue</h3>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-white/60">Queue is clear. Nothing slipping through.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {items.map((item, i) => (
            <li key={`${item.name}-${i}`} className="flex items-start gap-3 rounded-xl bg-white/[0.03] p-3">
              <span aria-hidden="true" className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${accent} bg-violet-400/15`}>
                {i + 1}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-white">{item.name}</p>
                <p className="text-sm text-white/60">{item.reason}</p>
                <p className="mt-0.5 text-xs font-medium text-violet-300">Due {item.due}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export function ActionButton({ props, emit }) {
  return (
    <button
      type="button"
      onClick={() => emit && emit(props.action)}
      className="rounded-xl bg-gradient-to-r from-violet-500 to-fuchsia-600 px-5 py-3 text-sm font-semibold text-white shadow-lg shadow-violet-900/40 transition-transform hover:scale-[1.02] focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 active:scale-[0.99]"
    >
      {props.label}
    </button>
  )
}

/** Registry: spec type -> implementation. TwistorRenderer reads this. */
export const componentRegistry = {
  DashboardSection,
  MetricCard,
  RevenueLeak,
  CallSummary,
  FollowUpQueue,
  ActionButton,
}
