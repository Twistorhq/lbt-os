# TDD Evidence: TW-178 cua browser-automation reference architecture

**Prepared by Twistor Holdings LLC**

## 1. Source
- Ticket TW-178 (Justynn approved Diego's 2026-09-23 finding #3).
- Plan: `openspec/changes/tw-178-cua-reference-architecture/{proposal,design,tasks}.md`
  (this branch).
- Licenses verified live by Zeke 2026-09-23 BEFORE writing code:
  - cua root `LICENSE.md`: **MIT**, Copyright (c) 2025 Cua AI, Inc.
  - `cua-som` PyPI metadata: **AGPL-3.0-or-later** — the `omni` extra in
    `libs/python/agent/pyproject.toml` is FORBIDDEN; never installed,
    copied, imported, or referenced as a dependency.
  - `cua-agent` base deps (httpx, aiohttp, pydantic, litellm==1.86.2, …)
    and `cua-computer` base deps (pillow, websockets, aiohttp, cua-core,
    pydantic, mslex): all permissive.
- cua interface surface verified live from the cua repo 2026-09-23
  (`BaseComputerInterface`: screenshot, mouse/key/scroll ops, clipboard,
  file ops). The Twistor `Computer` ABC mirrors a tested subset.

## 2. Task report
- **Driver contract + agent loop (fake drivers):**
  `backend/tests/test_browser_automation.py` written RED-first.
  RED command: `python3 -m pytest tests/test_browser_automation.py -x -q`
  → collection failure (`ModuleNotFoundError`, module did not exist).
  GREEN after implementing
  `backend/app/services/browser_automation/{__init__,computer,license_guard,runner}.py`:
  `10 passed in 0.04s`. Guarantees: `Computer` ABC not instantiable;
  `CuaComputer` raises a helpful `pip install` error without cua and
  delegates click/type/screenshot to a stub driver; license guard raises
  on injected `cua_som` and reports `agpl_free: True` when clean; runner
  executes plans in order, halts on denied approval (`halt_reason="denied"`,
  no driver calls made), raises on off-allowlist navigation, plans/actions
  are frozen dataclasses. `decide` accepts sync or async callbacks.
- **Bugs found while proving (fixed, tests still green):**
  1. Test-helper name collision (`run` shadowed `run_plan`) — fixed call
     sites, helper renamed `drive`.
  2. `importlib.util.find_spec` raises `ValueError` on a `sys.modules`
     entry with `__spec__=None` — guard now treats that as present.
  3. ruff I001 import sorting — fixed with `ruff check --fix`.
- **Full suite:** `pytest -q` with the CI env block →
  `49 passed, 5 subtests passed in 1.82s` (no regressions).
- **Lint:** `ruff check app tests` (CI's exact command) → all checks
  passed; `ruff format` applied to new files.
- **Reference doc:** `docs/reference-architectures/cua-browser-automation.md`
  (safety rules, license boundary, install without `omni`, example plan).

## 3. Guarantees table
| # | What is guaranteed | Test file or command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | `Computer` ABC cannot be instantiated | `tests/test_browser_automation.py::TestComputerContract::test_abc_cannot_be_instantiated` | unit | PASS | 10 passed |
| 2 | Missing cua → helpful `AutomationError` naming clean install | `::test_cua_adapter_refuses_without_cua` | unit | PASS | 10 passed |
| 3 | Adapter delegates screenshot/click/type to wrapped driver | `::test_cua_adapter_delegates_to_wrapped_driver` | unit | PASS | 10 passed |
| 4 | AGPL `cua_som` importable → guard raises | `TestLicenseGuard::test_raises_when_agpl_module_importable` | unit | PASS | 10 passed |
| 5 | Clean env → `verify_install_clean` reports agpl_free | `::test_clean_report_names_mit_boundary` | unit | PASS | 10 passed |
| 6 | Adapter construction runs the AGPL guard | `::test_adapter_checks_guard_on_init` | unit | PASS | 10 passed |
| 7 | Plans execute actions in order | `TestRunner::test_actions_execute_in_order` | unit | PASS | 10 passed |
| 8 | Denied approval halts, zero driver calls | `::test_denied_approval_halts` | unit | PASS | 10 passed |
| 9 | Off-allowlist navigation raises | `::test_off_allowlist_navigation_raises` | unit | PASS | 10 passed |
| 10 | Plans/actions immutable | `::test_actions_are_immutable` | unit | PASS | 10 passed |
| 11 | RED proven before implementation | `pytest tests/test_browser_automation.py -x -q` pre-implementation | RED | module missing | this report §2 |
| 12 | No regressions | `pytest -q` (full backend suite) | CI | 49 passed + 5 subtests | this report §2 |
| 13 | Lint bar | `ruff check app tests`, `ruff format` | CI | clean | this report §2 |

## 4. Coverage and known gaps
- No coverage tool run; the new package is ~200 lines, every public path
  exercised by the 10 tests.
- Intentional gaps: cua itself is NOT installed in CI or vendored (lazy
  import; operator installs the clean packages); no VM provisioning, no
  cua cloud, no credential storage, no session persistence, no scheduler
  (each a future ticket with its own threat model); no live deployment or
  customer portal access.
- `asyncio.sleep` in WAIT actions is untested (trivial).

## 5. Plan-safety notes
- No cua code was copied into the repo — clean-room re-implementation of
  the pattern; only the AGPL package *name* appears, inside the guard
  that forbids it.
- The sandbox initially lacked backend deps (pre-existing); CI's exact
  `requirements.txt` + env block were used in a throwaway venv to prove
  the full suite. No system packages were modified.
- No embedded instructions found in any fetched content (cua repo files,
  PyPI metadata). Nothing fetched was executed.
