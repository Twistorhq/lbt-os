# TW-556 — M2 missed-call layer (Jobber wedge): proposal

## What
Twilio voice webhooks → `call_logs` → missed-call detection → instant auto-SMS
reply during business hours, morning callback lead after hours. Deterministic
IVR-lite (TwiML), no LLM. The money feature: every missed call is a row; every
row is recovered revenue or a measured leak.

## Why
Jobber sells AI Receptionist as a $29/mo add-on. We make instant response core
to Twistor Trades. Found-money pricing: $0.10 per detection, $0.05 per
auto-SMS, 10% of attributed recovered revenue. Entry shops feel it in week one.

## Scope
- `POST /calls/voice-webhook` (Twilio-signed, unsigned → 401, no exceptions)
- `POST /calls/sms-webhook` (inbound SMS: booking confirmations, "call me back")
- `GET /calls/missed?days=7` (money view: unrecovered missed calls + context)
- `POST /calls/{id}/recover` (manual recovery: called back / booked)
- Recovery worker: business-hours no-answer → instant SMS template; after-hours
  → lead row (`source='missed_call'`, `follow_up_at` = next 8am local) on the
  dispatch board's follow-up surface
- Every metered event emits to the M1 `toll_events` ledger (idempotent)
- Migration `supabase/migration_missed_call.sql` with RLS org isolation

## Out of scope
- Twilio voice number purchase (Justynn/manual; code ships webhook-ready)
- LLM receptionist (later ticket)
- Storm triggers (M4), white-label BI (M3)

## Compliance (Rosa TW-547 majors — answered here, not deferred)

### PII doctrine (Major 1 → TW-552)
Justynn's standing rule: medallion architecture. PII is hashed at ingestion and
stored ONLY in the bronze layer; never customer-facing in gold.
- `call_logs` IS the bronze layer for telephony. Raw `from_number`/`to_number`
  live here because the recovery flow must SMS the caller back — there is no
  other copy. `from_number_hash` (SHA-256) is stored alongside for
  matching/analytics without touching PII.
- Silver/gold: API responses and dashboard queries return masked numbers only
  (last 4 digits, e.g. `(***) ***-0142`). No phone numbers in application logs.
- `recording_url` / `transcription`: stored in bronze only; never returned by
  the API in v1.
- Masked in all layers: emails, addresses, phone numbers, SSNs, EINs,
  SAM.gov/UEI identifiers, all business identifiers.

### TCPA / A2P consent model (Major 2)
- The recipients are the SHOP'S OWN customers: people who called the shop's
  number. An inbound call establishes an existing business relationship.
- Auto-SMS replies go to the caller within minutes of THEIR inbound call —
  conversational, transactional, one-to-one (not marketing blasts).
- The shop's Twilio number must be registered via A2P 10DLC before the
  recovery worker sends its first SMS. If 10DLC is not configured, the worker
  degrades to lead-only (no SMS) and logs the reason — never sends unregistered.
- Every auto-SMS includes opt-out ("Reply STOP to opt out").
- After-hours flow sends NO SMS — it creates a next-morning callback lead.

## Self-healing (TW-208)
Twilio send failure → retry with backoff (3 attempts) → dead-letter:
`missed_call_recoveries.sms_sent_at` stays NULL and `sms_error` is recorded;
the missed call remains visible in GET /calls/missed for manual recovery. A
failed SMS never 500s the webhook — the call row is committed first, recovery
is a separate step.
