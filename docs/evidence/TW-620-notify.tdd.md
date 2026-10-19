# TDD Evidence — TW-620 Twistor Notify V1

## 1. Source

- Ticket: TW-620 (Justynn 2026-10-19, moved to front of platform queue).
- Design: `docs/TW-620-notify-design.md` (this branch).
- Wedge: TW-630 complaint dossier `rea/battle-plans/complaints-novu.md`.

## 2. Task report

**RED** — `pytest backend/tests/test_notify_providers.py
test_notify_routing.py test_notify_service.py`:
3 collection errors (`ModuleNotFoundError: app.notify`) — no implementation
existed. Committed as `TW-620: RED`.

**GREEN** — after implementation:
`26 passed in 0.56s` (providers 6, routing 9, service 8, analytics 4).
What the passing tests guarantee:

- Every adapter satisfies the Provider contract (name/channel/cost/send).
- NullProvider sends without network; PushProviderStub raises
  NotImplementedError (no silent no-ops); Resend/Twilio adapters refuse
  loudly without credentials.
- Cheapest-enabled-first ordering; disabled excluded; channel filtered;
  priority breaks cost ties; NoProviderAvailable when nothing can serve.
- Failover walks primary→backup; full attempt trail recorded; all-down
  returns failure with the trail; first success stops the walk.
- Cost preview returns exact per-send cost before anything goes out.
- `{{var}}` rendering; missing variables raise TemplateError.
- Send writes message + attempts to the ledger, emits the toll with
  idempotency key `notify:{message_id}`, stores recipient + hash.
- Toll-ledger failure does NOT fail the send (TW-208).
- Delivery-rate math per channel; debugger returns message + attempt trail
  with the raw recipient excluded from the payload.

**Refactor** — ruff autofix + format; two dead `orig =` assignments removed
by hand. Final: `ruff check` clean, `ruff format --check` clean (12 files).

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | No implementation existed before tests | `pytest tests/test_notify_*.py` (RED) | RED | 3 collection errors | commit `TW-620: RED` |
| 2 | Provider contract holds for all 4 adapters | `tests/test_notify_providers.py` | unit | 6 passed | `26 passed in 0.56s` |
| 3 | Least-cost routing + failover walk | `tests/test_notify_routing.py` | unit | 9 passed | same run |
| 4 | Send orchestration, preview, ledger, toll | `tests/test_notify_service.py` | unit | 8 passed | same run |
| 5 | Analytics math + debugger PII exclusion | `tests/test_notify_analytics.py` | unit | 4 passed | same run |
| 6 | Lint/format clean | `ruff check`, `ruff format --check` | CI | clean | `All checks passed!` |
| 7 | Zero regressions vs main | full suite minus 11 pre-broken modules | integration | identical 20 pre-existing failures on main and branch; branch adds 26 green | `diff` of FAILED lists: empty |

## 4. Coverage and known gaps

- Coverage: 26 new tests across providers/routing/service/analytics.
  No coverage tool run (repo has no coverage gate configured).
- **Gaps (intentional, V1):** push channel is a stub adapter (no provider
  wired); async/queued sends are synchronous in V1 (Dre follow-up: queue
  workers); webhook delivery-status callbacks not implemented (provider
  webhooks are a V2 surface); `ntf_providers` rows must be seeded per org
  (falls back to null pipe when absent — by design, never hard-fails).
- The 11 ignored test modules fail identically on main (missing `argon2`
  and other env modules) — pre-existing, unrelated, not touched.

## 5. Plan-safety notes

- No untrusted plan content encountered. The brief, the TW-617 pitch, and
  the TW-630 dossier were parsed as data; all code is fresh clean-room
  (no Novu source read or copied — mechanism only, per the brief).
- Self-scan: no secrets/credentials in the diff (adapters read env at
  runtime); no PII in logs (sha256 prefix hashes only); no network calls
  outside the vendor SDKs the brief names; migration is idempotent
  (`IF NOT EXISTS` throughout); recipient storage justified in the design
  doc with the TW-619 precedent.
