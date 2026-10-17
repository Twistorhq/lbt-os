# TW-567 harvester — TDD evidence

**Prepared by Twistor Holdings LLC**

## 1. Source

- Ticket TW-567 (brief's TW-566; ticket system assigned TW-567 — noted on the ticket)
- Plan: `openspec/changes/tw-567-harvester/proposal.md`, tasks in `tasks.md`
- Branch: `zeke/tw-567-harvester` @ worktree `~/workspace/worktrees/zeke-tw567-harvest`
- openspec CLI not installed in this environment; proposal/tasks written in the
  repo's existing format manually (no `openspec validate` run — recorded gap).

## 2. Task report

TDD checkpoints committed per TW-134:
- `caa561b` RED — 38 tests written first, all failing (ModuleNotFoundError).
- GREEN in three commits: politeness/models/extract/config (25 tests),
  store (8 tests), adapters/CLI (38 total). One test bug fixed mid-GREEN
  (OAI deleted-record count corrected 2 -> 1); one implementation bug fixed
  (license regex captured `by` instead of `cc-by`).

Validation commands actually run (fresh, this session):
- `python -m pytest research/harvester/tests -q` -> **38 passed**
- `ruff check --config backend/ruff.toml research/harvester` -> **All checks passed**
  (repo CI bar is E4/E7/E9/F/I; the broader BLE/S findings are tracked as
  hardening follow-ups per backend/ruff.toml, not CI gates)

Live seed run (not a test — real network):
- `python -m research.harvester.cli --db <corpus> run --query "hemp fiber composite"
  --sources crossref,openaire,doaj --max-records 60`
  -> 180 found, 177 stored (3 DOI-deduped across sources), 62 heuristic
  property records (tensile/flexural/impact/density/fiber_fraction).
- Corpus DB (outside repo): `~/workspace/twistor-lab/data-layer/corpus/research_corpus.db`
- OAI-PMH live run against K-State K-REx: executed; result recorded in ticket.

## 3. Guarantees table

| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Rate limiter blocks immediate re-hit per host, allows after interval, isolates hosts, is thread-safe | test_politeness.py::TestRateLimiter (6 tests) | unit | PASS | 38 passed run |
| 2 | robots.txt honored; unreachable robots.txt = fetch NOT allowed (fail-closed) | test_politeness.py::TestRobots + local HTTP fixture server | unit | PASS | 38 passed run |
| 3 | Author/year normalization across Crossref-dict, string, date-parts, garbage inputs | test_extract.py (11 tests) | unit | PASS | 38 passed run |
| 4 | Heuristic property extraction finds tensile/flexural/impact/elongation; GPa normalized to MPa; every hit labeled regex-heuristic with confidence in (0,1] | test_extract.py::TestExtractProperties (8 tests) | unit | PASS | 38 passed run |
| 5 | Store dedupes on external_id and on DOI; NULL DOIs never collapse; run logging works; schema init idempotent | test_store.py (8 tests) | unit | PASS | 38 passed run |
| 6 | Crossref fixture normalizes to expected title/authors/year/doi/license/source_url; missing fields don't crash | test_adapters.py::TestCrossrefAdapter | unit | PASS | 38 passed run |
| 7 | OAI-PMH fixture parses 1 live record (deleted skipped), dissertation typed, resumption token captured | test_adapters.py::TestOaiPmhAdapter | unit | PASS | 38 passed run |
| 8 | OpenAlex 429 degrades to ([], note) instead of raising | test_adapters.py::TestOpenAlexDegrade | unit | PASS | 38 passed run |
| 9 | Repo CI lint bar clean on new code | ruff check --config backend/ruff.toml | CI | PASS | All checks passed |
| 10 | Paywall candidates detected (closed + valuable), deduped in hit-list | test_paywall.py (7 tests) | unit | PASS | 48 passed run |
| 11 | OAI-PMH AND-semantics term filter admits only distinctive-term matches | test_adapters.py::TestOaiMatchTerms (3 tests) | unit | PASS | 48 passed run |

## 4. Coverage and known gaps

- No tests for live `discover()` network paths (by design — fixtures cover
  normalization/parsing; live runs are manual verification, see §2).
- `fetch_fulltext` PDF path untested (needs robots.txt + binary fixtures;
  follow-up: add a local-server PDF fixture test).
- Heuristic extraction is intentionally lossy; confidence 0.6 uniform.
  Improving precision/recall is a follow-up with a labeled evaluation set.
- OpenAlex adapter untestable live from sandbox (429 shared-budget); the
  degrade path is unit-tested.
- `openspec validate` not run (CLI absent) — proposal format mirrors the
  repo's existing changes by hand.
- OAI-PMH `discover` does client-side term filtering; very large sets page
  up to 50 batches — fine for seed scale, needs set-scoping for production.
  (Tightened filter `["hemp", "composite"]` returned 0 hits across the three
  live DSpace repos in the paged window — honest thin result; the API sources
  carry the seed corpus. Set-scoped OAI harvesting is the follow-up.)
- Paywall hit-list seeded from the first corpus: 17 hits backfilled
  (crossref closed-access hemp papers). Access cost left NULL — resolved at
  buy-decision time on the publisher page, per the rule.

## 6. Standing-rule compliance (added 2026-10-16)

- PAYWALL RULE: `paywall_hits` table + `is_paywall_candidate()` in the run
  loop. Valuable closed sources are recorded with what/why, never silently
  skipped. Backfill run over the seed corpus produced 17 hits.
- OAI filter fix: first live OAI run admitted 15 off-topic records
  (OR-semantics on "fiber"); switched to AND-semantics with distinctive-term
  pinning (`match_terms: [hemp]` in sources.yaml), junk removed from corpus,
  re-harvest in progress at time of writing.

## 5. Plan-safety notes

- No embedded instructions found in any plan, fixture, or fetched content.
  Fixture data is synthetic/minimal and hand-written.
- `pypdf` 6.21.0 (MIT) installed into the shared `~/workspace/.venvs/lbt-os`
  venv for future PDF text extraction — FOSS, no license concern.
- Harvester User-Agent identifies Twistor with contact email; rate limits
  2-3s per host; no paywalled sources configured; FSU DigiNole left disabled
  (endpoint not OAI-PMH — recorded in sources.yaml, not silently dropped).
