# Pattern 06: Standing and rewards display

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 7.5 (social currency, standing tiers), 7.7 (entitlements), 7.9 (collectibles), 7.10 (recognition artifacts). Catalog: twistor-social-reward-catalog.md Sections 1, 2, 3, 5.

## What this surface is

The member's view of their social currency: what they have earned by showing up, what it unlocks, and what comes next. The computation is the spec's (TW-646 Section 7.5: inputs, windows, application order); the names, thresholds, and wording are the catalog's, and the catalog is the authority for them. This pattern renders; it never invents a number.

## The standing screen

1. **The standing line (profile and standing screen header):** the age band chip at standard size (pattern 00) immediately followed by the tier title in Fraunces (Verified, Regular, Fixture, Pillar, Keyholder), the catalog's one-line meaning ("The room expects you"), and the show rate chip once the member holds at least 5 obligated RSVPs ("Shows up: 94 percent"). Below 5, the profile reads "New: still building a record" (catalog Section 5). No raw score is ever displayed: bands and tiers, not numbers to game (TW-646 Section 7.5). The chip and the tier are a pair by design: the chip is the verified fact, the tier is the earned honor, and the header shows both because the room reads both.
2. **Gold discipline:** standing renders in Ledger Gold (`--soc-standing`, `--soc-standing-fill`) and gold appears nowhere else in the member UI except the collectible shelf. Gold is the currency metal; if everything is gold, nothing is.
3. **The standing detail:** every input behind the standing, listed in plain language with its count (nights out, show rate with its counts, would-meet-again rate with its review count, hosted-clean rooms, current streak), plus which force last moved the standing: dormancy or conduct (TW-646 Section 7.5 requires the explanation to distinguish "you went quiet" from "you did something"). A member who cannot see their record cannot improve it; this screen is that promise rendered.
4. **Progress to the next tier:** the next tier's missing conditions stated as facts ("2 more nights out for Fixture; your show rate already qualifies"). Computed from the same projection, never a percentage bar and never an estimate.
5. **Entitlements in force:** the member's live access rewards from the catalog (their RSVP window, host capacity, guest passes remaining, handle claims), each with its standing gate named. When an entitlement is suspended (guest entitlement after incidents, TW-646 Section 7.8), the row shows the suspension, its reason category, and its end or review path. Enforcement state is never a surprise in this UI.
6. **Watch and restricted bands:** while the safety band is watch or restricted, the tier is computed but NOT displayed and its entitlements stay inactive (TW-646 Section 7.5). The standing screen renders the catalog's honest version: no tier title, the standing detail still visible to the member, and the path back stated (the band recovers on conduct and time, per the spec). The profile other members see shows no tier either.

## The collectible shelf

- Pins render in the order earned, each as a mark plus its name and its one-line proof ("Saturday Hike, 8 straight", "50 nights out, verified"). The shelf is a list, not a grid of identical cards: each pin's proof line is different because each was earned differently.
- Pins are derived, never granted (TW-646 Section 7.9): there is no claim, redeem, or mint interaction anywhere in this pattern. A revoked pin leaves the shelf, and the standing detail shows the revocation with its plain-language reason (the shelf is only worth trusting if it can be corrected, catalog Section 3).
- The Full Passport renders as the shelf's centerpiece when held (catalog Section 3), with the district set shown as named places, completed ones in gold.

## Year Out and the standing card

- **Year Out (catalog Section 5, spec Section 7.10):** a December delivery, rendered as a shareable card: Twistor Social branding, the member's first name and tier, three headline numbers, nothing finer than neighborhood geography, nobody else's data. The in-app version carries the full sections (nights, districts, streak, people met as counts, rooms hosted, pins earned, the standing journey).
- **Standing card:** member triggered export of the same facts with the verification link; revoking the link leaves an unverifiable picture, which is the intended failure mode (catalog Section 5). Adult artifact only: no standing card exists in the teen band (catalog Section 7).

## What never appears here

- No purchase, boost, gift, or transfer affordance of any kind, at any tier, in any state. Social currency is never purchasable (TW-646 Section 1 red line); the UI contains no dormant commerce surface that a future toggle could wake up.
- Standing never renders on the Door Pass verify page or in the ghost town (TW-646 Section 7.5 display rules; pattern 07 holds that line).
- Teen standing (phase 2) renders inside teen surfaces only, capped at Pillar, with no guest pass rows and no venue perks (catalog Section 7; TW-646 Section 7.7 clamps).
