"""TW-767 Twistor Agent Identity — service: registry, provisioning,
inbound routing.

One identity record per org. Every read and write is org-scoped in the
service layer (the backend's Supabase client is service-role and
bypasses RLS; the agt_ tables are RLS deny-all as the second wall).

Fail-closed rules enforced here, not left to callers:
- Provisioning allocates the agent's email address deterministically
  from the org, on the Twistor-controlled agent domain. One address
  resolves to exactly one org, forever.
- The phone number BINDS to the shop's existing TW-284/Fonoster
  number path (org_phone_numbers). A number owned by another org, or
  by no org, is refused — never reassigned, never guessed.
- Inbound mail resolves by exact recipient address. An unknown or
  suspended recipient queues NOTHING, anywhere.
- Inbound enqueue is idempotent on the provider message id: a
  redelivered webhook can never double-queue a task.

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
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from .models import (
    AgentIdentity,
    Coverage,
    IdentityError,
    Persona,
    default_coverage,
    default_persona,
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


def _fetch_row(db: Any, org_id: str) -> dict[str, Any] | None:
    res = db.table("agt_identities").select("*").eq("org_id", org_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def _row_to_identity(row: dict[str, Any]) -> AgentIdentity:
    return AgentIdentity(
        id=row.get("id"),
        org_id=row["org_id"],
        agent_name=row["agent_name"],
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
    """The org's identity record, or None if never provisioned."""
    row = _fetch_row(db, org_id)
    return _row_to_identity(row) if row else None


def identity_view(db: Any, org_id: str) -> dict[str, Any]:
    """The record Spec 6 renders and Spec 3 reads. When the org has no
    identity yet, this returns the canonical fail-closed view —
    provisioned False, status 'unprovisioned', default persona and
    unconfigured coverage — so consumers never guess at absence."""
    identity = get_identity(db, org_id)
    if identity is not None:
        return identity.model_dump(mode="json")
    return {
        "id": None,
        "org_id": org_id,
        "agent_name": None,
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


def channel_config(db: Any, org_id: str, channel: str) -> dict[str, Any]:
    """The per-channel read of the identity. The identity block is built
    by ONE assembly path for phone, email, and sms, so the persona a
    channel sees is byte-identical across channels — one consistent
    person, not three configurations that can drift."""
    if channel not in CHANNELS:
        raise IdentityError(f"unknown channel {channel!r}; want one of {CHANNELS}")
    view = identity_view(db, org_id)
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
    """The agent's one email address: deterministic from the org, on the
    Twistor-controlled agent domain. Stable across re-provisioning;
    the org-id suffix makes collisions structurally impossible."""
    suffix = org_id.replace("-", "")[:8]
    return f"{_slugify(org_name)}-{suffix}@{settings.agent_email_domain}"


def provision_identity(
    db: Any, org_id: str, agent_name: str, actor: str
) -> AgentIdentity:
    """Create the org's identity record with fail-closed defaults:
    status 'unprovisioned', default persona, unconfigured coverage,
    the allocated email address, and no phone until one is bound.

    Idempotent: provisioning an org that already has an identity
    returns the existing record unchanged (onboarding retries are
    expected; silently minting a second identity is the failure).
    """
    name = (agent_name or "").strip()
    if not name:
        raise IdentityError("agent name must be non-empty")
    existing = _fetch_row(db, org_id)
    if existing:
        return _row_to_identity(existing)

    org_res = (
        db.table("organizations").select("id, name").eq("id", org_id).limit(1).execute()
    )
    org_rows = org_res.data or []
    if not org_rows:
        raise IdentityError(f"cannot provision agent identity: no org {org_id!r}")
    email_address = agent_email_for(org_id, org_rows[0].get("name") or "")

    row = {
        "org_id": org_id,
        "agent_name": name,
        "email_address": email_address,
        "phone_number": None,
        "persona": default_persona().model_dump(mode="json"),
        "coverage": default_coverage().model_dump(mode="json"),
        "status": "unprovisioned",
        "version": 1,
    }
    try:
        res = db.table("agt_identities").insert(row).execute()
    except Exception as exc:
        raise IdentityError(f"provisioning failed for org {org_id}: {exc}") from exc
    inserted = (res.data or [row])[0]
    _record_audit(
        db, org_id, "provision", actor, f"agent {name!r} provisioned; email allocated"
    )
    return _row_to_identity(inserted)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _require_row(db: Any, org_id: str) -> dict[str, Any]:
    row = _fetch_row(db, org_id)
    if row is None:
        raise IdentityNotFound(f"no agent identity for org {org_id}")
    return row


def _update_row(db: Any, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = {**payload, "updated_at": _now().isoformat()}
    res = db.table("agt_identities").update(payload).eq("org_id", org_id).execute()
    rows = res.data or []
    return rows[0] if rows else {**_require_row(db, org_id), **payload}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def update_persona(
    db: Any, org_id: str, patch: dict[str, Any], actor: str
) -> AgentIdentity:
    """Merge a patch into the persona record. The merged result must
    validate as a Persona or nothing is written (fail loud, no
    partial persona ever persists)."""
    row = _require_row(db, org_id)
    merged = _deep_merge(row.get("persona") or {}, patch or {})
    persona = Persona(**merged)  # raises IdentityError on any invalid field
    updated = _update_row(
        db,
        org_id,
        {
            "persona": persona.model_dump(mode="json"),
            "version": int(row.get("version") or 1) + 1,
        },
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
    db: Any, org_id: str, coverage_in: dict[str, Any], actor: str
) -> AgentIdentity:
    """Replace the coverage record. Must validate as Coverage —
    'unconfigured' stays fail-closed and cannot smuggle in windows."""
    row = _require_row(db, org_id)
    coverage = Coverage(**(coverage_in or {}))
    updated = _update_row(
        db,
        org_id,
        {
            "coverage": coverage.model_dump(mode="json"),
            "version": int(row.get("version") or 1) + 1,
        },
    )
    _record_audit(
        db, org_id, "coverage_update", actor, f"coverage mode -> {coverage.mode}"
    )
    return _row_to_identity(updated)


def bind_phone_number(
    db: Any, org_id: str, phone_number: str, actor: str
) -> AgentIdentity:
    """Bind the agent's ONE phone number from the shop's existing
    TW-284/Fonoster number path (org_phone_numbers). Fail-closed:
    a number owned by another org is refused, and a number no org
    owns is refused — the agent never gets a second, invented, or
    reassigned number through this door."""
    row = _require_row(db, org_id)
    number = (phone_number or "").strip()
    if not number:
        raise IdentityError("phone number must be non-empty")
    if row.get("phone_number") == number:
        return _row_to_identity(row)  # already bound; idempotent

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
    )
    _record_audit(db, org_id, "phone_bind", actor, "agent phone number bound")
    return _row_to_identity(updated)


def set_status(db: Any, org_id: str, status: str, actor: str) -> AgentIdentity:
    """Move the identity between unprovisioned / active / suspended.
    'suspended' is the registry-level stop: a suspended agent's
    address stops resolving, so inbound work stops with it."""
    row = _require_row(db, org_id)
    candidate = AgentIdentity(
        org_id=org_id,
        agent_name=row["agent_name"],
        status=status,
    )  # validates status via the model
    updated = _update_row(db, org_id, {"status": candidate.status})
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
