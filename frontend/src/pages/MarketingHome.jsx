import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '@clerk/clerk-react'
import { Ambient, DepthStage, DriftImage, Parallax, Reveal, ScrollScale } from '../components/motion'
import {
  ArrowRight,
  Check,
  Gauge,
  Lightning,
  ListChecks,
  Plug,
  Shield,
  Sparkle,
  TwistorMark,
} from '../components/icons'
import { trackVisitorEvent } from '../lib/analytics'

/* TW-159: rebuilt marketing homepage — cinematic scroll experience.
 * Motion: single rAF-throttled scroll bus (motion.jsx), transform/opacity only,
 * full prefers-reduced-motion support. Copy is honest: sample data is labeled,
 * no fabricated testimonials, no fake metrics, roadmap items marked as such.
 */

const TRADES = {
  hvac: {
    key: 'hvac',
    label: 'HVAC',
    name: 'Copperline Heating & Air',
    meta: 'Denver, CO · 3 crews',
    health: 72,
    concern: 'Follow-up speed is leaking warm demand before quotes turn into closed work.',
    opportunity: 'Maintenance plans are the highest-margin revenue and deserve more investment.',
    week: 'Clear the overdue lead queue and inspect materials spend before margin slips.',
  },
  plumbing: {
    key: 'plumbing',
    label: 'Plumbing',
    name: 'Blue Torch Plumbing Co.',
    meta: 'Aurora, CO · 2 crews',
    health: 68,
    concern: 'Emergency calls book same-day, but scheduled quotes sit unsent for a week.',
    opportunity: 'Water-heater replacements close at twice the margin of drain calls.',
    week: 'Follow up on 9 unsent water-heater quotes before the weekend.',
  },
  electrical: {
    key: 'electrical',
    label: 'Electrical',
    name: 'Amp & Anchor Electric',
    meta: 'Lakewood, CO · 4 crews',
    health: 81,
    concern: 'Panel upgrades stall waiting on permit paperwork.',
    opportunity: 'EV charger installs are the fastest-growing request this quarter.',
    week: 'Call back the 6 EV-charger inquiries from last week.',
  },
}

const ROADMAP_TRADES = ['Plumbing', 'Electrical', 'Roofing', 'Pest Control', 'Landscaping', 'Appliance Repair', 'Garage Doors']

const STEPS = [
  {
    n: '01',
    icon: Plug,
    title: 'Connect your tools',
    detail: 'QuickBooks, HubSpot, Stripe, or a simple CSV. Your invoices, contacts, and deals land in one stream instead of scattered apps.',
  },
  {
    n: '02',
    icon: Gauge,
    title: 'Get your health score',
    detail: 'The platform reads the whole picture and hands you a plain-English brief: biggest concern, best opportunity, and what to do this week.',
  },
  {
    n: '03',
    icon: ListChecks,
    title: 'Work the list',
    detail: 'Follow-ups, stalled quotes, margin leaks. Every morning starts with the three moves that put money back in the business.',
  },
]

const TIERS = [
  {
    key: 'basic',
    name: 'Starter',
    price: '$49',
    badge: null,
    description: 'One operating dashboard that replaces scattered spreadsheets.',
    features: ['Lead and sales pipeline', 'Customer management', 'Revenue and expense tracking', 'Real-time profit dashboard', 'Core metrics and reports'],
    cta: 'Start Starter',
    featured: false,
  },
  {
    key: 'pro',
    name: 'Growth',
    price: '$129',
    badge: 'Most popular',
    description: 'For shops that want AI watching the numbers every week.',
    features: ['Everything in Starter', 'Recurring AI revenue audits', 'Expense management', 'QuickBooks and HubSpot integrations', 'Up to 5 team members', 'PDF audit exports'],
    cta: 'Start Growth',
    featured: true,
  },
  {
    key: 'premium',
    name: 'Scale',
    price: '$299',
    badge: 'White-glove',
    description: 'Multi-location operators and agencies that need deeper support.',
    features: ['Everything in Growth', 'Unlimited team members', 'White-label audit reports', 'API access', 'Custom playbooks', 'Priority support'],
    cta: 'Start Scale',
    featured: false,
  },
]

const FAQS = [
  {
    q: 'How long does setup take?',
    a: 'Most owners connect their tools and see their first health score within a day. Pick your trade, authorize QuickBooks or HubSpot, and the platform handles the rest.',
  },
  {
    q: 'Is my financial data secure?',
    a: 'Your credentials are encrypted and tokens never leave our servers. Every query is scoped to your shop alone — your numbers stay yours.',
  },
  {
    q: "What if I don't use QuickBooks or HubSpot?",
    a: 'Import a simple CSV for leads, customers, sales, and expenses. More native integrations are on the roadmap.',
  },
  {
    q: 'Can I cancel anytime?',
    a: 'Yes. Plans are month to month. Cancel from the Billing page and you keep access until the end of the billing period.',
  },
  {
    q: "What's the difference between Starter and Growth?",
    a: 'Starter is the operating dashboard: pipeline, customers, revenue, expenses. Growth adds the AI revenue audit — recurring scans that find the money walking out your door — plus PDF exports and team seats.',
  },
]

const FOOTER_NAV = {
  Product: [
    { label: 'How it works', href: '#how-it-works' },
    { label: 'Live preview', href: '#live-preview' },
    { label: 'Pricing', href: '#pricing' },
    { label: 'FAQ', href: '#faq' },
  ],
  Company: [
    { label: 'Pilot program', href: '#pilot' },
    { label: 'Live preview', href: '#live-preview' },
  ],
  Account: [
    { label: 'Sign in', href: '/sign-in' },
    { label: 'Get started', href: '/sign-up' },
    { label: 'Billing', href: '/app/billing' },
  ],
}

function Logo({ dark = true }) {
  return (
    <Link to="/" className="flex items-center gap-2.5" aria-label="Twistor Trades home">
      <TwistorMark className="h-8 w-8" />
      <span className={`text-lg tracking-tight ${dark ? 'text-white' : 'text-slate-950'}`}>
        <span className="font-bold">Twistor</span> <span className="font-medium opacity-70">Trades</span>
      </span>
    </Link>
  )
}

function Header({ onCta, ctaUrl }) {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 18)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [menuOpen])

  const links = [
    { label: 'How it works', href: '#how-it-works' },
    { label: 'Live preview', href: '#live-preview' },
    { label: 'Pricing', href: '#pricing' },
    { label: 'FAQ', href: '#faq' },
  ]

  return (
    <header
      className={`sticky top-0 z-50 w-full transition-all duration-300 ${
        scrolled || menuOpen
          ? 'border-b border-white/10 bg-sofrito-950/85 shadow-[0_12px_40px_-18px_rgba(0,0,0,0.7)] backdrop-blur-xl'
          : 'border-b border-transparent bg-transparent'
      }`}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 xl:px-8">
        <Logo />
        <nav className="hidden items-center gap-8 text-sm font-medium text-white/65 md:flex" aria-label="Primary">
          {links.map((l) => (
            <a key={l.href} href={l.href} className="transition-colors hover:text-white">
              {l.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <Link to="/sign-in" className="hidden text-sm font-medium text-white/70 transition-colors hover:text-white sm:inline">
            Sign in
          </Link>
          <Link
            to={ctaUrl}
            onClick={() => onCta('header_get_started', ctaUrl)}
            className="inline-flex items-center gap-2 rounded-full bg-gold-400 px-5 py-2.5 text-sm font-bold text-sofrito-950 shadow-[0_10px_30px_-10px_rgba(245,185,66,0.6)] transition-transform duration-200 hover:scale-[1.03] active:scale-[0.98]"
          >
            Get started <ArrowRight className="h-4 w-4" />
          </Link>
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/15 text-white/80 md:hidden"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              {menuOpen ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
            </svg>
          </button>
        </div>
      </div>
      {menuOpen && (
        <nav id="mobile-nav" className="border-t border-white/10 px-6 py-4 md:hidden" aria-label="Mobile">
          <ul className="space-y-1">
            {links.map((l) => (
              <li key={l.href}>
                <a
                  href={l.href}
                  onClick={() => setMenuOpen(false)}
                  className="block rounded-xl px-3 py-3 text-base font-medium text-white/75 transition-colors hover:bg-white/5 hover:text-white"
                >
                  {l.label}
                </a>
              </li>
            ))}
            <li>
              <Link
                to="/sign-in"
                onClick={() => setMenuOpen(false)}
                className="block rounded-xl px-3 py-3 text-base font-medium text-white/75 transition-colors hover:bg-white/5 hover:text-white"
              >
                Sign in
              </Link>
            </li>
          </ul>
        </nav>
      )}
    </header>
  )
}

function Hero({ onCta, ctaUrl }) {
  return (
    <section className="relative overflow-hidden" aria-labelledby="hero-heading">
      <Ambient speed={0.05} className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute -top-40 left-1/4 h-[36rem] w-[36rem] rounded-full bg-indigo-600/25 blur-[140px]" />
        <div className="absolute bottom-0 right-0 h-[28rem] w-[28rem] rounded-full bg-gold-500/10 blur-[120px]" />
      </Ambient>
      <div className="relative mx-auto grid max-w-7xl items-center gap-14 px-6 pb-24 pt-16 sm:pt-20 lg:grid-cols-2 xl:px-8">
        <Parallax mode="copy">
          <div className="inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-white/15 bg-white/5 px-4 py-2 text-gold-300">
            <span className="h-1.5 w-1.5 rounded-full bg-gold-400" aria-hidden="true" />
            <span className="tt-kicker">Built for home-service businesses</span>
          </div>
          <h1 id="hero-heading" className="mt-6 max-w-2xl text-5xl font-bold leading-[1.02] tracking-tight sm:text-6xl xl:text-7xl">
            The money walking out your door, <span className="tt-gradient-word">finally visible.</span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-white/65">
            Twistor Trades is the operating system for shops that move: leads, sales, customers, and cash in one dashboard that tells you what to do next. No spreadsheets. No guessing.
          </p>
          <div className="mt-9 flex flex-wrap gap-4">
            <Link
              to={ctaUrl}
              onClick={() => onCta('hero_start_free', ctaUrl)}
              className="inline-flex items-center gap-2 rounded-full bg-gold-400 px-7 py-3.5 text-base font-bold text-sofrito-950 shadow-[0_16px_44px_-12px_rgba(245,185,66,0.65)] transition-transform duration-200 hover:scale-[1.04] active:scale-[0.98]"
            >
              Start free <ArrowRight className="h-5 w-5" />
            </Link>
            <a
              href="#how-it-works"
              onClick={() => onCta('hero_how_it_works', '#how-it-works')}
              className="inline-flex items-center rounded-full border border-white/20 px-7 py-3.5 text-base font-semibold text-white/85 transition-colors hover:border-white/45 hover:text-white"
            >
              See how it works
            </a>
          </div>
          <div className="mt-10 flex items-center gap-4">
            <div className="flex -space-x-2.5" aria-hidden="true">
              {['CT', 'BA', 'AA'].map((t) => (
                <span key={t} className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-sofrito-950 bg-sofrito-700 text-[10px] font-bold text-white/80">
                  {t}
                </span>
              ))}
            </div>
            <p className="text-sm text-white/55">
              Now welcoming founding pilot shops in{' '}
              <span className="font-semibold text-white/85">HVAC, plumbing, and electrical</span>
            </p>
          </div>
        </Parallax>

        <Parallax mode="visual" className="relative">
          <div className="relative overflow-hidden rounded-[2rem] border border-white/10 shadow-[0_50px_120px_-40px_rgba(0,0,0,0.8)]">
            <DriftImage
              src="/img/hvac-tech.jpg"
              alt="HVAC technician installing a compressor on a heating and cooling unit"
              className="aspect-[4/4.4] sm:aspect-[4/3.4]"
              drift={0.08}
            />
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-sofrito-950/70 via-transparent to-transparent" aria-hidden="true" />
            <div className="absolute left-5 top-5 max-w-[13rem] rounded-2xl border border-white/15 bg-sofrito-950/70 p-4 backdrop-blur-md">
              <div className="tt-kicker text-white/50">Shop health</div>
              <div className="mt-1 text-3xl font-bold tracking-tight">
                72<span className="text-base font-semibold text-white/45">/100</span>
              </div>
              <div className="mt-2 h-1.5 w-36 overflow-hidden rounded-full bg-white/15" role="img" aria-label="Shop health score 72 out of 100">
                <div className="h-full w-[72%] rounded-full bg-gradient-to-r from-indigo-400 to-gold-400" />
              </div>
            </div>
            <div className="absolute right-5 top-24 max-w-[15rem] rounded-2xl border border-white/15 bg-sofrito-950/70 p-4 backdrop-blur-md sm:top-28">
              <div className="flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gold-400/20 text-gold-300">
                  <Lightning className="h-4 w-4" />
                </span>
                <span className="tt-kicker text-gold-300">Money leak found</span>
              </div>
              <p className="mt-2.5 text-[13px] leading-6 text-white/75">
                14 open estimates are past 7 days. Close half and that is a new truck.
              </p>
            </div>
            <div className="absolute inset-x-5 bottom-5 flex items-end justify-between gap-4">
              <div>
                <div className="tt-kicker text-white/55">This week</div>
                <div className="mt-1 text-xl font-bold tracking-tight">Follow-ups first. Always.</div>
              </div>
              <span className="tt-sample-badge shrink-0">Sample data</span>
            </div>
          </div>
        </Parallax>
      </div>
    </section>
  )
}

function RoadmapMarquee() {
  return (
    <div className="marquee relative overflow-hidden border-y border-white/10 bg-sofrito-950 py-5" aria-label="Trades on our roadmap">
      <div className="marquee-track" aria-hidden="true">
        {[0, 1].map((copy) => (
          <div key={copy} className="flex shrink-0 items-center">
            <span className="flex items-center">
              <span className="px-8 text-[13px] font-bold uppercase tracking-[0.3em] text-gold-400">On the roadmap</span>
              <span className="h-1 w-1 rounded-full bg-indigo-400/60" aria-hidden="true" />
            </span>
            {ROADMAP_TRADES.map((t) => (
              <span key={t} className="flex items-center">
                <span className="px-8 text-[13px] font-semibold uppercase tracking-[0.3em] text-white/45">{t}</span>
                <span className="h-1 w-1 rounded-full bg-white/20" aria-hidden="true" />
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function HowItWorks() {
  return (
    <section id="how-it-works" className="relative bg-sofrito-950 py-24 sm:py-32" aria-labelledby="hiw-heading">
      <div className="mx-auto max-w-7xl px-6 xl:px-8">
        <Reveal>
          <div className="tt-kicker text-gold-400">How it works</div>
          <h2 id="hiw-heading" className="tt-h2 mt-4 max-w-2xl text-4xl sm:text-5xl">
            From scattered tools to one clear morning brief.
          </h2>
          <p className="mt-5 max-w-xl text-base leading-7 text-white/60">
            Three steps. No consultants, no six week onboarding, no new habits to learn.
          </p>
        </Reveal>
        <DepthStage className="mt-14">
          <div className="grid gap-5 md:grid-cols-3">
            {STEPS.map((s, i) => (
              <Reveal key={s.n} delay={i * 130} className="h-full">
                <div data-depth={i === 1 ? 70 : -30} className="depth-layer tt-card-dark h-full p-8">
                  <div className="flex items-start justify-between">
                    <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-500/15 text-indigo-300">
                      <s.icon className="h-6 w-6" />
                    </span>
                    <span className="text-sm font-semibold tracking-[0.2em] text-white/25">{s.n}</span>
                  </div>
                  <h3 className="mt-6 text-xl font-bold tracking-tight">{s.title}</h3>
                  <p className="mt-3 text-[15px] leading-7 text-white/60">{s.detail}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </DepthStage>
      </div>
    </section>
  )
}

function LivePreview({ onCta, ctaUrl }) {
  const [searchParams] = useSearchParams()
  const [tradeKey, setTradeKey] = useState(() => {
    const q = searchParams.get('demo')
    return TRADES[q] ? q : 'hvac'
  })
  const trade = TRADES[tradeKey]

  const select = (key) => {
    setTradeKey(key)
    trackVisitorEvent('demo_trade_select', { page: 'marketing_home', trade: key })
  }

  const briefCards = [
    { icon: Lightning, tint: 'bg-gold-400/15 text-gold-600', label: 'Biggest concern', text: trade.concern },
    { icon: Sparkle, tint: 'bg-indigo-500/10 text-indigo-600', label: 'Best opportunity', text: trade.opportunity },
    { icon: Check, tint: 'bg-emerald-500/10 text-emerald-600', label: 'This week', text: trade.week },
  ]

  return (
    <section id="live-preview" className="bg-cream py-24 text-slate-950 sm:py-32" aria-labelledby="preview-heading">
      <div className="mx-auto max-w-7xl px-6 xl:px-8">
        <Reveal>
          <div className="tt-kicker text-indigo-600">Live preview</div>
          <h2 id="preview-heading" className="tt-h2 mt-4 max-w-2xl text-4xl text-slate-950 sm:text-5xl">
            This is what Monday morning looks like.
          </h2>
          <p className="mt-5 max-w-xl text-base leading-7 text-slate-600">
            Pick a trade. This is a sample shop, but the brief format is exactly what lands in your inbox every week.
          </p>
        </Reveal>

        <Reveal delay={120} className="mt-8 flex flex-wrap items-center gap-3">
          <div role="group" aria-label="Choose a sample trade" className="flex flex-wrap gap-2.5">
            {Object.values(TRADES).map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => select(t.key)}
                aria-pressed={tradeKey === t.key}
                className={`rounded-full px-5 py-2.5 text-sm font-semibold transition-all duration-200 ${
                  tradeKey === t.key
                    ? 'bg-sofrito-950 text-white shadow-[0_10px_28px_-12px_rgba(10,10,24,0.6)]'
                    : 'border border-slate-300 bg-white text-slate-600 hover:border-slate-400 hover:text-slate-950'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <span className="tt-sample-badge on-light">Sample data</span>
        </Reveal>

        <ScrollScale from={0.93} fade className="mt-10">
          <div className="overflow-hidden rounded-[1.75rem] border border-slate-200/80 bg-white shadow-[0_40px_90px_-40px_rgba(10,10,24,0.25)]">
            <div className="grid lg:grid-cols-[0.9fr_1.1fr]">
              <div className="bg-sofrito-950 p-8 text-white sm:p-10">
                <div className="tt-kicker text-gold-400">Analyst brief</div>
                <h3 className="mt-3 text-3xl font-bold tracking-tight">{trade.name}</h3>
                <p className="mt-2 text-sm text-white/55">{trade.meta}</p>
                <div className="mt-8 flex items-end justify-between gap-6">
                  <div>
                    <div className="tt-kicker text-white/45">Shop health</div>
                    <div
                      className="mt-3 h-2 w-48 overflow-hidden rounded-full bg-white/10 sm:w-56"
                      role="img"
                      aria-label={`Shop health score ${trade.health} out of 100`}
                    >
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-indigo-400 via-indigo-300 to-gold-400 transition-all duration-700"
                        style={{ width: `${trade.health}%` }}
                      />
                    </div>
                  </div>
                  <div className="text-6xl font-bold tracking-tight">{trade.health}</div>
                </div>
                <div className="mt-8 flex gap-3 rounded-2xl border border-white/10 bg-white/5 p-4">
                  <Shield className="h-5 w-5 shrink-0 text-indigo-300" />
                  <p className="text-[13px] leading-6 text-white/60">
                    Your numbers stay yours. Every query is scoped to your shop alone.
                  </p>
                </div>
              </div>
              <div className="space-y-4 p-8 sm:p-10">
                {briefCards.map((c, i) => (
                  <Reveal key={c.label} delay={i * 110} y={22}>
                    <div className="flex gap-4 rounded-2xl border border-slate-200/70 bg-cream p-5">
                      <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${c.tint}`}>
                        <c.icon className="h-5 w-5" />
                      </span>
                      <div>
                        <div className="tt-kicker text-slate-400">{c.label}</div>
                        <p className="mt-2 text-[15px] leading-7 text-slate-700">{c.text}</p>
                      </div>
                    </div>
                  </Reveal>
                ))}
                <Reveal delay={360} y={22}>
                  <Link
                    to={ctaUrl}
                    onClick={() => onCta('preview_get_brief', ctaUrl)}
                    className="inline-flex items-center gap-2 rounded-full bg-sofrito-950 px-6 py-3 text-sm font-bold text-white transition-transform duration-200 hover:scale-[1.03] active:scale-[0.98]"
                  >
                    Get your brief <ArrowRight className="h-4 w-4" />
                  </Link>
                </Reveal>
              </div>
            </div>
          </div>
        </ScrollScale>
      </div>
    </section>
  )
}

function Pricing({ onCta, ctaUrl }) {
  return (
    <section id="pricing" className="relative overflow-hidden bg-sofrito-950 py-24 sm:py-32" aria-labelledby="pricing-heading">
      <Ambient speed={0.06} className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute left-1/2 top-0 h-[30rem] w-[46rem] -translate-x-1/2 rounded-full bg-indigo-600/15 blur-[130px]" />
      </Ambient>
      <div className="relative mx-auto max-w-7xl px-6 xl:px-8">
        <Reveal className="text-center">
          <div className="tt-kicker text-gold-400">Pricing</div>
          <h2 id="pricing-heading" className="tt-h2 mx-auto mt-4 max-w-2xl text-4xl sm:text-5xl">
            Pays for itself with one saved job.
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-white/60">
            Month to month. No setup fees. No contracts that outlive your truck.
          </p>
        </Reveal>
        <div className="mt-14 grid gap-6 lg:grid-cols-3">
          {TIERS.map((t, i) => (
            <Reveal key={t.key} delay={i * 130} className="h-full">
              <ScrollScale from={0.95} className="h-full">
                <div
                  className={`relative flex h-full flex-col rounded-[1.75rem] border p-8 transition-transform duration-300 hover:-translate-y-1.5 ${
                    t.featured
                      ? 'border-indigo-400/40 bg-gradient-to-b from-indigo-600/25 to-sofrito-900 shadow-[0_36px_90px_-30px_rgba(99,102,241,0.55)]'
                      : 'tt-card-dark'
                  }`}
                >
                  {t.badge && (
                    <span
                      className={`absolute -top-3.5 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full px-4 py-1.5 text-[11px] font-bold uppercase tracking-[0.16em] ${
                        t.featured ? 'bg-indigo-400 text-sofrito-950' : 'bg-gold-400/15 text-gold-300 ring-1 ring-gold-400/40'
                      }`}
                    >
                      {t.badge}
                    </span>
                  )}
                  <h3 className="text-lg font-bold tracking-tight">{t.name}</h3>
                  <p className="mt-2 min-h-[3.5rem] text-sm leading-6 text-white/55">{t.description}</p>
                  <p className="mt-4 flex items-baseline gap-2">
                    <span className="text-5xl font-bold tracking-tight">{t.price}</span>
                    <span className="text-sm font-medium text-white/45">/month</span>
                  </p>
                  <ul className="mt-7 flex-1 space-y-3.5">
                    {t.features.map((f) => (
                      <li key={f} className="flex items-start gap-3 text-[15px] text-white/75">
                        <Check className="mt-0.5 h-5 w-5 shrink-0 text-gold-400" />
                        {f}
                      </li>
                    ))}
                  </ul>
                  <Link
                    to={ctaUrl}
                    onClick={() => onCta(`pricing_${t.key}`, ctaUrl)}
                    className={`mt-8 inline-flex items-center justify-center gap-2 rounded-full px-6 py-3.5 text-base font-bold transition-transform duration-200 hover:scale-[1.03] active:scale-[0.98] ${
                      t.featured
                        ? 'bg-gold-400 text-sofrito-950 shadow-[0_16px_44px_-12px_rgba(245,185,66,0.65)]'
                        : 'border border-white/20 text-white hover:border-white/45'
                    }`}
                  >
                    {t.cta} <ArrowRight className="h-5 w-5" />
                  </Link>
                </div>
              </ScrollScale>
            </Reveal>
          ))}
        </div>
        <Reveal delay={200}>
          <p className="mt-10 text-center text-sm text-white/40">Prices in USD. Cancel anytime from the Billing page.</p>
        </Reveal>
      </div>
    </section>
  )
}

function Pilot({ onCta, ctaUrl }) {
  return (
    <section id="pilot" className="bg-cream py-24 text-slate-950 sm:py-32" aria-labelledby="pilot-heading">
      <div className="mx-auto max-w-7xl px-6 xl:px-8">
        <ScrollScale from={0.94} fade>
          <div className="grid overflow-hidden rounded-[2rem] border border-slate-200/70 bg-white shadow-[0_40px_90px_-40px_rgba(10,10,24,0.25)] lg:grid-cols-2">
            <div className="p-10 sm:p-14">
              <Reveal>
                <div className="tt-kicker text-indigo-600">Pilot program</div>
                <h2 id="pilot-heading" className="tt-h2 mt-4 text-4xl text-slate-950 sm:text-5xl">
                  Three shops. Thirty days. White-glove setup.
                </h2>
                <p className="mt-6 max-w-md text-base leading-8 text-slate-600">
                  We are onboarding a small founding group of HVAC, plumbing, and electrical shops. We connect your tools with you, tune the brief to your trade, and stay on the line until it pays.
                </p>
                <div className="mt-9">
                  <Link
                    to={ctaUrl}
                    onClick={() => onCta('pilot_claim', ctaUrl)}
                    className="inline-flex items-center gap-2 rounded-full bg-sofrito-950 px-7 py-3.5 text-base font-bold text-white transition-transform duration-200 hover:scale-[1.03] active:scale-[0.98]"
                  >
                    Claim a pilot spot <ArrowRight className="h-5 w-5" />
                  </Link>
                </div>
                <div className="mt-7 flex flex-wrap gap-x-7 gap-y-2">
                  {['No credit card', 'Done with you'].map((t) => (
                    <span key={t} className="inline-flex items-center gap-2 text-sm font-medium text-slate-500">
                      <Check className="h-4 w-4 text-indigo-600" /> {t}
                    </span>
                  ))}
                </div>
              </Reveal>
            </div>
            <DriftImage
              src="/img/hvac-team.jpg"
              alt="Two heating and cooling technicians soldering valves on a unit together"
              className="min-h-[20rem] lg:min-h-full"
              drift={0.1}
            />
          </div>
        </ScrollScale>
      </div>
    </section>
  )
}

function Faq() {
  const [open, setOpen] = useState(0)
  return (
    <section id="faq" className="bg-cream pb-24 text-slate-950 sm:pb-32" aria-labelledby="faq-heading">
      <div className="mx-auto max-w-3xl px-6">
        <Reveal>
          <div className="tt-kicker text-indigo-600">FAQ</div>
          <h2 id="faq-heading" className="tt-h2 mt-4 text-4xl text-slate-950 sm:text-5xl">
            Straight answers.
          </h2>
        </Reveal>
        <div className="mt-10 space-y-3.5">
          {FAQS.map((f, i) => {
            const isOpen = open === i
            return (
              <Reveal key={f.q} delay={i * 70} y={18}>
                <div className="overflow-hidden rounded-3xl border border-slate-200/80 bg-white shadow-[0_18px_44px_-28px_rgba(10,10,24,0.18)]">
                  <button
                    type="button"
                    onClick={() => setOpen(isOpen ? -1 : i)}
                    aria-expanded={isOpen}
                    aria-controls={`faq-panel-${i}`}
                    id={`faq-button-${i}`}
                    className="flex w-full items-center justify-between gap-4 px-7 py-5 text-left"
                  >
                    <span className="text-base font-bold tracking-tight">{f.q}</span>
                    <span
                      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 text-slate-500 transition-transform duration-300 ${isOpen ? 'rotate-45' : ''}`}
                      aria-hidden="true"
                    >
                      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <path d="M12 5v14M5 12h14" />
                      </svg>
                    </span>
                  </button>
                  <div
                    id={`faq-panel-${i}`}
                    role="region"
                    aria-labelledby={`faq-button-${i}`}
                    className={`grid transition-all duration-300 ${isOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}
                  >
                    <div className="overflow-hidden">
                      <p className="px-7 pb-7 text-[15px] leading-7 text-slate-600">{f.a}</p>
                    </div>
                  </div>
                </div>
              </Reveal>
            )
          })}
        </div>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="bg-sofrito-950 pb-10 pt-16" aria-label="Footer">
      <div className="mx-auto max-w-7xl px-6 xl:px-8">
        <div className="grid gap-12 md:grid-cols-[1.4fr_repeat(3,1fr)]">
          <div>
            <Logo />
            <p className="mt-5 max-w-xs text-[15px] leading-7 text-white/55">
              The operating system for home-service businesses. Intelligence, engineered.
            </p>
            <div className="mt-6 h-1 w-24 rounded-full bg-gradient-to-r from-indigo-400 to-gold-400" aria-hidden="true" />
          </div>
          {Object.entries(FOOTER_NAV).map(([group, items]) => (
            <nav aria-label={group} key={group}>
              <div className="tt-kicker text-white/35">{group}</div>
              <ul className="mt-5 space-y-3.5">
                {items.map((item) => (
                  <li key={item.label}>
                    {item.href.startsWith('/') ? (
                      <Link to={item.href} className="text-[15px] text-white/60 transition-colors hover:text-white">
                        {item.label}
                      </Link>
                    ) : (
                      <a href={item.href} className="text-[15px] text-white/60 transition-colors hover:text-white">
                        {item.label}
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>
        <div className="mt-14 flex flex-col gap-3 border-t border-white/10 pt-8 text-xs text-white/35 sm:flex-row sm:items-center sm:justify-between">
          <p>Prepared by Twistor Holdings LLC</p>
          <p>Photography: U.S. Air Force (public domain) via DVIDS (3241939, 3241944)</p>
        </div>
      </div>
    </footer>
  )
}

export default function MarketingHome() {
  const { isSignedIn } = useAuth()
  const ctaUrl = isSignedIn ? '/app' : '/sign-up'

  useEffect(() => {
    trackVisitorEvent('page_view', { page: 'marketing_home', signed_in: !!isSignedIn })
  }, [isSignedIn])

  const trackCta = (cta, destination) => {
    trackVisitorEvent('cta_click', { page: 'marketing_home', cta, destination, signed_in: !!isSignedIn })
  }

  return (
    <div className="min-h-screen bg-sofrito-950 font-sans text-white antialiased">
      <Header onCta={trackCta} ctaUrl={ctaUrl} />
      <main>
        <Hero onCta={trackCta} ctaUrl={ctaUrl} />
        <RoadmapMarquee />
        <HowItWorks />
        <LivePreview onCta={trackCta} ctaUrl={ctaUrl} />
        <Pricing onCta={trackCta} ctaUrl={ctaUrl} />
        <Pilot onCta={trackCta} ctaUrl={ctaUrl} />
        <Faq />
      </main>
      <Footer />
    </div>
  )
}
