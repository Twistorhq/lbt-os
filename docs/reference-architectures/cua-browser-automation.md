# Reference architecture: cua-style browser automation (TW-178)

**Prepared by Twistor Holdings LLC**

Clean-room reference layer for typed, approval-gated browser automation,
patterned on [cua](https://github.com/trycua/cua) (MIT, Copyright (c)
2025 Cua AI, Inc. — license re-verified live 2026-09-23). Twistor wrote
every line here; cua is an optional operator-installed driver, never a
vendored or required dependency.

## When to use this
Portal + spreadsheet workflows — supplier portals, QuickBooks exports —
the two most common "money walking out the door" automations in HVAC
shops. Offered later as a retainer service; this ticket builds only the
safe pattern, no live deployment.

## The pieces
| Twistor module | Role | cua analogue |
|---|---|---|
| `computer.py` → `Computer` ABC | Driver contract: screenshot, click, type, press_key, scroll, … | `BaseComputerInterface` (subset, same shapes) |
| `computer.py` → `CuaComputer` | Thin adapter delegating to a real cua driver | `cua.computer.Computer` |
| `license_guard.py` | Fails loud if the AGPL `cua-som` package is importable/installed | — (Twistor's own boundary) |
| `runner.py` → `BrowserAction` / `AutomationPlan` | Typed, frozen action plans | cua's agent loop inputs |
| `runner.py` → `run_plan()` | Agent loop: ordered execution, approval halts, allowlist navigation | cua's `Agent` run loop |

## Safety rules (non-negotiable)
1. **Approval checkpoints.** Any consequential action sets
   `requires_approval=True`; the human's `decide` callback must approve
   or the run halts. The default `decide` denies everything.
2. **Navigation allowlist.** `AutomationPlan.allowed_hosts` is the only
   set of hosts a plan may touch. Empty = no navigation at all.
3. **Driver/agent separation.** The agent loop never touches a screen
   directly — it only calls the `Computer` contract. Swap drivers
   (cua, Playwright-backed, fake) without touching the loop.
4. **Frozen plans.** `BrowserAction`/`AutomationPlan` are immutable
   dataclasses: a plan is evidence, not a mutable script.
5. **Fail loud.** No silent skips, no retries in the loop, no swallowed
   errors. Timeouts belong to the caller (`asyncio.wait_for`).

## License boundary
- cua root: **MIT**. `cua-agent` / `cua-computer` base deps: all
  permissive (verified live 2026-09-23).
- **NEVER the `omni` extra.** It pulls `cua-som` (AGPL-3.0-or-later).
  `license_guard.assert_no_agpl()` runs on every `CuaComputer`
  construction and raises `AutomationError` naming the offender.
- Install (human, per machine):
  `pip install cua-agent` — plain, no extras. Verify with
  `verify_install_clean()` before first use.

## Example: portal export plan
```python
from app.services.browser_automation.runner import (
    ActionType, AutomationPlan, BrowserAction, run_plan,
)

plan = AutomationPlan(
    name="supplier-portal-export",
    allowed_hosts=("portal.supplier.example",),
    actions=(
        BrowserAction(ActionType.NAVIGATE,
                      url="https://portal.supplier.example/login",
                      description="open supplier portal"),
        BrowserAction(ActionType.TYPE, text="orders",
                      description="search orders"),
        BrowserAction(ActionType.PRESS_KEY, key="Enter",
                      description="submit export", requires_approval=True),
        BrowserAction(ActionType.SCREENSHOT,
                      description="evidence of export"),
    ),
)
```

## Deliberately out of scope
VM provisioning, cua cloud, credential storage, session persistence,
scheduling. Each gets its own ticket and threat model before it exists.
