import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { orgApi, strategyApi } from '../lib/api'
import { Link } from 'react-router-dom'

const fmt$ = (n) => `$${(n || 0).toLocaleString('en-US', { minimumFractionDigits: 0 })}`

const SIGNAL_STYLES = {
  urgent:      { border: 'border-rose-200',    bg: 'bg-rose-50/70',    icon: 'text-rose-600',    badge: 'bg-rose-100 text-rose-700' },
  warning:     { border: 'border-amber-200',   bg: 'bg-amber-50/70',   icon: 'text-amber-600',   badge: 'bg-amber-100 text-amber-700' },
  opportunity: { border: 'border-brand-200',   bg: 'bg-brand-50/60',   icon: 'text-brand-600',   badge: 'bg-brand-100 text-brand-700' },
  positive:    { border: 'border-emerald-200', bg: 'bg-emerald-50/60', icon: 'text-emerald-600', badge: 'bg-emerald-100 text-emerald-700' },
}

const SIGNAL_LABELS = {
  urgent: 'Urgent', warning: 'Watch', opportunity: 'Opportunity', positive: 'Positive',
}

const EFFORT_COLORS = {
  low: 'text-emerald-700 bg-emerald-50 border-emerald-100',
  medium: 'text-amber-700 bg-amber-50 border-amber-100',
  high: 'text-rose-700 bg-rose-50 border-rose-100',
}

const TABS = ['AI Strategist', 'Competitor Intel', 'Market Position']

export default function Strategy() {
  const [activeTab, setActiveTab] = useState(0)

  const { data: org } = useQuery({
    queryKey: ['org-me'],
    queryFn: () => orgApi.getMe().then((r) => r.data),
  })

  const { data: briefing, isLoading: briefingLoading } = useQuery({
    queryKey: ['strategy-briefing'],
    queryFn: () => strategyApi.briefing().then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })

  const isPro = org?.plan === 'pro' || org?.plan === 'premium' || org?.plan === 'enterprise'

  return (
    <div className="page-shell">

      {/* Header */}
      <section className="page-command">
        <div>
          <div className="section-kicker">Intelligence</div>
          <h1 className="page-title">Strategy Room</h1>
          <p className="page-copy">Ask strategic questions, pressure-test competitors, and get decisions grounded in your numbers.</p>
        </div>
        {briefing && (
          <div className="rounded-[1.2rem] border border-slate-200 bg-white px-4 py-3 text-right shadow-sm">
            <div className="text-[0.66rem] font-semibold uppercase tracking-[0.18em] text-slate-400">Health</div>
            <div className="mt-1 text-3xl font-semibold text-slate-950">{briefing.health_score ?? '--'}</div>
            <div className="text-sm text-slate-500">{briefing.health_label || '--'}</div>
          </div>
        )}
      </section>

      {/* Proactive signals */}
      {!briefingLoading && briefing?.signals?.length > 0 && (
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {briefing.signals.map((sig, i) => {
            const style = SIGNAL_STYLES[sig.type] || SIGNAL_STYLES.opportunity
            return (
              <div key={i} className={`rounded-[1.5rem] border p-4 ${style.border} ${style.bg}`}>
                <div className="flex items-start justify-between gap-2">
                  <span className={`text-xl ${style.icon}`}>{sig.icon}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] ${style.badge}`}>
                    {SIGNAL_LABELS[sig.type]}
                  </span>
                </div>
                <div className="mt-2 text-sm font-semibold text-slate-900">{sig.title}</div>
                <div className="mt-1 text-xs leading-5 text-slate-600">{sig.detail}</div>
                <div className="mt-3 text-xs font-semibold text-slate-800">→ {sig.action}</div>
              </div>
            )
          })}
        </section>
      )}

      {/* Tabs */}
      <section className="card-surface overflow-hidden">
        <div className="border-b border-slate-100 px-6">
          <div className="flex gap-1">
            {TABS.map((tab, i) => (
              <button
                key={tab}
                onClick={() => setActiveTab(i)}
                className={`px-4 py-4 text-sm font-semibold transition-colors ${
                  activeTab === i
                    ? 'border-b-2 border-brand-600 text-brand-700'
                    : 'text-slate-500 hover:text-slate-900'
                }`}
              >
                {tab}
                {i > 0 && !isPro && (
                  <span className="ml-1.5 rounded-full border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-brand-600">Growth+</span>
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="p-6">
          {activeTab === 0 && <AIStrategistTab org={org} briefing={briefing} />}
          {activeTab === 1 && <CompetitorIntelTab isPro={isPro} />}
          {activeTab === 2 && <MarketPositionTab isPro={isPro} />}
        </div>
      </section>
    </div>
  )
}

// ── Tab 1: AI Strategist ────────────────────────────────────────────────────

function AIStrategistTab({ org, briefing }) {
  const [question, setQuestion] = useState('')
  const [conversation, setConversation] = useState([])
  const bottomRef = useRef(null)

  const ask = useMutation({
    mutationFn: (q) =>
      strategyApi.ask(q, conversation.map((m) => ({ role: m.role, content: m.content }))).then((r) => r.data),
    onSuccess: (data, question) => {
      setConversation((prev) => [
        ...prev,
        { role: 'user', content: question },
        { role: 'assistant', data },
      ])
      setQuestion('')
    },
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation])

  function submit(q) {
    const text = (q || question).trim()
    if (!text || ask.isPending) return
    ask.mutate(text)
  }

  const suggested = briefing?.suggested_questions || []

  return (
    <div className="space-y-4">
      <div>
        <div className="section-kicker">AI Strategist</div>
        <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Ask your data team anything</h2>
        <p className="mt-1 text-sm text-slate-500">Every answer uses your live business data — not generic advice.</p>
      </div>

      {/* Suggested questions */}
      {conversation.length === 0 && suggested.length > 0 && (
        <div>
          <div className="mb-3 text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-slate-400">Start with a question</div>
          <div className="flex flex-wrap gap-2">
            {suggested.map((q) => (
              <button
                key={q}
                onClick={() => submit(q)}
                disabled={ask.isPending}
                className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 transition-all hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700 disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Conversation */}
      {conversation.length > 0 && (
        <div className="space-y-4 max-h-[480px] overflow-y-auto pr-1">
          {conversation.map((msg, i) => (
            <div key={i}>
              {msg.role === 'user' ? (
                <div className="flex justify-end">
                  <div className="max-w-lg rounded-[1.25rem] rounded-tr-md bg-brand-600 px-4 py-3 text-sm text-white">
                    {msg.content}
                  </div>
                </div>
              ) : (
                <StrategistResponse data={msg.data} />
              )}
            </div>
          ))}
          {ask.isPending && (
            <div className="flex items-center gap-3 text-sm text-slate-400">
              <div className="flex gap-1">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-2 w-2 rounded-full bg-brand-400 animate-pulse" style={{ animationDelay: `${i * 150}ms` }} />
                ))}
              </div>
              <span>Strategist is analyzing your data…</span>
            </div>
          )}
          {ask.isError && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
              {ask.error?.response?.data?.detail || 'Strategy session failed. Make sure Ollama is running or your AI key is set.'}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      )}

      {/* Input */}
      <div className="flex gap-3">
        <input
          type="text"
          className="input flex-1"
          placeholder="Ask a strategic question about your business…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          disabled={ask.isPending}
        />
        <button
          onClick={() => submit()}
          disabled={!question.trim() || ask.isPending}
          className="btn-primary px-5"
        >
          {ask.isPending ? '⟳' : '→'}
        </button>
      </div>

      {/* Reset */}
      {conversation.length > 0 && (
        <button onClick={() => setConversation([])} className="text-xs text-slate-400 hover:text-slate-600">
          Clear conversation
        </button>
      )}
    </div>
  )
}

function StrategistResponse({ data }) {
  if (!data) return null
  return (
    <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50/80 p-5 space-y-4">
      {/* Key insight callout */}
      {data.key_insight && (
        <div className="rounded-[1.2rem] border border-brand-100 bg-brand-50/70 px-4 py-3">
          <div className="text-[0.65rem] font-semibold uppercase tracking-[0.2em] text-brand-600">Key Insight</div>
          <div className="mt-1 text-sm font-semibold text-slate-900">{data.key_insight}</div>
        </div>
      )}

      {/* Main answer */}
      {data.answer && (
        <div className="text-sm leading-7 text-slate-700 whitespace-pre-wrap">{data.answer}</div>
      )}

      {/* Actions */}
      {data.actions?.length > 0 && (
        <div className="space-y-2">
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-slate-400">Actions</div>
          {data.actions.map((action, i) => (
            <div key={i} className="flex gap-3 rounded-xl border border-slate-200 bg-white p-3.5">
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white shrink-0">{i + 1}</div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold text-slate-900">{action.action}</div>
                <div className="mt-1 flex flex-wrap gap-2">
                  {action.impact && (
                    <span className="text-xs text-emerald-700 font-semibold">{action.impact}</span>
                  )}
                  {action.timeframe && (
                    <span className="text-xs text-brand-600">⏱ {action.timeframe}</span>
                  )}
                  {action.effort && (
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${EFFORT_COLORS[action.effort] || ''}`}>
                      {action.effort} effort
                    </span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Follow-up suggestions */}
      {data.follow_up_questions?.length > 0 && (
        <div>
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-slate-400 mb-2">Ask next</div>
          <div className="flex flex-wrap gap-2">
            {data.follow_up_questions.map((q) => (
              <span key={q} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600">{q}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Tab 2: Competitor Intel ──────────────────────────────────────────────────

function CompetitorIntelTab({ isPro }) {
  const [searchQuery, setSearchQuery] = useState('')
  const [manualUrl, setManualUrl] = useState('')
  const [selectedUrls, setSelectedUrls] = useState([])
  const [searchResults, setSearchResults] = useState([])
  const [analysis, setAnalysis] = useState(null)

  const search = useMutation({
    mutationFn: (q) => strategyApi.searchCompetitors(q).then((r) => r.data),
    onSuccess: (data) => {
      setSearchResults(data.results || [])
      setSelectedUrls([])
      setAnalysis(null)
    },
  })

  const analyze = useMutation({
    mutationFn: (urls) => strategyApi.analyzeCompetitors(urls).then((r) => r.data),
    onSuccess: (data) => setAnalysis(data),
  })

  function toggleUrl(url) {
    setSelectedUrls((prev) =>
      prev.includes(url) ? prev.filter((u) => u !== url) : [...prev, url].slice(0, 4)
    )
  }

  function addManual() {
    const url = manualUrl.trim()
    if (!url) return
    if (!searchResults.find((r) => r.url === url)) {
      setSearchResults((prev) => [{ title: url, url, snippet: 'Added manually' }, ...prev])
    }
    toggleUrl(url)
    setManualUrl('')
  }

  if (!isPro) {
    return <ProGate feature="Competitor Intelligence" description="Search for competitors online, extract their pricing and services, and get a strategic analysis of your market position." />
  }

  return (
    <div className="space-y-5">
      <div>
        <div className="section-kicker">Competitor Intelligence</div>
        <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Find and analyze your competition</h2>
        <p className="mt-1 text-sm text-slate-500">Search by keyword or add competitor URLs. The AI will scrape their sites and compare against your data.</p>
      </div>

      {/* Search */}
      <div className="flex gap-3">
        <input
          type="text"
          className="input flex-1"
          placeholder="e.g. HVAC repair Austin TX  or  plumbing services Denver"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search.mutate(searchQuery)}
        />
        <button
          onClick={() => search.mutate(searchQuery)}
          disabled={search.isPending}
          className="btn-primary px-5 whitespace-nowrap"
        >
          {search.isPending ? '⟳ Searching…' : '⌕ Search Web'}
        </button>
      </div>

      {/* Manual URL */}
      <div className="flex gap-3">
        <input
          type="text"
          className="input flex-1"
          placeholder="Or paste a competitor URL directly (e.g. https://competitorsite.com)"
          value={manualUrl}
          onChange={(e) => setManualUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && addManual()}
        />
        <button onClick={addManual} disabled={!manualUrl.trim()} className="btn-secondary whitespace-nowrap">
          + Add URL
        </button>
      </div>

      {/* Search results */}
      {searchResults.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-slate-400">
              {searchResults.length} results — select up to 4 to analyze
            </div>
            {selectedUrls.length > 0 && (
              <button
                onClick={() => analyze.mutate(selectedUrls)}
                disabled={analyze.isPending}
                className="btn-primary text-sm px-4 py-2"
              >
                {analyze.isPending ? '⟳ Analyzing…' : `✦ Analyze ${selectedUrls.length} competitor${selectedUrls.length > 1 ? 's' : ''}`}
              </button>
            )}
          </div>

          {searchResults.map((result, i) => {
            const selected = selectedUrls.includes(result.url)
            return (
              <button
                key={i}
                onClick={() => toggleUrl(result.url)}
                className={`w-full rounded-[1.25rem] border p-4 text-left transition-all ${
                  selected
                    ? 'border-brand-400 bg-brand-50/70'
                    : 'border-slate-200 bg-white hover:border-brand-200 hover:bg-brand-50/30'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-semibold text-slate-900 truncate">{result.title}</div>
                    <div className="mt-0.5 text-xs text-brand-600 truncate">{result.url}</div>
                    {result.snippet && (
                      <div className="mt-1.5 text-xs leading-5 text-slate-500 line-clamp-2">{result.snippet}</div>
                    )}
                  </div>
                  <div className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${
                    selected ? 'border-brand-600 bg-brand-600' : 'border-slate-300'
                  }`}>
                    {selected && <div className="h-2 w-2 rounded-full bg-white" />}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      )}

      {search.isError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          Web search failed. Check your internet connection or add competitor URLs manually.
        </div>
      )}

      {/* Analysis result */}
      {analyze.isPending && (
        <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50/80 p-6 text-center text-sm text-slate-400 space-y-2">
          <div className="flex justify-center gap-1.5">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-2.5 w-2.5 rounded-full bg-brand-400 animate-pulse" style={{ animationDelay: `${i * 200}ms` }} />
            ))}
          </div>
          <div>Fetching competitor pages and running analysis… this takes 20–40 seconds.</div>
        </div>
      )}

      {analysis && !analyze.isPending && <CompetitorAnalysisDisplay analysis={analysis} />}
    </div>
  )
}

function CompetitorAnalysisDisplay({ analysis }) {
  const pricing = analysis.pricing_position || {}
  const pricingColor = {
    above_market: 'text-emerald-700 bg-emerald-50 border-emerald-200',
    at_market:    'text-amber-700 bg-amber-50 border-amber-100',
    below_market: 'text-rose-700 bg-rose-50 border-rose-200',
    unclear:      'text-slate-600 bg-slate-50 border-slate-200',
  }[pricing.assessment] || 'text-slate-600 bg-slate-50'

  return (
    <div className="space-y-4">
      {/* Market summary */}
      {analysis.market_summary && (
        <div className="rounded-[1.5rem] border border-brand-100 bg-brand-50/60 p-5">
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-brand-700">Market Position Summary</div>
          <p className="mt-2 text-sm leading-7 text-slate-800">{analysis.market_summary}</p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Pricing position */}
        <div className="rounded-[1.35rem] border border-slate-200 bg-white p-5">
          <div className="section-kicker mb-3">Pricing Position</div>
          {pricing.assessment && (
            <div className={`inline-flex rounded-full border px-3 py-1 text-xs font-bold uppercase tracking-wide mb-3 ${pricingColor}`}>
              {pricing.assessment.replace('_', ' ')}
            </div>
          )}
          <p className="text-sm leading-6 text-slate-700">{pricing.explanation || '—'}</p>
          {pricing.competitor_price_signals?.length > 0 && (
            <div className="mt-3 space-y-1">
              <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Price signals found</div>
              {pricing.competitor_price_signals.map((sig, i) => (
                <div key={i} className="text-xs text-slate-600 leading-5">• {sig}</div>
              ))}
            </div>
          )}
        </div>

        {/* Competitive advantages */}
        <div className="rounded-[1.35rem] border border-emerald-100 bg-emerald-50/60 p-5">
          <div className="section-kicker mb-3">Your Advantages</div>
          {(analysis.competitive_advantages || []).map((adv, i) => (
            <div key={i} className="flex gap-2 mb-2 text-sm text-slate-800">
              <span className="text-emerald-600 mt-0.5">✓</span>
              <span>{adv}</span>
            </div>
          ))}
          {(!analysis.competitive_advantages || analysis.competitive_advantages.length === 0) && (
            <div className="text-sm text-slate-500">Not enough data from competitor pages to assess.</div>
          )}
        </div>
      </div>

      {/* Service gaps */}
      {analysis.service_gaps?.length > 0 && (
        <div className="rounded-[1.35rem] border border-amber-100 bg-amber-50/60 p-5">
          <div className="section-kicker mb-3">Service Gaps (what competitors offer you may not)</div>
          <div className="space-y-3">
            {analysis.service_gaps.map((gap, i) => (
              <div key={i} className="rounded-xl border border-amber-100 bg-white p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm font-semibold text-slate-900">{gap.gap}</div>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide shrink-0 ${
                    gap.priority === 'high' ? 'bg-rose-100 text-rose-700' :
                    gap.priority === 'medium' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600'
                  }`}>{gap.priority}</span>
                </div>
                <div className="mt-1 text-xs leading-5 text-slate-600">{gap.opportunity}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Strategic moves */}
      {analysis.strategic_moves?.length > 0 && (
        <div className="rounded-[1.35rem] border border-slate-200 bg-white p-5">
          <div className="section-kicker mb-3">Strategic Moves</div>
          <div className="space-y-3">
            {analysis.strategic_moves.map((move, i) => (
              <div key={i} className="flex gap-4 rounded-xl border border-slate-100 bg-slate-50/80 p-4">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white">
                  {i + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-slate-900">{move.move}</div>
                  <div className="mt-1 text-xs leading-5 text-slate-600">{move.reasoning}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {move.estimated_impact && (
                      <span className="text-xs font-semibold text-emerald-700">{move.estimated_impact}</span>
                    )}
                    {move.timeframe && (
                      <span className="text-xs text-brand-600">⏱ {move.timeframe}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Threat + Opportunity */}
      {(analysis.biggest_threat || analysis.biggest_opportunity) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {analysis.biggest_threat && (
            <div className="rounded-[1.35rem] border border-rose-100 bg-rose-50/60 p-4">
              <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-rose-700">Biggest Threat</div>
              <p className="mt-2 text-sm leading-6 text-slate-800">{analysis.biggest_threat}</p>
            </div>
          )}
          {analysis.biggest_opportunity && (
            <div className="rounded-[1.35rem] border border-emerald-100 bg-emerald-50/60 p-4">
              <div className="text-[0.68rem] font-semibold uppercase tracking-[0.2em] text-emerald-700">Biggest Opportunity</div>
              <p className="mt-2 text-sm leading-6 text-slate-800">{analysis.biggest_opportunity}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Tab 3: Market Position ──────────────────────────────────────────────────

function MarketPositionTab({ isPro }) {
  const [run, setRun] = useState(false)
  const [competitorUrls, setCompetitorUrls] = useState('')

  const analyze = useMutation({
    mutationFn: (urls) => strategyApi.analyzeCompetitors(urls).then((r) => r.data),
  })

  if (!isPro) {
    return <ProGate feature="Market Position Analysis" description="Get an AI-generated analysis of your market position, pricing strategy, and competitive gaps based on real competitor data." />
  }

  if (!run) {
    return (
      <div className="space-y-5">
        <div>
          <div className="section-kicker">Market Position</div>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Where do you stand in your market?</h2>
          <p className="mt-1 text-sm text-slate-500">
            Add 2–4 competitor URLs (from the Competitor Intel tab or your own research) and we'll run a full comparative analysis against your live business data.
          </p>
        </div>
        <div className="rounded-[1.5rem] border border-dashed border-slate-200 bg-slate-50/80 p-6 space-y-4">
          <div className="text-sm font-semibold text-slate-700">Paste competitor URLs (one per line)</div>
          <textarea
            className="input h-32 resize-none font-mono text-xs"
            placeholder={"https://competitor1.com\nhttps://competitor2.com\nhttps://competitor3.com"}
            value={competitorUrls}
            onChange={(e) => setCompetitorUrls(e.target.value)}
          />
          <button
            onClick={() => {
              const urls = competitorUrls.split('\n').map((u) => u.trim()).filter(Boolean)
              if (urls.length > 0) {
                setRun(true)
                analyze.mutate(urls)
              }
            }}
            disabled={!competitorUrls.trim()}
            className="btn-primary"
          >
            ✦ Run Market Position Analysis
          </button>
        </div>
        <div className="rounded-[1.35rem] border border-brand-100 bg-brand-50/50 p-4 text-sm leading-6 text-slate-700">
          <strong>Tip:</strong> Use the <em>Competitor Intel</em> tab to search for competitors first, then paste their URLs here for a full comparative analysis.
        </div>
      </div>
    )
  }

  if (analyze.isPending) {
    return (
      <div className="py-12 text-center space-y-3">
        <div className="flex justify-center gap-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-3 w-3 rounded-full bg-brand-400 animate-pulse" style={{ animationDelay: `${i * 150}ms` }} />
          ))}
        </div>
        <div className="text-sm text-slate-500">Fetching competitor pages and building your market position analysis…</div>
        <div className="text-xs text-slate-400">This usually takes 20–40 seconds.</div>
      </div>
    )
  }

  if (analyze.data) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="section-kicker">Market Position</div>
            <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Your competitive landscape</h2>
          </div>
          <button onClick={() => { setRun(false); setCompetitorUrls('') }} className="btn-secondary text-sm">
            Run new analysis
          </button>
        </div>
        <CompetitorAnalysisDisplay analysis={analyze.data} />
      </div>
    )
  }

  if (analyze.isError) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
          Analysis failed: {analyze.error?.response?.data?.detail || 'Unknown error'}
        </div>
        <button onClick={() => setRun(false)} className="btn-secondary">Try again</button>
      </div>
    )
  }

  return null
}

// ── Shared components ────────────────────────────────────────────────────────

function ProGate({ feature, description }) {
  return (
    <div className="py-10 text-center space-y-4">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-brand-50 text-3xl">✦</div>
      <h2 className="text-lg font-semibold text-slate-950">{feature}</h2>
      <p className="text-sm text-slate-500 max-w-sm mx-auto leading-6">{description}</p>
      <p className="text-xs text-slate-400">Available on Growth ($129/mo) and above.</p>
      <Link to="/app/billing" className="btn-primary mx-auto inline-flex">See plans →</Link>
    </div>
  )
}
