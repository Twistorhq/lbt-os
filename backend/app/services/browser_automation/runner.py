"""Typed, approval-gated agent loop over a Computer driver (TW-178).

The agent loop never touches a screen directly: it executes a frozen
``AutomationPlan`` of typed ``BrowserAction``s against the driver
contract in ``computer.py``. Consequential actions carry
``requires_approval=True`` and halt unless the human's ``decide``
callback approves. Navigation is allowlisted. Fail loud everywhere.

**Prepared by Twistor Holdings LLC**
"""

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Tuple
from urllib.parse import urlparse

from app.services.browser_automation.computer import AutomationError, Computer


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    PRESS_KEY = "press_key"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    WAIT = "wait"


@dataclass(frozen=True)
class BrowserAction:
    action: ActionType
    description: str
    url: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    key: Optional[str] = None
    keys: Tuple[str, ...] = ()
    clicks: int = 1
    seconds: float = 0.0
    requires_approval: bool = False


@dataclass(frozen=True)
class AutomationPlan:
    name: str
    actions: Tuple[BrowserAction, ...] = field(default_factory=tuple)
    allowed_hosts: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class AutomationResult:
    completed: bool
    steps_completed: int
    halt_reason: Optional[str] = None
    screenshots: int = 0


DecideCallback = Callable[[BrowserAction], Awaitable[bool]]


async def _deny_everything(action: BrowserAction) -> bool:
    return False


def _host_allowed(hostname: Optional[str], allowed: Tuple[str, ...]) -> bool:
    if not hostname:
        return False
    host = hostname.lower()
    return any(
        host == allowed_host.lower() or host.endswith("." + allowed_host.lower())
        for allowed_host in allowed
    )


async def run_plan(
    plan: AutomationPlan,
    computer: Computer,
    decide: DecideCallback = _deny_everything,
) -> AutomationResult:
    """Execute a plan's actions in order against the driver.

    Approval-required actions call ``decide``; a denial halts the run.
    NAVIGATE validates the URL host against ``plan.allowed_hosts``.
    """
    result = AutomationResult(completed=False, steps_completed=0)
    for action in plan.actions:
        if action.requires_approval:
            approval = decide(action)
            if inspect.isawaitable(approval):
                approval = await approval
            if not approval:
                result.halt_reason = "denied"
                return result
        await _execute(action, plan, computer, result)
        result.steps_completed += 1
    result.completed = True
    return result


async def _execute(
    action: BrowserAction,
    plan: AutomationPlan,
    computer: Computer,
    result: AutomationResult,
) -> None:
    kind = action.action
    if kind is ActionType.NAVIGATE:
        if not action.url:
            raise AutomationError("NAVIGATE requires a url")
        hostname = urlparse(action.url).hostname
        if not _host_allowed(hostname, plan.allowed_hosts):
            raise AutomationError(
                f"Navigation to {action.url!r} blocked: host not on the "
                f"plan allowlist {list(plan.allowed_hosts)}"
            )
        await computer.screenshot()  # evidence after navigation
        result.screenshots += 1
    elif kind is ActionType.CLICK:
        await computer.click(_need(action.x, "x"), _need(action.y, "y"))
    elif kind is ActionType.DOUBLE_CLICK:
        await computer.double_click(_need(action.x, "x"), _need(action.y, "y"))
    elif kind is ActionType.RIGHT_CLICK:
        await computer.right_click(_need(action.x, "x"), _need(action.y, "y"))
    elif kind is ActionType.TYPE:
        await computer.type_text(_need(action.text, "text"))
    elif kind is ActionType.PRESS_KEY:
        await computer.press_key(_need(action.key, "key"))
    elif kind is ActionType.HOTKEY:
        await computer.hotkey(*action.keys)
    elif kind is ActionType.SCROLL:
        await computer.scroll(_need(action.x, "x"), _need(action.y, "y"), action.clicks)
    elif kind is ActionType.SCREENSHOT:
        await computer.screenshot()
        result.screenshots += 1
    elif kind is ActionType.WAIT:
        import asyncio as _asyncio

        await _asyncio.sleep(action.seconds)
    else:  # pragma: no cover - defensive
        raise AutomationError(f"Unknown action type: {kind!r}")


def _need(value: Optional[Any], name: str) -> Any:
    if value is None:
        raise AutomationError(f"Action requires {name!r} but it was not set")
    return value
