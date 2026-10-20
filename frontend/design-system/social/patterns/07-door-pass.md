# Pattern 07: Door Pass presentation and the verify page

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-647 Sections 2.3 (door mode), 3 (verify flow), 3.2 (result states), 5 (state at scan), 6.3 (door photo), 8 (member scan history). Parent: TW-646 Sections 5.8, 7.5.

The Door Pass has two Sofia surfaces: the member's door mode (on the member's phone) and the verify result page (on the venue's phone). Both render in the VENUE scheme at all times. Both are designed for the worst interface conditions in the product: a doorway, at night, with a line behind the member and a stranger holding the other phone.

## A. Door mode (member surface)

Entry: one tap from the home surface, labeled "I'm at the door". Full screen takeover; nothing else in the app exists while it is open.

Composition, in the order a door person reads it:

1. **The QR**, at maximum practical size, centered in the upper two thirds. It encodes only the verify URL with the current token (TW-647 Section 2.1); nothing about the member is readable from the code itself.
2. **The countdown ring** around the QR block: token lifetime, rotating silently as fresh tokens mint underneath (roughly every 25 seconds against the 45 second lifetime, TW-647 Section 2.3). The ring is functional state, not decoration; with reduced motion it renders as a numeric seconds count.
3. **The member's own door photo thumbnail**, small, beside the brand line, so the member can confirm at a glance that the pass on screen is theirs.
4. **The brand line:** "Checked at the door", the member-facing promise in the decided badge language.
5. **Exit:** one 72px control, bottom anchored. No navigation, no tab bar, no notifications render inside door mode.
- Screen brightness raises where the browser permits (TW-647 Section 2.3). The surface never times out to a lock state mid-presentation on its own; exit is the member's act or the OS's.
- **Why my pass stopped working:** when minting is refused (restriction, revocation, band change; TW-647 Section 5), door mode does not render a dead QR. It renders the member's own explanation surface: the real reason in plain words and the appeal path where one applies (TW-646 Section 6.6). A door is not a moderation hearing, but the member's own phone is the right place for the truth; the venue page never learns any of it.

## B. The verify result page (venue surface)

One round trip, tiny shell, one small photo image, nothing else loads (TW-647 Section 3.1): the page is sized for bad venue Wi-Fi. It renders exactly three result families and no fourth state exists:

1. **Valid.** In reading order: the member's door photo, large (the human match is the control, TW-647 Section 1); the band, "21+", at `--soc-text-door-band` (72px); the verified-at clock in server time, seconds ticking, tabular numerals at 36px; and the animated validity element: a rotating color ring with a shimmer sweep, seeded per scan, with the four-character scan word rotating every 10 seconds beside the clock. A screenshot freezes the clock and kills the word; a still green screen is visibly dead (TW-647 Section 3.2). This animation is a security control and the standing documented exception to the no-decorative-animation rule: its motion is the proof of liveness of the result itself. `prefers-reduced-motion` stills the ring only; the clock and scan word are text and keep rotating, so the anti-screenshot property survives.
2. **Not valid.** One generic page, the spec's exact words: "Not valid. Ask for physical ID." Every failure class renders identically; the page teaches a scanner nothing about which check failed (TW-647 Section 3.2). Rendered in the caution register: high contrast, no red theatrics, no member data of any kind.
3. **Unavailable.** A distinct page, never styled like a pass and never a soft yes: "Verification unavailable. Ask for physical ID." (TW-647 Sections 3.2 and 7: the degradation rule is uniform, and the fallback is the physical ID the member already carries.)

Design rules for the venue surface:

- The valid banner block uses `--soc-valid` as a full-width field with near-black text (contrast verified in README.md), because green must read at arm's length in mixed bar light. Color is still never the only signal: the band numerals, the ticking clock, and the scan word carry the result in text.
- The photo is never cached, bookmarkable, or retained on the scanner's device (signed URL expiring in 60 seconds, no-store on page and image; TW-647 Section 3.2). The page leaves nothing behind when it closes.
- No trust state, no standing tier, no show rate ever renders here (TW-647 Section 5.3). The door sees photo, band, and validity. Nothing else exists on this page.
- The verify domain is printed in words on the venue signage kit (TW-647 Section 6.4); the page footer repeats the domain in text, because the domain is the taught anti-phishing check.

## C. Member scan history

In the member's surfaces (day scheme): each scan with venue display name or "unregistered scanner", city and neighborhood, time, and result (TW-647 Section 8). An unrecognized scan carries the dispute action on its row: one tap freezes minting and opens a review, because an unknown scan means someone presented their credential. Internal failure classes are visible to the member's own dispute flow and to staff tooling, never to venues.
