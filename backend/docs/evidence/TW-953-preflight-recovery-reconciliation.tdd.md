# TW-953 Evidence: Preflight, Durable Fire Identity, Interruption Recovery, Exactly-Once Reconciliation (TW-948-B5)

Prepared by Twistor Holdings LLC

Author: Zeke Okafor, Lead Build Engineer
Date: 2026-11-05
Ticket: TW-953 (TW-948-B5, from the TW-948 build map Sections 2.4 and 4,
plus the inherited TW-952 review scope recorded in the ticket comments)
Spec source: tori-product-models.md Sections 8.1 and 8.5

## 1. Source

Ticket TW-953 and its two coordinator notes (2026-11-05), which carry
the inherited scope from the TW-952 (B4) Rosa gate and Rex reviews:
(1) stuck-running fire reaper (Rex F3 tail, confirmed by Rex retest
probe P4), (2) toll reconciliation for executed-but-untolled steps
(Rex F6), (3) durable tick heartbeat (Rex F7 tail), (4) router wiring
(Rex F5), (5) catch-up collapsed-slot accounting (Rex F8), and
(6) tick-summary double-listing (Rex P14 / Rosa nit). The task brief's
item 1 (scheduler preflight quarantining poisoned definitions) and
item 7 are the same items (6) and the preflight half of the ticket
body. Base branch: zeke/tw-952-b4 at tip 1b6f503 (B4, gate-complete,
NOT merged; merge is Justynn's separate word). This branch stacks on
B4 and rebases onto main when B1/B4 merge. No merge, no deploy, and
no migration application happened in this ticket.

## 2. What landed

- `backend/app/agent_jobs/preflight.py`: two-layer preflight.
  validate_definition is pure: bad cron, bad timezone, corrupt
  next_fire_at, empty prompt, malformed skill_keys, and an
  out-of-bounds catch-up window are reported as failures, never
  thrown. PreflightChecker runs the external seams fail-closed:
  credentials resolve through the injected just-in-time resolver,
  attached skills report ready + licensed + active through the
  injected registry seam, a delivery target is configured for the
  org (default check reads Notify's ntf_providers bindings; the
  null-pipe fallback does NOT count as configured), and the
  permission ledger grants every action class on the definition
  (default check is the TW-768 check_permission; amount_required
  passes because amounts are fenced at execution time). The phone
  pack is a reserved hook for B14: with call.outbound reachable
  and no hook wired, preflight records the reservation as a note
  and TW-884 dial-time checks stay the wall, unchanged.
  preflight_scan quarantines poisoned active jobs whether or not
  they are due. Alerters: a Notify-backed factory
  (make_notify_alerter, the delivery-ledger message id is the
  receipt) and a loud log fallback.
- `service.block_job` (in agent_jobs/service.py): the preflight
  failure landing. One blocked record (status blocked, masked
  reason, blocked_at), exactly one owner alert whose receipt is
  stored on the row, idempotent on re-tick (an already-blocked
  job alerts nobody), and the job leaves the ticker's active
  scan so it cannot fire or spend until an owner resumes it
  (resume clears the block markers). JobSpec/JobEdit gained the
  preflight surface: credential_refs, action_classes (validated
  against the TW-768 vocabulary at construction), delivery_channel.
- `backend/app/agent_jobs/reconciliation.py`: the durable step
  journal (agt_job_fire_steps) and reconciliation. run_steps now
  journals 'started' before a handler and 'executed' after it
  returns, BEFORE the toll posts; a toll failure journals
  'toll_failed' and still propagates (B4 fail-fast semantics
  preserved). fire_job wires the DB journal by default.
  reconcile_fire classifies each planned step done /
  executed_untolled / not_done / unknown from journal + toll
  evidence; with apply=True it posts a missing toll with the
  SAME fire-key-rooted idempotency key the original run used, so
  the ledger dedupes and the step handler never re-runs. Unknown
  (journal started, never executed, non-idempotent step) is
  surfaced by name and never auto-retried or auto-tolled.
- `backend/app/agent_jobs/recovery.py`: reap_stranded_fires
  parks a fire stranded in 'running' past the staleness
  threshold as 'interrupted' (conditional transition out of
  running only) and pauses its job for review (new job status
  'review'; the ticker scans 'active' only, so it never
  auto-resumes), with exactly one alert keyed to the durable
  transition. resume_after_review is the operator gate: it
  reconciles every interrupted fire (apply=True) and refuses
  the resume, naming the steps, while any step is unknown.
- Durable delivery handoff (service.fire_job, delivery seam):
  when a delivery seam is wired, the fire completes only when
  the seam returns a receipt; the receipt is stored on the fire
  row (delivery_receipt, delivery_status handed_off). No receipt
  or a delivery exception fails the fire; executed steps stay
  journaled and tolled for reconciliation to settle.
- `backend/app/agent_jobs/heartbeat.py`: every tick (and every
  scan failure, before the JobTickerError raises) upserts the
  singleton agt_job_tick_heartbeats row: last_tick_at, status,
  fired/failed/blocked counts, masked error. get_tick_health
  reports staleness; check_scheduler_health alerts exactly once
  per stale episode (marker keyed to last_tick_at on the row).
- Ticker (agent_jobs/ticker.py): preflight runs per due job
  before any fire (definition always; checker when wired);
  corrupt next_fire_at quarantines instead of failing; the
  reaper runs first when reap_stale_after is set; catch-up
  accounting counts slots (collapsed slots into
  missed_collapsed on the fire path, every skipped slot into
  missed_skipped on the skip path, bounded iteration); a failed
  post-fire advance lands only in advance_failed, never also in
  failed/errors (Rex P14); the heartbeat is recorded on every
  tick outcome.
- Router (agent_jobs/router.py, Rex F5): one shared RunControl
  on app.state used by tick, trigger, and cancel (an HTTP cancel
  now fences an HTTP-started run in-process); tick and trigger
  pass the production toll sink (services.toll_service
  .record_toll_event); the runtime step carries
  toll_event agent.task_completed; tick also wires preflight,
  the alerter, and the reaper (30-minute staleness). New
  endpoints: POST /jobs/{id}/fires/{fire_id}/reconcile,
  POST /jobs/{id}/resume-after-review, GET /tick-health
  (tick-key guarded, same as the tick).
- `supabase/migration_agent_jobs_tw953.sql`: additive columns
  (job preflight surface + block/review markers, fire delivery
  receipt + reconciled_at), widened status CHECKs (jobs gain
  review, fires gain interrupted), and the two new tables
  (agt_job_fire_steps, agt_job_tick_heartbeats) with deny-all
  RLS. Files only, never applied, gated on Justynn's word.
- `backend/app/persistence/models.py`: JOB_STATUSES gains
  review, FIRE_STATUSES gains interrupted (the code vocabulary
  the migration widens to match).

## 3. Task report (RED first, per house TDD)

Command: `python3 -m pytest tests/test_agent_jobs_tw953.py -q`

- RED (commit fb4309d, tests only): collection error,
  ImportError: cannot import name 'heartbeat' from
  'app.agent_jobs' (the B5 modules did not exist).
- First GREEN run: 42 passed, 1 failed. The failure was
  test-side, fixed in the test, not the code: the router tick
  test first used a pytest API this environment lacks
  (monkeypatch.dict), then a fixed 16:00 slot that the router's
  wall-clock tick correctly treated as outside the catch-up
  window; the test now builds its job relative to wall-clock
  now with an every-minute schedule.
- Final: 43 passed in the B5 suite.

Neighboring suites, same worktree: B5 + B4 + persistence service
and migration suites, 145 passed. B5 + B4 + persistence +
agent_runtime + durable_turns + agentperms (service, models,
multiagent, fencing) + notify service, 821 passed. Flows and
flags suites (11 files enumerated explicitly, as a bare -k run
aborts on the xmlsec collection errors): 154 passed. Full
backend suite with --continue-on-collection-errors: 2805
passed, 21 failed, 9 collection errors. The 21 failures are the
pre-existing baseline set, identical in identity to the B4 tip
(8 bi_whitelabel, 11 missed_call, 1 pilot_blockers,
1 telemetry_service); none are in files this diff touches, and
the 9 collection errors are the environment xmlsec mismatch.
2805 = 2762 (B4 tip full-suite count) + 43 (the B5 suite).

Ruff: `python3 -m ruff check` clean on app/agent_jobs/,
app/persistence/models.py, and tests/test_agent_jobs_tw953.py;
`ruff format --check` clean on the same set. app/config.py was
not touched by this ticket.

## 4. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Definition validation catches bad cron, bad timezone, corrupt next_fire_at, and empty prompt as reported failures, never exceptions | test_agent_jobs_tw953.py::TestDefinitionValidation (4 tests) | RED | PASS | Section 3 counts |
| 2 | Preflight passes a healthy job and fails closed on: unresolvable credential, credential ref with no resolver wired, unlicensed skill, skill with no registry wired, unconfigured delivery target, ungranted ledger class | TestPreflightChecks (8 tests) | RED | PASS | Section 3 counts |
| 3 | The phone pack is a reserved B14 hook: no hook records the reservation as a note and passes; a wired hook's failures block | TestPreflightChecks::test_phone_hook_reserved_without_hook, test_phone_hook_failure_blocks | RED | PASS | Section 3 counts |
| 4 | A job definition naming an unknown action class is a construction error (fail closed at the surface) | TestPreflightChecks::test_job_spec_rejects_unknown_action_class | RED | PASS | Section 3 counts |
| 5 | A failed preflight creates no task, no fire row, and no spend; the job is blocked with one alert; a second tick sends no second alert | TestPreflightBlocking::test_failed_preflight_blocks_alerts_once_creates_no_task | RED | PASS | Section 3 counts |
| 6 | A poisoned definition (bad cron written past the surface) is quarantined blocked at preflight while healthy jobs still fire; preflight_scan blocks a poisoned job before it is due | TestPreflightBlocking::test_poisoned_cron_quarantined_at_preflight_not_mid_tick, test_preflight_scan_blocks_poisoned_job_before_it_is_due | RED | PASS | Section 3 counts |
| 7 | A manual trigger preflights identically: failure blocks the job, alerts once, creates no fire, and raises | TestPreflightBlocking::test_trigger_now_preflight_failure_blocks_and_raises | RED | PASS | Section 3 counts |
| 8 | A fire completes only on a durable delivery handoff receipt: receipt stored on the fire row; no receipt or a delivery failure fails the fire | TestDeliveryHandoff (3 tests) | RED | PASS | Section 3 counts |
| 9 | A fire stranded in running past the threshold is parked interrupted and its job paused for review, with one alert; a fresh running fire is untouched; reaping twice parks and alerts nothing twice | TestReaper::test_stale_running_fire_is_interrupted_and_job_paused_for_review, test_fresh_running_fire_is_untouched, test_reaper_is_idempotent | RED | PASS | Section 3 counts |
| 10 | An interrupted job never auto-resumes: later ticks fire nothing for it and its status stays review; the ticker can reap inline before firing | TestReaper::test_interrupted_job_never_auto_resumes_on_tick, test_tick_can_reap_before_firing | RED | PASS | Section 3 counts |
| 11 | Executed-but-untolled (toll sink threw mid-run, Rex F6) is detected from journal + toll evidence; a dry run posts nothing; applying against a still-down sink reconciles nothing and loses nothing | TestReconciliation::test_executed_but_untolled_is_detected_and_reconciled_once | RED | PASS | Section 3 counts |
| 12 | Reconciliation apply posts the missing toll exactly once with the fire-key-rooted idempotency key; a second apply classifies done and posts nothing; the step handler never re-runs | TestReconciliation::test_reconcile_apply_posts_toll_exactly_once | RED | PASS | Section 3 counts |
| 13 | Kill between send and journal (acceptance scenario): the step classifies unknown, is surfaced by name, is never auto-tolled or re-sent; the outbox holds exactly one send | TestReconciliation::test_kill_between_send_and_journal_is_unknown_never_retried | RED | PASS | Section 3 counts |
| 14 | Resume after review is refused while any step is unknown (job stays in review), and succeeds when every step classifies done (job returns active, next slot recomputed) | TestReconciliation::test_resume_after_review_refused_on_unknown, test_resume_after_review_succeeds_when_all_classified | RED | PASS | Section 3 counts |
| 15 | Every tick records a durable heartbeat (last_tick_at, outcome, counts); a scan failure records status scan_failed before raising; a dead scheduler reads stale in-system and alerts exactly once per stale episode | TestTickHeartbeat (3 tests) | RED | PASS | Section 3 counts |
| 16 | Catch-up accounting counts slots (Rex F8): a 5-day-stale daily job inside its window fires once with missed_collapsed 5; a 3-day-stale daily job outside its window counts missed_skipped 4 (its slot plus the three slots the advance skips past) | TestCatchUpAccounting (2 tests) | RED | PASS | Section 3 counts |
| 17 | Tick-summary accuracy (Rex P14): a job whose post-fire advance write fails appears in fired and advance_failed only, never in failed/errors, and its fire row exists | TestTickSummaryAccuracy::test_advance_failure_after_fire_is_not_double_listed | RED | PASS | Section 3 counts |
| 18 | Router wiring (Rex F5): one shared RunControl instance serves tick/trigger/cancel; the runtime step carries toll_event agent.task_completed; the HTTP tick tolls through the production sink with a fire-key-rooted idempotency key and leaves a heartbeat; the cancel endpoint fences through the shared control | TestRouterWiring (4 tests) | RED | PASS | Section 3 counts |
| 19 | The status vocabulary and the migration agree: interrupted fires and review jobs exist in code; the migration is additive, attributed, and marked never applied by this ticket | TestVocabularyAndMigration (2 tests) | unit | PASS | Section 3 counts |
| 20 | No B4 guarantee regressed: the full B4 suite passes unchanged on this branch, including per-job containment, fire-first recovery, DuplicateFireError translation, and the last-step cancel fence | tests/test_agent_jobs_tw952.py (41 tests) in the neighboring run | unit | PASS | Section 3 counts |

## 5. Inherited-item map (ticket body + TW-952 review scope)

- Ticket preflight (credentials, skills, delivery, ledger;
  blocked + one alert + no task/spend): preflight.py,
  service.block_job, ticker and trigger wiring. Rows 2, 5, 7.
- Ticket durable fire identity + delivery receipt: the B4 fire
  id already roots toll and effect keys; B5 stores the delivery
  receipt on the fire row and completes only on it. Row 8.
- Ticket interruption (parks interrupted + pause-for-review,
  never auto-resumes): recovery.reap_stranded_fires. Rows 9, 10.
- Ticket reconciliation before resume/retry (done / not done /
  unknown; unknown surfaced, never auto-retried):
  reconciliation.reconcile_fire + recovery.resume_after_review.
  Rows 11 to 14.
- Brief item 1 / Rex F1 tail (poisoned definitions quarantined
  at preflight): validate_definition + preflight_scan + ticker
  quarantine. Rows 1, 6.
- Rex F3 tail / P4 (stuck-running reaper): recovery.py. Row 9.
- Rex F5 (router wiring): router.py. Row 18.
- Rex F6 (executed-but-untolled reconciliation): journal +
  reconcile apply. Rows 11, 12.
- Rex F7 tail (durable heartbeat): heartbeat.py + GET
  /tick-health. Row 15.
- Rex F8 (catch-up collapse accounting): ticker slot counting.
  Row 16.
- Rex P14 / Rosa nit (tick-summary double-listing): advance
  failures list under advance_failed only. Row 17.
- Rex F9 (tick-key plain compare) was a note only in the retest
  and is unchanged, matching the house FLOWS_TICK_KEY pattern.

## 6. B3 dependency note (binding for merge order)

B3 (TW-950, branch zeke/tw-950-b3, Rosa-approved, unmerged) is
NOT on this base either (same as B4): its agent_tolls helpers
and its TOLL_RATES entries (agent.task_completed at 25 cents,
agent.email_action at 5 cents, and the rest of the Section 11
vocabulary) do not exist here. B5 wires against the toll seams
exactly as B4 did: run_steps and reconciliation post through
the record_toll_event seam contract (idempotency key required,
duplicate key returns the existing row, unknown event raises),
the router passes the real production sink, and the tests
inject a fake sink implementing that contract with the post-B3
vocabulary (the router test adds agent.task_completed to the
rate table in-test to simulate the post-B3 base; no B3 code was
copied in). Merge order consequence, recorded as a hard
condition on Justynn's merge word: B3 merges before or with
B4/B5. Until B3 lands, a production fire naming agent toll
events fails LOUD at the toll sink (unknown event), the step
journals toll_failed, and reconciliation settles it once B3 is
in; nothing is ever silently untolled. That is the safe
direction and is accepted here.

## 7. Coverage and known gaps

- Covered: definition validation, all external preflight seams
  (including both fail-closed no-wiring cases and the reserved
  phone hook), blocking idempotence and the one-alert rule,
  delivery receipt gating, the reaper (stale, fresh, idempotent,
  inline-in-tick), reconciliation classification (done,
  executed_untolled, not_done, unknown, reconciled) with
  exactly-once toll posting, the resume gate both ways, the
  heartbeat (ok, scan_failed, stale alerting once per episode),
  slot-exact catch-up accounting both paths, tick-summary
  accuracy, router wiring end to end through the endpoint
  functions, the migration shape, and the status vocabulary.
- The default ledger checker is exercised through its seam
  contract by the fake ledger checker in the suite; the
  production default (agentperms check_permission against the
  real ledger tables) is covered by the agentperms suites
  staying green (Section 3), not by a dedicated B5 test, because
  the B5 fake-db harness does not model the ledger's grant
  tables. Named here so the gap is explicit.
- The Notify-backed alerter (make_notify_alerter) is a thin
  factory over notify.service.send, whose own suite stays
  green; the B5 suite asserts the one-alert contract through
  the alerter seam, not through a live Notify send.
- Delivery: fire_job's delivery seam is fully tested; the
  router does not yet wire a Notify-backed delivery seam for
  the runtime-adapter path (the adapter's durable handoff is
  the agent_runtime task enqueue, whose task id lands on the
  fire row as in B4). Wiring a Notify receipt into that path
  is deployment wiring, named here rather than faked.
- The reaper handles fires stranded in 'running'. A fire left
  in 'pending' by a crash between record_fire and the running
  transition is covered by B4's already_fired heal path (the
  slot cannot refire and the advance heals), not by the reaper;
  stated so the boundary is explicit.
- Slot counting is capped at 50,000 iterations as a runaway
  guard; a count that hits the cap reports the cap. The largest
  legitimate count (a minutely job stale across the maximum
  7-day catch-up window) is 10,080.
- HTTP end-to-end (JWT auth, app assembly) is not exercised:
  app.main cannot be imported in this sandbox due to the
  pre-existing xmlsec environment failure, verified present on
  the base as well (same posture as B4's evidence). The router
  endpoint functions are called directly in the suite.

## 8. Plan-safety notes

No untrusted plan content was found in the ticket, its
coordinator comments, the build map, or the spec sections used.
Nothing in the sources asked for a destructive,
credential-handling, or gate-skipping step. The B3, B4, and B1
worktrees were read for reference and not modified; no other
agent's lane was touched.
