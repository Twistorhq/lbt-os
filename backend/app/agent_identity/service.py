"""TW-767 Twistor Agent Identity — service: registry, provisioning,
inbound routing. TW-787 amendment: the registry is a ROSTER.

One identity record per AGENT (TW-787). The first agent an org
provisions is the subscription's included everything agent; every
additional agent is a hire (hire_agent) carrying the one-time
agent.hire toll. Every read and write is org-scoped in the service
layer (the backend's Supabase client is service-role and bypasses
RLS; the agt_ tables are RLS deny-all as the second wall), and
agent-scoped reads/writes resolve within the org's roster only.

Fail-closed rules enforced here, not left to callers:
- Provisioning allocates the agent's email address deterministically
  from the org, on the Twistor-controlled agent domain. One address
  resolves to exactly one agent in exactly one org, forever. The
  included agent keeps the TW-767 allocation formula byte-for-byte;
  hires extend it with the agent's slug.
- The phone number BINDS to the shop's existing TW-284/Fonoster
  number path (org_phone_numbers). A number owned by another org, or
  by no org, is refused — never reassigned, never guessed. A number
  already bound to a roster-mate is refused: one number reaches one
  agent, so inbound resolution is never ambiguous.
- Inbound mail resolves by exact recipient address. An unknown or
  suspended recipient queues NOTHING, anywhere.
- Inbound enqueue is idempotent on the provider message id: a
  redelivered webhook can never double-queue a task.
- The hire toll posts exactly once per hire (ensure_hire_toll): the
  toll ledger's idempotency key is the agent's id, and the pre-post
  existence check makes a retry after a failed post complete the
  hire instead of double-charging or minting a second agent.

TW-208: a failed audit insert never fails the mutation itself — the
identity write is the source of truth, the audit is the trail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..config import (
    INCLUDED_AGENT_POWER_UNITS,
    STANDARD_AGENT_POWER_UNITS,
    settings,
)
from .models import (
    PRIMARY_AGENT_SLUG,
    AgentIdentity,
    Coverage,
    IdentityError,
    Persona,
    default_coverage,
    default_persona,
    validate_archetype_key,
)

log = logging.getLogger(__name__)

CHANNELS = ("phone", "email", "sms")
_SIGNATURE_TOLERANCE_SEC = 300


class IdentityNotFound(Exception):
    """No agent identity provisioned for this org."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_address(address: str) -> str:
    """SHA-256 of a normalized email address: the silver-safe matching
    and display value for a bronze raw address (PII doctrine)."""
    return hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Audit (append-only; TW-208: failure never fails the mutation)
# ---------------------------------------------------------------------------


def _record_audit(db: Any, org_id: str, action: str, actor: str, summary: str) -> None:
    try:
        db.table("agt_audit").insert(
            {
                "org_id": org_id,
                "action": action,
                "actor": actor,
                "summary": summary[:500],
                "at": _now().isoformat(),
            }
        ).execute()
    except Exception as exc:
        log.warning(
            "agent_identity: audit insert failed for org %s (%s unaffected): %s",
            org_id,
            action,
            exc,
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _fetch_rows(db: Any, org_id: str) -> list[dict[str, Any]]:
    """The org's full roster, in storage order (oldest first)."""
    res = db.table("agt_identities").select("*").eq("org_id", org_id).execute()
    return res.data or []


def _primary_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The roster's primary: the included agent when flagged, else the
    oldest row (pre-TW-787 rows carry no flag until the migration
    backfills them, and the oldest row IS that agent)."""
    for row in rows:
        if row.get("included_with_subscription"):
            return row
    return rows[0] if rows else None


def _fetch_row(
    db: Any, org_id: str, agent_id: str | None = None
) -> dict[str, Any] | None:
    """One roster row: the agent named by agent_id (resolved strictly
    inside this org), or the org's primary agent when agent_id is
    None — the TW-767 org-level read, unchanged for singleton callers."""
    rows = _fetch_rows(db, org_id)
    if agent_id is None:
        return _primary_row(rows)
    for row in rows:
        if row.get("id") == agent_id:
            return row
    return None


def _power_units_for(row: dict[str, Any]) -> int:
    """The agent's task budget in standard agent units: the included
    agent is built to 10-agent power (INCLUDED_AGENT_POWER_UNITS);
    every hire carries the standard unit. Derived from config, so
    retuning the constants retunes every roster at once."""
    if row.get("included_with_subscription"):
        return INCLUDED_AGENT_POWER_UNITS
    return STANDARD_AGENT_POWER_UNITS


def _row_to_identity(row: dict[str, Any]) -> AgentIdentity:
    return AgentIdentity(
        id=row.get("id"),
        org_id=row["org_id"],
        agent_name=row["agent_name"],
        agent_slug=row.get("agent_slug"),
        role=row.get("role") or "everything",
        archetype_key=row.get("archetype_key"),
        included_with_subscription=bool(row.get("included_with_subscription")),
        power_units=_power_units_for(row),
        email_address=row.get("email_address"),
        phone_number=row.get("phone_number"),
        persona=Persona(**(row.get("persona") or {})),
        coverage=Coverage(**(row.get("coverage") or {})),
        status=row.get("status") or "unprovisioned",
        version=int(row.get("version") or 1),
        provisioned=True,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def get_identity(db: Any, org_id: str) -> AgentIdentity | None:
    """The org's primary (included) agent record, or None if the org
    has never provisioned an agent."""
    row = _fetch_row(db, org_id)
    return _row_to_identity(row) if row else None


def get_agent(db: Any, org_id: str, agent_id: str) -> AgentIdentity | None:
    """One agent by id, resolved inside the org's roster only: an id
    from another org reads as absent, never as a cross-org leak."""
    row = _fetch_row(db, org_id, agent_id)
    return _row_to_identity(row) if row else None


def list_agents(db: Any, org_id: str) -> list[AgentIdentity]:
    """The org's roster: the included agent first, then hires in
    provisioning order. This is the list Spec 3's runtime and Spec 6's
    roster surfaces iterate — agents are a roster, not a singleton."""
    rows = _fetch_rows(db, org_id)
    primary = _primary_row(rows)
    ordered = ([primary] if primary else []) + [r for r in rows if r is not primary]
    return [_row_to_identity(row) for row in ordered]


def _default_view(org_id: str) -> dict[str, Any]:
    return {
        "id": None,
        "org_id": org_id,
        "agent_name": None,
        "agent_slug": None,
        "role": "everything",
        "archetype_key": None,
        "included_with_subscription": False,
        "power_units": INCLUDED_AGENT_POWER_UNITS,
        "email_address": None,
        "phone_number": None,
        "persona": default_persona().model_dump(mode="json"),
        "coverage": default_coverage().model_dump(mode="json"),
        "status": "unprovisioned",
        "version": 0,
        "provisioned": False,
        "created_at": None,
        "updated_at": None,
    }


def identity_view(db: Any, org_id: str, agent_id: str | None = None) -> dict[str, Any]:
    """The record Spec 6 renders and Spec 3 reads: the primary agent,
    or the agent named by agent_id. When the org has no agent yet,
    this returns the canonical fail-closed view — provisioned False,
    status 'unprovisioned', default persona and unconfigured
    coverage — so consumers never guess at absence. A named agent_id
    that does not resolve in this org raises IdentityNotFound:
    silently serving the primary's record for a bad id would put the
    wrong employee on a channel."""
    if agent_id is not None:
        agent = get_agent(db, org_id, agent_id)
        if agent is None:
            raise IdentityNotFound(f"no agent {agent_id} in org {org_id}")
        return agent.model_dump(mode="json")
    identity = get_identity(db, org_id)
    if identity is not None:
        return identity.model_dump(mode="json")
    return _default_view(org_id)


def roster_view(db: Any, org_id: str) -> list[dict[str, Any]]:
    """Every agent's view, included first. Spec 6's roster surface."""
    return [a.model_dump(mode="json") for a in list_agents(db, org_id)]


def channel_config(
    db: Any, org_id: str, channel: str, agent_id: str | None = None
) -> dict[str, Any]:
    """The per-channel read of one agent's identity. The identity block
    is built by ONE assembly path for phone, email, and sms, so the
    persona a channel sees is byte-identical across channels — one
    consistent person, not three configurations that can drift. The
    TW-787 reading: the invariant holds PER AGENT; roster-mates have
    their own blocks and never share one."""
    if channel not in CHANNELS:
        raise IdentityError(f"unknown channel {channel!r}; want one of {CHANNELS}")
    view = identity_view(db, org_id, agent_id)
    block = {
        "agent_name": view["agent_name"],
        "email_address": view["email_address"],
        "phone_number": view["phone_number"],
        "persona": view["persona"],
        "coverage": view["coverage"],
        "status": view["status"],
    }
    return {"channel": channel, "identity": block}


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "shop"


def agent_email_for(org_id: str, org_name: str) -> str:
    """The included agent's one email address: deterministic from the
    org, on the Twistor-controlled agent domain. Stable across
    re-provisioning; the org-id suffix makes collisions structurally
    impossible. TW-787 keeps this formula byte-for-byte — addresses
    already issued under TW-767 never move."""
    suffix = org_id.replace("-", "")[:8]
    return f"{_slugify(org_name)}-{suffix}@{settings.agent_email_domain}"


def agent_email_for_agent(org_id: str, org_name: str, agent_slug: str) -> str:
    """A roster agent's one email address. The primary slug resolves to
    the original TW-767 formula; every hire extends it with the
    agent's slug, so allocation stays deterministic and per-agent:
    same org, same slug, same address, forever."""
    if agent_slug == PRIMARY_AGENT_SLUG:
        return agent_email_for(org_id, org_name)
    suffix = org_id.replace("-", "")[:8]
    return f"{_slugify(org_name)}-{suffix}-{agent_slug}@{settings.agent_email_domain}"


def _unique_slug(rows: list[dict[str, Any]], base: str) -> str:
    """First free slug in the roster for base: base, base-2, base-3...
    Deterministic in provisioning order, so a roster rebuilt from its
    history allocates the same slugs (and therefore addresses)."""
    taken = {row.get("agent_slug") for row in rows}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _org_name(db: Any, org_id: str) -> str:
    org_res = (
        db.table("organizations").select("id, name").eq("id", org_id).limit(1).execute()
    )
    org_rows = org_res.data or []
    if not org_rows:
        raise IdentityError(f"cannot provision agent identity: no org {org_id!r}")
    return org_rows[0].get("name") or ""


def _insert_identity(db: Any, row: dict[str, Any]) -> dict[str, Any]:
    try:
        res = db.table("agt_identities").insert(row).execute()
    except Exception as exc:
        raise IdentityError(
            f"provisioning failed for org {row['org_id']}: {exc}"
        ) from exc
    return (res.data or [row])[0]


def provision_identity(
    db: Any, org_id: str, agent_name: str, actor: str
) -> AgentIdentity:
    """Create the org's INCLUDED agent: the one agent the subscription
    carries (TW-787), role 'everything', slug 'primary', no hire toll.
    Fail-closed defaults: status 'unprovisioned', default persona,
    unconfigured coverage, the allocated email address, and no phone
    until one is bound.

    Idempotent: provisioning an org that already has an included agent
    returns the existing record unchanged (onboarding retries are
    expected; silently minting a second identity is the failure).
    Additional agents go through hire_agent, never through here.
    """
    name = (agent_name or "").strip()
    if not name:
        raise IdentityError("agent name must be non-empty")
    existing = _fetch_row(db, org_id)
    if existing:
        return _row_to_identity(existing)

    row = {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "agent_name": name,
        "agent_slug": PRIMARY_AGENT_SLUG,
        "role": "everything",
        "archetype_key": None,
        "included_with_subscription": True,
        "email_address": agent_email_for(org_id, _org_name(db, org_id)),
        "phone_number": None,
        "persona": default_persona().model_dump(mode="json"),
        "coverage": default_coverage().model_dump(mode="json"),
        "status": "unprovisioned",
        "version": 1,
    }
    inserted = _insert_identity(db, row)
    _record_audit(
        db, org_id, "provision", actor, f"agent {name!r} provisioned; email allocated"
    )
    return _row_to_identity(inserted)


# ---------------------------------------------------------------------------
# Hiring: additional agents (TW-787)
# ---------------------------------------------------------------------------

HIRE_TOLL_EVENT = "agent.hire"


def hire_toll_idempotency_key(agent_id: str) -> str:
    """The agent.hire toll's dedupe key: one agent, one hire, one toll."""
    return f"{HIRE_TOLL_EVENT}:{agent_id}"


def ensure_hire_toll(db: Any, org_id: str, agent_id: str) -> dict[str, Any] | None:
    """Post the agent.hire toll for a hired agent, exactly once.

    Returns the toll row, or None for the included agent (the
    subscription carries it; it is never tolled). Safe to call any
    number of times: the ledger row is looked up by its idempotency
    key before posting, and record_toll_event dedupes on the same key
    at insert, so a retry after a failed post completes the hire
    instead of double-charging.
    """
    agent = get_agent(db, org_id, agent_id)
    if agent is None:
        raise IdentityNotFound(f"no agent {agent_id} in org {org_id}")
    if agent.included_with_subscription:
        return None
    key = hire_toll_idempotency_key(agent_id)
    existing = (
        db.table("toll_events")
        .select("*")
        .eq("idempotency_key", key)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]
    # Local import: the toll lane is a sibling service; importing it
    # at module load would couple the registry to the billing stack.
    from ..services.toll_service import record_toll_event

    return record_toll_event(
        db,
        org_id,
        HIRE_TOLL_EVENT,
        quantity=1,
        idempotency_key=key,
        source_type="agent_identity",
        source_id=agent_id,
    )


def hire_agent(
    db: Any,
    org_id: str,
    agent_name: str,
    actor: str,
    *,
    role: str = "general",
    archetype_key: str | None = None,
) -> AgentIdentity:
    """Hire an additional agent into the org's roster (TW-787).

    Hire-then-customize: the agent is created from the SAME standard
    shape as the included agent (full capability parity, its own
    identity and persona per TW-766), with the customer's role and an
    optional archetype loaded at hire time; both are re-assignable
    later without ever touching the identity. The one-time agent.hire
    toll posts as part of the hire; if the post itself fails, the
    agent exists and the error names the recovery: ensure_hire_toll
    completes the charge exactly once, and no second agent is minted
    by retrying the hire.

    The org must already carry its included agent — a hire is by
    definition additional (provision_identity first).
    """
    name = (agent_name or "").strip()
    if not name:
        raise IdentityError("agent name must be non-empty")
    rows = _fetch_rows(db, org_id)
    if not rows:
        raise IdentityError(
            "hire requires the org's included agent first: "
            "provision the included agent first (provision_identity), "
            "then hire additional agents"
        )
    slug = _unique_slug(rows, _slugify(name))
    # Construct the candidate first: role and archetype validation
    # fail loud here, before any row or toll exists.
    candidate = AgentIdentity(
        org_id=org_id,
        agent_name=name,
        agent_slug=slug,
        role=role,
        archetype_key=archetype_key,
    )
    row = {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "agent_name": name,
        "agent_slug": slug,
        "role": candidate.role,
        "archetype_key": candidate.archetype_key,
        "included_with_subscription": False,
        "email_address": agent_email_for_agent(org_id, _org_name(db, org_id), slug),
        "phone_number": None,
        "persona": default_persona().model_dump(mode="json"),
        "coverage": default_coverage().model_dump(mode="json"),
        "status": "unprovisioned",
        "version": 1,
    }
    inserted = _insert_identity(db, row)
    identity = _row_to_identity(inserted)
    _record_audit(
        db,
        org_id,
        "agent_hire",
        actor,
        f"agent {name!r} hired as {slug!r} (role {candidate.role}); hire toll due",
    )
    try:
        ensure_hire_toll(db, org_id, identity.id)
    except Exception as exc:
        raise IdentityError(
            f"agent {name!r} hired as {identity.id} but its hire toll "
            f"failed to post ({exc}); call ensure_hire_toll to complete "
            "the hire exactly once — do not re-hire"
        ) from exc
    return identity


# ---------------------------------------------------------------------------
# Role + archetype assignment (TW-787; catalog is TW-788)
# ---------------------------------------------------------------------------


def assign_role(
    db: Any, org_id: str, agent_id: str, role: str, actor: str
) -> AgentIdentity:
    """Assign the agent's role (its task seat in the business). The
    role is validated against the roster vocabulary before anything
    is written; identity fields are never touched by an assignment."""
    row = _require_row(db, org_id, agent_id)
    candidate = AgentIdentity(
        org_id=org_id, agent_name=row["agent_name"], role=role
    )  # raises IdentityError on an unknown role
    updated = _update_row(
        db,
        org_id,
        {
            "role": candidate.role,
            "version": int(row.get("version") or 1) + 1,
        },
        agent_id=agent_id,
    )
    _record_audit(
        db,
        org_id,
        "role_assign",
        actor,
        f"agent {row.get('agent_slug')} role -> {role}",
    )
    return _row_to_identity(updated)


def assign_archetype(
    db: Any, org_id: str, agent_id: str, archetype_key: str | None, actor: str
) -> AgentIdentity:
    """Load (or clear, with None) the agent's archetype: the TW-788
    prebuilt shape the agent acts as. This is the identity-level
    mechanic only — the key is stored on the agent's own record, and
    re-assignment changes ONLY the key: the agent's name, email,
    number, and persona are its identity (TW-766) and never move
    when its archetype does."""
    row = _require_row(db, org_id, agent_id)
    validate_archetype_key(archetype_key)  # raises IdentityError on a bad key
    updated = _update_row(
        db,
        org_id,
        {
            "archetype_key": archetype_key,
            "version": int(row.get("version") or 1) + 1,
        },
        agent_id=agent_id,
    )
    _record_audit(
        db,
        org_id,
        "archetype_assign",
        actor,
        f"agent {row.get('agent_slug')} archetype -> {archetype_key or 'generalist'}",
    )
    return _row_to_identity(updated)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _require_row(db: Any, org_id: str, agent_id: str | None = None) -> dict[str, Any]:
    row = _fetch_row(db, org_id, agent_id)
    if row is None:
        if agent_id is not None:
            raise IdentityNotFound(f"no agent {agent_id} in org {org_id}")
        raise IdentityNotFound(f"no agent identity for org {org_id}")
    return row


def _update_row(
    db: Any, org_id: str, payload: dict[str, Any], agent_id: str | None = None
) -> dict[str, Any]:
    """Update ONE roster row. The update is keyed by the row's own id
    whenever the row carries one (every service-provisioned row does):
    an org-keyed update would smear the change across the whole
    roster (TW-787). The org-keyed fallback covers legacy rows that
    predate service-issued ids, which are singletons by construction."""
    row = _require_row(db, org_id, agent_id)
    payload = {**payload, "updated_at": _now().isoformat()}
    query = db.table("agt_identities").update(payload)
    if row.get("id"):
        query = query.eq("id", row["id"])
    else:
        query = query.eq("org_id", org_id)
    res = query.execute()
    rows = res.data or []
    return rows[0] if rows else {**row, **payload}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def update_persona(
    db: Any,
    org_id: str,
    patch: dict[str, Any],
    actor: str,
    agent_id: str | None = None,
) -> AgentIdentity:
    """Merge a patch into ONE agent's persona record (the primary
    agent unless agent_id names a roster-mate). The merged result must
    validate as a Persona or nothing is written (fail loud, no
    partial persona ever persists)."""
    row = _require_row(db, org_id, agent_id)
    merged = _deep_merge(row.get("persona") or {}, patch or {})
    persona = Persona(**merged)  # raises IdentityError on any invalid field
    updated = _update_row(
        db,
        org_id,
        {
            "persona": persona.model_dump(mode="json"),
            "version": int(row.get("version") or 1) + 1,
        },
        agent_id=agent_id,
    )
    _record_audit(
        db,
        org_id,
        "persona_update",
        actor,
        f"persona fields changed: {', '.join(sorted((patch or {}).keys()))}",
    )
    return _row_to_identity(updated)


def update_coverage(
    db: Any,
    org_id: str,
    coverage_in: dict[str, Any],
    actor: str,
    agent_id: str | None = None,
) -> AgentIdentity:
    """Replace ONE agent's coverage record. Must validate as Coverage —
    'unconfigured' stays fail-closed and cannot smuggle in windows."""
    row = _require_row(db, org_id, agent_id)
    coverage = Coverage(**(coverage_in or {}))
    updated = _update_row(
        db,
        org_id,
        {
            "coverage": coverage.model_dump(mode="json"),
            "version": int(row.get("version") or 1) + 1,
        },
        agent_id=agent_id,
    )
    _record_audit(
        db, org_id, "coverage_update", actor, f"coverage mode -> {coverage.mode}"
    )
    return _row_to_identity(updated)


def bind_phone_number(
    db: Any,
    org_id: str,
    phone_number: str,
    actor: str,
    agent_id: str | None = None,
) -> AgentIdentity:
    """Bind ONE agent's ONE phone number from the shop's existing
    TW-284/Fonoster number path (org_phone_numbers). Fail-closed:
    a number owned by another org is refused, and a number no org
    owns is refused — the agent never gets a second, invented, or
    reassigned number through this door. TW-787: a number already
    bound to a roster-mate is also refused — one number reaches
    exactly one agent, so phone resolution is never ambiguous."""
    row = _require_row(db, org_id, agent_id)
    number = (phone_number or "").strip()
    if not number:
        raise IdentityError("phone number must be non-empty")
    if row.get("phone_number") == number:
        return _row_to_identity(row)  # already bound; idempotent

    for mate in _fetch_rows(db, org_id):
        if mate.get("id") != row.get("id") and mate.get("phone_number") == number:
            raise IdentityError(
                f"phone number {number!r} is already bound to another agent "
                f"in this org ({mate.get('agent_slug') or mate.get('agent_name')}); "
                "one number reaches one agent"
            )

    res = (
        db.table("org_phone_numbers")
        .select("org_id, phone_number")
        .eq("phone_number", number)
        .limit(1)
        .execute()
    )
    owners = res.data or []
    if not owners:
        raise IdentityError(
            f"phone number {number!r} is not on any shop's number path; "
            "provision the number first (TW-284 lane)"
        )
    if owners[0].get("org_id") != org_id:
        raise IdentityError(
            f"phone number {number!r} belongs to another org; refusing to bind"
        )
    updated = _update_row(
        db,
        org_id,
        {
            "phone_number": number,
            "version": int(row.get("version") or 1) + 1,
        },
        agent_id=agent_id,
    )
    _record_audit(db, org_id, "phone_bind", actor, "agent phone number bound")
    return _row_to_identity(updated)


def set_status(
    db: Any,
    org_id: str,
    status: str,
    actor: str,
    agent_id: str | None = None,
) -> AgentIdentity:
    """Move ONE agent's identity between unprovisioned / active /
    suspended. 'suspended' is the registry-level stop: a suspended
    agent's address stops resolving, so inbound work stops with it —
    for that agent only; roster-mates keep resolving."""
    row = _require_row(db, org_id, agent_id)
    candidate = AgentIdentity(
        org_id=org_id,
        agent_name=row["agent_name"],
        status=status,
    )  # validates status via the model
    updated = _update_row(db, org_id, {"status": candidate.status}, agent_id=agent_id)
    _record_audit(db, org_id, "status_change", actor, f"status -> {status}")
    return _row_to_identity(updated)


# ---------------------------------------------------------------------------
# Inbound routing: mail to the agent becomes queued work
# ---------------------------------------------------------------------------


def resolve_email_recipient(db: Any, email_address: str) -> AgentIdentity | None:
    """Resolve an inbound recipient address to exactly one org's agent.
    Exact match on the normalized address; unknown or suspended
    resolves to None (fail closed). Mail addressed to org A's agent
    carrying org B's data still lands in org A — the address is the
    authority, never the content."""
    address = (email_address or "").strip().lower()
    if not address:
        return None
    res = (
        db.table("agt_identities")
        .select("*")
        .eq("email_address", address)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return None
    identity = _row_to_identity(rows[0])
    if identity.status == "suspended":
        return None
    return identity


def resolve_phone(db: Any, phone_number: str) -> AgentIdentity | None:
    """Resolve an inbound call/SMS target number to one org's agent.
    Same fail-closed contract as email resolution."""
    number = (phone_number or "").strip()
    if not number:
        return None
    res = (
        db.table("agt_identities")
        .select("*")
        .eq("phone_number", number)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return None
    identity = _row_to_identity(rows[0])
    if identity.status == "suspended":
        return None
    return identity


def enqueue_inbound_email(
    db: Any,
    *,
    to_address: str,
    from_address: str,
    subject: str | None,
    body_text: str | None,
    provider_message_id: str,
) -> dict[str, Any] | None:
    """Turn mail to the agent into a queued task in the recipient's org
    ONLY. Returns the queue row, or None when the recipient does not
    resolve (nothing is queued anywhere). Idempotent on
    provider_message_id: a redelivery returns the original row.

    The raw sender address, subject, and body are BRONZE intake: they
    live only in agt_inbound_messages and are never API-returned.
    """
    if not provider_message_id:
        raise IdentityError("provider_message_id is required for inbound enqueue")
    identity = resolve_email_recipient(db, to_address)
    if identity is None:
        return None

    # Idempotency is scoped to the resolved org: a message delivered
    # to two shops' agents queues once PER ORG, and a redelivery to
    # the same org returns its original row.
    existing = (
        db.table("agt_inbound_messages")
        .select("*")
        .eq("provider_message_id", provider_message_id)
        .eq("org_id", identity.org_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    row = {
        "org_id": identity.org_id,
        "identity_id": identity.id,
        "channel": "email",
        "provider": "resend",
        "provider_message_id": provider_message_id,
        "from_address": from_address,
        "from_address_hash": _hash_address(from_address or ""),
        "subject": subject,
        "body_text": body_text,
        "status": "queued",
        "received_at": _now().isoformat(),
    }
    res = db.table("agt_inbound_messages").insert(row).execute()
    inserted = (res.data or [row])[0]
    _record_audit(
        db,
        identity.org_id,
        "inbound_enqueued",
        "resend-webhook",
        f"inbound email queued from sha256:{row['from_address_hash'][:12]}",
    )
    return inserted


def list_inbound(
    db: Any, org_id: str, *, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Queued-work metadata for the org. Metadata ONLY: the raw sender
    address, subject, and body are bronze and are never selected here."""
    query = (
        db.table("agt_inbound_messages")
        .select("id, channel, provider, from_address_hash, status, received_at")
        .eq("org_id", org_id)
    )
    if status:
        query = query.eq("status", status)
    res = query.limit(limit).execute()
    # Project in code as well as in the select list: the bronze columns
    # must be structurally unable to leave this function, whatever the
    # query layer underneath does.
    safe_keys = (
        "id",
        "channel",
        "provider",
        "from_address_hash",
        "status",
        "received_at",
    )
    return [{key: row.get(key) for key in safe_keys} for row in (res.data or [])]


# ---------------------------------------------------------------------------
# Inbound webhook signature verification (Resend uses Svix signing)
# ---------------------------------------------------------------------------


def verify_inbound_signature(
    secret: str | None,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    body: bytes,
    *,
    now: float | None = None,
) -> bool:
    """Verify a Svix-signed webhook (Resend's scheme) with stdlib only.

    Fail-closed on everything: no secret configured, missing headers,
    malformed secret, stale timestamp, or a signature mismatch all
    return False. The signature header may carry several space-
    separated 'v1,<base64>' candidates; any one match verifies.
    """
    if not secret or not svix_id or not svix_timestamp or not svix_signature:
        return False
    try:
        timestamp = int(svix_timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > _SIGNATURE_TOLERANCE_SEC:
        return False
    try:
        key_b64 = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
        key = base64.b64decode(key_b64, validate=True)
    except Exception:
        return False
    signed_content = f"{svix_id}.{svix_timestamp}.".encode("utf-8") + body
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode("utf-8")
    for candidate in svix_signature.split():
        version, _, signature = candidate.partition(",")
        if version == "v1" and signature and hmac.compare_digest(signature, expected):
            return True
    return False
