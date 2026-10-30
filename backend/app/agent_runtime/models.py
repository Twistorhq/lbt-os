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
import html
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import unquote

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
    text, newlines included — a line break is not a hiding place.

    Round 2 (Rex run-2): HTML entities are unescaped first
    ("&#105;gnore" -> "ignore"), combining marks are stripped via
    NFKD + a category-Mn filter ("i" + U+0301 -> "i"), and letter
    runs joined by any separator (".", "-", "/", "_") collapse
    ("i.g.n.o.r.e" -> "ignore").

    Round 3 (Rex run-3): unescape and percent-decode run to a
    fixpoint (max 3 passes), so double HTML entities
    ("&amp;#105;gnore") and percent-encoded letters ("%69gnore")
    fold to plain text before matching."""
    text = text or ""
    for _ in range(3):
        folded = unquote(html.unescape(text))
        if folded == text:
            break
        text = folded
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_ZERO_WIDTH)
    text = "".join(_HOMOGLYPHS.get(ch, ch) for ch in text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(
        r"(?<![A-Za-z])(?:[A-Za-z] ){2,}[A-Za-z](?![A-Za-z])",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z])(?:[A-Za-z][.\-/_]){2,}[A-Za-z](?![A-Za-z])",
        lambda m: re.sub(r"[.\-/_]", "", m.group(0)),
        text,
    )
    return text


_GAP60 = r"[\s\S]{0,60}?"
_GAP40 = r"[\s\S]{0,40}?"
_GAP30 = r"[\s\S]{0,30}?"

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        # Round 3 (Rex run-3): the override family widens past the
        # imperative verbs — instruction-NEGATION phrasings ("do not
        # follow the instructions you were given", "stop following
        # your instructions", "never follow", "do not obey"),
        # "unlearn", and the act-as-if erasure ("act as if you never
        # received any instructions") are the same instruction to
        # drop the owner's instructions, in plain English.
        "ignore_instructions",
        re.compile(
            r"(\b(ignore|disregard|forget|overlook|disobey|unlearn"
            r"|pay\s+no\s+attention)\b"
            + _GAP60
            + r"\b(instructions?|directives?|guidelines|rules|everything|all)\b"
            r"|\b(do\s+not|don'?t|never|stop|cease)\s+"
            r"(follow|following|obey|obeying)\b"
            + _GAP40
            + r"\b(instructions?|directives?|guidelines|orders|rules)\b"
            r"|\bact\s+as\s+if\b" + _GAP60 + r"\binstructions?\b)",
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
        # Header-shaped system claims only: a bracketed or quoted
        # header, or "system message/prompt:". The bare noun must
        # NOT flag — "The system: check the HVAC system" is shop
        # talk (Rex run-2 benign FP).
        "system_impersonation",
        re.compile(
            r"(\[\s*system\s*\]|<<\s*sys(tem)?\s*>>|#{2,}\s*system\b"
            r"|\bsystem\s+(message|prompt)\s*:|(^|[>\"']\s*)system\s*:)",
            re.I,
        ),
    ),
    (
        # A send-shaped verb plus a SENSITIVE object plus a
        # destination. A bare "send X to Y" is ordinary shop talk
        # ("send the estimate to Mrs. Lee") and must not flag; the
        # object is noun-bound to qualified sensitive phrases
        # ("customer list", "contacts database", "the list",
        # "the data", a bare database/roster/credentials/contacts)
        # so "send the parts list to the supplier" stays clean
        # (Rex run-2: verb roster widened — exfiltrate, ship,
        # text, message, mail — the noun bound is what keeps
        # benign sends clean).
        "exfiltrate_send",
        re.compile(
            r"\b(send|forward|email|mail|upload|post|transmit|leak"
            r"|exfiltrate|ship|text|message|give|share|copy|dump"
            r"|hand\s+over)\b"
            + _GAP60
            + r"\b((customer|client|patient|employee|contacts?|personal)\s+"
            + r"(list|database|records?|roster|data|contacts?"
            + r"|phone\s+numbers?|email\s+addresses|contact\s+details)"
            + r"|the\s+(list|data)|database|roster|credentials|contacts"
            + r"|phone\s+numbers?|email\s+addresses|contact\s+details)\b"
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
        # "follow" does not match the phrasal verb "follow up":
        # "decode the VIN plate photo and follow up with the
        # customer" is shop talk, not an encoded payload (Rex run-3
        # benign FP, fixed at the pattern level).
        "encoded_payload",
        re.compile(
            r"\b(decode|decrypt|base64)\b"
            + _GAP30
            + r"\b(follow(?!\s+up\b)|execute|obey|run|instructions?)\b",
            re.I,
        ),
    ),
)

# Second pass over the text with ALL non-alphanumerics removed,
# using substring (not word-boundary) patterns: a fully spaced-out
# or separator-joined payload ("i g n o r e ...", "i.g.n.o.r.e",
# "ign_ore") collapses into one long token where \b anchors never
# fire. The exfiltration nouns are the qualified sensitive phrases
# in squashed form, for the same benign reason as the main pass.
_SQUASH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"(ignore|disregard|forget|overlook|disobey|unlearn"
            r"|paynoattention|donotfollow|donotobey|neverfollow"
            r"|stopfollowing|actasif)"
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
    (
        # Destination set drops "out" in the squashed pass: with no
        # word boundaries, "out" substring-matches inside "about"
        # ("message the whole contacts list about the holiday
        # hours" — Rex run-3 benign FP). "send the roster out" is
        # still caught by the main pass, which has real boundaries.
        "exfiltrate_send",
        re.compile(
            r"(send|forward|email|mail|upload|post|transmit|leak"
            r"|exfiltrate|ship|text|message|give|share|copy|dump"
            r"|handover)"
            + _GAP60
            + r"(customerlist|clientlist|patientlist|employeelist"
            r"|contactsdatabase|customerdatabase|clientdatabase"
            r"|customerdata|clientdata|clientroster|customerroster"
            r"|customerphonenumbers|clientphonenumbers|phonenumbers"
            r"|emailaddresses|contactdetails"
            r"|thelist|thedata|database|roster|credentials|contacts)"
            + _GAP40
            + r"(to|at|https?)",
            re.I,
        ),
    ),
    (
        "reveal_prompt",
        re.compile(r"reveal" + _GAP40 + r"(prompt|instructions?|system)", re.I),
    ),
)

# Header patterns keep their colon, so they run over the
# space-only squash (where ":" survives), never the alnum squash.
# A bare squashed "system:" is excluded for the same benign reason
# as the main pass.
_SQUASH_HEADER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("new_instructions", re.compile(r"newinstructions?:", re.I)),
    ("system_impersonation", re.compile(r"system(message|prompt):", re.I)),
)

_LEET_TABLE = str.maketrans("103457", "ioeast")
_ROT13_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
)


def _squash_variants(squashed: str) -> list[str]:
    """Deterministic decode passes over the squashed form (TW-818
    round 2): the text itself, leet-folded, ROT13-decoded, and
    reversed. Each is cheap and total; a payload written in any of
    these forms reads as plain English in exactly one variant."""
    return [
        squashed,
        squashed.translate(_LEET_TABLE),
        squashed.translate(_ROT13_TABLE),
        squashed[::-1],
    ]


_B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_HEX_TOKEN = re.compile(r"\b[0-9a-fA-F]{20,}\b")


def _decoded_texts(content: str) -> list[str]:
    """Base64-looking and hex-looking tokens in the content,
    decoded. An instruction does not stop being one because it was
    encoded first (round 3: hex joins base64 — one decode pass
    each, deterministic, no dependencies)."""
    out: list[str] = []
    for token in _B64_TOKEN.findall(content or ""):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded and all(ch.isprintable() or ch in "\n\t" for ch in decoded):
            out.append(decoded)
    for token in _HEX_TOKEN.findall(content or ""):
        try:
            raw = bytes.fromhex(token[: len(token) // 2 * 2])
            decoded = raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if decoded and all(ch.isprintable() or ch in "\n\t" for ch in decoded):
            out.append(decoded)
    return out


# -- document-level signal combination (TW-818 round 3) -------------------
#
# The proximity patterns above have bounded windows by design; a
# payload split by ~110 characters of ordinary prose walks between
# them (Rex run-3 GAP probes). The rule that catches it without
# flagging innocent prose is a COMBINATION, stated plainly:
#
#   a sensitive noun (customer list, phone numbers, roster, ...)
#   AND a strong destination (an email address, a URL, or a phone
#   number — not a bare "to"/"at", which innocent sentences carry)
#   AND a signal verb: an exfiltration verb anywhere in the text,
#   OR an override phrase anywhere in the text.
#
# The noun + strong-destination pair is the false-positive guard:
# a document that merely mentions a list and an email address in
# separate innocent sentences (no send-shaped verb, no override)
# stays clean, as does every benign send whose object is not a
# sensitive noun ("send the parts list to the supplier"). The
# honest tradeoff: a benign document that DOES combine all three
# signals across sentences (a send verb, a sensitive noun, and an
# address, each innocent in isolation) will flag — safe direction,
# and the flag is owner visibility, not an action.
_SENSITIVE_NOUN_DOC = re.compile(
    r"\b((customer|client|patient|employee|contacts?|personal)\s+"
    r"(list|database|records?|roster|data|contacts?"
    r"|phone\s+numbers?|email\s+addresses|contact\s+details)"
    r"|the\s+(list|data)|database|roster|credentials|contacts"
    r"|phone\s+numbers?|email\s+addresses|contact\s+details)\b",
    re.I,
)
_STRONG_DESTINATION = re.compile(
    r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+"
    r"|https?://\S+|\b\d{3}[-.\s]\d{4}\b)",
    re.I,
)
_EXFIL_VERB_DOC = re.compile(
    r"\b(send|forward|email|mail|upload|post|transmit|leak"
    r"|exfiltrate|ship|text|message|give|share|copy|dump"
    r"|hand\s+over)\b",
    re.I,
)
_OVERRIDE_FLAG_NAMES = frozenset(
    {
        "ignore_instructions",
        "override_rules",
        "safety_bypass",
        "new_instructions",
        "system_impersonation",
    }
)


def scan_injections(content: str) -> list[str]:
    """Names of instruction-shaped patterns found in agent-read
    content. The content itself is never returned, logged, or acted
    on — only the pattern names reach the audit feed.

    The scan normalizes first (TW-818: zero-width, homoglyph, and
    spacing evasions fold away; round 2 adds HTML entities,
    combining marks, and separator-joined letters; round 3 runs
    unescape + percent-decode to a fixpoint and widens the plain-
    English override/exfiltration families), crosses line
    breaks, scans leet / ROT13 / reversed decode passes over the
    squashed form, scans anything base64- or hex-decoded out of
    the content, and applies the document-level signal
    combination stated above the scan helpers. Verb patterns are
    noun-bound on purpose: ordinary shop phrasing ("send the
    estimate to Mrs. Lee", "disregard the previous invoice") carries
    no sensitive object and stays unflagged."""
    text = _normalize_for_scan(content or "")
    found = [name for name, pat in _INJECTION_PATTERNS if pat.search(text)]
    squashed = re.sub(r"[^A-Za-z0-9]", "", text)
    for variant in _squash_variants(squashed):
        for name, pat in _SQUASH_PATTERNS:
            if name not in found and pat.search(variant):
                found.append(name)
    for name, pat in _SQUASH_HEADER_PATTERNS:
        if name not in found and pat.search(text.replace(" ", "")):
            found.append(name)
    if "exfiltrate_send" not in found:
        if (
            _SENSITIVE_NOUN_DOC.search(text)
            and _STRONG_DESTINATION.search(text)
            and (
                _EXFIL_VERB_DOC.search(text) or _OVERRIDE_FLAG_NAMES.intersection(found)
            )
        ):
            found.append("exfiltrate_send")
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
