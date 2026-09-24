# Visual-Story Brief — Twistor Trades Marketing Site (TW-165)

**Build input for the scroll-storytelling pass.** The site builds from this; Rosa reviews against it.

Prepared by Twistor Holdings LLC

## Brand board (from the social brand kit + QC gate)
- **Palette:** near-black (`sofrito-950`) with indigo-violet glow, gold accents, red reserved for the problem stage. No light-brand drift on the marketing surface.
- **Look:** photographic, never AI-looking. No AI slop. No "magical lines" motif — AI is depicted as the builder.
- **Hands:** anatomically correct in every visual.
- **People:** faceless or fictional; showcase people of color across the board; never white-only default; no hyper-realistic likeness of the founder.
- **Political neutrality:** no military uniforms, insignia, or military-adjacent imagery anywhere on the marketing surface.
- **Honesty:** no fake testimonials, no fabricated metrics, no implied headcount. Sample businesses always labeled "Sample data."
- **Voice:** confident, never corporate. Short sentences. "The money walking out your door" is the thesis line — it leads, everything else supports it.

## Scroll narrative arc ("Bramble" pattern)
A sticky, scroll-driven story right after the hero: the visitor scrolls through three stages while the visual world crossfades around them. Compounds on TW-159's motion system (single rAF scroll bus, transform/opacity only, reduced-motion safe).

| Stage | Name | Kicker | Heading | Story beat |
|---|---|---|---|---|
| 01 | The leak | The problem | Money walks out the door every day. | A customer calls three shops and hires the first one that answers. The missed follow-up, the quote that sits unsent for a week — that is revenue leaving before the owner ever sees it. |
| 02 | The catch | The platform | Twistor catches what slips through. | Twistor Trades watches the gaps: missed follow-ups, unsent quotes, quiet weeks — surfaced in one plain-English brief every Monday morning. |
| 03 | The after-state | The payoff | Monday morning looks like this. | Three moves that put money back in the business, before the first cup of coffee is done. Follow-ups first. Always. |

**Choreography (desktop):** 340vh sticky track; `stagePos` runs 0..3 with stage *i* centered at *i* + 0.5. Foreground panels fade 0.3 → 0.5 off-center, rise 90px with depth scale; background layers use a wider overlap (0.4 → 0.6) so the world never goes blank between stages. Backgrounds crossfade: red-tinted leak world → indigo platform glow → gold-tinted resolution. Progress rail names the stages and marks the current one (`aria-current="step"`); rail buttons are keyboard-operable stage jumps (44px+ targets).

**Choreography (mobile / coarse pointers):** re-choreographed, not squeezed. Crossfade + gentle rise (40px) only — no aggressive scaling. Full-bleed panels, tighter copy, rail becomes a bottom-anchored progress row, all targets ≥ 44px, readable at arm's length.

**Reduced motion:** no sticky-driven animation at all — `StaticArc` renders all three stages stacked statically in document order. The motion hook listens for OS setting changes.

**Keyboard / screen-reader contract:** inactive panels are `inert` (never just `pointer-events:none` — invisible controls must not be tabbable; this was a shipped defect class in TW-159 and is fixed here), `aria-hidden` syncs with the active stage, rail state syncs with `aria-current`.

## Image / visual inventory
- **Stage 01 background (the leak):** `/img/hvac-tech.jpg` — civilian Latino HVAC technician (bucket hat, t-shirt) drilling into a rooftop AC unit. Pexels 5463581, José Andrés Pacheco Cortes. Red gradient tint reads as urgency/problem, not political.
- **Stage 03 background (the after-state):** `/img/hvac-team.jpg` — three Thai electrical utility workers in hard hats on a power pole. Pexels 17018103, Thampapon Otavorn. Gold-tinted resolution.
- **Footer credit:** "Photography: José Andrés Pacheco Cortes and Thampapon Otavorn via Pexels."
- **Composed in CSS/SVG (no generations needed, no people, no hands):** missed-call card, unsent-estimate card, health-score card, "This week" Monday-brief card with the Start-free CTA. Every composed card carries a "Sample data" badge.
- **No new AI generations this pass.** Any future generated visual must pass the brand-board QC gate before more variations burn and before it ships.
