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

import base64
import binascii
import re
import unicodedata
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
    # TW-828: the source label is caller-supplied and UNTRUSTED —
    # customer email can claim source="owner". Only the authenticated
    # intake wiring (Spec 8, TW-786) may set source_verified=True,
    # after it has verified the channel; the runtime treats an
    # unverified label as agent-initiated (most restrictive mode:
    # ask-first, quiet hours, attendant gating all apply).
    source_verified: bool = False
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

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)

# Cyrillic / Greek letters that render identically to Latin ones.
_HOMOGLYPHS: dict[str, str] = {
    "\u0430": "a",
    "\u0435": "e",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0445": "x",
    "\u0443": "y",
    "\u0456": "i",
    "\u0455": "s",
    "\u04bb": "h",
    "\u043a": "k",
    "\u043c": "m",
    "\u0442": "t",
    "\u0410": "A",
    "\u0415": "E",
    "\u041e": "O",
    "\u0420": "P",
    "\u0421": "C",
    "\u0425": "X",
    "\u0423": "Y",
    "\u0406": "I",
    "\u0405": "S",
    "\u039f": "O",
    "\u03bf": "o",
    "\u03b1": "a",
    "\u03c1": "p",
    "\u03c7": "x",
}


def _normalize_for_scan(text: str) -> str:
    """Fold the evasions away BEFORE pattern matching (TW-818):
    unicode compatibility forms, zero-width characters, Cyrillic /
    Greek homoglyphs, whitespace runs, and spaced-out letter runs
    ("i g n o r e" -> "ignore"). Matching then runs over the whole
    text, newlines included — a line break is not a hiding place."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_ZERO_WIDTH)
    text = "".join(_HOMOGLYPHS.get(ch, ch) for ch in text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(
        r"(?<![A-Za-z])(?:[A-Za-z] ){2,}[A-Za-z](?![A-Za-z])",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )
    return text


_GAP60 = r"[\s\S]{0,60}?"
_GAP40 = r"[\s\S]{0,40}?"
_GAP30 = r"[\s\S]{0,30}?"

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|overlook)\b"
            + _GAP60
            + r"\b(instructions?|directives?|guidelines|rules|everything|all)\b",
            re.I,
        ),
    ),
    (
        "override_rules",
        re.compile(
            r"\b(override|set aside|bypass|circumvent|suspend)\b"
            + _GAP40
            + r"\b(rules?|instructions?|guidelines|checks|restrictions?|safeguards?)\b",
            re.I,
        ),
    ),
    (
        "safety_bypass",
        re.compile(
            r"\b(skip|disable|turn off)\b"
            + _GAP30
            + r"\b(checks|verification|approvals?|safeguards?|restrictions?)\b",
            re.I,
        ),
    ),
    ("new_instructions", re.compile(r"\bnew\s+instructions?\s*:", re.I)),
    (
        "system_impersonation",
        re.compile(r"(^|[>\"']\s*|\s)system\s*(message|prompt)?\s*:", re.I),
    ),
    (
        # A send-shaped verb plus a SENSITIVE object plus a
        # destination. A bare "send X to Y" is ordinary shop talk
        # ("send the estimate to Mrs. Lee") and must not flag.
        "exfiltrate_send",
        re.compile(
            r"\b(send|forward|email|upload|post|transmit|leak)\b"
            + _GAP60
            + r"\b(customer|client|patient|employee|contact)?\s*"
            + r"(list|database|records?|roster|data|credentials|contacts)\b"
            + _GAP40
            + r"\b(to|at|@|https?://|out)\b",
            re.I,
        ),
    ),
    (
        "reveal_prompt",
        re.compile(r"\breveal\b" + _GAP40 + r"\b(prompt|instructions?|system)\b", re.I),
    ),
    (
        "encoded_payload",
        re.compile(
            r"\b(decode|decrypt|base64)\b"
            + _GAP30
            + r"\b(follow|execute|obey|run|instructions?)\b",
            re.I,
        ),
    ),
)

# Second pass over the text with ALL spacing removed, using
# substring (not word-boundary) patterns: a fully spaced-out
# payload ("i g n o r e  y o u r  i n s t r u c t i o n s")
# collapses into one long token where \b anchors never fire.
_SQUASH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"(ignore|disregard|forget|overlook)"
            + _GAP60
            + r"(instructions?|directives?|guidelines|rules|everything|all)",
            re.I,
        ),
    ),
    (
        "override_rules",
        re.compile(
            r"(override|setaside|bypass|circumvent|suspend)"
            + _GAP40
            + r"(rules?|instructions?|guidelines|checks|restrictions?|safeguards?)",
            re.I,
        ),
    ),
    ("new_instructions", re.compile(r"newinstructions?:", re.I)),
    ("system_impersonation", re.compile(r"system(message|prompt)?:", re.I)),
    (
        "exfiltrate_send",
        re.compile(
            r"(send|forward|email|upload|post|transmit|leak)"
            + _GAP60
            + r"(list|database|records?|roster|data|credentials|contacts)"
            + _GAP40
            + r"(to|at|@|https?://|out)",
            re.I,
        ),
    ),
    (
        "reveal_prompt",
        re.compile(r"reveal" + _GAP40 + r"(prompt|instructions?|system)", re.I),
    ),
)

_B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _decoded_texts(content: str) -> list[str]:
    """Base64-looking tokens in the content, decoded. An instruction
    does not stop being one because it was encoded first."""
    out: list[str] = []
    for token in _B64_TOKEN.findall(content or ""):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded and all(ch.isprintable() or ch in "\n\t" for ch in decoded):
            out.append(decoded)
    return out


def scan_injections(content: str) -> list[str]:
    """Names of instruction-shaped patterns found in agent-read
    content. The content itself is never returned, logged, or acted
    on — only the pattern names reach the audit feed.

    The scan normalizes first (TW-818: zero-width, homoglyph, and
    spacing evasions fold away), crosses line breaks, and also scans
    anything base64-decoded out of the content. Verb patterns are
    noun-bound on purpose: ordinary shop phrasing ("send the
    estimate to Mrs. Lee", "disregard the previous invoice") carries
    no sensitive object and stays unflagged."""
    text = _normalize_for_scan(content or "")
    found = [name for name, pat in _INJECTION_PATTERNS if pat.search(text)]
    squashed = text.replace(" ", "")
    for name, pat in _SQUASH_PATTERNS:
        if name not in found and pat.search(squashed):
            found.append(name)
    for decoded in _decoded_texts(content or ""):
        inner = _normalize_for_scan(decoded)
        hits = [name for name, pat in _INJECTION_PATTERNS if pat.search(inner)]
        for name in hits:
            if name not in found:
                found.append(name)
        if hits and "encoded_payload" not in found:
            found.append("encoded_payload")
    return found


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
