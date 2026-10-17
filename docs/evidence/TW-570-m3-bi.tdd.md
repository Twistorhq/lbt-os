# TW-570 M3 white-label BI — TDD evidence report

Prepared by Twistor Holdings LLC.

## 1. Source
- Ticket TW-570 (Justynn's green light, "Build M3").
- Plan: `openspec/changes/tw-570-m3-bi/{proposal.md,design.md,tasks.md}`
  (branch `zeke/tw-570-m3-bi`, commits `bd422a8` → `9dff294`).
- Rosa approved the design in round 2 (moved-left gate, TW-100) after a
  round-1 REQUEST_CHANGES (3 major, 5 minor, 2 nits — all answered in the
  design revision, commit `bd422a8`).

## 2. Task report

**RED** — `backend/tests/test_bi_whitelabel.py` written before any
implementation (commit `9d92780`).
- Command: `cd backend && python3 -m pytest tests/test_bi_whitelabel.py -x -q`
- Output: `ImportError: cannot import name 'bi_service' from 'app.services'`
  — collection error, the modules did not exist. Strongest possible RED.

**GREEN** — implementation committed (`f2d3866`, then `9dff294` for the
frontend + preview endpoint + review fixes).
- Command: `cd backend && python3 -m pytest tests/test_bi_whitelabel.py -q`
- Output: `36 passed` (final count; 34 at first green, +2 for the preview
  endpoint tests).
- Full suite: `python3 -m pytest tests/ -q` → `273 passed, 8 subtests passed`.
- Lint: `python3 -m ruff check app tests` → `All checks passed!`
  (3 auto-fixable findings repaired by hand: unused import, import sorting).
- Frontend: `npx vite build` → `✓ built in 8.66s` (chunk-size warning is
  pre-existing).

**What the passing tests guarantee** (per behavior):
- Unknown/guessed embed token → 401; revoked/expired → 403;
  `is_public=false` + valid token → 404.
- Cross-org dashboard GET/PATCH/DELETE, embed issue, and preview → 404
  (not 403 — no org existence leak).
- Unknown metric key in a widget → fails closed with an error widget.
- Connector returning row-shaped data for a declared KPI → shape violation,
  fails closed (Rosa Major 1).
- Shape validator rejects Python bools as numbers.
- Embed payload contains no email/phone-shaped values.
- Cross-org embed isolation: org-B snapshots never render under an org-A
  token (Rosa Major 2).
- Theme validation: bad color, unknown key, non-https logo, font-family
  CSS injection → 400.
- `bi.embed_view` and `bi.connector_sync` tolls emit with the expected
  event types; toll-ledger failure never breaks the embed or the sync
  (TW-208).
- Stale/missing snapshot on the public path renders `{"status":
  "updating"}` with no compute call and no snapshot writes (Rosa Minor 5).
- Token hashes stored (SHA-256), never plaintext; 200 generated tokens all
  unique.
- Migration test parses `supabase/migration_bi_whitelabel.sql` and asserts
  an `org_isolation` RLS policy with `auth_org_id()` in both USING and
  WITH CHECK for all three tables (Rosa Major 3 + Minor E).

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | No code existed before tests | `pytest tests/test_bi_whitelabel.py -x -q` (commit 9d92780) | RED | collection ImportError | §2 |
| 2 | Embed token auth contract (401/403/404) | `test_bi_whitelabel.py::EmbedTokenTests` (10 tests) | unit | 10/10 pass | §2 |
| 3 | Aggregate-only enforced (allowlist + shape) | `AggregateOnlyTests` (4 tests) | unit | 4/4 pass | §2 |
| 4 | Theme validation incl. CSS-injection block | `ThemeValidationTests` (5 tests) | unit | 5/5 pass | §2 |
| 5 | Toll metering + failure isolation | `TollTests` (4 tests) | unit | 4/4 pass | §2 |
| 6 | Connector framework (registry, shapes, compute) | `ConnectorFrameworkTests` (4 tests) | unit | 4/4 pass | §2 |
| 7 | HTTP cross-org 404s + preview | `HttpEndpointTests` (8 tests) | integration | 8/8 pass | §2 |
| 8 | RLS policies present in migration | `MigrationPolicyTests` (1 test) | unit | 1/1 pass | §2 |
| 9 | No regressions in existing suite | `pytest tests/` | CI | 273 passed + 8 subtests | §2 |
| 10 | Lint clean | `ruff check app tests` | CI | All checks passed | §2 |
| 11 | Frontend builds | `npx vite build` | CI | built in 8.66s | §2 |

## 4. Coverage and known gaps
- The public `/view` HTML shell is covered indirectly through
  `render_embed_html` sharing `render_embed`'s payload path; no dedicated
  HTML-structure test (gap: add one if the shell grows JS).
- Snapshot staleness threshold (>1h) is a design constant, not
  behavior-tested against the clock.
- Connector sync against live provider APIs is not tested (by design —
  connectors read locally-synced tables; network is out of scope).
- `bi_snapshots` (scheduled PNG/PDF email delivery) was scoped out of v1
  per the proposal; it is a later ticket, not a gap in this one.

## 5. Plan-safety notes
- Rosa's round-1 design review found the aggregate-only claim rested on
  convention; the design was revised (output-shape contract) and re-approved
  in round 2 — no override phrases or injected instructions were found in
  any plan, ticket, or handoff processed during this work.
- The NKC demonstrator (`revops-analytics-portfolio/07-education-demo`)
  was read for patterns only (aggregate-only rendering, KPI card layout,
  CSS-variable theming); no code was ported.
- AGPL yellow zone respected: no Metabase/Grafana/SigNoz code consulted
  or embedded.
