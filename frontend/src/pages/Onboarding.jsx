import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUser, useAuth } from '@clerk/clerk-react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { orgApi, setAuthToken } from '../lib/api'
import { TwistorMark } from '../components/icons'
import { trackVisitorEvent } from '../lib/analytics'

function StrokeIcon({ d, circles, className = 'h-6 w-6' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      {circles?.map((c, i) => (
        <circle key={i} cx={c[0]} cy={c[1]} r={c[2]} />
      ))}
      {d.map((path, i) => (
        <path key={i} d={path} />
      ))}
    </svg>
  )
}

const INDUSTRY_ICONS = {
  hvac:        <StrokeIcon d={['M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9L4.9 19.1']} />,
  plumbing:    <StrokeIcon d={['M12 3s6 6.6 6 11a6 6 0 0 1-12 0c0-4.4 6-11 6-11z']} />,
  electrician: <StrokeIcon d={['M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z']} />,
  landscaping: <StrokeIcon d={['M5 19C5 9 13 5 20 4c0 8-4 15-14 15zM5 19c3-5 7-9 11-11']} />,
  cleaning_service: <StrokeIcon d={['M12 3v4M12 17v4M3 12h4M17 12h4M6.5 6.5l2 2M17.5 17.5l-2-2M17.5 6.5l-2 2M6.5 17.5l2-2']} />,
  gig_worker:  <StrokeIcon d={['M6 8h12l1.2 12.5H4.8L6 8zM9 8V6a3 3 0 0 1 6 0v2']} />,
  salon_spa:   <StrokeIcon circles={[[6, 9, 2.5], [6, 15, 2.5]]} d={['M8.2 10.8L20 20M8.2 13.2L20 4']} />,
  restaurant:  <StrokeIcon d={['M7 3v6a2 2 0 0 0 4 0V3M9 11v10M17 3c-2.5 2.5-2.5 6.5 0 9v9']} />,
  gym:         <StrokeIcon d={['M7 7v10M17 7v10M4 9.5v5M20 9.5v5M7 12h10']} />,
  real_estate: <StrokeIcon d={['M3 11l9-7 9 7M5.5 9.5V20h13V9.5']} />,
  other:       <StrokeIcon d={['M4 8h16v12H4zM9 8V6a3 3 0 0 1 3-3v0a3 3 0 0 1 3 3v2']} />,
}

const INDUSTRY_OPTIONS = [
  { key: 'hvac',           label: 'HVAC' },
  { key: 'plumbing',       label: 'Plumbing' },
  { key: 'electrician',    label: 'Electrician' },
  { key: 'landscaping',    label: 'Landscaping' },
  { key: 'cleaning_service', label: 'Cleaning' },
  { key: 'gig_worker',     label: 'Gig Worker' },
  { key: 'salon_spa',      label: 'Salon / Spa' },
  { key: 'restaurant',     label: 'Restaurant' },
  { key: 'gym',            label: 'Gym / Fitness' },
  { key: 'real_estate',    label: 'Real Estate' },
  { key: 'other',          label: 'Other' },
]

export default function Onboarding() {
  const [step, setStep]         = useState(0)
  const [industry, setIndustry] = useState('')
  const [orgName, setOrgName]   = useState('')
  const [city, setCity]         = useState('Denver')
  const [replaceExistingDemo, setReplaceExistingDemo] = useState(false)
  const navigate      = useNavigate()
  const { user }      = useUser()
  const { getToken }  = useAuth()
  const tileRefs      = useRef([])

  // ARIA radiogroup pattern: arrows move + select, space/enter are native to buttons
  const onTileKeyDown = (e, idx) => {
    const len = INDUSTRY_OPTIONS.length
    const cols = 2
    let next = null
    if (e.key === 'ArrowRight')      next = (idx + 1) % len
    else if (e.key === 'ArrowLeft')  next = (idx - 1 + len) % len
    else if (e.key === 'ArrowDown')  next = (idx + cols) % len
    else if (e.key === 'ArrowUp')    next = (idx - cols + len) % len
    if (next !== null) {
      e.preventDefault()
      setIndustry(INDUSTRY_OPTIONS[next].key)
      tileRefs.current[next]?.focus()
    }
  }

  useEffect(() => {
    setReplaceExistingDemo(false)
  }, [industry])

  useEffect(() => {
    trackVisitorEvent('page_view', {
      page: 'onboarding',
      signed_in: Boolean(user?.id),
    })
  }, [user?.id])

  const submittedInfo = (mode) => ({
    page: 'onboarding',
    mode,
    clerk_user_id: user?.id || null,
    business_name: orgName || user?.fullName || 'My Business',
    industry: industry === 'other' ? null : industry,
    city,
    state: 'CO',
  })

  const { data: template } = useQuery({
    queryKey: ['industry-template', industry],
    queryFn: () => orgApi.getTemplate(industry).then((r) => r.data),
    enabled: Boolean(industry) && industry !== 'other',
  })

  const create = useMutation({
    mutationFn: async () => {
      // Always get a fresh token right before the call — avoids race with AuthSync
      const token = await getToken()
      setAuthToken(token)
      return orgApi.create({
        name:     orgName || user?.fullName || 'My Business',
        industry: industry === 'other' ? null : industry,
        city,
        state: 'CO',
      })
    },
    onSuccess: () => {
      trackVisitorEvent('info_submitted', submittedInfo('create_workspace'))
      navigate('/app')
    },
  })

  const launchDemo = useMutation({
    mutationFn: async () => {
      const token = await getToken()
      setAuthToken(token)
      return orgApi.bootstrapDemo({
        industry,
        name: orgName || undefined,
        city,
        state: 'CO',
        replace_existing: replaceExistingDemo,
      })
    },
    onSuccess: () => {
      trackVisitorEvent('info_submitted', submittedInfo('launch_demo'))
      navigate('/app')
    },
  })

  return (
    <div className="min-h-screen flex">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-5/12 bg-gradient-to-br from-sofrito-900 via-sofrito-950 to-sofrito-950 flex-col justify-between p-10">
        <div className="flex items-center gap-2.5">
          <TwistorMark className="h-9 w-9" />
          <div>
            <div className="text-white text-[15px] font-bold tracking-tight">
              Twistor <span className="font-medium text-white/60">Trades</span>
            </div>
            <div className="text-white/45 text-[11px]">Operating system</div>
          </div>
        </div>
        <div className="space-y-6">
          <h1 className="text-white text-4xl font-bold leading-tight">
            Run your business<br />smarter.
          </h1>
          <p className="text-indigo-200 text-base leading-relaxed">
            Track leads, revenue, and expenses in one place — then let AI tell you exactly where you're losing money.
          </p>
          <div className="space-y-3 pt-2">
            {['Lead & sales pipeline tracking', 'Real-time profit dashboard', 'AI-powered revenue audit', 'Built for Denver businesses'].map((f) => (
              <div key={f} className="flex items-center gap-3">
                <div className="w-5 h-5 rounded-full bg-indigo-500 flex items-center justify-center shrink-0">
                  <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <span className="text-indigo-100 text-sm">{f}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="text-indigo-300/70 text-xs">Prepared by Twistor Holdings LLC · Denver, CO</div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center bg-gray-50 p-6">
        <div className="w-full max-w-md">

          {/* Progress */}
          <div className="flex items-center gap-2 mb-8">
            {['Industry', 'Details'].map((label, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold transition-colors ${i <= step ? 'bg-sofrito-950 text-white' : 'bg-gray-200 text-gray-400'}`}>
                  {i < step ? (
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  ) : i + 1}
                </div>
                <span className={`text-sm font-medium ${i <= step ? 'text-gray-800' : 'text-gray-400'}`}>{label}</span>
                {i < 1 && <div className={`h-px w-8 mx-1 ${step > i ? 'bg-sofrito-950' : 'bg-gray-200'}`} />}
              </div>
            ))}
          </div>

          {step === 0 && (
            <div className="space-y-6">
              <div>
                <h2 className="text-2xl font-bold text-gray-900">What type of business?</h2>
                <p className="text-gray-500 text-sm mt-1">We'll pre-configure your dashboard and templates.</p>
              </div>
              <div
                role="radiogroup"
                aria-label="Business type"
                className="grid grid-cols-2 gap-3"
              >
                {INDUSTRY_OPTIONS.map((opt, idx) => {
                  const selected = industry === opt.key
                  return (
                    <button
                      key={opt.key}
                      ref={(el) => { tileRefs.current[idx] = el }}
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setIndustry(opt.key)}
                      onKeyDown={(e) => onTileKeyDown(e, idx)}
                      className={`p-4 rounded-xl border-2 text-left transition-all hover:border-gold-500/60 hover:bg-gold-400/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 ${
                        selected ? 'border-gold-500 bg-gold-400/10 shadow-sm' : 'border-gray-200 bg-white'
                      }`}
                    >
                      <div className={`mb-2 ${selected ? 'text-gold-600' : 'text-slate-400'}`}>
                        {INDUSTRY_ICONS[opt.key]}
                      </div>
                      <div className={`text-sm font-semibold ${selected ? 'text-sofrito-900' : 'text-gray-700'}`}>{opt.label}</div>
                    </button>
                  )
                })}
              </div>
              {industry && industry !== 'other' && template && (
                <div className="rounded-3xl border border-gold-400/40 bg-gradient-to-br from-white via-gold-400/10 to-slate-50 p-5 shadow-[0_18px_45px_-30px_rgba(212,143,29,0.35)]">
                  <div className="flex flex-col gap-4">
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-gold-600">Template Preview</div>
                      <h3 className="mt-2 text-lg font-semibold text-slate-950">{template.label} starter workspace</h3>
                      <p className="mt-2 text-sm leading-6 text-slate-600">
                        This preview shows how Twistor Trades will frame your first dashboard, audit, and test data.
                      </p>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-3">
                      <PreviewBlock
                        title="Example services"
                        items={template.services?.slice(0, 4)}
                      />
                      <PreviewBlock
                        title="Lead sources"
                        items={template.lead_sources?.slice(0, 4)?.map(formatLabel)}
                      />
                      <PreviewBlock
                        title="KPI focus"
                        items={template.key_metrics?.slice(0, 4)?.map(formatLabel)}
                      />
                    </div>

                    <div className="rounded-2xl border border-white/80 bg-white/80 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Quick wins your audit will look for</div>
                      <div className="mt-3 space-y-2">
                        {(template.quick_wins || []).slice(0, 2).map((item) => (
                          <div key={item} className="text-sm leading-6 text-slate-600">
                            {item}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              <button
                disabled={!industry}
                onClick={() => setStep(1)}
                className="w-full py-3 rounded-xl bg-sofrito-950 text-white font-semibold text-sm hover:bg-sofrito-900 transition-colors disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400 focus-visible:ring-offset-2"
              >
                Continue →
              </button>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-6">
              <div>
                <button onClick={() => setStep(0)} className="text-sm text-gray-400 hover:text-gray-600 mb-4 flex items-center gap-1">
                  ← Back
                </button>
                <h2 className="text-2xl font-bold text-gray-900">Business details</h2>
                <p className="text-gray-500 text-sm mt-1">You can update these any time.</p>
              </div>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Business Name</label>
                  <input
                    autoFocus
                    className="w-full rounded-xl border border-gray-200 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white placeholder:text-gray-400"
                    placeholder={user?.fullName || 'My Business LLC'}
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">City</label>
                  <input
                    className="w-full rounded-xl border border-gray-200 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white"
                    value={city}
                    onChange={(e) => setCity(e.target.value)}
                  />
                </div>
              </div>
              <button
                onClick={() => create.mutate()}
                disabled={create.isPending}
                className="w-full py-3 rounded-xl bg-sofrito-950 text-white font-semibold text-sm hover:bg-sofrito-900 transition-colors disabled:opacity-60 flex items-center justify-center gap-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400 focus-visible:ring-offset-2"
              >
                {create.isPending ? (
                  <>
                    <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                    </svg>
                    Setting up your dashboard...
                  </>
                ) : 'Launch My Dashboard →'}
              </button>
              {industry && industry !== 'other' && (
                <button
                  onClick={() => launchDemo.mutate()}
                  disabled={launchDemo.isPending}
                  className="w-full py-3 rounded-xl border border-slate-200 bg-white text-slate-800 font-semibold text-sm hover:border-gold-500/60 hover:text-gold-600 transition-colors disabled:opacity-60 flex items-center justify-center gap-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-gold-400 focus-visible:ring-offset-2"
                >
                  {launchDemo.isPending ? 'Building demo workspace...' : replaceExistingDemo ? `Replace current data with ${template?.label || 'industry'} demo` : `Launch ${template?.label || 'industry'} demo with sample data`}
                </button>
              )}
              {create.isError && (
                <div className="rounded-xl bg-red-50 border border-red-200 p-4 text-sm text-red-700">
                  {create.error?.response?.data?.detail || 'Could not connect to backend. Is it running on port 8002?'}
                </div>
              )}
              {launchDemo.isError && (
                <div className="rounded-xl bg-red-50 border border-red-200 p-4 text-sm text-red-700">
                  <div>{launchDemo.error?.response?.data?.detail || 'Could not launch the demo workspace.'}</div>
                  {launchDemo.error?.response?.status === 409 && !replaceExistingDemo && (
                    <button
                      onClick={() => setReplaceExistingDemo(true)}
                      className="mt-3 inline-flex rounded-full border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-red-700 transition-colors hover:border-red-300 hover:bg-red-100"
                    >
                      Enable demo replacement
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </div>
  )
}

function formatLabel(value) {
  return value.replaceAll('_', ' ')
}

function PreviewBlock({ title, items = [] }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/80 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        {items.map((item) => (
          <span key={item} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-700">
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}
