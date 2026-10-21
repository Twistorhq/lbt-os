# Pattern 01: Verification capture (T2, "Checked at the door")

Prepared by Twistor Holdings LLC. Part of the Twistor Social design foundation (TW-652).
Serves: TW-646 Sections 2, 5.1, 5.2, 5.3, 5.7, 5.11. Door photo step: TW-647 Section 6.3 (behind its legal gate).

## What this flow is

The door check of the whole product. An unverified (T0) account lives in the ghost town (pattern 02) until it passes T2 here. The flow is five engine stages (TW-646 Section 5.2) presented as one calm, guided capture. Positioning per the decided doctrine: this is framed as the door check everyone already accepts going out, with the badge language "Checked at the door", never as a bureaucratic application.

## Entry points

- The verify call to action on every ghost surface (required by TW-646 Section 5.11).
- The moment any ghost action routes to verification (join, RSVP attempts): the sheet explains in one line what verification unlocks, then offers Start or Not now. Not now returns the member to exactly where they were.

## Steps (one decision per screen, bottom-sheet composition)

1. **Consent first, separate and plain.** Before any capture: what is collected, why, how long it is kept, when it is destroyed (TW-646 Section 5.7). Its own screen, its own affirmative tap, never bundled into terms acceptance. Declining returns to the ghost town with nothing captured.
2. **Document capture.** Camera viewfinder with a document frame guide, edge detection feedback, and capture guidance as part of the engine (lighting, distance, glare prompts; TW-646 Section 5.3 is explicit that input quality is the cheapest accuracy). Front of license, then back (PDF417), or passport photo page. Retake is free and unpenalized, and the copy says so: a false reject is an inconvenience with a remedy, never a verdict on the person.
3. **Selfie and liveness.** Guided capture, not an upload (TW-646 Section 5.2 stage 4): the active challenge (turn, follow the target, blink) renders as a large face guide with one instruction at a time in 24px type. Instructions are visual first, words second, because the member may be mid-flow in either launch language.
4. **Door photo step (TW-647 Section 6.3).** Immediately after a pass verdict, its own consent line: this photo is kept so door staff can match a face to a pass, shown only at scan moments. Separate from verification consent. Ships behind the Angela consent gate (TW-647 Section 9); until it clears, the step does not render and the pass does not mint.
5. **Verdict.** Three renderings only: passed (the band is stated as a band, never a birthdate: "You are verified 21+"), routed to human review (with the expected window and what happens next, in plain language), or not passed (the remedy path named: retake guidance or human review, never a dead end). A banned-anchor refusal renders as the spec's generic inability to verify (TW-646 Section 6.2) with no signal about which check matched.

## Design rules specific to this flow

- Day scheme. This flow happens at home, in daylight, before the first night out; the venue scheme is reserved for in-room moments.
- Progress is a simple step count ("Step 2 of 4"), never a percentage bar and never a fake sequence of decorative numbers.
- The camera surfaces never show other app chrome: no tab bar, no feed notifications. One task, full attention.
- Failure states follow the engine's honesty posture (TW-646 Section 5.4): the copy never claims the check is perfect, never uses the words "fraud" or "fake" about the member, and always names the human review remedy.

## Accessibility notes

- Liveness challenges have an accessible alternative path: a member who cannot perform the head-turn challenge routes to human review with assisted capture, never to a fail. This is a launch requirement, not a backlog item; the benchmark gate in TW-646 Section 5.3 measures demographic error, and the UI must not add motor or vision error on top.
- All guidance text meets the venue-size floor even in day scheme: capture instructions at 18px minimum.
- Full Spanish parity in this flow is a launch requirement (TW-646 Section 3), human translated and reviewed, including the consent screens.
