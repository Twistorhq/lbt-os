# Third-party notices

**Prepared by Twistor Holdings LLC**

This product includes or is derived from the following third-party software,
used under their respective licenses. No third-party code is vendored into this
repository except via standard package managers; patterns and APIs are adopted,
not copied.

## json-render (TW-181)

- Source: https://github.com/vercel-labs/json-render
- License: Apache License 2.0 — Copyright 2025 Vercel Inc.
- Used as: `@json-render/core` npm package (catalog/schema primitives);
  the flat JSON spec format `{ root, elements }` and the catalog-constrained
  generation pattern. Twistor ships its own React 18 renderer
  (`frontend/src/agent-ui/TwistorRenderer.jsx`) — `@json-render/react` was not
  adopted (requires React 19; lbt-os runs React 18).
- License text: https://www.apache.org/licenses/LICENSE-2.0
- License re-verified live from the raw LICENSE file: 2026-09-23.

## open-design (TW-175)

- Source: https://github.com/nexu-io/open-design
- License: Apache License 2.0 — Copyright 2026 Open Design contributors
- Used as: the DESIGN.md brand-contract pattern, the design-system package
  shape (`manifest.json` + `tokens.css`), and the composable-skills workflow
  for AI-assisted component generation. No open-design code is vendored;
  `frontend/DESIGN.md` and `frontend/design-system/` are Twistor-original
  content following the pattern.
- License text: https://www.apache.org/licenses/LICENSE-2.0
- License re-verified live from the raw LICENSE file: 2026-09-23.
