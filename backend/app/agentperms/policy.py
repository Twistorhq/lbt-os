"""TW-954 (TW-948-B7) — unattended-operation policy: model,
storage, and the layered evaluation every unattended action
passes before it runs.

Deny by default, in layers (spec Sections 8.1/8.2, build map
Section 2.9). evaluate_action() consults, in order:

1. the hard floor (floor.py) — code-level, non-overridable;
2. the permission ledger (service.check_permission) — the
   owner's mandate, checked at execution time as always;
3. the unattended policy — this module. Only for unattended
   contexts (scheduled, event, background, delegated): the
   ledger grant must itself extend to unattended work
   (agt_grants.extends_unattended, default FALSE), and a stored
   policy for (org, agent) must be enabled and must permit the
   job class, the tool class, and the action class. Anything
   not explicitly permitted is refused.

Attended work needs no policy — the floor and the ledger govern
it exactly as before TW-954. There is deliberately no code path
here that permits an unattended action on the strength of an
attended grant alone.

Fix round (Rex F1/F4): the floor's global facts (revocation,
approvals-off, raw PII) evaluate even when the caller names no
action class, and an absent dimension is a refusal, never a
vacuous pass — unattended evaluation requires the action, job,
and tool classes all named, and a class-less evaluation never
authorizes a concrete action in any context. Audit masking
covers the whole detail structure: keys and non-string leaves
as well as string values.

Storage discipline matches B1's _scope rules: every query
filters on org_id + agent_identity_id + the derived workspace
key (persistence.models.workspace_id_for), and a caller-supplied
workspace_id that differs from the derived key is refused, never
honored. Policy changes and floor-blocked actions write audit
events into the same append-only agt_permission_events history
the ledger uses, with detail passed through the B1 masking
pipeline (persistence.models.mask_text) so no raw identifier
reaches the history.

The policy model refuses to state a floor relaxation: unknown
vocabulary fails construction, and any key that names a floor
override — in the statement or nested anywhere in mandate_scope
— is a construction error. Even a row written past this model,
straight into the table with floor-disable keys set, cannot
relax anything: evaluation never reads mandate_scope for floor
facts, and the floor check runs in code before the policy layer
is consulted at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..persistence.models import mask_text, workspace_id_for
from . import floor, service
from .models import ACTION_CLASSES, LedgerError, _StrictBase, channel_of

# Every execution carries one of these context tags (build map
# Section 2.9). Attended is the live-conversation context; the
# rest are unattended and face the policy layer.
CONTEXTS: tuple[str, ...] = (
    "attended",
    "scheduled",
    "event",
    "background",
    "delegated",
)
UNATTENDED_CONTEXTS: frozenset[str] = frozenset(c for c in CONTEXTS if c != "attended")

# The bounded job-class vocabulary: the shapes of cadence work a
# policy may permit (spec Section 8.3 loops express as these).
JOB_CLASSES: tuple[str, ...] = (
    "research.scan",
    "followup.chase",
    "booking.ops",
    "inbox.triage",
    "report.digest",
    "background.review",
    "task.generic",
)

# The bounded tool-class vocabulary: gateway-level categories a
# policy may permit for unattended work.
TOOL_CLASSES: tuple[str, ...] = (
    "read.only",
    "draft.write",
    "memory.stage",
    "skill.stage",
    "external.send",
    "external.call",
    "booking.write",
    "payment.initiate",
)

# Keys that would state a floor relaxation. A policy carrying one
# — at the top level or nested anywhere in mandate_scope — cannot
# be constructed. Matching is exact per key, plus any key that
# names the floor itself.
FORBIDDEN_POLICY_KEYS: frozenset[str] = frozenset(
    {
        "dnc_override",
        "override_dnc",
        "approvals_off",
        "bypass_floor",
        "disable_floor",
        "floor_disabled",
        "no_floor",
        "unmetered",
        "skip_toll",
    }
)

POLICY_TABLE = "agt_unattended_policies"


def _scan_forbidden(value: Any, path: str = "mandate_scope") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_POLICY_KEYS or "floor" in lowered:
                raise LedgerError(
                    f"{path}.{key}: a policy cannot state a hard-floor "
                    "relaxation; the floor is code, not data (TW-954)"
                )
            _scan_forbidden(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _scan_forbidden(item, path)


def _validate_vocabulary(values: tuple[str, ...], allowed: tuple[str, ...], label: str):
    for value in values:
        if value not in allowed:
            raise LedgerError(f"unknown {label} {value!r}; want one of {allowed}")


class PolicyStatement(_StrictBase):
    """An owner's unattended-operation policy for one agent.

    enabled defaults FALSE: stating a policy and switching it on
    are two facts, and the off state is the safe one. The
    permitted sets are deny-by-default — an empty set permits
    nothing in that dimension.
    """

    enabled: bool = False
    permitted_job_classes: tuple[str, ...] = ()
    permitted_tool_classes: tuple[str, ...] = ()
    permitted_action_classes: tuple[str, ...] = ()
    mandate_scope: dict[str, Any] = {}

    def model_post_init(self, _ctx: Any) -> None:
        _validate_vocabulary(self.permitted_job_classes, JOB_CLASSES, "job class")
        _validate_vocabulary(self.permitted_tool_classes, TOOL_CLASSES, "tool class")
        _validate_vocabulary(
            self.permitted_action_classes, ACTION_CLASSES, "action class"
        )
        _scan_forbidden(self.mandate_scope)


class PolicyDecision(BaseModel):
    """evaluate_action()'s answer. Returned, never raised.

    reason vocabulary: 'granted' (attended, ledger-allowed),
    'unattended_permitted', a ledger reason passthrough
    ('no_grant', 'denied', 'revoked', 'cap_exceeded',
    'unknown_grant_status', ...),
    'floor:<rule>' when the hard floor blocked the action,
    'grant_not_extended_unattended', 'no_policy',
    'policy_disabled', 'job_class_not_permitted',
    'tool_class_not_permitted', 'action_class_not_permitted',
    and the absent-dimension refusals 'action_class_required',
    'job_class_required', 'tool_class_required' (TW-954 fix
    round, Rex F1: an absent dimension is not permitted, never
    a vacuous pass).
    """

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str
    context: str
    action_class: str | None = None
    floor_rule: str | None = None
    grant_id: str | None = None
    policy_id: str | None = None


# -- storage (B1 _scope discipline) --------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_workspace(org_id: str, agent_id: str, override: str | None) -> str:
    if not org_id or not agent_id:
        raise LedgerError("org_id and agent_id are required on every policy call")
    ws = workspace_id_for(org_id, agent_id)
    if override is not None and override != ws:
        raise LedgerError(
            f"workspace_id {override!r} does not match the scope workspace {ws!r}"
        )
    return ws


def _scoped(db: Any, org_id: str, agent_id: str, workspace_id: str | None):
    ws = _resolve_workspace(org_id, agent_id, workspace_id)
    return (
        db.table(POLICY_TABLE)
        .select("*")
        .eq("org_id", org_id)
        .eq("agent_identity_id", agent_id)
        .eq("workspace_id", ws)
    )


def _mask_scalar(value: Any) -> Any:
    """One non-container leaf/key through the masking pipeline.

    Strings pass through mask_text. Numbers are judged by their
    digit string (TW-954 fix round, Rex F4): a leaf whose digits
    mask_text would alter — a 10-digit phone planted as an int,
    a long identifier run — is replaced by its masked string
    form, while an ordinary small number (a version, a count, a
    seat total) keeps its type and value. Bools are not numbers
    for this purpose and pass through untouched, as does None.
    """
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        digits = str(value)
        masked = mask_text(digits)
        return masked if masked != digits else value
    return value


def _mask_detail(value: Any) -> Any:
    """B1 masking pipeline over the WHOLE audit detail structure,
    recursively (TW-954 fix round, Rex F4): dict KEYS are masked
    as well as values — a raw email planted as a mandate_scope
    key survived verbatim on the round-1 code — and non-string
    leaves (ints, nested containers) are masked too, so no raw
    identifier reaches the append-only history by any route."""
    if isinstance(value, dict):
        return {_mask_scalar(k): _mask_detail(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_detail(v) for v in value]
    return _mask_scalar(value)


def _record_policy_event(
    db: Any,
    org_id: str,
    agent_id: str,
    kind: str,
    actor: str,
    detail: dict[str, Any],
    *,
    action_class: str | None = None,
) -> None:
    service._record_event(
        db,
        org_id,
        kind,
        actor,
        action_class=action_class,
        channel=channel_of(action_class) if action_class else None,
        detail=_mask_detail(detail),
        agent_identity_id=agent_id,
    )


def get_policy(
    db: Any, org_id: str, agent_id: str, *, workspace_id: str | None = None
) -> dict[str, Any] | None:
    """The stored policy row for exactly this (org, agent,
    workspace) scope, or None. Another org's, another agent's,
    or a foreign-stamped row is invisible here."""
    rows = _scoped(db, org_id, agent_id, workspace_id).limit(1).execute().data
    return rows[0] if rows else None


def set_policy(
    db: Any,
    org_id: str,
    agent_id: str,
    statement: PolicyStatement,
    *,
    actor: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Create or replace the agent's unattended policy. One row
    per scope (the migration's UNIQUE law); a re-set bumps the
    version. Writes a masked policy_set event — a policy change
    that is not in the history did not happen."""
    ws = _resolve_workspace(org_id, agent_id, workspace_id)
    existing = get_policy(db, org_id, agent_id, workspace_id=ws)
    now = _now_iso()
    payload = {
        "enabled": statement.enabled,
        "permitted_job_classes": list(statement.permitted_job_classes),
        "permitted_tool_classes": list(statement.permitted_tool_classes),
        "permitted_action_classes": list(statement.permitted_action_classes),
        "mandate_scope": statement.mandate_scope,
        "updated_by": actor,
        "updated_at": now,
    }
    if existing is None:
        row = {
            "org_id": org_id,
            "agent_identity_id": agent_id,
            "workspace_id": ws,
            "created_at": now,
            "version": 1,
            **payload,
        }
        saved = db.table(POLICY_TABLE).insert(row).execute().data[0]
    else:
        patch = {**payload, "version": int(existing.get("version", 1)) + 1}
        saved = (
            db.table(POLICY_TABLE).update(patch).eq("id", existing["id"]).execute()
        ).data[0]
    _record_policy_event(
        db,
        org_id,
        agent_id,
        "policy_set",
        actor,
        {
            "policy_id": saved.get("id"),
            "version": saved.get("version"),
            "enabled": statement.enabled,
            "permitted_job_classes": list(statement.permitted_job_classes),
            "permitted_tool_classes": list(statement.permitted_tool_classes),
            "permitted_action_classes": list(statement.permitted_action_classes),
            "mandate_scope": statement.mandate_scope,
        },
    )
    return saved


def disable_policy(
    db: Any, org_id: str, agent_id: str, *, actor: str
) -> dict[str, Any] | None:
    """Switch the agent's policy off. Deny-by-default means the
    off state needs no other change to stop unattended work."""
    existing = get_policy(db, org_id, agent_id)
    if existing is None:
        return None
    saved = (
        db.table(POLICY_TABLE)
        .update({"enabled": False, "updated_by": actor, "updated_at": _now_iso()})
        .eq("id", existing["id"])
        .execute()
    ).data[0]
    _record_policy_event(
        db, org_id, agent_id, "policy_disabled", actor, {"policy_id": saved.get("id")}
    )
    return saved


# -- evaluation -------------------------------------------------------------


def evaluate_action(
    db: Any,
    org_id: str,
    agent_id: str,
    *,
    context: str,
    action_class: str | None = None,
    job_class: str | None = None,
    tool_class: str | None = None,
    amount_cents: int | None = None,
    lane: str | None = None,
    dnc_status: str | None = None,
    toll_event: str | None = None,
    approvals_off: bool = False,
    pii_raw_in_projection: bool = False,
    detail_note: str | None = None,
) -> PolicyDecision:
    """May this agent take this action in this context, now?

    The single pre-action evaluation for unattended work (and a
    floor+ledger pass for attended work). Layers run floor,
    ledger, policy — see the module docstring. Floor-blocked
    actions and policy-layer denials write masked audit events;
    the event insert is not best-effort on the floor path: a
    blocked action whose block cannot be recorded raises, like
    the revocation receipt law in service.revoke().
    """
    if context not in CONTEXTS:
        raise LedgerError(f"unknown context {context!r}; want one of {CONTEXTS}")
    if action_class is not None:
        channel_of(action_class)  # LedgerError on an unknown class
    if job_class is not None:
        _validate_vocabulary((job_class,), JOB_CLASSES, "job class")
    if tool_class is not None:
        _validate_vocabulary((tool_class,), TOOL_CLASSES, "tool class")

    base: dict[str, Any] = {"context": context, "action_class": action_class}

    ledger_decision = None
    grant_row = None
    if action_class is not None:
        ledger_decision = service.check_permission(
            db,
            org_id,
            action_class,
            amount_cents=amount_cents,
            agent_identity_id=agent_id,
        )
        grant_row = service._pick_grant(db, org_id, action_class, agent_id)
        base["grant_id"] = ledger_decision.grant_id

    # Layer 1: the hard floor, in code, before anything stored
    # is consulted for permission. A stored row's claims play
    # no part in these facts. The floor's global facts — the
    # agent stands revoked, an approvals-off claim, raw PII in
    # a projection — condemn the evaluation whether or not the
    # caller named an action class (TW-954 fix round, Rex F1:
    # the round-1 code ran this layer only under
    # 'if action_class is not None', so a class-less evaluation
    # skipped revocation supremacy itself).
    agent_revoked = service.agent_status(db, org_id, agent_id) == "revoked"
    if action_class is not None:
        verdict = floor.check_floor(
            action_class=action_class,
            revoked=agent_revoked,
            approvals_off=approvals_off,
            pii_raw_in_projection=pii_raw_in_projection,
            dnc_status=dnc_status,
            lane=lane,
            # The floor's mandate fact is about adverse states:
            # a grant row standing denied (or revoked, though
            # supremacy fires first) is a removed mandate. Mere
            # absence of a row is the ledger's no_grant denial,
            # one layer down, and keeps its own reason.
            mandate_allowed=(grant_row is None or grant_row.get("status") == "granted"),
            amount_cents=amount_cents,
            cap_cents=grant_row.get("cap_cents") if grant_row else None,
            toll_event=toll_event,
        )
    else:
        verdict = floor.check_floor_global(
            revoked=agent_revoked,
            approvals_off=approvals_off,
            pii_raw_in_projection=pii_raw_in_projection,
        )
    if not verdict.allowed:
        _record_policy_event(
            db,
            org_id,
            agent_id,
            "floor_blocked",
            service.SYSTEM_ACTOR,
            {
                "floor_rule": verdict.rule,
                "reason": verdict.reason,
                "context": context,
                "note": detail_note,
            },
            action_class=action_class,
        )
        return PolicyDecision(
            allowed=False,
            reason=f"floor:{verdict.rule}",
            floor_rule=verdict.rule,
            **base,
        )

    # Layer 2: the ledger mandate.
    if ledger_decision is not None and not ledger_decision.allowed:
        return PolicyDecision(allowed=False, reason=ledger_decision.reason, **base)

    # No action class named: there is no grant leg to stand on,
    # in any context. A class-less evaluation is a policy-only
    # question and must never authorize a concrete action
    # (TW-954 fix round, Rex F1 / Rosa N2).
    if action_class is None:
        return PolicyDecision(allowed=False, reason="action_class_required", **base)

    if context == "attended":
        return PolicyDecision(
            allowed=True,
            reason=ledger_decision.reason if ledger_decision else "granted",
            **base,
        )

    # Layer 3: the unattended policy. Every dimension the policy
    # layer checks must be NAMED: an omitted job or tool class
    # is not permitted, never a vacuous pass (Rex F1). The grant
    # must extend to unattended work on its own face, and the
    # policy must permit every named dimension.
    if job_class is None:
        return PolicyDecision(allowed=False, reason="job_class_required", **base)
    if tool_class is None:
        return PolicyDecision(allowed=False, reason="tool_class_required", **base)
    if grant_row is not None and not grant_row.get("extends_unattended", False):
        return PolicyDecision(
            allowed=False, reason="grant_not_extended_unattended", **base
        )
    policy_row = get_policy(db, org_id, agent_id)
    if policy_row is None:
        return PolicyDecision(allowed=False, reason="no_policy", **base)
    base["policy_id"] = policy_row.get("id")
    if not policy_row.get("enabled", False):
        return PolicyDecision(allowed=False, reason="policy_disabled", **base)
    # Presence of all three dimensions is guaranteed here (the
    # absent-dimension refusals above), so these are plain
    # membership checks.
    if job_class not in (policy_row.get("permitted_job_classes") or []):
        return PolicyDecision(allowed=False, reason="job_class_not_permitted", **base)
    if tool_class not in (policy_row.get("permitted_tool_classes") or []):
        return PolicyDecision(allowed=False, reason="tool_class_not_permitted", **base)
    if action_class not in (policy_row.get("permitted_action_classes") or []):
        return PolicyDecision(
            allowed=False, reason="action_class_not_permitted", **base
        )
    return PolicyDecision(allowed=True, reason="unattended_permitted", **base)
