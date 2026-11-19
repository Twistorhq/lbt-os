# TW-950 Evidence: Section 11 Agent Toll Vocabulary (TW-948-B3)

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-950 (TW-948-B3), reporter Justynn, from the TW-948 Tori persistence build map (`twistor-lab/agent-ops/tw948-tori-persistence-build-map.md`, Section 4 entry B3 and Section 6 metering map) and the Tori product spec (`twistor-lab/sales/tori-product-models.md`, Sections 8.4 and 11). Branch `zeke/tw-950-b3` in its own worktree, from lbt-os main at `45a845c`. No merge, no deploy, no migration.

Scope delivered: the five missing fixed Section 11 agent events added to `backend/app/config.py` TOLL_RATES at their existing decided rates; the `booking.recovered` percent path confirmed and given band validation at emission in `backend/app/services/toll_service.py`; a small emission helper module at `backend/app/services/agent_tolls.py`. Out of scope and not done: wiring emission points into agent actions (later tickets own that), any dynamic rate store, and any new event name or rate.

## 2. Task report

### Behavior 1: five fixed events toll at exactly their Section 11 rates

Validation command: `python3 -m pytest tests/test_agent_tolls_tw950.py -q` from `backend/`.

RED (before the change, commit for the failing tests only): collection failed with `ImportError: cannot import name 'agent_tolls' from 'app.services'`, and the five events were absent from TOLL_RATES.

GREEN (after the change): `28 passed in 0.53s`. The parametrized helper tests assert the emitted row carries the exact event name, the exact unit rate in cents, the exact amount in cents, and the idempotency key `"<event>:<root id>"` for `agent.email_action` (5), `agent.task_completed` (25), `agent.booking_confirmed` (50), `agent.invoice_chase_step` (5), and `agent.estimate_chase_step` (5). A vocabulary test asserts the set of `agent.` keys in TOLL_RATES is exactly `agent.hire` plus these five.

### Behavior 2: duplicate emission returns the existing row and the total does not move

The tests use an in-memory fake of the supabase client whose insert enforces the UNIQUE idempotency key the same way the production table does (migration `supabase/migration_toll_ledger.sql`). Emitting `agent.email_action` twice under root `task-akeem-2` leaves one row, the second call returns the existing row, the ledger total stays at 5 cents, and `get_toll_summary` reports count 1 and amount 5. A companion test proves two distinct roots toll separately (two rows, 10 cents), so dedupe is keyed, not global.

### Behavior 3: booking.recovered percent path with band validation at emission

`booking.recovered` stays out of TOLL_RATES, following the standing config contract that percent-of-value events are emitter-computed. The band lives in one place: `BOOKING_RECOVERED_MIN_PERCENT` (2.0), `BOOKING_RECOVERED_MAX_PERCENT` (4.0), and `BOOKING_RECOVERED_ANCHOR_PERCENT` (3.0) in `backend/app/config.py`. `record_toll_event` now takes an optional `base_value_cents`; for `booking.recovered` it requires both `amount_cents` and `base_value_cents` and calls `validate_booking_recovered_band`, which raises ValueError for a non-positive base, a non-positive amount, or an amount outside the band (a half-cent tolerance on each edge absorbs rounding to the nearest cent). The helper `emit_booking_recovered` computes the amount from the recovered value at the anchor 3 percent by default, and the ledger re-validates it, so a caller bypassing the helper still cannot toll out of band. Tests cover 2.0, 2.5, 3.0, 3.5, and 4.0 percent accepted, and 0, 1.0, 1.99, 4.01, 5.0, and 10.0 percent rejected, plus direct emission out of band rejected and direct emission without a base value refused. The stale config comment that described booking.recovered at 10 percent of job value is corrected to the Section 11 band.

### Behavior 4: unknown events still fail loud, and the unnamed gaps stay unnamed

A test asserts an invented event (`agent.something_invented`) with no amount raises `ValueError: Unknown toll event type` and writes nothing. A second test asserts no TOLL_RATES key names a background or review toll, and the helper module exposes no per-call helper and no background-review helper. The two gaps named in spec Section 8.4 (per-call toll, standalone background-review toll) remain founder decisions, unpriced and unnamed in code.

### Full suite and lint

Command (CI shape, from `backend/`): `python3 -m pytest -q` with nine test files ignored, listed in Section 4 below because they fail collection identically on unmodified main in this sandbox.

Branch result: 2721 passed, 21 failed, 76 skipped. Main baseline, same command and same ignores: 2693 passed, 21 failed, 76 skipped. The 21 failing test ids are byte-identical between main and this branch (verified by diffing the sorted FAILED lists), so every failure is pre-existing and none is caused by this change. The passed delta is exactly the 28 new tests.

Lint: `python3 -m ruff check app tests` from `backend/` reports "All checks passed!", which is the CI lint gate. `python3 -m ruff check` and `python3 -m ruff format --check` both pass on the two files authored by this ticket (`backend/app/services/agent_tolls.py`, `backend/tests/test_agent_tolls_tw950.py`). For the two edited files, my hunks were range-formatted with `ruff format --range` so every line I added or changed is format-clean; both files still fail a whole-file `ruff format --check` on pre-existing regions only (config.py regions above the toll section, and one blank line after the toll_service module docstring), a state verified identical on main before my change. Reformatting those whole files would have buried this change in unrelated churn, so it was deliberately not done.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Each of the five fixed events is in TOLL_RATES at exactly its Section 11 rate | tests/test_agent_tolls_tw950.py::test_five_fixed_events_present_at_exact_section_11_rates | RED | PASS after fix | 28 passed in the module run |
| 2 | Each emission helper tolls at exactly its rate with key `"<event>:<root id>"` | test_each_helper_tolls_at_exactly_its_rate (5 parameters) | RED | PASS after fix | Same module run |
| 3 | The agent vocabulary is exactly agent.hire plus the five events, nothing invented | test_agent_event_vocabulary_is_exactly_the_decided_set | unit | PASS | Same module run |
| 4 | Duplicate emission with the same key returns the existing row and the total does not move | test_duplicate_emission_returns_existing_row_and_total_does_not_move | RED | PASS after fix | Same module run, plus get_toll_summary assertion |
| 5 | booking.recovered at the 3 percent anchor tolls 300 cents on a 10000 cent recovery | test_booking_recovered_anchor_three_percent | RED | PASS after fix | Same module run |
| 6 | booking.recovered inside the band (2.0 to 4.0 percent) is accepted | test_booking_recovered_inside_band_accepted (5 parameters) | unit | PASS | Same module run |
| 7 | booking.recovered outside the band is rejected at the helper and at direct ledger emission | test_booking_recovered_outside_band_rejected (6 parameters), test_booking_recovered_direct_emission_out_of_band_rejected | RED | PASS after fix | Same module run |
| 8 | booking.recovered without the recovered value fails loud instead of tolling unverified | test_booking_recovered_without_base_value_fails_loud | unit | PASS | Same module run |
| 9 | An unknown event still fails loud and writes nothing | test_unknown_event_still_fails_loud | unit | PASS | Same module run |
| 10 | Per-call and standalone background-review tolls stay unnamed and unpriced | test_unnamed_gaps_stay_unnamed_and_unpriced | unit | PASS | Same module run |
| 11 | No regressions in the backend suite | Full suite run, branch vs main, sorted FAILED lists diffed | CI | PASS (identical failure sets) | 2721 passed on branch vs 2693 on main, 21 pre-existing failures both sides |
| 12 | Ruff clean on authored files and on the CI lint gate | `ruff check app tests`; `ruff format --check` on the two authored files | CI | PASS | "All checks passed!" and "2 files already formatted" |

## 4. Coverage and known gaps

New tests: 28, all in `backend/tests/test_agent_tolls_tw950.py`. They run against an in-memory fake of the supabase client, following the house pattern in `test_billing_flat.py`; no test touches a live database, consistent with the rest of the suite.

Pre-existing suite state, stated plainly: nine test files fail collection in this sandbox on unmodified main and on this branch alike (eight with `xmlsec.Error: lxml & xmlsec libxml2 library version mismatch`, and `test_speedstd.py` with `ModuleNotFoundError: No module named 'duckdb'`). They were ignored for the comparison runs above. A further 21 tests fail at runtime on both main and this branch with identical ids; they are pre-existing and unrelated to tolling (examples: `test_pilot_blockers.py` endpoint wiring, `test_telemetry_service.py` sampled rows). Nothing in this ticket fixes or worsens them.

Intentional gaps in this ticket, by scope: no emission point is wired into any agent action (job fires, delivery, delegation wire these helpers in their own tickets); no dynamic rate assignment store; no per-call toll and no standalone background-review toll, both preserved as founder decisions per spec Sections 8.4 and 11.

One design note for Rosa: before this change, any event name paired with an explicit `amount_cents` tolled through the generic percent path, and several science lanes rely on that (quantum SKUs, download and API row events, `agent.task_step`). That behavior is unchanged. `booking.recovered` is now the one named percent event with a stricter contract, checked before the generic paths.

Layering note from the build map Section 6, recorded so a future bill review does not misread it: when a Tori task internally runs a Flow step or a Notify send, the platform tolls (`flows.step_executed`, `notify.message_sent`) and the agent toll are different events for different layers, and both may legitimately fire for one customer-visible action. That is layering, not double-tolling. Double-tolling means the same event charged twice for the same action, which the idempotency roots in this ticket prevent.

## 5. Reconciliation: the Core inbox prototype and the production event

One name, one rate, two ledgers at two stages of maturity. The Twistor Core inbox prototype (`twistor-core/src/twistor_core/inbox/toll.py`, TW-923) defines the event as structural constants a caller cannot override: `EMAIL_ACTION_EVENT = "agent.email_action"` and `EMAIL_ACTION_TOLL_USD = 0.05`, and its agent loop emits exactly one such toll per successful draft into an in-memory prototype ledger. The production event added here is the same name at the same value expressed in the production unit: `TOLL_RATES["agent.email_action"] = 5` cents on the M1 ledger, emitted through `record_toll_event` with an idempotency key.

The one semantic difference is what the prototype counts, and it is a prototype artifact, not a second definition. The Core loop is draft-only: it never sends mail, so its draft is the closest auditable email action it has, and it tolls there. Spec Section 8.4 meters emails as email actions, and the build map Section 6 records that send-path emission wires with the inbox loop's production wiring. When that wiring lands, the action that tolls is the production email action, once, on the production ledger. There is no double charge across the two: the prototype ledger is in-memory, belongs to the prototype, and never writes to M1. Same event, same five cents, one production home.

## 6. Plan-safety notes

No untrusted plan content was found in the ticket, the build map, or the spec sections used. The brief, the map, and the spec agreed on event names, rates, and the two unnamed gaps, and nothing read during this work asked for a step outside the ticket scope.
