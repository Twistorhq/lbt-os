"""TW-949 Tori persistence: model, validation, masking helpers.

Vocabularies are pinned here and in the migration CHECK
constraints so the layers cannot drift (TW-768 cap-law style).
All semantic validation raises PersistenceError raw, via the
StrictBase pattern the neighboring ledgers use.

PII posture (medallion, TW-552): hash_body is the ingestion
hash; it is a KEYED hash (HMAC-SHA256, TW-949 fix round, Rex
finding F4): the key lives only in the server environment and
never in any stored row, so a hash that ships in a retrieval
projection cannot be dictionary-reversed against known
identifier shapes the way a bare SHA-256 can. mask_text builds
the masked retrieval projection. The raw body is stored by the
service in the bronze column only and is never part of a
projection dict (see service.message_projection).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import warnings
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


HASH_KEY_ENV = "TWISTOR_PERSISTENCE_HASH_KEY"

_ephemeral_hash_key: bytes | None = None


def _hash_key() -> bytes:
    """The keyed-hash key. Server environment only; never stored.

    Production (APP_ENV=production) fails closed when the key is
    unset: an ephemeral key would rotate every restart and an
    unsalted fallback would reopen the F4 oracle. Outside
    production a per-process ephemeral key keeps dev and test
    runs working while staying non-reversible to anyone who
    does not hold the key.
    """
    global _ephemeral_hash_key
    configured = os.environ.get(HASH_KEY_ENV)
    if configured:
        return configured.encode("utf-8")
    if os.environ.get("APP_ENV") == "production":
        raise PersistenceError(
            f"{HASH_KEY_ENV} is not set; refusing to hash PII bodies "
            "without the keyed-hash key in production"
        )
    if _ephemeral_hash_key is None:
        _ephemeral_hash_key = secrets.token_bytes(32)
        warnings.warn(
            f"{HASH_KEY_ENV} is not set; using an ephemeral per-process "
            "hash key. Stored hashes will not verify across restarts. "
            "Set the key before any production use.",
            stacklevel=2,
        )
    return _ephemeral_hash_key


def hash_body(body: str, *, org_id: str = "", agent_id: str = "") -> str:
    """Keyed ingestion hash of the raw body (HMAC-SHA256).

    The org and agent scope is domain-separated into the MAC so
    the same body in two scopes hashes differently. The returned
    value is safe to ship in retrieval projections: without the
    server key it cannot be reversed by dictionary attack, even
    when the body is a single short identifier (Rex F4).
    """
    msg = f"agt-body-v1\x00{org_id}\x00{agent_id}\x00{body}".encode("utf-8")
    digest = hmac.new(_hash_key(), msg, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OBFUSCATED_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+\s*(?:@|\[at\]|\(at\)|\bat\b)\s*"
    r"[A-Za-z0-9-]+(?:\s*(?:\.|\[dot\]|\(dot\)|\bdot\b)\s*|\s*\.\s*)"
    r"(?:com|net|org|io|co|gov|edu|us)\b",
    re.IGNORECASE,
)
_SSN_RE = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_DASH_DATE_RE = re.compile(r"\b\d{1,2}-\d{1,2}-\d{4}\b")
_MONTHS = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)
_MONTH_DATE_RE = re.compile(
    rf"\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\s+\d{{4}}\b",
    re.IGNORECASE,
)
_PO_BOX_RE = re.compile(r"\bP\.?\s*O\.?\s*Box\s+\d+\b", re.IGNORECASE)
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){1,4}?"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|"
    r"Court|Ct|Place|Pl|Way|Terrace|Ter|Circle|Cir|Highway|Hwy|"
    r"Parkway|Pkwy|Trail|Trl|Square|Sq)\b\.?",
    re.IGNORECASE,
)
_UEI_RE = re.compile(
    r"\b(?=[A-Z0-9]{12}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{12}\b"
)
_CAGE_RE = re.compile(
    r"\b(?=[A-Z0-9]{5}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5}\b"
)
_ZIP4_RE = re.compile(r"\b\d{5}-\d{4}\b")
_PAREN_PHONE_RE = re.compile(r"\(\d{3}\)\s*\d{3}[-. ]\d{4}\b")
_CONTIGUOUS_PHONE_RE = re.compile(r"\b(?:1\d{10}|\d{10})\b")
_SEVEN_DIGIT_PHONE_RE = re.compile(r"\b\d{3}[-. ]\d{4}\b")
_PHONE_RE = re.compile(r"\+?\d[\d .()-]{7,}\d")
_LONG_DIGITS_RE = re.compile(r"\d{9,}")
_BARE_SEVEN_RE = re.compile(r"\b\d{7}\b")
_BARE_FIVE_RE = re.compile(r"\b\d{5}\b")


def mask_text(text: str) -> str:
    """Masked retrieval projection of a raw body.

    Deterministic and conservative. Covers every identifier
    category the TW-552 doctrine names (TW-949 fix round, Rex
    finding F3): email addresses including obfuscated shapes,
    street addresses and PO boxes, dates (birth dates included),
    SSNs, EINs, phone numbers in full and 7-digit shapes, ZIP
    codes, SAM.gov UEI values and other business identifier
    shapes (long digit runs, CAGE-style codes). Ordinary words
    pass through so full-text search over the projection stays
    useful. Personal names are not regex-maskable reliably; that
    needs NER and is tracked outside this function.
    """
    masked = _EMAIL_RE.sub("[email]", text)
    masked = _OBFUSCATED_EMAIL_RE.sub("[email]", masked)
    masked = _SSN_RE.sub("[ssn]", masked)
    masked = _EIN_RE.sub("[ein]", masked)
    masked = _ISO_DATE_RE.sub("[date]", masked)
    masked = _SLASH_DATE_RE.sub("[date]", masked)
    masked = _DASH_DATE_RE.sub("[date]", masked)
    masked = _MONTH_DATE_RE.sub("[date]", masked)
    masked = _PO_BOX_RE.sub("[address]", masked)
    masked = _STREET_RE.sub("[address]", masked)
    masked = _UEI_RE.sub("[uei]", masked)
    masked = _CAGE_RE.sub("[id]", masked)
    masked = _ZIP4_RE.sub("[zip]", masked)
    masked = _PAREN_PHONE_RE.sub("[phone]", masked)
    masked = _CONTIGUOUS_PHONE_RE.sub("[phone]", masked)
    masked = _LONG_DIGITS_RE.sub("[number]", masked)
    masked = _PHONE_RE.sub("[phone]", masked)
    masked = _SEVEN_DIGIT_PHONE_RE.sub("[phone]", masked)
    masked = _BARE_SEVEN_RE.sub("[phone]", masked)
    masked = _BARE_FIVE_RE.sub("[number]", masked)
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
