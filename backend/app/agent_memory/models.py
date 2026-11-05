"""TW-951 agent memory: vocabularies, limits, error types.

Vocabularies are pinned here and in the migration CHECK
constraints so the layers cannot drift (the TW-768 cap-law
style B1 uses). All semantic validation raises MemoryError
raw, via the StrictBase pattern the neighboring ledgers use.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..persistence.models import PersistenceError


class MemoryError(PersistenceError):
    """A memory statement that cannot be represented. Fails loud."""


class DuplicateEntryError(MemoryError):
    """An entry key that already exists, live or inside a batch."""

    _twistor_permanent = True


class EntryNotFoundError(MemoryError):
    """An update or remove aimed at an entry that does not exist."""


class OverflowRefused(MemoryError):
    """The batch result would exceed a collection bound.

    Carries the refusal payload the contract requires: the
    current entries and the exact overflow, so the caller can
    show the owner precisely what did not fit. Nothing was
    written when this is raised.
    """

    _twistor_permanent = True

    def __init__(self, refusal: dict) -> None:
        self.refusal = refusal
        super().__init__(
            "batch result exceeds the collection bound: "
            f"{refusal['excess_entries']} excess entries, "
            f"{refusal['excess_chars']} excess chars"
        )


class ScreeningBlocked(MemoryError):
    """Content that may not be staged at all: a whole-secret
    write or injection-shaped text. Raised before staging."""

    _twistor_permanent = True


class StaleTargetError(MemoryError):
    """The live entry no longer matches the staged pin."""

    _twistor_permanent = True


class MemoryClaimError(MemoryError):
    """A memory claim that no valid receipt backs."""

    _twistor_permanent = True


class GatePermissionError(MemoryError):
    """A gate-setting change attempted by a non-owner actor."""

    _twistor_permanent = True


class _StrictBase(BaseModel):
    """Surfaces EVERY construction failure as raw MemoryError."""

    model_config = ConfigDict(extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except MemoryError:
            raise
        except ValidationError as e:
            raise MemoryError(str(e)) from e


COLLECTION_BUSINESS_FACTS = "business_facts"
COLLECTION_OWNER_PREFERENCES = "owner_preferences"
COLLECTIONS: tuple[str, ...] = (
    COLLECTION_BUSINESS_FACTS,
    COLLECTION_OWNER_PREFERENCES,
)

# Hard bounds per collection. Enforced on the batch result of
# a write (service._check_bounds), never by silent compaction:
# memory that does not fit is refused, not trimmed.
COLLECTION_LIMITS: dict[str, dict[str, int]] = {
    COLLECTION_BUSINESS_FACTS: {"max_entries": 50, "max_chars": 8000},
    COLLECTION_OWNER_PREFERENCES: {"max_entries": 30, "max_chars": 4000},
}

OP_ADD = "add"
OP_UPDATE = "update"
OP_REMOVE = "remove"
OPS: tuple[str, ...] = (OP_ADD, OP_UPDATE, OP_REMOVE)

GATE_GATED = "gated"
GATE_RELAXED = "relaxed"
GATE_MODES: tuple[str, ...] = (GATE_GATED, GATE_RELAXED)

EVENT_GATE_MODE_CHANGED = "memory_gate_mode_changed"

AUDIT_EVENTS: tuple[str, ...] = (
    "memory_write_applied",
    "memory_write_rejected",
    "memory_write_refused_stale",
    EVENT_GATE_MODE_CHANGED,
)

# The marker an inline secret is replaced with during screening.
REDACTION_MARKER = "[redacted-secret]"


class MemoryWriteOp(_StrictBase):
    """One operation inside a proposed batch."""

    op: str
    entry_key: str
    body: str = ""

    def model_post_init(self, _ctx: Any) -> None:
        if self.op not in OPS:
            raise MemoryError(f"unknown memory op {self.op!r}; want one of {OPS}")
        if not self.entry_key.strip():
            raise MemoryError("entry_key must be non-empty")
        if self.op in (OP_ADD, OP_UPDATE) and not self.body.strip():
            raise MemoryError(f"{self.op} requires a non-empty body")


def require_collection(collection: str) -> str:
    if collection not in COLLECTIONS:
        raise MemoryError(
            f"unknown memory collection {collection!r}; want one of {COLLECTIONS}"
        )
    return collection
