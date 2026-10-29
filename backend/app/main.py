"""
LBT OS — FastAPI Application Entry Point
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import settings

# TW-593 science data layer V1 — six lanes. Each router carries its own
# /data/v1/science/{lane} prefix except turbulence (mounted with prefix below).
from .data_layer.science.carbon import router as sci_carbon_router
from .data_layer.science.crop_climate import router as sci_crop_router
from .data_layer.science.electrocatalysis import router as sci_electro_router
from .data_layer.science.high_tc import router as sci_hightc_router
from .data_layer.science.pfas import router as sci_pfas_router
from .data_layer.science.subseasonal import router as sci_subseasonal_router
from .data_layer.science.turbulence import router as sci_turbulence_router
from .flows import router as flows_router
from .flags import router as flags_router
from .agentperms import router as agentperms_router
from .telemetry import router as telemetry_router
from .identity import oauth as identity_oauth
from .identity import router as identity_router
from .identity import saml as identity_saml
from .limiter import limiter
from .logging_config import configure_logging, request_id_var
from .notify import router as notify_router
from .routers import (
    admin,
    audit,
    bi,
    calls,
    consults,
    customers,
    expenses,
    integrations,
    leads,
    leak_engine,
    messages,
    metrics,
    organizations,
    revenue_intelligence,
    sales,
    strategy,
    stripe_webhooks,
    tradeview,
    visitor_events,
)
from .services.scheduler import start_scheduler, stop_scheduler
from .social import router as social_router
from .verify import router as verify_router

configure_logging(level="DEBUG" if settings.app_env != "production" else "INFO")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", extra={"env": settings.app_env})
    start_scheduler()
    yield
    stop_scheduler()
    log.info("shutdown")


app = FastAPI(
    title="LBT OS API",
    description="Lean Business Tracker OS — Backend API",
    version="1.0.0",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.app_env != "production" else None,
    lifespan=lifespan,
)

# --- Host header protection ---
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.parsed_trusted_hosts,
)

# --- Rate limiting ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parsed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request ID + access logging ---
@app.middleware("http")
async def request_lifecycle(request: Request, call_next) -> Response:
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = request_id_var.set(rid)
    start = time.monotonic()
    response: Response | None = None
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled_exception", extra={"path": request.url.path})
        raise
    finally:
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code if response is not None else 500,
                "ms": elapsed_ms,
            },
        )
        request_id_var.reset(token)
    response.headers["x-request-id"] = rid
    return response


# --- Routers ---
app.include_router(identity_router.router, prefix="/api/v1")
app.include_router(identity_oauth.oauth_router, prefix="/api/v1")
app.include_router(identity_saml.saml_router, prefix="/api/v1")
app.include_router(notify_router.router, prefix="/api/v1")
app.include_router(flows_router.router, prefix="/api/v1")
app.include_router(flags_router.router, prefix="/api/v1")
app.include_router(agentperms_router.router, prefix="/api/v1")
app.include_router(telemetry_router.router, prefix="/api/v1")
app.include_router(social_router.router, prefix="/api/v1")
app.include_router(verify_router.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(bi.router, prefix="/api/v1")
app.include_router(calls.router, prefix="/api/v1")
app.include_router(consults.router, prefix="/api/v1")
app.include_router(organizations.router, prefix="/api/v1")
app.include_router(leads.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(sales.router, prefix="/api/v1")
app.include_router(expenses.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(stripe_webhooks.router, prefix="/api/stripe")
app.include_router(strategy.router, prefix="/api/v1")
app.include_router(messages.router, prefix="/api/v1")
app.include_router(revenue_intelligence.router, prefix="/api/v1")
app.include_router(leak_engine.router, prefix="/api/v1")
app.include_router(tradeview.router, prefix="/api/v1")
app.include_router(visitor_events.router, prefix="/api/v1")

# TW-593 science data layer V1 — six lanes, each router carries its own
# /data/v1/science/{lane} prefix except turbulence (mounted with prefix here).
app.include_router(sci_electro_router.router)
app.include_router(sci_subseasonal_router.router)
app.include_router(sci_carbon_router.router)
app.include_router(sci_turbulence_router.router, prefix="/data/v1/science/turbulence")
app.include_router(sci_pfas_router.router)
app.include_router(sci_crop_router.router)
app.include_router(sci_hightc_router.router)


@app.get("/health", include_in_schema=False)
def health():
    return {
        "status": "ok",
        "env": settings.app_env,
        "version": "1.0.0",
    }
