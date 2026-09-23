/**
 * LeakCalloutBand (TW-175) — generated through the twistor-ui skill.
 *
 * Skill workflow followed:
 * 1. Read frontend/DESIGN.md + tokens.css (near-black + indigo-violet, card/CTA patterns).
 * 2. Brief: narrative band for the marketing page — "money walking out the door";
 *    placed after the platform stats bar; copy is illustrative SAMPLE DATA.
 * 3. Generated: React + Tailwind only, no new deps, mobile-first.
 * 4. Self-check: tokens only from the table; sample numbers labeled; semantic
 *    section/article markup; real link CTA with focus ring; contrast-checked
 *    text (white / white-60+ on near-black); no fake people or testimonials;
 *    motion gated behind motion-safe.
 * 5. Delivered: this file, wired into MarketingHome.
 */
import { Link } from 'react-router-dom'

const leaks = [
  {
    title: 'After-hours callers',
    amount: '$8,900',
    detail: 'Calls that hit voicemail on nights and weekends — and never called back.',
  },
  {
    title: 'Estimates gone quiet',
    amount: '$14,200',
    detail: 'Quotes sent, no follow-up within 48 hours. The shop down the street got the job.',
  },
  {
    title: 'No-shows nobody chased',
    amount: '$3,750',
    detail: 'Booked jobs that cancelled with an empty slot nobody tried to refill.',
  },
]

export default function LeakCalloutBand() {
  return (
    <section aria-label="Revenue leaking from a typical shop" className="mt-16 motion-safe:animate-riseIn">
      <div className="overflow-hidden rounded-[1.75rem] border border-white/10 bg-[#07070d] p-8 shadow-[0_24px_65px_-34px_rgba(0,0,0,0.6)] sm:p-10">
        <p className="text-xs font-medium uppercase tracking-[0.22em] text-violet-300">The leak</p>
        <h2 className="mt-3 max-w-xl text-3xl font-bold tracking-tight text-white sm:text-4xl">
          Money walks out the door every day.
        </h2>
        <p className="mt-3 max-w-2xl text-white/60">
          A typical 6-tech shop loses this much in a month to calls, quotes, and
          jobs that slip through the cracks. <span className="text-white/40">(Illustrative sample data.)</span>
        </p>

        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {leaks.map((leak) => (
            <article
              key={leak.title}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
            >
              <h3 className="text-sm font-medium text-white/80">{leak.title}</h3>
              <p className="mt-2 text-3xl font-bold tracking-tight text-violet-300">{leak.amount}</p>
              <p className="mt-2 text-sm leading-relaxed text-white/60">{leak.detail}</p>
            </article>
          ))}
        </div>

        <div className="mt-8 flex flex-wrap items-center gap-4">
          <Link
            to="/sign-up"
            className="rounded-xl bg-gradient-to-r from-violet-500 to-fuchsia-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-violet-900/40 transition-transform hover:scale-[1.02] focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 active:scale-[0.99]"
          >
            Find my leaks
          </Link>
          <span className="text-xs uppercase tracking-[0.18em] text-white/40">Sample data</span>
        </div>
      </div>
    </section>
  )
}
