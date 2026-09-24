"""Internal product analytics — event emission (TW-209 Phase 1, Dre Coleman).

PRIVACY BOUNDARY (hard rule): usage metadata only — which features an org
uses, in which vertical, when. NEVER client business data: no job/quote/
invoice contents, no customer PII, no financial amounts, no free text.

Enforcement is structural, not advisory:
  * `feature_key` must match a dotted lowercase path (no arbitrary strings).
  * `context` accepts ONLY ALLOWED_CONTEXT_KEYS; everything else is dropped
    before the write, so a careless caller cannot leak PII by accident.
  * Key names containing PII-shaped fragments are rejected outright.
  * There is intentionally no free-text column anywhere in the analytics
    schema (see supabase/migration_analytics_events.sql).

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


def _key_is_blocked(key: str) -> bool:
    low = key.lower()
    return any(frag in low for frag in BLOCKED_KEY_FRAGMENTS)


def sanitize_context(context: dict | None) -> dict:
    """Strip context down to the allowlisted metadata keys.

    Drops unknown keys, PII-shaped keys, and overlong/non-scalar values.
    Pure function — safe to unit test without a database.
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
