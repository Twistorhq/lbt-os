"""TW-769 Spec 3 — agent task runtime models + the injection guard.

A task is work handed to one shop's agent from one of four sources:
its email inbox, the phone lane, a Flows trigger, or the owner
directly. The content of a task (notably customer email) is DATA the
agent reads, never instructions it obeys: scan_injections() flags
instruction-shaped content at intake, the flag lands in the audit
feed for the owner (Spec 6 renders it), and the runtime never turns
content into a tool call — steps are code, supplied by the caller.

Presence and attendant mode are first-class contracts here so
Spec 8 (TW-786) is wiring, not surgery: presence is at_desk / away /
unknown, attendant mode is on / off, and a mode switch arrives as a
one-word instruction from any owner channel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from ..agentperms.models import channel_of


class RuntimeModelError(ValueError):
    """A runtime statement that cannot be represented. Fails loud."""


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except RuntimeModelError:
            raise
        except ValidationError as e:
            raise RuntimeModelError(str(e)) from e


TASK_SOURCES: tuple[str, ...] = ("email", "phone", "flow", "owner")

TASK_STATUSES: tuple[str, ...] = (
    "queued",
    "awaiting_owner",
    "deferred",
    "running",
    "completed",
    "failed",
    "fenced",
    "toll_exceeded",
)

PRESENCES: tuple[str, ...] = ("at_desk", "away", "unknown")
ATTENDANT_MODES: tuple[str, ...] = ("on", "off")


class AgentTask(_StrictBase):
    """One unit of work for one shop's agent.

    toll_budget_cents is the per-task toll ceiling: the runtime stops
    the task BEFORE the step whose toll would spend past it. None
    means the caller set no ceiling; tolls are still recorded.
    quiet_window is ("HH:MM", "HH:MM") in the runtime clock's frame;
    agent-initiated work defers inside it, owner work does not.
    """

    id: str | None = None
    org_id: str
    agent_identity_id: str | None = None
    source: str
    content: str = ""
    toll_budget_cents: int | None = None
    quiet_window: tuple[str, str] | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if self.source not in TASK_SOURCES:
            raise RuntimeModelError(
                f"unknown task source {self.source!r}; want one of {TASK_SOURCES}"
            )
        if self.toll_budget_cents is not None and self.toll_budget_cents < 0:
            raise RuntimeModelError("toll_budget_cents must be >= 0")
        if self.quiet_window is not None:
            for part in self.quiet_window:
                if not re.match(r"^\d{2}:\d{2}$", part):
                    raise RuntimeModelError(f"quiet window bound {part!r} is not HH:MM")


@dataclass
class TaskStepDef:
    """One step of a task. Exactly one execution shape: a handler, or
    a tool name routed through the TW-800 gateway — never both, and
    never a raw side door around either gate.

    amount_cents is the amount DECLARED at build time. For money
    classes whose real amount is computed during execution, the
    handler must re-check with ctx.checkpoint(action_class,
    amount_cents=<executed>) before its effect (TW-785 / TR-014):
    the fence that matters is on the executed amount.
    """

    name: str
    handler: Callable[[Any], Any] | None = None
    idempotent: bool = True
    action_class: str | None = None
    amount_cents: int | None = None
    toll_cents: int = 0
    tool: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    credential_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.idempotent, bool):
            raise RuntimeModelError(
                f"step {self.name!r} must declare idempotent=True/False"
            )
        if self.handler is not None and self.tool is not None:
            raise RuntimeModelError(f"step {self.name!r}: handler or tool, never both")
        if self.handler is None and self.tool is None:
            raise RuntimeModelError(
                f"step {self.name!r}: a step needs a handler or a tool"
            )
        if self.action_class is not None:
            channel_of(self.action_class)  # LedgerError on unknown class
        if self.toll_cents < 0:
            raise RuntimeModelError("toll_cents must be >= 0")


# -- injection guard --------------------------------------------------------

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(r"ignore\b[^.\n]{0,40}\binstructions\b", re.I),
    ),
    ("disregard", re.compile(r"disregard\s+(the|your|all)\s", re.I)),
    ("new_instructions", re.compile(r"new\s+instructions\s*:", re.I)),
    ("system_impersonation", re.compile(r"^\s*system\s*:", re.I | re.M)),
    (
        "exfiltrate_send",
        re.compile(r"(send|forward|email)\b[^.\n]{0,60}\b(to|@)\b", re.I),
    ),
    ("reveal_prompt", re.compile(r"reveal\b[^.\n]{0,40}\bprompt", re.I)),
    (
        "override_rules",
        re.compile(r"override\s+(your|the)\s+(rules|instructions)", re.I),
    ),
)


def scan_injections(content: str) -> list[str]:
    """Names of instruction-shaped patterns found in agent-read
    content. The content itself is never returned, logged, or acted
    on — only the pattern names reach the audit feed."""
    return [name for name, pat in _INJECTION_PATTERNS if pat.search(content or "")]


# -- Spec 8 mode-switch intake ----------------------------------------------

_MODE_ON = {"on", "attendant on", "auto attendant on", "away", "im away"}
_MODE_OFF = {"off", "attendant off", "auto attendant off", "back", "at my desk"}


def parse_mode_switch(text: str) -> str | None:
    """A one-word (or short fixed-phrase) owner instruction from any
    channel -> 'on' / 'off', or None when the text is not a switch.
    Anything that is not unambiguously a switch changes nothing."""
    normalized = re.sub(r"[^a-z ]", "", (text or "").lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in _MODE_ON:
        return "on"
    if normalized in _MODE_OFF:
        return "off"
    return None
