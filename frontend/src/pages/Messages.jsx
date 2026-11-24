import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useUser } from '@clerk/clerk-react'
import { messagesApi } from '../lib/api'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EMOJI_CATEGORIES = {
  'Smileys': ['😀','😃','😄','😁','😅','😂','🤣','😊','😇','🙂','😉','😍','🤩','😎','🤔','😐','🙄','😏','😒','😔','😢','😭','😤','😡','🥺'],
  'Gestures': ['👍','👎','👌','✌️','🤞','👊','✊','💪','🙌','👏','🙏','🤝','✋','👋','🤚','💅','🤙','☝️','👆','👇'],
  'Business': ['📊','📈','📉','💰','💵','💸','🏆','🎯','🔥','⚡','✅','❌','⚠️','🔔','📌','📎','🗓️','⏰','🔒','🔑','📧','📱','💻','🖥️','📁'],
  'Symbols': ['❤️','🧡','💛','💚','💙','💜','🤍','💯','✨','⭐','🌟','💫','🎉','🎊','🚀','💡','🔮','🎯','🏅','🥇'],
}

const QUICK_REACTIONS = ['👍','❤️','😂','🎉','🔥','✅']

const FILE_ICONS = {
  pdf: '📄', docx: '📝', xlsx: '📊', pptx: '📑',
  csv: '📋', image: '🖼', video: '🎬', text: '📃', other: '📎',
}

const CHANNEL_TYPE_ICON = {
  team: '◇',
  ai_assistant: '✦',
  announcements: '◉',
}

const CHART_COLORS = ['#0f172a', '#0ea5e9', '#f59e0b', '#10b981', '#ef4444', '#6366f1', '#14b8a6', '#a855f7']

const OFFICE_PULSE = [
  { label: 'Mode', value: 'Ad hoc answers', detail: 'Questions go into rooms; specialist bots bring back analysis.' },
  { label: 'Data boundary', value: 'Connected only', detail: 'Bots answer from CRM, revenue, expense, customer, and connector data.' },
  { label: 'Cadence', value: '5 sec refresh', detail: 'Live enough for launch, simple enough to trust.' },
]

const DEPLOYMENT_CHECKLIST = [
  { label: 'Org-scoped rooms', status: 'Ready', tone: 'good' },
  { label: 'Message rate limits', status: 'Ready', tone: 'good' },
  { label: 'AI assistant limits', status: 'Ready', tone: 'good' },
  { label: 'File vault bucket', status: 'Needs Supabase bucket', tone: 'watch' },
  { label: 'XLSX channel export', status: 'Ready', tone: 'good' },
  { label: 'Specialist AI mentions', status: 'Ready', tone: 'good' },
]

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diff = now - d
  if (diff < 60_000) return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatBytes(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getInitials(name) {
  return (name || 'U').split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()
}

function channelLabel(channel) {
  if (!channel) return 'room'
  return channel.name?.replaceAll('-', ' ') || 'room'
}

function channelKicker(type) {
  if (type === 'ai_assistant') return 'Operator AI'
  if (type === 'announcements') return 'Company wire'
  return 'Team room'
}

function formatChartValue(value, format) {
  const number = Number(value || 0)
  if (format === 'currency') return `$${number.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (format === 'percent') return `${number.toFixed(1)}%`
  return number.toLocaleString('en-US', { maximumFractionDigits: 1 })
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function EmojiPicker({ onSelect, onClose }) {
  const [category, setCategory] = useState('Smileys')
  return (
    <div className="absolute bottom-full left-0 mb-2 bg-[#1a2438] border border-white/10 rounded-xl shadow-2xl p-3 w-72 z-50">
      <div className="flex items-center justify-between mb-2">
        <div className="flex gap-1 flex-wrap">
          {Object.keys(EMOJI_CATEGORIES).map(cat => (
            <button key={cat} onClick={() => setCategory(cat)}
              className={`px-2 py-0.5 rounded text-[11px] transition-colors ${category === cat ? 'bg-blue-500/30 text-blue-300' : 'text-white/50 hover:text-white hover:bg-white/8'}`}>
              {cat}
            </button>
          ))}
        </div>
        <button onClick={onClose} className="text-white/30 hover:text-white ml-2 text-sm">✕</button>
      </div>
      <div className="grid grid-cols-8 gap-0.5 max-h-40 overflow-y-auto">
        {EMOJI_CATEGORIES[category].map(emoji => (
          <button key={emoji} onClick={() => { onSelect(emoji); onClose() }}
            className="p-1 hover:bg-white/10 rounded text-lg leading-none transition-colors">
            {emoji}
          </button>
        ))}
      </div>
    </div>
  )
}

function GifPicker({ onSelect, onClose }) {
  const [query, setQuery] = useState('')
  const [gifs, setGifs] = useState([])
  const [loading, setLoading] = useState(false)
  const TENOR_KEY = import.meta.env.VITE_TENOR_API_KEY
  const debounceRef = useRef(null)

  const fetchGifs = useCallback(async (q) => {
    if (!TENOR_KEY) return
    setLoading(true)
    try {
      const endpoint = q.trim()
        ? `https://tenor.googleapis.com/v2/search?q=${encodeURIComponent(q)}&key=${TENOR_KEY}&limit=16&media_filter=gif`
        : `https://tenor.googleapis.com/v2/featured?key=${TENOR_KEY}&limit=16&media_filter=gif`
      const res = await fetch(endpoint)
      const data = await res.json()
      setGifs(data.results || [])
    } catch {}
    setLoading(false)
  }, [TENOR_KEY])

  useEffect(() => { fetchGifs('') }, [fetchGifs])

  const handleQuery = (val) => {
    setQuery(val)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchGifs(val), 400)
  }

  const gifUrl = (gif) =>
    gif.media_formats?.gif?.url ||
    gif.media_formats?.tinygif?.url ||
    gif.media_formats?.mediumgif?.url || ''

  return (
    <div className="absolute bottom-full left-0 mb-2 bg-[#1a2438] border border-white/10 rounded-xl shadow-2xl p-3 w-80 z-50">
      <div className="flex items-center gap-2 mb-2">
        <input value={query} onChange={e => handleQuery(e.target.value)}
          placeholder="Search GIFs..." autoFocus
          className="flex-1 bg-white/8 border border-white/10 rounded-lg px-3 py-1.5 text-white text-sm placeholder-white/30 focus:outline-none focus:border-blue-400/50" />
        <button onClick={onClose} className="text-white/40 hover:text-white text-sm">✕</button>
      </div>
      {!TENOR_KEY && (
        <p className="text-white/40 text-xs text-center py-6">
          Set <code className="text-blue-300">VITE_TENOR_API_KEY</code> to enable GIFs.
        </p>
      )}
      {TENOR_KEY && (
        <div className="grid grid-cols-2 gap-1 max-h-56 overflow-y-auto">
          {loading && <p className="col-span-2 text-center text-white/40 py-4 text-sm">Loading...</p>}
          {!loading && gifs.length === 0 && <p className="col-span-2 text-center text-white/40 py-4 text-sm">No GIFs found.</p>}
          {!loading && gifs.map(gif => (
            <button key={gif.id} onClick={() => { onSelect(gifUrl(gif)); onClose() }}
              className="rounded-lg overflow-hidden hover:opacity-80 transition-opacity border border-white/5">
              <img src={gifUrl(gif)} alt={gif.title} className="w-full h-24 object-cover" loading="lazy" />
            </button>
          ))}
        </div>
      )}
      <p className="text-white/20 text-[10px] text-center mt-2">Powered by Tenor</p>
    </div>
  )
}

function FileAttachment({ file, onDownload }) {
  const icon = FILE_ICONS[file.file_type] || FILE_ICONS.other
  return (
    <div className="inline-flex items-center gap-2 bg-white border border-slate-200 rounded-xl px-3 py-2 mt-2 max-w-xs hover:bg-slate-50 transition-colors cursor-pointer group shadow-sm"
      onClick={() => onDownload(file.id, file.filename)}>
      <span className="text-lg">{icon}</span>
      <div className="min-w-0">
        <p className="text-xs font-medium text-slate-800 truncate">{file.filename}</p>
        <p className="text-[10px] text-slate-400">{formatBytes(file.file_size)}</p>
      </div>
      <span className="text-slate-300 group-hover:text-slate-600 text-xs ml-1">↓</span>
    </div>
  )
}

function AnalyticsTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-2xl border border-slate-200 bg-white/95 px-4 py-3 text-xs shadow-[0_18px_50px_-28px_rgba(15,23,42,0.45)] backdrop-blur">
      <div className="mb-2 font-semibold text-slate-950">{label}</div>
      {payload.map((item) => (
        <div key={item.dataKey || item.name} className="flex items-center justify-between gap-6 py-0.5 text-slate-600">
          <span>{item.name}</span>
          <span className="font-semibold text-slate-950">{formatChartValue(item.value, item.payload?.__format?.[item.dataKey] || item.format)}</span>
        </div>
      ))}
    </div>
  )
}

function AnalyticsLayer({ analytics }) {
  const charts = analytics?.charts || []
  if (!charts.length) return null
  return (
    <div className="mt-4 rounded-[1.5rem] border border-sky-200/80 bg-white/85 p-4 shadow-[0_20px_60px_-42px_rgba(14,165,233,0.55)]">
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-[0.66rem] font-semibold uppercase tracking-[0.22em] text-sky-700/70">Analytics Layer</div>
          <div className="mt-1 text-sm font-semibold text-slate-950">Generated from connected LBT OS data</div>
        </div>
        <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-sky-700">
          {analytics.bot ? `@${analytics.bot}` : 'Bot'}
        </span>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {charts.map((chart) => (
          <AnalyticsChart key={chart.id || chart.title} chart={chart} />
        ))}
      </div>
      {(analytics.notes || []).length > 0 && (
        <div className="mt-3 rounded-2xl bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-500">
          {analytics.notes.join(' ')}
        </div>
      )}
    </div>
  )
}

function AnalyticsChart({ chart }) {
  const data = (chart.data || []).map((row) => ({
    ...row,
    __format: Object.fromEntries((chart.series || []).map((series) => [series.key, series.format])),
  }))
  if (!data.length) return null

  return (
    <div className="rounded-[1.25rem] border border-slate-200 bg-white p-4">
      <div className="mb-3">
        <h4 className="text-sm font-semibold text-slate-950">{chart.title}</h4>
        {chart.subtitle && <p className="mt-1 text-xs leading-5 text-slate-500">{chart.subtitle}</p>}
      </div>
      <div className="h-64">
        {chart.type === 'pie' ? <AnalyticsPie chart={chart} data={data} /> : null}
        {chart.type === 'bar' ? <AnalyticsBar chart={chart} data={data} /> : null}
        {chart.type === 'line' ? <AnalyticsLine chart={chart} data={data} /> : null}
      </div>
    </div>
  )
}

function AnalyticsBar({ chart, data }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis dataKey={chart.xKey} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={54} tickFormatter={(value) => formatChartValue(value, chart.series?.[0]?.format)} />
        <Tooltip content={<AnalyticsTooltip />} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {(chart.series || []).map((series, idx) => (
          <Bar key={series.key} dataKey={series.key} name={series.name} fill={CHART_COLORS[idx % CHART_COLORS.length]} radius={[8, 8, 3, 3]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

function AnalyticsLine({ chart, data }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 12, left: -18, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
        <XAxis dataKey={chart.xKey} tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} width={54} tickFormatter={(value) => formatChartValue(value, chart.series?.[0]?.format)} />
        <Tooltip content={<AnalyticsTooltip />} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {(chart.series || []).map((series, idx) => (
          <Line
            key={series.key}
            type="monotone"
            dataKey={series.key}
            name={series.name}
            stroke={CHART_COLORS[idx % CHART_COLORS.length]}
            strokeWidth={2.5}
            dot={{ r: 4, strokeWidth: 2, fill: '#ffffff' }}
            activeDot={{ r: 6 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

function AnalyticsPie({ chart, data }) {
  const total = data.reduce((sum, row) => sum + Number(row[chart.valueKey] || 0), 0)
  return (
    <div className="grid h-full grid-cols-[1fr_0.9fr] items-center gap-3">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} dataKey={chart.valueKey} nameKey={chart.nameKey} innerRadius="58%" outerRadius="86%" paddingAngle={3} stroke="none">
            {data.map((entry, idx) => (
              <Cell key={entry[chart.nameKey]} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(value) => formatChartValue(value, chart.format)} contentStyle={{ borderRadius: 16, border: '1px solid #e2e8f0', fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
      <div className="space-y-2">
        {data.slice(0, 6).map((row, idx) => (
          <div key={row[chart.nameKey]} className="flex items-center justify-between gap-2 text-xs">
            <div className="flex min-w-0 items-center gap-2">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: CHART_COLORS[idx % CHART_COLORS.length] }} />
              <span className="truncate font-medium text-slate-700">{row[chart.nameKey]}</span>
            </div>
            <span className="font-semibold text-slate-950">{total ? Math.round((Number(row[chart.valueKey] || 0) / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function MessageItem({ msg, currentUserId, onReact, onDownload }) {
  const [hover, setHover] = useState(false)
  const isAI = msg.sender_id === 'ai_assistant' || String(msg.sender_id || '').startsWith('bot_')
  const isSelf = msg.sender_id === currentUserId
  const files = msg.message_files || msg.files || []

  return (
    <div
      className={`group flex items-start gap-3 rounded-[1.35rem] px-3 py-3 transition-all ${
        isAI
          ? 'border border-sky-200/60 bg-sky-50/80 text-slate-950'
          : isSelf
            ? 'border border-amber-100 bg-amber-50/70'
            : 'border border-transparent hover:border-slate-200 hover:bg-white/70'
      }`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* Avatar */}
      <div className={`h-9 w-9 rounded-2xl flex-shrink-0 flex items-center justify-center text-xs font-bold shadow-sm ${
        isAI ? 'bg-sky-600 text-white text-base' : isSelf ? 'bg-amber-500 text-white' : 'bg-slate-900 text-white'
      }`}>
        {isAI ? (String(msg.sender_id || '').startsWith('bot_') ? msg.sender_name?.split(' ')?.[0]?.slice(0, 3) || 'AI' : '✦') : getInitials(msg.sender_name)}
      </div>

      {/* Body */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`text-sm font-semibold ${isAI ? 'text-sky-800' : isSelf ? 'text-amber-900' : 'text-slate-950'}`}>
            {isAI ? msg.sender_name || 'Answer Bot' : msg.sender_name}
          </span>
          <span className="text-[11px] text-slate-400">{formatTime(msg.created_at)}</span>
          {isAI && (
            <span className="text-[10px] bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded-full border border-sky-200">AI</span>
          )}
        </div>

        {msg.message_type === 'gif' ? (
          <img src={msg.gif_url} alt="GIF" className="max-h-48 rounded-xl border border-slate-200" />
        ) : (
          <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap break-words">{msg.content}</p>
        )}

        {files.map(f => (
          <FileAttachment key={f.id} file={f} onDownload={onDownload} />
        ))}

        <AnalyticsLayer analytics={msg.analytics} />

        {/* Reactions display */}
        {Object.keys(msg.reactions || {}).length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {Object.entries(msg.reactions).map(([emoji, users]) => (
              <button key={emoji} onClick={() => onReact(msg.id, emoji)}
                className={`flex items-center gap-1 text-xs rounded-full px-2 py-0.5 border transition-colors ${
                  users.includes(currentUserId)
                    ? 'bg-sky-100 border-sky-200 text-sky-800'
                    : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                }`}>
                <span>{emoji}</span>
                <span className="text-slate-400">{users.length}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Quick reaction bar on hover */}
      {hover && (
        <div className="flex gap-0.5 flex-shrink-0">
          {QUICK_REACTIONS.map(emoji => (
            <button key={emoji} onClick={() => onReact(msg.id, emoji)}
              className="p-1 hover:bg-slate-100 rounded-lg text-sm transition-colors" title={emoji}>
              {emoji}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function OfficePulseRail({ activeChannel, messageCount, bots, onMentionBot }) {
  const roomName = channelLabel(activeChannel)
  return (
    <aside className="hidden w-80 shrink-0 flex-col border-l border-amber-100/80 bg-[linear-gradient(180deg,#fff7ed_0%,#ffffff_46%,#eff6ff_100%)] xl:flex">
      <div className="border-b border-amber-100 px-5 py-5">
        <div className="text-[0.68rem] font-semibold uppercase tracking-[0.24em] text-amber-700/70">Answer Pulse</div>
        <h2 className="mt-2 text-xl font-semibold tracking-tight text-slate-950">Ad hoc requests land here. Bots bring the answer back.</h2>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          Signal Desk turns connected CRM and operating data into channel-native answers your whole team can see.
        </p>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        <section>
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Active Room</div>
          <div className="mt-3 rounded-[1.5rem] border border-slate-200 bg-white/80 p-4 shadow-[0_18px_45px_-34px_rgba(15,23,42,0.45)]">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-950 text-white">
                {CHANNEL_TYPE_ICON[activeChannel?.channel_type] || '◇'}
              </div>
              <div>
                <div className="text-sm font-semibold capitalize text-slate-950">{roomName}</div>
                <div className="text-xs text-slate-500">{channelKicker(activeChannel?.channel_type)}</div>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <div className="rounded-2xl bg-slate-50 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">Messages</div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">{messageCount}</div>
              </div>
              <div className="rounded-2xl bg-amber-50 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-amber-700/60">Mode</div>
                <div className="mt-1 text-sm font-semibold text-amber-900">Launch</div>
              </div>
            </div>
          </div>
        </section>

        <section>
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Friday Brief</div>
          <div className="mt-3 space-y-2">
            {OFFICE_PULSE.map((item) => (
              <div key={item.label} className="rounded-[1.25rem] border border-slate-200 bg-white/75 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{item.label}</span>
                  <span className="text-sm font-semibold text-slate-950">{item.value}</span>
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">{item.detail}</p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Specialist Bots</div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {(bots || []).slice(0, 12).map((bot) => (
              <button
                key={bot.key}
                onClick={() => onMentionBot(bot.key)}
                className="rounded-[1.1rem] border border-slate-200 bg-white/80 p-3 text-left transition hover:border-amber-200 hover:bg-amber-50"
                title={bot.scope}
              >
                <div className="text-sm font-semibold text-slate-950">@{bot.key}</div>
                <div className="mt-1 truncate text-[11px] text-slate-500">{bot.title}</div>
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-400">Mention a bot in any room, like `@REVOPS forecast staffing for next month`.</p>
        </section>

        <section>
          <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">Deployment Readiness</div>
          <div className="mt-3 rounded-[1.5rem] border border-slate-200 bg-white/80 p-4">
            <div className="space-y-3">
              {DEPLOYMENT_CHECKLIST.map((item) => (
                <div key={item.label} className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-slate-800">{item.label}</div>
                    <div className="mt-0.5 text-xs text-slate-400">{item.status}</div>
                  </div>
                  <span className={`mt-0.5 h-2.5 w-2.5 rounded-full ${item.tone === 'good' ? 'bg-emerald-500' : 'bg-amber-400'}`} />
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </aside>
  )
}

export function NewChannelModal({ onClose, onCreate }) {
  const [name, setName] = useState('')
  const [type, setType] = useState('team')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name.trim()) return
    setLoading(true)
    setError('')
    try {
      await onCreate({ name, channel_type: type, description: desc || undefined })
      onClose()
    } catch (err) {
      // TW-1322: this catch used to swallow the failure whole — the modal
      // just sat there. Surface the reason; the entries stay put.
      setError(err?.response?.data?.detail || 'Could not open the room. Your entries are still here. Try again.')
    }
    setLoading(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-white border border-slate-200 rounded-[1.7rem] w-full max-w-md p-6 shadow-2xl">
        <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-amber-700/70">Signal Desk</div>
        <h3 className="mt-2 text-slate-950 font-semibold text-lg mb-4">Open a room</h3>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5">Room name</label>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. quote-room"
              className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-950 text-sm placeholder-slate-400 focus:outline-none focus:border-amber-300" />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5">Type</label>
            <select value={type} onChange={e => setType(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-950 text-sm focus:outline-none focus:border-amber-300">
              <option value="team">Team room</option>
              <option value="announcements">Company wire</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1.5">Description (optional)</label>
            <input value={desc} onChange={e => setDesc(e.target.value)}
              placeholder="What should this room keep moving?"
              className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-950 text-sm placeholder-slate-400 focus:outline-none focus:border-amber-300" />
          </div>
          {error && (
            <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          )}
          <div className="flex gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="flex-1 px-4 py-2 rounded-xl border border-slate-200 text-slate-500 hover:text-slate-950 hover:bg-slate-50 text-sm transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={!name.trim() || loading}
              className="flex-1 px-4 py-2 rounded-xl bg-slate-950 hover:bg-slate-800 disabled:opacity-40 text-white text-sm font-medium transition-colors">
              {loading ? 'Opening...' : 'Open room'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function Messages() {
  const { user } = useUser()
  const qc = useQueryClient()
  const bottomRef = useRef(null)
  const fileInputRef = useRef(null)
  const textareaRef = useRef(null)

  const [activeChannelId, setActiveChannelId] = useState(null)
  const [draft, setDraft] = useState('')
  const [gifPreview, setGifPreview] = useState(null)
  const [pendingFiles, setPendingFiles] = useState([])  // [{id, filename, file_type, file_size}]
  const [aiMode, setAiMode] = useState(false)
  const [showEmoji, setShowEmoji] = useState(false)
  const [showGif, setShowGif] = useState(false)
  const [showNewChannel, setShowNewChannel] = useState(false)
  const [uploadingFile, setUploadingFile] = useState(false)
  const [uploadError, setUploadError] = useState('')

  const { data: botCatalog } = useQuery({
    queryKey: ['message-bots'],
    queryFn: () => messagesApi.bots().then(r => r.data),
  })
  const bots = botCatalog?.bots || []

  // Channels
  const { data: channels = [] } = useQuery({
    queryKey: ['message-channels'],
    queryFn: () => messagesApi.channels().then(r => r.data),
    onSuccess: (data) => {
      if (!activeChannelId && data.length > 0) setActiveChannelId(data[0].id)
    },
  })

  useEffect(() => {
    if (!activeChannelId && channels.length > 0) setActiveChannelId(channels[0].id)
  }, [channels, activeChannelId])

  const activeChannel = channels.find(c => c.id === activeChannelId)

  // Messages — poll every 5 seconds
  const { data: messagesData } = useQuery({
    queryKey: ['messages', activeChannelId],
    queryFn: () => messagesApi.getMessages(activeChannelId).then(r => r.data),
    enabled: !!activeChannelId,
    refetchInterval: 5000,
  })
  const messageList = messagesData?.messages || []

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messageList.length])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 128) + 'px'
  }, [draft])

  // Mutations
  const sendMutation = useMutation({
    mutationFn: (payload) => {
      if (aiMode) return messagesApi.askAI(activeChannelId, payload.content)
      return messagesApi.sendMessage(activeChannelId, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries(['messages', activeChannelId])
      setDraft('')
      setGifPreview(null)
      setPendingFiles([])
      setAiMode(false)
    },
  })

  const reactMutation = useMutation({
    mutationFn: ({ messageId, emoji }) => messagesApi.react(messageId, emoji),
    onSuccess: () => qc.invalidateQueries(['messages', activeChannelId]),
  })

  const createChannelMutation = useMutation({
    mutationFn: (body) => messagesApi.createChannel(body),
    onSuccess: () => qc.invalidateQueries(['message-channels']),
  })

  const handleSend = () => {
    const content = draft.trim()
    if (!content && !gifPreview && pendingFiles.length === 0) return
    // TW-1322: mutations expose isPending in React Query v5 — the old
    // isLoading check was always undefined, so this guard never fired.
    if (sendMutation.isPending) return

    sendMutation.mutate({
      content,
      message_type: gifPreview ? 'gif' : 'text',
      gif_url: gifPreview || undefined,
      file_ids: pendingFiles.map(f => f.id),
      sender_name: user?.fullName || user?.firstName || user?.id?.slice(0, 8) || 'User',
    })
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleFileSelect = async (e) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    setUploadingFile(true)
    setUploadError('')
    for (const file of files.slice(0, 5)) {
      try {
        const res = await messagesApi.uploadFile(activeChannelId, file)
        setPendingFiles(prev => [...prev, res.data])
      } catch {
        // TW-1322: name the file that failed — a silent skip left the user
        // believing the attachment was part of the message.
        setUploadError(`${file.name} did not upload. Try attaching it again.`)
      }
    }
    setUploadingFile(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleDownload = async (fileId, filename) => {
    try {
      const res = await messagesApi.getFileUrl(fileId)
      const url = res.data?.url
      if (url) {
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        a.target = '_blank'
        a.rel = 'noopener noreferrer'
        a.click()
      }
    } catch {}
  }

  const handleExport = async () => {
    try {
      const res = await messagesApi.exportXlsx(activeChannelId)
      const blob = new Blob([res.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${activeChannel?.name || 'channel'}-export.xlsx`
      a.click()
      URL.revokeObjectURL(url)
    } catch {}
  }

  const handleEmojiSelect = (emoji) => setDraft(prev => prev + emoji)
  const handleGifSelect = (url) => { setGifPreview(url); setShowGif(false) }
  const handleMentionBot = (key) => {
    setDraft(prev => {
      const prefix = prev.trim() ? `${prev.trim()} ` : ''
      return `${prefix}@${key} `
    })
    textareaRef.current?.focus()
  }

  return (
    <div className="flex h-[calc(100vh-0px)] overflow-hidden bg-[linear-gradient(135deg,#f8fafc_0%,#fff7ed_45%,#eff6ff_100%)] text-slate-950">

      {/* ── Channel sidebar ── */}
      <aside className="w-68 flex w-[17rem] flex-shrink-0 flex-col border-r border-slate-200/80 bg-slate-950 text-white">
        <div className="border-b border-white/10 px-5 py-5">
          <div className="inline-flex rounded-full border border-amber-300/20 bg-amber-300/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-amber-100/75">
            AI Answer Hub
          </div>
          <h2 className="mt-4 text-xl font-semibold tracking-tight text-white">Signal Desk</h2>
          <p className="mt-1 text-xs leading-5 text-white/45">Rooms, specialist bots, connected-data answers.</p>
        </div>

        <nav className="flex-1 overflow-y-auto py-3 px-2">
          {['team','announcements'].map(type => {
            const group = channels.filter(c => c.channel_type === type)
            if (group.length === 0) return null
            return (
              <div key={type} className="mb-3">
                <p className="text-[10px] font-semibold text-white/30 uppercase tracking-wider px-2 mb-1">
                  {type === 'team' ? 'Rooms' : 'Company Wire'}
                </p>
                {group.map(ch => (
                  <button key={ch.id} onClick={() => setActiveChannelId(ch.id)}
                    className={`w-full text-left px-3 py-2.5 rounded-xl flex items-center gap-2 transition-colors text-sm ${
                      activeChannelId === ch.id ? 'bg-white/12 text-white shadow-[0_16px_30px_-24px_rgba(255,255,255,0.7)]' : 'text-white/55 hover:bg-white/6 hover:text-white/85'
                    }`}>
                    <span className="text-amber-200/70 text-xs">{CHANNEL_TYPE_ICON[ch.channel_type] || '◇'}</span>
                    <span className="truncate capitalize">{channelLabel(ch)}</span>
                  </button>
                ))}
              </div>
            )
          })}

          {/* AI channels */}
          {channels.filter(c => c.channel_type === 'ai_assistant').length > 0 && (
            <div className="mb-3">
              <p className="text-[10px] font-semibold text-white/30 uppercase tracking-wider px-2 mb-1">Operator AI</p>
              {channels.filter(c => c.channel_type === 'ai_assistant').map(ch => (
                <button key={ch.id} onClick={() => { setActiveChannelId(ch.id); setAiMode(true) }}
                  className={`w-full text-left px-3 py-2.5 rounded-xl flex items-center gap-2 transition-colors text-sm ${
                    activeChannelId === ch.id ? 'bg-sky-500/18 text-sky-100' : 'text-white/55 hover:bg-white/6 hover:text-white/85'
                  }`}>
                  <span className="text-blue-400/70 text-xs">✦</span>
                  <span className="truncate capitalize">{channelLabel(ch)}</span>
                </button>
              ))}
            </div>
          )}
        </nav>

        <div className="px-3 pb-4">
          <button onClick={() => setShowNewChannel(true)}
            className="w-full flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-white/55 hover:text-white hover:bg-white/8 text-sm transition-colors">
            <span className="text-base leading-none">+</span>
            <span>Open Room</span>
          </button>
        </div>
      </aside>

      {/* ── Chat area ── */}
      {activeChannel ? (
        <div className="flex-1 flex flex-col min-w-0">

          {/* Header */}
          <div className="flex min-h-20 items-center gap-4 border-b border-slate-200/80 bg-white/78 px-6 py-4 backdrop-blur-xl flex-shrink-0">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-white shadow-[0_18px_40px_-28px_rgba(15,23,42,0.8)]">
              {CHANNEL_TYPE_ICON[activeChannel.channel_type] || '◇'}
            </div>
            <div>
              <div className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">{channelKicker(activeChannel.channel_type)}</div>
              <div className="mt-1 text-lg font-semibold capitalize tracking-tight text-slate-950">{channelLabel(activeChannel)}</div>
            </div>
            {activeChannel.description && (
              <span className="hidden max-w-md text-sm leading-6 text-slate-500 lg:block">{activeChannel.description}</span>
            )}
            <div className="ml-auto flex items-center gap-2">
              {activeChannel.channel_type === 'ai_assistant' && (
                <span className="text-[11px] bg-sky-50 text-sky-700 border border-sky-200 px-2 py-1 rounded-full font-semibold">AI Mode</span>
              )}
              <button onClick={handleExport}
                className="text-[11px] text-slate-500 hover:text-slate-950 px-3 py-2 rounded-full border border-slate-200 bg-white hover:border-slate-300 transition-colors font-semibold">
                Export XLSX
              </button>
            </div>
          </div>

          {/* Message thread */}
          <div className="flex-1 overflow-y-auto px-5 py-5 space-y-2">
            {messageList.length === 0 && (
              <div className="text-center py-16">
                <p className="text-4xl mb-3">{CHANNEL_TYPE_ICON[activeChannel.channel_type] || '#'}</p>
                <p className="text-slate-700 font-medium capitalize">Welcome to {channelLabel(activeChannel)}</p>
                <p className="text-slate-400 text-sm mt-1">{activeChannel.description || 'Start the conversation.'}</p>
                {activeChannel.channel_type === 'ai_assistant' && (
                  <p className="text-sky-700/70 text-sm mt-3">Ask anything about your business data: revenue, leads, margins, and more.</p>
                )}
              </div>
            )}
            {messageList.map(msg => (
              <MessageItem
                key={msg.id}
                msg={msg}
                currentUserId={user?.id}
                onReact={(msgId, emoji) => reactMutation.mutate({ messageId: msgId, emoji })}
                onDownload={handleDownload}
              />
            ))}
            <div ref={bottomRef} />
          </div>

          {/* Composer */}
          <div className="border-t border-slate-200/80 bg-white/80 px-5 pb-5 pt-4 backdrop-blur-xl flex-shrink-0">
            {/* Pending file chips */}
            {(pendingFiles.length > 0 || uploadingFile) && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {pendingFiles.map(f => (
                  <div key={f.id} className="flex items-center gap-1.5 bg-slate-50 border border-slate-200 rounded-lg px-2 py-1 text-xs text-slate-600">
                    <span>{FILE_ICONS[f.file_type] || '📎'}</span>
                    <span className="max-w-[120px] truncate">{f.filename}</span>
                    <button onClick={() => setPendingFiles(prev => prev.filter(p => p.id !== f.id))}
                      className="text-slate-300 hover:text-slate-700 ml-0.5">×</button>
                  </div>
                ))}
                {uploadingFile && (
                  <div className="flex items-center gap-1.5 bg-sky-50 border border-sky-200 rounded-lg px-2 py-1 text-xs text-sky-700">
                    <span className="animate-spin">⟳</span> Uploading…
                  </div>
                )}
              </div>
            )}

            {/* TW-1322: failed sends/uploads used to be invisible. The draft
                and pending files are preserved on failure; say so. */}
            {(sendMutation.isError || uploadError) && (
              <div role="alert" className="mb-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {uploadError || 'That message did not send. Your draft is still here. Try again.'}
              </div>
            )}

            {/* GIF preview */}
            {gifPreview && (
              <div className="relative inline-block mb-2">
                <img src={gifPreview} alt="GIF preview" className="h-24 rounded-lg border border-slate-200" />
                <button onClick={() => setGifPreview(null)}
                  className="absolute -top-1.5 -right-1.5 bg-white border border-slate-200 text-slate-500 hover:text-slate-950 rounded-full w-5 h-5 flex items-center justify-center text-xs">
                  ×
                </button>
              </div>
            )}

            <div className="relative flex items-end gap-2">
              {/* Emoji popover */}
              {showEmoji && (
                <div className="absolute bottom-full left-0 z-50">
                  <EmojiPicker onSelect={handleEmojiSelect} onClose={() => setShowEmoji(false)} />
                </div>
              )}
              {/* GIF popover */}
              {showGif && (
                <div className="absolute bottom-full left-0 z-50">
                  <GifPicker onSelect={handleGifSelect} onClose={() => setShowGif(false)} />
                </div>
              )}

              {/* Input box */}
              <div className={`flex-1 border rounded-[1.35rem] bg-white shadow-[0_18px_55px_-36px_rgba(15,23,42,0.45)] transition-colors ${
                aiMode ? 'border-sky-300 bg-sky-50/70' : 'border-slate-200 focus-within:border-amber-300'
              }`}>
                {aiMode && (
                  <div className="flex items-center gap-1.5 px-3 pt-2.5">
                    <span className="text-[10px] font-semibold text-sky-700 uppercase tracking-widest">✦ Operator AI Ask</span>
                    <button onClick={() => setAiMode(false)} className="ml-auto text-slate-300 hover:text-slate-700 text-xs">✕</button>
                  </div>
                )}
                <textarea
                  ref={textareaRef}
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    aiMode
                      ? 'Ask the AI anything about your business…'
                      : `Ask @BI, @REVOPS, @HR, or message ${channelLabel(activeChannel)}`
                  }
                  rows={1}
                  className="w-full bg-transparent text-slate-900 placeholder-slate-400 px-3 py-3 text-sm resize-none focus:outline-none leading-relaxed"
                  style={{ maxHeight: '128px', overflowY: 'auto' }}
                />
                <div className="flex items-center gap-0.5 px-2 pb-2">
                  <button onClick={() => { setShowEmoji(!showEmoji); setShowGif(false) }}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-slate-800 hover:bg-slate-100 transition-colors text-base" title="Emoji">
                    😊
                  </button>
                  <button onClick={() => { setShowGif(!showGif); setShowEmoji(false) }}
                    className="p-1.5 rounded-lg text-slate-400 hover:text-slate-800 hover:bg-slate-100 transition-colors text-[11px] font-bold tracking-tight" title="GIF">
                    GIF
                  </button>
                  <label className="p-1.5 rounded-lg text-slate-400 hover:text-slate-800 hover:bg-slate-100 transition-colors cursor-pointer text-base" title="Attach file">
                    📎
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.pptx,.ppt,.png,.jpg,.jpeg,.gif,.webp,.txt,.mp4"
                      className="hidden"
                      onChange={handleFileSelect}
                    />
                  </label>
                  <button onClick={() => { setAiMode(!aiMode); setShowEmoji(false); setShowGif(false) }}
                    className={`p-1.5 rounded-lg text-sm transition-colors ${
                      aiMode ? 'text-sky-700 bg-sky-100' : 'text-slate-400 hover:text-slate-800 hover:bg-slate-100'
                    }`} title="Ask AI">
                    ✦
                  </button>
                </div>
              </div>

              {/* Send button */}
              <button
                onClick={handleSend}
                disabled={(!draft.trim() && !gifPreview && pendingFiles.length === 0) || sendMutation.isPending}
                className={`h-10 w-10 rounded-xl flex items-center justify-center text-white transition-all ${
                  aiMode
                    ? 'bg-sky-600 hover:bg-sky-500'
                    : 'bg-slate-950 hover:bg-slate-800'
                } disabled:opacity-30 disabled:cursor-not-allowed`}>
                {sendMutation.isPending ? (
                  <span className="animate-spin text-sm">⟳</span>
                ) : (
                  <span className="text-sm">↑</span>
                )}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-slate-400 text-sm">Select a room to start messaging</p>
        </div>
      )}

      <OfficePulseRail activeChannel={activeChannel} messageCount={messageList.length} bots={bots} onMentionBot={handleMentionBot} />

      {/* New channel modal */}
      {showNewChannel && (
        <NewChannelModal
          onClose={() => setShowNewChannel(false)}
          onCreate={(body) => createChannelMutation.mutateAsync(body)}
        />
      )}
    </div>
  )
}
