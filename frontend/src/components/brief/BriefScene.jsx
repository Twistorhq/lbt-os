import { Reveal } from '../motion'
import { fmtMoney } from './useCountUp'

/**
 * TW-207: human-readable detector labels. Eyebrows never show raw slugs —
 * a brand rule from Imani's TW-207 review.
 */
export const DETECTOR_LABELS = {
  'equipment-age-graveyard': 'Equipment age',
  'plan-churn-risk': 'Plan churn',
  'quote-resurrection': 'Quote follow-up',
}

export function detectorLabel(slug) {
  if (DETECTOR_LABELS[slug]) return DETECTOR_LABELS[slug]
  return String(slug || '')
    .split('-')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/**
 * Severity sets the visual temperature — as edge/badge accents ONLY
 * (Imani's review: surfaces stay near-black/ink).
 */
export const SEVERITY_EDGE = {
  urgent: 'border-l-rose-400/70',
  watch: 'border-l-amber-400/70',
  info: 'border-l-violet-400/70',
}

export const SEVERITY_BADGE = {
  urgent: 'bg-rose-400/10 text-rose-200 border-rose-300/30',
  watch: 'bg-amber-400/10 text-amber-200 border-amber-300/30',
  info: 'bg-violet-400/10 text-violet-200 border-violet-300/30',
}

/**
 * TW-207: one finding, one scene. Eyebrow -> headline -> subcopy -> money
 * line -> the fix. Staggered entrance via Reveal.
 */
export default function BriefScene({ finding, index = 0 }) {
  const edge = SEVERITY_EDGE[finding.severity] || SEVERITY_EDGE.info
  const badge = SEVERITY_BADGE[finding.severity] || SEVERITY_BADGE.info
  const value = Number(finding.estimated_value)

  return (
    <Reveal delay={Math.min(index, 6) * 90}>
      <article
        className={`rounded-2xl border border-white/10 border-l-4 bg-[#0d0d17] p-6 ${edge}`}
        aria-label={finding.title}
      >
        <div className="flex flex-wrap items-center gap-3">
          <span className="tt-kicker text-white/60">
            {detectorLabel(finding.detector)}
          </span>
          {finding.is_demo && (
            <span className="tt-sample-badge">SAMPLE DATA</span>
          )}
          {finding.severity === 'urgent' && (
            <span
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${badge}`}
            >
              Needs you today
            </span>
          )}
        </div>

        <h3 className="mt-3 text-xl font-semibold tracking-tight text-white sm:text-2xl">
          {finding.title}
        </h3>

        {finding.detail && (
          <p className="mt-2 max-w-2xl leading-relaxed text-white/60">
            {finding.detail}
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-baseline gap-x-8 gap-y-2">
          {finding.count != null && (
            <div className="text-sm text-white/45">
              <span className="text-2xl font-bold tabular-nums text-white">
                {finding.count}
              </span>{' '}
              affected
            </div>
          )}
          {!!value && (
            <div className="text-sm text-white/45">
              <span className="tt-gradient-word text-2xl font-bold tabular-nums">
                {fmtMoney(value)}
              </span>{' '}
              on the table
            </div>
          )}
        </div>

        {finding.recommended_action && (
          <div className="mt-5 rounded-xl border border-violet-300/20 bg-violet-400/5 px-4 py-3">
            <span className="text-xs font-bold uppercase tracking-[0.18em] text-violet-200">
              The move:{' '}
            </span>
            <span className="text-sm leading-relaxed text-white/75">
              {finding.recommended_action}
            </span>
          </div>
        )}
      </article>
    </Reveal>
  )
}
