# Twistor Trades — DESIGN.md

**Brand contract for AI-assisted component generation (TW-175).**
Pattern adopted from [nexu-io/open-design](https://github.com/nexu-io/open-design)
(Apache-2.0): every generated render reads this file as the core brand contract.
If a generated component conflicts with this file, the file wins.

**Prepared by Twistor Holdings LLC**

---

## 1. Brand

- **Product:** Twistor Trades — the operating system for home-service businesses (HVAC-first).
- **Company:** Twistor Holdings LLC. Every document/artifact carries "Prepared by Twistor Holdings LLC".
- **Positioning:** "We find the money walking out the door before you notice it's gone."
- **Voice:** culture-first, confident, never corny. Customer-facing copy reads executive-grade.
  Urban/culturally current when it fits ("for the culture", "holla"), never forced.

## 2. Color tokens

| Token | Value | Use |
|---|---|---|
| `--tw-near-black` | `#07070d` | Page backgrounds, deep surfaces |
| `--tw-ink` | `#09111f` | Cards, panels (matches Tailwind `ink-950`) |
| `--tw-surface` | `#0d0d17` | Raised surfaces |
| `--tw-violet` | `#8b5cf6` | Primary accent, CTAs, highlights |
| `--tw-violet-deep` | `#7c3aed` | Gradient stops, hover states |
| `--tw-fuchsia` | `#d946ef` | Gradient partner for CTAs, "money" moments |
| `--tw-text` | `#ffffff` | Primary text on dark |
| `--tw-text-dim` | `rgba(255,255,255,0.6)` | Secondary text |
| `--tw-text-faint` | `rgba(255,255,255,0.4)` | Tertiary text, captions |

Scheme: **near-black + indigo-violet, always.** Light sections are the exception,
never the default — and any light section must still carry violet accents.
Never ship flat gray-on-white enterprise SaaS looks.

## 3. Typography

- Display/headings: `"Space Grotesk"`, tight tracking (`tracking-tight`), semibold/bold.
- Body: `Inter`, system-ui fallback. Relaxed leading for paragraphs (`leading-relaxed`).
- Eyebrow labels: 11–12px, uppercase, wide tracking (`tracking-[0.18em]+`), dim color.
- Never more than two typefaces on one screen.

## 4. Component patterns

- **Cards:** `rounded-2xl`, `border-white/10`, near-black surface, soft deep shadow.
  Content padding `p-5`–`p-6`. No hard black borders, no flat gray fills.
- **CTAs:** gradient `from-violet-500 to-fuchsia-600`, white semibold text,
  `rounded-xl`, subtle scale on hover (`hover:scale-[1.02]`), visible focus ring
  (`focus-visible:ring-2 focus-visible:ring-violet-300`).
- **Badges/pills:** `rounded-full`, translucent tinted backgrounds
  (e.g. `bg-violet-400/10 text-violet-200 border-violet-300/30`).
- **Sections:** generous vertical rhythm (`py-16`+), max width `max-w-6xl/7xl`,
  eyebrow → headline → subcopy → content.
- **Data displays:** tabular numbers for metrics; trend arrows ▲▼ with
  emerald/rose tints; money values always display-ready strings ("$12,480").

## 5. Imagery & representation (hard gates)

- Photographic, never AI-looking. High-production, minimalist.
- People of color across the board — Black, Latinx, Native American, Asian American.
  Never white-only default.
- **Faceless rule:** no hyper-realistic likeness of the founder. Hands, back-of-head,
  silhouettes, or fictional people only.
- **Hands:** anatomically correct, always. Five fingers, natural grip.
- No fake employees, customers, or metrics. Sample data is always labeled **SAMPLE DATA**.

## 6. Motion

- Cinematic scroll: elements scale and shift depth as you scroll (subtle, purposeful).
- Micro-interactions: 150–300ms eases, never bouncy.
- `prefers-reduced-motion`: all non-essential motion off, content fully readable.
- Never motion for decoration alone — motion must guide the eye to the next action.

## 7. Accessibility (non-negotiable)

- Semantic HTML: `section`/`article`/`header` with `aria-label`, real `button`s, real headings.
- Keyboard: every interactive element reachable and operable; visible focus states.
- Contrast: text on near-black ≥ 4.5:1 (dim text is for large/bold or decorative only).
- `alt` text on every image; `aria-hidden="true"` on decorative elements.

## 8. Copy rules

- Never ship lorem ipsum or placeholder copy — flag missing copy as blocked.
- Numbers that aren't real are labeled SAMPLE DATA, every time, no exceptions.
- Headlines make a claim about the customer's business, not about Twistor.

## 9. Generation workflow (for agents)

1. Read this file completely before generating anything.
2. Take the brief: section purpose, copy (or "copy blocked"), placement.
3. Generate the component: Tailwind only, no new dependencies, no inline `<style>`.
4. Self-check against sections 2–8. Fix violations before delivering.
5. Deliver: the component file, a note on accessibility checks performed,
   and a screenshot or preview for visual work.

## 10. Provenance

- Pattern: open-design DESIGN.md design-system package (Apache-2.0).
- This file is Twistor-original content; no open-design code is vendored.
- See `THIRD-PARTY-NOTICES.md` for attribution.
