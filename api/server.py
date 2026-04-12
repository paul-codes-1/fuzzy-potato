"""FastAPI server for RAG Q&A with multi-tenant authentication."""

import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from api.analytics import get_analytics_store, init_analytics
from api.analytics_routes import router as analytics_router
from api.audit import get_audit_logger, init_audit
from api.audit_routes import router as audit_router
from api.billing_routes import router as billing_router
from api.branding import init_branding
from api.branding_routes import router as branding_router
from api.export import init_export
from api.export_routes import router as export_router
from api.metrics import MetricsMiddleware, init_metrics
from api.metrics_routes import router as metrics_router
from api.openapi_config import configure_openapi
from api.search import init_search
from api.search_routes import router as search_router
from api.diff import init_diff
from api.diff_routes import router as diff_router
from api.auth import (
    Tenant,
    get_tenant_store,
    init_auth,
    require_admin,
    require_tenant,
    require_tenant_legacy,
    PLAN_LIMITS,
    VALID_PLANS,
)
from api.ingest import get_chroma_collection
from api.logging_config import setup_logging
from api.query import ask, chat, load_clip_metadata
# Optional modules — fail gracefully if deps are missing
_optional_modules = {}

def _try_import(name, import_fn):
    try:
        _optional_modules[name] = import_fn()
    except (ImportError, ModuleNotFoundError) as e:
        print(f"  [info] Optional module {name} not available: {e}")

_try_import("translate", lambda: __import__("api.translate", fromlist=["TranslationService", "SUPPORTED_LANGUAGES"]))
_try_import("scheduler", lambda: __import__("api.scheduler", fromlist=["init_scheduler", "get_scheduler"]))
_try_import("scheduler_routes", lambda: __import__("api.scheduler_routes", fromlist=["router"]))
_try_import("webhooks", lambda: __import__("api.webhooks", fromlist=["init_webhooks"]))
_try_import("webhook_routes", lambda: __import__("api.webhook_routes", fromlist=["router"]))
_try_import("slack", lambda: __import__("api.integrations.slack", fromlist=["init_slack"]))
_try_import("slack_routes", lambda: __import__("api.integrations.slack_routes", fromlist=["router"]))
_try_import("teams", lambda: __import__("api.integrations.teams", fromlist=["init_teams"]))
_try_import("teams_routes", lambda: __import__("api.integrations.teams_routes", fromlist=["router"]))
_try_import("zapier", lambda: __import__("api.integrations.zapier", fromlist=["init_zapier"]))
_try_import("zapier_routes", lambda: __import__("api.integrations.zapier_routes", fromlist=["router"]))
_try_import("status", lambda: __import__("api.status", fromlist=["init_status"]))
_try_import("status_routes", lambda: __import__("api.status_routes", fromlist=["router"]))
_try_import("sso", lambda: __import__("api.sso", fromlist=["init_sso"]))
_try_import("sso_routes", lambda: __import__("api.sso_routes", fromlist=["router"]))
_try_import("tracker", lambda: __import__("api.tracker", fromlist=["init_tracker"]))
_try_import("tracker_routes", lambda: __import__("api.tracker_routes", fromlist=["router"]))
_try_import("push", lambda: __import__("api.push", fromlist=["init_push"]))
_try_import("push_routes", lambda: __import__("api.push_routes", fromlist=["router"]))
_try_import("health", lambda: __import__("api.customer_health", fromlist=["init_health"]))
_try_import("health_routes", lambda: __import__("api.health_routes", fromlist=["router"]))
_try_import("highlights", lambda: __import__("api.highlights", fromlist=["init_highlights"]))
_try_import("highlights_routes", lambda: __import__("api.highlights_routes", fromlist=["router"]))
_try_import("calendar_mod", lambda: __import__("api.calendar", fromlist=["init_calendar"]))
_try_import("calendar_routes", lambda: __import__("api.calendar_routes", fromlist=["router"]))
_try_import("sentiment", lambda: __import__("api.sentiment", fromlist=["init_sentiment"]))
_try_import("sentiment_routes", lambda: __import__("api.sentiment_routes", fromlist=["router"]))
_try_import("database", lambda: __import__("api.database", fromlist=["init_database"]))
_try_import("digest", lambda: __import__("api.digest", fromlist=["init_digest"]))
_try_import("digest_routes", lambda: __import__("api.digest_routes", fromlist=["router"]))
_try_import("reports", lambda: __import__("api.reports", fromlist=["init_reports"]))
_try_import("report_routes", lambda: __import__("api.report_routes", fromlist=["router"]))
_try_import("backup", lambda: __import__("api.backup", fromlist=["init_backup", "BackupManager"]))
_try_import("backup_routes", lambda: __import__("api.backup_routes", fromlist=["router"]))
_try_import("metrics", lambda: __import__("api.metrics", fromlist=["MetricsCollector", "MetricsMiddleware", "init_metrics"]))
_try_import("metrics_routes", lambda: __import__("api.metrics_routes", fromlist=["router"]))
_try_import("feature_flags", lambda: __import__("api.feature_flags", fromlist=["init_feature_flags", "FeatureFlagManager"]))
_try_import("feature_flags_routes", lambda: __import__("api.feature_flags_routes", fromlist=["router"]))
_try_import("compliance", lambda: __import__("api.compliance", fromlist=["ComplianceReporter", "init_compliance"]))
_try_import("compliance_routes", lambda: __import__("api.compliance_routes", fromlist=["router"]))
_try_import("cost", lambda: __import__("api.cost", fromlist=["init_cost_tracker", "get_cost_tracker"]))
_try_import("cost_routes", lambda: __import__("api.cost_routes", fromlist=["router"]))
_try_import("saved_searches", lambda: __import__("api.saved_searches", fromlist=["init_saved_searches_manager"]))
_try_import("saved_searches_routes", lambda: __import__("api.saved_searches_routes", fromlist=["router"]))
_try_import("leads", lambda: __import__("api.leads", fromlist=["init_lead_store", "get_lead_store"]))
_try_import("leads_routes", lambda: __import__("api.leads_routes", fromlist=["router"]))
_try_import("query_cache", lambda: __import__("api.query_cache", fromlist=["init_query_cache", "get_query_cache"]))
_try_import("query_cache_routes", lambda: __import__("api.query_cache_routes", fromlist=["router"]))

def _opt(name, attr=None):
    """Get an optional module or attribute, returns None if not loaded."""
    mod = _optional_modules.get(name)
    if mod and attr:
        return getattr(mod, attr, None)
    return mod

load_dotenv()

# Initialize structured JSON logging
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")

VERSION = "1.0.0"

# Set during lifespan startup
_server_start_time: Optional[float] = None

# Singletons -- initialized lazily on first request
_collection = None
_clip_metadata = None
_openai_client = None


def _get_collection():
    global _collection
    if _collection is None:
        _collection = get_chroma_collection(OUTPUT_DIR)
    return _collection


def _get_clip_metadata():
    global _clip_metadata
    if _clip_metadata is None:
        _clip_metadata = load_clip_metadata(OUTPUT_DIR)
    return _clip_metadata


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic()
    return _anthropic_client


_translation_service = None


def _get_translation_service():
    global _translation_service
    if _translation_service is None:
        translate_mod = _opt("translate")
        if translate_mod:
            _translation_service = translate_mod.TranslationService(_get_openai_client())
    return _translation_service


def _get_supported_languages():
    translate_mod = _opt("translate")
    return getattr(translate_mod, "SUPPORTED_LANGUAGES", {}) if translate_mod else {}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup -- initialize auth store, analytics, audit, scheduler; ChromaDB loads lazily on first request."""
    global _server_start_time
    _server_start_time = time.time()
    init_auth(OUTPUT_DIR)
    init_analytics(OUTPUT_DIR)
    init_audit(OUTPUT_DIR)
    init_branding(OUTPUT_DIR)
    init_export(OUTPUT_DIR)
    init_metrics(OUTPUT_DIR)
    # Initialize diff engine
    init_diff(OUTPUT_DIR)
    # Initialize search engine and build index from existing clips
    search_engine = init_search(OUTPUT_DIR)
    try:
        stats = search_engine.build_index(OUTPUT_DIR)
        logger.info("Search index built: %s", stats)
    except Exception:
        logger.warning("Failed to build search index on startup", exc_info=True)
    # Initialize database layer (needed by digest and other DB-backed modules)
    db_init = _opt("database", "init_database")
    if db_init:
        try:
            db_init(OUTPUT_DIR)
        except Exception:
            logger.warning("Failed to initialize database", exc_info=True)

    # Initialize optional modules (skip if deps missing)
    for name, attr in [
        ("webhooks", "init_webhooks"), ("slack", "init_slack"),
        ("teams", "init_teams"), ("status", "init_status"),
        ("tracker", "init_tracker"), ("sso", "init_sso"),
        ("push", "init_push"), ("health", "init_health"),
        ("zapier", "init_zapier"), ("highlights", "init_highlights"),
        ("calendar_mod", "init_calendar"), ("sentiment", "init_sentiment"),
        ("digest", "init_digest"), ("reports", "init_reports"),
        ("backup", "init_backup"), ("metrics", "init_metrics"),
        ("feature_flags", "init_feature_flags"), ("compliance", "init_compliance"),
        ("cost", "init_cost_tracker"),
        ("saved_searches", "init_saved_searches_manager"),
        ("leads", "init_lead_store"),
        ("query_cache", "init_query_cache"),
    ]:
        init_fn = _opt(name, attr)
        if init_fn:
            init_fn(OUTPUT_DIR)

    # Initialize and start the meeting ingestion scheduler
    scheduler_init = _opt("scheduler", "init_scheduler")
    _scheduler = None
    if scheduler_init:
        _scheduler = scheduler_init(OUTPUT_DIR)
        _scheduler.start()

    logger.info("API server starting (ChromaDB will load on first request)")
    yield

    # Graceful shutdown
    if _scheduler:
        _scheduler.shutdown()


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="Meeting API", lifespan=lifespan)

# Configurable CORS: set CORS_ORIGINS as comma-separated list, or defaults to
# allowing all origins in dev mode.
_cors_origins = os.environ.get("CORS_ORIGINS", "*")
_origins_list = [o.strip() for o in _cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns a unique request_id to every request and logs request/response."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.perf_counter()

        logger.info(
            "request_started",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client_ip": request.client.host if request.client else None,
            },
        )

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        response.headers["X-Request-ID"] = request_id
        return response


class AuditMiddleware(BaseHTTPMiddleware):
    """Automatically logs every API request to the audit log.

    Captures endpoint, method, tenant (from X-API-Key), request_id,
    status code, and duration for every request.
    """

    # Paths that are too noisy or not meaningful to audit
    _SKIP_PATHS = {"/health", "/api/health", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        # Skip health checks and docs
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        # Best-effort audit logging -- never block the response
        try:
            audit = get_audit_logger()
            request_id = getattr(request.state, "request_id", None)
            api_key = request.headers.get("X-API-Key")
            ip_address = request.client.host if request.client else None

            # Determine tenant_id from the API key (lightweight lookup)
            tenant_id = "anonymous"
            if api_key:
                admin_key = os.environ.get("ADMIN_API_KEY")
                if api_key == admin_key:
                    tenant_id = "_admin"
                else:
                    store = get_tenant_store()
                    tenant = store.get_by_api_key(api_key)
                    if tenant:
                        tenant_id = tenant.id

            audit.log(
                tenant_id=tenant_id,
                action="api.request",
                resource_type="api",
                resource_id=request.url.path,
                details={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
                user_api_key=api_key,
                ip_address=ip_address,
                request_id=request_id,
            )
        except Exception:
            logger.debug("Failed to write audit log for request", exc_info=True)

        return response


app.add_middleware(MetricsMiddleware)  # collector resolved lazily after init_metrics()
app.add_middleware(AuditMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Mount core routes (always available)
app.include_router(analytics_router)
app.include_router(audit_router)
app.include_router(billing_router)
app.include_router(branding_router)
app.include_router(export_router)
app.include_router(metrics_router)
app.include_router(search_router)
app.include_router(diff_router)

# Mount optional routes (skip if deps are missing)
for _route_mod_name in [
    "webhook_routes", "scheduler_routes", "slack_routes", "teams_routes",
    "status_routes", "tracker_routes", "sso_routes", "push_routes",
    "health_routes", "zapier_routes", "highlights_routes",
    "calendar_routes", "sentiment_routes", "digest_routes",
    "report_routes", "backup_routes",
    "feature_flags_routes", "compliance_routes",
    "cost_routes",
    "saved_searches_routes",
    "leads_routes",
    "query_cache_routes",
]:
    _route_mod = _opt(_route_mod_name)
    if _route_mod and hasattr(_route_mod, "router"):
        app.include_router(_route_mod.router)

# ---------------------------------------------------------------------------
# OpenAPI schema customization (must be after all routers are mounted)
# ---------------------------------------------------------------------------
configure_openapi(app)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    meeting_body: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        if len(v) > 2000:
            raise ValueError("question must be under 2000 characters")
        return v


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        return v


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    meeting_body: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None
    model_provider: str = "openai"

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v):
        if not v:
            raise ValueError("messages must not be empty")
        if v[-1].role != "user":
            raise ValueError("last message must be from the user")
        return v

    @field_validator("model_provider")
    @classmethod
    def model_provider_must_be_valid(cls, v: str) -> str:
        if v not in ("openai", "anthropic"):
            raise ValueError("model_provider must be 'openai' or 'anthropic'")
        return v


class CreateTenantRequest(BaseModel):
    id: str
    name: str
    granicus_host: str
    granicus_view_id: str = ""
    plan: str = "starter"

    @field_validator("plan")
    @classmethod
    def plan_must_be_valid(cls, v: str) -> str:
        if v not in VALID_PLANS:
            raise ValueError(f"Invalid plan. Choose from: {', '.join(sorted(VALID_PLANS))}")
        return v


class UpdatePlanRequest(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def plan_must_be_valid(cls, v: str) -> str:
        if v not in VALID_PLANS:
            raise ValueError(f"Invalid plan. Choose from: {', '.join(sorted(VALID_PLANS))}")
        return v


# ---------------------------------------------------------------------------
# Helper: build Granicus URL template from tenant context
# ---------------------------------------------------------------------------

def _granicus_url_template(tenant: Tenant) -> str:
    host = tenant.granicus_host
    view_id = tenant.granicus_view_id
    if not host:
        return ""
    return f"https://{host}/player/clip/{{clip_id}}?view_id={view_id}&entrytime={{timestamp}}"


def _build_filters(request) -> dict:
    filters = {}
    if request.meeting_body:
        filters["meeting_body"] = request.meeting_body
    if request.date_after:
        filters["date_after"] = request.date_after
    if request.date_before:
        filters["date_before"] = request.date_before
    return filters


# ---------------------------------------------------------------------------
# V1 API endpoints (authenticated)
# ---------------------------------------------------------------------------

@app.post("/api/v1/ask")
def ask_v1(
    request: AskRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant),
):
    return _handle_ask(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.post("/api/v1/chat")
def chat_v1(
    request: ChatRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant),
):
    return _handle_chat(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.get("/api/v1/health")
def health_v1(tenant: Tenant = Depends(require_tenant)):
    return _handle_health(tenant)


@app.get("/api/v1/system/info")
def system_info(_: bool = Depends(require_admin)):
    """Return system health and stats. Admin-only."""
    store = get_tenant_store()
    tenant_count = len(store.list_all())

    clips_dir = os.path.join(OUTPUT_DIR, "clips")
    total_clips = 0
    if os.path.isdir(clips_dir):
        total_clips = sum(
            1 for entry in os.scandir(clips_dir) if entry.is_dir()
        )

    uptime = round(time.time() - _server_start_time, 1) if _server_start_time else 0.0

    database_backend = "postgresql" if os.environ.get("DATABASE_URL") else "sqlite"

    return {
        "version": VERSION,
        "tenant_count": tenant_count,
        "total_clips_processed": total_clips,
        "uptime_seconds": uptime,
        "python_version": sys.version,
        "database_backend": database_backend,
    }


# ---------------------------------------------------------------------------
# Legacy endpoints (dev-friendly backward compatible paths)
# ---------------------------------------------------------------------------

@app.post("/ask")
def ask_endpoint_direct(
    request: AskRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant_legacy),
):
    return _handle_ask(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.post("/api/ask")
def ask_endpoint(
    request: AskRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant_legacy),
):
    return _handle_ask(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.post("/api/chat")
def chat_endpoint(
    request: ChatRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant_legacy),
):
    return _handle_chat(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.post("/chat")
def chat_endpoint_direct(
    request: ChatRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant_legacy),
):
    return _handle_chat(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.get("/health")
def health_endpoint():
    """Lightweight health check -- no auth required, no ChromaDB loading."""
    result = {"status": "ok"}
    if _collection is not None:
        result["chunks_indexed"] = _collection.count()
    if _clip_metadata is not None:
        result["clips_indexed"] = len(_clip_metadata)
    return result


@app.get("/api/health")
def health_endpoint_api():
    return health_endpoint()


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/admin/tenants")
def admin_list_tenants(_: bool = Depends(require_admin)):
    store = get_tenant_store()
    tenants = store.list_all()
    return {
        "tenants": [
            {
                "id": t.id,
                "name": t.name,
                "granicus_host": t.granicus_host,
                "granicus_view_id": t.granicus_view_id,
                "plan": t.plan,
                "created_at": t.created_at,
            }
            for t in tenants
        ]
    }


@app.post("/api/v1/admin/tenants")
def admin_create_tenant(
    request: CreateTenantRequest,
    _: bool = Depends(require_admin),
):
    store = get_tenant_store()
    try:
        tenant = store.create(
            tenant_id=request.id,
            name=request.name,
            granicus_host=request.granicus_host,
            granicus_view_id=request.granicus_view_id,
            plan=request.plan,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Auto-register a default schedule for the new tenant
    try:
        _get_sched = _opt("scheduler", "get_scheduler")
        if _get_sched:
            scheduler = _get_sched()
            scheduler.register_tenant(tenant.id)
            logger.info("auto_registered_schedule", extra={"tenant_id": tenant.id})
    except Exception:
        logger.warning("failed_to_register_schedule", extra={"tenant_id": tenant.id}, exc_info=True)

    return {
        "id": tenant.id,
        "name": tenant.name,
        "granicus_host": tenant.granicus_host,
        "granicus_view_id": tenant.granicus_view_id,
        "plan": tenant.plan,
        "api_key": tenant.api_key,
        "created_at": tenant.created_at,
    }


@app.delete("/api/v1/admin/tenants/{tenant_id}")
def admin_delete_tenant(tenant_id: str, _: bool = Depends(require_admin)):
    store = get_tenant_store()
    if not store.delete(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return {"deleted": tenant_id}


@app.post("/api/v1/admin/tenants/{tenant_id}/rotate-key")
def admin_rotate_key(tenant_id: str, _: bool = Depends(require_admin)):
    store = get_tenant_store()
    new_key = store.rotate_key(tenant_id)
    if not new_key:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return {"tenant_id": tenant_id, "api_key": new_key}


@app.patch("/api/v1/admin/tenants/{tenant_id}/plan")
def admin_update_plan(
    tenant_id: str,
    request: UpdatePlanRequest,
    _: bool = Depends(require_admin),
):
    store = get_tenant_store()
    try:
        if not store.update_plan(tenant_id, request.plan):
            raise HTTPException(status_code=404, detail="Tenant not found.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"tenant_id": tenant_id, "plan": request.plan}


@app.get("/api/v1/admin/plans")
def admin_list_plans(_: bool = Depends(require_admin)):
    return {
        "plans": {
            plan: {"queries_per_month": limit if limit else "unlimited"}
            for plan, limit in PLAN_LIMITS.items()
        }
    }


# ---------------------------------------------------------------------------
# Shared handler implementations
# ---------------------------------------------------------------------------

def _handle_ask(
    request: AskRequest,
    tenant: Tenant,
    lang: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    start_time = time.perf_counter()
    try:
        collection = _get_collection()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

        filters = _build_filters(request)
        # Scope to tenant's data when not in dev mode
        if tenant.id != "dev":
            filters["tenant_id"] = tenant.id

        result = ask(
            question=request.question,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            filters=filters if filters else None,
            tenant_id=tenant.id,
            request_id=request_id,
        )

        # Inject tenant-specific Granicus URLs into sources
        url_template = _granicus_url_template(tenant)
        if url_template:
            for source in result.get("sources", []):
                timestamp = source.get("timestamp", 0)
                source["granicus_url"] = url_template.format(
                    clip_id=source.get("clip_id", ""),
                    timestamp=timestamp,
                )

        result["tenant"] = tenant.id

        # Translate response if a non-English language was requested
        if lang and lang != "en" and lang in _get_supported_languages():
            translator = _get_translation_service()
            result = translator.translate_response(
                result,
                lang,
                tenant_id=tenant.id,
                request_id=request_id,
            )

        # Log analytics event
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        try:
            analytics = get_analytics_store()
            analytics.log_query(
                tenant_id=tenant.id,
                question=request.question,
                model="gpt-4o",
                response_time_ms=elapsed_ms,
                chunks_retrieved=len(result.get("sources", [])),
                meeting_body=request.meeting_body,
            )
        except Exception:
            logger.debug("Failed to log analytics for ask", exc_info=True)

        return result
    except Exception as e:
        logger.error("ask failed for tenant=%s: %s", tenant.id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your question.")


def _handle_chat(
    request: ChatRequest,
    tenant: Tenant,
    lang: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    start_time = time.perf_counter()
    try:
        collection = _get_collection()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

        filters = _build_filters(request)
        if tenant.id != "dev":
            filters["tenant_id"] = tenant.id

        anthropic_client = None
        if request.model_provider == "anthropic":
            anthropic_client = _get_anthropic_client()

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        result = chat(
            messages=messages,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            anthropic_client=anthropic_client,
            filters=filters if filters else None,
            model_provider=request.model_provider,
            tenant_id=tenant.id,
            request_id=request_id,
        )

        # Inject tenant-specific Granicus URLs into sources
        url_template = _granicus_url_template(tenant)
        if url_template:
            for source in result.get("sources", []):
                timestamp = source.get("timestamp", 0)
                source["granicus_url"] = url_template.format(
                    clip_id=source.get("clip_id", ""),
                    timestamp=timestamp,
                )

        result["tenant"] = tenant.id

        # Translate response if a non-English language was requested
        if lang and lang != "en" and lang in _get_supported_languages():
            translator = _get_translation_service()
            result = translator.translate_response(
                result,
                lang,
                tenant_id=tenant.id,
                request_id=request_id,
            )

        # Log analytics event
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        try:
            analytics = get_analytics_store()
            last_question = messages[-1]["content"] if messages else ""
            analytics.log_query(
                tenant_id=tenant.id,
                question=last_question,
                model=request.model_provider,
                response_time_ms=elapsed_ms,
                chunks_retrieved=len(result.get("sources", [])),
                meeting_body=request.meeting_body,
            )
        except Exception:
            logger.debug("Failed to log analytics for chat", exc_info=True)

        return result
    except Exception as e:
        logger.error("chat failed for tenant=%s: %s", tenant.id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your chat.")


def _handle_health(tenant: Tenant) -> dict:
    result = {"status": "ok", "tenant": tenant.id}
    if _collection is not None:
        result["chunks_indexed"] = _collection.count()
    if _clip_metadata is not None:
        result["clips_indexed"] = len(_clip_metadata)
    return result


# ---------------------------------------------------------------------------
# Widget endpoints (same auth but exposed for cross-origin widget use)
# ---------------------------------------------------------------------------

@app.post("/api/v1/widget/ask")
def widget_ask(
    request: AskRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant),
):
    """Single-turn Q&A for the embeddable widget."""
    return _handle_ask(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )


@app.post("/api/v1/widget/chat")
def widget_chat(
    request: ChatRequest,
    http_request: Request,
    lang: Optional[str] = None,
    tenant: Tenant = Depends(require_tenant),
):
    """Multi-turn chat for the embeddable widget."""
    return _handle_chat(
        request,
        tenant,
        lang=lang,
        request_id=getattr(http_request.state, "request_id", None),
    )
