# TW-677 Evidence: PR #4 fix round (Trade View Lite and Pro access boundary)

Prepared by Twistor Holdings LLC
Author: Zeke Okafor, Lead Build Engineer
Ticket: TW-677 (fix round for lbt-os PR #4, branch feat/tradeview-lite-pro)
Reviewer of record: Rosa Delgado, PR4-review.md (REQUEST CHANGES, 95 percent)

## Source

Rosa's review of PR #4 at head 27378dc against merge target main 6d1af39:
one blocker (B1), three majors (M1, M2, M3), four minors (m1 to m4; m3 and
m4 noted as not this PR's work). Justynn's merge word was already on file.
This round lands the fixes and leaves the branch for Rosa's spot-check of
the resolved auth.py plus the merged-tree full-suite comparison. No merge
was performed by the author.

## What was done, per finding

### Merge of current main into the PR branch (commit 8edbf06)

`git merge origin/main` (6d1af39) into feat/tradeview-lite-pro. Exactly
one textual conflict, backend/app/auth.py, as Rosa predicted; the other
16 PR files and all of main's growth auto-merged.

### M1: auth.py conflict resolution (in the merge commit)

Two conflict hunks, resolved as a union, per Rosa's M1 wording:

- AuthContext constructor: the PR's fields keep their positional slots
  (user_id, org_id, plan, subscription_status, stripe_subscription_id)
  because the PR's own tests construct them positionally, and TW-484's
  `is_owner` is appended as the final parameter with default False. Every
  main-side caller uses keywords or at most three positional args
  (verified by grep over backend/), so no caller changes meaning.
- get_auth: the select keeps the PR's extended column list (id, plan,
  subscription_status, stripe_subscription_id) and the constructor call
  populates both halves: subscription_status and stripe_subscription_id
  from the org row, and is_owner via TW-484's resolve_owner_status
  (cached lookup, unchanged from main).
- Main's owner machinery (is_owner_email, _owner_allowlist,
  resolve_owner_status) and TW-550's module-level PLAN_ORDER with
  require_plan's owner bypass came through the merge intact.

### B1 and M1(b): the gate itself (RED 31cb35f, GREEN 38568f4)

require_tradeview_subscription in backend/app/auth.py:

- Owner bypass first, mirroring require_plan exactly:
  `if auth.is_owner: return auth`. Without it the owner account was
  locked out of Trade View unless it also held a Stripe subscription.
- The plan allowlist is no longer a hardcoded set
  ({basic, pro, premium, enterprise}) that predates TW-550. It derives
  from PLAN_ORDER keys, the single source of truth that already maps
  trades_starter (pro level) and trades_pro (premium level) alongside
  the legacy plans. Grandfathered legacy subscriptions still pass;
  unknown plan strings still fail closed.

### M3, m1, m2: preview and docs (commit 75bfef1)

- M3: frontend/scripts/export-tradeview-preview.mjs title changed from
  the em dash form to "Trade View Lite: Interactive sample"; the v1
  preview was regenerated from current source with the project's own
  export script. Grep of the generated file finds zero em dash and zero
  en dash characters.
- m1: docs/tradeview-lite-pro-iteration-1.md attribution corrected to
  "Prepared by Twistor Holdings LLC".
- m2: the generator wrote the identical HTML string to v1 and v2 (same
  content hash, 236,096 bytes each, at Rosa's inspection). Nothing in
  the repo referenced v2; the iteration doc references only v1. The
  generator now writes a single artifact (v1) and
  docs/previews/tradeview-lite-v2.html is removed, so no future reader
  hunts for a difference that does not exist.

### M2: pre-merge browser QA gate, performed (this commit)

The iteration doc's own gate (real-browser review at 390px and desktop)
was executed against the regenerated docs/previews/tradeview-lite-v1.html
in headless Chromium (Playwright, Chromium 1248 build) on 2026-10-21.
Screenshots: docs/evidence/TW-677-pr4-browser-qa-390.png,
docs/evidence/TW-677-pr4-browser-qa-desktop.png,
docs/evidence/TW-677-pr4-browser-qa-390-pro-locked.png.

Findings, both widths unless noted:

- Page title renders as "Trade View Lite: Interactive sample" (M3 fix
  confirmed in the rendered artifact, not just the source).
- No horizontal overflow: scrollWidth equals clientWidth at 390 and at
  1440; zero elements extend past the viewport on either width.
- Zero console errors and zero page errors.
- Honesty labels all present in rendered text: "SAMPLE DATA",
  "ILLUSTRATED SAMPLE, NOT TO SCALE", and the closing line "Fictional
  properties and workflow. No charges, bookings, messages, or saved
  changes."
- Interactivity: the React bundle is live (8 buttons). Clicking the
  "Pro: Connected data" tab swaps the view to Trade View Pro with the
  subscription boundary state ("Access requires an eligible subscription
  and an authenticated organization"); no connected data is shown.
- Layout reads clean at both widths (visually inspected): 390 stacks
  map over the request card; desktop places them side by side; no
  clipped or overlapping text found.

## Task report: validation commands and outcomes

- RED (merged tree, gate unfixed): entitlement tests with new cases,
  3 failed / 18 passed. The three failures are exactly Rosa's diagnosis:
  trades_starter denied, trades_pro denied, owner denied.
  Command: `python -m pytest tests/test_tradeview_entitlement.py -q`
  in the verification venv described below.
- GREEN (after the gate fix): 21 passed, same command.
- Targeted Trade View backend on the merged tree: 71 passed
  (entitlement, api, actions, leaks). Rosa measured 61 at PR head; the
  delta is the 9 new entitlement cases plus main-side test growth.
- Frontend on the merged tree: `npx vitest run`, 155 passed, 0 failed
  (94 at PR-head review; main brought the rest).
- Ruff (0.16.8, the CI pin): `ruff check backend/app/auth.py
  backend/tests/test_tradeview_entitlement.py`, all checks passed.

## Full-suite node-set comparison (Rosa's decisive check)

Two environments were run, each as a pair (merged tree vs a same-run
clean-main 6d1af39 control), full backend/tests with
--continue-on-collection-errors:

1. Sandbox default python (the environment of Rosa's baseline):
   - Control: 1436 passed, 20 failed, 7 errors. This reproduces her
     stated baseline exactly (TW-471: 8 bi_whitelabel, 11 missed_call,
     1 pilot_blockers failures; 7 xmlsec collection-error files).
   - Merged tree: 1436 passed, 20 failed, 8 errors.
   - FAILED node sets are byte-identical between control and merged
     (diff empty). The single delta is one additional collection-error
     file: tests/test_tradeview_entitlement.py, the PR's own new test
     file, failing collection with the identical pre-existing xmlsec
     libxml2 mismatch that already takes out its three sibling Trade
     View files (api, actions, leaks) plus four others on clean main.
     The cause is the sandbox user-site lxml/xmlsec pairing, not this
     change; the file cannot import app.main in this environment for
     the same reason the sibling files cannot.
2. Verification venv (fresh install of backend/requirements.txt, where
   the lxml/xmlsec pairing is consistent and app.main imports):
   - Control: 1564 passed, 0 failed, 0 errors.
   - Merged tree: 1585 passed, 0 failed, 0 errors.
   - The merged tree adds 21 net passing tests over control and breaks
     nothing; the entitlement file passes 21 of 21 here.

## Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Active trades_starter and trades_pro subscribers pass the Trade View gate | test_tradeview_entitlement.py::test_policy_allows_every_sold_plan | RED | Failed at RED (both denied), passes at GREEN | RED 3 failed / 18 passed; GREEN 21 passed |
| 2 | Legacy plans (basic, pro, premium, enterprise) still pass when synced active | test_policy_allows_every_sold_plan, test_policy_allows_synced_subscription | unit | Pass | GREEN run, 21 passed |
| 3 | Trades plans without an active subscription or Stripe reference still denied 403 | test_policy_denies_trades_plans_without_active_subscription | unit | Pass | GREEN run |
| 4 | Owner context passes the gate with no subscription (TW-484 doctrine) | test_policy_allows_owner_without_subscription | RED | Failed at RED, passes at GREEN | RED and GREEN runs above |
| 5 | Every connected Trade View route still denies before data access | test_every_route_denies_before_data_access | unit | Pass | Targeted run, 71 passed |
| 6 | Merged tree introduces zero new failed nodes vs clean main | Full suite, sandbox python, node-set diff | integration | Pass | FAILED sets byte-identical; counts above |
| 7 | Merged tree is fully green where the environment can import app.main | Full suite, verification venv | integration | Pass | 1585 passed, 0 failed, 0 errors |
| 8 | Generated preview carries no em or en dash | grep over docs/previews/tradeview-lite-v1.html after regeneration | manual | Pass | Zero matches; title "Trade View Lite: Interactive sample" |
| 9 | Preview renders correctly at 390px and desktop with the Pro boundary intact | Playwright Chromium render plus visual inspection | manual | Pass | Screenshots in docs/evidence, findings in M2 section |
| 10 | Ruff clean on touched backend files | ruff check (CI pin 0.16.8) | CI | Pass | All checks passed |

## Coverage and known gaps

- The one environment-bound gap: in the sandbox default python, the
  entitlement test file collection-errors with the standing xmlsec
  defect (see comparison note 1). It is fully green in the verification
  venv. Fixing the sandbox lxml/xmlsec pairing is environment work
  outside this ticket; TW-471 already tracks main being red in this
  sandbox.
- Rosa's m3 (trial semantics differ by write path: admin override can
  write a literal "trialing" status this gate denies) is not this PR's
  code and was not changed; it belongs to the billing follow-up the
  author scoped in the iteration doc, item 3.
- Rosa's m4 (route test exercises the first method of multi-method
  routes) was noted by her as adequate for a router-level gate; not
  changed.
- The browser QA covers the Lite preview artifact, which is the surface
  the iteration doc's gate names. The connected Pro UI behind real auth
  is exercised by the frontend vitest suite (155 passed), not by a live
  Clerk session; no production deployment was performed.

## Plan-safety notes

- Rosa's review and the iteration doc were read as data and checked
  against the code before any edit. No embedded override instructions
  were found in either. No step in this round was skipped or waived:
  M2 was performed, not waived.
