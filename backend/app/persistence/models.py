"""TW-949 Tori persistence: model, validation, masking helpers.

Vocabularies are pinned here and in the migration CHECK
constraints so the layers cannot drift (TW-768 cap-law style).
All semantic validation raises PersistenceError raw, via the
StrictBase pattern the neighboring ledgers use.

PII posture (medallion, TW-552): hash_body is the ingestion
hash; mask_text builds the masked retrieval projection. The raw
body is stored by the service in the bronze column only and is
never part of a projection dict (see service.message_projection).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class PersistenceError(ValueError):
    """A persistence statement that cannot be represented. Fails loud."""


class DuplicateFireError(PersistenceError):
    """A fire identity that already exists. Never a silent no-op."""

    _twistor_permanent = True


class _StrictBase(BaseModel):
    """Surfaces EVERY construction failure as raw PersistenceError."""

    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except PersistenceError:
            raise
        except ValidationError as e:
            raise PersistenceError(str(e)) from e


SESSION_STATUSES: tuple[str, ...] = (
    "active",
    "completed",
    "compressed",
    "archived",
    "failed",
)
MESSAGE_ROLES: tuple[str, ...] = ("user", "assistant", "system", "tool")
JOB_STATUSES: tuple[str, ...] = ("active", "paused", "blocked", "archived")
FIRE_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "blocked",
)
STAGED_KINDS: tuple[str, ...] = ("memory", "skill")
STAGED_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "rejected",
    "applied",
    "refused_stale",
)


def workspace_id_for(org_id: str, agent_id: str) -> str:
    """The per-agent workspace key (B12 builds the file area on it)."""
    if not org_id or not agent_id:
        raise PersistenceError("workspace key requires org_id and agent_id")
    return f"{org_id}/{agent_id}"


def hash_body(body: str) -> str:
    """SHA-256 of the raw body, computed at ingestion."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d .()-]{7,}\d")
_LONG_DIGITS_RE = re.compile(r"\d{9,}")


def mask_text(text: str) -> str:
    """Masked retrieval projection of a raw body.

    Deterministic and conservative: email addresses, phone-shaped
    digit runs, and long digit runs (account and government id
    shapes) are replaced. Ordinary words pass through so full-text
    search over the projection stays useful.
    """
    masked = _EMAIL_RE.sub("[email]", text)
    masked = _PHONE_RE.sub("[phone]", masked)
    masked = _LONG_DIGITS_RE.sub("[number]", masked)
    return masked


class SessionCreate(_StrictBase):
    """A new session. parent_session_id is the lineage link."""

    channel: str = "chat"
    parent_session_id: str | None = None
    title: str = ""
    status: str = "active"

    def model_post_init(self, _ctx: Any) -> None:
        if not self.channel.strip():
            raise PersistenceError("channel must be non-empty")
        if self.status not in SESSION_STATUSES:
            raise PersistenceError(
                f"unknown session status {self.status!r}; want one of {SESSION_STATUSES}"
            )
        if self.parent_session_id is not None and not self.parent_session_id.strip():
            raise PersistenceError("parent_session_id must be non-empty when given")


class MessageAppend(_StrictBase):
    """One transcript message append."""

    role: str
    body: str

    def model_post_init(self, _ctx: Any) -> None:
        if self.role not in MESSAGE_ROLES:
            raise PersistenceError(
                f"unknown message role {self.role!r}; want one of {MESSAGE_ROLES}"
            )


class JobCreate(_StrictBase):
    """A persistent scheduled job definition (shape only in B1)."""

    name: str
    prompt: str
    skill_keys: list[str] = []
    cron_expr: str | None = None
    timezone: str = "UTC"
    status: str = "active"

    def model_post_init(self, _ctx: Any) -> None:
        if not self.name.strip():
            raise PersistenceError("job name must be non-empty")
        if not self.prompt.strip():
            raise PersistenceError("job prompt must be non-empty (self-contained)")
        if self.status not in JOB_STATUSES:
            raise PersistenceError(
                f"unknown job status {self.status!r}; want one of {JOB_STATUSES}"
            )
        if not self.timezone.strip():
            raise PersistenceError("job timezone must be non-empty (IANA name)")


class FireCreate(_StrictBase):
    """One durable fire of a job. fire_key is its durable identity."""

    fire_key: str
    scheduled_for: str

    def model_post_init(self, _ctx: Any) -> None:
        if not self.fire_key.strip():
            raise PersistenceError("fire_key must be non-empty (durable fire identity)")
        if not self.scheduled_for.strip():
            raise PersistenceError("scheduled_for must be non-empty")


class StagedWriteCreate(_StrictBase):
    """A proposed memory or skill write held for the approval gate."""

    kind: str
    target_key: str
    proposed_body: str
    target_ref: str | None = None
    pinned_target_hash: str | None = None

    def model_post_init(self, _ctx: Any) -> None:
        if self.kind not in STAGED_KINDS:
            raise PersistenceError(
                f"unknown staged write kind {self.kind!r}; want one of {STAGED_KINDS}"
            )
        if not self.target_key.strip():
            raise PersistenceError("staged write target_key must be non-empty")
