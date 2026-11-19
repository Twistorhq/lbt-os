"""TW-953 (TW-948-B5): preflight for the persistent job service.

Two layers, both run BEFORE any task is created, any model
spend happens, or any external step runs (spec Section 8.5: a
failed preflight marks the job blocked, sends the owner one
alert, and makes no customer-facing action):

1. Definition validation (pure, no I/O): the stored definition
   itself must be sound: cron parses, timezone is a real IANA
   name, next_fire_at parses, the prompt is non-empty, the
   catch-up window is in bounds. Poisoned rows (a bad cron
   written past the management surface, a corrupt slot) are
   caught and quarantined HERE, loudly, not discovered mid-tick
   as an unexplained failure (the TW-952 Rex F1 tail: containment
   kept the tick alive, but the poisoned job itself just failed
   every tick forever).

2. External checks (injected seams, fail closed): every
   credential the job names resolves through the just-in-time
   resolver; every attached skill is present, ready, and
   commercially licensed; a delivery target is configured for
   the org through Notify; the permission ledger grants every
   action class the job's prompt can reach. A check that cannot
   be performed (no resolver wired, no skill registry wired)
   FAILS: unvalidatable is not valid. The phone pack is a hook
   reserved for B14 (TW-948-B14): when a job can reach
   call.outbound and a hook is wired, its failures block; with
   no hook wired, preflight records the reservation as a note
   and dial-time checks in TW-884 remain the wall, unchanged.

Blocking itself lives in service.block_job: one blocked record,
exactly one owner alert, idempotent on re-tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .cron import CronSchedule, validate_timezone
from .models import MAX_CATCH_UP_WINDOW_MINUTES

log = logging.getLogger(__name__)

__all__ = [
    "PreflightChecker",
    "PreflightResult",
    "log_alerter",
    "make_notify_alerter",
    "notify_delivery_configured",
    "preflight_scan",
    "validate_definition",
]


def _parse_dt_lenient(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def validate_definition(job: dict) -> list[str]:
    """Definition-integrity failures for a stored job row.

    Pure: reads only the row. An empty list means the definition
    is sound enough to fire; anything else is a quarantine
    reason. Never raises: corruption is data here, and data gets
    reported, not thrown.
    """
    failures: list[str] = []
    if not str(job.get("prompt") or "").strip():
        failures.append("job prompt is empty (a job needs a self-contained prompt)")
    cron_expr = job.get("cron_expr")
    if cron_expr:
        try:
            CronSchedule(cron_expr)
        except Exception as exc:
            failures.append(f"invalid cron expression: {exc}")
    try:
        validate_timezone(job.get("timezone") or "")
    except Exception as exc:
        failures.append(f"invalid timezone: {exc}")
    next_fire_at = job.get("next_fire_at")
    if next_fire_at is not None:
        try:
            _parse_dt_lenient(next_fire_at)
        except Exception:
            failures.append(f"corrupt next_fire_at: {next_fire_at!r} does not parse")
    if not isinstance(job.get("skill_keys") or [], list):
        failures.append("skill_keys is not a list")
    window = job.get("catch_up_window_minutes")
    if window is not None:
        try:
            window_int = int(window)
            if not 0 <= window_int <= MAX_CATCH_UP_WINDOW_MINUTES:
                failures.append(
                    f"catch_up_window_minutes {window_int} out of bounds "
                    f"(0..{MAX_CATCH_UP_WINDOW_MINUTES})"
                )
        except (TypeError, ValueError):
            failures.append(f"catch_up_window_minutes {window!r} is not an integer")
    return failures


def notify_delivery_configured(db: Any, org_id: str) -> bool:
    """Default delivery check: the org has at least one enabled
    Notify provider binding that resolves to a REAL delivery
    provider. Reads ntf_providers directly (the Notify service
    falls back to a null pipe when nothing is configured, which
    is exactly what preflight must NOT accept as a configured
    delivery target).

    Rex TW-953 F1: an enabled row is not enough. The 'null'
    provider (NullProvider) sends nothing and always succeeds,
    so a null-only org, or an org whose only real provider is
    disabled while null stays enabled, has NO delivery target
    and must fail closed here, not fire-and-spend against a
    no-op pipe. Rows Notify's build_provider would skip
    (unknown provider name, malformed row) do not count either:
    delivery counts as configured only when build_provider
    builds an enabled, non-null provider from a stored row.
    Any read failure is a not-configured answer: fail closed."""
    from ..notify import providers as notify_providers
    from ..notify import service as notify_service

    try:
        rows = (
            db.table("ntf_providers").select("*").eq("org_id", org_id).execute().data
            or []
        )
    except Exception as exc:
        log.error("agent_jobs: delivery config read failed for org %s: %s", org_id, exc)
        return False
    for row in rows:
        if not bool(row.get("is_enabled", True)):
            continue
        try:
            provider = notify_service.build_provider(
                row["provider_name"], row["channel"], row.get("config") or {}
            )
        except Exception:
            continue  # a row build_provider would skip is not a target
        if isinstance(provider, notify_providers.NullProvider):
            continue  # the null pipe delivers nothing, ever
        return True
    return False


def _default_ledger_check(
    db: Any, org_id: str, agent_id: str, action_class: str
) -> bool:
    """Default ledger check against the TW-768 permission ledger.

    Granted is a pass. amount_required is also a pass: the grant
    exists and is capped, and the amount itself is fenced at
    execution time by the gate, which is where amounts live.
    No grant, denied, revoked, or a broken (uncapped) money
    grant all fail. An unknown class raises in the ledger and
    fails here too.
    """
    from ..agentperms import service as perms

    try:
        decision = perms.check_permission(
            db, org_id, action_class, agent_identity_id=agent_id
        )
    except Exception as exc:
        log.error(
            "agent_jobs: ledger check failed for %s/%s: %s", org_id, action_class, exc
        )
        return False
    return bool(decision.allowed or decision.reason == "amount_required")


@dataclass
class PreflightResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class PreflightChecker:
    """The external preflight seams for one deployment.

    Every seam is a callable; None means "use the default" for
    delivery and ledger (both have real db-backed defaults), and
    "fail closed when the job needs it" for credentials, skills,
    and the phone hook.
    """

    def __init__(
        self,
        *,
        credential_resolver: Callable[[str], Any] | None = None,
        skill_checker: Callable[[str], Any] | None = None,
        delivery_checker: Callable[..., bool] | None = None,
        ledger_checker: Callable[..., bool] | None = None,
        phone_hook: Callable[..., Any] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._skill_checker = skill_checker
        self._delivery_checker = delivery_checker
        self._ledger_checker = ledger_checker
        self._phone_hook = phone_hook

    def check(self, db: Any, org_id: str, agent_id: str, job: dict) -> PreflightResult:
        failures: list[str] = []
        notes: list[str] = []

        # Credentials: resolvable through the JIT resolver, or
        # the run does not start (no task, no model spend).
        for ref in job.get("credential_refs") or []:
            if self._credential_resolver is None:
                failures.append(
                    f"credential {ref!r} cannot be validated: "
                    "no credential resolver is wired"
                )
                continue
            try:
                resolved = self._credential_resolver(ref)
            except Exception as exc:
                failures.append(f"credential {ref!r} failed to resolve: {exc}")
                continue
            if resolved is None:
                failures.append(f"credential {ref!r} did not resolve")

        # Skills: present, ready, licensed, active. No registry
        # wired means attached skills cannot be validated.
        for key in job.get("skill_keys") or []:
            if self._skill_checker is None:
                failures.append(
                    f"skill {key!r} cannot be validated: no skill registry is wired"
                )
                continue
            try:
                state = self._skill_checker(key)
            except Exception as exc:
                failures.append(f"skill {key!r} check failed: {exc}")
                continue
            if state is None:
                failures.append(f"skill {key!r} is not registered")
            elif isinstance(state, dict):
                for flag in ("ready", "licensed", "active"):
                    if not state.get(flag):
                        failures.append(f"skill {key!r} is not {flag}")
            elif not state:
                failures.append(f"skill {key!r} is not ready")

        # Delivery target: configured for the org via Notify.
        delivery_checker = self._delivery_checker or (
            lambda db_, org_id_, job_: notify_delivery_configured(db_, org_id_)
        )
        try:
            if not delivery_checker(db, org_id, job):
                failures.append(
                    "delivery target is not configured for this org via Notify"
                )
        except Exception as exc:
            failures.append(f"delivery target check failed: {exc}")

        # Ledger state for every reachable action class.
        ledger_checker = self._ledger_checker or _default_ledger_check
        action_classes = list(job.get("action_classes") or [])
        for action_class in action_classes:
            try:
                allowed = ledger_checker(db, org_id, agent_id, action_class)
            except Exception as exc:
                failures.append(f"ledger check for {action_class!r} failed: {exc}")
                continue
            if not allowed:
                failures.append(
                    f"action class {action_class!r} is not granted in the "
                    "permission ledger (no grant, denied, or revoked)"
                )

        # Phone pack: hook reserved for B14. Dial-time checks in
        # TW-884 are unchanged and remain the final wall.
        if "call.outbound" in action_classes:
            if self._phone_hook is None:
                notes.append(
                    "phone preflight checks reserved for B14 (TW-948-B14); "
                    "dial-time checks in TW-884 unchanged"
                )
            else:
                try:
                    hook_result = self._phone_hook(db, org_id, agent_id, job)
                except Exception as exc:
                    failures.append(f"phone preflight hook failed: {exc}")
                else:
                    if isinstance(hook_result, (list, tuple)):
                        failures.extend(str(f) for f in hook_result)
                    elif hook_result is False:
                        failures.append("phone preflight hook refused the fire")

        return PreflightResult(ok=not failures, failures=failures, notes=notes)


# -- alerters ---------------------------------------------------------


def log_alerter(db: Any, org_id: str, job: dict, reason: str) -> str:
    """The floor alerter: a loud log line and a stable receipt.

    Deployments wire a Notify-backed alerter (make_notify_alerter
    or app.state.agent_jobs_alerter); this default exists so a
    block is never silent even before that wiring lands.
    """
    log.error(
        "agent_jobs: job %s BLOCKED for org %s: %s",
        job.get("id"),
        org_id,
        reason,
    )
    return f"log:{job.get('id')}"


def make_notify_alerter(to_address: str) -> Callable[..., str]:
    """Alerter backed by Twistor Notify (TW-620): one message
    through the delivery ledger, whose message id is the durable
    alert receipt stored on the blocked job row."""

    def alerter(db: Any, org_id: str, job: dict, reason: str) -> str:
        from ..notify import service as notify_service

        message = notify_service.send(
            db,
            org_id,
            channel="email",
            to=to_address,
            subject=f"Twistor: scheduled job blocked: {job.get('name', job.get('id'))}",
            body=(
                f"Job {job.get('name')!r} ({job.get('id')}) was blocked by "
                f"preflight and did not run.\n\nReason: {reason}\n\n"
                "Fix the job definition or its configuration, then resume "
                "the job. It will not fire while blocked."
            ),
            workflow="agent_jobs_blocked",
            metadata={"job_id": job.get("id")},
        )
        return str(message.get("id") or "notify:unknown")

    return alerter


# -- scan ---------------------------------------------------------------


def preflight_scan(db: Any, *, alerter: Callable[..., str] | None = None) -> dict:
    """Validate every active job's definition and quarantine the
    poisoned ones, whether or not they are due. Wired into every
    tick (ticker.tick, Rosa MF3) and callable directly by an
    operator, so corruption is caught when it lands, not when
    the job's slot arrives. Returns {"blocked": [...], "ok": int}."""
    from . import service

    rows = db.table("agt_jobs").select("*").eq("status", "active").execute().data or []
    blocked: list[dict] = []
    ok = 0
    for job in rows:
        failures = validate_definition(job)
        if not failures:
            ok += 1
            continue
        reason = "; ".join(failures)
        service.block_job(
            db,
            job.get("org_id"),
            job.get("agent_identity_id"),
            job,
            reason,
            alerter=alerter,
        )
        blocked.append({"job_id": job.get("id"), "status": "blocked", "error": reason})
    return {"blocked": blocked, "ok": ok}
