"""Internal product analytics — event emission (TW-209 Phase 1, Dre Coleman).

PRIVACY BOUNDARY (hard rule): usage metadata only — which features an org
uses, in which vertical, when. NEVER client business data: no job/quote/
invoice contents, no customer PII, no financial amounts.

Enforcement is structural, not advisory:
  * `feature_key` must match a dotted lowercase path (no arbitrary strings).
  * `context` accepts ONLY ALLOWED_CONTEXT_KEYS; everything else is dropped
    before the write. String VALUES are also screened: PII-shaped values
    (emails, blocked fragments) are dropped, and `metric`/`detector` values
    must match an identifier shape — so a careless caller cannot leak PII
    into this schema by accident.
  * TEXT columns and their caller contracts (see
    supabase/migration_analytics_events.sql):
      - `vertical`: leak-engine vertical from the org lookup (e.g. 'hvac').
      - `feature_key`: dotted lowercase path (validated by FEATURE_KEY_RE).
      - `actor_role`: enum-ish 'owner' | 'admin' | 'tech'; NEVER a user id.
      - `session_id`: opaque token only; PII-shaped values are rejected
        (never a login, email, or user id).
      - `context`: JSONB with allowlisted keys only (see below).
      - catalog `description`: maintainer-written reference text, never
        user input.

Reliability contract (TW-208 spirit): analytics must NEVER break the product
path. emit_event() catches everything, logs, and returns False on failure.
"""
import logging
import re

logger = logging.getLogger(__name__)

FEATURE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# The ONLY context keys that may be written. Metadata only:
# result/duration/source describe the interaction; metric/detector/ladder
# name allowlisted product concepts (never values from client data).
ALLOWED_CONTEXT_KEYS = frozenset({
    "result",       # 'ok' | 'error'
    "duration_ms",  # int — how long the feature took to serve
    "source",       # 'ui' | 'api'
    "metric",       # benchmark metric name (must come from the metric allowlist)
    "detector",     # detector name (registry-controlled)
    "ladder",       # question-ladder rung: 'happened' | 'will_happen' | 'should_do'
    "count",        # small integer counts (rows scanned, detectors run) — never money
})

# Belt and suspenders: any context KEY containing one of these fragments is
# rejected even if it somehow passed the allowlist above.
BLOCKED_KEY_FRAGMENTS = frozenset({
    "email", "phone", "ssn", "social", "card", "cvv", "address", "street",
    "city", "zip", "postal", "name", "dob", "birth", "password", "secret",
    "token", "apikey", "api_key", "license", "vin", "account",
})

MAX_CONTEXT_VALUE_LEN = 256
MAX_SESSION_ID_LEN = 128

# metric/detector values name allowlisted product concepts — they must look
# like identifiers, never like sentences or contact details.
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")


def _key_is_blocked(key: str) -> bool:
    low = key.lower()
    return any(frag in low for frag in BLOCKED_KEY_FRAGMENTS)


def _value_looks_like_pii(value: str) -> bool:
    """True if a string value smells like contact details or credentials."""
    low = value.lower()
    return "@" in value or any(frag in low for frag in BLOCKED_KEY_FRAGMENTS)


def _session_id_is_safe(session_id) -> bool:
    """Opaque tokens only — never a login, email, or user id."""
    return (
        isinstance(session_id, str)
        and 0 < len(session_id) <= MAX_SESSION_ID_LEN
        and not _value_looks_like_pii(session_id)
    )


def _context_value_is_safe(key: str, value: str) -> bool:
    """Screen context VALUES, not just keys (Rosa, TW-209 fix round).

    Every string value is rejected if it looks like PII; metric/detector
    values must additionally match identifier shape.
    """
    if _value_looks_like_pii(value):
        return False
    if key in ("metric", "detector") and not IDENTIFIER_RE.match(value):
        return False
    return True


def sanitize_context(context: dict | None) -> dict:
    """Strip context down to the allowlisted metadata keys.

    Drops unknown keys, PII-shaped keys, PII-shaped VALUES, overlong /
    non-scalar values. Pure function — safe to unit test without a database.
    """
    clean: dict = {}
    if not isinstance(context, dict):
        return clean
    for key, value in context.items():
        if not isinstance(key, str):
            continue
        if key not in ALLOWED_CONTEXT_KEYS:
            continue
        if _key_is_blocked(key):
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            if not _context_value_is_safe(key, value):
                logger.debug("analytics.sanitize_context dropped unsafe value for %r", key)
                continue
            clean[key] = value[:MAX_CONTEXT_VALUE_LEN]
        # dicts/lists/None/objects are never metadata — drop silently
    return clean


def emit_event(
    db,
    *,
    org_id: str,
    vertical: str,
    feature_key: str,
    actor_role: str | None = None,
    session_id: str | None = None,
    context: dict | None = None,
) -> bool:
    """Record one feature-usage event. Returns True on write, False otherwise.

    NEVER raises: analytics failures are logged and swallowed so the product
    path they instrument is never affected (TW-208 self-healing standard).
    """
    try:
        if not org_id or not vertical or not feature_key:
            logger.warning("analytics.emit_event dropped: missing org_id/vertical/feature_key")
            return False
        if not FEATURE_KEY_RE.match(feature_key):
            logger.warning("analytics.emit_event dropped: bad feature_key %r", feature_key)
            return False
        # Privacy-safe default: a PII-shaped session_id degrades to NULL
        # rather than being stored (or killing the event). When in doubt,
        # store less.
        if session_id is not None and not _session_id_is_safe(session_id):
            logger.warning("analytics.emit_event dropped PII-shaped session_id")
            session_id = None
        row = {
            "org_id": org_id,
            "vertical": vertical,
            "feature_key": feature_key,
            "actor_role": actor_role,
            "session_id": session_id,
            "context": sanitize_context(context),
        }
        db.table("analytics_feature_events").insert(row).execute()
        return True
    except Exception:  # noqa: BLE001 — analytics must never break the product path
        logger.warning("analytics.emit_event failed for %s", feature_key, exc_info=True)
        return False
