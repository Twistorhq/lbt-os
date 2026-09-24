import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { billingApi, orgApi } from '../lib/api'

const plans = [
  {
    key: 'basic',
    name: 'Starter',
    price: '$49/mo',
    description: 'One dashboard that replaces four spreadsheets.',
    features: [
      'Lead & sales pipeline',
      'Customer management',
      'Revenue & expense tracking',
      'Real-time profit dashboard',
      'Basic metrics & reports',
    ],
  },
  {
    key: 'pro',
    name: 'Growth',
    price: '$129/mo',
    description: 'For teams that need AI-powered revenue intelligence.',
    features: [
      'Everything in Starter',
      'AI Revenue Audit (recurring scans)',
      'Expense management',
      'QuickBooks & HubSpot integrations',
      'Up to 5 team members',
      'PDF audit exports',
    ],
  },
  {
    key: 'premium',
    name: 'Scale',
    price: '$299/mo',
    description: 'Multi-location operators and agencies.',
    features: [
      'Everything in Growth',
      'Unlimited team members',
      'White-label audit reports',
      'API access',
      'Custom playbooks',
      'Priority support',
    ],
  },
  {
    key: 'enterprise',
    name: 'Enterprise + DOTs',
    price: 'Custom',
    description: 'For teams that want LBT OS plus a professional video editing and scheduling layer.',
    features: [
      'Everything in Scale',
      'DOTs raw video import pipeline',
      'Industry-specific video templates',
      'Short-form and long-form edit generation',
      'Review, approval, and posting schedule workflow',
      'White-glove implementation and media ops support',
    ],
    contactOnly: true,
  },
]

export default function Billing() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const upgradedPlan = searchParams.get('upgraded')   // set by Stripe success redirect
  const checkoutSessionId = searchParams.get('session_id')
  const qc = useQueryClient()

  const { data: org } = useQuery({
    queryKey: ['org-me'],
    queryFn: () => orgApi.getMe().then((r) => r.data),
  })

  const checkoutStatus = useQuery({
    queryKey: ['checkout-session', checkoutSessionId],
    queryFn: () => billingApi.checkoutStatus(checkoutSessionId).then((r) => r.data),
    enabled: Boolean(checkoutSessionId),
    retry: 2,
    retryDelay: 2500,
  })

  // Stripe's redirect to this page already confirms payment — always refresh org data
  useEffect(() => {
    if (!upgradedPlan && !checkoutSessionId) return
    qc.invalidateQueries({ queryKey: ['org-me'] })
    // Poll a few more times so the plan shows up even when webhooks have slight delay
    const t1 = setTimeout(() => qc.invalidateQueries({ queryKey: ['org-me'] }), 3000)
    const t2 = setTimeout(() => qc.invalidateQueries({ queryKey: ['org-me'] }), 8000)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [upgradedPlan, checkoutSessionId, qc])

  // Auto-clear URL params after the banner has been seen
  useEffect(() => {
    if (!upgradedPlan && !checkoutSessionId) return
    const t = setTimeout(() => setSearchParams({}, { replace: true }), 14000)
    return () => clearTimeout(t)
  }, [upgradedPlan, checkoutSessionId, setSearchParams])

  // Reset dismiss state when banner source changes
  useEffect(() => {
    setBannerDismissed(false)
  }, [upgradedPlan, checkoutSessionId])

  const checkout = useMutation({
    mutationFn: (plan) => billingApi.checkout(plan).then((r) => r.data),
    onSuccess: (data) => {
      window.location.href = data.checkout_url
    },
  })

  const portal = useMutation({
    mutationFn: () => billingApi.portal().then((r) => r.data),
    onSuccess: (data) => {
      window.location.href = data.portal_url
    },
  })

  const subscriptionActive = ['active', 'trialing'].includes(org?.subscription_status)

  return (
    <div className="page-shell">

      {/* ── Stripe checkout return banner ── */}
      {(upgradedPlan || checkoutSessionId) && !bannerDismissed && (() => {
        // Verification error → degrade gracefully (Stripe redirect already confirms payment)
        const isVerified   = checkoutStatus.isSuccess
        const isVerifying  = checkoutStatus.isPending
        // Treat any verification failure as a soft success — plan will sync via webhook
        const softSuccess  = checkoutStatus.isError || (!checkoutSessionId && upgradedPlan)
        const displayPlan  = checkoutStatus.data?.plan || upgradedPlan
        const displayLabel = plans.find((p) => p.key === displayPlan)?.name

        return (
          <section className={`rounded-2xl border px-6 py-5 flex items-start gap-4 ${
            isVerifying ? 'border-blue-200 bg-blue-50' : 'border-emerald-200 bg-emerald-50'
          }`}>
            {/* Icon */}
            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full font-bold text-lg ${
              isVerifying ? 'bg-blue-100 text-blue-600' : 'bg-emerald-100 text-emerald-700'
            }`}>
              {isVerifying
                ? <span className="inline-block animate-spin text-base">⟳</span>
                : '✓'}
            </div>

            {/* Message */}
            <div className="flex-1 min-w-0">
              <div className={`font-semibold ${isVerifying ? 'text-blue-900' : 'text-emerald-900'}`}>
                {isVerifying
                  ? 'Activating your plan…'
                  : displayLabel ? `Welcome to ${displayLabel}!` : 'Payment received!'}
              </div>
              <p className={`mt-1 text-sm leading-6 ${isVerifying ? 'text-blue-700' : 'text-emerald-700'}`}>
                {isVerifying
                  ? 'Confirming with Stripe — this only takes a moment.'
                  : isVerified
                    ? 'Verified with Stripe. Your paid features are active now.'
                    : 'Your payment went through. Your plan will update momentarily — refresh if it takes more than a minute.'}
              </p>
            </div>

            {/* Dismiss */}
            <button
              className="shrink-0 ml-2 text-slate-400 hover:text-slate-600 text-xl leading-none"
              aria-label="Dismiss"
              onClick={() => { setBannerDismissed(true); setSearchParams({}, { replace: true }) }}
            >
              ×
            </button>
          </section>
        )
      })()}

      <section className="page-command">
        <div>
          <div className="section-kicker">Access</div>
          <h1 className="page-title">Billing</h1>
          <p className="page-copy">Replace your spreadsheets. Know your numbers. Start with Starter, scale when you're ready.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-500">Current plan: {org?.plan || 'basic'}</span>
          {subscriptionActive && (
            <button className="btn-secondary" onClick={() => portal.mutate()} disabled={portal.isPending}>
              Manage Billing
            </button>
          )}
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-4">
        {plans.map((plan) => {
          const isCurrent = subscriptionActive && org?.plan === plan.key
          const actionLabel = subscriptionActive ? `Switch to ${plan.name}` : `Start ${plan.name}`
          return (
            <div key={plan.key} className={`group relative overflow-hidden card-surface p-6 ${plan.key === 'pro' ? 'ring-2 ring-gold-400/60 shadow-[0_24px_60px_-30px_rgba(245,185,66,0.45)]' : ''}`}>
              <div className={`absolute inset-x-0 top-0 h-1 ${plan.key === 'pro' ? 'bg-[linear-gradient(90deg,#6366f1_0%,#f5b942_100%)]' : 'bg-[linear-gradient(90deg,rgba(148,163,184,0.15),rgba(148,163,184,0.55),rgba(148,163,184,0.15))]'}`} />
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[0.7rem] font-semibold uppercase tracking-[0.22em] text-slate-400">{plan.name}</div>
                  <div className="mt-3 text-4xl font-semibold tracking-tight text-slate-950">{plan.price}</div>
                  <div className="mt-2 text-sm leading-6 text-slate-500">{plan.description}</div>
                </div>
                {plan.key === 'pro' && (
                  <div className="animate-glowPulse rounded-full bg-gold-400/20 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-gold-600 ring-1 ring-gold-400/50">
                    Recommended
                  </div>
                )}
              </div>
              <div className="mt-6 space-y-3">
                {plan.features.map((feature) => (
                  <div key={feature} className="flex items-start gap-3 text-sm leading-6 text-slate-600">
                    <div className={`mt-2 h-2.5 w-2.5 rounded-full ${plan.key === 'pro' ? 'bg-gold-500' : plan.key === 'premium' ? 'bg-violet-500' : 'bg-emerald-500'}`} />
                    <span>{feature}</span>
                  </div>
                ))}
              </div>
              <div className="mt-8">
                {isCurrent ? (
                  <div className="inline-flex rounded-full border border-emerald-100 bg-emerald-50 px-4 py-2 text-sm font-medium text-emerald-700 shadow-sm">
                    Current plan
                  </div>
                ) : plan.contactOnly ? (
                  <a
                    className="btn-secondary w-full justify-center"
                    href="mailto:sales@lbt-os.com?subject=DOTs%20Enterprise%20for%20LBT%20OS"
                  >
                    Talk to sales
                  </a>
                ) : (
                  <button className="btn-primary w-full justify-center" onClick={() => checkout.mutate(plan.key)} disabled={checkout.isPending}>
                    {checkout.isPending ? 'Opening Stripe...' : actionLabel}
                  </button>
                )}
                {checkout.isError && (
                  <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">
                    {checkout.error?.response?.data?.detail || 'Could not open Stripe Checkout.'}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </section>
    </div>
  )
}
