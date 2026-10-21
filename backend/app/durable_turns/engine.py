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
     effect but before the completion journal write. The JOURNALED
     declaration governs on resume: a resuming process whose step list
     disagrees with the journal (renamed step, flipped declaration) is
     refused with JournalMismatch instead of silently proceeding, and a
     non-idempotent step also gets exactly one attempt inside a run (a
     declaration of "not safe to repeat" is never retried in-run either).
  5. Fencing. One worker holds a turn at a time via a lease. Acquisition
     is a single conditional UPDATE (compare-and-swap: the row changes
     only if the lease is free, already ours, or expired) and raises
     TurnFenced unless exactly one row changed; ownership is revalidated
     before each step execution and inside apply_effect, so a worker
     whose lease expired mid-run stops instead of writing. A dead
     worker's lease expires and the next worker takes over. (The Postgres
     port still owes the same fencing as one transaction plus a lease
     heartbeat for long steps; see the pattern doc.)
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


class JournalMismatch(RuntimeError):
    """The supplied step list disagrees with the journaled step rows.

    Raised on resume when a step was renamed or its idempotency
    declaration flipped versus what the original run journaled. The
    journal is authoritative: proceeding on the caller's word would let
    a redeployed step list silently re-run a crashed non-idempotent step.
    """


@dataclass
class StepDef:
    """One step of a turn. `idempotent` must be declared explicitly."""

    name: str
    handler: Callable[["StepContext"], Any]
    idempotent: bool

    def __post_init__(self):
        if not isinstance(self.idempotent, bool):
            raise ValueError(
                f"step {self.name!r} must declare idempotent=True/False; "
                "an undeclared tool is never re-run after a crash (fx0 rule)"
            )


class StepContext:
    def __init__(
        self,
        engine: "DurableTurnEngine",
        turn_id: str,
        step: StepDef,
        *,
        owner: str | None = None,
    ):
        self._engine = engine
        self.turn_id = turn_id
        self.step_name = step.name
        self._owner = owner
        self.interrupted = engine.get_turn(turn_id).get("interrupted", 0) == 1

    def apply_effect(self, effect_key: str, fn: Callable[[], Any]) -> Any:
        """Run a side effect exactly once per (turn, effect_key).

        If a previous attempt already landed the effect, its stored result
        is returned and fn is not called again. This is what makes an
        idempotent step safe to re-run after a crash that hit between the
        effect and the step-completion journal write.
        """
        return self._engine._apply_effect(
            self.turn_id, effect_key, fn, owner=self._owner
        )


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
            cur = c.execute(
                "INSERT OR IGNORE INTO durable_turns"
                " (turn_id, session_id, message_id, prompt, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'pending',?,?)",
                (turn_id, session_id, message_id, prompt, now, now),
            )
            row = c.execute(
                "SELECT turn_id FROM durable_turns WHERE message_id=?", (message_id,)
            ).fetchone()
            # Journal prompt_saved only when the INSERT actually landed:
            # a retried submit reaches the same turn but is not a second
            # prompt (TW-665 review m3).
            if cur.rowcount == 1:
                self._event(
                    c, row["turn_id"], "prompt_saved", {"message_id": message_id}
                )
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
        """Take the turn's lease with a single conditional UPDATE.

        Compare-and-swap: the row changes only when the lease is free,
        already held by this owner, or expired. SQLite executes one
        UPDATE atomically under the write lock, so two racing workers
        cannot both land it; the loser sees rowcount 0 and is fenced.
        (The previous SELECT-then-UPDATE let both racers win; TW-665
        review M3.)
        """
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE durable_turns SET owner=?, lease_expires_at=?, updated_at=?"
                " WHERE turn_id=? AND (owner IS NULL OR owner=?"
                " OR lease_expires_at IS NULL OR lease_expires_at<=?)",
                (owner, now + self.lease_seconds, now, turn_id, owner, now),
            )
            if cur.rowcount != 1:
                row = c.execute(
                    "SELECT owner FROM durable_turns WHERE turn_id=?", (turn_id,)
                ).fetchone()
                holder = row["owner"] if row else None
                raise TurnFenced(f"turn {turn_id} held by {holder}")

    def _assert_lease(self, turn_id: str, owner: str) -> None:
        """Revalidate that `owner` still holds a live lease on the turn.

        A worker whose lease expired mid-run (a zombie the resumer has
        already replaced) must stop before its next write instead of
        racing the new owner.
        """
        turn = self.get_turn(turn_id)
        if (
            turn.get("owner") != owner
            or not turn.get("lease_expires_at")
            or turn["lease_expires_at"] <= time.time()
        ):
            raise TurnFenced(
                f"turn {turn_id} lease no longer held by {owner}"
                f" (held by {turn.get('owner')})"
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
        self,
        turn_id: str,
        effect_key: str,
        fn: Callable[[], Any],
        *,
        owner: str | None = None,
    ) -> Any:
        """Land an effect exactly once, atomically.

        The existence check, the fn() call, and the journal insert run
        inside one BEGIN IMMEDIATE transaction, which takes the write
        lock up front: two live workers cannot both pass the check and
        both run fn before either journals it (the old check-then-insert
        across two connections had exactly that window; TW-665 review
        M3). When `owner` is known, lease ownership is revalidated
        inside the same transaction.
        """
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                if owner is not None:
                    t = conn.execute(
                        "SELECT owner, lease_expires_at FROM durable_turns"
                        " WHERE turn_id=?",
                        (turn_id,),
                    ).fetchone()
                    if (
                        t is None
                        or t["owner"] != owner
                        or not t["lease_expires_at"]
                        or t["lease_expires_at"] <= time.time()
                    ):
                        raise TurnFenced(
                            f"turn {turn_id} lease no longer held by {owner}"
                        )
                row = conn.execute(
                    "SELECT result_json FROM durable_effects"
                    " WHERE turn_id=? AND effect_key=?",
                    (turn_id, effect_key),
                ).fetchone()
                if row:
                    conn.execute("COMMIT")
                    return json.loads(row["result_json"] or "null")
                result = fn()
                conn.execute(
                    "INSERT INTO durable_effects (turn_id, effect_key, result_json)"
                    " VALUES (?,?,?)",
                    (turn_id, effect_key, json.dumps(result, default=str)),
                )
                conn.execute("COMMIT")
                return result
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

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

    def _validate_against_journal(
        self, turn_id: str, steps: list[StepDef]
    ) -> dict[int, Any]:
        """Check the supplied step list against the journaled step rows.

        The journal is authoritative on resume (TW-665 review M1): a
        supplied step whose name or idempotency declaration disagrees
        with the row the original run journaled raises JournalMismatch
        instead of silently proceeding on the caller's word. Returns the
        journaled rows by step index for the run loop.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM durable_steps WHERE turn_id=? ORDER BY step_index",
                (turn_id,),
            ).fetchall()
        journaled: dict[int, Any] = {}
        for row in rows:
            idx = row["step_index"]
            journaled[idx] = row
            if idx >= len(steps):
                raise JournalMismatch(
                    f"turn {turn_id}: journal has step {idx} ({row['name']!r})"
                    f" but the supplied list has only {len(steps)} steps"
                )
            supplied = steps[idx]
            if supplied.name != row["name"] or bool(supplied.idempotent) != bool(
                row["idempotent"]
            ):
                raise JournalMismatch(
                    f"turn {turn_id}: step {idx} journaled as"
                    f" {row['name']!r} idempotent={bool(row['idempotent'])}"
                    f" but supplied as {supplied.name!r}"
                    f" idempotent={bool(supplied.idempotent)};"
                    " the journaled declaration governs"
                )
        return journaled

    def _run(self, turn_id: str, steps: list[StepDef], owner: str) -> dict[str, Any]:
        with self._conn() as c:
            turn = dict(
                c.execute(
                    "SELECT * FROM durable_turns WHERE turn_id=?", (turn_id,)
                ).fetchone()
            )
        if turn["status"] == "completed":
            # A completed turn is final. Re-running returns the stored
            # result WITHOUT flipping status or journaling a phantom
            # second turn_started / turn_completed pair (review m2).
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM durable_steps WHERE turn_id=? ORDER BY step_index",
                    (turn_id,),
                ).fetchall()
            stored: list[Any] = []
            for row in rows:
                if row["status"] == "completed":
                    stored.append(json.loads(row["result_json"] or "null"))
                elif row["status"] == "ambiguous":
                    stored.append({"interrupted_unknown_outcome": True})
            return {
                "status": "completed",
                "results": stored,
                "interrupted": turn["interrupted"] == 1,
            }
        # Refuse a step list that disagrees with the journal BEFORE any
        # state changes (review M1).
        self._validate_against_journal(turn_id, steps)
        with self._conn() as c:
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
            # The JOURNALED declaration decides this step's fate, never
            # the resuming caller's word (review M1). Validation above
            # has already proven supplied == journaled when a row
            # exists; reading the journal here keeps that true by
            # construction, not by trust.
            declared_idempotent = (
                bool(row["idempotent"]) if row else bool(step.idempotent)
            )
            if row and row["status"] == "started" and not declared_idempotent:
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
            # Refuse to execute if our lease died mid-run and a resumer
            # has taken over (review M3 ownership revalidation).
            self._assert_lease(turn_id, owner)
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
            ctx = StepContext(self, turn_id, step, owner=owner)

            def attempt() -> Any:
                # The journal's attempts column must state reality: one
                # increment per actual handler execution (review m1).
                with self._conn() as c:
                    c.execute(
                        "UPDATE durable_steps SET attempts=attempts+1"
                        " WHERE turn_id=? AND step_index=?",
                        (turn_id, idx),
                    )
                return step.handler(ctx)

            try:
                result = sh.retry_with_backoff(
                    attempt,
                    # A step that declared itself not safe to repeat gets
                    # exactly one attempt in-run too (review M2):
                    # retrying it on a transient classification repeats a
                    # side effect the declaration exists to protect.
                    attempts=self.retry_attempts if declared_idempotent else 1,
                    base_delay=self.base_delay,
                    max_delay=1.0,
                    sleep=self._sleep,
                )
            except TurnFenced:
                # Lease lost mid-step (raised via apply_effect): stop
                # without journaling a failure over the new owner's run.
                raise
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

        # Do not journal completion over a lease we no longer hold; the
        # journaled steps let the rightful owner finish the turn itself.
        self._assert_lease(turn_id, owner)
        with self._conn() as c:
            self._set_status(c, turn_id, "completed", interrupted=was_interrupted)
            self._event(c, turn_id, "turn_completed", {"steps": len(results)})
        return {
            "status": "completed",
            "results": results,
            "interrupted": was_interrupted,
        }
