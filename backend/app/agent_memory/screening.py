"""TW-951 agent memory: pre-staging screening.

The MemoryDefense pattern (Core learning_memory, echoed, not
imported), layered on the B1 masking pipeline:

- whole-secret content is BLOCKED: a body that is itself a
  credential (an API key, a private key block, a JWT, a bare
  high-entropy token) is not a memory, and redacting it would
  leave a useless entry while still staging the attempt.
- secret-shaped tokens inside ordinary prose are REDACTED
  inline and the write proceeds with the marker in place.
  The inline layer covers the forms the Rex pass (TW-951 fix
  round, F3) proved persisted unredacted before: AWS secret
  keys in prose, credential assignments with ':' '=' or
  'is', credential-bearing URLs and connection strings,
  bare hex and generic high-entropy tokens in a sentence,
  headerless private-key base64 (blocked as a block), PEM
  headers split across lines (blocked), and IBANs. Generic
  token detection is format-drop-aware (round 3): a token
  laced with a single invisible character is detected as
  its stripped form and redacted whole, so it is no more
  recoverable from stored content than its plain form.
- injection-shaped and steering text is BLOCKED: classic
  instruction-override phrasing, and (F4) directive phrasing
  that reads as a standing order to the agent (always-approve
  orders, from-now-on rules, concealment directives aimed at
  the owner, claims of pre-approval). Detection runs on a
  normalized copy with every Unicode format character
  (category Cf), the combining grapheme joiner, and every
  variation selector (U+FE00-U+FE0F, U+E0100-U+E01EF)
  dropped and whitespace collapsed, so evasion by invisible
  characters does not work. The squashed-PEM and
  base64-block checks run on the same format-dropped copy
  (line structure preserved), so invisible-character lacing
  cannot defeat them either. Visible combining marks are
  not dropped: they change the grapheme and are the TW-961
  homoglyph scope. A memory is future prompt
  context, so this is the prompt-injection surface of the
  whole feature. The residual semantic layer is TW-961,
  not this module.
- entry keys are screened too (F1): screen_key blocks any
  secret, injection, or steering shape in a key outright;
  a blocked shape is never redacted into a different key.
  PII-shaped substrings are a separate layer: after
  screening, service passes the slug through the B1 masking
  pipeline, so a digit run inside a key is stored masked.
- everything else passes to the B1 mask_text pipeline, which
  masks the TW-552 PII categories (emails, phones, SSNs,
  addresses, dates, business identifiers) at staging.

screen() returns the screened text or raises ScreeningBlocked.
It runs BEFORE staging (service.propose_batch) and again at
approval as defense in depth (service.approve_write).
"""

from __future__ import annotations

import re
import unicodedata

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

# A PEM header, including one split across lines by injected
# whitespace or line breaks (detection runs on a squashed copy
# with whitespace and hyphens removed).
_SQUASHED_PRIVATE_KEY_MARKERS = ("BEGINPRIVATEKEY", "ENDPRIVATEKEY")

_BASE64_LINE_RE = re.compile(r"^[A-Za-z0-9+/=]{16,}$")

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
    # credential assignment with ':' or '=' (api_key = ..., secret: ...)
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[-_ ]?key|access[-_ ]?key|"
        r"auth[-_ ]?token|token)\s*[:=]\s*\S+"
    ),
    # credential assignment with 'is' (the password is ...)
    re.compile(
        r"(?i)\b(?:secret\s+access\s+key|access\s+key|password|passwd|pwd|"
        r"secret|api[-_ ]?key)\s+is\s+\S+"
    ),
    # an IBAN in prose
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
)

# Credential-bearing URL or connection string userinfo:
# scheme://user:password@host becomes scheme://[marker]@host.
_URL_USERINFO_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")

_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32,}$")
_IBAN_TOKEN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_TOKEN_CHARSET_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_TOKEN_STRIP_CHARS = ",.;:!?()\"'"


def _token_classes(token: str) -> int:
    classes = 0
    if any(c.islower() for c in token):
        classes += 1
    if any(c.isupper() for c in token):
        classes += 1
    if any(c.isdigit() for c in token):
        classes += 1
    if any(c in "+/=_-" for c in token):
        classes += 1
    return classes


def _redact_generic_tokens(text: str) -> str:
    """Redact bare high-entropy tokens sitting inside prose:
    long hex strings, IBAN-shaped tokens, and base64-shaped
    tokens whose character mix no ordinary word has. Ordinary
    words, however long, are single-class and pass through.

    Detection runs on the format-dropped copy of the token
    (Rex re-probe round 3, TW-951): a single variation
    selector or format character laced inside a token breaks
    its raw charset, and on raw text alone the token passed
    screening unredacted and stayed strip-recoverable from
    stored content. The dropped copy is throwaway and only
    decides; on a hit the WHOLE raw token, lacing included,
    is replaced by the marker, exactly the treatment its
    plain form gets, so nothing recoverable is stored and
    benign content is never rewritten by the drop."""

    def _fix(match: re.Match[str]) -> str:
        token = match.group(0)
        core = token.strip(_TOKEN_STRIP_CHARS)
        if not core:
            return token
        dropped = _drop_format_chars(core)
        suspicious = (
            bool(_HEX_TOKEN_RE.match(dropped))
            or bool(_IBAN_TOKEN_RE.match(dropped))
            or (
                len(dropped) >= 20
                and bool(_TOKEN_CHARSET_RE.match(dropped))
                and _token_classes(dropped) >= 3
            )
            or (
                len(dropped) >= 40
                and bool(_TOKEN_CHARSET_RE.match(dropped))
                and _token_classes(dropped) >= 2
            )
        )
        if not suspicious:
            return token
        return token.replace(core, REDACTION_MARKER, 1)

    return re.sub(r"\S+", _fix, text)


# -- injection and steering shapes: block ----------------------------

_INJECTION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"ignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?|directions?|guidance)",
        re.I,
    ),
    re.compile(
        r"ignore\s+(?:all\s+|the\s+)?(?:instructions?|prompts?|rules?|"
        r"directions?|guidance)\s+(?:above|previous|prior|earlier)",
        re.I,
    ),
    re.compile(r"disregard\s+(?:the\s+)?(?:above|previous|prior|system)", re.I),
    re.compile(
        r"disregard\s+(?:\w+\s+){0,3}?(?:above|previous|prior|instructions?|"
        r"rules?|directions?)",
        re.I,
    ),
    re.compile(r"you\s+are\s+now\s+(?:a|an|in)\b", re.I),
    re.compile(r"\bnew\s+(?:system\s+)?(?:prompt|instructions?)\s*:", re.I),
    re.compile(r"\bsystem\s+prompt\s*:", re.I),
    re.compile(r"</?\s*(?:system|instructions?)\s*>", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"\bdeveloper\s+mode\b", re.I),
    re.compile(
        r"override\s+(?:the\s+)?(?:owner|approval|gate|safety|permission)", re.I
    ),
    re.compile(r"\bfollow\s+this\s+(?:note|entry|instruction|directive)\b", re.I),
)

# Standing-directive phrasing (F4): text whose only function in
# a memory entry is to steer the agent's future behavior, or to
# conceal behavior from the owner, or to claim the owner's
# approval in advance. Curated entries state facts; directives
# addressed to the agent do not belong in them.
_DIRECTIVE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\balways\s+(?:approve|refund|quote|discount|route|call|tell|give|"
        r"offer|send|charge|accept)\b",
        re.I,
    ),
    re.compile(r"\bfrom\s+now\s+on\b", re.I),
    re.compile(r"\bnever\s+(?:mention|tell|inform|notify|disclose|reveal|ask)\b", re.I),
    re.compile(r"\bwithout\s+asking\s+(?:the\s+)?owner\b", re.I),
    re.compile(
        r"\bdo\s+not\s+(?:tell|inform|mention|notify|ask)\s+(?:the\s+)?owner\b",
        re.I,
    ),
    re.compile(r"\bdon'?t\s+(?:tell|inform|mention|notify)\s+(?:the\s+)?owner\b", re.I),
    re.compile(r"\bpre[-\s]?approved\b", re.I),
    re.compile(r"\bhas\s+(?:already\s+)?approved\s+all\b", re.I),
    re.compile(r"\btreat\s+.{0,40}?\s+as\s+approved\b", re.I),
    re.compile(
        r"\bremember\s+to\s+(?:route|approve|always|never|quote|say|tell|"
        r"send|call)\b",
        re.I,
    ),
    re.compile(r"\bstanding\s+(?:instruction|directive|order)s?\b", re.I),
    re.compile(r"\bevery\s+time\s+(?:a\s+)?(?:customer|caller|client)\b", re.I),
)

# Characters dropped from the detection copy on top of the
# category-Cf strip: U+034F COMBINING GRAPHEME JOINER (category
# Mn, a combining joiner) and U+180E MONGOLIAN VOWEL SEPARATOR
# (Cf in the Unicode data this runtime ships, Mn in some other
# versions, so it is named explicitly rather than trusted to
# the category table).
_DETECTION_DROP_EXTRA = frozenset({chr(0x034F), chr(0x180E)})

# Variation selector ranges dropped from the detection copy
# (Rex re-retest round 2, TW-951): U+FE00-U+FE0F and
# U+E0100-U+E01EF. Selectors are category Mn but zero-width and
# rendering-preserving on Latin text, so they are the same
# invisible-character evasion mechanism as the Cf set, not the
# visible combining marks (U+0300-class accents change the
# grapheme and stay, per the TW-961 homoglyph split). Dropping
# them is safe for legitimate text: the drop applies only to
# the throwaway detection copy, so an emoji presentation
# selector in stored prose is never rewritten.
_VS_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))


def _is_detection_dropped(ch: str) -> bool:
    if unicodedata.category(ch) == "Cf" or ch in _DETECTION_DROP_EXTRA:
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _VS_RANGES)


def _drop_format_chars(text: str) -> str:
    """Drop every Unicode format character (category Cf), the
    combining joiners named above, and every variation
    selector. The old strip list named only U+200B, U+200C,
    U+200D, and U+FEFF; Rex's retest (TW-951) proved U+2060,
    U+00AD, U+061C, U+200E, U+200F, U+2064, U+180E, and U+034F
    each defeated directive detection when planted inside a
    keyword, and his re-retest proved the variation selectors
    U+FE0F, U+FE00, U+E0100, and U+E01EF did the same."""
    return "".join(ch for ch in text if not _is_detection_dropped(ch))


def _normalize_for_detection(text: str) -> str:
    """The copy block-detection runs on: format characters
    dropped, all whitespace runs collapsed to single spaces.
    This copy is throwaway; stored content is never rewritten
    by normalization."""
    return re.sub(r"\s+", " ", _drop_format_chars(text))


def _is_base64_block(text: str) -> bool:
    """Two or more lines that are nothing but base64, totalling
    real key material: headerless private-key bodies (F3)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if not all(_BASE64_LINE_RE.match(line) for line in lines):
        return False
    return sum(len(line) for line in lines) >= 64


def _check_blocked(text: str) -> None:
    """Raise ScreeningBlocked for whole-secret, private-key,
    injection, or steering shapes in text (detection copy).

    The squashed-PEM and base64-block checks run on the
    format-dropped copy, not the raw text (Rex re-retest round
    2, TW-951): on raw text, a word joiner or variation
    selector laced into every base64 line failed the line
    charset check and the block sailed through, recoverable
    from the snapshot by stripping the invisible characters.
    The dropped copy keeps line structure, so both checks see
    exactly what an attacker recovers by stripping."""
    detection = _drop_format_chars(text)
    squashed = re.sub(r"[\s-]+", "", detection).upper()
    if any(marker in squashed for marker in _SQUASHED_PRIVATE_KEY_MARKERS):
        raise ScreeningBlocked("private key material is not a memory entry")
    if _is_base64_block(detection):
        raise ScreeningBlocked("base64 key material is not a memory entry")
    stripped = text.strip()
    normalized = _normalize_for_detection(text).strip()
    for pattern in _WHOLE_SECRET_RES:
        if pattern.match(stripped) or pattern.match(normalized):
            raise ScreeningBlocked(
                "the write is itself a secret; secrets do not belong in memory"
            )
    for pattern in _INJECTION_RES + _DIRECTIVE_RES:
        if pattern.search(normalized):
            raise ScreeningBlocked("injection-shaped text is refused in curated memory")


def screen(text: str) -> str:
    """Screen one proposed memory body.

    Returns the screened text (inline secrets redacted) or
    raises ScreeningBlocked for whole-secret content,
    private-key material, and injection or steering text.
    PII masking is NOT done here: the B1 staging call applies
    mask_text, and approval re-runs both layers.
    """
    _check_blocked(text)
    screened = text
    for pattern in _INLINE_SECRET_RES:
        screened = pattern.sub(REDACTION_MARKER, screened)
    screened = _URL_USERINFO_RE.sub(r"\1" + REDACTION_MARKER + "@", screened)
    screened = _redact_generic_tokens(screened)
    # Redaction joins nothing new, but re-check the result so a
    # shape assembled across a redaction boundary cannot slip.
    _check_blocked(screened)
    return screened


def screen_key(entry_key: str) -> str:
    """Screen one proposed entry key (F1).

    Keys persist into staged target keys, audit rows, and
    snapshot keys, so they get the full block treatment, and
    any inline-secret shape blocks outright: a key is never
    silently redacted into a different key. Returns the key
    unchanged when it is clean.
    """
    _check_blocked(entry_key)
    for pattern in _INLINE_SECRET_RES:
        if pattern.search(entry_key):
            raise ScreeningBlocked("entry keys may not carry secret-shaped material")
    if _redact_generic_tokens(entry_key) != entry_key:
        raise ScreeningBlocked("entry keys may not carry secret-shaped material")
    return entry_key
