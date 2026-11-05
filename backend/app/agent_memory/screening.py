"""TW-951 agent memory: pre-staging screening.

The MemoryDefense pattern (Core learning_memory, echoed, not
imported), layered on the B1 masking pipeline:

- whole-secret content is BLOCKED: a body that is itself a
  credential (an API key, a private key block, a JWT, a bare
  high-entropy token) is not a memory, and redacting it would
  leave a useless entry while still staging the attempt.
- secret-shaped tokens inside ordinary prose are REDACTED
  inline and the write proceeds with the marker in place.
- injection-shaped text is BLOCKED: instruction-override
  phrasing has no legitimate form inside a curated memory
  entry, and a memory is future prompt context, so this is
  the prompt-injection surface of the whole feature.
- everything else passes to the B1 mask_text pipeline, which
  masks the TW-552 PII categories (emails, phones, SSNs,
  addresses, dates, business identifiers) at staging.

screen() returns the screened text or raises ScreeningBlocked.
It runs BEFORE staging (service.propose_batch) and again at
approval as defense in depth (service.approve_write).
"""

from __future__ import annotations

import re

from .models import REDACTION_MARKER, ScreeningBlocked

# -- whole-secret shapes: the body IS the secret -------------------

_WHOLE_SECRET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-(?:live|test|ant)?-?[A-Za-z0-9_-]{16,}$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{10,}$"),
    re.compile(r"^AIza[0-9A-Za-z_-]{30,}$"),
    # a JWT on its own
    re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"),
    # a password assignment as the entire content
    re.compile(r"^(?:password|passwd|pwd|secret|api[-_ ]?key)\s*[:=]\s*\S+$", re.I),
    # one bare high-entropy token, no prose around it
    re.compile(r"^[A-Za-z0-9+/=_-]{32,}$"),
)

_PRIVATE_KEY_MARKER = "PRIVATE KEY-----"

# -- inline secret tokens: redact and continue ---------------------

_INLINE_SECRET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-(?:live|test|ant)?-?[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S+"),
)

# -- injection shapes: block ----------------------------------------

_INJECTION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"ignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?)",
        re.I,
    ),
    re.compile(r"disregard\s+(?:the\s+)?(?:above|previous|prior|system)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|in)\b", re.I),
    re.compile(r"\bnew\s+(?:system\s+)?(?:prompt|instructions?)\s*:", re.I),
    re.compile(r"\bsystem\s+prompt\s*:", re.I),
    re.compile(r"</?\s*(?:system|instructions?)\s*>", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"\bdeveloper\s+mode\b", re.I),
    re.compile(
        r"override\s+(?:the\s+)?(?:owner|approval|gate|safety|permission)", re.I
    ),
)


def screen(text: str) -> str:
    """Screen one proposed memory body.

    Returns the screened text (inline secrets redacted) or
    raises ScreeningBlocked for whole-secret content and
    injection-shaped text. PII masking is NOT done here: the
    B1 staging call applies mask_text, and approval re-runs
    both layers.
    """
    stripped = text.strip()
    if _PRIVATE_KEY_MARKER in text:
        raise ScreeningBlocked("private key material is not a memory entry")
    for pattern in _WHOLE_SECRET_RES:
        if pattern.match(stripped):
            raise ScreeningBlocked(
                "the write is itself a secret; secrets do not belong in memory"
            )
    screened = text
    for pattern in _INLINE_SECRET_RES:
        screened = pattern.sub(REDACTION_MARKER, screened)
    for pattern in _INJECTION_RES:
        if pattern.search(screened):
            raise ScreeningBlocked("injection-shaped text is refused in curated memory")
    return screened
