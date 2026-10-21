"""Durable agent turn engine (TW-665).

Pattern-mined from vercel-labs/fx0 / libfx (Apache-2.0, verified live in the
2026-10-21 GitHub sweep; LICENSE read at the upstream repo root). This is a
clean-room pattern implant, not a port: no fx0 code is copied, and none of
its vendor substrate is adopted.

Adopted from fx0's durability design (README, "Durability"):
  1. Save before answering. A prompt is journaled and acknowledged before any
     step runs, and a retried submission with the same message_id reaches the
     same turn, so a sent message cannot be lost or double-started.
  2. Per-step journal. Every step is recorded started, then completed with
     its result. A crash leaves the journal exactly where the process died.
  3. Resume, don't restart. A new worker continues from the last completed
     step; completed steps are never re-run and never re-billed.
  4. Idempotency is declared, never assumed. Every step must say whether it
     is safe to repeat. An interrupted step that declared itself
     non-idempotent is NOT re-run: it is marked
     interrupted_unknown_outcome and the turn is told it was interrupted.
     An interrupted idempotent step re-runs, and ctx.apply_effect() makes
     its side effect land exactly once even if the crash hit after the
     effect but before the completion journal write.
  5. Fencing. One worker holds a turn at a time via a lease. A second
     worker is refused while the lease is live; a dead worker's lease
     expires and the next worker takes over.
  6. Step cap. A turn stops after `step_cap` steps (fx0: 64), so a looping
     agent cannot run forever.

Deliberately NOT adopted: Vercel Workflow World, Vercel Queues, and the
Vercel AI Gateway. Those are vendor-gated / paid paths and violate the
FOSS-first, $0 rule. Here the journal is our own store (SQLite file in this
prototype; the same tables map 1:1 onto Postgres for production), the
wake-up is a plain function call / our own Flows scheduler, and model calls
stay with whatever caller owns the turn.

Retries reuse the TW-208 self-healing primitives
(app.self_healing.retry_with_backoff / is_transient): transient failures
retry with backoff, permanent failures fail the turn loudly at once.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app import self_healing as sh

DEFAULT_STEP_CAP = 64  # fx0's per-turn cap, subagent steps included upstream


class TurnFenced(RuntimeError):
    """Another worker holds this turn's lease."""


@dataclass
class StepDef:
    """One step of a turn. `idempotent` must be declared explicitly."""

    name: str
    handler: Callable[["StepContext"], Any]
    idempotent: bool

    def __post_init__(self):
        if self.idempotent is None:
            raise ValueError(
                f"step {self.name!r} must declare idempotent=True/False; "
                "an undeclared tool is never re-run after a crash (fx0 rule)"
            )


class StepContext:
    def __init__(self, engine: "DurableTurnEngine", turn_id: str, step: StepDef):
        self._engine = engine
        self.turn_id = turn_id
        self.step_name = step.name
        self.interrupted = engine.get_turn(turn_id).get("interrupted", 0) == 1

    def apply_effect(self, effect_key: str, fn: Callable[[], Any]) -> Any:
        """Run a side effect exactly once per (turn, effect_key).

        If a previous attempt already landed the effect, its stored result
        is returned and fn is not called again. This is what makes an
        idempotent step safe to re-run after a crash that hit between the
        effect and the step-completion journal write.
        """
        return self._engine._apply_effect(self.turn_id, effect_key, fn)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_turns (
  turn_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  message_id TEXT NOT NULL UNIQUE,
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  interrupted INTEGER NOT NULL DEFAULT 0,
  owner TEXT,
  lease_expires_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS durable_steps (
  turn_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  idempotent INTEGER NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (turn_id, step_index)
);
CREATE TABLE IF NOT EXISTS durable_effects (
  turn_id TEXT NOT NULL,
  effect_key TEXT NOT NULL,
  result_json TEXT,
  PRIMARY KEY (turn_id, effect_key)
);
CREATE TABLE IF NOT EXISTS durable_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT,
  created_at REAL NOT NULL
);
"""


class DurableTurnEngine:
    """A durable-session turn runner backed by a local journal store."""

    def __init__(
        self,
        db_path: str,
        *,
        step_cap: int = DEFAULT_STEP_CAP,
        lease_seconds: float = 30.0,
        retry_attempts: int = 3,
        base_delay: float = 0.05,
        sleep: Callable[[float], None] | None = None,
    ):
        self.db_path = db_path
        self.step_cap = step_cap
        self.lease_seconds = lease_seconds
        self.retry_attempts = retry_attempts
        self.base_delay = base_delay
        self._sleep = sleep
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # -- store plumbing -------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _event(self, c: sqlite3.Connection, turn_id: str, kind: str, payload: Any):
        c.execute(
            "INSERT INTO durable_events (turn_id, kind, payload_json, created_at)"
            " VALUES (?,?,?,?)",
            (turn_id, kind, json.dumps(payload, default=str), time.time()),
        )

    # -- submission: persist before execution ---------------------------
    def submit(self, *, session_id: str, message_id: str, prompt: str) -> str:
        """Journal the prompt before anything runs; dedupe by message_id."""
        turn_id = uuid.uuid5(uuid.NAMESPACE_URL, f"twistor-turn:{message_id}").hex
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO durable_turns"
                " (turn_id, session_id, message_id, prompt, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'pending',?,?)",
                (turn_id, session_id, message_id, prompt, now, now),
            )
            row = c.execute(
                "SELECT turn_id FROM durable_turns WHERE message_id=?", (message_id,)
            ).fetchone()
            self._event(c, row["turn_id"], "prompt_saved", {"message_id": message_id})
        return row["turn_id"]

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM durable_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
        return dict(row) if row else {}

    def read_events(self, turn_id: str, *, cursor: int = 0):
        """Stream the journal after `cursor`; each event is read once per cursor."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM durable_events WHERE turn_id=? AND id>? ORDER BY id",
                (turn_id, cursor),
            ).fetchall()
        events = [
            {
                "id": r["id"],
                "kind": r["kind"],
                "payload": json.loads(r["payload_json"] or "null"),
            }
            for r in rows
        ]
        return events, (rows[-1]["id"] if rows else cursor)

    # -- fencing ----------------------------------------------------------
    def acquire_lease(self, turn_id: str, *, owner: str) -> None:
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT owner, lease_expires_at FROM durable_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if (
                row
                and row["owner"]
                and row["owner"] != owner
                and row["lease_expires_at"]
                and row["lease_expires_at"] > now
            ):
                raise TurnFenced(f"turn {turn_id} held by {row['owner']}")
            c.execute(
                "UPDATE durable_turns SET owner=?, lease_expires_at=?, updated_at=?"
                " WHERE turn_id=?",
                (owner, now + self.lease_seconds, now, turn_id),
            )

    def _release_lease(self, turn_id: str, owner: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE durable_turns SET owner=NULL, lease_expires_at=NULL"
                " WHERE turn_id=? AND owner=?",
                (turn_id, owner),
            )

    # -- effects ----------------------------------------------------------
    def _apply_effect(
        self, turn_id: str, effect_key: str, fn: Callable[[], Any]
    ) -> Any:
        with self._conn() as c:
            row = c.execute(
                "SELECT result_json FROM durable_effects WHERE turn_id=? AND effect_key=?",
                (turn_id, effect_key),
            ).fetchone()
        if row:
            return json.loads(row["result_json"] or "null")
        result = fn()
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO durable_effects (turn_id, effect_key, result_json)"
                " VALUES (?,?,?)",
                (turn_id, effect_key, json.dumps(result, default=str)),
            )
            row = c.execute(
                "SELECT result_json FROM durable_effects WHERE turn_id=? AND effect_key=?",
                (turn_id, effect_key),
            ).fetchone()
        return json.loads(row["result_json"] or "null")

    def count_effects(self, turn_id: str) -> int:
        with self._conn() as c:
            return c.execute(
                "SELECT COUNT(*) n FROM durable_effects WHERE turn_id=?", (turn_id,)
            ).fetchone()["n"]

    # -- execution ----------------------------------------------------------
    def run_turn(
        self, turn_id: str, steps: list[StepDef], *, owner: str = "worker-1"
    ) -> dict[str, Any]:
        """Run or resume a turn. Safe to call again after a crash: completed
        steps are read back from the journal, never re-executed."""
        self.acquire_lease(turn_id, owner=owner)
        try:
            return self._run(turn_id, steps, owner)
        finally:
            # On a BaseException (process-death simulation) the lease is left
            # in place deliberately: it expires, then a resumer takes over,
            # exactly like fx0's backstop waiting out the dead request.
            if self.get_turn(turn_id).get("status") in (
                "completed",
                "failed",
                "step_cap",
            ):
                self._release_lease(turn_id, owner)

    def _set_status(self, c, turn_id, status, *, interrupted=False):
        c.execute(
            "UPDATE durable_turns SET status=?, interrupted=?, updated_at=? WHERE turn_id=?",
            (status, 1 if interrupted else 0, time.time(), turn_id),
        )

    def _run(self, turn_id: str, steps: list[StepDef], owner: str) -> dict[str, Any]:
        with self._conn() as c:
            turn = dict(
                c.execute(
                    "SELECT * FROM durable_turns WHERE turn_id=?", (turn_id,)
                ).fetchone()
            )
            was_interrupted = False
            if turn["status"] in ("running",):
                was_interrupted = True
            self._set_status(c, turn_id, "running", interrupted=was_interrupted)
            self._event(
                c, turn_id, "turn_started", {"owner": owner, "resume": was_interrupted}
            )

        results: list[Any] = []
        executed = 0
        for idx, step in enumerate(steps):
            if executed >= self.step_cap or idx >= self.step_cap:
                with self._conn() as c:
                    self._set_status(
                        c, turn_id, "step_cap", interrupted=was_interrupted
                    )
                    self._event(c, turn_id, "step_cap_hit", {"cap": self.step_cap})
                return {
                    "status": "step_cap",
                    "results": results,
                    "interrupted": was_interrupted,
                }
            with self._conn() as c:
                row = c.execute(
                    "SELECT * FROM durable_steps WHERE turn_id=? AND step_index=?",
                    (turn_id, idx),
                ).fetchone()
            if row and row["status"] == "completed":
                results.append(json.loads(row["result_json"] or "null"))
                continue
            if row and row["status"] == "ambiguous":
                results.append({"interrupted_unknown_outcome": True})
                continue
            if row and row["status"] == "started" and not step.idempotent:
                # Crash cut this step off and it is not safe to repeat:
                # never re-run it; tell the turn it may have happened.
                with self._conn() as c:
                    c.execute(
                        "UPDATE durable_steps SET status='ambiguous'"
                        " WHERE turn_id=? AND step_index=?",
                        (turn_id, idx),
                    )
                    self._set_status(c, turn_id, "running", interrupted=True)
                    self._event(
                        c, turn_id, "step_ambiguous", {"step": step.name, "index": idx}
                    )
                was_interrupted = True
                results.append({"interrupted_unknown_outcome": True})
                executed += 1
                continue
            # Journal the start BEFORE the handler runs.
            with self._conn() as c:
                c.execute(
                    "INSERT INTO durable_steps (turn_id, step_index, name, idempotent,"
                    " status, attempts) VALUES (?,?,?,?,'started',0)"
                    " ON CONFLICT(turn_id, step_index) DO UPDATE SET status='started'",
                    (turn_id, idx, step.name, 1 if step.idempotent else 0),
                )
                self._event(
                    c, turn_id, "step_started", {"step": step.name, "index": idx}
                )
            ctx = StepContext(self, turn_id, step)
            try:
                result = sh.retry_with_backoff(
                    lambda: step.handler(ctx),
                    attempts=self.retry_attempts,
                    base_delay=self.base_delay,
                    max_delay=1.0,
                    sleep=self._sleep,
                )
            except Exception as exc:  # permanent, or retries exhausted
                with self._conn() as c:
                    c.execute(
                        "UPDATE durable_steps SET status='failed'"
                        " WHERE turn_id=? AND step_index=?",
                        (turn_id, idx),
                    )
                    self._set_status(c, turn_id, "failed", interrupted=was_interrupted)
                    self._event(
                        c,
                        turn_id,
                        "turn_failed",
                        {"step": step.name, "error": f"{type(exc).__name__}: {exc}"},
                    )
                return {
                    "status": "failed",
                    "results": results,
                    "interrupted": was_interrupted,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            with self._conn() as c:
                c.execute(
                    "UPDATE durable_steps SET status='completed', result_json=?"
                    " WHERE turn_id=? AND step_index=?",
                    (json.dumps(result, default=str), turn_id, idx),
                )
                self._event(
                    c, turn_id, "step_completed", {"step": step.name, "index": idx}
                )
            results.append(result)
            executed += 1

        with self._conn() as c:
            self._set_status(c, turn_id, "completed", interrupted=was_interrupted)
            self._event(c, turn_id, "turn_completed", {"steps": len(results)})
        return {
            "status": "completed",
            "results": results,
            "interrupted": was_interrupted,
        }
