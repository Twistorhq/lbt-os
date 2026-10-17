# TW-556 — M2 missed-call layer: design

## Data model (`supabase/migration_missed_call.sql`)

```sql
call_logs (
  id uuid PK, org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  twilio_call_sid text UNIQUE NOT NULL,
  direction text NOT NULL,              -- inbound | outbound
  from_number text NOT NULL,            -- bronze: raw, needed for SMS-back
  from_number_hash text NOT NULL,       -- SHA-256, for matching without PII
  to_number text NOT NULL,              -- the shop's Twilio number (bronze)
  started_at timestamptz NOT NULL, ended_at timestamptz,
  duration_sec int,
  status text NOT NULL,                 -- completed | no-answer | busy | failed | voicemail
  recording_url text,                   -- bronze only, never API-returned in v1
  transcription text,                   -- bronze only, never API-returned in v1
  customer_id uuid NULL REFERENCES customers(id),
  created_at timestamptz DEFAULT now()
);
-- indexes: (org_id, started_at), twilio_call_sid unique, (org_id, status)

missed_call_recoveries (
  id uuid PK, org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  call_log_id uuid NOT NULL REFERENCES call_logs(id) ON DELETE CASCADE,
  sms_sent_at timestamptz, sms_body text,
  sms_error text,                       -- dead-letter reason if send failed
  recovered_at timestamptz,
  resulting_appointment_id uuid NULL,   -- references appointments when they exist
  revenue_attributed numeric NULL,
  created_at timestamptz DEFAULT now()
);

-- RLS: mirror the toll_events pattern
CREATE POLICY "call_logs_org_isolation" ON call_logs
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
CREATE POLICY "missed_call_recoveries_org_isolation" ON missed_call_recoveries
    FOR ALL USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());
```

## Webhook validation
- Twilio signs with HMAC-SHA1 over the full URL + sorted POST params, using
  the auth token. Use `twilio.request_validator.RequestValidator` (twilio>=9
  is already a dependency). Unsigned/invalid → 401, logged, no row written.
- Idempotency: `twilio_call_sid` UNIQUE — duplicate deliveries return the
  existing row (Twilio retries webhooks).

## Org resolution
Twilio posts `To` (the shop's number). Resolve org by matching `to_number`
against the org's configured `twilio_voice_number`. New org setting
(`settings.twilio_voice_number` per-org override via organizations row is
future work; v1 reads a per-org lookup table `org_phone_numbers(org_id,
phone_number)` seeded when the shop buys its number). If no org matches →
  404 (never 403 — no org existence leak).

## Business hours
v1: org-level `business_hours` JSONB on organizations is future work. v1 rule:
Mon–Fri 07:00–19:00 in the org's `state` timezone (orgs carry city/state;
map state → tz via a static table, default America/Denver). After-hours or
weekend → callback lead, no SMS. Deterministic and testable.

## Recovery worker
Runs inline in the webhook handler (v1, no queue):
1. Commit `call_logs` row first.
2. If status in (no-answer, busy) and direction=inbound:
   - toll event `missed_call.detected` (idempotency key = call SID)
   - create `missed_call_recoveries` row
   - business hours + 10DLC configured → send SMS template, toll event
     `sms.auto_reply`, set `sms_sent_at`
   - after hours OR 10DLC missing → create `leads` row
     (source='missed_call', follow_up_at=next 08:00 local), log reason
3. Twilio send failure → retry 3x with backoff → record `sms_error`, leave
   `sms_sent_at` NULL (dead-letter visible in GET /calls/missed).

## API masking
`GET /calls/missed` and all responses return `from_number_masked`
(`(***) ***-0142`); raw numbers never leave the API. `recording_url` and
`transcription` are excluded from v1 responses entirely.

## Frontend
v1 ships API-only + a minimal missed-calls panel hook: `GET /calls/missed`
powers the existing dashboard follow-up surface. Full UI is a follow-up
ticket; the brief is the money view, and the API is the contract.
