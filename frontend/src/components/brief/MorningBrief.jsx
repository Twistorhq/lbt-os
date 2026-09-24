import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { leakApi } from '../../lib/api'
import { Reveal } from '../motion'
import BriefHero from './BriefHero'
import BriefScene from './BriefScene'

/**
 * TW-207: the Morning Brief — three acts, not three tabs. The product's
 * signature moment: a reveal, not a dashboard.
 *
 * Act I — What Happened. Act II — What Will Happen. Act III — What Should We Do.
 * Honest states throughout: no data degrades to an invitation to connect,
 * never a fabricated leak.
 */

const ACTS = [
  {
    key: 'what_happened',
    eyebrow: 'Act I',
    title: 'What Happened',
    sub: 'The money already walking out the door.',
  },
  {
    key: 'what_will_happen',
    eyebrow: 'Act II',
    title: 'What Will Happen',
    sub: 'The leaks still forming — if nothing changes.',
  },
  {
    key: 'what_should_we_do',
    eyebrow: 'Act III',
    title: 'What Should We Do',
    sub: 'The fixes, in the order that pays.',
  },
]

function LoadingState() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading your morning brief">
      <div className="h-56 motion-safe:animate-pulse rounded-3xl border border-white/10 bg-white/5" />
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-40 motion-safe:animate-pulse rounded-2xl border border-white/10 bg-white/5"
        />
      ))}
    </div>
  )
}

function HonestEmpty({ dataStatus }) {
  const missing = dataStatus?.insufficient || []
  return (
    <div className="rounded-3xl border border-white/10 bg-[#0d0d17] px-6 py-14 text-center sm:px-10">
      <p className="tt-kicker text-violet-300/90">Your Morning Brief</p>
      <h1 className="tt-h2 mx-auto mt-4 max-w-xl text-3xl text-white sm:text-4xl">
        Connect your data and the brief writes itself.
      </h1>
      <p className="mx-auto mt-4 max-w-lg leading-relaxed text-white/60">
        {missing.length > 0
          ? 'The leak engine is ready — it just needs something to read. Connect your sources and tomorrow morning starts with answers.'
          : 'No leaks found in the latest scan. The engine is watching.'}
      </p>
      <Link
        to="/app/connections"
        className="mt-8 inline-flex rounded-xl bg-gradient-to-r from-violet-500 to-fuchsia-600 px-6 py-3 text-sm font-semibold text-white transition-transform hover:scale-[1.02] focus-visible:ring-2 focus-visible:ring-violet-300"
      >
        Connect your sources
      </Link>
    </div>
  )
}

export default function MorningBrief() {
  const { data: brief, isLoading, isError } = useQuery({
    queryKey: ['leak-brief'],
    queryFn: () => leakApi.brief().then((r) => r.data),
  })

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
        <LoadingState />
      </div>
    )
  }

  if (isError || !brief) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
        <div className="rounded-3xl border border-white/10 bg-[#0d0d17] px-6 py-14 text-center">
          <h1 className="tt-h2 text-2xl text-white">
            The brief couldn&rsquo;t load this morning.
          </h1>
          <p className="mx-auto mt-3 max-w-md text-white/60">
            Try refreshing — your data is safe, this is just the delivery.
          </p>
        </div>
      </div>
    )
  }

  const totals = brief.totals || { findings: 0, dollars_at_stake: 0 }
  const acts = ACTS.map((act) => ({
    ...act,
    findings: brief[act.key] || [],
  }))
  const allEmpty = acts.every((a) => a.findings.length === 0)

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      {allEmpty ? (
        <HonestEmpty dataStatus={brief.data_status} />
      ) : (
        <>
          <BriefHero
            findings={totals.findings}
            dollarsAtStake={totals.dollars_at_stake}
            generatedAt={brief.generated_at}
          />

          {acts.map((act) =>
            act.findings.length === 0 ? null : (
              <section key={act.key} aria-label={act.title} className="mt-14">
                <Reveal>
                  <p className="tt-kicker text-violet-300/90">{act.eyebrow}</p>
                  <h2 className="tt-h2 mt-2 text-3xl text-white sm:text-4xl">
                    {act.title}
                  </h2>
                  <p className="mt-2 text-white/55">{act.sub}</p>
                </Reveal>
                <div className="mt-6 space-y-5">
                  {act.findings.map((f, i) => (
                    <BriefScene
                      key={`${f.detector}-${i}`}
                      finding={f}
                      index={i}
                    />
                  ))}
                </div>
              </section>
            )
          )}

          <Reveal className="mt-14">
            <p className="text-center text-sm text-white/60">
              The brief refreshes every morning. The engine never sleeps.
            </p>
          </Reveal>
        </>
      )}
    </div>
  )
}
