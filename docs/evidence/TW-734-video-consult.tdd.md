# TW-734 — Video consult: TDD evidence

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-734 (high, Zeke Okafor) + Marcus's refinement comment
2026-10-28 (Justynn's product call): a customer-facing VIDEO CONSULT
the shop sells. A homeowner video-calls their HVAC/plumbing company
from the browser (link/QR, no app install) and talks to a real tech
directly — show the furnace, the leak, the panel. Primary use:
DIYers who want pro help without the pro coming out. If video can't
fix it, the consult is the diagnostic and converts to a booked visit.
The shop charges per-minute (micro-tolled) or one flat rate,
shop-configured; Twistor's toll runs on the M1 ledger in both modes.
Branch `zeke/tw-734-video-consult` off lbt-os main @ 3142dc9.
Checkpoints: RED `c09dd47`, GREEN `56c190f`, this evidence commit.

## 2. Research — what is proven vs wired later

**Fonoster (self-hosted stack, TW-727) — audio only, verified.**
Fonoster core is a programmable VOICE stack: its VoiceServer/
Autopilot verbs (Say, Gather, Play, Record, Stream) are audio-only.
SIP/WebRTC is the transport (Routr + RTPengine), but the core API
(v0.34.3, the version our compose pins) exposes no video verb, no
video recording, and no browser video client. Fonoster's video
product is Goodtok — a separate project (PeerJS widget + agent
front-office for e-commerce support) that is NOT part of the
fonoster/fonoster compose we ship and was not adopted. Conclusion:
Fonoster/Autopilot carries the consult's audio AI side (disclosure,
announcements) through the existing TW-284 voice-agent port; it does
not carry the video.

**Jitsi (Twistor's own package, TW-729) — the room, not deployed.**
twistor-infra `services/jitsi-meet` (merged today): four services
pinned to stable-11248-2, PUBLIC_URL https://meet.twistor.co,
ENABLE_AUTH=1 / AUTH_TYPE=internal (only authenticated host accounts
open rooms), ENABLE_GUESTS=1, ENABLE_LOBBY=1 (guests wait in the
lobby until the host admits). Jitsi rooms are implicit (a room
exists when its URL is opened), so the backend's room provider is a
URL builder: room `twistor-consult-{session_id}` on the configured
base URL. The tech is the host; the homeowner joins as lobby guest.
meet.twistor.co has NO DNS, NO host, NOTHING DEPLOYED — room links
are go-live wiring, and the provider fails closed
(ProviderNotConfigured) until `video_consult_jitsi_base_url` is set.
The package ships no Jibri, so production recording control also
fails closed until Jibri is wired; the announcement-before-recording
discipline is proven against the fake room provider.

**Transcription — seam, not bridge.** No faster-whisper exists in
the lbt-os backend today (it lives in the workspace TTS/video
lanes). `LocalWhisperTranscriber` is the seam: it raises
ProviderNotConfigured until the local bridge wires at go-live. The
answering machine stores messages with
transcription_status="pending" rather than dropping them (TW-208).

**Payments — receivable, honestly.** The only Stripe surface in this
backend bills the SHOP's Twistor subscription (org plans); there is
no homeowner-facing collection surface. The metering service
computes the charge and stamps it on the session as
charge_status="receivable". No payment integration was invented;
collection is go-live wiring.

## 3. Task report

**Tenant boundary (tested first).** Every org-scoped entry point
(tech_join, get_session, end_session, convert_to_visit,
set_recording, settings) filters by org_id; cross-org is 404 and
writes nothing — the Rosa M1 lesson from TW-284. The join token is
stored only as its SHA-256 hash; customer_join with an unknown
token is 404 and the public view carries no phone, no hash.
Command: `pytest tests/test_video_consult_tw734.py -q` → 33 passed.

**Session core + state machine.** create_session gates on the
global kill switch AND the shop toggle (409 otherwise), speaks the
AI disclosure as utterance #1 BEFORE the room is created, then
transitions ringing → ai_triaging with the tech-join deadline
stamped. tech_join (org-checked) → live and starts the billable
clock. expire_triage before the deadline is 409; after it, the
session lands in message_left, meters at $0, and rings the owner
chain through TW-284's escalation (chain exhaustion is visible on
the session). leave_message (token) transcribes via the transcriber
port and stores an extractive summary; with the production
(unwired) transcriber the message is kept as pending.
convert_to_visit from live/ended/message_left books job +
appointment (source "video_consult"), confirms by SMS, and meters
a live consult up to conversion first.

**Metering (named rules).** Billable time = tech time only
(live_started_at → end); triage is never billed.
- per_minute: STARTED-MINUTE rule — billable_minutes =
  max(1, ceil(seconds/60)) when live; charge = minutes × the shop's
  per_minute_rate_cents. Twistor toll = $0.05 × billable_minutes,
  emitted as ONE `consult.session` toll row whose quantity IS the
  per-minute metering record.
- flat: charge = the shop's flat_rate_cents when live, any
  duration; toll = $0.50, quantity 1.
- never live (answering machine): charge $0, toll $0.50, quantity 1.
Exactly-once: metered_at stamp guards re-entry (end → convert
stays one toll, charge computed once) and the toll carries
idempotency_key `tw734-consult-{session_id}` as the backstop.

**Recording.** Starts OFF, never starts implicitly (proven through
end_session). Switching ON: events are exactly
[speak(announcement), recording_start] — the announcement
("This call is now being recorded.", the TW-284 constant) precedes
any recording; audit row lands in video_consult_events; toggling is
idempotent.

**Providers fail closed.** JitsiRoomProvider without a base URL and
LocalWhisperTranscriber raise ProviderNotConfigured; the router maps
unconfigured providers to 503, mirroring routers/calls.py.

## 4. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Cross-org tech_join/get/end/convert/recording → 404, zero writes | tests/test_video_consult_tw734.py (6 tests) | RED | PASS (33/33 suite) | RED c09dd47 (collection error, modules absent) → GREEN 56c190f |
| 2 | Join token stored hashed only; unknown token 404; public view has no PII fields | test_video_consult_tw734.py | RED | PASS | same |
| 3 | Disclosure is utterance #1, before room creation; consult gates 409 (global off / shop off) | test_video_consult_tw734.py | RED | PASS | same |
| 4 | Settings validation: bad mode / negative rate / window out of 15–600 → 400; settings org-scoped | test_video_consult_tw734.py | RED | PASS | same |
| 5 | Tech join → live, billable clock starts at join; join from wrong state → 409 | test_video_consult_tw734.py | RED | PASS | same |
| 6 | No-join window lapse → message_left + owner chain rung + escalation id on session; early expiry → 409 | test_video_consult_tw734.py | RED | PASS | same |
| 7 | Video message transcribed + summarized via fake; unwired transcriber → stored pending, not dropped | test_video_consult_tw734.py | RED | PASS | same |
| 8 | Convert from live and from message_left books job + appointment, SMS confirmation | test_video_consult_tw734.py | RED | PASS | same |
| 9 | Per-minute started-minute rule (61s = 2 min, 20s = 1 min), charge math, ONE toll (qty = minutes, $0.05/min) | test_video_consult_tw734.py | RED | PASS | same |
| 10 | Flat mode: flat charge at 30 min AND at 30 s; ONE toll ($0.50, qty 1) | test_video_consult_tw734.py | RED | PASS | same |
| 11 | Finalize idempotent: end → convert = one toll, charge computed once | test_video_consult_tw734.py | unit | PASS | same |
| 12 | Message-left: charge $0 / not_charged, exactly one $0.50 toll | test_video_consult_tw734.py | RED | PASS | same |
| 13 | Recording: announcement spoken BEFORE recording start; audit row; never implicit; idempotent | test_video_consult_tw734.py | RED | PASS | same |
| 14 | Production providers fail closed (Jitsi no base URL, whisper unbridged, factory) | test_video_consult_tw734.py | unit | PASS | same |
| 15 | TW-284 + TW-727 suites untouched and green | pytest tests/test_phone_automation.py tests/test_fonoster_provider_tw727.py | CI | PASS (44/44; 77/77 combined with TW-734) | this run |
| 16 | Full suite: no new failures vs clean-main control @ 3142dc9 | paired junit node-set comparison | CI | PASS — failed/error sets identical (28 nodes), delta exactly +33 (the new tests) | ~/scratch-tw734/{branch,control}.xml |
| 17 | Router mounts: 11 consult routes import-verified | python import of app.routers.consults with dummy env | manual | PASS | app.main import itself blocked by the pre-existing sandbox xmlsec failure — byte-identical error on the control tree, not this diff |
| 18 | Ruff gate on authored files | ruff check + ruff format --check (4 authored files) | CI | PASS — "All checks passed!", "4 files already formatted" | this run; config.py/main.py keep inherited format state per TW-284 precedent; the 1 ruff error on main.py (I001) exists identically on control |

## 5. Coverage and known gaps

- Coverage: the full TW-734 suite (33 tests) covers the service and
  provider contracts end-to-end against fakes. Full backend suite
  run on both trees (above).
- NOT covered / go-live wiring, by design: live Jitsi room join
  (nothing deployed), Jibri recording, the faster-whisper bridge,
  homeowner card collection (charge is a receivable), the worker
  that sweeps lapsed triage windows (expire_triage is the
  transition; the router exposes it), and router-level HTTP tests
  (TestClient is blocked in this sandbox by the pre-existing xmlsec
  failure — same limit TW-284 recorded; the router is
  import-verified only).
- Migration SQL is written to the house pattern (org-isolation RLS
  enabled explicitly, TW-710 lesson) but, like every migration here,
  is not executed against a live database in this sandbox.
- Early message-leaving (before the window lapses) does not ring
  the owner chain — there is no telephony handle on that path; the
  message lands in the shop's consult queue. The window-lapse path
  is the one that escalates. Stated here so nobody reads silence
  as notification.
- Summaries are extractive (issue + caller's first sentence). The
  local Ollama think bridge (already in the Fonoster stack) is the
  upgrade seam, not built here.

## 6. Surface contract for the frontend lane (Sofia)

Base path `/api/v1/consults`:
- Shop (authenticated): GET/PUT `/settings`; POST `/sessions`
  (returns session_id, join_url, join_token — token shown ONCE,
  room_url, triage_deadline_at); GET `/sessions/{id}` (masked
  phone); POST `/sessions/{id}/tech-join` (tech = caller);
  POST `/sessions/{id}/recording` {on, actor};
  POST `/sessions/{id}/expire-triage`;
  POST `/sessions/{id}/end` (returns billable_minutes, charge_cents,
  charge_status); POST `/sessions/{id}/convert` {requested_at?}.
- Customer (public, token is the credential): GET `/join/{token}`
  → {shop_name, status, room_url, room_name, recording_enabled,
  ai_disclosure_given}; POST `/join/{token}/message`
  {recording_ref} → {message_id, transcription_status, summary}.
- States: ringing → ai_triaging → live → ended, with message_left
  (answering machine) reachable from ai_triaging, and
  converted_to_visit reachable from live / ended / message_left.
- Customer page shape: open join_url → GET join info → join the
  Jitsi room as lobby guest (host admits) → if the session lands in
  message_left, offer the video-message recorder and POST the
  resulting storage ref to the message endpoint.

## 7. Plan-safety notes

No embedded instructions, override phrases, or untrusted directives
were encountered in the ticket, the repo, or the web sources read
for the Fonoster video research. The SaaSCounter feature list
("WebRTC", "Call Conferencing") is marketing copy for the Fonoster
platform generally; it was treated as a claim to verify, not
evidence — the shipped compose and the core API surface are what
this build relies on, and they are audio-only as documented above.
