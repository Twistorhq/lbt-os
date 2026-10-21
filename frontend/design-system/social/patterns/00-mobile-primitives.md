# Pattern 00: Mobile primitives (thumb-first foundations)

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).

Every member flow in this package composes from these primitives. They exist because of one sentence in the architecture (TW-646 Section 1): the member's computing moment is standing in a room with a phone in one hand. Desktop patterns are in `08-operator-desktop.md`; nothing here is retrofitted from them.

## Layout

- Single column, full bleed, 16px edge padding (`--soc-thumb-pad`). Content max width on phones is the phone; on larger screens member surfaces cap at 560px centered, because a stretched phone layout is not a desktop design (desktop is the operator layer, a different surface).
- Vertical rhythm in 8px steps. Sections separate by space and type weight, not by boxed cards inside cards.
- The bottom of the screen is the working area. Primary actions anchor in a fixed bottom action bar (height plus `--soc-action-bar-clearance`) or inside a bottom sheet. Nothing critical lives at the top of the screen except the title and the back affordance: top corners are the two hardest points to reach one-handed.

## Thumb rules (hard requirements)

- Every tappable target is at least `--soc-touch-min` (48px) in both dimensions. Primary actions are `--soc-touch-primary` (56px). Door and live-session confirm targets are `--soc-touch-door` (72px).
- One primary action per screen state. Secondary actions are text-weight, below or beside, never a second filled button competing at the same height.
- Destructive or irreversible actions never sit in the primary thumb position, and never within 12px of a primary action.
- Swipe gestures may enhance, never gate: every swipeable action (RSVP, confirm, dismiss) also exists as a visible button, for motor accessibility and for gloves, wet hands, and cracked screens in real venues.

## Components (Astryx side, house design split)

Social builds on the Astryx component library, pinned at `@astryxdesign/core` 0.6.2 exact with `@astryxdesign/theme-neutral` 0.6.2 (the pinned manifest from the website adoption; upgrades are deliberate, never drive-by). Twistor wrappers live in `src/components/ui/` following the existing wrapper pattern, own a small Twistor-named prop API, and take all color, type, and spacing values from `tokens.css` in this package. No component hardcodes a hex. The primitives this foundation needs first:

- `SocActionBar` (fixed bottom bar, one primary plus optional secondary text action)
- `SocSheet` (bottom sheet, drag handle, one decision per sheet)
- `SocTabBar` (bottom navigation: Home, Groups, Events, Standing, Profile; icons plus labels, never icons alone)
- `SocBandChip` (age band and standing labels; spec below)
- `SocAudienceRow` and `SocAudienceSheet` (pattern 09: audience display and picker)
- `SocStateBanner` (full-width state strip for session and result states)
- `SocCountdownRing` (door mode token lifetime, TW-647)
- `SocStaticMapCard` (the cached static tile composite from TW-646 Section 10.2; never an interactive map in a list)

## The age band chip (`SocBandChip`)

Decision: Justynn, 2026-10-20. Every profile carries a visible age band chip, because on this platform the band is a verified fact the room is entitled to read at a glance, the way a door reads it. The earned standing tier displays beside the chip (pattern 06). Two rules bound the whole system:

- **The chip states the band and nothing else.** Its variants are exactly three: `21+`, `18 to 20` rendered as `18+` with the under-21 meaning carried by its black treatment, and `TEEN`. There is no fourth variant.
- **Restriction is never displayed, anywhere.** There is deliberately no chip style, badge, tint, icon, or label for sanction, restriction, watch state, or punishment on any surface in the product. A member under restriction looks like any other member of their band to every other member; their own surfaces show them their own state (pattern 06), and nobody else's business includes it. This is the design's load-bearing refusal, and it holds in the feed, the roster, the encounter cards, search, and the Door Pass (which shows no band chip at all: the verify page carries the band as its result, TW-647).

Variants and treatment:

| Variant | Day scheme | Venue scheme (low light) | Text |
|---|---|---|---|
| 21 plus | Deep green fill (`--soc-chip-21-bg`), warm white text | Bright green fill, near-black text | `21+` |
| 18 to 20 | Black fill (Ink, `--soc-chip-18-bg`), warm white text | Same black fill, warm white text, plus a 1.5px warm white outline (`--soc-chip-18-line`) so the chip stays defined on a near-black canvas | `18+` |
| Teen | Outlined chip: 1.5px Indigo Deep outline, Indigo Deep text, transparent fill | Violet Mist outline and text | `TEEN` |

The TEEN chip is an outline, never a fill, on purpose: it flags the protective tier without borrowing a state color, so it reads as a category, not a warning. It ships in the design system now and renders only when the teen tier opens in phase 2.

Sizing, for glanceability:

- **Standard chip** (profile header, feed author line, standing screen): minimum height 28px, horizontal padding 10px, text at `--soc-text-caption` (13px) set semibold with slight letter spacing. Readable at conversational distance, quiet at reading distance.
- **Glance chip** (encounter confirm cards, roster rows in the live session, host door view): minimum height 36px, text at 16px semibold. This is the variant read across a room or over a shoulder; it is the only chip size used inside the check-in loop (pattern 05) and the venue scheme generally.
- The chip never truncates, never wraps, and never shrinks below these sizes to fit a layout; the layout moves instead.

Placement (where the chip appears): the profile header beside the display name; the feed author line; the event roster and the "who is going" list (pattern 04); the encounter confirm card beside the name (pattern 05); the people pickers in patterns 08 and 09; member search results. Placement (where it never appears): the ghost town (no people surfaces exist there; pattern 02), the Door Pass verify page (TW-647 carries the band itself), any venue-facing aggregate, and beside any member in a context where their band is above the viewer's visibility (under-band content is absent entirely, TW-646 Section 5.9, so no orphaned chips ever render).

Standing beside the chip: the earned tier name (Regular, Fixture, Pillar, Keyholder) renders immediately after the chip in the standing gold text token, on profile and standing surfaces. While a member's tier is suspended from display under TW-646 Section 7.5 (watch or restricted band), the tier simply does not render; the chip remains, because the band is a fact and the tier is an honor, and the design treats them differently on purpose.

Accessibility: the chip is text, announced as text ("Age band: 21 plus", "Age band: 18 to 20", "Teen account"), never conveyed by color alone. Every chip pair is contrast verified in README.md (green 5.09 to 1 day and 10.27 to 1 venue; black 15.97 to 1 both schemes; TEEN outline text 7.35 to 1 day and 9.45 to 1 venue).

## Type and voice on screen

- Fraunces for display moments only (screen titles, celebration headlines, the Year Out). Inter for everything the member operates. Two typefaces per screen, maximum, per the brand contract.
- Body text never below 16px (`--soc-text-body`). Venue scheme raises sizes per the token boosts.
- All strings ship from the string catalog in English and Spanish (TW-646 Section 3). No hardcoded copy in components; pattern docs mark unfinished member copy as blocked, per the no-placeholder rule.

## Motion

- 150 to 300ms, `--soc-ease`, guiding the eye to the next action. Nothing bounces, nothing loops for decoration.
- State changes inside the check-in loop use view transitions between steps so the member always knows the session moved; the content crossfades, the session header and safety button stay fixed (continuity is the orientation cue).
- `prefers-reduced-motion` turns all of it off except functional state changes; content remains fully readable (tokens.css carries the media query).

## Connectivity and failure posture

Mobile-first in Denver venues means bad signal is a design case, not an error case:

- Every loop screen renders its current state from data already on the device; a failed request shows the last known state plus a plain retry line, never a blank screen or a spinner with no exit.
- Actions taken offline-capable (an encounter confirm tap) queue locally and confirm visually as recorded-on-device; the spec's server records remain the truth, and the UI says so in plain words when sync lands.
- The Door Pass and verify flows follow TW-647 Section 7 exactly: when the chain cannot answer, the surface says so and falls back to physical ID. The UI never softens a fail-closed answer.

## Accessibility (checked for this foundation)

- Semantic landmarks and real buttons throughout; sheets are dialogs with focus trapped and returned.
- Focus ring token `--soc-focus-ring` on every interactive element in both schemes.
- Contrast: every token pair verified numerically in README.md; all text pairs pass WCAG AA for normal text, most pass AAA.
- Color is never the only signal: band chips carry text, result states carry icon shape plus words, standing carries a tier name.
- Screen reader order follows thumb order: title, state, content, primary action. The fixed safety button in the check-in loop is a persistent landmark, announced as such.
