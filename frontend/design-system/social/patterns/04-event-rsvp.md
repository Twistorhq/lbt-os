# Pattern 04: Event page and RSVP waves

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 4 (events, RSVPs), 5.9 (visibility), 7.7 (RSVP windows and capacities), 7.8 (guest passes), 8 (location reveal, attendance toggles).

## Page anatomy (member view, mobile)

Top to bottom, in reading and thumb order:

1. **Header block:** event title (Fraunces), host line (display name plus standing tier name), group name, band chip (the event's age band as text: 18+, 21+), series mark when the event is a series instance ("Saturday Hike, week 12").
2. **When and where (coarse):** date, start and end time, neighborhood and city, static map preview card. The exact location is never on this page until the reveal (below); list and detail payloads carry coarse area only (TW-646 Section 4).
3. **The RSVP block (the thumb anchor):** the member's own state and their one primary action, in the fixed bottom action bar:
   - Window not yet open for their tier: the bar shows their personal opening time ("Your window opens Tuesday 6:00 PM") and a notify-me secondary action. The event page shows each member their own opening time (TW-646 Section 7.7); it never shows a generic countdown to someone else's wave.
   - Window open, seats available: RSVP primary action (56px). Couple RSVP is offered as a linked secondary choice when a couple link is active (TW-646 Section 4: two first-class rows, one per person).
   - Full: waitlist join, with the member's waitlist position rule stated plainly (band, then RSVP time, per TW-646 Section 7.5); no fake scarcity counters, no exact remaining-seat numbers rendered as pressure.
   - Going: the state shows as confirmed, with Decline as a text action of equal dignity (a timely decline discharges the show-rate obligation, TW-646 Section 7.5; the design treats declining honestly as a first-class act, not a buried cancellation).
   - Guest passes (Pillar and Keyholder, TW-646 Section 7.8): an attach-a-guest row inside the RSVP block, showing passes remaining for this event and the plain accountability line before use (the guest's night writes to the anchor's record; catalog Section 2). The attach flow is one sheet: guest first-name label, the band attestation as an affirmative statement, confirm.
4. **Who is going:** the roster of verified attendees (display name, photo, age band chip at standard size, standing tier name), governed by each attendee's own "show that I attended" setting (TW-646 Section 8). Guests render as "Guest of [anchor first name]", never with a fabricated profile.
5. **Details:** host description, what to bring (from the reminder workflow), capacity context in words ("Small room, 12 seats"), the group's tier floor when it raises the bar above verified.
6. **Location reveal card:** appears only when every reveal condition holds at request time (TW-646 Section 8): verified session, group membership, tier at or above the event minimum, RSVP going, inside the window (48 hours before start until event end), no ban or block conflict, trust band above restricted. The reveal is an explicit tap, and the member is told reveals are logged (TW-646 Section 4) in the same breath as the button. The revealed view carries the member's display name as a visible watermark over the address block (screenshots cannot be prevented; attribution deters sharing). Arrival instructions render in venue-boosted type, because this card gets read standing outside a building.
   **Home events change this block** (pattern 09): the venue is a residence, the reveal window is the short home window (opens 3 hours before start), the RSVP itself is a request until the host approves (close friends are confirmed on RSVP), and the reveal card exists only for close friends and host-approved confirmed guests. Everyone else's event page carries the coarse area and, where relevant, their request state, at every stage.

## Series instances

A series instance is an ordinary event page with two additions: the streak line for members on a run ("Your streak: 6 straight") rendered from the trust projection, and next-instance navigation. Editing and cancellation are the host's desktop work (pattern 08); the member surface only reads.

## Rating enforcement on this page

Opening this page by shared link under the target's band returns the standard restricted shell: the same shape as a target that does not exist, no title, no venue, no host, no schedule (TW-646 Section 5.9). The pattern renders that shell identically for not-found and restricted; the UI never distinguishes them, and Open Graph previews for above-lowest-band targets are platform-generic by the same section.

## Accessibility notes

- The RSVP bar is reachable by keyboard and screen reader in the same order as the visual thumb order; state changes (window opened, promoted from waitlist) announce via live region and also arrive through Notify (TW-646 Section 7.6).
- The watermark is decorative to assistive tech (`aria-hidden`); the address itself is plain text, readable and copyable by the member it was revealed to.
