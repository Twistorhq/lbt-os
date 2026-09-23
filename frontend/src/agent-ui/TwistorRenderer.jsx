/**
 * TwistorRenderer (TW-181) — React 18 renderer for json-render flat specs.
 *
 * @json-render/react requires React 19; lbt-os runs React 18, so this thin
 * renderer walks the spec ({ root, elements }) and renders Twistor's own
 * branded registry. Guardrails:
 *  - unknown component type  -> safe fallback card, never a crash
 *  - props failing zod validation -> safe fallback card, console warning
 *  - missing child keys / cycles -> skipped with a warning, never infinite loop
 *
 * Usage:
 *   <TwistorRenderer spec={agentJson} onAction={(actionId) => ...} />
 */
import { useMemo } from 'react'
import { twistorCatalog } from './catalog'
import { componentRegistry } from './components'

function FallbackCard({ reason }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/20 bg-white/[0.02] p-5" role="note">
      <p className="text-sm font-medium text-white/70">This block could not be rendered.</p>
      <p className="mt-1 text-xs text-white/40">{reason}</p>
    </div>
  )
}

/** Maximum spec-tree depth — defense against pathological non-cyclic chains. */
const MAX_DEPTH = 25

function renderElement(spec, key, registry, propsByType, onAction, seen, depth) {
  if (depth > MAX_DEPTH) {
    console.warn(`[TwistorRenderer] max depth exceeded at "${key}" — skipping`)
    return null
  }
  if (seen.has(key)) {
    console.warn(`[TwistorRenderer] cycle detected at element "${key}" — skipping`)
    return null
  }
  const root = spec.elements?.[key]
  if (!root) {
    console.warn(`[TwistorRenderer] missing element "${key}" — skipping`)
    return null
  }
  const Impl = registry[root.type]
  if (!Impl) {
    return <FallbackCard key={key} reason={`Unknown component type "${root.type}".`} />
  }
  let props = root.props ?? {}
  const zodSchema = propsByType[root.type]
  if (zodSchema) {
    const parsed = zodSchema.safeParse(props)
    if (!parsed.success) {
      console.warn(`[TwistorRenderer] invalid props for "${root.type}" (${key}):`, parsed.error.issues)
      return <FallbackCard key={key} reason={`"${root.type}" received invalid data.`} />
    }
    props = parsed.data
  }
  const nextSeen = new Set(seen)
  nextSeen.add(key)
  const children = (root.children ?? [])
    .map((childKey) => renderElement(spec, childKey, registry, propsByType, onAction, nextSeen, depth + 1))
    .filter(Boolean)
  const emit = (actionId) => onAction && onAction(actionId, key)
  return <Impl key={key} props={props} emit={emit}>{children}</Impl>
}

export default function TwistorRenderer({ spec, onAction }) {
  const propsByType = useMemo(() => {
    const map = {}
    const defs = twistorCatalog.data?.components ?? {}
    for (const [name, def] of Object.entries(defs)) {
      if (def && def.props) map[name] = def.props
    }
    return map
  }, [])

  if (!spec || typeof spec !== 'object' || !spec.root || !spec.elements) {
    return <FallbackCard reason="No dashboard spec was provided." />
  }
  if (!spec.elements[spec.root]) {
    return <FallbackCard reason={`Root element "${spec.root}" is missing from the spec.`} />
  }

  return (
    <div className="twistor-agent-ui">
      {renderElement(spec, spec.root, componentRegistry, propsByType, onAction, new Set(), 0)}
    </div>
  )
}
