# TW-668 Review — stalemap implant (Jalen)

Prepared by Twistor Holdings LLC
Reviewer: Rosa Delgado, Chief Quality Guardian
Branches reviewed: lbt-os `jalen/tw-668-stalemap` @ e9a8992; twistor-website `jalen/tw-668-stalemap` @ 6e8bf05
Plus: `twistor-lab/devex/stalemap-fleet-notes-2026-10-21.md` and run logs in `twistor-lab/devex/stalemap-run-2026-10-21/`; exposure ticket TW-674 in scope.

## Verdict: REQUEST CHANGES

The stalemap notes themselves are genuine, accurate, and reproduce clean. The exposure
finding (TW-674) is real and correctly characterized. What blocks approval is the CI
proposal workflow, identical in both repos: as written it is not safe to apply as-is
on a merge word. Two majors, both confined to `.github/workflows/stalemap.yml`.
Fix those two files and this is an approve on a spot-check; the AGENTS.md blocks
need no changes.

## Findings

### M1 (major, both repos) — third-party action runs from a floating branch, and the header says it is pinned
`.github/workflows/stalemap.yml`, the `uses: Umair/stalemap@main` step in both branches.

- `@main` means every future CI run executes whatever `action/run.sh` is on upstream
  main that day. Upstream is a single-maintainer repository created 2026-10-19.
  The action is composite: its run step executes a shell script straight from the
  action checkout, with `GH_TOKEN` (default `github.token`) in its environment.
- The file header claims it uses the action "pinned to the stalemap npm version we
  run locally." The `version: 0.4.1` input pins the npm tool the action downloads;
  it does not pin the action's own code. The comment misdescribes the trust boundary
  a future reader will rely on.
- Fix: reference the action by full commit SHA, e.g.
  `uses: Umair/stalemap@e3eb54bfde3f27875188f51a3bfb46bd0b27e306 # main 2026-10-21`
  (the main HEAD I verified today; a release tag is acceptable only if it is a
  commit-pinned tag, not a movable one), and correct the header wording to say the
  npm version is pinned by the `version` input and the action by SHA. Upstream's
  own action internally floats `actions/setup-node@v7`; the SHA pin freezes that
  too, which is a second reason to pin.

### M2 (major, both repos) — no `permissions:` block; check mode needs read only
`.github/workflows/stalemap.yml` (whole file, both branches).

- Neither workflow declares `permissions:`, so the job's GITHUB_TOKEN carries the
  repository default, which may include write scopes. Check mode writes nothing:
  it reads the checkout and fails or passes. The upstream README confirms only
  `comment` mode needs `pull-requests: write`; we do not use comment mode.
- Fix: add `permissions: contents: read` (workflow or job level) to both files.

### n1 (nit, both repos) — `actions/checkout@v4` is a floating major tag
Consistent with existing repo practice (ci.yml uses `actions/checkout@v4` and
`gitleaks/gitleaks-action@v2`), so non-blocking. If the fix round touches these
files anyway, SHA-pinning checkout as well is welcome but not required.

### Observation (not a finding)
Token counts for the blocks (~724 / ~315) and scanned-file counts are tool-reported
and I did not independently recount them; the check hash, dependency set, and exit
codes are the load-bearing claims, and those I did reproduce.

## Verified by my own execution (all reproduced today, Node v24.20.0 sandbox)

1. **Upstream identity.** GitHub repo `Umair/stalemap`: public, MIT License
   classifier, created 2026-10-19. The npm artifact in the local npx cache,
   `stalemap@0.4.1` package.json, declares `repository: git+https://github.com/Umair/stalemap.git`
   and `license: MIT` — the published build is that repo's artifact. Attribution
   headers on both branches ("MIT, Copyright (c) 2026 Umair Ejaz and stalemap
   contributors") match the LICENSE line read live by the sweep and by Jalen.
2. **Blocks are genuine stalemap output, not hand-written.** Spot-checked against
   `package.json` + lockfiles: lbt-os jest-dom 7.0.1, clerk 5.61.5, axios 1.15.0,
   jsdom 30.1.1, react-query 5.99.0 all match the block and the lockfile pins;
   website @astrojs/react 4.4.2, react 19.3.0, stylex 0.19.1, @astryxdesign/core
   0.6.2 all match. Direct dependency counts are exact (lbt-os 20, website 8).
   `npx stalemap@0.4.1 check --model claude-sonnet-4-5 --all`: exit 0 in BOTH
   worktrees ("block is up to date"), reproduced by me, matching the run logs.
3. **No API-level exposure — greps re-run by me, clean** in lbt-os frontend:
   `TaskSelectOrganization`, `BrowserProgressEvent`, `sendTo`,
   `experimental_withState`, `getActionState` — zero hits outside the lockfile.
   The only `useOrganization` call (Sidebar.jsx) destructures `organization` only;
   no `subscriptions` param anywhere in `src/`. Website: `experimental_withState` /
   `experimental_getActionState` clean.
4. **TW-674 CONFIRMED, all three evidence points, from the lockfile and ci.yml:**
   (1) jest-dom 7.0.1 (lockfile) declares engines `node >=22` and peer
   `@testing-library/dom >=10 <11`; `@testing-library/dom` appears 0 times in
   `frontend/package.json` and resolves to 10.4.2 in the lockfile; `vitest.setup.js`
   line 1 imports jest-dom. (2) jsdom 30.1.1 (lockfile) declares engines
   `^22.22.2 || ^24.15.0 || >=26.0.0`; vitest config uses environment jsdom.
   (3) `.github/workflows/ci.yml` frontend job pins `node-version: 20` and runs only
   `npm ci` + `npm run build` — no vitest. "Latent, not red today" is accurate:
   nothing in the current CI job loads the test stack, and local Node 24 masks it.
5. **CI proposal mechanics.** Path triggers are correctly scoped per repo
   (frontend paths vs root) and the YAML parses in both files. Secrets: none in
   either diff (scanned). The workflow's only defects are M1 and M2 above.
6. **Data-portal verdict confirmed.** `~/workspace/repos/twistor-data-portal`
   exists locally and contains only CNAME, app.js, datasets, index.html,
   styles.css — no `package.json` anywhere (also none in the website's
   data-portal copy). Nothing for stalemap to scan; Jalen stated it plainly and
   correctly.

Process notes: evidence-report exemption applies (docs + CI-config change, no
product behavior change); the fleet notes served as run evidence and their central
claims reproduced. Ruff is not applicable — no Python files are touched by either
diff. No plan-embedded override instructions were present in the material reviewed.

Nothing merges without Justynn's word. On the fix landing, I will spot-verify the
two workflow files on both branches; the rest of this review stands.
