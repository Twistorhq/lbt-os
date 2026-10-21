# TW-677 Verification: PR #4 fix round spot-check

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Branch: feat/tradeview-lite-pro, head verified at 348e8d7 (PR #4, OPEN)
Standard: docs/reviews/PR4-review.md (REQUEST CHANGES: B1, M1 to M3, m1, m2)
Date: 2026-10-21

## Verdict: APPROVE

Every builder claim in docs/evidence/TW-677-pr4-fix-round.tdd.md was
reproduced by my own execution, not taken on trust. The blocker is fixed,
the auth.py resolution meets both non-negotiables from M1 line by line,
the browser QA gate was performed and I inspected the screenshots myself,
and the merged tree adds 21 net passing tests and breaks nothing in any
environment. Justynn's merge word is on file; the merge may execute.

## Per-finding verification

### B1 (blocker): FIXED, verified

- Code: require_tradeview_subscription in backend/app/auth.py now rejects
  on `auth.plan not in PLAN_ORDER`, so the recognized set derives from
  PLAN_ORDER keys (basic, pro, premium, enterprise, trades_starter,
  trades_pro), the single source of truth. No second hardcoded list.
  Fail-closed conditions preserved: non-active status or missing Stripe
  subscription id still returns 403.
- Tests: test_tradeview_entitlement.py carries the parametrized allow-test
  over all six sold plans, plus deny-tests for trades plans without an
  active subscription (inactive status, missing subscription id).
- RED reproduced by me at commit 31cb35f in a fresh venv: exactly 3 failed
  / 18 passed, and the three failures are precisely the diagnosed cases:
  test_policy_allows_every_sold_plan[trades_starter],
  test_policy_allows_every_sold_plan[trades_pro],
  test_policy_allows_owner_without_subscription.
- GREEN at head, my venv: entitlement 21 of 21 passed; targeted Trade View
  backend (entitlement, api, actions, leaks) 71 passed.

### M1 (auth.py resolution): FIXED, spot-checked line by line

- AuthContext constructor is the required union: the PR's fields keep their
  positional slots (user_id, org_id, plan, subscription_status,
  stripe_subscription_id) and TW-484's is_owner is appended last with
  default False. New fields default fail-closed (inactive, None).
- My own grep over backend/ confirms every positional construction passes
  at most the PR's five slots in the original order, and main-side callers
  use keywords or at most three positional args. No caller changes meaning.
- get_auth selects the extended column list (id, plan, subscription_status,
  stripe_subscription_id) and populates is_owner via resolve_owner_status,
  TW-484's cached lookup, unchanged. The owner machinery (is_owner_email,
  _owner_allowlist, resolve_owner_status) came through the merge intact.
- The new gate honors the owner bypass exactly as require_plan does:
  `if auth.is_owner: return auth` is the first check in
  require_tradeview_subscription, mirroring require_plan's structure and
  comment. The owner entitlement test (owner, inactive, no subscription)
  passes at GREEN and failed at RED, as required.
- No conflict markers remain in auth.py; the merge commit 8edbf06 carries
  auth.py as its only textual conflict resolution, matching the prediction.

### M2 (browser QA): PERFORMED, screenshots inspected by me

- docs/evidence/TW-677-pr4-browser-qa-390.png: Lite sample at phone width.
  SAMPLE DATA and ILLUSTRATED SAMPLE, NOT TO SCALE labels present; closing
  honesty line present (fictional properties, no charges, bookings,
  messages, or saved changes); layout stacks cleanly, no clipped or
  overlapping text.
- docs/evidence/TW-677-pr4-browser-qa-desktop.png: same surface at desktop
  width, map and request card side by side, labels and closing line
  present, layout clean.
- docs/evidence/TW-677-pr4-browser-qa-390-pro-locked.png: the Pro tab shows
  SUBSCRIPTION ACCESS, YOUR ORGANIZATION ONLY, states that access requires
  an eligible subscription and an authenticated organization, and shows no
  connected data. The boundary the iteration doc promised is what renders.
- The iteration doc carries an addendum recording the performed gate and
  honestly listing what remains unperformed (provider sandbox, Google,
  real customer, production). Acceptable scope: the doc's gate named the
  preview artifact, and the connected UI is covered by the frontend suite.

### M3 (em dash in preview title): FIXED, verified

- Generator (frontend/scripts/export-tradeview-preview.mjs) title now
  reads "Trade View Lite: Interactive sample".
- My grep over the regenerated docs/previews/tradeview-lite-v1.html:
  zero em dash and zero en dash characters.
- The rendered page title in the M2 screenshots matches the fixed string.

### m1 (attribution): FIXED

- docs/tradeview-lite-pro-iteration-1.md header reads "Prepared by Twistor
  Holdings LLC".

### m2 (duplicate previews): FIXED

- docs/previews/tradeview-lite-v2.html is removed from the tree; the
  generator writes only v1; my grep finds no remaining references to v2
  outside the fix-round evidence describing the removal.

### m3, m4: correctly left alone

- Both were noted as not this PR's code or adequate as-is in the original
  review. The fix round did not touch them; the evidence doc says so.

## Suite evidence (all run by me)

- Sandbox python (the environment of the original baseline), full
  backend/tests with --continue-on-collection-errors:
  clean-main control (6d1af39): 1436 passed, 20 failed, 7 errors.
  Merged tree: 1436 passed, 20 failed, 8 errors.
  FAILED node sets are byte-identical between the two runs (diff empty);
  composition 8 bi_whitelabel, 11 missed_call, 1 pilot_blockers, the
  standing TW-471 baseline.
- Fresh venv built by me from backend/requirements.txt (lxml/xmlsec
  pairing consistent, app.main imports), full suite:
  control 1564 passed, 0 failed, 0 errors;
  merged 1585 passed, 0 failed, 0 errors.
  Delta is exactly plus 21, the new entitlement tests, zero breakage.
- Frontend: fresh npm ci, npx vitest run: 155 passed, 0 failed (18 files).
- Ruff (0.16.7): ruff check clean on backend/app/auth.py and
  backend/tests/test_tradeview_entitlement.py. ruff format --check flags
  both files, as it flags main's own auth.py; format drift is the repo
  baseline, ruled not a finding in the original review and unchanged here.

## Ruling on the 8th collection error

Question left to me: the merged tree in sandbox python shows one more
collection error than the clean-main control (8 vs 7), the extra file
being the PR's own new tests/test_tradeview_entitlement.py, failing
collection with xmlsec.Error (100, lxml and xmlsec libxml mismatch), the
identical signature that already takes out seven files on clean main,
including all three sibling Trade View test files.

Ruling: the zero-new-failures bar is satisfied. Reasons:

1. It is not a failed test. No test in the file ran and failed in that
   environment; the file cannot be collected there because app.main's
   import chain hits a broken system library pairing. The FAILED node
   set, which is what the bar measures, is byte-identical.
2. The cause is pre-existing and environmental, identical on clean main
   without this PR, and already tracked under TW-471. The sibling files
   have carried the same error since before this branch existed.
3. In a correct environment the same merged tree is fully green and the
   file passes 21 of 21, while the control is also fully green at 1564.
   The change adds tests and breaks nothing wherever tests can run.
4. The contrary ruling would make it impossible to add any test file that
   imports app.main until the sandbox pairing is repaired, which punishes
   the fix for an environment defect the branch did not create.

The standing recommendation is unchanged: repair the sandbox lxml/xmlsec
pairing under TW-471 so the full suite can collect in the default python.

## Evidence-doc audit (TW-122)

Every number quoted in TW-677-pr4-fix-round.tdd.md that I re-ran matched
exactly: RED 3 failed / 18 passed with the same three node names, GREEN
21, targeted 71, vitest 155, sandbox pair 1436/20/7 vs 1436/20/8 with
identical FAILED sets, venv pair 1564 vs 1585. No result reads PASS for a
test that was never run; the guarantees table maps to real tests I
executed. No plan-embedded override instructions in the evidence or the
iteration doc; the fix round performed M2 rather than seeking a waiver.

## Process

- Fix commits are tightly scoped: RED touches only the test file, GREEN
  only auth.py, the preview commit only the generator, previews, and
  iteration doc, the QA commit only screenshots, evidence, and the doc
  addendum.
- Ticket TW-677 stays in-progress pending Marcus's merge execution; this
  note is the closing gate. Merge on Justynn's word already given.
