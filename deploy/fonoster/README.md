# Fonoster self-host package — Twistor Trades AI answering (TW-727)

Prepared by Twistor Holdings LLC

Fonoster (fonoster/fonoster, MIT) is the default AI-answering provider
for Twistor Trades as of TW-727, replacing the paid Twilio + OpenAI
voice path (TW-284 Path A, kept in code as a deprecated rollback).

**This package is configuration only.** Nothing here is deployed, no
carrier account exists, and no number has been purchased. Zero spend.

## Contents

- `docker-compose.yml` — the Fonoster service set (API server,
  Autopilot, Routr SIP, RTPengine, Asterisk, dashboard, Postgres,
  NATS, InfluxDB) plus a local Ollama service for the think step.
  Service set and images follow Fonoster's upstream compose.
- `autopilot/trades-answering.agent.yaml` — the Twistor Trades
  answering agent: AI disclosure as the first message, per-call
  recording toggle with the spoken announcement, emergency booking and
  escalation tools, Ollama as the think provider.
- `.env.example` — every value the stack and the backend need, with
  the real secrets left blank by design.

## Go-live sequence (each step is Justynn's word, in order)

1. Pick a SIP carrier and buy the shop number (manual, Justynn).
2. Deploy this compose on the Docker host; create the workspace
   access-key pair; pull the Ollama model (`llama3.1`).
3. Configure the hear/speak provider key (Deepgram by default) as a
   Fonoster workspace integration.
4. Create the agent: `fonoster applications:create -f autopilot/trades-answering.agent.yaml`;
   point the number at the application; set `FONOSTER_AUTOPILOT_APP_REF`.
5. Wire the SDK bridge (the backend's Fonoster client seam — Fonoster's
   API is gRPC with a Node-only official SDK) and set the backend's
   `FONOSTER_*` settings; flip `phone_automation_enabled` per shop.

Until step 5 lands, the backend fails closed: the factories raise
`ProviderNotConfigured` without credentials, and the bridge client
raises it without the bridge. Nothing half-answers a caller.

## Cost shape (no invented pricing)

- Gone: Twilio platform fees for the answering path and OpenAI
  Realtime voice fees. Not part of this path anymore.
- Stays: the SIP carrier. Numbers and per-minute carrier charges are
  whatever the chosen carrier bills — that choice is Justynn's step 1.
- Hear/Speak run on cloud provider keys today (Deepgram by default):
  Fonoster's catalog has no local STT/TTS product yet. Think (the LLM)
  runs fully local on Ollama.
- The Fonoster platform itself is MIT and self-hosted: no license fee,
  no per-seat or per-minute platform charge from Fonoster.

Full research and test evidence:
`docs/evidence/TW-727-fonoster-provider.tdd.md`.
