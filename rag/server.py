"""FastAPI server for RAG Q&A."""

import logging
import sys

# Claim the root logger BEFORE importing rag.mcp_server below. FastMCP's
# `configure_logging` runs at import-time (build_mcp_server → FastMCP() →
# configure_logging) and installs a RichHandler via logging.basicConfig.
# basicConfig is a no-op once root has any handler, so claiming it here
# pre-empts Rich and keeps full-width log lines in CloudWatch — Rich was
# wrapping JSON telemetry + query rewrites at ~80 cols, making truncated
# garbage of user-visible logs.
_root = logging.getLogger()
_root.setLevel(logging.INFO)
if not _root.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    _root.addHandler(_h)

import hmac
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from clients import get_anthropic, get_openai
from config import get_config
from rag.ingest import get_chroma_collection
from rag.mcp_server import mcp_server
from rag.query import ask, chat, load_clip_metadata
from rag.rate_limit import check as rate_check
from rag.related import related as related_clips
from rag.search import facets as search_facets, search as search_clips, suggest as search_suggest
from rag.telemetry import log_query_event, set_request_context

load_dotenv()

logger = logging.getLogger(__name__)
logging.getLogger("rag.query").setLevel(logging.INFO)

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")

# Per-process caches for things only this server needs (Chroma collection,
# clip metadata). The OpenAI/Anthropic clients are cached in clients.py.
_collection = None
_clip_metadata = None


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


# Wrappers (not aliases) so test patches against `rag.server.get_openai`
# / `rag.server.get_anthropic` are honored — direct assignment would
# resolve the function object at import time and bypass the patch.
def _get_openai_client():
    return get_openai()


def _get_anthropic_client():
    return get_anthropic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup — ChromaDB loads lazily on first request.

    Also runs the MCP server's session manager for the lifetime of the
    process. The streamable-HTTP transport mounted at /mcp depends on it.
    """
    logger.info("RAG API starting (ChromaDB will load on first request)")
    async with mcp_server.session_manager.run():
        logger.info("MCP server mounted at /mcp")
        yield


app = FastAPI(title=f"{get_config().publication_name} RAG API", lifespan=lifespan)


class _MCPTrailingSlashMiddleware:
    """Rewrite `/api/mcp` → `/api/mcp/` (and `/mcp` → `/mcp/`) before routing.

    Without this, a request to `/api/mcp` (no trailing slash) reaches the
    mounted FastMCP Starlette app with an empty residual path, doesn't match
    its route at `/`, and triggers Starlette's `redirect_slashes` 307. The
    redirect URL is built from the request scope, which behind CloudFront →
    App Runner has scheme=http and host=<internal app-runner-host>. The
    client gets `Location: http://...awsapprunner.com/api/mcp/` and can't
    follow it. Rewriting the path here makes the Mount route the request
    directly, no redirect needed. claude.ai's MCP connector and several
    other clients POST without the trailing slash, so this is the only
    spelling that actually reaches users.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") in ("/api/mcp", "/mcp"):
            scope = dict(scope)
            scope["path"] = scope["path"] + "/"
            raw_path = scope.get("raw_path")
            if raw_path is not None:
                scope["raw_path"] = raw_path + b"/"
        await self.app(scope, receive, send)


app.add_middleware(_MCPTrailingSlashMiddleware)


def _peer_is_trusted_proxy(host: Optional[str]) -> bool:
    """True when the direct socket peer is our own proxy layer (Caddy on
    localhost, or anything on the box's private network) — the only case in
    which forwarded-IP headers are trustworthy."""
    if not host:
        return False
    try:
        import ipaddress

        addr = ipaddress.ip_address(host)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return False


def _client_ip_from_request(request: Request) -> Optional[str]:
    """Resolve the real client IP for rate limiting.

    Behind CloudFront → Caddy, request.client.host is the local proxy hop —
    useless for rate limiting — and the original client IP arrives in
    `CF-Connecting-IP` / `X-Forwarded-For`. But those headers are
    client-controlled: anyone hitting the origin directly could rotate
    `CF-Connecting-IP: <random>` per request and bypass the expensive-tier
    caps entirely (unbounded OpenAI spend). So only honor them when the
    socket peer is our own proxy; otherwise the peer IP *is* the client.
    """
    peer = request.client.host if request.client else None
    if _peer_is_trusted_proxy(peer):
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # leftmost is the originating client
            return xff.split(",")[0].strip()
    return peer


@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    """Stash IP + UA + request-id in contextvars so handlers and telemetry can read them."""
    set_request_context(
        client_ip=_client_ip_from_request(request),
        user_agent=request.headers.get("user-agent"),
    )
    return await call_next(request)


def _rate_limited_response(decision, *, endpoint: str, query: Optional[str] = None,
                          filters: Optional[dict] = None) -> JSONResponse:
    """Build a 429 response and emit a rate-limited telemetry event."""
    log_query_event(
        surface="http",
        endpoint=endpoint,
        query=query,
        filters=filters,
        status="rate_limited",
        error_type=f"rate_limit_{decision.reason}",
    )
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "reason": decision.reason,
            "retry_after_seconds": decision.retry_after_seconds,
        },
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )

# MCP endpoint — public, stateless, CORS-open. Lets Claude Desktop, Cursor,
# NotebookLM, and other MCP-aware clients query the meeting archive via the
# standard MCP protocol instead of HTTP/JSON. See rag/mcp_server.py for the
# tools exposed.
#
# Mounted at BOTH /api/mcp (for CloudFront — only `/api/*` paths are routed
# from CloudFront → App Runner) AND /mcp (for direct App Runner URL access,
# matching the existing `/ask` vs `/api/ask` dual-mount pattern). Both
# routes wrap the same FastMCP instance, so the lifespan-managed session
# manager singleton serves both.
_mcp_http_app = mcp_server.streamable_http_app()
app.mount("/api/mcp", _mcp_http_app)
app.mount("/mcp", _mcp_http_app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


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


MAX_CHAT_MESSAGE_CHARS = 4000
MAX_CHAT_MESSAGES = 24


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        return v

    @field_validator("content")
    @classmethod
    def content_not_too_long(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message content must not be empty")
        if len(v) > MAX_CHAT_MESSAGE_CHARS:
            raise ValueError(f"message content must be under {MAX_CHAT_MESSAGE_CHARS} characters")
        return v


MAX_SEARCH_QUERY_CHARS = 200
MAX_SEARCH_LIMIT = 100


class SearchRequest(BaseModel):
    q: str
    meeting_body: Optional[str] = None
    speaker: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None
    limit: int = 50

    @field_validator("q")
    @classmethod
    def q_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("q must not be empty")
        if len(v) > MAX_SEARCH_QUERY_CHARS:
            raise ValueError(f"q must be under {MAX_SEARCH_QUERY_CHARS} characters")
        return v

    @field_validator("limit")
    @classmethod
    def limit_in_range(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > MAX_SEARCH_LIMIT:
            return MAX_SEARCH_LIMIT
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
        if len(v) > MAX_CHAT_MESSAGES:
            raise ValueError(f"messages must contain no more than {MAX_CHAT_MESSAGES} entries")
        if v[-1].role != "user":
            raise ValueError("last message must be from the user")
        return v

    @field_validator("model_provider")
    @classmethod
    def model_provider_must_be_valid(cls, v: str) -> str:
        if v not in ("openai", "anthropic"):
            raise ValueError("model_provider must be 'openai' or 'anthropic'")
        return v


@app.post("/ask")  # Direct endpoint for App Runner
def ask_endpoint_direct(request: AskRequest):
    return ask_endpoint(request)


@app.post("/api/ask")
def ask_endpoint(request: AskRequest):
    filters = {}
    if request.meeting_body:
        filters["meeting_body"] = request.meeting_body
    if request.date_after:
        filters["date_after"] = request.date_after
    if request.date_before:
        filters["date_before"] = request.date_before

    decision = rate_check("expensive")
    if not decision.allowed:
        return _rate_limited_response(
            decision, endpoint="/api/ask", query=request.question, filters=filters or None
        )

    started = time.monotonic()
    try:
        collection = _get_collection()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

        result = ask(
            question=request.question,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            filters=filters if filters else None,
        )

        latency_ms = (time.monotonic() - started) * 1000
        sources = result.get("sources") if isinstance(result, dict) else None
        log_query_event(
            surface="http",
            endpoint="/api/ask",
            query=request.question,
            filters=filters or None,
            result_count=len(sources) if isinstance(sources, list) else None,
            latency_ms=latency_ms,
            status="ok" if sources else "empty",
        )
        return result
    except Exception as e:
        log_query_event(
            surface="http",
            endpoint="/api/ask",
            query=request.question,
            filters=filters or None,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.error("ask_endpoint failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your question.")


@app.post("/api/chat")
def chat_endpoint(request: ChatRequest):
    filters = {}
    if request.meeting_body:
        filters["meeting_body"] = request.meeting_body
    if request.date_after:
        filters["date_after"] = request.date_after
    if request.date_before:
        filters["date_before"] = request.date_before

    # The last user message is the "query" for telemetry purposes.
    last_user_msg = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), None
    )

    decision = rate_check("expensive")
    if not decision.allowed:
        return _rate_limited_response(
            decision, endpoint="/api/chat", query=last_user_msg, filters=filters or None
        )

    started = time.monotonic()
    try:
        collection = _get_collection()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

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
        )

        sources = result.get("sources") if isinstance(result, dict) else None
        log_query_event(
            surface="http",
            endpoint="/api/chat",
            query=last_user_msg,
            filters={**(filters or {}), "model_provider": request.model_provider,
                    "message_count": len(request.messages)},
            result_count=len(sources) if isinstance(sources, list) else None,
            latency_ms=(time.monotonic() - started) * 1000,
            status="ok" if sources else "empty",
        )
        return result
    except Exception as e:
        log_query_event(
            surface="http",
            endpoint="/api/chat",
            query=last_user_msg,
            filters={**(filters or {}), "model_provider": request.model_provider},
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.error("chat_endpoint failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your chat.")


@app.post("/chat")
def chat_endpoint_direct(request: ChatRequest):
    return chat_endpoint(request)


def _search_handler(request: SearchRequest):
    filters = {
        k: v
        for k, v in {
            "meeting_body": request.meeting_body,
            "speaker": request.speaker,
            "date_after": request.date_after,
            "date_before": request.date_before,
        }.items()
        if v
    }

    decision = rate_check("cheap")
    if not decision.allowed:
        return _rate_limited_response(
            decision, endpoint="/api/search", query=request.q, filters=filters or None
        )

    started = time.monotonic()
    try:
        results = search_clips(
            query=request.q,
            output_dir=OUTPUT_DIR,
            meeting_body=request.meeting_body,
            speaker=request.speaker,
            date_after=request.date_after,
            date_before=request.date_before,
            limit=request.limit,
        )
        log_query_event(
            surface="http",
            endpoint="/api/search",
            query=request.q,
            filters=filters or None,
            result_count=len(results),
            latency_ms=(time.monotonic() - started) * 1000,
            status="ok" if results else "empty",
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        log_query_event(
            surface="http",
            endpoint="/api/search",
            query=request.q,
            filters=filters or None,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.error("search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred running search.")


@app.post("/api/search")
def search_endpoint(request: SearchRequest):
    return _search_handler(request)


@app.post("/search")  # Direct endpoint for App Runner (matches /ask, /chat)
def search_endpoint_direct(request: SearchRequest):
    return _search_handler(request)


def _suggest_handler(q: str, limit: int):
    q = (q or "").strip()
    if not q:
        return {"results": []}
    if len(q) > MAX_SEARCH_QUERY_CHARS:
        raise HTTPException(status_code=400, detail="q too long")

    decision = rate_check("cheap")
    if not decision.allowed:
        return _rate_limited_response(decision, endpoint="/api/suggest", query=q)

    started = time.monotonic()
    try:
        limit = max(1, min(int(limit or 10), 25))
        results = search_suggest(q, OUTPUT_DIR, limit=limit)
        log_query_event(
            surface="http",
            endpoint="/api/suggest",
            query=q,
            result_count=len(results),
            latency_ms=(time.monotonic() - started) * 1000,
            status="ok" if results else "empty",
        )
        return {"results": results}
    except Exception as e:
        log_query_event(
            surface="http",
            endpoint="/api/suggest",
            query=q,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.error("suggest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred running suggest.")


@app.get("/api/suggest")
def suggest_endpoint(q: str = "", limit: int = 10):
    return _suggest_handler(q, limit)


@app.get("/suggest")
def suggest_endpoint_direct(q: str = "", limit: int = 10):
    return _suggest_handler(q, limit)


def _facets_handler():
    decision = rate_check("cheap")
    if not decision.allowed:
        return _rate_limited_response(decision, endpoint="/api/facets")

    try:
        return search_facets(OUTPUT_DIR)
    except Exception as e:
        logger.error("facets failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred loading facets.")


@app.get("/api/facets")
def facets_endpoint():
    return _facets_handler()


@app.get("/facets")
def facets_endpoint_direct():
    return _facets_handler()


def _related_handler(clip_id: int, limit: int):
    decision = rate_check("cheap")
    if not decision.allowed:
        return _rate_limited_response(
            decision, endpoint="/api/related", filters={"clip_id": clip_id}
        )

    started = time.monotonic()
    try:
        limit = max(1, min(int(limit or 5), 20))
        collection = _get_collection()
        clip_metadata = _get_clip_metadata()
        results = related_clips(clip_id, collection, clip_metadata, limit=limit)
        log_query_event(
            surface="http",
            endpoint="/api/related",
            filters={"clip_id": clip_id},
            result_count=len(results),
            latency_ms=(time.monotonic() - started) * 1000,
            status="ok" if results else "empty",
        )
        return {"results": results}
    except Exception as e:
        log_query_event(
            surface="http",
            endpoint="/api/related",
            filters={"clip_id": clip_id},
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.error("related failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred loading related clips.")


@app.get("/api/related/{clip_id}")
def related_endpoint(clip_id: int, limit: int = 5):
    return _related_handler(clip_id, limit)


@app.get("/related/{clip_id}")
def related_endpoint_direct(clip_id: int, limit: int = 5):
    return _related_handler(clip_id, limit)


@app.get("/health")
def health_endpoint():
    """Lightweight health check — no ChromaDB loading."""
    result = {"status": "ok", "jurisdiction": get_config().slug}
    if _collection is not None:
        result["chunks_indexed"] = _collection.count()
    if _clip_metadata is not None:
        result["clips_indexed"] = len(_clip_metadata)
    return result


@app.get("/api/health")  # Keep for CloudFront routing
def health_endpoint_api():
    return health_endpoint()


@app.post("/admin/reload")
def admin_reload(request: Request):
    """Drop the in-process caches (Chroma collection, clip metadata, the
    SQLite search connection) so freshly-ingested data is picked up WITHOUT
    a full process restart — avoids dropping in-flight /api/ask calls on the
    6-hourly ingest. The next request rebuilds each lazily.

    Token-guarded: requires ``X-Reload-Token`` to match the ``RELOAD_TOKEN``
    env var. If ``RELOAD_TOKEN`` is unset the endpoint is disabled (404).
    The ingest cron calls this on 127.0.0.1 directly; Caddy is configured
    NOT to proxy /admin from the public origin (defense in depth). There is
    deliberately no /api/admin alias, so it's unreachable via CloudFront.
    """
    expected = os.environ.get("RELOAD_TOKEN")
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    provided = request.headers.get("x-reload-token", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")

    global _collection, _clip_metadata
    _collection = None
    _clip_metadata = None
    # The MCP module keeps its OWN per-process caches (same pattern, separate
    # module). Without clearing them, the MCP surface keeps serving the stale
    # pre-reload index until a full restart — worse, its cached Collection is
    # bound to the orphaned Chroma system whose cache we clear below.
    import rag.mcp_server as _mcp_mod

    _mcp_mod._collection = None
    _mcp_mod._clip_metadata = None
    # Dropping the _collection reference is NOT enough: ChromaDB keeps a
    # process-wide SharedSystemClient (keyed by path) whose in-memory segment
    # cache holds the HNSW index loaded at first use. A clip re-ingested by a
    # SEPARATE process (rag.ingest in the cron) writes to disk, but this
    # server's cached system never re-reads it — so a fresh PersistentClient
    # here would still serve the STALE in-memory index (observed: Paris
    # citations fell back to the Granicus URL because the re-ingested
    # canonical_url chunks weren't visible until a full restart). Clearing the
    # system cache forces the next _get_collection() to rebuild from disk.
    try:
        from chromadb.api.shared_system_client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception as e:  # pragma: no cover - best-effort
        logger.warning("admin reload: failed to clear Chroma system cache: %s", e)
    try:
        from rag.search import close_connections

        close_connections()
    except Exception as e:  # pragma: no cover - best-effort
        logger.warning("admin reload: failed to close search connections: %s", e)
    logger.info(
        "admin reload: dropped HTTP + MCP collection/metadata caches + Chroma system cache + search connections"
    )
    return {"reloaded": True}
