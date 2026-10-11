import { useEffect, useState } from 'react'

// Fictional properties and assignments. No customer or Google-derived data.
const JOBS = [
  { id: 'sample-1', name: 'Juniper Hall', unit: 'Unit 204', trade: 'Plumbing', issue: 'Kitchen tap leaks', x: 180, y: 160 },
  { id: 'sample-2', name: 'Aspen Court', unit: 'Common area', trade: 'HVAC', issue: 'Uneven heating', x: 415, y: 270 },
  { id: 'sample-3', name: 'Cedar House', unit: 'Unit 108', trade: 'Electrical', issue: 'Routine lighting inspection', x: 535, y: 130 },
]
const STEPS = ['Request received', 'Manager approves', 'Provider accepts', 'Work in progress', 'Completion reviewed']
const button = 'rounded-xl border border-white/20 px-4 py-3 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-400'

export default function TradeViewExperience({ loadConnected, accountLink, connectedMapLink }) {
  const [tier, setTier] = useState('lite')
  const [trade, setTrade] = useState('All trades')
  const [selected, setSelected] = useState(JOBS[0].id)
  const [steps, setSteps] = useState({})
  const [layer, setLayer] = useState(true)
  const [connected, setConnected] = useState({ state: 'idle', pins: [] })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (tier !== 'pro' || !loadConnected) return
    let cancelled = false
    setConnected({ state: 'loading', pins: [] })
    loadConnected().then(({ data }) => {
      if (cancelled) return
      if (data.source !== 'live' || !Array.isArray(data.pins)) throw new Error('Invalid data source')
      setConnected({ state: 'ready', pins: data.pins, unlocated: data.unlocated_count || 0, date: data.generated_at })
    }).catch((error) => {
      if (cancelled) return
      const status = error.response?.status
      setConnected({ state: [401, 403].includes(status) ? 'locked' : 'error', pins: [] })
    })
    return () => { cancelled = true }
  }, [tier, loadConnected, attempt])
  const visible = JOBS.filter((job) => trade === 'All trades' || job.trade === trade)
  const job = visible.find((item) => item.id === selected) || visible[0]
  const step = steps[job.id] || 0
  const reset = () => { setTrade('All trades'); setSelected(JOBS[0].id); setSteps({}); setLayer(true) }
  return (
    <main className="min-h-screen bg-[#07070d] px-4 py-8 text-white sm:px-8">
      <div className="mx-auto max-w-7xl">
        <header className="flex flex-wrap items-center justify-between gap-5 border-b border-white/10 pb-6">
          <div><p className="text-xs font-semibold uppercase tracking-[0.22em]">Twistor Trades</p><h1 className="mt-2 text-3xl font-bold tracking-tight">Trade View {tier === 'lite' ? 'Lite' : 'Pro'}</h1></div>
          <nav aria-label="Trade View editions" className="flex flex-wrap gap-2">
            <button className={`${button} ${tier === 'lite' ? 'bg-violet-700' : ''}`} aria-pressed={tier === 'lite'} onClick={() => setTier('lite')}>Lite · Free sample</button>
            <button className={`${button} ${tier === 'pro' ? 'bg-violet-700' : ''}`} aria-pressed={tier === 'pro'} onClick={() => setTier('pro')}>Pro · Connected data</button>
          </nav>
        </header>
        <section className="py-8" aria-labelledby="experience-title">
          <p className="text-xs font-semibold uppercase tracking-[0.18em]">{tier === 'lite' ? 'SAMPLE DATA · No account required' : 'Subscription access · Your organization only'}</p>
          <h2 id="experience-title" className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Every property. A clear next move.</h2>
          <p className="mt-3 max-w-2xl leading-relaxed">{tier === 'lite' ? 'Explore the map, open a request, and follow the handoff from report to completion. Every change stays in this sample session.' : 'Keep connected customer and lead records in one workspace. Access requires an eligible subscription and an authenticated organization.'}</p>
        </section>
        {tier === 'lite' ? <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <label className="text-sm font-semibold" htmlFor="trade-filter">Trade</label>
            <select id="trade-filter" value={trade} onChange={(e) => setTrade(e.target.value)} className={`${button} bg-[#09111f]`}>
              {['All trades', 'Plumbing', 'HVAC', 'Electrical'].map((item) => <option key={item}>{item}</option>)}
            </select>
            <button className={button} aria-pressed={layer} onClick={() => setLayer(!layer)}>{layer ? 'Hide' : 'Show'} request pins</button>
            <button className={button} onClick={reset}>Reset sample</button>
          </div>
          <div className="grid gap-5 lg:grid-cols-[1.5fr_1fr]">
            <section aria-label="Sample property map" className="overflow-hidden rounded-2xl border border-white/10 bg-[#09111f]">
              <div className="flex justify-between gap-3 p-5 text-sm"><strong>Sample neighborhood</strong><span>{visible.length} sample requests</span></div>
              <svg viewBox="0 0 700 400" role="group" aria-label="Illustrated sample map, not geographic imagery" className="w-full">
                <defs><pattern id="tv-lite-grid" width="70" height="60" patternUnits="userSpaceOnUse"><path d="M 70 0 H 0 V 60" fill="none" stroke="#ffffff" strokeOpacity=".12" strokeWidth="2" /></pattern></defs>
                <rect width="700" height="400" fill="url(#tv-lite-grid)" />
                <path d="M-20 340 Q240 280 290 400 M240 -20 Q290 120 700 330" fill="none" stroke="#8b5cf6" strokeOpacity=".25" strokeWidth="25" />
                <text x="30" y="375" fill="white" fontSize="13">ILLUSTRATED SAMPLE · NOT TO SCALE</text>
                {layer && visible.map((item) => <g key={item.id} role="button" tabIndex={0} aria-label={`Open ${item.name} sample request`} aria-pressed={job.id === item.id} onClick={() => setSelected(item.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(item.id) } }} className="cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-white">
                  <circle cx={item.x} cy={item.y} r={job.id === item.id ? 24 : 18} fill={job.id === item.id ? '#d946ef' : '#7c3aed'} stroke="white" strokeWidth="2" />
                  <circle cx={item.x} cy={item.y} r="5" fill="white" />
                  <text x={item.x} y={item.y + 46} fill="white" textAnchor="middle" fontSize="16">{item.name}</text>
                </g>)}
              </svg>
              <div className="flex flex-wrap gap-2 p-5" aria-label="Sample requests">
                {visible.map((item) => <button key={item.id} className={`${button} ${job.id === item.id ? 'bg-violet-700' : ''}`} aria-pressed={job.id === item.id} onClick={() => setSelected(item.id)}>{item.name}</button>)}
              </div>
            </section>
            <section className="rounded-2xl border border-white/10 bg-[#0d0d17] p-6" aria-labelledby="request-title">
              <p className="text-xs font-semibold uppercase tracking-[0.18em]">SAMPLE REQUEST · {job.trade}</p>
              <h3 id="request-title" className="mt-3 text-2xl font-semibold">{job.issue}</h3>
              <p className="mt-2">{job.name} · {job.unit}</p>
              <ol className="my-6 space-y-3" aria-label="Sample request progress">{STEPS.map((label, i) => <li key={label} aria-current={i === step ? 'step' : undefined} className={`flex gap-3 rounded-xl p-3 ${i === step ? 'bg-violet-900' : ''}`}><span aria-hidden="true">{i < step ? '✓' : i + 1}</span><span>{label}</span></li>)}</ol>
              <p role="status" className="mb-4 text-sm">Sample status: {STEPS[step]}. No provider is contacted.</p>
              <button className={`${button} w-full bg-gradient-to-r from-violet-700 to-fuchsia-800 disabled:opacity-60`} disabled={step === STEPS.length - 1} onClick={() => setSteps({ ...steps, [job.id]: step + 1 })}>{step === STEPS.length - 1 ? 'Sample complete' : `Try: ${STEPS[step + 1]}`}</button>
            </section>
          </div>
          <p className="mt-5 text-sm leading-relaxed">Fictional properties and workflow. No charges, bookings, messages, or saved changes. This sample uses no Google API, external map tiles, or live video.</p>
        </> : <section className="rounded-2xl border border-white/10 bg-[#09111f] p-6" aria-label="Connected workspace">
          <h3 className="text-2xl font-semibold">Your business, connected.</h3>
          <p role="status" className="my-4 leading-relaxed">{!loadConnected ? 'This offline preview contains sample data only. Live access is not activated here.' : connected.state === 'loading' ? 'Checking subscription access and loading organization records…' : connected.state === 'locked' ? 'Sign in with an organization that has an eligible active subscription.' : connected.state === 'error' ? 'Connected data is unavailable. Try again; sample records do not replace your data.' : connected.state === 'ready' ? `${connected.pins.length} located records · ${connected.unlocated} records without a map location.` : 'Connected workspace'}</p>
          {connected.state === 'ready' && <>
            {connected.pins.length === 0 && <p>No located customer or lead records. Connect your business data and add usable addresses.</p>}
            <ul className="my-4 grid gap-3 sm:grid-cols-2">{connected.pins.map((pin) => <li key={`${pin.kind}-${pin.id}`} className="rounded-xl border border-white/20 p-4"><strong>{pin.name || 'Unnamed record'}</strong><p>{pin.trade || 'Trade not supplied'} · {pin.kind}</p><p>{pin.address || 'Address not supplied'}</p></li>)}</ul>
            <p className="text-sm">Source: your organization records. Google imagery and provider availability are not connected in this iteration.</p>
            {connectedMapLink && <a className={`${button} mt-4 inline-block`} href={connectedMapLink}>Open connected Trade View map</a>}
          </>}
          {loadConnected && ['error', 'locked'].includes(connected.state) && <button className={button} onClick={() => setAttempt(attempt + 1)}>Retry access check</button>}
          {accountLink && <a href={accountLink} className={`${button} mt-4 inline-block`}>Open your account</a>}
          <button className={`${button} ml-2 mt-4`} onClick={() => setTier('lite')}>Explore the free sample</button>
        </section>}
        <footer className="mt-8 border-t border-white/10 pt-6 text-sm leading-relaxed">Lite: free, interactive sample. Pro: subscription-gated organization data. Google maps, 3D imagery, provider dispatch, payments, and video each require their own live integration and activation checks.</footer>
      </div>
    </main>
  )
}
