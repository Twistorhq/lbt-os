# Pattern 09: Close friends, audiences, and home events

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Decision: Justynn, 2026-10-20. Home hosting is allowed on Social. Serves and extends: TW-646 Sections 4 (posts, events), 8 (visibility scopes, location reveal), 9 (launch shape: this decision overrides the soft-launch assumption of public venues only, recorded in the README).

## The model in one paragraph

Every member curates a close friends list. Any post or event can be scoped to an audience: the container default (the group), close friends, or a hand-picked set of people. A home address is the most protected fact in the product: it is visible only to the host's close friends and to host-approved confirmed guests, only inside the reveal window, and never public in any state, at any stage, on any surface, including notifications, previews, and the index.

## A. Close friends list management

- The list lives in profile settings: "Close friends", with add and remove as the only operations. Adding picks from people the member actually knows on the platform (group co-members and confirmed connections), searchable by display name; the picker shows photo, name, band chip, and tier (pattern 00 chip rules).
- **One-way and quiet.** The list is the member's own curation (the Instagram close-friends model): people are not notified when they are added or removed, the list is visible to no one but its owner, and being on a list grants nothing until the owner scopes something to it. A member never learns whose lists they are on; they learn only that a specific post, event, or address was shared with them, at the moment it is shared.
- Removal takes effect immediately and everywhere: content scoped to close friends stops rendering for the removed person on their next request, the same fail-closed posture as band visibility (TW-646 Section 5.9). Anything they already saw is history, the same honesty the location reveal carries; the UI says exactly that in the remove confirmation, in plain words.
- Teen band (phase 2): a teen's close friends list can contain only teen-band members inside their school and area scope, under the Section 5.10 voice and visibility rules. No adult can be on a teen's list and no teen on an adult's, because the no-reply wall and band invisibility bind first; the picker enforces it by simply never offering cross-band people.

## B. The audience picker (compose time, thumb-first)

Used by the post composer (pattern 03) and event creation. One control, always visible, never buried in a settings screen:

- **The audience row** sits directly above the composer (for posts) or in the venue block (for events): a labeled control showing the current audience in words: "Group: Saturday Hike", "Close friends", or "Pick people (6)". Minimum height 48px, full width, bottom half of the screen at compose time.
- Tapping opens one bottom sheet with three options, each a 56px row with a one-line consequence statement:
  - **The group** (or the profile default for standalone posts): everyone who can see the container sees this.
  - **Close friends:** only your list. For a home event, the sheet adds: close friends are auto-confirmed and can see the address in the reveal window.
  - **Pick people:** a hand-picked set for this one item. Opens the people picker (checkbox rows, photo, name, chip, tier; a running count in the sheet header; Done in the thumb bar). A hand-picked event audience works like a guest list: named people only, nobody else in the group sees the event exists.
- **Defaults are the safe ones.** A post defaults to the container audience and remembers the member's last deliberate choice per container. An event defaults to its group. Choosing close friends or pick-people is always a deliberate act, never a sticky accident, and switching an existing item's audience later re-runs the visibility rules immediately (narrowing hides it from the dropped people on next request; widening is announced in the edit sheet for what it is).
- Hand-picked and close-friends content inherits the same rating enforcement as everything else (TW-646 Section 5.9): audience scoping narrows within the band; it never widens past it. A close friend under the band still does not see the item.

## C. Home events: the address states

A host marks an event's venue as **home** at creation (the venue block asks, in plain words, "Is this at someone's home?" and marking it turns on every rule in this section; the address itself lives in the bronze location record, TW-646 Section 4, exactly like a venue address, and is never a post, a description line, or a notification field).

### Who can attend

- **Close friends of the host:** RSVP works as usual and they are confirmed on RSVP. They are the home audience by standing invitation.
- **Anyone else the event is visible to:** their RSVP is a **request**. It lands in the host's approval queue (pattern 08) as a pending state the requester sees honestly ("Requested, waiting on the host"). Host approval makes them a **confirmed guest** for this event. Host decline closes the request with a plain notice; no reason is required and none is displayed. An unapproved requester holds no seat, sees no address, and does not appear on the roster.
- **Guest passes at home events:** an anchor's guest (TW-646 Section 7.8) is a confirmed guest only when the host approves the pass on the roster like any other guest; the anchor's own confirmation never auto-confirms their guest at a home.

### What each audience sees, stage by stage

| Stage | Close friends (confirmed) | Host-approved confirmed guests | Everyone else who can see the event (group members, requested, waitlisted) |
|---|---|---|---|
| Listed and open | Coarse area (neighborhood), "Home event" marker, date and time | Same | Same |
| After RSVP or approval | Coarse area; reveal state line ("Address unlocks day of") | Coarse area; same reveal state line | Coarse area only; requesters see their pending state |
| Reveal window (home window: opens 3 hours before start, closes at event end; pattern proposal flagged for spec reconciliation, since TW-646 Section 8 sets 48 hours for venue events) | Full address via the gated reveal: explicit tap, logged, watermarked with the viewer's display name (the Section 8 reveal rules, unmodified) | Full address via the same gated reveal | Coarse area only. The address does not exist for them in any payload |
| During and after | Address remains in their reveal history per the evidence rules; post-event surfaces show neighborhood only | Same | Neighborhood only, forever |

### Hard rules (no surface may break them)

- The home address never appears in: the public index or any listing, group landing shells, feed posts or recaps, celebration posts, Open Graph or link previews, Notify messages (reminders carry coarse area only, as they already do), the Year Out, or the standing card. It is served only by the gated reveal endpoint to the two confirmed audiences, inside the window.
- Map surfaces for a home event render the static preview at neighborhood zoom centered on a coarse point, never the parcel; directions handoff (TW-646 Section 10.2) is offered only to viewers who hold the reveal.
- The host's own profile never displays their address or links their identity to the home venue record; the roster shows attendees the host's display name, nothing more.
- Who-went visibility and the member's attendance toggle (TW-646 Section 8) apply unchanged, and a home event's recap names the neighborhood, never the street.
- A block between any attendee and the host voids that attendee's confirmation and reveal, immediately, per the absolute blocks rule (TW-646 Section 8).

## D. What this pattern needs from the spec (flagged, not hidden)

1. TW-646 Section 9's soft-launch line (public venues only) is overridden by Justynn's 2026-10-20 decision; the OpenSpec change should amend Section 9 and the Section 12 open question 2 drops as decided.
2. The RSVP request and host-approval state for home events is a new RSVP sub-state the data model does not yet carry (`rsn_rsvps` has going, waitlist, declined, no_show); Zeke's OpenSpec (TW-648) owns adding it. The guest-pass attach flow needs the host-approval flag at home events.
3. The 3 hour home reveal window is this pattern's proposal against Section 8's 48 hour general window; tighter is right for a residence, and the amendment should say so explicitly.
4. Angela's lane: home hosting raises the meetup-liability questions in TW-646 Section 12 (item 6) from soft launch to launch; the copy gates for the home marker and the approval flow belong on her list before the first home event is created.
