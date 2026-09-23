---
name: "twistor-ui"
description: "Generate Twistor Trades UI components that conform to the brand contract. Trigger: when building or iterating on any customer-facing component in this repo."
---

# Twistor UI — AI-assisted component generation (TW-175)

You generate production-grade React + Tailwind components for Twistor Trades.
Pattern adopted from [nexu-io/open-design](https://github.com/nexu-io/open-design)
(Apache-2.0): composable skills + a `DESIGN.md` brand contract the agent reads
before generating. **The brand contract is law:** `frontend/DESIGN.md` and
`frontend/design-system/tokens.css`. If your output conflicts with them, the
contract wins.

## Workflow

### 1. Read the contract
Read `frontend/DESIGN.md` completely — colors, type, component patterns,
imagery gates, motion, accessibility, copy rules. Check `tokens.css` for exact values.

### 2. Take the brief
You need: section purpose, placement, and real copy. If copy is missing, generate
with clearly-labeled SAMPLE DATA or flag it as blocked — never lorem ipsum.

### 3. Generate
- React + Tailwind only. No new dependencies. No inline `<style>` tags.
- Near-black + indigo-violet scheme (section 2 of DESIGN.md).
- Cards: `rounded-2xl border-white/10`, near-black surface. CTAs: violet→fuchsia gradient.
- Mobile-first; verify the layout at 390px width mentally before delivering.
- Reduced-motion: wrap non-essential animation so `prefers-reduced-motion` disables it.

### 4. Self-check (do this before delivering, every time)
- [ ] Colors only from the token table?
- [ ] No lorem ipsum; sample numbers labeled SAMPLE DATA?
- [ ] Semantic HTML (`section`/`article`/`header`, real buttons, heading order)?
- [ ] Keyboard operable, visible focus states?
- [ ] Contrast ≥ 4.5:1 for body text?
- [ ] No fake people, metrics, or testimonials?
- [ ] Copy claims something about the customer's business, not Twistor?

### 5. Deliver
- The component file under `frontend/src/components/`.
- A note on the accessibility checks you performed.
- Wire it into the page the brief names. Keep the diff small and reviewable.

## Brand-board QC gate
Every generated component must pass the standing brand-board gate: photographic
(not AI-looking) where imagery is used, people-of-color representation, faceless
rule, anatomically correct hands in any imagery, near-black + indigo-violet,
no killed motifs, no fake claims. When in doubt, generate the conservative
option and flag the judgment call.

## Anti-patterns
- Flat gray-on-white enterprise SaaS cards.
- Gradient text on body copy (headlines only, sparingly).
- More than two typefaces per screen.
- Motion that doesn't guide the eye to an action.
- Copy-pasting markup across pages — build a component instead.
