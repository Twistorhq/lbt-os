"""TW-769 Spec 3 — the agent task runtime: a sandboxed per-shop
employee computer, built on the contracts that already exist:

- durable turns (TW-665): the journal IS the task's memory. A killed
  worker resumes from the last completed step; completed steps never
  re-run.
- TW-768 consent ledger: every consequential step is fenced by
  gate.guarded_step / LedgerFence, checked at EXECUTION time, never
  cached at intake. Revocation lands at step boundaries (TH-017);
  long handlers re-check at sub-step checkpoints via
  ctx.checkpoint() so the post-revocation window stays a small known
  bound, not a call's full duration (TR-015). Scheduler bindings
  consult the ledger at execution time for the same reason (TH-005):
  nothing in this runtime caches a permission decision.
- TW-800 tool gateway: a step that names a tool executes ONLY
  through gateway.invoke — policy, its own ledger re-check, and its
  hash-chained audit. There is no other tool path in this module.
- M1 toll ledger (TW-550): each executed step's toll posts through
  record_toll_event (or an injected sink) under an idempotency key
  of (task, step), so a resume can never double-charge. A task's
  toll budget is checked BEFORE a step runs: the step that would
  overspend never executes.
- TW-785 money fence: where a step's amount is computed at execution
  (tax, parts, revised estimate), the handler re-checks the EXECUTED
  amount via ctx.checkpoint() before its effect; as a backstop, a
  handler result declaring executed_amount_cents is re-checked by
  the runtime and a violation fails the task on the audit feed.
- Spec 1 (TW-767): persona inputs come from the identity record —
  one name, one email, one phone per agent (TW-766). Credentials
  resolve just-in-time for the one step that names a credential_ref,
  through the injected resolver; resolved values live only in that
  step's context and are never written to the audit feed, the task
  record, or any log in this module.

Sandbox: a runtime is bound to ONE org. Its store rows carry the
org, every read filters on it, and a task for another org is
refused at enqueue — two shops' runtimes may share a journal file
and still never see each other's work.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ..agentperms import gate
from ..agentperms import service as ledger
from ..durable_turns.engine import DurableTurnEngine, StepDef
from ..toolgateway import service as gateway
from .models import (
    ATTENDANT_MODES,
    PRESENCES,
    AgentTask,
    TaskStepDef,
    parse_mode_switch,
    scan_injections,
)


class TaskNotFound(LookupError):
    """No task with that id exists FOR THIS ORG."""


class OrgMismatch(ValueError):
    """A task (or identity) that does not belong to this runtime's org."""


class TollBudgetExceeded(Exception):
    """The next step's toll would spend past the task's budget."""

    _twistor_permanent = True


class ToolRefused(Exception):
    """The gateway refused or parked a tool step. Carries the outcome."""

    _twistor_permanent = True

    def __init__(self, status: str, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(f"tool refused: {status}: {reason}")


class MoneyFenceViolation(Exception):
    """A result declared an executed amount the ledger does not allow."""

    _twistor_permanent = True


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tasks (
  task_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  agent_identity_id TEXT,
  source TEXT NOT NULL,
  content TEXT NOT NULL,
  toll_budget_cents INTEGER,
  quiet_start TEXT,
  quiet_end TEXT,
  flagged INTEGER NOT NULL DEFAULT 0,
  flags_json TEXT NOT NULL DEFAULT '[]',
  owner_approved INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued',
  results_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  org_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_tolls (
  task_id TEXT NOT NULL,
  step_name TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  PRIMARY KEY (task_id, step_name)
);
CREATE TABLE IF NOT EXISTS agent_state (
  org_id TEXT PRIMARY KEY,
  presence TEXT NOT NULL DEFAULT 'unknown',
  attendant_mode TEXT NOT NULL DEFAULT 'off'
);
"""


class TaskContext:
    """What a step handler sees. task_content is DATA (possibly
    injection-flagged); credentials holds only this step's resolved
    credential, only when the step declared a credential_ref."""

    def __init__(
        self, runtime: "AgentTaskRuntime", task: dict[str, Any], step_name: str
    ):
        self._runtime = runtime
        self._task = task
        self.step_name = step_name
        self.task_id = task["task_id"]
        self.org_id = task["org_id"]
        self.task_content = task["content"]
        self.persona = runtime.prompt_inputs()
        self.credentials: dict[str, Any] = {}
        self._durable_ctx: Any = None

    def checkpoint(self, action_class: str, *, amount_cents: int | None = None) -> None:
        """Sub-step ledger re-check (TH-017 / TR-015 / TW-785).

        Call inside a long handler — above all immediately before an
        effect whose amount was computed at execution time — so a
        revocation or a cap is honored at the moment of the effect,
        not at the step's wrap time. Raises PermissionDenied."""
        self._runtime.fence.check(action_class, amount_cents=amount_cents)

    def apply_effect(self, effect_key: str, fn: Callable[[], Any]) -> Any:
        """Exactly-once side effect, delegated to the durable journal."""
        if self._durable_ctx is None:
            raise RuntimeError("apply_effect outside a running step")
        return self._durable_ctx.apply_effect(effect_key, fn)


class AgentTaskRuntime:
    def __init__(
        self,
        db: Any,
        *,
        org_id: str,
        journal_path: str,
        agent_identity_id: str | None = None,
        identity: Any = None,
        toll_sink: Callable[..., dict[str, Any]] | None = None,
        tool_executor: Callable[..., dict[str, Any]] | None = None,
        credential_resolver: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: float = 30.0,
    ):
        self._db = db
        self.org_id = org_id
        self.agent_identity_id = agent_identity_id
        self._identity = identity
        self._toll_sink = toll_sink
        self._tool_executor = tool_executor
        self._credential_resolver = credential_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.journal_path = journal_path
        self._engine = DurableTurnEngine(
            journal_path, lease_seconds=lease_seconds, base_delay=0.01
        )
        self.fence = gate.LedgerFence(db, org_id, agent_identity_id=agent_identity_id)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            c.execute(
                "INSERT OR IGNORE INTO agent_state (org_id) VALUES (?)", (org_id,)
            )

    # -- store plumbing ---------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.journal_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _audit(self, task_id: str | None, kind: str, payload: Any = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO agent_audit (task_id, org_id, kind, payload_json,"
                " created_at) VALUES (?,?,?,?,?)",
                (
                    task_id,
                    self.org_id,
                    kind,
                    json.dumps(payload or {}, default=str),
                    time.time(),
                ),
            )

    def list_audit(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """The per-action audit feed for THIS org (Spec 6 renders it)."""
        with self._conn() as c:
            if task_id is None:
                rows = c.execute(
                    "SELECT * FROM agent_audit WHERE org_id=? ORDER BY id",
                    (self.org_id,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM agent_audit WHERE org_id=? AND task_id=?"
                    " ORDER BY id",
                    (self.org_id, task_id),
                ).fetchall()
        return [
            {
                "id": r["id"],
                "task_id": r["task_id"],
                "kind": r["kind"],
                "payload": json.loads(r["payload_json"] or "{}"),
            }
            for r in rows
        ]

    # -- persona (Spec 1) ---------------------------------------------------

    def _identity_record(self) -> Any:
        if self._identity is not None:
            return self._identity
        if self.agent_identity_id:
            try:
                from ..agent_identity import service as identity_service

                return identity_service.get_agent(
                    self._db, self.org_id, self.agent_identity_id
                )
            except Exception:
                return None
        return None

    def prompt_inputs(self) -> dict[str, Any]:
        """Persona inputs assembled from the Spec 1 record: the same
        name, email, phone, and voice for every task this agent runs."""
        rec = self._identity_record()
        if rec is None:
            return {
                "agent_name": None,
                "email_address": None,
                "phone_number": None,
                "tone": None,
                "sign_off": None,
                "greeting": None,
                "voice_style": None,
            }
        get = (
            (lambda k: rec.get(k))
            if isinstance(rec, dict)
            else (lambda k: getattr(rec, k, None))
        )
        persona = get("persona")
        pget = (
            (lambda k: persona.get(k))
            if isinstance(persona, dict)
            else (lambda k: getattr(persona, k, None))
        )
        voice = pget("voice") if persona is not None else None
        vget = (
            (lambda k: voice.get(k))
            if isinstance(voice, dict)
            else (lambda k: getattr(voice, k, None))
        )
        return {
            "agent_name": get("agent_name"),
            "email_address": get("email_address"),
            "phone_number": get("phone_number"),
            "tone": pget("tone") if persona is not None else None,
            "sign_off": pget("sign_off") if persona is not None else None,
            "greeting": pget("greeting") if persona is not None else None,
            "voice_style": vget("style") if voice is not None else None,
        }

    # -- presence / attendant state (Spec 8 contracts) ----------------------

    def _state(self) -> dict[str, str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT presence, attendant_mode FROM agent_state WHERE org_id=?",
                (self.org_id,),
            ).fetchone()
        return dict(row) if row else {"presence": "unknown", "attendant_mode": "off"}

    def presence_state(self) -> dict[str, str]:
        return self._state()

    def set_presence(self, presence: str) -> None:
        if presence not in PRESENCES:
            raise ValueError(f"unknown presence {presence!r}; want one of {PRESENCES}")
        with self._conn() as c:
            c.execute(
                "UPDATE agent_state SET presence=? WHERE org_id=?",
                (presence, self.org_id),
            )
        self._audit(None, "presence_changed", {"presence": presence})

    def set_attendant_mode(self, mode: str) -> None:
        if mode not in ATTENDANT_MODES:
            raise ValueError(
                f"unknown attendant mode {mode!r}; want one of {ATTENDANT_MODES}"
            )
        with self._conn() as c:
            c.execute(
                "UPDATE agent_state SET attendant_mode=? WHERE org_id=?",
                (mode, self.org_id),
            )
        self._audit(None, "attendant_mode_changed", {"mode": mode})

    def apply_mode_switch(self, text: str, *, channel: str) -> str | None:
        """One-word owner instruction from any channel flips the
        attendant mode. Non-switch text changes nothing (None)."""
        mode = parse_mode_switch(text)
        if mode is None:
            return None
        self.set_attendant_mode(mode)
        self._audit(None, "mode_switch", {"mode": mode, "channel": channel})
        return mode

    # -- task lifecycle -------------------------------------------------------

    def enqueue(self, task: AgentTask) -> dict[str, Any]:
        if task.org_id != self.org_id:
            raise OrgMismatch(
                f"task org {task.org_id!r} is not this runtime's org {self.org_id!r}"
            )
        if (
            task.agent_identity_id
            and self.agent_identity_id
            and task.agent_identity_id != self.agent_identity_id
        ):
            raise OrgMismatch("task names a different agent than this runtime runs")
        # Intake fence: a fully revoked agent's queue never starts
        # turning at all (TW-768 assert_accepting_work).
        self.fence.assert_accepting_work()
        task_id = task.id or uuid.uuid4().hex
        flags = scan_injections(task.content)
        now = time.time()
        quiet = task.quiet_window or (None, None)
        with self._conn() as c:
            c.execute(
                "INSERT INTO agent_tasks (task_id, org_id, agent_identity_id,"
                " source, content, toll_budget_cents, quiet_start, quiet_end,"
                " flagged, flags_json, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?, 'queued', ?, ?)",
                (
                    task_id,
                    self.org_id,
                    task.agent_identity_id or self.agent_identity_id,
                    task.source,
                    task.content,
                    task.toll_budget_cents,
                    quiet[0],
                    quiet[1],
                    1 if flags else 0,
                    json.dumps(flags),
                    now,
                    now,
                ),
            )
        # The durable journal persists the prompt before anything runs.
        self._engine.submit(
            session_id=f"agent:{self.org_id}",
            message_id=f"task:{task_id}",
            prompt=task.content,
        )
        self._audit(task_id, "task_enqueued", {"source": task.source})
        if flags:
            self._audit(task_id, "injection_flagged", {"patterns": flags})
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM agent_tasks WHERE task_id=? AND org_id=?",
                (task_id, self.org_id),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["id"] = out["task_id"]
        out["flagged"] = bool(out["flagged"])
        out["flags"] = json.loads(out.pop("flags_json") or "[]")
        out["owner_approved"] = bool(out["owner_approved"])
        out["results"] = (
            json.loads(out.pop("results_json")) if out.get("results_json") else None
        )
        return out

    def _set_status(self, task_id: str, status: str, results: Any = None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE agent_tasks SET status=?, results_json=?, updated_at=?"
                " WHERE task_id=? AND org_id=?",
                (
                    status,
                    json.dumps(results, default=str) if results is not None else None,
                    time.time(),
                    task_id,
                    self.org_id,
                ),
            )

    def approve_task(
        self, task_id: str, steps: list[TaskStepDef], *, owner: str = "worker-1"
    ) -> dict[str, Any]:
        """Owner's answer to an ask-first surfacing: approve and run."""
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        with self._conn() as c:
            c.execute(
                "UPDATE agent_tasks SET owner_approved=1 WHERE task_id=? AND org_id=?",
                (task_id, self.org_id),
            )
        self._audit(task_id, "owner_approved", {})
        return self.run_task(task_id, steps, owner=owner)

    # -- execution ------------------------------------------------------------

    def _in_quiet_hours(self, task: dict[str, Any]) -> bool:
        if not task.get("quiet_start") or not task.get("quiet_end"):
            return False
        now = self._clock().strftime("%H:%M")
        start, end = task["quiet_start"], task["quiet_end"]
        if start <= end:
            return start <= now < end
        return now >= start or now < end  # overnight window

    def _toll_spent(self, task_id: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(amount_cents),0) n FROM agent_tolls"
                " WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return int(row["n"])

    def _charge_toll(self, task: dict[str, Any], step: TaskStepDef) -> None:
        if step.toll_cents <= 0:
            return
        key = f"{task['task_id']}:{step.name}"
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO agent_tolls (task_id, step_name,"
                " amount_cents, idempotency_key, created_at) VALUES (?,?,?,?,?)",
                (task["task_id"], step.name, step.toll_cents, key, time.time()),
            )
            first = cur.rowcount == 1
        if not first:
            return  # already charged for this (task, step): never double
        if self._toll_sink is not None:
            self._toll_sink(
                self.org_id, "agent.task_step", step.toll_cents, key, task["task_id"]
            )
        else:
            from ..services.toll_service import record_toll_event

            record_toll_event(
                self._db,
                self.org_id,
                "agent.task_step",
                idempotency_key=key,
                source_type="agent_task",
                source_id=task["task_id"],
                amount_cents=step.toll_cents,
            )
        self._audit(
            task["task_id"],
            "toll_charged",
            {"step": step.name, "amount_cents": step.toll_cents},
        )

    def _make_handler(self, task: dict[str, Any], step: TaskStepDef):
        def raw(dctx: Any) -> Any:
            task_id = task["task_id"]
            budget = task.get("toll_budget_cents")
            if step.toll_cents and budget is not None:
                spent = self._toll_spent(task_id)
                if spent + step.toll_cents > budget:
                    self._audit(
                        task_id,
                        "toll_budget_exceeded",
                        {
                            "step": step.name,
                            "spent_cents": spent,
                            "step_toll_cents": step.toll_cents,
                            "budget_cents": budget,
                        },
                    )
                    raise TollBudgetExceeded(
                        f"step {step.name!r} toll {step.toll_cents} would spend"
                        f" past task budget {budget} (spent {spent})"
                    )
            tctx = TaskContext(self, task, step.name)
            tctx._durable_ctx = dctx
            if step.credential_ref is not None:
                if self._credential_resolver is None:
                    raise ToolRefused("failed", "no_credential_resolver")
                # Just-in-time: resolved here, held only in this step's
                # context, never audited or logged (ref name only).
                tctx.credentials[step.credential_ref] = self._credential_resolver(
                    step.credential_ref
                )
                self._audit(
                    task_id,
                    "credential_resolved",
                    {"step": step.name, "ref": step.credential_ref},
                )
            if step.tool is not None:
                outcome = gateway.invoke(
                    self._db,
                    self.org_id,
                    step.tool,
                    dict(step.tool_args),
                    actor=self.agent_identity_id or "agent",
                    amount_cents=step.amount_cents,
                    executor=self._tool_executor,
                )
                if outcome.status != "executed":
                    self._audit(
                        task_id,
                        "tool_refused",
                        {
                            "step": step.name,
                            "tool": step.tool,
                            "status": outcome.status,
                            "reason": outcome.reason,
                        },
                    )
                    raise ToolRefused(outcome.status, outcome.reason)
                self._audit(
                    task_id,
                    "tool_executed",
                    {"step": step.name, "tool": step.tool},
                )
                result = outcome.result
            else:
                result = step.handler(tctx)  # type: ignore[misc]
            # TW-785 backstop: a result declaring its executed amount
            # is re-checked against the ledger before the step counts.
            if isinstance(result, dict) and "executed_amount_cents" in result:
                executed = result["executed_amount_cents"]
                if step.action_class is not None and isinstance(executed, int):
                    decision = ledger.check_permission(
                        self._db,
                        self.org_id,
                        step.action_class,
                        amount_cents=executed,
                        agent_identity_id=self.agent_identity_id,
                    )
                    if not decision.allowed:
                        self._audit(
                            task_id,
                            "money_fence_violation",
                            {
                                "step": step.name,
                                "action_class": step.action_class,
                                "executed_amount_cents": executed,
                                "reason": decision.reason,
                            },
                        )
                        raise MoneyFenceViolation(
                            f"executed amount {executed} denied: {decision.reason}"
                        )
            self._charge_toll(task, step)
            self._audit(task_id, "step_completed", {"step": step.name})
            return result

        return raw

    def run_task(
        self,
        task_id: str,
        steps: list[TaskStepDef],
        *,
        owner: str = "worker-1",
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        if task["status"] == "completed":
            return {"status": "completed", "results": task.get("results") or []}

        agent_initiated = task["source"] != "owner"
        consequential = any(s.action_class or s.tool for s in steps)
        state = self._state()

        # Spec 8 ask-first: owner present (or presence unknown), the
        # agent surfaces a question instead of acting on its own
        # initiative. Owner-initiated work is already an instruction.
        if (
            agent_initiated
            and consequential
            and not task["owner_approved"]
            and state["presence"] in ("at_desk", "unknown")
        ):
            self._set_status(task_id, "awaiting_owner")
            self._audit(
                task_id,
                "ask_first",
                {
                    "presence": state["presence"],
                    "question": (
                        f"Task from {task['source']} wants to act "
                        f"({steps[0].name}); run it, or are you doing it?"
                    ),
                },
            )
            return {"status": "awaiting_owner", "results": []}

        # Away with the attendant off, agent-initiated work waits.
        if (
            agent_initiated
            and state["presence"] == "away"
            and state["attendant_mode"] == "off"
        ):
            self._set_status(task_id, "deferred")
            self._audit(task_id, "deferred_attendant_off", {})
            return {"status": "deferred", "results": []}

        if agent_initiated and self._in_quiet_hours(task):
            self._set_status(task_id, "deferred")
            self._audit(task_id, "deferred_quiet_hours", {})
            return {"status": "deferred", "results": []}

        turn_id = self._engine.submit(
            session_id=f"agent:{self.org_id}",
            message_id=f"task:{task_id}",
            prompt=task["content"],
        )
        durable_steps: list[StepDef] = []
        for step in steps:
            inner = StepDef(
                name=step.name,
                handler=self._make_handler(task, step),
                idempotent=step.idempotent,
            )
            if step.action_class is not None:
                inner = gate.guarded_step(
                    self._db,
                    self.org_id,
                    step.action_class,
                    inner,
                    amount_cents=step.amount_cents,
                    agent_identity_id=self.agent_identity_id,
                )
            durable_steps.append(inner)

        self._set_status(task_id, "running")
        result = self._engine.run_turn(turn_id, durable_steps, owner=owner)
        status = result["status"]
        if status == "completed":
            self._set_status(task_id, "completed", result["results"])
            self._audit(task_id, "task_completed", {"steps": len(result["results"])})
            return {"status": "completed", "results": result["results"]}
        if status == "step_cap":
            self._set_status(task_id, "failed", result.get("results"))
            self._audit(task_id, "task_failed", {"reason": "step_cap"})
            return {
                "status": "failed",
                "results": result.get("results") or [],
                "reason": "step_cap",
            }
        # failed: classify from the engine's error string
        error = result.get("error") or ""
        results = result.get("results") or []
        if "PermissionDenied" in error:
            self._set_status(task_id, "fenced", results)
            self._audit(task_id, "revocation_fenced", {"error": error})
            return {"status": "fenced", "results": results, "reason": error}
        if "TollBudgetExceeded" in error:
            self._set_status(task_id, "toll_exceeded", results)
            return {"status": "toll_exceeded", "results": results, "reason": error}
        self._set_status(task_id, "failed", results)
        self._audit(task_id, "task_failed", {"error": error})
        return {"status": "failed", "results": results, "reason": error}
