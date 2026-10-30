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
  The runtime passes its agent_identity_id, so the gateway's ledger
  check reads the ACTING AGENT's grant scope (TW-821): per-agent
  fencing is a property of the tool path, not an opt-in the step
  earns by also declaring an action_class.
- M1 toll ledger (TW-550): each executed step's toll posts through
  record_toll_event (or an injected sink) under an idempotency key
  of (task, step), so a resume can never double-charge. The sink is
  called BEFORE the local toll row is written (TW-823): a sink
  failure leaves no local row, is audited as toll_sink_failed, and
  the resume retries the sink — a toll is never locally "spent"
  while M1 holds nothing. A task's toll budget is checked BEFORE a
  step runs: the step that would overspend never executes.
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

Revocation is not opt-in (TW-820): every step re-asserts
assert_accepting_work at its boundary before anything else runs,
so a revoke-all landing mid-run fences the NEXT step even when it
is a plain handler with no action_class and no tool. Declared
steps keep their finer per-class fencing on top of that floor.

Trust boundaries, stated plainly:
- Task SOURCE is a caller-supplied label and is untrusted data
  (TW-828): customer email can claim to be the owner. Only a task
  whose intake was authenticated by the channel wiring carries
  source_verified=True; anything else is treated as
  agent-initiated and gets the most restrictive handling
  (ask-first, quiet hours, attendant gating).
- A mode switch is an owner instruction with side effects, so
  apply_mode_switch requires a verified owner actor token and a
  known owner channel (TW-827); the Spec 8 wiring authenticates
  the channel and supplies both. Unverified claims change nothing.
- Resolved credential values are registered with the runtime the
  moment they are resolved and scrubbed from every surface the
  runtime persists — step results, error strings, audit rows,
  task records (TW-824). Handlers still see the real value, in
  their step's context only.

Sandbox: a runtime is bound to ONE org. Its store rows carry the
org, every read filters on it, and a task for another org is
refused at enqueue — two shops' runtimes may share a journal file
and still never see each other's work. A constructor-injected
identity record is validated against the runtime's org (and the
runtime's agent, when both name one) before anything runs
(TW-826): a foreign shop's persona is never served.
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
from ..agentperms.models import MONEY_CLASSES
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


class DuplicateTaskId(ValueError):
    """A task id that is already claimed — by this org or another.

    Task ids live in one shared journal namespace (TW-825): letting
    a second enqueue take an existing id would pre-claim or shadow
    the first task. The refusal is this typed error, never a raw
    sqlite3.IntegrityError leaking out of the store."""


class UnverifiedModeSwitch(ValueError):
    """A mode switch arrived without a verified owner actor on a
    known owner channel (TW-827). The attendant mode is left
    exactly as it was."""


# Channels a mode switch may legitimately arrive on. The Spec 8
# wiring maps each real owner channel (the app flip, the owner's
# phone, the owner's mailbox, a call from the owner) onto one of
# these kinds and attests the actor; anything else is a claim,
# not a channel.
VERIFIED_MODE_CHANNELS: tuple[str, ...] = ("app", "sms", "email", "call", "phone")


def _record_field(record: Any, key: str) -> Any:
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def _iter_strings(value: Any):
    """Every string inside a step result (dict keys and values,
    lists, tuples) — the surfaces TW-819 scans and TW-824 scrubs."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_strings(k)
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _collect_executed_amounts(value: Any, out: list[Any]) -> None:
    """Every value carried under the documented result key
    'executed_amount_cents', at any nesting depth. A money amount
    does not escape the fence by riding inside a sub-dict."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "executed_amount_cents":
                out.append(v)
            else:
                _collect_executed_amounts(v, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_executed_amounts(item, out)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tasks (
  task_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  agent_identity_id TEXT,
  source TEXT NOT NULL,
  source_verified INTEGER NOT NULL DEFAULT 0,
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
        # TW-826: an injected identity record must belong to THIS
        # org (and to this runtime's agent, when both name one).
        # Serving another shop's persona is a cross-org leak with a
        # friendly face; refuse it at construction, before any
        # state exists.
        if identity is not None:
            rec_org = _record_field(identity, "org_id")
            if rec_org is not None and rec_org != org_id:
                raise OrgMismatch(
                    f"identity record belongs to org {rec_org!r}, "
                    f"not this runtime's org {org_id!r}"
                )
            rec_id = _record_field(identity, "id")
            if (
                rec_id is not None
                and agent_identity_id is not None
                and rec_id != agent_identity_id
            ):
                raise OrgMismatch(
                    f"identity record is agent {rec_id!r}, "
                    f"not this runtime's agent {agent_identity_id!r}"
                )
        self._db = db
        self.org_id = org_id
        self.agent_identity_id = agent_identity_id
        self._identity = identity
        # TW-824: values resolved for steps, registered so every
        # persisted surface can be scrubbed of them. Never logged.
        self._known_secrets: set[str] = set()
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

    def _scrub(self, value: Any) -> Any:
        """Replace every known resolved credential value with a
        redaction marker, deeply. Applied to audit payloads, step
        results, error strings, and task records before any of
        them persist (TW-824)."""
        if not self._known_secrets:
            return value
        if isinstance(value, str):
            out = value
            for secret in self._known_secrets:
                out = out.replace(secret, "[redacted]")
            return out
        if isinstance(value, dict):
            return {self._scrub(k): self._scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._scrub(v) for v in value]
        return value

    def _audit(self, task_id: str | None, kind: str, payload: Any = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO agent_audit (task_id, org_id, kind, payload_json,"
                " created_at) VALUES (?,?,?,?,?)",
                (
                    task_id,
                    self.org_id,
                    kind,
                    json.dumps(self._scrub(payload or {}), default=str),
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

    def apply_mode_switch(
        self, text: str, *, channel: str, actor: str | None = None
    ) -> str | None:
        """One-word owner instruction flips the attendant mode —
        but only on a verified owner claim (TW-827).

        Non-switch text changes nothing and needs no authentication
        (None). A text that IS a switch is an owner instruction
        with side effects: the Spec 8 wiring must supply the
        channel kind (one of VERIFIED_MODE_CHANNELS) and an actor
        token of the verified-owner shape ("owner:<id>"), attesting
        it authenticated that channel. Anything less raises
        UnverifiedModeSwitch and the mode does not move — a
        customer email that says "on" is data, not an instruction.
        The verified actor is recorded on the audit row."""
        mode = parse_mode_switch(text)
        if mode is None:
            return None
        if channel not in VERIFIED_MODE_CHANNELS or not (
            isinstance(actor, str)
            and actor.startswith("owner:")
            and len(actor) > len("owner:")
        ):
            raise UnverifiedModeSwitch(
                "mode switch requires a verified owner actor on a known "
                f"owner channel; got channel {channel!r}, actor {actor!r}"
            )
        self.set_attendant_mode(mode)
        self._audit(
            None, "mode_switch", {"mode": mode, "channel": channel, "actor": actor}
        )
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
            # TW-825: task ids are one namespace across every org
            # sharing this journal. A second claim on an id — same
            # org or another shop's — is a typed refusal, never a
            # raw sqlite error and never a silent shadow.
            claimed = c.execute(
                "SELECT task_id FROM agent_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if claimed is not None:
                raise DuplicateTaskId(
                    f"task id {task_id!r} is already claimed in this journal"
                )
            try:
                c.execute(
                    "INSERT INTO agent_tasks (task_id, org_id, agent_identity_id,"
                    " source, source_verified, content, toll_budget_cents,"
                    " quiet_start, quiet_end, flagged, flags_json, status,"
                    " created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'queued', ?, ?)",
                    (
                        task_id,
                        self.org_id,
                        task.agent_identity_id or self.agent_identity_id,
                        task.source,
                        1 if task.source_verified else 0,
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
            except sqlite3.IntegrityError:
                raise DuplicateTaskId(
                    f"task id {task_id!r} is already claimed in this journal"
                ) from None
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
        out["source_verified"] = bool(out.get("source_verified"))
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
                    json.dumps(self._scrub(results), default=str)
                    if results is not None
                    else None,
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
            already = c.execute(
                "SELECT 1 FROM agent_tolls WHERE task_id=? AND step_name=?",
                (task["task_id"], step.name),
            ).fetchone()
        if already is not None:
            return  # already charged for this (task, step): never double
        # TW-823: the sink is called BEFORE the local row exists.
        # If the sink raises, no local row survives to suppress the
        # retry: the failure is audited (toll_sink_failed), the step
        # fails, and a resume calls the sink again under the same
        # idempotency key — M1 receives the toll exactly once, and
        # local spent never claims money M1 does not hold. If the
        # sink succeeded but the process died before the local
        # insert, the resume replays the sink call and M1's own
        # idempotency key dedupes it.
        try:
            if self._toll_sink is not None:
                self._toll_sink(
                    self.org_id,
                    "agent.task_step",
                    step.toll_cents,
                    key,
                    task["task_id"],
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
        except Exception:
            self._audit(
                task["task_id"],
                "toll_sink_failed",
                {"step": step.name, "amount_cents": step.toll_cents},
            )
            raise
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO agent_tolls (task_id, step_name,"
                " amount_cents, idempotency_key, created_at) VALUES (?,?,?,?,?)",
                (task["task_id"], step.name, step.toll_cents, key, time.time()),
            )
        self._audit(
            task["task_id"],
            "toll_charged",
            {"step": step.name, "amount_cents": step.toll_cents},
        )

    def _scan_result(
        self, task: dict[str, Any], step: TaskStepDef, origin: str, result: Any
    ) -> None:
        """TW-819: content coming BACK from a step — handler results
        and tool outputs — is agent-read content too. Planted
        instructions there are flagged on the audit feed and marked
        on the task record, so downstream consumers (Spec 4
        sequences, Spec 6 surfaces) can see the step's output was
        tainted. The result itself still flows: flagging is
        visibility, and no step output is ever executed as an
        instruction by this runtime."""
        patterns: list[str] = []
        for text in _iter_strings(result):
            for name in scan_injections(text):
                if name not in patterns:
                    patterns.append(name)
        if not patterns:
            return
        task_id = task["task_id"]
        self._audit(
            task_id,
            "injection_flagged",
            {"step": step.name, "origin": origin, "patterns": patterns},
        )
        with self._conn() as c:
            row = c.execute(
                "SELECT flags_json FROM agent_tasks WHERE task_id=? AND org_id=?",
                (task_id, self.org_id),
            ).fetchone()
            merged = list(json.loads(row["flags_json"] or "[]")) if row else []
            for name in patterns:
                if name not in merged:
                    merged.append(name)
            c.execute(
                "UPDATE agent_tasks SET flagged=1, flags_json=?"
                " WHERE task_id=? AND org_id=?",
                (json.dumps(merged), task_id, self.org_id),
            )

    def _check_executed_amounts(
        self,
        task: dict[str, Any],
        step: TaskStepDef,
        effective_class: str | None,
        result: Any,
    ) -> None:
        """TW-785 backstop, type-strict (TW-822).

        Every value a result carries under 'executed_amount_cents'
        — top level or nested — must be a real int (bool, string,
        and float renderings are rejected, never silently skipped),
        and must be fenced by a MONEY action class: the step's own
        declared class, or for a tool step the class the gateway
        resolved for the call. An amount with no money fence
        governing it is a violation by construction. Amounts under
        a money class are checked against the ledger for the
        ACTING AGENT at the executed value."""
        amounts: list[Any] = []
        _collect_executed_amounts(result, amounts)
        if not amounts:
            return
        task_id = task["task_id"]

        def violation(reason: str, amount: Any) -> None:
            self._audit(
                task_id,
                "money_fence_violation",
                {
                    "step": step.name,
                    "action_class": effective_class,
                    "executed_amount_cents": amount,
                    "reason": reason,
                },
            )
            raise MoneyFenceViolation(
                f"step {step.name!r} executed amount {amount!r} refused: {reason}"
            )

        for amount in amounts:
            if isinstance(amount, bool) or not isinstance(amount, int):
                violation("non_integer_executed_amount", amount)
            if effective_class is None or effective_class not in MONEY_CLASSES:
                violation("executed_amount_without_money_fence", amount)
            decision = ledger.check_permission(
                self._db,
                self.org_id,
                effective_class,
                amount_cents=amount,
                agent_identity_id=self.agent_identity_id,
            )
            if not decision.allowed:
                violation(decision.reason, amount)

    def _make_handler(self, task: dict[str, Any], step: TaskStepDef):
        def raw(dctx: Any) -> Any:
            task_id = task["task_id"]
            # TW-820: the step-boundary floor. Before budget, before
            # credentials, before any effect: a revoke-all that
            # landed while an earlier step ran fences THIS step,
            # whether or not it declares an action_class or a tool.
            # Declared steps keep their per-class fencing on top.
            self.fence.assert_accepting_work()
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
                # The value is registered for redaction (TW-824) the
                # moment it exists, so nothing the handler returns or
                # raises can carry it into a persisted surface.
                resolved = self._credential_resolver(step.credential_ref)
                tctx.credentials[step.credential_ref] = resolved
                if isinstance(resolved, str) and resolved:
                    self._known_secrets.add(resolved)
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
                    agent_identity_id=self.agent_identity_id,
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
                origin = "tool_output"
                effective_class = outcome.action_class
            else:
                try:
                    result = step.handler(tctx)  # type: ignore[misc]
                except Exception as exc:
                    # TW-824: a handler error whose message carries a
                    # resolved credential is re-raised scrubbed, so
                    # the engine's error record — and everything the
                    # runtime derives from it — never holds the value.
                    # Gate exceptions (their messages are ledger-built,
                    # never credential-bearing) pass through untouched.
                    if type(exc).__name__ not in (
                        "PermissionDenied",
                        "AgentWorkRefused",
                    ) and any(s in str(exc) for s in self._known_secrets):
                        raise RuntimeError(self._scrub(str(exc))) from None
                    raise
                origin = "step_result"
                effective_class = step.action_class
            result = self._scrub(result)
            # TW-819: step outputs are agent-read content too.
            self._scan_result(task, step, origin, result)
            # TW-785 backstop on the executed amount, type-strict
            # and covering tool steps (TW-822).
            self._check_executed_amounts(task, step, effective_class, result)
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

        # TW-828: owner treatment is earned by a VERIFIED owner
        # source, never by the label alone. An unverified "owner"
        # label is treated exactly like any agent-initiated source.
        verified_owner = task["source"] == "owner" and bool(task.get("source_verified"))
        agent_initiated = not verified_owner
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
            results = self._scrub(result["results"])
            self._set_status(task_id, "completed", results)
            self._audit(task_id, "task_completed", {"steps": len(results)})
            return {"status": "completed", "results": results}
        if status == "step_cap":
            self._set_status(task_id, "failed", result.get("results"))
            self._audit(task_id, "task_failed", {"reason": "step_cap"})
            return {
                "status": "failed",
                "results": result.get("results") or [],
                "reason": "step_cap",
            }
        # failed: classify from the engine's error string
        error = self._scrub(result.get("error") or "")
        results = self._scrub(result.get("results") or [])
        if "PermissionDenied" in error or "AgentWorkRefused" in error:
            self._set_status(task_id, "fenced", results)
            self._audit(task_id, "revocation_fenced", {"error": error})
            return {"status": "fenced", "results": results, "reason": error}
        if "TollBudgetExceeded" in error:
            self._set_status(task_id, "toll_exceeded", results)
            return {"status": "toll_exceeded", "results": results, "reason": error}
        self._set_status(task_id, "failed", results)
        self._audit(task_id, "task_failed", {"error": error})
        return {"status": "failed", "results": results, "reason": error}
