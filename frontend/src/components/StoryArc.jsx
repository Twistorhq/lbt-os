import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { usePrefersReducedMotion, useScrollEffect } from './motion'
import { stageStyles, bgStyles, clamp } from './storyArcMath'
import { ArrowRight, Check, Gauge, Lightning, Phone } from './icons'

/* TW-165: scroll-narrative story arc ("Bramble" pattern) built from the
 * visual-story brief (brand-assets/twistor-trades-visual-story-brief.md).
 * A sticky 3-stage cinematic: the leak → the catch → the after-state.
 *
 * Motion compounds on TW-159's single rAF scroll bus: transform/opacity only,
 * no layout thrash. Stage state is written imperatively to DOM refs (no
 * React state on the scroll path). Reduced motion renders all three stages
 * stacked statically — no sticky track, no scroll choreography.
 * Mobile (coarse pointers) re-choreographs: crossfade + gentle rise only,
 * full-bleed panels, compact progress rail, 44px+ targets.
 */

const STAGES = [
  {
    key: 'leak',
    label: 'The leak',
    index: '01',
    kicker: 'The problem',
    heading: 'Money walks out the door every day.',
    copy: 'A customer calls three shops and hires the first one that answers. The missed follow-up, the quote that sits unsent for a week — that is revenue leaving before the owner ever sees it.',
    tint: 'text-red-300',
  },
  {
    key: 'catch',
    label: 'The catch',
    index: '02',
    kicker: 'The platform',
    heading: 'Twistor catches what slips through.',
    copy: 'Twistor Trades watches the gaps: missed follow-ups, unsent quotes, quiet weeks — surfaced in one plain-English brief every Monday morning.',
    tint: 'text-indigo-300',
  },
  {
    key: 'after',
    label: 'The after-state',
    index: '03',
    kicker: 'The payoff',
    heading: 'Monday morning looks like this.',
    copy: 'Three moves that put money back in the business, before the first cup of coffee is done. Follow-ups first. Always.',
    tint: 'text-gold-300',
  },
]

/* Scroll-math lives in ./storyArcMath.js (pure, unit-tested). */

function LeakVisual() {
  return (
    <div className="relative mx-auto w-full max-w-sm" aria-label="Illustration of missed calls and an unsent estimate">
      <div className="rotate-[-2deg] rounded-3xl border border-white/15 bg-sofrito-950/80 p-5 shadow-[0_30px_70px_-30px_rgba(0,0,0,0.8)] backdrop-blur-md">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-red-500/15 text-red-300">
            <Phone className="h-5 w-5" />
          </span>
          <div>
            <div className="text-sm font-bold">Missed call</div>
            <div className="text-xs text-white/50">Yesterday, 7:42 PM</div>
          </div>
        </div>
        <p className="mt-3 text-[13px] leading-6 text-white/60">New customer. Never called back.</p>
      </div>
      <div className="mt-4 rotate-[1.5deg] rounded-3xl border border-white/15 bg-sofrito-950/80 p-5 shadow-[0_30px_70px_-30px_rgba(0,0,0,0.8)] backdrop-blur-md sm:ml-10">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-bold">Estimate #2481</div>
          <span className="rounded-full bg-red-500/15 px-3 py-1 text-[11px] font-bold uppercase tracking-[0.14em] text-red-300">
            Unsent · 9 days
          </span>
        </div>
        <p className="mt-3 text-[13px] leading-6 text-white/60">Quote sitting in drafts while the customer hires someone else.</p>
      </div>
      <span className="tt-sample-badge absolute -top-3 right-4">Sample data</span>
    </div>
  )
}

function CatchVisual() {
  return (
    <div className="relative mx-auto w-full max-w-sm" aria-label="Illustration of the Twistor Trades health score catching a leak">
      <div className="rounded-3xl border border-white/15 bg-sofrito-950/80 p-6 shadow-[0_30px_70px_-30px_rgba(0,0,0,0.8)] backdrop-blur-md">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-indigo-500/15 text-indigo-300">
            <Gauge className="h-5 w-5" />
          </span>
          <div>
            <div className="tt-kicker text-white/50">Shop health</div>
            <div className="text-2xl font-bold tracking-tight">
              72<span className="text-sm font-semibold text-white/45">/100</span>
            </div>
          </div>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/10" role="img" aria-label="Shop health score 72 out of 100">
          <div className="h-full w-[72%] rounded-full bg-gradient-to-r from-indigo-400 to-gold-400" />
        </div>
      </div>
      <div className="mt-4 rounded-3xl border border-gold-400/25 bg-sofrito-950/80 p-5 shadow-[0_30px_70px_-30px_rgba(0,0,0,0.8)] backdrop-blur-md sm:-ml-8">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gold-400/20 text-gold-300">
            <Lightning className="h-4 w-4" />
          </span>
          <span className="tt-kicker text-gold-300">Money leak found</span>
        </div>
        <p className="mt-2.5 text-[13px] leading-6 text-white/75">14 open estimates are past 7 days. The brief flags them Monday morning.</p>
      </div>
      <span className="tt-sample-badge absolute -top-3 right-4">Sample data</span>
    </div>
  )
}

function AfterVisual({ onCta, ctaUrl }) {
  const moves = [
    'Call back the 6 unanswered inquiries from last week.',
    'Clear the overdue lead queue before Friday.',
    'Inspect materials spend before margin slips.',
  ]
  return (
    <div className="relative mx-auto w-full max-w-sm" aria-label="Illustration of the Monday morning brief">
      <div className="rounded-3xl border border-white/15 bg-sofrito-950/80 p-6 shadow-[0_30px_70px_-30px_rgba(0,0,0,0.8)] backdrop-blur-md">
        <div className="tt-kicker text-gold-300">This week</div>
        <ul className="mt-4 space-y-3.5">
          {moves.map((m) => (
            <li key={m} className="flex items-start gap-3 text-[14px] leading-6 text-white/80">
              <Check className="mt-0.5 h-5 w-5 shrink-0 text-gold-400" />
              {m}
            </li>
          ))}
        </ul>
        <Link
          to={ctaUrl}
          onClick={() => onCta('storyarc_start_free', ctaUrl)}
          className="mt-6 inline-flex items-center gap-2 rounded-full bg-gold-400 px-6 py-3 text-sm font-bold text-sofrito-950 transition-transform duration-200 hover:scale-[1.03] active:scale-[0.98]"
        >
          Start free <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
      <span className="tt-sample-badge absolute -top-3 right-4">Sample data</span>
    </div>
  )
}

function StageContent({ stage, onCta, ctaUrl }) {
  return (
    <div className="mx-auto grid w-full max-w-6xl items-center gap-10 px-6 sm:gap-12 lg:grid-cols-2 lg:gap-16 xl:px-8">
      {/* Mobile: visual first, full-bleed. Desktop: copy left, visual right. */}
      <div className="order-first lg:order-last">
        {stage.key === 'leak' && <LeakVisual />}
        {stage.key === 'catch' && <CatchVisual />}
        {stage.key === 'after' && <AfterVisual onCta={onCta} ctaUrl={ctaUrl} />}
      </div>
      <div className="order-last text-center lg:order-first lg:text-left">
        <div className={`tt-kicker ${stage.tint}`}>{stage.kicker}</div>
        <h2 className="mt-4 text-4xl font-bold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">{stage.heading}</h2>
        <p className="mx-auto mt-5 max-w-md text-base leading-8 text-white/65 lg:mx-0 lg:text-lg">{stage.copy}</p>
        <div className="mt-6 text-sm font-bold tracking-[0.25em] text-white/30" aria-hidden="true">
          {stage.index} / 03
        </div>
      </div>
    </div>
  )
}

function StaticArc({ onCta, ctaUrl }) {
  return (
    <section className="bg-sofrito-950 py-24 sm:py-32" aria-label="How Twistor Trades works: the story">
      <div className="mx-auto max-w-6xl px-6 xl:px-8">
        <div className="tt-kicker text-gold-400">The story</div>
        <div className="mt-14 space-y-20">
          {STAGES.map((s) => (
            <article key={s.key} aria-label={s.label}>
              <StageContent stage={s} onCta={onCta} ctaUrl={ctaUrl} />
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}

export default function StoryArc({ onCta, ctaUrl }) {
  const reduced = usePrefersReducedMotion()

  if (reduced) return <StaticArc onCta={onCta} ctaUrl={ctaUrl} />

  return <AnimatedArc onCta={onCta} ctaUrl={ctaUrl} />
}

function AnimatedArc({ onCta, ctaUrl }) {
  const trackRef = useRef(null)
  const panelRefs = useRef([])
  const bgRefs = useRef([])
  const railRefs = useRef([])
  const lastActive = useRef(-1)
  // Mobile re-choreography: coarse pointers get crossfade + gentle rise only.
  const isCoarse = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches

  useScrollEffect(() => {
    const track = trackRef.current
    if (!track) return
    const vh = window.innerHeight
    const rect = track.getBoundingClientRect()
    const total = track.offsetHeight - vh
    const p = clamp(-rect.top / total, 0, 1)
    const stagePos = p * 3 // 0..3, stage i centered at i + 0.5

    panelRefs.current.forEach((el, i) => {
      if (!el) return
      const st = stageStyles(stagePos, i, isCoarse)
      el.style.opacity = st.opacity.toFixed(3)
      el.style.transform = st.transform
      el.style.pointerEvents = st.pointerEvents ? '' : 'none'
      // TW-165 keyboard-trap fix: an invisible panel must not be tabbable.
      // pointer-events:none does not remove elements from the tab order;
      // inert does (and also hides them from assistive tech).
      el.inert = !st.pointerEvents
    })

    bgRefs.current.forEach((el, i) => {
      if (!el) return
      el.style.opacity = bgStyles(stagePos, i).opacity.toFixed(3)
    })

    const active = clamp(Math.floor(stagePos), 0, 2)
    if (active !== lastActive.current) {
      lastActive.current = active
      panelRefs.current.forEach((el, i) => {
        if (el) el.setAttribute('aria-hidden', i === active ? 'false' : 'true')
      })
      railRefs.current.forEach((btn, i) => {
        if (!btn) return
        if (i === active) {
          btn.setAttribute('aria-current', 'step')
          btn.setAttribute('data-active', 'true')
        } else {
          btn.removeAttribute('aria-current')
          btn.setAttribute('data-active', 'false')
        }
      })
    }
  }, [])

  const jumpToStage = (i) => {
    const track = trackRef.current
    if (!track) return
    const vh = window.innerHeight
    const trackTop = track.getBoundingClientRect().top + window.scrollY
    const total = track.offsetHeight - vh
    const target = trackTop + (total * (i + 0.5)) / 3
    window.scrollTo({ top: target, behavior: 'smooth' })
  }

  return (
    <section
      ref={trackRef}
      className="relative bg-sofrito-950"
      style={{ height: '340vh' }}
      aria-label="How Twistor Trades works: the story"
    >
      <div className="sticky top-0 h-[100svh] overflow-hidden">
        {/* Background layers — crossfade between photographic worlds */}
        <div className="pointer-events-none absolute inset-0" aria-hidden="true">
          <div ref={(el) => (bgRefs.current[0] = el)} className="absolute inset-0" style={{ opacity: 0 }}>
            <img src={`${import.meta.env.BASE_URL}img/hvac-tech.jpg`} alt="" className="h-full w-full object-cover opacity-25" />
            <div className="absolute inset-0 bg-gradient-to-b from-sofrito-950/60 via-red-950/25 to-sofrito-950" />
          </div>
          <div ref={(el) => (bgRefs.current[1] = el)} className="absolute inset-0" style={{ opacity: 0 }}>
            <div className="absolute -left-32 top-1/4 h-[34rem] w-[34rem] rounded-full bg-indigo-600/30 blur-[140px]" />
            <div className="absolute -right-24 bottom-0 h-[26rem] w-[26rem] rounded-full bg-indigo-500/15 blur-[120px]" />
            <div className="absolute inset-0 bg-gradient-to-b from-sofrito-950/40 via-transparent to-sofrito-950" />
          </div>
          <div ref={(el) => (bgRefs.current[2] = el)} className="absolute inset-0" style={{ opacity: 0 }}>
            <img src={`${import.meta.env.BASE_URL}img/hvac-team.jpg`} alt="" className="h-full w-full object-cover opacity-20" />
            <div className="absolute inset-0 bg-gradient-to-b from-sofrito-950/60 via-gold-600/20 to-sofrito-950" />
          </div>
        </div>

        {/* Stage panels */}
        {STAGES.map((s, i) => (
          <div
            key={s.key}
            ref={(el) => {
              panelRefs.current[i] = el
              // Initial state matches the initial aria-hidden: only the
              // first panel is interactive before the first scroll tick.
              if (el) el.inert = i !== 0
            }}
            role="group"
            aria-label={s.label}
            aria-hidden={i === 0 ? 'false' : 'true'}
            className="absolute inset-0 flex items-center"
            style={{ opacity: i === 0 ? 1 : 0, willChange: 'transform, opacity' }}
          >
            <StageContent stage={s} onCta={onCta} ctaUrl={ctaUrl} />
          </div>
        ))}

        {/* Progress rail — keyboard-operable stage jumps */}
        <nav aria-label="Story stages" className="absolute inset-x-0 bottom-6 z-10 flex justify-center px-6">
          <div className="flex items-center gap-2 rounded-full border border-white/10 bg-sofrito-950/70 px-2.5 py-2 backdrop-blur-md sm:gap-3 sm:px-4">
            {STAGES.map((s, i) => (
              <button
                key={s.key}
                ref={(el) => (railRefs.current[i] = el)}
                type="button"
                onClick={() => jumpToStage(i)}
                aria-current={i === 0 ? 'step' : undefined}
                data-active={i === 0 ? 'true' : 'false'}
                className="story-rail-btn flex min-h-[44px] items-center gap-2 rounded-full px-3 text-[13px] font-semibold text-white/55 transition-colors hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gold-400 sm:px-4"
              >
                <span className="story-rail-index text-[11px] font-bold tracking-[0.2em] text-white/35">
                  {s.index}
                </span>
                <span className="hidden sm:inline">{s.label}</span>
              </button>
            ))}
          </div>
        </nav>
      </div>
    </section>
  )
}
