# TW-727 — Fonoster provider for TW-284 (default replaces Twilio + OpenAI voice): TDD evidence

Prepared by Twistor Holdings LLC

**Source:** TW-727 (reporter Justynn, urgent). Directive, 2026-10-28:
"One of these repos tackles twilio so I want to use that one instead of
the paid version with open AI voice." The repo is Fonoster
(fonoster/fonoster, MIT — raw LICENSE read live by Marcus, Copyright
Fonoster Inc; v0.34.3 released 2026-10-26). Branch
`zeke/tw-727-fonoster-provider` in its own worktree, based on the
TW-284 tip `ec3f660` (branch `zeke/tw-284-phone-automation`, Rosa
APPROVE + Rex sign-off). Scope: build only — no merge, no push to
main, no deploy, no carrier signup, no number purchase, zero spend.

## What changed

- `backend/app/services/voice_providers.py` — Fonoster provider added
  behind the existing ports: `FonosterTelephonyProvider` (place_call /
  send_sms / start_recording / stop_recording) and `FonosterVoiceAgent`
  (speak), both delegating to an injected client. The factories now
  dispatch on `settings.phone_provider`: **"fonoster" is the default**;
  "twilio_openai" selects the legacy TW-284 Path A classes, which stay
  byte-for-byte functional and are marked DEPRECATED in docstrings.
  Unknown provider names raise `ProviderNotConfigured` (fail closed).
- `backend/app/config.py` — `phone_provider` (default "fonoster") +
  `fonoster_*` settings (API endpoint, workspace access-key pair, from
  number, Autopilot app ref). Legacy Path A fields annotated as
  deprecated for AI answering. The twilio_* keys are NOT deprecated
  globally: the M2 missed-call layer (call_service.py) still uses them.
- `backend/app/services/phone_automation.py` — docstring/comment
  updates only (provider-neutral wording; the `twilio_call_sid` column
  now documented as the provider call ref — name predates TW-727; no
  schema change, no migration).
- `backend/app/routers/calls.py` — one comment updated (provider-facing
  actions fail closed until the selected provider is configured). No
  behavior change; the Twilio-signed `/ai-voice-webhook` remains the
  legacy inbound path (see gaps).
- `deploy/fonoster/` — self-host package, config only:
  `docker-compose.yml` (Fonoster service set per upstream compose:
  apiserver, autopilot, routr-one, rtpengine, asterisk, dashboard,
  postgres, nats, influxdb + a local Ollama service; image tags pinned
  via `FONOSTER_IMAGE_TAG`, default v0.34.3), `.env.example` (secrets
  blank by design), `README.md` (go-live sequence), and
  `autopilot/trades-answering.agent.yaml` (the Autopilot agent).
- `backend/tests/test_fonoster_provider_tw727.py` — 20 tests.

## The Autopilot agent (deploy/fonoster/autopilot/trades-answering.agent.yaml)

Written against the verified Autopilot application schema (fonoster.com
agent.yaml example + docs.fonoster.com Autopilot concepts page):
`name` / `type: AUTOPILOT` / `speechToText{productRef, config}` /
`textToSpeech{productRef, config}` / `intelligence{productRef,
config{languageModel{provider, model, tools}, conversationSettings}}`,
deployed with `fonoster applications:create -f <file>`. Wired to the
TW-284 behaviors:

- **Disclosure first**: `conversationSettings.firstMessage` is the AI
  disclosure (mirrors the backend `DISCLOSURE_TEMPLATE`; the backend
  also speaks the shop-specific disclosure at session start through
  the voice-agent port — both paths disclose before anything else).
- **Recording**: off by default; the `set_call_recording` tool maps to
  the backend toggle, which speaks "This call is now being recorded."
  BEFORE recording starts and audit-logs every change.
- **Escalation**: the `escalate_to_owner` tool maps to the backend
  chain — all owner-set emergency numbers, priority order, bounded
  rounds, visible `exhausted` final state.
- **Booking + reschedule**: the `book_emergency_visit` tool maps to
  the backend intake (job + appointment + customer confirmation);
  auto-reschedule with the honest reason wording stays in the backend
  service, exercised through the Fonoster provider in tests.
- **Toll**: exactly one `call.ai_handled` per AI-handled call, emitted
  by the backend at session start; the agent never emits tolls.

## Research — hear / think / speak, and what local really means

Sources: fonoster.com (checked 2026-10-28), docs.fonoster.com
Autopilot concepts, the fonoster/fonoster repo docs (DeepWiki mirrors
of mods/autopilot, mods/apiserver, compose.yaml). Product catalog per
fonoster.com:

| Step | Fonoster providers | Local option? |
|---|---|---|
| Hear (STT) | Deepgram, Google | **No.** No local STT product exists in the catalog today. |
| Think (LLM) | OpenAI, Anthropic, Gemini, Groq, **Ollama** | **Yes.** Ollama runs on the self-host; the agent YAML defaults to it (`llm.ollama`, model `llama3.1`). |
| Speak (TTS) | ElevenLabs, Azure, Deepgram, Google | **No.** All four are cloud services today. |

So the honest shape: **think goes fully local; hear and speak still
run on a cloud provider key** (Deepgram for both in the shipped YAML —
one key, nova-3 / aura-asteria-en per Fonoster's own example). Anyone
claiming a fully-local Fonoster voice loop today is ahead of the
catalog. Platform facts that shape the build: the API server is gRPC
(port 50051; Autopilot voice server 50061; Routr SIP 5060-5063);
the only official SDK is Node (@fonoster/sdk); there is **no official
Python SDK**. PSTN reach is "any carrier" SIP trunking — Fonoster is
not a carrier.

### What a shop still pays for vs what goes away (no invented pricing)

- **Goes away on this path**: Twilio platform charges for the
  AI-answering path (per-minute voice, number rental through Twilio,
  Twilio messaging for these flows) and OpenAI Realtime voice charges
  (the TW-284 Path A cost model's two metered legs; the TW-725 brief's
  $0.27–$0.63 per answered call was built on them).
- **Stays**: a SIP carrier. The shop still needs a number and pays
  carrier per-minute rates — carrier choice and rates are the
  go-live step (Justynn's manual signup, same posture as the Twilio
  number was). No carrier pricing is quoted here because none was
  researched for this ticket.
- **Stays, smaller**: hear/speak cloud usage (Deepgram by default) is
  metered by that provider under the shop's own key.
- **New, fixed**: self-host compute for the compose stack + Ollama.
  Fonoster itself is MIT — no license fee, no per-minute platform
  charge from Fonoster.

## Task report

| Behavior | Validation command | RED | GREEN | Guarantee |
|---|---|---|---|---|
| Fonoster provider absent | `pytest tests/test_fonoster_provider_tw727.py -q` | 7 failed, 13 errors, 0 passed (commit 24fdc93) | 20 passed (commit 1f446a6) | Suite pins every contract below |
| TW-284 behaviors via Fonoster path (disclosure+toll-once, refusal, recording order/audit/idempotence, escalation order/stop-on-ack/bounded exhaustion, cross-org ack 404, intake booking+confirm, reschedule both reasons) | same file, 13 tests with a fake Fonoster client injected | RED | passed | Every provider-touching TW-284 behavior runs unchanged through the Fonoster providers |
| Default selection = Fonoster; legacy selectable | `test_default_provider_selection_is_fonoster`, `test_legacy_twilio_openai_still_selectable_but_not_default` | RED | passed | `settings.phone_provider` defaults to "fonoster"; factories return the right classes |
| Fail closed | factory-without-credentials, methods-without-client, unknown-provider tests | RED | passed | `ProviderNotConfigured` everywhere; never a silent live call |
| Autopilot YAML contract | `test_autopilot_yaml_disclosure_first_local_think_and_tools` (yaml.safe_load) | RED | passed | Disclosure is firstMessage; think = Ollama; tool names present; announcement text ships verbatim |
| Compose contract | `test_compose_declares_fonoster_services_config_only` | RED | passed | Upstream service set + gRPC/Autopilot ports declared |
| Existing TW-284 suite untouched | `pytest tests/test_phone_automation.py -q` | n/a | 24 passed | Zero behavior change to the gated TW-284 code |
| Router wiring | `import app.routers.calls` (dummy env, CI posture) | n/a | import OK; default provider resolves "fonoster" | Endpoints register; factories dispatch |

## Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Fonoster is the default provider; Twilio/OpenAI remain selectable, deprecated | test_fonoster_provider_tw727.py (2 selection tests) | unit | PASS | 20 passed, 2026-10-28 run |
| 2 | All TW-284 provider-touching behaviors hold through the Fonoster provider path | same file (13 mirrored tests, fake Fonoster client) | unit | PASS | same run |
| 3 | Fail-closed posture: no credentials → factory raises; no client/bridge → methods raise; unknown provider → raises | same file (3 tests) | unit | PASS | same run |
| 4 | Autopilot YAML carries disclosure-first, local think (Ollama), the 3 tool contracts, and the exact recording announcement | same file (YAML contract test) | unit | PASS | same run |
| 5 | TW-284 suite unchanged and green | tests/test_phone_automation.py | unit | PASS | 24 passed |
| 6 | Merge bar: zero new failures vs base-tip control | full suite both worktrees, `--continue-on-collection-errors`, junit node sets compared | CI | PASS | control (ec3f660) 1578 tests, 20f/1550p/8e (28 bad nodes); branch 1598 tests, 20f/1570p/8e (28 bad nodes); bad-node sets **identical**; collection delta exactly +20 (the new tests, all passing) |
| 7 | Lint posture unchanged | `ruff check app tests` in both worktrees | CI | PASS | exactly the 1 pre-existing main.py import-sort finding in both; `ruff format --check` passes on voice_providers.py, phone_automation.py, and the new test file |

## Coverage and known gaps (honest limits)

- **The Python↔Fonoster wire binding is go-live wiring, by design.**
  Fonoster's API is gRPC with a Node-only official SDK; there is no
  Python SDK to call. The providers delegate to an injected client;
  the production `FonosterSdkBridgeClient` (the SDK bridge deployed
  with the self-host stack) raises `ProviderNotConfigured` until the
  bridge is deployed — the identical posture TW-284 shipped for the
  OpenAI media-stream bridge, and the reason the factories can be
  merged safely before any deploy exists. Orchestration is fully
  covered via the fake client.
- **Inbound Fonoster calls do not flow through the backend webhook.**
  With Fonoster, inbound calls land on the SIP trunk → Routr →
  Autopilot application directly. The Twilio-signed `/ai-voice-webhook`
  stays as the legacy path. The provider-driven endpoints (recording
  toggle, auto-reschedule, settings, contacts, ack) already dispatch
  through the factories, so they follow `phone_provider` today.
- **Messaging (SMS) rides the same client seam.** Escalation and
  confirmation messages go through the provider's send_message;
  whether the chosen carrier's trunk carries SMS is a carrier
  selection fact for go-live, not verified here.
- **Compose is config-only and untested as a deployment**: no stack
  was started (zero-spend scope). Image tags pin via
  `FONOSTER_IMAGE_TAG` (default v0.34.3, the current release) and the
  exact apiserver env keys must be taken from the pinned release's
  upstream `.env.example` at deploy time — both are called out in the
  compose/README so nobody mistakes the package for a tested deploy.
- **Recording control semantics**: Fonoster recording is a voice
  session control, mapped here to start/stop on the call ref through
  the client seam; the announcement-before-recording ordering is
  enforced in `phone_automation.set_recording` and proven by the
  shared event log in tests, same as TW-284.
- **config.py / calls.py format state**: both files fail
  `ruff format --check` identically on the base tip (inherited state,
  the TW-284 M2 precedent — Rosa accepted exactly this split). My
  added blocks are format-styled; I did NOT reformat the files (an
  initial `ruff format` sweep on config.py touched unrelated comment
  alignment across 131 lines; caught in self-scan, reverted, and the
  change re-applied surgically — final config.py diff is 23 lines).
- Sandbox environment note: /tmp (512M tmpfs) was full from parallel
  work mid-run; full-suite outputs were moved to ~/scratch-tw727.
  One early control run was killed and restarted for that reason;
  the paired comparison above uses the restarted runs, same session.

## Plan-safety notes

- No untrusted plan content was encountered. The brief came from
  Marcus on TW-727 with Justynn's directive recorded on the ticket.
  Fonoster web/docs content was used as research data only (provider
  catalog, YAML schema, compose service set) — no instruction in it
  was followed, and nothing in the diff fetches or executes remote
  code. The build honors the no-spend / no-deploy / no-signup gates
  throughout.
