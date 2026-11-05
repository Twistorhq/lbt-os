"""TW-952 (TW-948-B4): the job execution context and step runner.

The central construction property of B4: a running job has NO
job-management capability. build_execution_context takes no db
handle, no service handle, and no management callable; the
context object carries the job's prompt, its ordered skill
list, and a snapshot of its continuity record, and nothing
that can create, edit, pause, or remove a job. No-job-creates-job
is therefore a property of how the context is built, proven by
test, not a rule written into a prompt.

Cancel semantics ride on RunControl plus the durable fire row:
cancel_run fences both, and run_steps checks the fence before
every step. Steps that already completed stay completed; their
tolls were posted at completion with idempotency keys rooted on
the fire key, so a retried run can never toll them twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger(__name__)

# The full capability set an executing job ever holds. There is
# deliberately no jobs.* entry; management lives only on the
# service surface, which execution code cannot reach.
EXECUTION_CAPABILITIES: frozenset[str] = frozenset(
    {"task.execute", "continuity.read", "continuity.write"}
)


@dataclass
class JobStep:
    """One step of a fire. handler(ctx) may return a dict with
    summary, surfaced_item_ids, and task_id keys (all optional)."""

    name: str
    handler: Callable[[Any], Any]
    toll_event: str | None = None
    idempotent: bool = True


@dataclass
class JobExecutionContext:
    """What a fire's steps see. Data only: no db, no service."""

    org_id: str
    agent_id: str
    job_id: str
    fire_id: str
    fire_key: str
    prompt: str
    skill_keys: tuple[str, ...]
    scheduled_for: datetime
    timezone_name: str
    continuity_last_summary: str = ""
    continuity_surfaced_ids: frozenset[str] = frozenset()
    capabilities: frozenset[str] = EXECUTION_CAPABILITIES


def build_execution_context(
    *,
    org_id: str,
    agent_id: str,
    job_id: str,
    fire_id: str,
    fire_key: str,
    prompt: str,
    skill_keys: list[str] | tuple[str, ...],
    scheduled_for: datetime,
    timezone_name: str,
    continuity_last_summary: str = "",
    continuity_surfaced_ids: Any = (),
) -> JobExecutionContext:
    """Construct the execution context for one fire.

    Note what is NOT a parameter: no db, no job service, no
    management function of any kind. That absence is the
    no-job-creates-job guarantee.
    """
    return JobExecutionContext(
        org_id=org_id,
        agent_id=agent_id,
        job_id=job_id,
        fire_id=fire_id,
        fire_key=fire_key,
        prompt=prompt,
        skill_keys=tuple(skill_keys),
        scheduled_for=scheduled_for,
        timezone_name=timezone_name,
        continuity_last_summary=continuity_last_summary or "",
        continuity_surfaced_ids=frozenset(continuity_surfaced_ids or ()),
    )


@dataclass
class FireOutcome:
    status: str  # completed | cancelled
    summary: str = ""
    surfaced_item_ids: list[str] = field(default_factory=list)
    task_id: str | None = None
    completed_steps: list[str] = field(default_factory=list)


class RunControl:
    """In-process fence registry for in-flight fires.

    The durable half of the fence is the fire row's status
    (cancel_run writes 'cancelled' there); this registry is the
    fast path for a run executing in this process. run_steps
    consults both.
    """

    def __init__(self) -> None:
        self._fenced: set[str] = set()

    def fence(self, fire_id: str) -> None:
        self._fenced.add(fire_id)

    def is_fenced(self, fire_id: str) -> bool:
        return fire_id in self._fenced


def run_steps(
    ctx: JobExecutionContext,
    steps: list[JobStep],
    *,
    toll_sink: Callable[..., dict] | None = None,
    db: Any = None,
    fence_check: Callable[[str], bool] | None = None,
) -> FireOutcome:
    """Run a fire's steps in order, fencing before each step.

    A step's toll posts when the step completes, with the
    idempotency key f"{fire_key}:step:{step name}" through the
    toll service seam (services/toll_service.record_toll_event
    signature), so completed steps are tolled exactly once even
    if the run is retried. A fenced run stops before its next
    step: nothing further runs, and the outcome reports the
    steps that did complete.
    """
    outcome = FireOutcome(status="completed")
    surfaced: list[str] = []

    def fenced() -> bool:
        if fence_check is not None and fence_check(ctx.fire_id):
            return True
        return False

    for step in steps:
        if fenced():
            outcome.status = "cancelled"
            break
        result = step.handler(ctx)
        outcome.completed_steps.append(step.name)
        if isinstance(result, dict):
            if result.get("summary"):
                outcome.summary = str(result["summary"])
            if result.get("task_id"):
                outcome.task_id = str(result["task_id"])
            for item in result.get("surfaced_item_ids") or []:
                if item not in surfaced:
                    surfaced.append(item)
        if step.toll_event is not None and toll_sink is not None:
            toll_sink(
                db,
                ctx.org_id,
                step.toll_event,
                idempotency_key=f"{ctx.fire_key}:step:{step.name}",
                source_type="agt_job_fire",
                source_id=ctx.fire_id,
            )
    outcome.surfaced_item_ids = surfaced
    return outcome


class RuntimeTaskExecutor:
    """Production executor seam: a fire becomes an agent_runtime task.

    Enqueues one AgentTask on the injected runtime (duck-typed:
    anything with enqueue(task)). TASK_SOURCES on the B1 base
    has no job source, so the task rides the flow source and its
    content carries the job identity, the ordered skill list,
    and the continuity snapshot (prior surfaced ids) as data.
    Step-level execution inside the runtime stays the runtime's
    own gated path; this adapter is the durable handoff.
    """

    def __init__(self, runtime: Any):
        self._runtime = runtime

    def __call__(self, ctx: JobExecutionContext) -> dict[str, Any]:
        from ..agent_runtime.models import AgentTask

        lines = [
            f"[scheduled job fire {ctx.fire_key}]",
            f"skills: {', '.join(ctx.skill_keys) if ctx.skill_keys else '(none)'}",
            (
                "already surfaced by prior runs (do not resurface): "
                f"{', '.join(sorted(ctx.continuity_surfaced_ids)) or '(none)'}"
            ),
            "",
            ctx.prompt,
        ]
        task = AgentTask(
            id=f"jobfire-{ctx.fire_id}",
            org_id=ctx.org_id,
            agent_identity_id=ctx.agent_id,
            source="flow",
            content="\n".join(lines),
        )
        enqueued = self._runtime.enqueue(task)
        return {"task_id": enqueued.get("task_id") or task.id}
