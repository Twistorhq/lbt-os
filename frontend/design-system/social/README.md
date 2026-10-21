# Twistor Social: design foundation (TW-652)

**Prepared by Twistor Holdings LLC**

The design foundation for Twistor Social's member surfaces: the token extension, the thumb-first pattern specs for every launch flow, and the desktop operator-layer patterns. This package is the frontend companion to the TW-646 architecture spec, the TW-647 Door Pass spec, and Imani's reward catalog. Zeke's services (`backend/app/social/`, `backend/app/verify/`, `backend/app/door/`) and these surfaces are one system: every pattern below names the spec sections it serves, and no pattern invents behavior the spec does not contain.

Package contents:

- `tokens.css`: the Social design-token extension (schemes, thumb primitives, type scale, motion).
- `manifest.json`: package manifest, mirroring the Twistor Trades package shape (TW-175).
- `patterns/`: one spec per flow, listed in the mapping table below.

## The scheme decision (the face of Social)

**Decision: Social runs the Arroz con Leche day scheme as its default face, with the Medianoche venue scheme for in-room moments. The trades-rugged scheme stays out, entirely.**

Why, in order of weight:

1. **Social is a consumer product, not a jobsite tool and not a brand campaign.** The trades-rugged language (warm bone, concrete, safety orange) belongs to Twistor Trades, whose users are shops and dispatchers; the guard script's design split exists to keep product languages scoped. Social's users are everybody going out in Denver, on phones, in daylight and in bars. A consumer social surface has to read clean, warm, and ordinary in the best sense: the corporate-clean face (warm paper, ink text, violet as structure) is the Twistor face built for exactly this register.
2. **The product physically happens in dark rooms.** Door mode, the live check-in session, and the venue verify page occur at night, in bar light, at arm's length. Those surfaces switch to Medianoche (near-black, warm white, boosted type) by context, never by a user theme toggle: dark does not blast the member's eyes, does not light up a room, and carries the contrast headroom a door result needs. The brand's flagship near-black and violet identity therefore appears at the product's moment of highest public visibility: the door, in front of the line, which Diego's Door Pass sizing identified as the product's best advertisement.
3. **One brand, two faces, one spine.** Every hex in `tokens.css` is drawn from the two existing brand palettes (Arroz con Leche, Medianoche) in `twistor_core.design_intelligence`, and the indigo-violet family is byte-identical to the shared brand tokens in `../tokens.css`. Fraunces gives Social its own display voice; Inter, the family body font, is shared. A customer moving between Trades and Social sees one company with two products, never two brands. That is the standing design-split rule (shared brand tokens as the single source of truth), applied.
4. **Accessibility breadth.** The light scheme carries ink-on-paper contrast at 16.70 to 1, which serves the broadest audience including older members; the venue scheme carries 17.55 to 1. Both schemes pass WCAG AA for every text pair (table below).

Kept-on-purpose note for the Impeccable audit: Arroz con Leche is a warm paper, and the 15-tell audit bans default beige and cream. This is not the default: it is a named brand palette with ink text and violet structure, chosen deliberately for warmth (the design-intelligence composer independently selected style Warm Community and palette Medianoche for this brief; the day scheme extends the system rather than replacing it). Documented here with its reason, per the gate protocol, not silently kept.

## How the tokens layer

`tokens.css` defines the shared spine (brand violet family, warm white, near-black, type pairing, thumb primitives) once, then two scheme blocks switched by the `data-social-scheme` attribute: `day` (default) and `venue`. Components consume `--soc-*` variables only. At runtime the tokens map through the Astryx theme layer: Social is an app surface per the house design split, built on `@astryxdesign/core` 0.6.2 exact with `@astryxdesign/theme-neutral` 0.6.2 (the pinned manifest from the website adoption), with Twistor wrappers in `src/components/ui/` owning small Twistor-named prop APIs. Versions stay pinned; upgrades are deliberate.

## Pattern to spec mapping

| Pattern | Serves (spec sections) | Surface and scheme |
|---|---|---|
| 00 Mobile primitives | TW-646 S.1 (form factor), S.3 (localization), S.10.2 (static map cards) | All member surfaces; both schemes |
| 01 Verification capture | TW-646 S.2, S.5.1, S.5.2, S.5.3, S.5.7, S.5.11; TW-647 S.6.3, S.9 (door photo gate) | Member mobile; day |
| 02 Ghost town | TW-646 S.5.9, S.5.11, S.10.5 | Member mobile and public web; day |
| 03 Feed and celebrations | TW-646 S.4 (feed), S.5.9, S.7.10, S.8; Catalog S.4 | Member mobile; day |
| 04 Event page and RSVP | TW-646 S.4, S.5.9, S.7.7, S.7.8, S.8 | Member mobile; day (reveal card borrows venue type sizes) |
| 05 Check-in loop | TW-646 S.7.1, S.7.2, S.7.3, S.7.4, S.7.6, S.7.8 | Member mobile, live session; venue |
| 06 Standing and rewards | TW-646 S.7.5, S.7.7, S.7.9, S.7.10; Catalog S.1, S.2, S.3, S.5 | Member mobile; day |
| 07 Door Pass | TW-647 S.2.3, S.3, S.3.2, S.5, S.6.3, S.8; TW-646 S.5.8, S.7.5 | Member door mode and venue verify page; venue |
| 08 Operator desktop | TW-646 S.4, S.6.5, S.7.7, S.10.4; TW-647 S.6.4, S.6.5 | Desktop (organizer console, venue dashboard, staff console note); day |
| 09 Close friends and home events | Decision (Justynn, 2026-10-20); extends TW-646 S.4, S.8; overrides the S.9 public-venue-only soft-launch assumption | Member mobile and organizer desktop; day |

Two founder decisions from 2026-10-20 are designed into this foundation and flagged where they touch the architecture: the **visible age band chip** (pattern 00: 21 plus green, 18 to 20 black, TEEN outlined; standing tier beside it; restriction never displayed anywhere, by design and without exception), and **home hosting with close-friends audiences** (pattern 09). Pattern 09 Section D lists what the OpenSpec change must reconcile: the Section 9 soft-launch line it overrides, the RSVP request sub-state the data model does not yet carry, the proposed 3 hour home reveal window against Section 8's 48 hours, and Angela's liability gates moving from soft launch to launch.

## Accessibility verification (this run)

Contrast computed with the WCAG relative-luminance formula against the final token values:

| Pair | Ratio | Verdict |
|---|---|---|
| Ink on Paper (day body) | 16.70 to 1 | AAA |
| Ink on Crema (day cards) | 15.17 to 1 | AAA |
| Indigo Deep on Paper (links, structure) | 7.35 to 1 | AAA |
| Warm White on Indigo Deep (primary action) | 7.03 to 1 | AAA |
| Muted Clay on Paper (secondary text) | 5.22 to 1 | AA |
| Standing Cafecito on Paper (tier text, day) | 5.49 to 1 | AA |
| Standing Chinola on Medianoche (tier text, venue) | 10.56 to 1 | AAA |
| Valid deep green on Paper | 5.32 to 1 | AA |
| Danger deep red on Paper | 6.14 to 1 | AA |
| Caution deep amber on Paper | 5.07 to 1 | AA |
| Warm White on Medianoche (venue body) | 17.55 to 1 | AAA |
| Warm White on Panel (venue cards) | 16.51 to 1 | AAA |
| Violet Mist on Medianoche (venue accent) | 9.45 to 1 | AAA |
| Bright valid green on Medianoche | 10.27 to 1 | AAA |
| Near-black on bright valid green (valid banner text) | 10.27 to 1 | AAA |
| Venue caution amber on Medianoche | 10.67 to 1 | AAA |
| Venue danger red on Medianoche | 7.05 to 1 | AAA |
| Chip 21 plus: Warm White on deep green (day) | 5.09 to 1 | AA |
| Chip 21 plus: Medianoche on bright green (venue) | 10.27 to 1 | AAA |
| Chip 18 to 20: Warm White on Ink black (both schemes) | 15.97 to 1 | AAA |
| Chip TEEN: Indigo Deep on Paper (day) / Violet Mist on Medianoche (venue) | 7.35 / 9.45 to 1 | AAA |

Design rules the table enforces: Indigo Violet `#7C5CFF` never carries small text on the day scheme (3.88 to 1 against warm white); it is an accent and a dark-scheme fill. Primary actions on day are Indigo Deep. Ledger Gold `#C9A24B` (2.24 to 1 on paper) is a mark only: shelf marks and passport accents, always beside ink or Cafecito text, never a text color and never a text background in either scheme. Touch targets: 48px minimum, 56px primary, 72px door and session confirms (pattern 00).

## Impeccable gate (TW-415 standing)

- Detector: this package is design documentation and tokens, not a rendered surface, so the run is the manual 15-tell audit against the foundation plus the design-intelligence `check_spec` pass; the detector runs against real components in the build runs that follow, per the standing protocol.
- `check_spec` results (run 2026-10-20, both scheme palettes, full token color set, all verified contrast pairs): **zero errors**. Thirteen `g-scheme` warnings per palette, every one a palette-membership note and every one accounted for: this package is dual-scheme by design, so each single-palette check flags the other scheme's colors; the gold family (Cafecito, Ledger Gold, Chinola) is drawn from its home brand palettes (Cafecito, Mofongo, Chinola, Coco) rather than the two scheme palettes; and the six semantic state hues are the one documented extension in tokens.css (functional colors a door result cannot ship without). No warning is unexamined, and no token color is unaccounted for.
- Audit findings against the foundation: no gradients anywhere (flat tokens only; the CTA gradient from the Trades marketing contract does not cross into Social); no gradient text; no glassmorphism; no pill badges (band and tier labels are squared chips carrying text); no icon-in-rounded-square; no identical card grids (the shelf and feed are lists with distinct proof lines); no cards in cards; no decorative side stripes; no context-free hero stats; no fake numbered sequences (progress is a step count tied to real steps); no decorative animation (the single animation, the Door Pass validity element, is a security control, documented in pattern 07); copy in patterns is structural notation or spec-exact strings, with unfinished member copy marked blocked rather than invented.
- Brand-board QC expectations applied where they bind a UI foundation: people of color across the board in any future imagery (no imagery ships in this run); faceless rule untouched (Social member photos are members' own, the founder does not appear); no fake metrics (operator patterns label aggregates and sample data); the magical-lines motif is absent. The board's near-black color gate governs marketing visuals; this foundation's scheme decision above records why the app surface differs and where near-black does govern (the venue scheme).

## What this run is not

- No components are built yet: the patterns are buildable specs for the component runs that follow, sequenced after the TW-646 schema step lands the API contracts these surfaces consume. Where a pattern needs an endpoint shape that does not exist yet, the contract source is the spec section named in the mapping table; frontend does not invent backend contracts.
- Copy status: strings quoted from the specs (result page wording, badge language, catalog celebration patterns) are spec-exact. All other member-facing copy routes through the string catalog with human Spanish translation (TW-646 S.3); final copy is Imani and Nia's lane and Justynn's approval, and patterns mark it blocked rather than shipping placeholder text.
- The staff safety console (TW-646 S.6.5) is a pattern note here; its detailed design lands with the safety build.

Prepared by Twistor Holdings LLC
