"""TW-621 Twistor Flags — HTTP API.

Two surfaces:
- Evaluation: POST /flags/evaluate (single + bulk). The SDK hot path.
  Pure evaluation — no toll, no per-call logging (TW-630 wedge:
  unlimited evaluations on the flat subscription).
- Admin: CRUD + flip + audit + LaunchDarkly import. Every mutation is
  attributed to the caller from the Identity JWT.

Org scoping: the caller's active org comes from the Twistor Identity JWT
(claims["org"]). Every endpoint 403s without an active org.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..database import get_db
from ..limiter import limiter
from . import evaluation, migration, service
from .models import DefinitionError

router = APIRouter(prefix="/flags", tags=["flags"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _require_org(creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> str:
    # Local import: identity is the auth authority; flags must not import
    # it at module load (keeps the package importable in tests).
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    if not creds:
        raise HTTPException(status_code=401, detail="missing bearer <redacted>")
    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    org_id = claims.get("org")
    if not org_id:
        raise HTTPException(status_code=403, detail="no active org on token")
    return org_id


def _require_actor(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    if not creds:
        raise HTTPException(status_code=401, detail="missing bearer <redacted>")
    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token")
    return str(claims.get("sub") or claims.get("email") or "unknown")


class EvaluateIn(BaseModel):
    flag_key: str
    targeting_key: str = ""
    attributes: dict[str, Any] = {}


class BulkEvaluateIn(BaseModel):
    evaluations: list[EvaluateIn]


class FlagCreateIn(BaseModel):
    definition: dict[str, Any]


class FlagUpdateIn(BaseModel):
    patch: dict[str, Any]


class FlipIn(BaseModel):
    enabled: bool


class LDImportIn(BaseModel):
    ld_flag: dict[str, Any]
    strict: bool = True


@router.post("/evaluate")
@limiter.limit("600/minute")
def evaluate_flag(
    request: Request,
    body: EvaluateIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Evaluate one flag. No toll, no per-call log — unlimited by design."""
    flag = service.get_flag(db, org_id, body.flag_key)
    if flag is None:
        raise HTTPException(status_code=404, detail="flag not found")
    result = evaluation.evaluate(
        flag,
        {"targeting_key": body.targeting_key, "attributes": body.attributes},
    )
    return result.model_dump(mode="json")


@router.post("/evaluate/bulk")
@limiter.limit("120/minute")
def evaluate_bulk(
    request: Request,
    body: BulkEvaluateIn,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Evaluate many flags in one round trip. Same no-toll contract."""
    if len(body.evaluations) > 100:
        raise HTTPException(status_code=400, detail="max 100 evaluations per call")
    flags = {f.key: f for f in service.list_flags(db, org_id)}
    out = []
    for item in body.evaluations:
        flag = flags.get(item.flag_key)
        if flag is None:
            out.append({"flag_key": item.flag_key, "error": "flag not found"})
            continue
        result = evaluation.evaluate(
            flag,
            {"targeting_key": item.targeting_key, "attributes": item.attributes},
        )
        out.append(result.model_dump(mode="json"))
    return {"evaluations": out}


@router.post("/admin", status_code=201)
@limiter.limit("60/minute")
def create_flag(
    request: Request,
    body: FlagCreateIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        row = service.create_flag(db, org_id, body.definition, actor)
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"key": row["key"], "version": row["version"]}


@router.get("/admin")
@limiter.limit("120/minute")
def list_flags(
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return {
        "flags": [f.model_dump(mode="json") for f in service.list_flags(db, org_id)]
    }


@router.get("/admin/{flag_key}")
@limiter.limit("120/minute")
def get_flag(
    request: Request,
    flag_key: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    flag = service.get_flag(db, org_id, flag_key)
    if flag is None:
        raise HTTPException(status_code=404, detail="flag not found")
    return flag.model_dump(mode="json")


@router.patch("/admin/{flag_key}")
@limiter.limit("60/minute")
def update_flag(
    request: Request,
    flag_key: str,
    body: FlagUpdateIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        row = service.update_flag(db, org_id, flag_key, body.patch, actor)
    except service.FlagNotFound:
        raise HTTPException(status_code=404, detail="flag not found")
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"key": row["key"], "version": row["version"]}


@router.post("/admin/{flag_key}/flip")
@limiter.limit("60/minute")
def flip_flag(
    request: Request,
    flag_key: str,
    body: FlipIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        row = service.flip_flag(db, org_id, flag_key, body.enabled, actor)
    except service.FlagNotFound:
        raise HTTPException(status_code=404, detail="flag not found")
    return {"key": row["key"], "enabled": body.enabled}


@router.delete("/admin/{flag_key}", status_code=204)
@limiter.limit("60/minute")
def delete_flag(
    request: Request,
    flag_key: str,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    try:
        service.delete_flag(db, org_id, flag_key, actor)
    except service.FlagNotFound:
        raise HTTPException(status_code=404, detail="flag not found")
    return None


@router.get("/admin/{flag_key}/audit")
@limiter.limit("120/minute")
def flag_audit(
    request: Request,
    flag_key: str,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    return {"audit": service.list_audit(db, org_id, flag_key)}


@router.post("/admin/import/launchdarkly", status_code=201)
@limiter.limit("30/minute")
def import_launchdarkly(
    request: Request,
    body: LDImportIn,
    org_id: str = Depends(_require_org),
    actor: str = Depends(_require_actor),
    db: Client = Depends(get_db),
):
    """Import a LaunchDarkly flag. Strict mode (default) rejects unmapped
    constructs; non-strict imports what maps and returns the unmapped list."""
    try:
        if body.strict:
            flag = migration.from_launchdarkly(body.ld_flag)
            unmapped: list[str] = []
        else:
            report = migration.migrate_with_report(body.ld_flag)
            flag, unmapped = report.flag, report.unmapped
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        row = service.create_flag(db, org_id, flag, actor)
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"key": row["key"], "version": row["version"], "unmapped": unmapped}


@router.post("/admin/apply-schedules")
@limiter.limit("30/minute")
def apply_schedules(
    request: Request,
    org_id: str = Depends(_require_org),
    db: Client = Depends(get_db),
):
    """Apply due scheduled flips. The scheduler tick calls this."""
    applied = service.apply_due_schedules(db, org_id, datetime.now(timezone.utc))
    return {"applied": applied}
