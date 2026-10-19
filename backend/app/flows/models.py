"""TW-625 Twistor Flows — workflow definition model + validation.

Definitions are plain JSON so the visual builder (Sofia's lane), the MCP
surface, and the Zapier importer all speak one language.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


class DefinitionError(ValueError):
    """A workflow definition that cannot run. Fails loud, never silent."""


TRIGGER_TYPES = ("manual", "webhook", "schedule")
STEP_TYPES = ("notify", "delay", "branch", "webhook", "set", "loop")

# V1 cron: 5 fields, each '*' or a plain integer. Ranges, lists, and step
# values are rejected at definition time (loud) rather than silently
# never firing at tick time.
_CRON_FIELD_RES = {
    0: (0, 59),  # minute
    1: (0, 23),  # hour
    2: (1, 31),  # day of month
    3: (1, 12),  # month
    4: (0, 7),  # day of week (0 and 7 = Sunday)
}


def validate_cron(cron: str) -> None:
    """Validate a V1 cron expression. Raises DefinitionError."""
    fields = (cron or "").strip().split()
    if len(fields) != 5:
        raise DefinitionError(
            f"bad cron {cron!r}: need 5 fields (minute hour dom month dow)"
        )
    for i, field in enumerate(fields):
        if field == "*":
            continue
        lo, hi = _CRON_FIELD_RES[i]
        try:
            n = int(field)
        except ValueError:
            raise DefinitionError(
                f"bad cron {cron!r}: field {i + 1} ({field!r}) must be "
                f"'*' or an integer {lo}-{hi} in V1"
            ) from None
        if not lo <= n <= hi:
            raise DefinitionError(
                f"bad cron {cron!r}: field {i + 1} out of range {lo}-{hi}"
            )


class Trigger(BaseModel):
    type: str
    key: str | None = None  # webhook: the secret in the path
    cron: str | None = None  # schedule: standard 5-field cron


class Budget(BaseModel):
    max_steps_per_run: int = 100
    max_runs_per_day: int = 500
    max_cost_per_day_cents: int = 1000


class Step(BaseModel):
    id: str
    type: str
    # notify
    channel: str | None = None
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    template: str | None = None
    variables: dict = Field(default_factory=dict)
    # delay
    duration: str | None = None  # "30s" | "5m" | "3h" | "3d"
    # branch
    condition: str | None = None
    then: list["Step"] = Field(default_factory=list)
    else_: list["Step"] = Field(default_factory=list, alias="else")
    # webhook (outbound)
    method: str | None = None
    url: str | None = None
    headers: dict = Field(default_factory=dict)
    # set
    var: str | None = None
    value: object = None
    # loop
    over: str | None = None
    as_: str | None = Field(default=None, alias="as")
    max_iterations: int = 500
    steps: list["Step"] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


Step.model_rebuild()


class WorkflowDefinition(BaseModel):
    name: str
    trigger: Trigger
    steps: list[Step]
    budget: Budget = Field(default_factory=Budget)


def _nested_delays(steps: list) -> list[str]:
    """Find delay step ids nested inside branch/loop arms.

    V1 resume tracks a single top-level step id, so a delay buried in a
    branch arm could suspend but never resume. Reject loudly at
    definition time (documented); path-based resume is a follow-up.
    """
    found: list[str] = []
    for s in steps or []:
        if s.get("type") == "delay":
            found.append(s.get("id") or "?")
        found += _nested_delays(s.get("then") or [])
        found += _nested_delays(s.get("else") or [])
        found += _nested_delays(s.get("steps") or [])
    return found


def _check_step(step: dict, path: str, depth: int = 0) -> None:
    stype = step.get("type")
    if stype not in STEP_TYPES:
        raise DefinitionError(f"{path}: unknown step type {stype!r}")
    sid = step.get("id") or path
    if stype == "notify":
        if not step.get("channel") or not step.get("to"):
            raise DefinitionError(f"{sid}: notify needs channel + to")
    elif stype == "delay":
        if not step.get("duration"):
            raise DefinitionError(f"{sid}: delay needs duration")
        _parse_duration(step["duration"])  # validates format now
    elif stype == "branch":
        if not step.get("condition"):
            raise DefinitionError(f"{sid}: branch needs condition")
        if "then" not in step and "else" not in step:
            raise DefinitionError(f"{sid}: branch needs then and/or else")
        arms = (step.get("then") or []) + (step.get("else") or [])
        nested = _nested_delays(arms)
        if nested:
            raise DefinitionError(
                f"{sid}: delay steps {nested} nested inside branch arms "
                f"cannot resume in V1 — hoist delays to the top level"
            )
        for i, sub in enumerate(step.get("then") or []):
            _check_step(sub, f"{sid}.then[{i}]", depth)
        for i, sub in enumerate(step.get("else") or []):
            _check_step(sub, f"{sid}.else[{i}]", depth)
    elif stype == "webhook":
        if not step.get("url"):
            raise DefinitionError(f"{sid}: webhook needs url")
    elif stype == "set":
        if not step.get("var"):
            raise DefinitionError(f"{sid}: set needs var")
    elif stype == "loop":
        if depth >= 3:
            raise DefinitionError(f"{sid}: loop nesting too deep (max 3)")
        if not step.get("over") or not step.get("as"):
            raise DefinitionError(f"{sid}: loop needs over + as")
        if not step.get("steps"):
            raise DefinitionError(f"{sid}: loop needs steps")
        mi = step.get("max_iterations", 500)
        if not isinstance(mi, int) or mi < 1:
            raise DefinitionError(f"{sid}: max_iterations must be a positive integer")
        nested = _nested_delays(step.get("steps") or [])
        if nested:
            raise DefinitionError(
                f"{sid}: delay steps {nested} nested inside a loop "
                f"cannot resume in V1 — hoist delays to the top level"
            )
        for i, sub in enumerate(step["steps"]):
            _check_step(sub, f"{sid}.loop[{i}]", depth + 1)


def _parse_duration(raw: str) -> int:
    """'30s' | '5m' | '3h' | '3d' -> seconds. Raises DefinitionError."""
    m = re.fullmatch(r"(\d+)([smhd])", raw.strip())
    if not m:
        raise DefinitionError(f"bad duration {raw!r}: use 30s, 5m, 3h, 3d")
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    return int(m.group(1)) * mult


def parse_duration(raw: str) -> int:
    return _parse_duration(raw)


def validate_definition(raw: dict) -> WorkflowDefinition:
    """Validate raw JSON into a WorkflowDefinition. Raises DefinitionError."""
    if not isinstance(raw, dict):
        raise DefinitionError("definition must be a JSON object")
    if not raw.get("name"):
        raise DefinitionError("definition needs a name")
    trig = raw.get("trigger") or {}
    if trig.get("type") not in TRIGGER_TYPES:
        raise DefinitionError(f"unknown trigger type {trig.get('type')!r}")
    if trig["type"] == "webhook" and not trig.get("key"):
        raise DefinitionError("webhook trigger needs a key (the path secret)")
    if trig["type"] == "schedule":
        if not trig.get("cron"):
            raise DefinitionError("schedule trigger needs a cron expression")
        validate_cron(trig["cron"])
    steps = raw.get("steps") or []
    if not steps:
        raise DefinitionError("definition needs at least one step")
    seen: set[str] = set()

    def _ids(s: dict, path: str) -> None:
        sid = s.get("id")
        if not sid:
            raise DefinitionError(f"{path}: step needs an id")
        if sid in seen:
            raise DefinitionError(f"duplicate step id {sid!r}")
        seen.add(sid)
        for sub in (
            (s.get("then") or []) + (s.get("else") or []) + (s.get("steps") or [])
        ):
            _ids(sub, f"{sid}/sub")

    for i, s in enumerate(steps):
        _ids(s, f"steps[{i}]")
        _check_step(s, f"steps[{i}]")
    try:
        return WorkflowDefinition(**raw)
    except Exception as exc:  # pydantic shape errors -> our error type
        raise DefinitionError(f"definition shape invalid: {exc}") from exc
