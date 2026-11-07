<!-- Prepared by Twistor Holdings LLC -->

# TW-940 Evidence: Twistor Social Safety Desk Minimum

## 1. Source

Ticket TW-940 (reporter Justynn): Social safety was partial
(safety.py constants + one report endpoint whose service was a
501 stub; no sanction, ban, appeal, evidence, or disclosure
endpoints). Build the Safety Desk Minimum for the Jan 1 Denver
launch on the interim trust model (no live identity verification
at launch; accountability is behavioral: standing, strikes, bans).
Ban enforcement hooks exist at the token/service layer
(get_member_context reads rsn_bans); the new endpoints feed them.
Base: main 45a845c. Branch: zeke/tw-940-safety-desk.

## 2. Task report

Per behavior, with the validation command actually run:
`python3 -m pytest tests/test_social_safety_desk.py -q`
(backend/, in-memory Supabase stand-in from test_social_core_loop).

- Report filing implemented (was the Phase 6 501 stub): voice
  required (ghost town cannot file), subject defaults to the
  target for user reports, intake routing by
  safety.ambassador_may_triage, case metro resolved from group /
  event targets (unresolvable = staff only), audit row written.
- Triage queue: list / claim / resolve. Staff (rsn_safety_staff,
  see section 5) see all; active ambassadors see only eligible
  categories in their metro, unassigned or their own. Claim is
  single-assignee, reporter cannot claim their own report,
  resolve requires the claim, 'actioned' requires a sanction or
  ban already issued on the case, ambassadors may only resolve
  'unfounded' or 'escalated' (escalation returns the case to the
  staff queue unassigned). Resolutions write the matching trust
  event (report_substantiated -5 / report_unfounded +1) once, at
  resolution.
- Sanctions: strike / suspend / restrict from a claimed case,
  staff or lead only. Strike writes an rsn_sanctions 'strike' row
  plus the identity_strike trust event (-10, the weight the
  check-in loop already uses). Suspend writes a 'suspension' row;
  expiry is required and capped at 72 hours
  (safety.suspension_within_cap, Section 6.2). Restrict writes a
  'restriction' row. Enforcement is in get_member_context: an
  active suspension removes voice until expiry; an active
  restriction removes hosting (create_group / create_event gate)
  and RSVP. Duplicate active same-level sanction on one case
  conflicts.
- Ban: safety_lead only, from a claimed case, identity-anchored
  rsn_bans row (platform or group scope). The existing
  get_member_context enforcement picks it up immediately
  (verified: banned member's list_groups is refused). Duplicate
  active platform ban for an anchor conflicts.
- Appeals: intake binds the target to the appellant (sanction's
  user, or the ban's anchor) and is deliberately open to banned /
  suspended members (due process; it grants no voice). SLA from
  safety.appeal_sla: ack_due_at +24h, sla_due_at +7d in the
  payload. Acknowledge (staff / lead) stamps ack_at and assigns.
  Decision requires a fresh reviewer: the issuer and the
  appellant are refused, a non-assignee staff member is refused
  (a lead may take an assigned case). Overturned lifts the ban
  (lifted_at, appeal_status) or retires the sanction (status
  'overturned'); modified rewrites a sanction's level (bans
  cannot be modified).
- Evidence: packets are created against a real report or ban
  case (missing case = 404), carry retention_until (default 365
  days from creation), and accept hash-chained manifest items
  (safety.evidence_item_hash; each item links prev_hash, the
  manifest carries chain_head). Legal hold is safety_lead only
  and freezes the manifest (attach then conflicts).
- Disclosure, internal: GET one report's disclosure view.
  Reporter gets outcome only; subject gets category, the
  sanctions / bans binding them, and can_appeal, never the
  reporter id or description; staff get the full case; anyone
  else gets the restricted shell (404).
- Disclosure, external: intake creates an rsn_disclosures row in
  pending_review; a safety_lead decision sets legal_review_by,
  and disclosed_at only on 'disclose'. Record-only workflow: no
  endpoint sends anything anywhere.

RED (before implementation, commit b7b3fcf):
`50 failed, 1 warning in 10.90s` (all 50 new tests failing:
NotImplementedError 501 stub, missing service functions, missing
models, missing migration file).

GREEN (after implementation, formatted):
- New suite: `50 passed, 1 warning in 1.66s`
- All Social suites: `413 passed, 1 warning in 2.80s`
  (test_social_safety_desk 50 + safety, router, core_loop,
  checkin_loop, rsvp_windows, models, trust, geo, migration,
  migration_v2; the 363 pre-existing tests still pass unchanged
  in behavior, one router test was updated because report filing
  is no longer a 501 stub).
- Ruff: `ruff check` on the six authored Python files: all checks
  passed. `ruff format --check` on the same six: 6 files already
  formatted.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Filing requires voice; ghost town cannot file | test_social_safety_desk.py::TestCreateReport | RED | PASS | 50 passed |
| 2 | Serious categories route to staff, routine to ambassador queue | TestCreateReport | RED | PASS | 50 passed |
| 3 | Plain members and non-active staff rows cannot read the queue (fail closed) | TestTriageQueue | RED | PASS | 50 passed |
| 4 | Ambassador queue is category- and metro-scoped; unresolvable metro is staff-only | TestTriageQueue | RED | PASS | 50 passed |
| 5 | Claim is single-assignee; reporter cannot claim own report; ambassadors cannot claim serious categories | TestTriageQueue | RED | PASS | 50 passed |
| 6 | Resolve requires claim and assignee; ambassador decisions are bounded (unfounded / escalated only) | TestResolveReport | RED | PASS | 50 passed |
| 7 | 'actioned' resolution requires a linked sanction or ban; resolutions write trust events | TestResolveReport, TestSanctions | RED | PASS | 50 passed |
| 8 | Strike writes sanction + identity_strike; suspension is 72h-capped and removes voice until expiry; restriction removes hosting | TestSanctions | RED | PASS | 50 passed |
| 9 | Ban is lead-only, platform ban feeds existing enforcement, group ban does not platform-ban, duplicate conflicts | TestBans | RED | PASS | 50 passed |
| 10 | Appeals bind to the appellant, banned members can appeal, duplicates conflict, SLA is 24h / 7d | TestAppeals | RED | PASS | 50 passed |
| 11 | Appeal decision refuses the issuer (fresh reviewer); overturn lifts sanction and ban; modified rewrites sanction level | TestAppeals | RED | PASS | 50 passed |
| 12 | Evidence packets need a real case, hash-chain their items, freeze on legal hold (lead only) | TestEvidence | RED | PASS | 50 passed |
| 13 | Disclosure views are audience-scoped; reporter / subject never see each other's protected fields; strangers get 404 | TestDisclosureViews | RED | PASS | 50 passed |
| 14 | External disclosure is a record-only lifecycle; only a lead decision sets disclosed_at | TestExternalDisclosures | RED | PASS | 50 passed |
| 15 | Migration v3 file declares the staff table, desk columns, and new sanction levels | TestMigrationV3File | RED | PASS | 50 passed |
| 16 | No regression in the pre-existing Social suites (363 tests) | Full Social suite command above | integration | PASS | 413 passed |
| 17 | Ruff check + format clean on authored Python | ruff commands in section 2 | CI | PASS | all checks passed |
| 18 | (Fix round 1) Subject can never claim or resolve their own case, incl. no report_unfounded trust event | TestSubjectConflictOfInterest | RED | PASS | 70 passed |
| 19 | (Fix round 1) Appeal decisions require safety_lead; plain staff cannot overturn a lead ban | TestAppealDecisionLeadGate | RED | PASS | 70 passed |
| 20 | (Fix round 1) Disclosure: staff-subject gets subject view; banned/suspended staff lose staff view; subject projections carry no note / issued_by / anchor | TestDisclosureAudienceGate | RED | PASS | 70 passed |
| 21 | (Fix round 1) Suspended staff lose triage (list / claim / resolve) via the shared _require_desk gate | TestSuspendedStaffTriageGate | RED | PASS | 70 passed |
| 22 | (Fix round 1) Issuer and appellant cannot acknowledge an appeal | TestAcknowledgeConflictOfInterest | RED | PASS | 70 passed |
| 23 | (Fix round 1) Sensitive reads (queue list, appeals list, evidence read, disclosure view) write rsn_audit_log rows | TestSensitiveReadsAreAudited | RED | PASS | 70 passed |
| 24 | (Fix round 1) Unanchored / subjectless sanction + ban return Conflict (previously untested guarantee) | TestUnanchoredSubjectConflict | unit | PASS | 70 passed |
| 25 | (Fix round 1) Modified-to-suspension re-validates the 72h cap; duplicate group-scope ban conflicts; couple partner desk restriction blocks pair RSVP | TestModifiedSuspensionCap, TestDuplicateGroupBan, test_social_rsvp_windows.py::TestCouplePartnerDeskRestriction | RED | PASS | 434 passed |

## 4. Coverage and known gaps

- Coverage: service- and router-layer behavior is covered by the
  50 new tests over the in-memory Supabase stand-in; no coverage
  percentage was measured (the Social suites have no coverage
  gate configured).
- MIGRATION NOT APPLIED: supabase/migration_social_v3.sql ships
  as a file only. It adds rsn_safety_staff (the desk's staff
  authorization record, which does not exist in v1 / v2), the
  'strike' / 'suspension' sanction levels, and the desk columns
  (rsn_reports.resolution_note / metro, rsn_bans.source_report_id
  / note, rsn_appeals.ack_at / decided_by,
  rsn_evidence_packets.retention_until, rsn_disclosures.status).
  The desk endpoints must not go live until this migration is
  applied on Justynn's word and staff rows are provisioned by
  hand; before it, the staff table does not exist and every desk
  call fails closed at the database.
- Sanctions and bans require the subject to have an identity
  anchor (the existing enforcement is anchor-keyed). A subject
  with no vfy anchor gets a ConflictError, stated in the service
  header; under the interim model this is the same limit the ban
  hook already has.
- get_member_context gained an optional `now` keyword (defaults
  to the real clock, unchanged for existing callers); only
  create_group threads its `now` through today. Other call sites
  resolve sanctions against the real clock, which is correct in
  production (sanction timestamps are real there).
- Ambassador metro scoping reads the case metro stored at filing
  (v3 rsn_reports.metro); reports filed before v3 have no metro
  and are therefore staff-only. That is the fail-closed direction.
- Not built (out of TW-940 minimum scope): staff console UI,
  purge pipeline endpoints, ambassador invitation / vetting flow,
  suspension lifting by staff outside the appeal path, retention
  destruction jobs at retention_until, notify workflows for SLA
  timers. SLA due times are returned as data (ack_due_at,
  sla_due_at) for a later notify slice.
- Environment note: importing app.main in this sandbox fails on
  main as well (xmlsec / libxml2 version mismatch in the SAML
  dependency); pre-existing, unrelated to this change. The social
  router is exercised through the TestClient suites above.

## 5. Fix round 1 (Rosa gate on ca339c6: REQUEST CHANGES)

Rosa reproduced seven must-fixes with in-memory FakeDb probes.
Each became a failing test first (RED), then the smallest fix.

RED (fix-round tests against the ca339c6 implementation):
`python3 -m pytest tests/test_social_safety_desk.py tests/test_social_rsvp_windows.py -q`
gave `17 failed, 72 passed`. The 17 failures are exactly the
probes and nit reproductions; 5 new tests (unanchored-subject
Conflict x3, subjectless-report Conflict, modified-within-cap)
passed pre-fix because they pin guarantees that already held.

GREEN (after the fixes, formatted):
- New desk suite: `70 passed` (50 original + 20 fix-round).
- All Social suites: `434 passed` (413 at Rosa's gate + 21 new:
  20 desk + 1 couple-RSVP), `tests/test_social_*.py -q`.
- Ruff: `ruff check` clean and `ruff format --check` clean on
  the three edited Python files (service.py, both test files).

What changed, per must-fix:

1. claim_report and resolve_report refuse the report's SUBJECT
   outright (ForbiddenError), any desk role, before any state
   change; the subject can no longer collect the +1
   report_unfounded trust event on their own case. Sanction / ban
   already self-checked and still do.
2. decide_appeal now goes through _require_desk(lead=True),
   matching the service header, the v3 migration header, and the
   test docstring, which all stated safety_lead from the start.
   Operative tests updated: overturn / modified reviewers are
   leads now, and the fresh-reviewer test uses a lead issuer so
   the refusal comes from the issuer rule, not the gate.
3. get_report_disclosure resolves the subject audience FIRST (a
   staff member who is the case subject gets the subject view,
   never the staff view with reporter_id and description), and
   the staff view is gated on the viewer being neither banned
   nor suspended; a banned / suspended staff non-party falls to
   the restricted shell (404).
4. Subject disclosure sanctions / bans are new redacted
   projections (_sanction_out_subject / _ban_out_subject):
   level / scope / status / dates / appeal position only. No
   note, no issued_by, no identity anchor hash.
5. list_report_queue / claim_report / resolve_report no longer
   hand-roll authorization: all three route through
   _require_desk with its new ambassador branch
   (allow_ambassador=True, which keeps the ambassador voice
   requirement), so suspended staff lose triage power exactly
   like every other desk action.
6. acknowledge_appeal refuses the appellant and the sanction /
   ban ISSUER (acknowledging assigns the case, so the issuer
   would self-assign the appeal of their own action), mirroring
   decide_appeal's conflict family.
7. The header's audit promise is now kept by auditing the reads
   instead of narrowing the claim: report.queue_listed,
   appeal.listed, evidence.viewed, and report.disclosure_viewed
   (detail = audience) each write an rsn_audit_log row. The
   header AUDIT paragraph says so explicitly.

Nits taken (all four of Rosa's logged nits):
- Tests for the unanchored-subject Conflict guarantee (sanction
  and ban) plus the subjectless-report Conflict.
- decide_appeal 'modified' to suspension re-validates the kept
  expiry with safety.suspension_within_cap at decision time
  (present, future, within 72h), not mere presence.
- Duplicate group-scope bans for the same anchor + group now
  conflict in issue_ban, like platform duplicates; a different
  group still succeeds.
- The couple-RSVP partner gate now checks the partner's desk
  'restricted' sanction flag alongside trust_band 'restricted',
  matching the single RSVP path (one line + test in
  test_social_rsvp_windows.py).

Untouched, as instructed: migration_social_v3.sql still ships
UNAPPLIED as a file only; fail-closed pre-v3 behavior unchanged;
the Rex posture list on TW-940 is not addressed here (it runs
after Rosa's re-check clears).

## 6. Plan-safety notes

No untrusted plan content was acted on. The brief, the ticket,
and the in-repo spec (openspec TW-646 design, ambassador scope)
were the only sources; nothing in them asked for destructive,
credential-handling, or external-send steps, and none were taken.
No merge, no deploy, no migration applied, nothing sent.
