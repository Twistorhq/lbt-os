# TW-181 evidence report — json-render dashboard adoption

**Prepared by Twistor Holdings LLC**

Ticket: TW-181 · Branch: `feature/tw-181-json-render-dashboards` · Author: Sofia Reyes
Reviewer: Rosa Delgado · Date: 2026-09-23

## What this is

Adopts the [json-render](https://github.com/vercel-labs/json-render) pattern
(Apache-2.0, license re-verified live from the raw LICENSE file 2026-09-23)
for AI-agent-generated dashboards inside lbt-os. `@json-render/core` installed
via npm; `@json-render/react` deliberately NOT installed (every published
version requires React 19; lbt-os runs React 18.3.1). Twistor ships its own
~110-line React 18 renderer (`TwistorRenderer.jsx`) on core's primitives; the
spec wire format matches json-render's flat `{ root, elements }` format so a
future React 19 upgrade can swap renderers without changing the agent contract.

## RED / GREEN history

- RED (Rosa round 1, hostile render test): `const el` reassignment in
  `TwistorRenderer.jsx` threw `TypeError: Assignment to constant variable` on
  **every valid spec** — the demo page was a white screen. The vite build did
  not catch it (esbuild permits const reassignment syntactically).
- GREEN (fix): immutable element flow (`const root` + separate `let props`);
  sample spec renders.
- RED (Rosa round 2, 18-case hostile suite): non-array `children`
  (`"c1"`, `{0:'c1'}`, `42`) threw `TypeError: .map is not a function`,
  crashing the whole render — breaking the documented "never a crash" guarantee.
- GREEN (fix): `Array.isArray(root.children) ? root.children : []` + warning.

## Commands run (all GREEN, 2026-09-23)

```
$ npm run test:agent-ui
All 11 agent-UI smoke tests passed.

$ npx vite build   # VITE_CLERK_PUBLISHABLE_KEY=pk_test_dummy
✓ built in ~8s (pre-existing chunk-size warning only)
```

## Test coverage (`frontend/scripts/test-agent-ui.mjs`)

| # | Case | Result |
|---|------|--------|
| 1 | Valid sample spec renders real content | PASS |
| 2 | Unknown component type → safe fallback card | PASS |
| 3 | Invalid props (zod) → safe fallback card | PASS |
| 4 | Cyclic spec (a→b→a) terminates | PASS |
| 5 | Dangling child key skipped | PASS |
| 6 | 60-deep non-cyclic chain capped by MAX_DEPTH=25 | PASS |
| 7 | ActionButton click emits `onAction('do_thing', 'b')` (react-test-renderer, real handler invocation) | PASS |
| 8 | Non-array children (string/object/number) ignored safely | PASS |
| 9 | `validateAgentSpec` accepts sample spec | PASS |
| 10 | `validateAgentSpec` rejects dangling child (`dangling_child`) | PASS |
| 11 | `validateAgentSpec` rejects unknown type | PASS |

## Validator contract

`validateAgentSpec` enforces the documented contract from `buildAgentPrompt`:
every key in a `children` array must exist in `elements`. Dangling references
return `ok:false` with a `dangling_child` issue. The renderer independently
degrades (skip + console warning), so validator and runtime agree.

## License / security notes

- `@json-render/core@0.21.0`: Apache-2.0 (confirmed in installed package
  metadata). `zod@4.6.5`: MIT. Single deduped zod copy. `react-test-renderer@18.3.1`
  (devDependency): matches React 18.3.1.
- No `dangerouslySetInnerHTML`; all spec strings render as React-escaped text.
- `ActionButton` emits only an action id string to the host — no navigation,
  no fetch. No secrets, PII, or auth touched (sample data only).
- Attribution recorded in `THIRD-PARTY-NOTICES.md` (shipped on the TW-175 branch).
