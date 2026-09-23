/**
 * Agent UI demo (TW-181).
 * Shows a Twistor dashboard rendered from an agent-emitted JSON spec —
 * the json-render pattern: agent output becomes client-facing UI
 * without hand-building views. Everything on this page is SAMPLE DATA.
 */
import { useState } from 'react'
import TwistorRenderer from '../agent-ui/TwistorRenderer'
import { validateAgentSpec, buildAgentPrompt } from '../agent-ui/catalog'
import sampleSpec from '../agent-ui/sampleSpec.json'

export default function AgentUIDemo() {
  const [lastAction, setLastAction] = useState(null)
  const validation = validateAgentSpec(sampleSpec)

  return (
    <div className="min-h-screen bg-[#07070d] px-4 py-10 text-white sm:px-8">
      <div className="mx-auto max-w-6xl">
        <div className="mb-8 flex flex-wrap items-center gap-3">
          <span className="rounded-full border border-violet-300/40 bg-violet-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-violet-200">
            Sample data
          </span>
          <span className="rounded-full border border-white/15 px-3 py-1 text-xs text-white/60">
            TW-181 · json-render pattern
          </span>
          <span
            className={`rounded-full border px-3 py-1 text-xs ${
              validation.ok
                ? 'border-emerald-300/40 bg-emerald-400/10 text-emerald-200'
                : 'border-rose-300/40 bg-rose-400/10 text-rose-200'
            }`}
          >
            Spec validation: {validation.ok ? 'passed' : 'failed'}
          </span>
        </div>

        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Agent output, rendered as a dashboard.
        </h1>
        <p className="mt-3 max-w-2xl text-white/60">
          A Twistor agent emits a JSON spec constrained to our component catalog —
          no free-form markup, no hand-built views. <code className="text-violet-300">TwistorRenderer</code>{' '}
          turns that spec into this page. Unknown components or bad props render a
          safe fallback instead of crashing.
        </p>

        {lastAction ? (
          <p className="mt-4 rounded-xl border border-violet-300/30 bg-violet-400/10 px-4 py-2 text-sm text-violet-200" role="status">
            Action emitted: <code>{lastAction}</code> — the host app would handle this (send the text, open the dialer, …).
          </p>
        ) : null}

        <div className="mt-8">
          <TwistorRenderer spec={sampleSpec} onAction={(actionId) => setLastAction(actionId)} />
        </div>

        <details className="mt-10 rounded-2xl border border-white/10 bg-white/[0.02] p-5">
          <summary className="cursor-pointer text-sm font-medium text-white/80">
            The JSON spec behind this page (what the agent actually emitted)
          </summary>
          <pre className="mt-4 max-h-96 overflow-auto text-xs leading-relaxed text-white/60">
            {JSON.stringify(sampleSpec, null, 2)}
          </pre>
        </details>

        <details className="mt-4 rounded-2xl border border-white/10 bg-white/[0.02] p-5">
          <summary className="cursor-pointer text-sm font-medium text-white/80">
            The prompt we give the agent (catalog-constrained generation)
          </summary>
          <pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-white/60">
            {buildAgentPrompt('Show me Monday morning for a 6-tech HVAC shop.')}
          </pre>
        </details>
      </div>
    </div>
  )
}
