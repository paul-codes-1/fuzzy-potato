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
import subprocess
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from clients import get_anthropic, get_openai
from config import get_config
from rag.mcp_server import mcp_server
from rag.query import DEFAULT_MAX_DISTANCE, ask, chat, load_clip_metadata
from rag.vecstore import ISO_DATE_RE, embedding_dims, get_vecstore
from rag.rate_limit import check as rate_check
from rag.related import related as related_clips
from rag.search import facets as search_facets, search as search_clips, suggest as search_suggest
from rag.telemetry import log_query_event, set_request_context

load_dotenv()

logger = logging.getLogger(__name__)
logging.getLogger("rag.query").setLevel(logging.INFO)

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")

# Per-process caches for things only this server needs (vector store,
# clip metadata, computed facets). The OpenAI/Anthropic clients are cached
# in clients.py. All are dropped by POST /admin/reload.
_store = None
_clip_metadata = None
_facets_cache = None


class _TTLCache:
    """Tiny thread-safe LRU + TTL cache (dict-backed, no dependencies).

    Used for the answer cache on /api/ask and the related-clips cache on
    /api/related. Both key on immutable inputs and only go stale on a
    reindex — which drops these alongside _facets_cache in /admin/reload —
    so the TTL is just a backstop.
    """

    def __init__(self, maxsize: int = 256, ttl_seconds: float = 3600.0):
        self._data: "OrderedDict[object, tuple[float, object]]" = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            ts, value = item
            if now - ts > self._ttl:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key, value) -> None:
        now = time.monotonic()
        with self._lock:
            self._data[key] = (now, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Answer cache for /api/ask (normalized question + filters) and related-clips
# cache for /api/related/{id} (clip_id + limit). Both dropped by /admin/reload.
_ask_cache = _TTLCache(maxsize=256, ttl_seconds=3600.0)
_related_cache = _TTLCache(maxsize=512, ttl_seconds=3600.0)


def _normalize_question(question: str) -> str:
    """Collapse whitespace + lowercase so trivially-different spellings of the
    same question share a cache entry."""
    return " ".join((question or "").split()).lower()


def _freeze_filters(filters: Optional[dict]):
    """Hashable, order-independent view of a filters dict for cache keys."""
    if not filters:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in filters.items()))


# Global daily backstop on expensive-tier (OpenAI-spend) calls. This is a
# process-wide ceiling ACROSS all clients — the per-IP limiter caps any one
# caller, this caps total spend if many IPs (or a botnet) each stay under
# their own cap. Resets on UTC-date rollover (same keying as the telemetry
# salt). Generous default so it only ever trips on genuine abuse.
_daily_ask_lock = threading.Lock()
_daily_ask_day: Optional[str] = None
_daily_ask_count = 0


def _daily_ask_cap() -> int:
    try:
        return int(os.environ.get("RAG_DAILY_ASK_CAP", "2000"))
    except (TypeError, ValueError):
        return 2000


def _check_daily_ask_cap() -> bool:
    """Record one expensive-tier call against the UTC-daily counter.

    Returns True if under the cap (and records the call), False once the day's
    ceiling is exceeded. Rolls over at UTC midnight.
    """
    global _daily_ask_day, _daily_ask_count
    today = time.strftime("%Y-%m-%d", time.gmtime())
    cap = _daily_ask_cap()
    with _daily_ask_lock:
        if _daily_ask_day != today:
            _daily_ask_day = today
            _daily_ask_count = 0
        if _daily_ask_count >= cap:
            return False
        _daily_ask_count += 1
        return True


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((tomorrow - now).total_seconds()))


def _compute_git_sha() -> str:
    """The deployed git SHA, resolved once at startup for /api/health.

    Lets an operator confirm a deploy actually landed (deploy-code.sh does a
    git reset --hard origin/main + restart; the health SHA is the proof). Best
    effort — returns 'unknown' outside a git checkout or if git is unavailable.
    """
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


_GIT_SHA = _compute_git_sha()


def _get_store():
    global _store
    if _store is None:
        _store = get_vecstore(OUTPUT_DIR)
    return _store


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
    """Startup — the vector store loads lazily on first request.

    Also runs the MCP server's session manager for the lifetime of the
    process. The streamable-HTTP transport mounted at /mcp depends on it.
    """
    logger.info("RAG API starting (vector store will load on first request)")
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

    Topology is fixed: real client → CloudFront edge → Caddy → uvicorn. So the
    socket peer is always Caddy on loopback and the real client arrives inside
    `X-Forwarded-For`. That header is client-controlled — a caller hitting the
    origin directly could rotate a fake per request to dodge the expensive-tier
    caps (unbounded OpenAI spend) — so:

    - We only trust forwarded headers when the socket peer is our own proxy
      (loopback/private); otherwise the peer IP *is* the client.
    - We do NOT read `CF-Connecting-IP` at all. CloudFront → Caddy doesn't set
      it here; anything present is pure forgery surface (and Caddy now strips
      it on the way in).
    - With two KNOWN proxy hops appended to XFF (CloudFront's edge IP added by
      CloudFront, then CloudFront's edge appended by Caddy), the real client is
      the second-from-last entry — ``xff.split(',')[-2]``. The leftmost is
      whatever the client chose to send, so it is never trusted. If the list is
      too short to have two hops, fall back to the socket peer.
    """
    peer = request.client.host if request.client else None
    if _peer_is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if len(parts) >= 2:
                return parts[-2]
    return peer


@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    """Stash IP + UA + request-id in contextvars so handlers and telemetry can read them."""
    set_request_context(
        client_ip=_client_ip_from_request(request),
        user_agent=request.headers.get("user-agent"),
        referer=request.headers.get("referer"),
        origin=request.headers.get("origin"),
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


def _daily_cap_response(*, endpoint: str, query: Optional[str] = None,
                       filters: Optional[dict] = None) -> JSONResponse:
    """Build a 429 for the global daily expensive-tier backstop + log it."""
    retry = _seconds_until_utc_midnight()
    log_query_event(
        surface="http",
        endpoint=endpoint,
        query=query,
        filters=filters,
        status="rate_limited",
        error_type="daily_ask_cap",
    )
    return JSONResponse(
        status_code=429,
        content={
            "error": "Daily question limit reached",
            "reason": "daily_ask_cap",
            "retry_after_seconds": retry,
        },
        headers={"Retry-After": str(retry)},
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


def _validate_iso_date(v: Optional[str]) -> Optional[str]:
    """Shared date_after/date_before validator for the request models.

    Malformed dates behave DIFFERENTLY per vector backend (chroma's string
    post-filter drops everything; sqlite's integer coercion no-ops the
    bound), so reject anything that isn't YYYY-MM-DD with a 422 before a
    filter reaches a store. Empty strings normalize to None (no filter).
    """
    if v is None:
        return v
    v = v.strip()
    if not v:
        return None
    if not ISO_DATE_RE.match(v):
        raise ValueError("dates must be YYYY-MM-DD")
    return v


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

    @field_validator("date_after", "date_before")
    @classmethod
    def dates_must_be_iso(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date(v)


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

    @field_validator("date_after", "date_before")
    @classmethod
    def dates_must_be_iso(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date(v)


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

    @field_validator("date_after", "date_before")
    @classmethod
    def dates_must_be_iso(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso_date(v)


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

    # Answer cache: identical question+filters only change on a reindex (which
    # drops this cache in /admin/reload). A hit costs nothing, so it's served
    # BEFORE the rate/spend gates — those exist to bound OpenAI calls, and a
    # cache hit makes none.
    cache_key = (_normalize_question(request.question), _freeze_filters(filters))
    cached = _ask_cache.get(cache_key)
    if cached is not None:
        cached_sources = cached.get("sources") if isinstance(cached, dict) else None
        log_query_event(
            surface="http",
            endpoint="/api/ask",
            query=request.question,
            filters=filters or None,
            result_count=len(cached_sources) if isinstance(cached_sources, list) else None,
            latency_ms=0.0,
            status="ok" if cached_sources else "empty",
            model=cached.get("model_used") if isinstance(cached, dict) else None,
        )
        return cached

    decision = rate_check("expensive")
    if not decision.allowed:
        return _rate_limited_response(
            decision, endpoint="/api/ask", query=request.question, filters=filters or None
        )
    if not _check_daily_ask_cap():
        return _daily_cap_response(
            endpoint="/api/ask", query=request.question, filters=filters or None
        )

    started = time.monotonic()
    try:
        store = _get_store()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

        result = ask(
            question=request.question,
            store=store,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            filters=filters if filters else None,
        )

        latency_ms = (time.monotonic() - started) * 1000
        sources = result.get("sources") if isinstance(result, dict) else None
        if isinstance(result, dict):
            _ask_cache.set(cache_key, result)
        log_query_event(
            surface="http",
            endpoint="/api/ask",
            query=request.question,
            filters=filters or None,
            result_count=len(sources) if isinstance(sources, list) else None,
            latency_ms=latency_ms,
            status="ok" if sources else "empty",
            model=result.get("model_used") if isinstance(result, dict) else None,
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
    if not _check_daily_ask_cap():
        return _daily_cap_response(
            endpoint="/api/chat", query=last_user_msg, filters=filters or None
        )

    started = time.monotonic()
    try:
        store = _get_store()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

        anthropic_client = None
        if request.model_provider == "anthropic":
            anthropic_client = _get_anthropic_client()

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        result = chat(
            messages=messages,
            store=store,
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
            model=result.get("model_used") if isinstance(result, dict) else None,
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

    # Facets are derived from the whole search.db (bodies, top-30 speakers,
    # date range) and only change on a reindex, but every SPA cold-mount hits
    # this. Cache the computed result in-process; /admin/reload drops it
    # alongside the Chroma/metadata caches after each ingest.
    global _facets_cache
    if _facets_cache is not None:
        return _facets_cache
    try:
        result = search_facets(OUTPUT_DIR)
        _facets_cache = result
        return result
    except Exception as e:
        logger.error("facets failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred loading facets.")


@app.get("/api/facets")
def facets_endpoint(response: Response):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _facets_handler()


@app.get("/facets")
def facets_endpoint_direct(response: Response):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _facets_handler()


def _related_handler(clip_id: int, limit: int):
    decision = rate_check("cheap")
    if not decision.allowed:
        return _rate_limited_response(
            decision, endpoint="/api/related", filters={"clip_id": clip_id}
        )

    limit = max(1, min(int(limit or 5), 20))
    # Related clips are derived purely from the vector store and only change on
    # a reindex (which drops this cache in /admin/reload). Cache by (clip, limit).
    cache_key = (int(clip_id), limit)
    cached = _related_cache.get(cache_key)
    if cached is not None:
        log_query_event(
            surface="http",
            endpoint="/api/related",
            filters={"clip_id": clip_id},
            result_count=len(cached),
            latency_ms=0.0,
            status="ok" if cached else "empty",
        )
        return {"results": cached}

    started = time.monotonic()
    try:
        store = _get_store()
        clip_metadata = _get_clip_metadata()
        results = related_clips(clip_id, store, clip_metadata, limit=limit)
        _related_cache.set(cache_key, results)
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
def related_endpoint(clip_id: int, response: Response, limit: int = 5):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _related_handler(clip_id, limit)


@app.get("/related/{clip_id}")
def related_endpoint_direct(clip_id: int, response: Response, limit: int = 5):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _related_handler(clip_id, limit)


@app.get("/health")
def health_endpoint():
    """Lightweight health check — no vector-store loading."""
    result = {"status": "ok", "jurisdiction": get_config().slug, "sha": _GIT_SHA}
    # Surface the serving config so a dims/backend mismatch is diagnosable
    # from the health payload (same spirit as the git sha) instead of only
    # showing up as opaque 500s once queries start. All best-effort so a bad
    # env value can never fail the health check.
    result["backend"] = os.getenv("VECTOR_BACKEND", "chroma").strip().lower()
    try:
        result["dims"] = embedding_dims()
    except (TypeError, ValueError):
        result["dims"] = None
    try:
        result["max_distance"] = float(os.getenv("RAG_MAX_DISTANCE", str(DEFAULT_MAX_DISTANCE)))
    except (TypeError, ValueError):
        result["max_distance"] = None
    if _store is not None:
        result["chunks_indexed"] = _store.count()
    if _clip_metadata is not None:
        result["clips_indexed"] = len(_clip_metadata)
    return result


@app.get("/api/health")  # Keep for CloudFront routing
def health_endpoint_api():
    return health_endpoint()


def _require_admin_token(request: Request) -> None:
    """Shared guard for the /admin/* endpoints.

    Requires ``X-Reload-Token`` to match the ``RELOAD_TOKEN`` env var. If
    ``RELOAD_TOKEN`` is unset the endpoint is disabled (404). Caddy is
    configured NOT to proxy /admin from the public origin (defense in depth),
    and there is deliberately no /api/admin alias, so /admin/* is unreachable
    via CloudFront — the cron calls it on 127.0.0.1 directly.
    """
    expected = os.environ.get("RELOAD_TOKEN")
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    provided = request.headers.get("x-reload-token", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/admin/reload")
def admin_reload(request: Request):
    """Drop the in-process caches (vector store, clip metadata, computed
    facets, the SQLite search connection) so freshly-ingested data is picked
    up WITHOUT a full process restart — avoids dropping in-flight /api/ask
    calls on the 6-hourly ingest. The next request rebuilds each lazily.
    """
    _require_admin_token(request)

    global _store, _clip_metadata, _facets_cache
    # Close the sqlite-backend store's fd so a rebuilt/os.replace()'d vec.db
    # is reopened by the next request (same pattern as search.db below).
    # Best-effort: chroma stores no-op close(), mocks may lack it entirely.
    try:
        if _store is not None:
            _store.close()
    except Exception as e:
        logger.warning("admin reload: failed to close vector store: %s", e)
    _store = None
    _clip_metadata = None
    _facets_cache = None
    # Drop the answer + related-clips caches too — they're keyed on inputs that
    # only change when the index is rebuilt, which is exactly now.
    _ask_cache.clear()
    _related_cache.clear()
    # The MCP module keeps its OWN per-process caches (same pattern, separate
    # module). Without clearing them, the MCP surface keeps serving the stale
    # pre-reload index until a full restart — worse, its cached store is
    # bound to the orphaned Chroma system whose cache we clear below.
    import rag.mcp_server as _mcp_mod

    try:
        if _mcp_mod._store is not None:
            _mcp_mod._store.close()
    except Exception as e:
        logger.warning("admin reload: failed to close MCP vector store: %s", e)
    _mcp_mod._store = None
    _mcp_mod._clip_metadata = None
    # Dropping the _store reference is NOT enough for the chroma backend:
    # ChromaDB keeps a process-wide SharedSystemClient (keyed by path) whose
    # in-memory segment cache holds the HNSW index loaded at first use. A clip
    # re-ingested by a SEPARATE process (rag.ingest in the cron) writes to
    # disk, but this server's cached system never re-reads it — so a fresh
    # PersistentClient here would still serve the STALE in-memory index
    # (observed: Paris citations fell back to the Granicus URL because the
    # re-ingested canonical_url chunks weren't visible until a full restart).
    # Clearing the system cache forces the next _get_store() to rebuild from
    # disk. Only relevant to the chroma backend — importing chromadb here on a
    # sqlite box drags ~63MB of RSS in for a guaranteed no-op, so guard it.
    if os.getenv("VECTOR_BACKEND", "chroma").strip().lower() == "chroma":
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
        "admin reload: dropped HTTP + MCP store/metadata/facets caches + Chroma system cache + search connections"
    )
    return {"reloaded": True}


@app.get("/admin/analytics")
def admin_analytics(request: Request, days: int = 7):
    """Token-guarded query-analytics summary from the telemetry SQLite sink.

    Same auth + origin-only posture as /admin/reload. Returns top queries,
    empty-result rate, volume by transport (http vs mcp) + endpoint, p50/p95
    latency, and the rate-limited count over the trailing ``days`` window.
    """
    _require_admin_token(request)
    days = max(1, min(int(days or 7), 90))
    try:
        from rag.telemetry import analytics

        return analytics(window_days=days)
    except Exception as e:
        logger.error("admin analytics failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred loading analytics.")
