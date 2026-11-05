# TW-951 (TW-948-B2): Bounded staged memory with receipts

Prepared by Twistor Holdings LLC

## 1. Source

- Ticket TW-951 (TW-948-B2), reporter Justynn, go dated 2026-11-05.
- Build map: `twistor-lab/agent-ops/tw948-tori-persistence-build-map.md` Sections 2.1 and 4 (TW-948-B2 entry).
- Spec: `twistor-lab/sales/tori-product-models.md` Section 8.1, memory paragraph.
- Base: branch `dre/tw-949-b1` tip `7df0bfb` (B1 gate-complete, not merged; merge is Justynn's separate word). This branch (`zeke/tw-951-b2`) rebases onto main when B1 merges.
- Built on B1, not rebuilt: `agt_staged_writes` staging with pinned target hash, the TW-552 masking pipeline (`mask_text`), and keyed HMAC hashing (`hash_body`) in `backend/app/persistence/`.

## 2. Task report

New module `backend/app/agent_memory/` (named to echo, not import, the Core prototype), plus one additive migration file (written, never applied):

- `models.py`: collections, hard limits (business facts 50 entries / 8000 chars; owner preferences and mandate notes 30 entries / 4000 chars), ops, gate modes, error types.
- `screening.py`: MemoryDefense-pattern screening. Whole-secret content blocked (API key shapes, private key blocks, JWT-only bodies, password-assignment-only bodies, bare high-entropy tokens); secret-shaped tokens inside prose redacted inline to `[redacted-secret]`; injection-shaped text blocked. Runs before staging and again at approval.
- `service.py`: propose/stage on the B1 staging table with the full target entry pinned by keyed hash (absent-entry marker for adds); bounds enforced on the batch result and re-checked at approval against live state; approve refuses stale pins (staged row marked `refused_stale`, refusal audited), re-masks as defense in depth, applies, records an audit event, and returns a receipt id (keyed digest over staged write id, body hash, and applied version, so the agent path cannot forge one). `claim_saved_memory` is the runtime-facing claim path: no receipt, unknown receipt, transplanted receipt, or forged receipt all fail. `assemble_snapshot` reads approved entries once per (org, agent, session) and freezes copies; mid-session approvals are invisible to the running snapshot and visible to the next session. Gate setting is per (org, agent): owner actors may relax or re-tighten; every non-owner change attempt is refused, so the agent has no path that removes a tightening.
- `supabase/migration_agent_memory_tw951.sql`: `agt_memory_entries`, `agt_memory_audit_events`, `agt_memory_gate_settings`, all scoped (org, agent, workspace), RLS deny-all, DOWN section documented. File only; no migration was applied.

Validation commands actually run (2026-11-05):

- RED (tests committed first, commit `c9ee3dd`): `python3 -m pytest tests/test_agent_memory_service.py tests/test_agent_memory_migration.py -q` failed at collection: `app.agent_memory` and the migration file did not exist.
- GREEN: same command plus the B1 suites: `102 passed` (B2 service 32, B2 migration 9, B1 persistence service 47, B1 persistence migration 14; the B1 total of 61 matches the B1 baseline).
- Broad suite: `python3 -m pytest tests/ -q --continue-on-collection-errors` gave `21 failed, 2762 passed, 76 skipped, 9 errors`. The 21 failures (test_bi_whitelabel, test_missed_call, test_pilot_blockers, test_telemetry_service) reproduce identically on the B1 base (verified: same 4 files on `dre/tw-949-b1` give `21 failed, 57 passed`); the 9 collection errors are a pre-existing xmlsec native-library environment issue in unrelated SAML modules. Zero new failures from this change.
- Lint: `python3 -m ruff check` and `python3 -m ruff format --check` on every authored Python file: clean (after one `ruff format` pass).

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Overflow batch refused, current entries and exact overflow returned, nothing staged or written | test_agent_memory_service.py::TestBounds::test_overflow_batch_refused_and_nothing_written | RED | PASS | excess_entries 1, excess_keys names the overflowing batch key, staged row count unchanged |
| 2 | Char bound enforced with exact excess chars | TestBounds::test_char_overflow_reports_exact_excess | RED | PASS | excess_chars 15 on a 4000 char collection bound |
| 3 | Bound applies to the batch result, not per op (a remove in the batch frees room for adds) | TestBounds::test_batch_result_is_what_counts_not_single_op | unit | PASS | full collection plus remove-plus-add batch stages cleanly |
| 4 | Bound re-checked at approval against live state | TestBounds::test_approve_time_bound_recheck | unit | PASS | second staged add refused once the first fills the last slot |
| 5 | Duplicates rejected: existing key, duplicate key inside one batch, update or remove of a missing key | TestDuplicates (4 tests) | RED | PASS | DuplicateEntryError and EntryNotFoundError raised before staging |
| 6 | Screening blocks a whole-secret write and a private key block; nothing staged | TestScreening::test_whole_secret_blocked_and_nothing_staged, test_private_key_block_blocked | RED | PASS | ScreeningBlocked; agt_staged_writes empty |
| 7 | Inline secret redacted before staging; inline PII masked by the B1 pipeline | TestScreening::test_inline_secret_redacted_before_staging, test_inline_pii_masked_by_b1_pipeline | RED | PASS | staged body carries the redaction marker and [email], never the raw values |
| 8 | Injection-shaped text blocked before staging | TestScreening::test_injection_shaped_blocked | RED | PASS | ScreeningBlocked; nothing staged |
| 9 | Approval applies the write, records an audit event, and returns a receipt recorded on the staged row | TestApproval::test_approve_applies_and_returns_receipt | RED | PASS | receipt id on entry, staged row, and audit event all agree |
| 10 | Staged patch refused after the target entry changed underneath (pin mismatch); also for an add whose key appeared | TestApproval::test_staged_patch_refused_after_target_changed, test_add_refused_when_key_appeared_underneath | RED | PASS | StaleTargetError; staged row refused_stale; live entry untouched |
| 11 | Approval re-masks as defense in depth (Rex F5 note from B1) | TestApproval::test_approve_remasks_as_defense_in_depth | unit | PASS | a staged body tampered with directly in the table is applied masked, never raw |
| 12 | Reject applies nothing and issues no receipt; remove applies with a receipt; double approval refused | TestApproval (3 tests) | unit | PASS | suite output |
| 13 | A memory claim without a receipt fails; a forged receipt fails; a real receipt verifies; a receipt from another scope fails | TestClaims (4 tests) | RED | PASS | MemoryClaimError on missing, forged, and transplanted receipts |
| 14 | Snapshot frozen at session start: mid-session approval invisible to the running snapshot, visible to the next; snapshot is a copy, not a live view | TestSnapshot (2 tests) | RED | PASS | same-session assemble returns the identical frozen object |
| 15 | Cross-agent and cross-org reads return nothing; approval from a foreign scope fails | TestScoping (2 tests) | RED | PASS | empty lists and empty snapshot across both boundaries |
| 16 | Gate relaxes per owner per Tori: default gated; owner relax auto-applies with a receipt; owner re-tighten restores staging; agent attempts to change the setting (relax or tighten) are refused; relaxation does not leak to another agent of the same owner | TestGateSetting (4 tests) | RED | PASS | GatePermissionError on every agent-side attempt |
| 17 | Migration contract: three agt_ tables only, scoped, CHECK-pinned vocabularies, entry uniqueness, RLS deny-all, DOWN documented | test_agent_memory_migration.py (9 tests) | RED | PASS | suite output |
| 18 | No regression: B1 persistence suites green; broad suite failure count unchanged at the 21 pre-existing failures | pytest full run, plus base comparison on dre/tw-949-b1 | CI | PASS | counts in Section 2 |

## 4. Coverage and known gaps

- Coverage: all acceptance behaviors in the map Section 4 B2 entry are test-pinned above. The service runs against the house in-memory Supabase fake, the same seam B1 uses; no live database was touched and no migration was applied.
- The pin stored on the B1 staging row is the keyed hash of the full target entry content (B1's column design stores the hash, never raw content, in the staging table); refuse-on-change compares the recomputed hash of the live entry. Storing the literal content in staging would break B1's no-raw-in-staging discipline, so the hash is the pin.
- Approve infers the op from the staged row (B1 staging carries no op column): a live entry plus an empty proposed body is a remove; no live entry plus a body is an add; a live entry plus a body is an update. A remove staged against an entry that is then removed by another approved write fails the pin check first, so the inference never acts on a stale target.
- Snapshot registry is in-process memory, matching the session-lifetime contract; a process restart ends sessions, and the next session assembles fresh from approved entries. Durable snapshot storage is a B6 concern, out of scope here.
- Entry keys pass through the B1 masking pipeline (the canonical-key rule), so a key shaped like an identifier is stored masked and two raw keys that mask to the same value collide as duplicates. Conservative by design; ordinary keys are unaffected.
- No UI, no router endpoints, no change to Core memory modules, no change to the agent_runtime module: the runtime surfaces receipts through the approve return value and verifies claims through `claim_saved_memory`, which is the tested claim path.
- Out of scope per ticket and untouched: the learning layer (observations and mental models stay Core-side proposers via B8).

## 5. Plan-safety notes

- No untrusted plan content was found in the ticket, build map, or spec sections read for this build. Nothing in the sources asked for destructive steps, credential handling, skipped validation, or unlogged work, and none was performed.
- One interpretation recorded: the ticket says the module is built "on the B1 tables" and the map's B2 entry also proposes "a curated memory table"; the curated collections therefore live in a new additive migration in B1's exact style while staging, masking, and hashing all reuse B1 unchanged.
