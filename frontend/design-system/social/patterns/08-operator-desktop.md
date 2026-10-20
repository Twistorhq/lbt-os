# Pattern 08: Desktop operator layers (pattern level)

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 4 (organizer billing), 6.5 (admin and staff tools), 7.7 (roster and waitlist operations), 10.4 (claim a listing). TW-647 Sections 6.4 (venue onboarding), 6.5 (lane dashboard).

Desktop is the operator layer (TW-646 Section 1): the work here is desk work, done seated, with a keyboard, often over hours. These surfaces are desktop-first by doctrine, and they are the exception that proves the mobile rule: the member product lives on phones; the operation of it lives here. This run specs patterns only; component builds follow in later runs.

## Shared operator rules

- Day scheme, same tokens, same Fraunces and Inter pairing. Operators are inside the same brand; nothing about the console reads as a different company. Venue scheme is never used on desktop: it exists for dark rooms, not desks.
- Data-dense is allowed here and only here: tables with real sorting and filtering, tabular numerals, keyboard-first operation, visible focus everywhere. Density never leaks into member surfaces.
- Honesty rules bind hardest on operator screens: aggregates are labeled aggregates, sample or seed data is labeled SAMPLE DATA, and no console invents a metric the ledger does not hold.
- Every consequential action (ban, sanction, claim decision, trust override) requires a written reason in the action itself and lands in the audit log (TW-646 Sections 4 and 6.5). The UI makes the reason field part of the action form, never an optional afterthought.

## A. Organizer console (hosts and group owners)

- **Home:** the organizer's groups and series, upcoming instances with fill state (RSVPs against capacity, waitlist depth), and anything needing a decision (held seats nearing their 24 hour release, TW-646 Section 7.7; claim status; plan state).
- **Roster and waitlist operations:** the event roster as a table (member, standing tier, age band shown as the chip's text value, eligibility shown as eligible or not, never the underlying attestation; check-in state during the session; guest rows attached to their anchors). For home events (pattern 09) the console carries the host's **approval queue**: pending RSVP requests and guest-pass approvals with the requester's profile facts (photo, name, band, tier, show rate), approve and decline as one-click rows with no reason required, because at a residence the host's judgment is the gate and the product does not make them argue with it. Waitlist promotion follows band then RSVP time; the console shows the computed order and lets the host promote only into host-held seats out of order, exactly the override surface TW-646 Section 7.7 grants, with the limits stated inline (no override crosses the verified floor, the age band, or capacity).
- **Series management:** recurrence editing that affects future instances only, per-instance exceptions, pause and end. The console states the rule at the point of edit (TW-646 Section 4).
- **Group administration:** member removal, content removal, the group's meetup tier floor (raise only; the floor control renders its minimum at verified and cannot be set below it), group-scope bans, and the group safety queue with subject detail limited to what the admin needs (never verification data, never other groups' reports; TW-646 Section 6.5).
- **Session door view (live events):** the host's check-in roster during a session, the venue QR for strong check-in (TW-646 Section 7.1), host-confirm actions, and guest arrivals by anchor label. This is the one operator surface also used in a room; it offers a large-type mode using the venue token sizes, because a host at a door has the same lighting problem as a member at a door.
- **Billing:** plan state, the toll ledger rows for the organizer's own plans, promotions, and claims (TW-646 Section 4), with idempotency visible as one row per charge. Attendees never appear as billing subjects anywhere in this console.

## B. Venue dashboard (Door Pass partners, TW-647)

- **Lane view per location:** scans today and tonight, valid rate, busiest windows, and the live bundle counter against the plan (the no-surprise doctrine: 80 percent warning state renders on the counter itself, TW-647 Section 6.1). Aggregates only, always: counts and windows, never a member list, never subject references, never photos (TW-647 Section 8).
- **Locations, lanes, and staff:** lane activation per location, staff roles with the scan-only scanner view for door staff (no member data, no settings, nothing to leak from a borrowed staff phone; TW-647 Section 6.2), and plan management per location. A plan lapse degrades dashboard and bundle entitlements only; the dashboard says plainly that scans already served are unaffected (TW-647 Section 6.5).
- **Onboarding:** the claim ladder (business email domain, matching published listing, manual review fallback), location setup, and the signage kit: a generated, versioned print document per lane (the door sign with the verify domain spelled in words, and the staff quick card teaching what a valid result looks like: moving animation, live clock, photo). Kit copy ships only behind the TW-647 Section 9 copy gates; until they clear, the kit generator renders a draft watermark.

## C. Staff review and safety console (internal, pattern note only)

Specced in TW-646 Section 6.5 and owned as an internal tool: report queue with SLA timers, evidence packet freeze, suspension and ban actions, anchor lookup, trust override with mandatory reason, appeal handling on the Section 6.6 workflow. Two pattern rules carry from this foundation: no automated system bans a person from this console (automation queues and recommends; a human decides), and evidence reads are themselves audit logged. Detailed design lands with the safety build, not this run.
