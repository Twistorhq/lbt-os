# TW-178: cua browser-automation reference architecture (Zeke)

**Prepared by Twistor Holdings LLC**

## License boundary (verified live 2026-09-23)
- cua root `LICENSE.md`: MIT, Copyright (c) 2025 Cua AI, Inc. Clean.
- `cua-agent` base deps (httpx, aiohttp, pydantic, litellm==1.86.2, …):
  all permissive. Clean.
- `cua-computer` base deps (pillow, websockets, aiohttp, cua-core,
  pydantic, mslex): all permissive. Clean.
- **FORBIDDEN:** the `omni` extra in `libs/python/agent/pyproject.toml`
  pulls `cua-som` (PyPI: `AGPL-3.0-or-later`). Never install, copy, import,
  depend on, or reference it. A `license_guard` module enforces this at
  runtime and fails loud if `cua_som` is ever importable.

## What we adopt
Only the architectural pattern, re-implemented clean-room as Twistor code:
typed action plans, explicit URL allowlists, human approval checkpoints for
consequential actions, driver/agent separation (the `Computer` ABC is the
driver contract; the runner is the agent loop), and a thin `CuaComputer`
adapter that delegates to the real `cua.computer.Computer` when the
operator installs `cua-agent`/`cua-computer` themselves. No live
deployment, no customer portal access, no CUA cloud dependency, no secrets.

## Shape
- `backend/app/services/browser_automation/computer.py` — `Computer` ABC
  (screenshot/click/type/press_key/scroll/…) + `CuaComputer` adapter
  (lazy import, helpful error when cua is absent).
- `backend/app/services/browser_automation/license_guard.py` —
  `assert_no_agpl()` / `verify_install_clean()`; raises if `cua_som` is
  importable or installed.
- `backend/app/services/browser_automation/runner.py` — `BrowserAction`
  (typed, with `requires_approval`), `AutomationPlan` (with
  `allowed_hosts`), `run_plan()` executing actions in order, halting on
  denied approvals or off-allowlist navigation.
- `backend/tests/test_browser_automation.py` — RED-first unit tests with a
  `FakeComputer` (no real browser, no cua installed in CI).
- `docs/reference-architectures/cua-browser-automation.md` — the pattern
  doc: when to use it (portal + spreadsheet workflows), how the pieces
  map to cua's modules, install instructions with the `omni` warning.
- `docs/evidence/TW-178-cua-reference-architecture.tdd.md` — TDD evidence.

## Why Twistor Trades
Portal logins and spreadsheet exports are the two most common "money
walking out the door" workflows in HVAC shops (supplier portals, QuickBooks
exports). A typed, approval-gated automation layer is the safe way to offer
them as a retainer service later. Dual-lens: main product (Trades) +
portable pattern.
