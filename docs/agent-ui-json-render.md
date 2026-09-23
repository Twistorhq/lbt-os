# Agent UI — json-render adoption (TW-181)

**Prepared by Twistor Holdings LLC**

## What this is

An adoption of the [vercel-labs/json-render](https://github.com/vercel-labs/json-render)
pattern (Apache-2.0, Copyright 2025 Vercel Inc. — license re-verified live 2026-09-23):
**an AI agent emits a JSON spec constrained to a component catalog, and the app
renders it as a client-facing dashboard.** No hand-built views per client, per
report, per vertical.

## Why not @json-render/react directly

`@json-render/react` (all published versions, incl. 0.21.0) declares a
`react: ^19.x` peer dependency. lbt-os runs React 18.3.1. Upgrading the app to
React 19 is out of scope for this adoption, so Twistor ships:

- `@json-render/core` (npm, no React peer dep — only zod) for the real
  primitives: `defineSchema`, `defineCatalog`, catalog `prompt()`,
  `validate()`, `jsonSchema()`.
- `TwistorRenderer.jsx` — a thin React 18 renderer (~90 lines) that walks the
  flat spec format (`{ root, elements }`) and renders Twistor's own branded
  component registry.

The spec wire format matches json-render's flat format, so a future React 19
upgrade can swap in `@json-render/react`'s renderer without changing the
agent contract.

## Files

| File | Purpose |
|---|---|
| `frontend/src/agent-ui/catalog.js` | Schema + catalog: 6 Twistor components with zod prop schemas and agent-facing descriptions; `buildAgentPrompt()`; `validateAgentSpec()` |
| `frontend/src/agent-ui/components.jsx` | Branded React implementations (near-black + indigo-violet) |
| `frontend/src/agent-ui/TwistorRenderer.jsx` | React 18 renderer with guardrails (unknown type / bad props / cycles / missing keys → safe fallback, never a crash) |
| `frontend/src/agent-ui/sampleSpec.json` | Example agent output — SAMPLE DATA |
| `frontend/src/pages/AgentUIDemo.jsx` | Public demo at `/agent-ui-demo` |

## The contract for agents

1. Call `buildAgentPrompt(userAsk)` to get the catalog-constrained prompt.
2. The agent returns JSON: `{ root, elements: { key: { type, props, children } } }`.
3. `type` must be a catalog key. Every `children` key must exist. Leaf nodes use `"children": []`.
4. Validate with `validateAgentSpec(spec)` before rendering; `TwistorRenderer` re-validates per element at render time.

## Guardrails (why an agent can't break the page)

- The agent can only use catalog components — no arbitrary markup, no scripts.
- Props are zod-validated twice (spec-level + per-element). Failures render a
  neutral fallback card and log a console warning.
- Cycles and dangling child keys are skipped, never followed.
- `ActionButton` emits an action id to the host app via `onAction` — it cannot
  navigate or fetch on its own.

## Extending the catalog

Add the component in `catalog.js` (zod props + description) and its
implementation in `components.jsx`, then register it in `componentRegistry`.
Keep components presentational; data fetching stays in the host app (Dre's lane).

## Money path

Each new vertical (plumbing, electrical, dental) reuses the same renderer with
a vertical-specific catalog. Agent output → client dashboard with zero
hand-built views: faster delivery on data retainers, margin on every one.
