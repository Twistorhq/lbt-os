"""Self-healing pipeline primitives (TW-208).

Standing rule: one error never crushes the full process. Every pipeline in
lbt-os — ingestion, detectors, benchmarks, briefs, notifications — builds on
these three primitives:

- per-item fault isolation (`run_isolated`): a poison row/record fails alone,
  never its batch;
- transient retries with backoff (`retry_with_backoff`): DB blips and network
  flakes heal themselves instead of failing the run;
- dead-letter capture (`DeadLetterQueue`): poison items are logged loudly with
  their index and reason, and callers persist the summary where the morning
  standup can read it (csv_import_logs.details for imports,
  brief["data_status"] for scans).

Health checks: `health_check(db, tables)` probes the tables a pipeline needs
and reports per-table reachability — the cheap, honest pre-flight the leak
engine runs before every scan.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("twistor.self_healing")

# Substrings that mark an exception as transient (worth retrying). Matched
# against both the exception class name and its message, lowercased.
_TRANSIENT_KEYWORDS = (
    "timeout", "timed out", "temporary", "temporarily",
    "connection reset", "connection refused", "connection aborted",
    "connection timed out", "network", "socket", "econn",
    "service unavailable", "too many requests", "rate limit", "ratelimit",
    "throttl", "deadlock", "lock timeout",
)
# HTTP status codes match only as standalone tokens ("503" yes, "15035" no).
# Heuristic limit, documented: a message like "Row 503 invalid" still matches
# and will be retried as transient. That errs toward retrying, which is the
# safe direction for a self-healing pipeline — a wasted retry is just slower,
# while a dropped batch is silent data loss.
_TRANSIENT_CODE_RE = re.compile(r"\b(?:429|502|503|504)\b")

# Email/phone-shaped values are redacted from DLQ previews so row PII never
# lands in logs or the import-log JSONB. Previews are debugging samples, not
# primary data — over-redaction here is the correct bias.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
_PII_KEYS = {"email", "phone", "phone_number", "mobile", "cell",
             "email_address"}


def is_transient(exc: BaseException) -> bool:
    """True when the failure looks transient (worth a retry with backoff)."""
    if getattr(exc, "_twistor_permanent", False):
        return False
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "network", "socket",
                               "temporary", "unavailable", "throttl", "ratelimit")):
        return True
    msg = str(exc).lower()
    if _TRANSIENT_CODE_RE.search(msg):
        return True
    return any(k in msg for k in _TRANSIENT_KEYWORDS)


def mark_permanent(exc: BaseException) -> BaseException:
    """Opt an exception out of retry classification (e.g. validated poison)."""
    exc._twistor_permanent = True  # noqa: SLF001 - intentional marker
    return exc


def retry_with_backoff(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] | None = None,
    on_retry: Callable[[BaseException, int, float], None] | None = None,
) -> Any:
    """Run fn(); retry transient failures with exponential backoff + jitter.

    Permanent failures raise immediately — retrying a poison record is just
    a slower way to fail. After `attempts` total tries the last error raises.
    The jitter multiplier (0.5x-1.5x) is applied before the `max_delay` cap,
    so `max_delay` is a true ceiling on every sleep.
    """
    if sleep is None:
        sleep = time.sleep
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classification decides
            if not is_transient(exc) or i >= attempts - 1:
                raise
            delay = min(max_delay, base_delay * (2 ** i) * (0.5 + random.random()))
            log.warning(
                "self-healing: transient failure (%s), retry %d/%d in %.2fs: %s",
                type(exc).__name__, i + 1, attempts - 1, delay, exc,
            )
            if on_retry is not None:
                on_retry(exc, i + 1, delay)
            sleep(delay)


@dataclass
class SkippedItem:
    """One poison item, captured loudly instead of killing its batch."""
    index: int
    preview: str
    error: str


def _redact_value(value: Any) -> Any:
    if isinstance(value, str) and (_EMAIL_RE.search(value) or _PHONE_RE.search(value)):
        return "[redacted]"
    return value


def _preview(item: Any, limit: int = 120) -> str:
    try:
        if isinstance(item, dict):
            item = {
                k: ("[redacted]" if str(k).lower() in _PII_KEYS else _redact_value(v))
                for k, v in item.items()
            }
        text = str(item)
    except Exception:  # noqa: BLE001 - preview must never raise
        text = f"<unprintable {type(item).__name__}>"
    return text[:limit]


def run_isolated(
    items: list[Any],
    fn: Callable[[Any], Any],
    *,
    preview: Callable[[Any], str] | None = None,
) -> tuple[list[Any], list[SkippedItem]]:
    """Run fn(item) for each item; a failing item is skipped loudly.

    Returns (results, skipped). Never raises for item-level errors — the
    caller decides what "done" means and reports the skips.
    """
    preview_fn = preview or _preview
    results: list[Any] = []
    skipped: list[SkippedItem] = []
    for i, item in enumerate(items):
        try:
            results.append(fn(item))
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            err = f"{type(exc).__name__}: {exc}"
            text = preview_fn(item)
            skipped.append(SkippedItem(index=i, preview=text, error=err))
            log.warning("self-healing: skipped item %d (%s): %s", i, text, err)
    return results, skipped


@dataclass
class DeadLetterQueue:
    """In-memory dead-letter capture for one pipeline run.

    The summary is JSON-serializable by design: callers persist it where the
    morning standup can read it (csv_import_logs.details, brief data_status)
    and the structured log line alerts anyone tailing the logs.
    """
    pipeline: str
    items: list[dict[str, Any]] = field(default_factory=list)

    def collect(self, index: int, item: Any, exc: BaseException) -> None:
        entry = {
            "index": index,
            "preview": _preview(item),
            "error": f"{type(exc).__name__}: {exc}",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.items.append(entry)
        log.error(
            "self-healing DLQ [%s]: item %d poisoned (%s): %s",
            self.pipeline, index, _preview(item), entry["error"],
        )

    def summarize(self, sample_size: int = 20) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline,
            "skipped": len(self.items),
            "sample": self.items[:sample_size],
        }


def health_check(db: Any, tables: list[str]) -> dict[str, str]:
    """Probe each table; return {table: 'ok' | 'unreachable: ...'}.

    A cheap pre-flight: pipelines call this before doing real work so a dead
    database degrades honestly instead of mid-batch.

    Assumes every probed table has an `id` column — the probe is
    `select("id").limit(1)`. True for every lbt-os table today; revisit if a
    probed table ever drops its id column.
    """
    report: dict[str, str] = {}
    for t in tables:
        try:
            db.table(t).select("id").limit(1).execute()
            report[t] = "ok"
        except Exception as exc:  # noqa: BLE001 - the report IS the handling
            report[t] = f"unreachable: {type(exc).__name__}: {exc}"
    return report
