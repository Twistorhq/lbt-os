<!-- Prepared by Twistor Holdings LLC -->

# TW-648 Review: OpenSpec change package, Twistor Social (TW-646) + Door Pass (TW-647)

- Reviewer: Rosa Delgado, Chief Quality Guardian

- Branch: `zeke/tw-648-social-openspec` at bc7f2bf (package 0ea6051 + fold-in bc7f2bf)
- Scope reviewed: proposal.md, design.md, tasks.md, .openspec.yaml (658 lines, docs only)
- Sources checked: TW-646 architecture spec (Sections 4, 5.1 to 5.12, 6.3, 6.6, 6.7, 7.5, 7.7 to 7.10, 8, 9, 12), TW-647 Door Pass spec (as specced in design cross-references), reward catalog structure, TW-654 legal package (Angela, full text), TW-652 design foundation patterns 04 and 09 and README (Sofia, branch sofia/tw-652-social-surfaces at aa2a4f2)

## Verdict: REQUEST CHANGES

No blockers. Five majors, all in the fold-in material (the Oct 20 decisions and
the retention change list), none in the package's spine. The spine is strong:
gate 2's NO VENDOR FALLBACK is airtight in all four files, the ambassador
scopes match the brief point for point, and the density numbers trace exactly
to spec Section 9. The majors below are the kind that get expensive only if
they survive into Phase 2 to 4 code, which is what this review exists to
prevent. Nothing merges on this review regardless; Justynn's merge word is
still required per merge.

## Major findings

**M1. The retention corrected rule is incomplete against Angela's TW-654 text.**
design.md:278 (Retention change list) and tasks.md item 10a.
The change list was mine to verify against counsel's package, and it drops two
parts of Angela's rule:
(a) Colorado HB 24-1130 destruction has three triggers, not two (TW-654
Section 1.2): the earliest of purpose satisfied, 24 months after last
interaction, OR within 45 days after we determine storage is no longer
necessary. The design's formula (earlier of the Section 6.3 schedule or 24
months) never fires the third trigger, because the Section 6.3 schedule for
templates (life of tier, deleted on closure) does not contain it either.
(b) Angela's dormancy rule carries an account consequence the design omits
(TW-654 Section 1.4 item 2): at dormancy destruction the member's tier drops
to unverified, with notice, and re-verification is available. As written,
task 10a would build a job that destroys templates at month 24 and leaves
accounts asserting T2 with no template behind them, silently degrading the
1:1 re-challenge (spec 6.7b) and duplicate search (spec 6.7a), with no member
notice.
Fix: rewrite the corrected rule to carry Angela's rule in full (all three
triggers, logged counsel-confirmed holds), and add the tier-drop, notice, and
re-verification consequence to the change list and to task 10a.

**M2. The RSVP request and host-approval sub-state exists nowhere.**
design.md:116, proposal.md:44, tasks.md items 13, 16, 17b.
The home reveal tier rests on "host-approved confirmed guests (an RSVP the
host has individually approved)," but spec Section 4's `rsn_rsvps` carries
only going, waitlist, declined, no_show, and neither the spec nor this package
adds a requested or pending state or host decision fields. Sofia flagged this
in TW-652 pattern 09 Section D and assigned it to this change by name ("Zeke's
OpenSpec (TW-648) owns adding it"); the package does not add it. There is
currently no state for the approval to be.
Fix: add the sub-state to the design's data-model deltas (a requested status
with host decision fields, or an approvals table), wire it into the schema
task (13) and the RSVP task (16), and state pattern 09's companion rule:
close friends are confirmed on RSVP; everyone else passes through request
then host approval before any home reveal eligibility.

**M3. The home reveal window is unreconciled with spec Section 8.**
design.md home hosting paragraph; tasks.md item 17b.
The design places home reveals "inside the existing reveal window." Spec
Section 8's window is 48 hours before start until event end, and its reveal
also requires group membership, tier at or above `min_tier_view`, RSVP going,
trust band above restricted, and no ban or block conflict. Sofia's pattern 09
proposes a 3 hour home window against that 48, in writing: "tighter is right
for a residence, and the amendment should say so explicitly." The package
neither adopts nor rejects the 3 hour window and does not state which of
Section 8's conditions apply, narrowed how, to home events. Silence here
defaults the product's most sensitive address class to the loosest window in
the system.
Fix: decide the home window in the design (my recommendation: adopt the 3
hour proposal), enumerate the home reveal condition set explicitly (close
friend or host-approved confirmed guest, inside the home window, reveal
logged, plus whichever Section 8 conditions carry over), and mirror it in
task 17b so TW-650 and TW-652 build the same flow.

**M4. The design's precedence clause contradicts the package's own decisions.**
design.md:7.
"Where this design and a source spec disagree, the source spec wins" would, read
literally, resurrect three superseded spec positions over Justynn's recorded
2026-10-20 decisions: spec 5.5's vendor fallback paragraph ("better a paid
check than an untrustworthy free one... that fallback decision belongs to
Justynn"), spec Section 9's public-venues-only soft launch, and spec Section
12 questions 1 to 4 as still open. The package implements the decisions
correctly everywhere else; its own preamble instructs a future reader to undo
them.
Fix: precedence reads recorded 2026-10-20 decisions (as carried in this
package), then source specs, then this design; and list the spec fold-in edits
owed at archive time (5.5 fallback paragraph, Section 9 launch line, Section
12 closures) so the specs stop disagreeing with the decisions on paper.

**M5. Angela's launch gates are stated two ways, and the proposal takes the weaker one.**
proposal.md:102 to 103 (gate 3) and gate 4; design.md "Decisions that gate
specific phases."
The proposal says liquor-law, callout, law-enforcement, and liability items
"gate the copy and the venue pilot." The design's own decisions section says
meetup liability posture gates launch; Angela's decision list (TW-654 D6, D7)
makes the terms allocation and the insurance conversation pre-public-launch
items; and Sofia's pattern 09 places the home-event copy gates before the
first home event is created, which under gate 4 and task 41 can happen in
soft launch. Proposal gate 4 then omits Angela's launch items from the public
launch criteria entirely (task 41 does include "Angela launch items cleared,"
which is the correct instinct trapped in the wrong file). Sofia's third flag,
confirmed.
Fix: align proposal gates 3 and 4 with the design and TW-654: liability,
terms, and insurance gate public launch; home-event copy gates gate the first
home event (soft launch); callout and law-enforcement wording keep their copy
and runbook gates. One gate, one wording, everywhere.

## Minor findings (nits, non-blocking but fix in the same round)

**N1. Age band chip vs the spec Section 8 member display toggle.**
design.md display rules; tasks.md item 33a. Spec Section 8 gives members
control over whether their band displays on their profile (default shown).
The package mandates the chip and never says whether that toggle survives.
One sentence settles it: the chip is a door fact and is not toggleable (or
the toggle is scoped to standing display only). Sofia's pattern 00 already
renders the chip as designed social proof; make the package agree with itself
about who can turn it off.

**N2. Ambassador auto-suspension trigger wording.**
design.md:190. Eligibility admits safety band "trusted or good standing"
(design.md:151); suspension fires when the band "leaves good standing." Read
literally, a drop from trusted to good standing suspends an ambassador who
still meets eligibility. Clarify: suspension on drop below good standing.

**N3. Package-introduced tables have no migration home.**
tasks.md items 13, 17a, 26a. `rsn_close_friends` (17a), `rsn_ambassadors`
(26a), and the audience-scope columns on posts and events are introduced by
this package but absent from the Phase 3 schema enumeration (item 13 lists
spec Section 4 tables only). Name them in item 13 or in explicit phase
migrations, so TW-650 cannot close the schema without them.

**N4. Gate count mismatch.**
.openspec.yaml:29 lists three launch gates; proposal.md lists four (density
omitted from the yaml). Align.

**N5. Self-scan attestation unchecked.**
tasks.md:16. Item 3 (self-scan) sits unchecked while item 3a is checked and
both commits exist. My own scan of the package found no secrets, no embedded
instructions, and no scope beyond the source specs and the recorded
decisions, so this is bookkeeping only: complete or annotate the box.

## Observation, routed to TW-654 (not a defect in this package)

Angela's package, Section 1.4 item 4, still contemplates a future vendor
bridge ("if Justynn later authorizes the paid vendor bridge, spec Section 12
item 3"). The 2026-10-20 NO VENDOR FALLBACK decision forecloses it. When the
D-list is signed, that sentence should be struck or annotated so the bridge
cannot be revived later by citation.

## What I verified (the parts that earned their keep)

- Gate 2, NO VENDOR FALLBACK: present and unqualified in proposal gate 2,
  proposal out-of-scope, design Excluded section, .openspec.yaml gate text,
  and the decisions list. The fold-in diff removes every bridge remnant,
  including the original yaml wording. This is the doctrine, stated like one.
- Ambassador scopes against the brief: no sanction or ban power (recommend
  only), no evidence packet freeze or open, no face template or door photo
  reads, serious categories bypass to staff at intake, entitlement-layer
  enforcement (not UI), audit log on every action, automatic suspension on
  standing drop, band exit, or substantiated report, instant staff revocation
  with case reassignment, Angela's vetting before any queue access. All
  present.
- Home hosting protections: address bronze-only in `rsn_event_locations`,
  excluded from the public index, directory previews, and anonymous surfaces,
  neighborhood-only in list payloads, every reveal logged, full check-in loop
  applied. Present; the gaps are M2 and M3 (state and window), not the
  protections.
- Display rules: standing tier visible, punishment and restriction state
  rendered nowhere member-facing (with the member's own private notice
  preserved, which keeps due process intact), age band as a color chip only.
  Present and consistent with the decisions and with spec 7.5 as superseded.
- Density and SLA gate language (gate 4): 500 T2 and 10 seeded groups soft,
  2,500 verified-active, 70 percent fill, benchmark passed, and the review
  SLA conditioned on the ambassador program being recruited and vetted. Exact
  trace to spec Section 9 plus the staffing decision.
- Benchmark mechanics strengthen spec 5.3 honestly: INSUFFICIENT_DATA as a
  gate state, minimum sample sizes, per-group gap ceiling as planning values,
  final criteria set with the measured report in front of Rosa and Justynn.
  A group cannot pass by silence. Correct.
- Retention conflict statement is accurate (spec 6.3 quotes verified against
  the spec table); the defect is only in the corrected rule (M1).
- Hygiene: zero em-dashes and zero en-dashes in all four files (grep
  verified), Prepared by Twistor Holdings LLC header on all three documents,
  branch touches openspec files only, no secrets or credentials anywhere in
  the diff, and no embedded override instructions in the package or in the
  source material I read. Docs-only change, so the TDD evidence requirement
  is exempt; ruff has no Python to run on this branch.

## Verification limit, stated plainly

The author's "openspec validate: change is valid" claim is recorded in
tasks.md item 2 and the 0ea6051 commit message. The openspec CLI is not
installed in this sandbox and npx could not fetch it, so I did not rerun the
validation myself. The package structure matches the repo's existing change
conventions (proposal, design, tasks, .openspec.yaml, as in tw-550 and
tw-570), but the validate result stands on the author's run, not mine.

Rosa Delgado, Chief Quality Guardian

---

## Addendum: fix-round verification (2026-10-20, commit b2abc83)

**Verdict: APPROVE.** All five majors and all five nits from the review
above are closed, verified against the fix diff (bc7f2bf..b2abc83, 4 files,
151 insertions) and the sources, not the author's summary. No new findings.

- **M1 closed.** design.md's corrected rule now carries Angela's TW-654
  rule in full: destruction at the earliest of (a) purpose satisfied
  (including the S6.3 schedule events), (b) 24 months after last
  interaction, (c) within 45 days after storage is determined no longer
  necessary, extendable 45 days for complexity following an annual
  necessity review; holds are counsel-confirmed and logged. The dormancy
  consequence is present as its own paragraph: tier drops to unverified,
  with Notify notice in both locales, write scopes withdrawn to the
  ghost-town posture, re-verification available. tasks.md item 10a mirrors
  both halves, consequence shipped in the same job.
- **M2 closed.** design.md's data-model delta adds the `requested` status
  to `rsn_rsvps` with `host_decided_by` / `host_decided_at`; the home
  flow (requested, host approves to going or declines) and pattern 09's
  close-friend auto-confirm rule are stated. tasks.md items 13 and 16
  carry the delta; item 17b reads the state.
- **M3 closed.** The 3-hour home reveal window is adopted explicitly,
  with the reasoning recorded (most sensitive address class, tightest
  window; S8's 48 hours governs venues only). The home condition set is
  enumerated (a) through (g), narrower than S8's, with S8's group
  membership translated to audience scope. Task 17b mirrors both.
- **M4 closed.** Precedence reads DECISIONS FIRST: the recorded
  2026-10-20 decisions (yaml `x-decisions_2026_10_20` plus the TW-646
  ticket record), then source specs, then this design. tasks.md item 43a
  lists the three owed spec fold-in edits (S5.5 fallback paragraph, S9
  public-venues-only line, S12 Q1 to Q4 marked decided) with a landing
  rule: with the archive or earlier as its own reviewed edit, spec text
  known-stale in exactly those places until then.
- **M5 closed.** Proposal gate 3 now gates each TW-654 item in the
  design's words (liquor to venue sales and pilot, callout to
  member-facing copy, law-enforcement wording to the runbook, D6/D7
  liability to public launch, home-event copy gates to the first home
  event in soft launch); gate 4 adds Angela's launch items cleared (D6
  adopted, D7 held), matching tasks.md item 41, which still carries the
  line. Design's decisions section closes with "One gate, one wording."
- **N1 closed.** Chip is a door fact, NOT toggleable; S8's toggle
  survives scoped to standing display only (design.md and task 33a).
- **N2 closed.** Ambassador auto-suspension now fires when the safety
  band "drops below good standing," aligned with eligibility.
- **N3 closed.** Task 13's enumeration names `rsn_close_friends`,
  `rsn_ambassadors`, the audience-scope columns, and the RSVP sub-state,
  "so TW-650 cannot close the schema without them."
- **N4 closed.** .openspec.yaml now lists four launch gates, including
  the density gate with the D6/D7 launch items; matches proposal.md.
- **N5 closed.** Task 3 is checked with a dated completion annotation
  noting this review's concurrence.

Spot-checks on the fix round itself: gate numbering intact (proposal
gates 1 to 4; scope items 1 to 10 undisturbed), task cross-references
resolve (10a, 13, 16, 17a, 17b, 26a, 33a, 41, 43a all exist and say what
the design claims they say), decision dates consistent (2026-10-20
throughout), zero em-dashes and zero en-dashes in all four files (grep
verified), no secrets or embedded instructions in the fix diff.

Verification limit, unchanged: the openspec CLI still cannot run in this
sandbox (`npx` cannot fetch the executable), so the author's re-run
"change is valid" result stands on his run, as before; structure and
cross-references were verified by hand. Docs-only change; the TDD
evidence exemption applies as in round one.

The TW-654 observation from round one stands for the D-list signing: strike
or annotate Angela's Section 1.4 item 4 vendor-bridge sentence so it cannot
revive the bridge by citation.

Nothing merges on this approval. Justynn's merge word is still required.

Rosa Delgado, Chief Quality Guardian

