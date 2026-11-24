# TW-1329 TDD Evidence: Quantum Custody Collector

Prepared by Twistor Holdings LLC

## 1. Source

Ticket TW-1329 (build arm of the TW-1328 quantum exit plan). Brief:
build the custody collector the same day the founder killed the IBM
lane — a provider-agnostic quantum store in Supabase, adapters behind
one interface, data pulled into Twistor custody in the same run it is
produced, and a nightly export out of Supabase so no provider
deactivation can ever take the record. Branch
`dre/tw-1329-quantum-custody`, own worktree. Build commit `fc1b326`;
Rosa gate PASS (96) with one MAJOR (export verification count check)
fixed in the round recorded in section 3.

## 2. Build round: what the tests guarantee

Validation command:
`python -m pytest tests/test_quantum_custody.py tests/test_quantum_lane.py -q`
Result at build: **42 passed**. No network, no real DB: SQLite via the
SQLAlchemy mirrors of the Supabase migration, fixture transports, fake
boto clients. The suite (test_quantum_custody.py) guarantees, one test
or more per TW-1328 custody mechanic:

1. **Intent first.** The job row exists in our store, status `intent`,
   before the transport's submit hook fires; the provider job ID is
   recorded on the row the moment submission returns.
2. **Pull on completion.** Result payload lands in bronze verbatim
   (read back and compared), a fresh backend snapshot is captured, and
   the job flips to `custodied` in the same run.
3. **Reconciliation sweep.** Idempotent: a second sweep custodies what
   completed, a third finds nothing pre-custodied remaining.
4. **Daily diff.** A provider-side job we never submitted is recovered
   into custody with provenance `recovered_by=daily_diff`, unknown
   fields left unknown (shots 0, never invented), and the gap pages as
   a `diff_gap` alert.
5. **Daily telemetry snapshots** write the snapshot ledger
   (properties + configuration kinds) whether or not jobs ran.
6. **Auth failure pages same-day.** A revoked provider raises
   `CustodyAuthAlert`, writes exactly one `auth_failure` alert row,
   and pages the named channel with the named owner. It never pauses
   silently (the TW-838 failure shape).
7. **Deactivation drill.** Provider revokes mid-life after two jobs
   custodied: custodied count is unchanged (zero data loss), one alert
   is raised, and the in-flight job stays named with its circuit
   intact and re-runnable on the fallback.
8. **Braket adapter.** Fails closed with `AdapterUnavailable` naming
   the founder unlock (AWS account) when prerequisites are absent;
   with injected fake boto clients it submits, maps status, reads
   staged S3 results, lists job IDs, and maps a 403 to
   `ProviderAuthError`.
9. **IBM transport is a retired stub** (founder decision): every entry
   point raises `AdapterUnavailable` saying so.
10. **Published source.** The credential-free qiskit-fake-provider
    source yields real published backend snapshots with the device's
    own calibration date, provenanced as published, never as account
    telemetry (skipped when the package is absent from the test env).
11. **Model constraints.** Bad job status and bad alert kind are
    rejected by the schema check constraints; a source scan guards
    the transports against hardcoded credentials.

## 3. Rosa fix round: one-sided export verification

Rosa's MAJOR on the gate: `quantum_custody_verify_export.sh` promised
in its header (and the runbook promises) that a shortfall exits
nonzero, but its only count assertion failed solely when the export
held MORE jobs than the live store. A short dump passed silently.

**RED.** New test `test_verify_export_fails_on_short_dump`
(tests/test_quantum_custody.py) fabricates an export holding 3 jobs
against a stubbed `psql` reporting 5 jobs in the live store at export
time. Against the pre-fix script it failed as designed: the script
printed `live jobs=5, export jobs=3` followed by `OK` and exited 0.

**The invariant enforced.** pg_dump writes one consistent snapshot,
taken seconds after the export directory is stamped, and `created_at`
is stamped by the store at insert. So the dump must hold every job
with `created_at <= export stamp` — a computed lower bound, read from
the live store at verify time. Exact equality with the *current* live
count is not the invariant and would false-fail weekly: the store
keeps growing after the nightly export, so a healthy dump trails
today's count. The fixed script brackets the dump count:
`as_of_count <= dump_count <= live_count`, and exits nonzero with an
`export_failure` alert below the lower bound. The header now states
this reasoning in place of the old tolerance wording.

**GREEN.** The new test passes both halves: the short dump exits
nonzero and writes the alert; a complete dump (5 of 5 at export time)
trailing a grown live store (8) exits 0. Full custody suite:
**43 passed** (42 + the new test).

**Nits cleared in the same round.** The dead first `DUMP_COUNT`
assignment (a `grep -c` immediately overwritten by the awk count) is
removed. The `fail()` alert line in both shell scripts now routes the
error text through a `json_escape` helper (backslash, quote, newline,
carriage return, tab) before interpolating it into the JSON line;
proven end-to-end by forcing the export script to fail on a path
containing a literal quote and backslash and parsing the resulting
alert line with `json.loads`.

**Style gate.** `ruff check` and `ruff format --check` on the touched
Python file: clean. `bash -n` on both scripts: clean.
