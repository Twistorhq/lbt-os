# TW-767 Evidence — Agent Identity and Persona Registry (TW-764 Spec 1)

Prepared by Twistor Holdings LLC

## What was built

`backend/app/agent_identity/` — the per-org agent identity
record for the Twistor Trades shop agent (the TW-766
foundation: one named persona, ONE email, ONE phone number
per shop):

- `supabase/migration_agent_identity_v1.sql` — `agt_identities`
  (one row per org: UNIQUE `org_id`, UNIQUE `email_address`;
  `agent_name`, `phone_number`, `persona` JSONB, `coverage`
  JSONB, `status` default `unprovisioned`, `version`),
  `agt_inbound_messages` (bronze intake: raw from/subject/body
  never API-returned, `from_address_hash` for matching, UNIQUE
  (`provider_message_id`, `org_id`)), `agt_audit` (append-only).
  RLS deny-all on all three (the flg_/flw_/ntf_ convention);
  the backend filters `org_id` on every query.
- `models.py` — fail-loud validation for the identity record,
  persona, and coverage shapes.
- `service.py` — `provision` (idempotent; email allocated
  deterministically `<org-slug>-<org-id-prefix>@agents.twistor.co`
  from the new settings `agent_email_domain` /
  `agent_inbound_webhook_secret`), `identity_view` (fail-closed
  default view while unprovisioned), `channel_config` (ONE
  assembly path: the persona block is byte-identical across
  phone/email/sms, which is what Spec 3 assembles prompts from
  and Spec 6 renders the Agent Card from),
  `update_persona`/`update_coverage` (validate-then-write,
  version bump, audit), `bind_phone_number` (binds ONLY a
  number the org already owns on the TW-284
  `org_phone_numbers` path; another org's number and unowned
  numbers are refused), `set_status` (suspended stops address
  resolution), `resolve_email_recipient`/`resolve_phone`
  (exact match, fail closed), `enqueue_inbound_email` (queues
  in the ADDRESSED org only, idempotent per org),
  `list_inbound` (metadata only; bronze fields projected out
  in code), `verify_inbound_signature` (Svix HMAC, stdlib,
  fail closed).
- `router.py` — JWT registry surface (org taken from claims
  only, 403 without) plus `POST /inbound/email`: 503 when the
  webhook secret is unconfigured, 401 on bad signature, and an
  unknown recipient queues nothing yet still returns 200 (no
  address enumeration).

## Acceptance proof (77 new tests, all passing)

Commands run on branch `dre/tw-767-agent-identity` @ 4657de8:

- `backend/tests/test_agent_identity_models.py` — 24 passed.
- `backend/tests/test_agent_identity_service.py` — 31 passed.
- `backend/tests/test_agent_identity_router.py` — 13 passed.
- `backend/tests/test_agent_identity_migration.py` — 9 passed
  (migration contract: table/column/constraint shape pinned
  against the SQL file).

## Guarantee-to-test map

| Guarantee | Where proven |
| --- | --- |
| One identity per org; one address per agent (UNIQUE `org_id`, UNIQUE `email_address` in DDL) | migration contract tests; service provisioning tests |
| Two orgs get distinct addresses and numbers | service tests (two-org provisioning) |
| Mail queues in the ADDRESSED org only — the address is the authority, content is ignored (org B content inside org A's mail still lands in org A) | service tests (`enqueue_inbound_email`) |
| Per-(message, org) idempotency: a redelivery returns the original row; the same provider message addressed to two orgs queues once per org | service tests; migration UNIQUE (`provider_message_id`, `org_id`) |
| Suspended identity stops address resolution | service tests (`set_status`, `resolve_email_recipient`) |
| Persona block byte-identical across phone/email/sms (ONE assembly path) | service tests AND router tests (`channel_config`) |
| Phone binding refuses another org's number and unowned numbers; binds only the org's own TW-284 number | service tests (`bind_phone_number`) |
| Bronze discipline: raw from/subject/body never leave `agt_inbound_messages`; `list_inbound` returns metadata and `from_address_hash` only; audit summaries carry a hash prefix, never an address | service tests; router tests |
| Webhook fails closed: 503 unconfigured secret, 401 bad signature, unknown/suspended recipient queues nothing and returns 200 (no enumeration) | router tests; service tests (`verify_inbound_signature`) |
| Org isolation: router takes org from JWT claims only (403 without); every service query filters `org_id` | router tests; service tests |

## Regression evidence

Full backend suite on branch `dre/tw-767-agent-identity`: the
failed/error node set is BYTE-IDENTICAL to a clean-main
control worktree run in the same environment (28 pre-existing
nodes on both sides, including 8 xmlsec collection errors from
the sandbox's lxml/xmlsec mismatch — clean main fails the app
import the same way here). Zero regressions; the 77 new tests
are additive passes.

Ruff: `ruff check` clean on all authored/edited .py files;
`ruff format --check` clean on all authored files plus
`app/main.py`. Disclosed exception: `app/config.py` fails
`ruff format --check` at clean HEAD 7c08f8d — pre-existing
drift, verified byte-for-byte against the control worktree.
The block this diff adds to `config.py` follows the file's
existing style; the file was deliberately NOT reformatted so
the diff stays reviewable.

## Honest limits, stated

- Resend's `email.received` webhook carries metadata only, so
  `body_text` queues as None. Spec 3 fetches the message body
  via the Resend API by provider message id. Documented in the
  code and in the migration header.
- Mid-build catch, on record: inbound idempotency was first
  scoped per provider message alone; building the two-org
  tests exposed that the same provider message can
  legitimately queue once per addressed org, so the scope is
  per (`provider_message_id`, `org_id`) in both the UNIQUE
  constraint and the service path, with tests asserting both
  halves (redelivery returns the original row; cross-org
  delivery queues separately).

## Go-live notes (not this ticket)

DNS/MX for `agents.twistor.co`, Resend inbound setup, and
setting `AGENT_INBOUND_WEBHOOK_SECRET` are go-live steps.
Phone binding completes against live `org_phone_numbers` when
the Fonoster wiring lands. Spec 2's dependency on the
identity record is satisfiable from this branch.
