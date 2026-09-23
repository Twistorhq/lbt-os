/**
 * Twistor agent-UI component catalog (TW-181).
 *
 * Adopts the vercel-labs/json-render pattern (Apache-2.0, Copyright 2025 Vercel Inc.):
 * an AI agent emits a JSON *spec* constrained to this catalog, and TwistorRenderer
 * turns it into a client-facing dashboard — no hand-built views per client.
 *
 * NOTE: @json-render/react requires React 19; lbt-os runs React 18, so Twistor
 * ships its own thin React 18 renderer (TwistorRenderer.jsx) on top of
 * @json-render/core's catalog/schema primitives. The spec wire format matches
 * json-render's flat format: { root, elements: { key: { type, props, children } } }.
 */
import { z } from 'zod'
import { defineSchema, defineCatalog } from '@json-render/core'

export const twistorSchema = defineSchema((s) => ({
  // What the AI-generated SPEC looks like (flat element tree).
  spec: s.object({
    /** Root element key */
    root: s.string(),
    /** Flat map of elements by key */
    elements: s.record(
      s.object({
        /** Component type — must be a key of catalog.components */
        type: s.ref('catalog.components'),
        /** Props validated against the component's zod schema */
        props: s.propsOf('catalog.components'),
        /** Child element keys (flat references) */
        children: s.array(s.string()),
      }),
    ),
  }),

  // What the CATALOG must provide.
  catalog: s.object({
    components: s.map({
      /** Zod schema for component props */
      props: s.zod(),
      /** Human description — fed to the agent as generation guidance */
      description: s.string(),
    }),
    actions: s.map({
      description: s.string(),
    }),
  }),
}))

const money = z.string().describe('Display-ready money string, e.g. "$1,240"')

export const twistorCatalog = defineCatalog(twistorSchema, {
  components: {
    DashboardSection: {
      props: z.object({
        title: z.string().describe('Section heading'),
        subtitle: z.string().optional().describe('One-line subheading'),
      }),
      description:
        'A titled dashboard section. Use to group related cards. Children render inside the section body.',
    },
    MetricCard: {
      props: z.object({
        label: z.string().describe('What the metric measures, e.g. "Revenue recovered"'),
        value: z.string().describe('Display-ready value, e.g. "$12,480" or "37"'),
        format: z.enum(['currency', 'percent', 'number']).nullable().optional(),
        delta: z.string().optional().describe('Change vs prior period, e.g. "+18% vs last week"'),
        trend: z.enum(['up', 'down', 'flat']).optional(),
      }),
      description: 'A KPI card: one big number with a label and optional trend delta.',
    },
    RevenueLeak: {
      props: z.object({
        title: z.string().describe('Short leak name, e.g. "Unanswered after-hours calls"'),
        amount: money.describe('Estimated dollars walking out the door'),
        detail: z.string().optional().describe('One sentence on the cause'),
      }),
      description:
        'A "money walking out the door" callout card. Use for missed revenue opportunities the shop should act on.',
    },
    CallSummary: {
      props: z.object({
        caller: z.string().describe('Caller name or "Unknown caller"'),
        time: z.string().describe('Display-ready time, e.g. "Tue 9:12 AM"'),
        outcome: z.enum(['answered', 'missed', 'voicemail']),
        summary: z.string().describe('One or two sentences on what the call was about'),
        followUp: z.string().optional().describe('Suggested next step, if any'),
      }),
      description: 'A single call rendered as a summary card with its outcome badge.',
    },
    FollowUpQueue: {
      props: z.object({
        items: z
          .array(
            z.object({
              name: z.string(),
              reason: z.string().describe('Why they need a follow-up'),
              due: z.string().describe('Display-ready due label, e.g. "Today 2 PM"'),
            }),
          )
          .describe('Follow-ups, most urgent first'),
      }),
      description: 'The follow-up queue: a ranked list of people the shop must call back.',
    },
    ActionButton: {
      props: z.object({
        label: z.string().describe('Button text, e.g. "Send win-back text"'),
        action: z.string().describe('Action id the host app handles, e.g. "send_winback"'),
      }),
      description: 'A primary action button. Emits its action id to the host app; never navigates on its own.',
    },
  },
  actions: {
    refresh_dashboard: { description: 'Re-run the agent and refresh this dashboard' },
    export_summary: { description: 'Export this dashboard as a PDF summary' },
  },
})

/**
 * Build the system prompt an agent needs to emit a valid spec.
 * Uses the catalog's own prompt generator (component list + prop shapes),
 * wrapped with Twistor's guardrails.
 */
export function buildAgentPrompt(userAsk) {
  const catalogPrompt =
    typeof twistorCatalog.prompt === 'function' ? twistorCatalog.prompt() : ''
  return [
    'You generate a Twistor dashboard as JSON for a home-services shop owner.',
    'Guardrails:',
    '- Output ONLY a JSON object shaped like { "root": "<key>", "elements": { "<key>": { "type": "<ComponentName>", "props": {...}, "children": ["<key>", ...] } } } }.',
    '- Every key referenced in a "children" array MUST exist in "elements". Leaf components use "children": [].',
    '- Use realistic, professional SAMPLE DATA — never leave fields empty. All money values are estimates.',
    catalogPrompt,
    `User request: ${userAsk}`,
  ].join('\n')
}

/**
 * Validate an agent-emitted spec against this catalog.
 * Enforces the documented contract: every key referenced in a "children"
 * array must exist in "elements" (the catalog-level zod check alone does
 * not cover cross-references). Returns { ok, issues }.
 */
export function validateAgentSpec(spec) {
  const issues = []
  const result = twistorCatalog.validate(spec)
  if (result.success !== true) {
    if (result.error) issues.push(...(result.error.issues ?? []))
    return { ok: false, issues, raw: result }
  }
  const elements = spec?.elements ?? {}
  for (const [key, el] of Object.entries(elements)) {
    for (const childKey of el?.children ?? []) {
      if (!Object.prototype.hasOwnProperty.call(elements, childKey)) {
        issues.push({
          code: 'dangling_child',
          message: `Element "${key}" references missing child "${childKey}".`,
        })
      }
    }
  }
  return { ok: issues.length === 0, issues, raw: result }
}
