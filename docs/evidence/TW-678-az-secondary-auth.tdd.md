Prepared by Twistor Holdings LLC

# TW-678 Evidence: Arizona Secondary Identity Verification Stage

## 1. Source

Ticket TW-678 (reporter Justynn, assignee Zeke Okafor), approved as
decision TW-672-D2 on 2026-10-21. Legal basis: Angela Price's
TW-672 addendum, Finding 2 and Section 3: A.R.S. 4-101 condition
(b) requires the record holder's identity to have been previously
verified through a secondary electronic authentication process
using commercially available data (public records query, knowledge
based authentication) or a state or federal government system of
records. The TW-658 engine had no such stage; the TW-671 door
resolver already disabled any profile naming the
`secondary_identity_check` handler.

## 2. Task report

Design, in one paragraph: a new enrollment stage in
`backend/app/verify/engine/secondary.py` behind a port, on the
TW-658 discipline. The request that crosses the port carries only
the HMAC field hashes and the identity anchor the document stage
already computed (medallion doctrine: hashes at ingestion, bronze
only); the result is a verdict plus reference hashes, persisted
only as a `secondary_identity_check` signal. `EngineConfig.
secondary_identity_required` marks a flow that requires the stage
(Arizona-purpose enrollment); the verdict combiner then refuses
to pass without a passed secondary result (review reasons
`secondary_identity_failed` / `secondary_identity_unavailable`).
The stage holds every adapter answer to an evidence bar: a
claimed pass with no statutory source kind, no source record
reference, or no match on the name and birthdate core is
downgraded to failed. The door resolver's handler gate now
follows genuine wiring: `built_enrollment_handlers(port)` adds
`secondary_identity_check` only when a configured adapter
declares `production_grade`, and `ProfileResolver` accepts the
resulting handler set.

RED (before the stage existed), command:
`python3 -m pytest tests/test_verify_engine_secondary.py -q`
Output excerpt: `ImportError: cannot import name
'built_enrollment_handlers' from 'app.door.profiles'` (collection
error; the secondary module likewise absent). Committed as the
RED checkpoint `ed37a48`.

GREEN (after implementation), same command: `19 passed in 0.49s`.
Neighboring suites: `tests/test_verify_engine_secondary.py
tests/test_door_pass_biometric.py tests/test_verify_engine_
pipeline.py tests/test_verify_engine_core.py` = 133 passed.
Verify + door selection (`-k "verify or door"`,
--continue-on-collection-errors): 207 passed, 7 errors, all 7
the standing xmlsec collection defect.

Full suite, branch vs same-environment clean-main control (both
`python3 -m pytest tests/ -q --continue-on-collection-errors`):
branch 1455 passed / 20 failed / 7 errors; control (main
6d1af39) 1436 passed / 20 failed / 7 errors. The failed + error
NODE SETS are byte-identical (27 nodes: the TW-471 baseline), and
the +19 passed delta is exactly the new test file. Zero new
failures.

Lint: `ruff check` and `ruff format --check` clean on all eight
touched files.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | No configured source reports unavailable, never passed | test_verify_engine_secondary.py::TestEvaluateSecondaryCheck::test_no_port_is_unavailable_never_passed | RED | PASS | 19 passed run |
| 2 | A records miss and a records field mismatch both fail the stage | TestEvaluateSecondaryCheck fixture miss / wrong-DOB tests | RED | PASS | 19 passed run |
| 3 | An adapter claiming a pass without source evidence is downgraded to failed | test_lying_adapter_cannot_pass_without_evidence (+ pipeline twin) | RED | PASS | 19 passed run |
| 4 | An AZ-required enrollment with a records hit passes, persists a template, and its secondary signal holds hashes only (no name, birthdate, or document number plaintext) | TestPipelineSecondaryStage::test_required_with_records_hit_passes_and_signals_hashes_only | RED | PASS | 19 passed run |
| 5 | An AZ-required enrollment with a records miss, no port, or a lying adapter cannot complete: verdict review, tier 0, no biometric persisted | TestPipelineSecondaryStage (miss / no-port / liar tests) | RED | PASS | 19 passed run |
| 6 | Flows that do not require the stage are unchanged: pass with no secondary signal written | test_not_required_runs_no_secondary_stage | RED | PASS | 19 passed run |
| 7 | The door handler gate follows wiring, not claims: default set and fixture adapter keep the handler unbuilt; a production-grade adapter adds it; a resolver given the wired set activates a profile the default resolver disables | TestHandlerGateTruthfulness (5 tests) | RED | PASS | 19 passed run |
| 8 | Arizona's shipped posture is unchanged: fixture still resolves disabled | test_arizona_fixture_still_disabled_today | unit | PASS | 19 passed run |
| 9 | TW-654 D3 is untouched: production capture still defaults off | TestConfigPosture::test_d3_capture_flag_untouched | unit | PASS | 19 passed run |
| 10 | No regressions anywhere in the backend suite | Full-suite node-set diff vs clean-main control | CI | PASS | 27 nodes identical, both runs |

## 4. Coverage and known gaps

Coverage: the stage's unit surface (evaluation, evidence
downgrade, fixture membership), the pipeline integration (pass,
miss, no-port, liar, not-required), the verdict combiner reasons,
and the door handler gate are all under test. No coverage
percentage is quoted; this suite does not measure one.

Known gaps, stated plainly:

1. **No production data source is wired, and that is the
   headline.** The FOSS survey (Section 5) found no $0 source
   meeting condition (b). `adapters.secondary_port()` therefore
   returns None in production wiring (router.get_pipeline passes
   it through), so today the stage makes AZ-required enrollments
   fail closed into review rather than pass. Turning the stage
   live needs Justynn's spend decision: license a commercial
   public-records / KBA product, or contract AAMVA DLDV directly
   or through a gateway partner (setup fee plus per-transaction
   fee). No paid API was wired, per the standing FOSS-first rule.
2. **No production caller sets `secondary_identity_required`
   yet.** The Arizona enrollment surface belongs to the TW-653
   build (the door service is prototype-scale and runs on
   fixtures); the flag, the enforcement, and the resolver wiring
   the TW-653 builder needs are in place and tested.
3. **The port is a single check call.** A knowledge based
   authentication adapter, if one is ever licensed, is
   interactive (generate quiz, then score answers) and will need
   a two-phase extension of the port. The records-query and
   government-records routes fit the current shape.
4. The secondary port is a pipeline constructor parameter, not a
   `PipelinePorts` field: it is an enrollment records source,
   not a model stage, and PipelinePorts documents itself as the
   model boundary. Noted so the placement reads as a decision,
   not an oversight.

## 5. FOSS data-source survey (2026-10-21)

Question: can any free or self-hostable source perform the
condition (b) check? Method: targeted web survey of the two
statutory routes, read the same day.

- Knowledge based authentication: every located implementation
  is a paid per-use product built on commercial data (DocuSeal
  KBA add-on at $2 per verification attempt; Adobe Acrobat Sign
  KBA as a premium per-use charge on enterprise plans; DocuSign
  KBA generated from LexisNexis data; PandaDoc KBA sold in
  verification packages). The quiz data is the product; there is
  no open dataset behind any of them.
- Public records query: the aggregators behind these products
  (LexisNexis and peers) are commercial. Open public data
  (sanctions lists, statistical name data, state voter files
  with use restrictions) is not an identity verification source
  for the general public and does not evidence that a specific
  record holder matches a specific document.
- Government system of records: AAMVA's Driver's License Data
  Verification (DLDV), the natural fit for driver licenses,
  prices as a one-time setup fee plus a per-transaction fee for
  direct connections, and gateway partners charge on the same
  shape; the vendor APIs advertising AAMVA checks are paid
  products reselling that access. SSA and IRS verification
  programs are scoped to employers and tax transcripts, not
  consumer enrollment.
- Checks derivable from data enrollment already holds: the
  engine's OCR / MRZ / PDF417 cross-checks are condition (a)
  document authentication, which Angela's Finding 2 expressly
  distinguishes from condition (b). Re-labeling them as the
  secondary check would be a false claim, and this build does
  not do it. A human-review-backed records check was considered
  and rejected as a stage claim: a human reading the same
  document data is review, already modeled, not a secondary
  electronic authentication against an outside records source.

Verdict: STOP at the port, exactly as the brief instructed for
this outcome. Port, stage, verdict enforcement, and gate wiring
are built and tested against a fixture adapter; the production
source is a spend decision for Justynn.

## 6. Plan-safety notes

No untrusted plan content was encountered: the brief, the
TW-672 addendum, and the TW-671/TW-658 code were read as
requirements and evidence. Nothing in any of them instructed
credential handling, destructive action, or a skipped gate. The
survey results above are third-party marketing and reference
pages, used as data about pricing and access only.
