import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '@clerk/clerk-react'
import { trackVisitorEvent } from '../lib/analytics'
import LeakCalloutBand from '../components/LeakCalloutBand'

const proof = [
  'Connect QuickBooks and CRM tools in minutes',
  'Clean and map business data automatically',
  'Get a dashboard that tells owners what to do next',
]

const trustStrip = ['QuickBooks', 'HubSpot', 'Stripe', 'Supabase']

const platformStats = [
  { label: 'Industries supported', value: '12+' },
  { label: 'Data integrations', value: '3' },
  { label: 'Audit dimensions', value: '40+' },
  { label: 'Uptime SLA', value: '99.9%' },
]

const productMoments = [
  {
    title: 'Raw source data',
    detail: 'Invoices, purchases, contacts, and deals land in one stream instead of scattered tools.',
  },
  {
    title: 'Schema mapping',
    detail: 'Provider-specific fields get normalized into leads, customers, sales, and expenses.',
  },
  {
    title: 'Audit output',
    detail: 'The dashboard returns a health score, plain-English summary, risks, and next actions.',
  },
]

const analystCards = [
  {
    label: 'Biggest concern',
    value: 'Follow-up speed is leaking warm demand before quotes turn into closed work.',
  },
  {
    label: 'Best opportunity',
    value: 'Referral leads are producing the highest-margin revenue and deserve more investment.',
  },
  {
    label: 'This week',
    value: 'Clear the overdue lead queue and inspect materials spend before margin slips further.',
  },
]

const industryShowcase = [
  {
    key: 'hvac',
    title: 'HVAC',
    snapshot: 'Maintenance plans, emergency calls, and follow-up speed',
    services: ['AC Repair', 'Furnace Install', 'Tune-Up / Maintenance'],
    metrics: ['conversion rate', 'avg job value', 'repeat customer pct'],
  },
  {
    key: 'plumbing',
    title: 'Plumbing',
    snapshot: 'Emergency work, scheduled jobs, and margin discipline',
    services: ['Drain Cleaning', 'Water Heater Install', 'Leak Detection'],
    metrics: ['emergency vs scheduled', 'conversion rate', 'avg job value'],
  },
  {
    key: 'gig_worker',
    title: 'Gig Worker',
    snapshot: 'Take-home profit, service mix, and repeat client momentum',
    services: ['Ride Share', 'Food Delivery', 'Freelance Design'],
    metrics: ['net income', 'top service mix', 'expense ratio'],
  },
]

const testimonials = [
  {
    quote:
      "I connected QuickBooks on a Tuesday and had my first real health score by Wednesday morning. The follow-up recommendations alone made it worth it.",
    name: 'Marcus T.',
    role: 'Owner, HVAC company',
    initials: 'MT',
  },
  {
    quote:
      "We were using five spreadsheets and guessing at our margins. LBT OS replaced all of that with one clean dashboard that actually makes sense.",
    name: 'Priya N.',
    role: 'Founder, Plumbing & Heating',
    initials: 'PN',
  },
  {
    quote:
      "The Growth plan's recurring scans catch things I used to miss for weeks. It's like having a part-time analyst watching the business.",
    name: 'James O.',
    role: 'Solo contractor, gig & field work',
    initials: 'JO',
  },
]

const plans = [
  {
    key: 'basic',
    title: 'Starter',
    price: '$49',
    sub: '/ month',
    badge: null,
    description: 'One operating dashboard that replaces scattered spreadsheets.',
    bullets: [
      'Lead & sales pipeline',
      'Customer management',
      'Revenue & expense tracking',
      'Real-time profit dashboard',
      'Basic metrics & reports',
    ],
    cta: 'Start Starter',
    href: '/sign-up',
    highlight: false,
  },
  {
    key: 'pro',
    title: 'Growth',
    price: '$129',
    sub: '/ month',
    badge: 'Most popular',
    description: 'For teams that need AI-powered revenue intelligence.',
    bullets: [
      'Everything in Starter',
      'AI Revenue Audit recurring scans',
      'Expense management',
      'QuickBooks & HubSpot integrations',
      'Up to 5 team members',
      'PDF audit exports',
    ],
    cta: 'Start Growth',
    href: '/sign-up',
    highlight: true,
  },
  {
    key: 'premium',
    title: 'Scale',
    price: '$299',
    sub: '/ month',
    badge: 'White-glove',
    description: 'Multi-location operators and agencies that need deeper support.',
    bullets: [
      'Everything in Growth',
      'Unlimited team members',
      'White-label audit reports',
      'API access',
      'Custom playbooks',
      'Priority support',
    ],
    cta: 'Contact Sales',
    href: '/sign-up',
    highlight: false,
  },
  {
    key: 'enterprise',
    title: 'Enterprise + DOTs',
    price: 'Custom',
    sub: '',
    badge: 'Video layer',
    description: 'For companies that want LBT OS plus professional video editing and scheduled publishing.',
    bullets: [
      'Everything in Scale',
      'Import raw video files',
      'Industry-specific formal templates',
      'Short-form and long-form edit generation',
      'Review and publishing schedule workflow',
      'White-glove media ops support',
    ],
    cta: 'Talk DOTs',
    href: 'mailto:sales@lbt-os.com?subject=DOTs%20Enterprise%20for%20LBT%20OS',
    highlight: false,
  },
]

const faqs = [
  {
    q: 'How long does setup take?',
    a: 'Most owners are connected and audited within 10 minutes. You pick an industry template, authorize QuickBooks or HubSpot, and the platform handles the rest.',
  },
  {
    q: 'Is my financial data secure?',
    a: 'OAuth tokens never leave the server. Credentials are encrypted at rest, queries are org-scoped with RLS, and sync logs expose no raw secrets.',
  },
  {
    q: "What if I don't use QuickBooks or HubSpot?",
    a: 'You can import data via CSV for any module — leads, customers, sales, expenses. More native integrations are on the roadmap.',
  },
  {
    q: 'Can I cancel anytime?',
    a: 'Yes. Paid plans are month-to-month. Cancel from the Billing page and your access continues until the end of the billing period.',
  },
  {
    q: "What's the difference between Starter and Growth AI audits?",
    a: 'Starter gives you core dashboards and operating metrics. Growth adds recurring AI revenue audits, PDF exports, deeper analysis, and team access.',
  },
]

const footerLinks = {
  Product: [
    { label: 'How It Works', href: '#how-it-works' },
    { label: 'Pricing', href: '#pricing' },
    { label: 'Industry Templates', href: '#templates' },
    { label: 'Security', href: '#security' },
  ],
  Company: [
    { label: 'Sign In', href: '/sign-in' },
    { label: 'Get Started', href: '/sign-up' },
  ],
  Legal: [
    { label: 'Privacy Policy', href: '#' },
    { label: 'Terms of Service', href: '#' },
  ],
}

export default function MarketingHome() {
  const { isSignedIn } = useAuth()
  const primaryHref = isSignedIn ? '/app' : '/sign-up'
  const secondaryHref = isSignedIn ? '/app/connections' : '/sign-in'
  const [scrolled, setScrolled] = useState(false)
  const [openFaq, setOpenFaq] = useState(null)

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 18)
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])

  useEffect(() => {
    trackVisitorEvent('page_view', {
      page: 'marketing_home',
      signed_in: Boolean(isSignedIn),
    })
  }, [isSignedIn])

  const trackCta = (cta, destination) => {
    trackVisitorEvent('cta_click', {
      page: 'marketing_home',
      cta,
      destination,
      signed_in: Boolean(isSignedIn),
    })
  }

  return (
    <div className="min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,rgba(37,99,235,0.14),transparent_22%),radial-gradient(circle_at_bottom_right,rgba(15,23,42,0.08),transparent_25%),linear-gradient(180deg,#f8fafc_0%,#e8edf5_100%)] text-slate-950">
      <div className="absolute inset-x-0 top-0 h-[36rem] bg-[radial-gradient(circle_at_top_right,rgba(59,130,246,0.18),transparent_30%),radial-gradient(circle_at_left,rgba(255,255,255,0.6),transparent_35%)] pointer-events-none" />

      {/* Sticky header */}
      <header
        className={`sticky top-0 z-50 mx-auto w-full transition-all duration-300 ${
          scrolled
            ? 'border-b border-slate-200/70 bg-white/80 shadow-[0_8px_32px_-18px_rgba(15,23,42,0.22)] backdrop-blur-xl'
            : 'bg-transparent'
        }`}
      >
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5 xl:px-8">
          <div>
            <Link to="/" className="text-xl font-semibold tracking-tight text-slate-950">LBT OS</Link>
            <div className="mt-0.5 text-[11px] font-medium uppercase tracking-[0.22em] text-slate-500">Lean Business Tracker</div>
          </div>
          <nav className="hidden items-center gap-8 text-sm font-medium text-slate-600 md:flex">
            <a href="#how-it-works" className="transition-colors hover:text-slate-950">How It Works</a>
            <a href="#templates" className="transition-colors hover:text-slate-950">Templates</a>
            <a href="#pricing" className="transition-colors hover:text-slate-950">Pricing</a>
            <a href="#security" className="transition-colors hover:text-slate-950">Security</a>
          </nav>
          <div className="flex items-center gap-3">
            <Link to="/sign-in" className="btn-secondary hidden sm:inline-flex">Sign In</Link>
            <Link to={primaryHref} onClick={() => trackCta('header_get_my_business_audit', primaryHref)} className="btn-primary">Get My Business Audit</Link>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto max-w-7xl px-6 pb-24 pt-6 xl:px-8">

        {/* Hero */}
        <section className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
          <div className="animate-riseIn">
            <div className="inline-flex rounded-full border border-brand-100 bg-white/80 px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-brand-700 shadow-sm backdrop-blur">
              Business audit software
            </div>
            <h1 className="mt-6 max-w-3xl text-5xl font-semibold tracking-tight text-slate-950 sm:text-6xl xl:text-[4.35rem] xl:leading-[0.95]">
              Connect your business data. Get a clear audit in minutes.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-slate-600">
              LBT OS pulls in accounting and CRM data, cleans it, and turns it into one dashboard with a health score, key risks, and next steps.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to={primaryHref} onClick={() => trackCta('hero_get_my_business_audit', primaryHref)} className="btn-primary px-5 py-3 text-base">Get My Business Audit</Link>
              <Link to={secondaryHref} onClick={() => trackCta(isSignedIn ? 'hero_open_workspace' : 'hero_see_product', secondaryHref)} className="btn-secondary px-5 py-3 text-base">
                {isSignedIn ? 'Open Product Workspace' : 'See The Product'}
              </Link>
            </div>
            <div className="mt-8 space-y-3">
              {proof.map((item) => (
                <div key={item} className="flex items-start gap-3 text-sm leading-6 text-slate-600">
                  <div className="mt-2 h-2.5 w-2.5 flex-shrink-0 rounded-full bg-brand-500 shadow-[0_0_18px_rgba(37,99,235,0.55)]" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
            <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-3 text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">
              {trustStrip.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
          </div>

          {/* Dashboard preview card */}
          <div className="relative">
            <div className="absolute -inset-8 rounded-[2.8rem] bg-[radial-gradient(circle_at_top_right,rgba(37,99,235,0.22),transparent_34%),radial-gradient(circle_at_bottom_left,rgba(15,23,42,0.2),transparent_32%)] blur-3xl" />
            <div className="relative overflow-hidden rounded-[2.2rem] border border-white/70 bg-[linear-gradient(180deg,#08101d_0%,#0f172a_58%,#162340_100%)] p-5 shadow-[0_45px_110px_-52px_rgba(15,23,42,0.95)]">
              <div className="rounded-[1.7rem] border border-white/10 bg-[radial-gradient(circle_at_top_right,rgba(96,165,250,0.24),transparent_28%),linear-gradient(135deg,#0f172a_0%,#111827_48%,#1d4ed8_100%)] p-5 text-white">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="text-[0.68rem] font-semibold uppercase tracking-[0.26em] text-blue-100/55">Analyst Brief Preview</div>
                    <div className="mt-2 text-2xl font-semibold tracking-tight">6ix Bio Composites Inc</div>
                    <div className="mt-2 text-sm leading-6 text-blue-100/72">Connected accounting and CRM activity.</div>
                  </div>
                  <div className="rounded-[1.25rem] border border-white/15 bg-white/10 px-4 py-3 text-right backdrop-blur">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-blue-100/58">Health Score</div>
                    <div className="mt-1 text-4xl font-semibold tracking-tight">81</div>
                    <div className="text-sm text-blue-100/72">Strong</div>
                  </div>
                </div>
                <div className="mt-5 rounded-[1.45rem] border border-white/10 bg-white/10 p-4 text-sm leading-7 text-blue-50/88">
                  Revenue is steady, but profit can improve if the team fixes follow-up speed and protects the best lead sources.
                </div>
                <div className="mt-5 grid gap-3 md:grid-cols-3">
                  {analystCards.map((item) => (
                    <div key={item.label} className="rounded-[1.25rem] border border-white/10 bg-white/8 p-4 backdrop-blur-sm">
                      <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-blue-100/50">{item.label}</div>
                      <div className="mt-3 text-sm leading-6 text-white">{item.value}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-[1.08fr_0.92fr]">
                <div className="rounded-[1.6rem] border border-white/70 bg-white/94 p-5 shadow-[0_20px_45px_-35px_rgba(15,23,42,0.45)]">
                  <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Pipeline Visibility</div>
                  <div className="mt-4 space-y-3">
                    {productMoments.map((item, idx) => (
                      <div key={item.title} className="rounded-[1.15rem] border border-slate-200 bg-slate-50/90 p-4">
                        <div className="flex items-center gap-3">
                          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-[linear-gradient(135deg,#2563eb_0%,#1d4ed8_100%)] text-sm font-semibold text-white shadow-[0_12px_24px_-14px_rgba(37,99,235,0.7)]">
                            {idx + 1}
                          </div>
                          <div className="text-sm font-semibold text-slate-950">{item.title}</div>
                        </div>
                        <div className="mt-3 text-sm leading-6 text-slate-500">{item.detail}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded-[1.6rem] border border-white/70 bg-white/94 p-5 shadow-[0_20px_45px_-35px_rgba(15,23,42,0.45)]">
                  <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">What You Get</div>
                  <div className="mt-4 space-y-3">
                    {[
                      'A dashboard that explains the business in plain English',
                      'A clean connection flow for QuickBooks and CRM systems',
                      'Paid plans with Growth reserved for deeper analysis and monitoring',
                    ].map((item) => (
                      <div key={item} className="rounded-[1.15rem] border border-brand-100 bg-brand-50/60 p-4 text-sm leading-6 text-slate-700">
                        {item}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Platform stats bar */}
        <section className="mt-16 grid grid-cols-2 gap-4 rounded-[1.75rem] border border-white/75 bg-white/80 p-6 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur md:grid-cols-4">
          {platformStats.map((stat) => (
            <div key={stat.label} className="text-center">
              <div className="text-3xl font-semibold tracking-tight text-slate-950">{stat.value}</div>
              <div className="mt-1 text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{stat.label}</div>
            </div>
          ))}
        </section>

        {/* Revenue leak callout (TW-175: generated via twistor-ui skill) */}
        <LeakCalloutBand />

        {/* How it works */}
        <section id="how-it-works" className="mt-24 grid gap-5 lg:grid-cols-[0.78fr_1.22fr] lg:items-start">
          <div className="pt-2">
            <div className="section-kicker">How It Works</div>
            <h2 className="mt-3 max-w-lg text-3xl font-semibold tracking-tight text-slate-950">
              From sign-in to business audit.
            </h2>
            <p className="mt-4 max-w-md text-sm leading-7 text-slate-600">
              Connect the tools, clean the data, compute the metrics, and return a dashboard the owner can use right away.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {[
              ['1. Sign up', 'The user lands on a polished page, sees the dashboard preview, and starts with a single CTA.'],
              ['2. Connect systems', 'QuickBooks and CRM connectors bring in real operating records without manual exports.'],
              ['3. Schema match', 'Provider fields are mapped into leads, customers, sales, and expenses.'],
              ['4. Validate + clean', 'Bad shapes and missing values are flagged before they distort the dashboard.'],
              ['5. Compute KPIs', 'Metrics and operating patterns are calculated from the normalized tables.'],
              ['6. Return the audit', 'The owner gets an analyst-style summary, risks, opportunities, and next actions.'],
            ].map(([title, detail]) => (
              <div key={title} className="glass-panel p-5">
                <div className="text-lg font-semibold tracking-tight text-slate-950">{title}</div>
                <div className="mt-2 text-sm leading-7 text-slate-600">{detail}</div>
              </div>
            ))}
          </div>
        </section>

        {/* Industry templates */}
        <section id="templates" className="mt-24 grid gap-5 lg:grid-cols-[0.72fr_1.28fr] lg:items-start">
          <div className="pt-2">
            <div className="section-kicker">Industry Templates</div>
            <h2 className="mt-3 max-w-lg text-3xl font-semibold tracking-tight text-slate-950">
              Choose the business model before you even sign up.
            </h2>
            <p className="mt-4 max-w-md text-sm leading-7 text-slate-600">
              Every template comes with a sample dashboard shape, service mix, lead-source profile, and KPI focus so owners can picture the product in their world immediately.
            </p>
            <div className="mt-6">
            <Link to={primaryHref} onClick={() => trackCta('industry_template_start', primaryHref)} className="btn-primary">Start With An Industry Template</Link>
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            {industryShowcase.map((industry) => (
              <div key={industry.key} className="glass-panel overflow-hidden p-0">
                <div className="border-b border-slate-200 bg-[linear-gradient(135deg,#0f172a_0%,#1d4ed8_100%)] px-5 py-4 text-white">
                  <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-blue-100/55">Industry</div>
                  <div className="mt-2 text-xl font-semibold tracking-tight">{industry.title}</div>
                  <div className="mt-2 text-sm leading-6 text-blue-100/74">{industry.snapshot}</div>
                </div>
                <div className="p-5">
                  <div className="rounded-[1.2rem] border border-slate-200 bg-slate-50/90 p-4">
                    <div className="text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-slate-400">Services</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {industry.services.map((service) => (
                        <span key={service} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700">
                          {service}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="mt-4 rounded-[1.2rem] border border-brand-100 bg-brand-50/60 p-4">
                    <div className="text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-brand-700">KPI Focus</div>
                    <div className="mt-3 space-y-2">
                      {industry.metrics.map((metric) => (
                        <div key={metric} className="text-sm leading-6 text-slate-700">{metric}</div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Testimonials */}
        <section className="mt-24">
          <div className="mb-10 text-center">
            <div className="section-kicker">What owners say</div>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
              Built for operators, not analysts.
            </h2>
          </div>
          <div className="grid gap-5 md:grid-cols-3">
            {testimonials.map((t) => (
              <div key={t.name} className="card-surface p-6">
                <div className="text-sm leading-7 text-slate-600">"{t.quote}"</div>
                <div className="mt-5 flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[linear-gradient(135deg,#2563eb,#0f172a)] text-xs font-semibold text-white">
                    {t.initials}
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-slate-950">{t.name}</div>
                    <div className="text-xs text-slate-500">{t.role}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Pricing */}
        <section id="pricing" className="mt-24">
          <div className="mb-10 text-center">
            <div className="section-kicker">Pricing</div>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
              Start lean. Grow into more.
            </h2>
            <p className="mt-4 text-sm leading-7 text-slate-600">
              Monthly plans for teams that want clearer numbers. Upgrade or cancel any time.
            </p>
          </div>
          <div className="grid gap-5 lg:grid-cols-4">
            {plans.map((plan) => (
              <div
                key={plan.key}
                className={`relative flex flex-col rounded-[1.75rem] border p-7 transition-all duration-300 ${
                  plan.highlight
                    ? 'border-brand-500/40 bg-[linear-gradient(160deg,#1d4ed8_0%,#0f172a_100%)] text-white shadow-[0_36px_90px_-38px_rgba(37,99,235,0.6)]'
                    : 'border-white/75 bg-white/90 text-slate-950 shadow-[0_24px_65px_-34px_rgba(15,23,42,0.18)] backdrop-blur hover:-translate-y-0.5'
                }`}
              >
                {plan.badge && (
                  <div className={`absolute -top-3 left-1/2 -translate-x-1/2 rounded-full px-4 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${
                    plan.highlight ? 'bg-white text-brand-700' : 'bg-brand-50 text-brand-700 border border-brand-100'
                  }`}>
                    {plan.badge}
                  </div>
                )}
                <div>
                  <div className={`text-[0.72rem] font-semibold uppercase tracking-[0.24em] ${plan.highlight ? 'text-blue-200/70' : 'text-slate-400'}`}>
                    {plan.title}
                  </div>
                  <div className="mt-4 flex items-end gap-1">
                    <span className="text-5xl font-semibold tracking-tight">{plan.price}</span>
                    <span className={`mb-1.5 text-sm ${plan.highlight ? 'text-blue-100/60' : 'text-slate-500'}`}>{plan.sub}</span>
                  </div>
                  <p className={`mt-4 text-sm leading-7 ${plan.highlight ? 'text-blue-100/75' : 'text-slate-600'}`}>
                    {plan.description}
                  </p>
                </div>
                <div className="mt-6 flex-1 space-y-3">
                  {plan.bullets.map((bullet) => (
                    <div key={bullet} className="flex items-start gap-3 text-sm leading-6">
                      <div className={`mt-2 h-2 w-2 flex-shrink-0 rounded-full ${plan.highlight ? 'bg-blue-300' : 'bg-brand-500'}`} />
                      <span className={plan.highlight ? 'text-blue-50/90' : 'text-slate-600'}>{bullet}</span>
                    </div>
                  ))}
                </div>
                <div className="mt-8">
                  {plan.href.startsWith('mailto:') ? (
                    <a
                      href={plan.href}
                      onClick={() => trackCta(`pricing_${plan.key}`, plan.href)}
                      className="btn-primary block w-full justify-center text-center"
                    >
                      {plan.cta}
                    </a>
                  ) : (
                    <Link
                      to={plan.href}
                      onClick={() => trackCta(`pricing_${plan.key}`, plan.href)}
                      className={`block w-full rounded-xl px-5 py-3 text-center text-sm font-semibold transition-all duration-300 ${
                        plan.highlight
                          ? 'border border-white/30 bg-white text-brand-700 hover:bg-white/90'
                          : 'btn-primary'
                      }`}
                    >
                      {plan.cta}
                    </Link>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Security */}
        <section id="security" className="mt-24 overflow-hidden rounded-[2rem] border border-slate-200 bg-[linear-gradient(135deg,#ffffff_0%,#f8fafc_55%,#dbeafe_100%)] p-6 shadow-[0_30px_80px_-44px_rgba(15,23,42,0.45)]">
          <div className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
            <div>
              <div className="section-kicker">Security & Trust</div>
              <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
                Secure by design.
              </h2>
              <p className="mt-4 max-w-lg text-sm leading-7 text-slate-600">
                Tokens stay server-side, credentials are encrypted, and imported data is validated before it reaches the dashboard.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {[
                'Encrypted integration credentials',
                'Server-side OAuth token exchange',
                'Org-scoped queries and RLS support',
                'Validation before normalization',
                'Sync logs without exposing secrets',
                'A paywall that gates advanced AI features cleanly',
              ].map((item) => (
                <div key={item} className="rounded-[1.3rem] border border-white/80 bg-white/80 p-4 text-sm leading-6 text-slate-700 shadow-[0_14px_34px_-28px_rgba(15,23,42,0.32)] backdrop-blur">
                  {item}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section className="mt-24 grid gap-5 lg:grid-cols-[0.7fr_1.3fr] lg:items-start">
          <div className="pt-2">
            <div className="section-kicker">FAQ</div>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
              Common questions.
            </h2>
            <p className="mt-4 text-sm leading-7 text-slate-600">
              Everything you need to know before connecting your first integration.
            </p>
          </div>
          <div className="space-y-3">
            {faqs.map((faq, idx) => (
              <div key={faq.q} className="overflow-hidden rounded-[1.5rem] border border-white/75 bg-white/85 shadow-[0_14px_40px_-24px_rgba(15,23,42,0.14)] backdrop-blur">
                <button
                  className="flex w-full items-center justify-between px-6 py-5 text-left text-sm font-semibold text-slate-950 transition-colors hover:text-brand-700"
                  onClick={() => setOpenFaq(openFaq === idx ? null : idx)}
                >
                  <span>{faq.q}</span>
                  <svg
                    className={`ml-4 h-4 w-4 flex-shrink-0 text-slate-400 transition-transform duration-200 ${openFaq === idx ? 'rotate-180' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {openFaq === idx && (
                  <div className="px-6 pb-5 text-sm leading-7 text-slate-600">{faq.a}</div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Final CTA banner */}
        <section className="mt-24 overflow-hidden rounded-[2rem] bg-[linear-gradient(135deg,#0f172a_0%,#1d4ed8_55%,#1e3a5f_100%)] p-10 text-center shadow-[0_40px_100px_-44px_rgba(37,99,235,0.7)]">
          <div className="section-kicker text-blue-200/60">Get started today</div>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            Your business deserves a real audit.
          </h2>
          <p className="mx-auto mt-4 max-w-lg text-sm leading-7 text-blue-100/72">
            Connect your tools, pick your industry, and get a health score with plain-English next steps in minutes.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-4">
            <Link to={primaryHref} onClick={() => trackCta('final_cta_start', primaryHref)} className="rounded-xl border border-white/30 bg-white px-6 py-3 text-sm font-semibold text-brand-700 shadow-[0_18px_40px_-18px_rgba(255,255,255,0.4)] transition-all duration-300 hover:-translate-y-0.5 hover:bg-white/95">
              {isSignedIn ? 'Go to Dashboard' : 'Start Starter'}
            </Link>
            <a href="#how-it-works" className="rounded-xl border border-white/25 px-6 py-3 text-sm font-semibold text-white transition-all duration-300 hover:-translate-y-0.5 hover:border-white/50 hover:bg-white/10">
              See How It Works
            </a>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="relative z-10 mt-16 border-t border-slate-200 bg-white/60 backdrop-blur">
        <div className="mx-auto max-w-7xl px-6 py-12 xl:px-8">
          <div className="grid gap-8 md:grid-cols-[1.4fr_repeat(3,1fr)]">
            <div>
              <div className="text-xl font-semibold tracking-tight text-slate-950">LBT OS</div>
              <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.22em] text-slate-500">Lean Business Tracker</div>
              <p className="mt-4 text-sm leading-7 text-slate-600">
                Audit software for small business owners who want clarity, not complexity.
              </p>
            </div>
            {Object.entries(footerLinks).map(([group, links]) => (
              <div key={group}>
                <div className="text-[0.72rem] font-semibold uppercase tracking-[0.22em] text-slate-400">{group}</div>
                <ul className="mt-4 space-y-3">
                  {links.map((link) => (
                    <li key={link.label}>
                      {link.href.startsWith('/') ? (
                        <Link to={link.href} className="text-sm text-slate-600 transition-colors hover:text-slate-950">
                          {link.label}
                        </Link>
                      ) : (
                        <a href={link.href} className="text-sm text-slate-600 transition-colors hover:text-slate-950">
                          {link.label}
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="mt-10 border-t border-slate-200 pt-8 flex flex-col items-center justify-between gap-4 sm:flex-row">
            <p className="text-xs text-slate-400">© {new Date().getFullYear()} LBT OS. All rights reserved.</p>
            <p className="text-xs text-slate-400">Built for lean operators.</p>
          </div>
        </div>
      </footer>
    </div>
  )
}
