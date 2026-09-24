import { Reveal } from '../motion'
import { useCountUp, fmtMoney } from './useCountUp'

/**
 * TW-207 Act 0 — the opening shot. Totals counted up with cinematic pacing
 * against near-black: the money moment in violet-to-fuchsia.
 */
export default function BriefHero({ findings, dollarsAtStake, generatedAt }) {
  const count = useCountUp(findings, { duration: 1100 })
  const dollars = useCountUp(dollarsAtStake, { duration: 1600 })

  const date = generatedAt
    ? new Date(generatedAt).toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
      })
    : ''

  return (
    <header className="relative overflow-hidden rounded-3xl border border-white/10 bg-[#0d0d17] px-6 py-10 sm:px-10 sm:py-14">
      {/* ambient violet glow — decorative, aria-hidden */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-32 left-1/2 h-72 w-[42rem] -translate-x-1/2 rounded-full bg-violet-600/25 blur-3xl"
      />
      <div className="relative">
        <Reveal>
          <p className="tt-kicker text-violet-300/90">
            Your Morning Brief{date ? ` — ${date}` : ''}
          </p>
        </Reveal>
        <Reveal delay={120}>
          <h1 className="tt-h2 mt-4 max-w-2xl text-4xl text-white sm:text-5xl">
            Here&rsquo;s what walked out the door.
          </h1>
        </Reveal>
        <div className="mt-10 grid gap-8 sm:grid-cols-2">
          <Reveal delay={260}>
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-white/60">
                Leaks found
              </div>
              <div
                className="mt-2 text-5xl font-bold tabular-nums text-white"
                aria-hidden="true"
              >
                {Math.round(count)}
              </div>
              <span className="sr-only" aria-live="polite">
                {Math.round(findings)} leaks found
              </span>
            </div>
          </Reveal>
          <Reveal delay={380}>
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-white/60">
                Dollars at stake
              </div>
              <div
                className="tt-gradient-word mt-2 text-5xl font-bold tabular-nums sm:text-6xl"
                aria-hidden="true"
              >
                {fmtMoney(dollars)}
              </div>
              <span className="sr-only" aria-live="polite">
                {fmtMoney(dollarsAtStake)} dollars at stake
              </span>
            </div>
          </Reveal>
        </div>
      </div>
    </header>
  )
}
