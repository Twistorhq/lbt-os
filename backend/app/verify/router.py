"""Twistor Verification Engine: verdict-only API (TW-658, spec S5.6).

The boundary every consumer (Twistor Identity first) talks to:

- POST /verify/sessions: create a session. Refused with 403 while
  the D3 production capture flag is off: capture cannot occur.
  The caller may supply device_ref and network_ref, pre-hashed
  velocity references; raw device or network identifiers never
  reach this service.
- POST /verify/sessions/{id}/document and /selfie: guided-capture
  payloads over TLS. Images go to the encrypted staging path and
  nowhere else; identity data never travels in URLs.
- POST /verify/sessions/{id}/liveness: issue the session's active
  liveness challenges, so stage 4 can complete over this surface;
  the selfie is evaluated against the set issued here.
- GET /verify/sessions/{id}: the verdict only (status, tier,
  confidence band, age band, decided_at), owner only. No images,
  no extracted fields, no scores beyond the band.
- POST /verify/anchors/lookup: service-to-service; answers whether
  an identity anchor exists and whether it is banned. Booleans.

Auth is the Twistor Identity JWT, verified exactly as the Social
router verifies it; the member's user_id is the token subject and
is never taken from a request body.
"""

from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from ..database import get_db
from ..limiter import limiter

log = logging.getLogger(__name__)

router = APIRouter(prefix="/verify", tags=["verify"])
_bearer_scheme = HTTPBearer(auto_error=False)


def _verified_claims(
    creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="missing credentials")
    # Local import, the Social router's pattern: identity is the
    # auth authority and is not imported at module load.
    from ..identity import service as idn_service
    from ..identity import tokens as idn_tokens

    try:
        claims = idn_tokens.verify_access_token(
            idn_service.get_verification_keypairs(), creds.credentials
        )
    except idn_tokens.TokenError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="token has no subject")
    return claims


def get_pipeline(db: Client = Depends(get_db)):
    """Build the production pipeline (overridable in tests).

    Heavy wiring is lazy: importing this router never imports the
    model stack, and a flag-off deployment never builds a port.
    """
    import os

    from .engine.adapters import real_ports
    from .engine.config import EngineConfig
    from .engine.crypto import AesGcmEncryptor
    from .engine.hashing import FieldHasher
    from .engine.pipeline import VerificationPipeline
    from .engine.staging import EncryptedFileStaging
    from .engine.store import SupabaseVerifyStore

    config = EngineConfig.from_env()
    if not config.production_capture_enabled:
        # The D3 refusal runs ahead of key material: a flag-off
        # deployment answers the honest 403 even before its keys
        # are provisioned (Rosa's m3). Keys are mandatory only
        # when capture is on.
        raise HTTPException(
            status_code=403,
            detail=(
                "production capture is disabled (TW-654 D3): the "
                "capture flag stays off until counsel's written "
                "review is filed"
            ),
        )
    template_key = base64.b64decode(os.environ["VFY_TEMPLATE_KEY_B64"])
    encryptor = AesGcmEncryptor(template_key)
    from pathlib import Path

    staging = EncryptedFileStaging(
        Path(os.environ.get("VFY_STAGING_DIR", "/tmp/vfy-staging")), encryptor
    )
    return VerificationPipeline(
        store=SupabaseVerifyStore(db),
        staging=staging,
        hasher=FieldHasher(os.environ["VFY_HMAC_KEY"].encode("utf-8")),
        encryptor=encryptor,
        ports=real_ports(),
        config=config,
    )


class SessionCreate(BaseModel):
    document_type: str
    tier_requested: int = 2
    # Pre-hashed velocity references (device, network), supplied
    # by the calling client. The verify service never sees the raw
    # identifiers these were hashed from.
    device_ref: str | None = None
    network_ref: str | None = None


class DocumentSubmit(BaseModel):
    image_b64: str


class SelfieSubmit(BaseModel):
    image_b64: str
    # Landmark frames from the guided capture (MediaPipe Face Mesh
    # runs in the capture client per spec S5.2.4); each frame is a
    # list of [x, y, z] landmark triples.
    frames: list = []


class AnchorLookup(BaseModel):
    anchor_hash: str


def _decode_image(payload: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="image_b64 is not base64")


@router.post("/sessions", status_code=201)
@limiter.limit("10/hour")
def create_session(
    request: Request,
    body: SessionCreate,
    claims: dict = Depends(_verified_claims),
    pipeline=Depends(get_pipeline),
):
    from .engine.errors import (
        CaptureDisabled,
        ConsentRequired,
        DocumentClassNotEnabled,
    )

    try:
        view = pipeline.start_session(
            claims["sub"],
            body.document_type,
            body.tier_requested,
            device_ref=body.device_ref,
            network_ref=body.network_ref,
        )
    except CaptureDisabled as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (ConsentRequired, DocumentClassNotEnabled) as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"verification_id": view.id, "status": view.status}


@router.post("/sessions/{session_id}/document")
@limiter.limit("20/hour")
def submit_document(
    request: Request,
    session_id: str,
    body: DocumentSubmit,
    claims: dict = Depends(_verified_claims),
    pipeline=Depends(get_pipeline),
):
    from app.verify.benchmark.stages import StageUnavailable

    from .engine.errors import SessionStateError, UnknownSession

    try:
        status = pipeline.submit_document(
            session_id, claims["sub"], _decode_image(body.image_b64)
        )
    except UnknownSession:
        raise HTTPException(status_code=404, detail="unknown session")
    except PermissionError:
        raise HTTPException(status_code=403, detail="not your session")
    except SessionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except StageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"status": status}


@router.post("/sessions/{session_id}/liveness")
@limiter.limit("20/hour")
def begin_liveness(
    request: Request,
    session_id: str,
    claims: dict = Depends(_verified_claims),
    pipeline=Depends(get_pipeline),
):
    from .engine.errors import SessionStateError, UnknownSession

    try:
        challenges = pipeline.begin_liveness(session_id, claims["sub"])
    except UnknownSession:
        raise HTTPException(status_code=404, detail="unknown session")
    except PermissionError:
        raise HTTPException(status_code=403, detail="not your session")
    except SessionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"challenges": challenges}


@router.post("/sessions/{session_id}/selfie")
@limiter.limit("20/hour")
def submit_selfie(
    request: Request,
    session_id: str,
    body: SelfieSubmit,
    claims: dict = Depends(_verified_claims),
    pipeline=Depends(get_pipeline),
):
    from app.verify.benchmark.stages import StageUnavailable

    from .engine.errors import SessionStateError, UnknownSession

    frames = [[tuple(point) for point in frame] for frame in body.frames]
    try:
        view = pipeline.submit_selfie(
            session_id, claims["sub"], _decode_image(body.image_b64), frames
        )
    except UnknownSession:
        raise HTTPException(status_code=404, detail="unknown session")
    except PermissionError:
        raise HTTPException(status_code=403, detail="not your session")
    except SessionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except StageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return view.as_dict()


@router.get("/sessions/{session_id}")
@limiter.limit("60/minute")
def get_session(
    request: Request,
    session_id: str,
    claims: dict = Depends(_verified_claims),
    pipeline=Depends(get_pipeline),
):
    from .engine.errors import UnknownSession

    try:
        view = pipeline.get_verdict(session_id, claims["sub"])
    except UnknownSession:
        raise HTTPException(status_code=404, detail="unknown session")
    except PermissionError:
        raise HTTPException(status_code=403, detail="not your session")
    return view.as_dict()


@router.post("/anchors/lookup")
@limiter.limit("120/minute")
def anchor_lookup(
    request: Request,
    body: AnchorLookup,
    claims: dict = Depends(_verified_claims),
    pipeline=Depends(get_pipeline),
):
    return pipeline.anchor_lookup(body.anchor_hash)
