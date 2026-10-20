# Pattern 05: The check-in loop, state by state

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 7.1 (geo arrival), 7.2 (encounter confirm), 7.3 (welfare check), 7.4 (write-up), 7.6 (scheduling), 7.8 (guest presence).

This is the product's signature flow and its safety core, benchmarked on Uber's trip-safety stack (TW-646 Section 7). The loop produces arrival proof and welfare signals, never a location history: the device computes the geofence verdict locally and sends a verdict, an accuracy bucket, and a timestamp. Raw member coordinates never cross the wire (TW-646 Section 7.1), and no screen in this pattern ever shows a member's location to anyone, including the member.

**Scheme: VENUE for every live state.** From the arrival window onward, the session surface renders in the Medianoche venue scheme (tokens.css): dark room, boosted type, maximum contrast. The session header (event name, session state word) and the persistent safety button stay fixed across all states; everything else changes underneath them. That fixed frame is the orientation system: wherever the night goes, the member always knows where the safety surface lives.

**The persistent safety button.** Present in every live state, fixed above the bottom action bar, reachable by the right thumb without looking. One tap opens the help sheet (state 5 below). A long press fires the silent alert (trusted contact plus staff safety queue notified, evidence packet frozen, screen does not change; TW-646 Section 7.3). The button is labeled in words ("Safety"), never an icon alone, and it is a screen reader landmark announced in every state.

## State 0: Scheduled (before the arrival window)

- Entry: the event page (pattern 04) gains a session card at start minus 30 minutes, when the arrival prompt lands (TW-646 Section 7.6). Before that, the event page is the surface and this pattern is not active.
- The session card shows: the event, the coarse area, the arrival window ("Check-in opens 6:30 PM"), and the revealed location card if the member has revealed it.
- No actions are live yet. The card's job is orientation: tonight, this room, this plan.

## State 1: Arrival (geo_soft), with the QR strong path

- Primary action, one tap, 72px, bottom anchored: "I've arrived." The tap triggers a single one-shot geolocation fix; the geofence verdict is computed on the device.
- Results render as three honest outcomes:
  - **Inside:** arrival recorded, the state advances to Arrived. The confirmation shows the proof tier the night is running on ("Checked in, venue area") without exposing the verdict machinery.
  - **Outside or weak fix:** the surface says plainly that it could not place the member at the venue, and offers the two real alternatives in order: try again (a fresh fix), or the venue QR. It never accuses; GPS error indoors and downtown is a documented design fact (TW-646 Section 7.1), and the copy treats it that way.
  - **Early or late window:** the arrival is recorded as early or late per the spec, stated in words, not treated as fraud.
- **Venue QR (qr_strong):** the host's door view shows the session QR; the member scans it with the in-app scanner (a full-screen viewfinder state of this pattern). A successful scan upgrades the proof and the state line says so ("Checked in at the door"). Host confirm is the third proof path and needs no member UI beyond the host's roster action (pattern 08 is the organizer side; the member sees the resulting state).
- Guests have no arrival surface of their own: the anchor's pass row shows the guest state (attached, checked in by host confirm, completed, no-show) inside the anchor's session view (TW-646 Section 7.8).

## State 2: Arrived (encounter confirm window)

- On mutual arrival, the encounter confirm prompt lands (TW-646 Section 7.6). It renders as a card stack, one person per card, each answerable in seconds:
  - The person's verified profile photo, large, display name, and their age band chip at glance size (pattern 00), because the member is about to compare a face to a face and the band is part of who the platform says this person is.
  - Question 1: "Did you meet [name]?" Two 56px answers: Yes, No.
  - Question 2 (on Yes): "Were they who they said they were?" Yes, No. The photo stays on screen during this question; the member is comparing a face to a face.
- A No on question 2 is the identity strike path (TW-646 Section 7.2): the confirm screen says, before the tap is final, what a No does in plain language (it opens a verification review; it does not accuse anyone publicly). One deliberate confirmation step, then done. The design never makes a strike easier to file than to mean.
- Cards are dismissible to later (the write-up re-asks); unanswered cards return at the midpoint and at write-up, because corroboration is the loop's evidentiary spine (a completed night needs a second verified identity, TW-646 Section 7.5).

## State 3: In progress (mid-event welfare check)

- At the session midpoint the welfare prompt renders as a sheet over the session surface: "How's it going?" Three full-width answers, 56px each, in this order: "I'm fine", "I need a minute", "I need help" (response enums per TW-646 Section 4).
- **Fine:** recorded, sheet closes, the night continues. The session surface shows a quiet "Checked in, all good" line.
- **Need a minute:** recorded; the sheet offers the help sheet one level down without alarm framing, and re-prompts once later in the session.
- **Need help, or no response to two prompts:** the help sheet surfaces immediately (state 4). No response is treated as a signal to escalate prompts and notify the trusted contact, never as proof of danger (TW-646 Section 7.3); the copy respects that ambiguity while never ignoring the signal.

## State 4: The help sheet (available from every live state)

Rendered in this exact order (TW-646 Section 7.3), each row a 56px target:

1. **Notify my trusted contact:** one tap via Notify, with the pre-written message shown and editable before sending.
2. **Share my live location with my trusted contact:** member initiated, time boxed to 60 minutes, delivered as an expiring link. The platform stores only that a share happened, and the sheet says so.
3. **Get exit help:** ride links, the venue address shown large for reading to a driver, and the "call me in 10 minutes" scheduled check-in.
4. **Emergency services:** a `tel:911` link, rendered as its own row, never styled as an app action.
- **Discreet mode:** one control renders the whole sheet as a plain settings-looking page (neutral title, list rows, no safety vocabulary), because the person next to the member may be the problem. Discreet mode is reachable in at most two taps from the safety button and is itself unlabeled with any word that would read aloud badly.

## State 5: Completed (the session ends)

- When the session ends (host ends it, or the scheduled end passes), the surface transitions: the safety button retires to a standard "Report or get help about this event" row that remains available (reporting entry points exist on the event after the fact, TW-646 Section 6.1), the state line reads "Completed", and the member's own night summary shows: their check-in proof, who confirmed them in aggregate ("Confirmed by 3 people"), and their guest outcome if any.
- Nothing about anyone else's welfare responses, confirms given about them, or reviews is shown to a member beyond their own aggregates (TW-646 Sections 7.4 and 8, couple privacy included: a partner's welfare record is never the other partner's to see).

## State 6: Reviewed (post-event write-up)

- Thirty minutes after the end (next-morning fallback), the write-up prompt lands. Per person met, one card each, structured answers first: how was the interaction (the spec's structured scale, rendered as four labeled segments, not stars; stars import a rating economy this product does not run), good person (Yes or No), would you meet again (Yes or No).
- Per event: would you join again (Yes or No), plus optional free text. Free text carries the moderation note in plain words before the field (person review text is never displayed to the subject; it exists for staff review and pattern detection, TW-646 Section 7.4).
- The guest-conduct item appears only for sessions that included a guest, and only for members who shared the room (TW-646 Section 7.8).
- Submitting completes the loop: the member sees their own aggregate update (their would-meet-again rate lives in pattern 06), because a reputation you cannot see is a reputation you cannot improve.
- Skipping is allowed and costs nothing visible; the loop's design gets its completion rate from being genuinely short, not from blocking exits.

## Failure and honesty rules for the whole loop

- Every prompt is one tap deep from its notification (TW-646 Section 7.6). In v1, delivery is email plus in-app inbox (push is a Core stub); the in-app session surface is the mechanism, notifications are the reminder. No state in this pattern depends on a notification having been seen.
- The hard rule renders verbatim in the help sheet's footer: this system gets help options to a person fast and keeps records; it does not dispatch rescue and never claims to (TW-646 Section 7.3). Copy review keeps that sentence exact.
- On total connectivity loss mid-session, the surface keeps the last known state and the help sheet's local rows (the venue address if revealed, the 911 link) functional; welfare answers queue as recorded-on-device (pattern 00).
