"""Structured logging for RAG query traffic.

Emits one JSON line per query event to the standard `rag.telemetry` logger,
which in production (App Runner) goes to stdout and is collected into
CloudWatch Logs. Query "what are people asking" with CloudWatch Logs
Insights:

    fields @timestamp, surface, endpoint, query, result_count, latency_ms
    | filter event = "rag.query"
    | sort @timestamp desc
    | limit 200

App Runner's container filesystem is ephemeral, so we deliberately do NOT
write to a local SQLite file the way feeds (on EC2) does. The trade-off:
no Paul-facing /admin dashboard yet; if/when one is wanted, swap the
emit() implementation to also write to DynamoDB or S3 line-delimited JSON
— call sites won't have to change.

Privacy posture: free-text query/question is capped at 200 chars before
emission. Raw client IPs are hashed (sha256, daily-rotated salt) — never
the raw IP. No request body fields beyond what the API itself accepts.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("rag.telemetry")

MAX_QUERY_CHARS = 200

# Set by the HTTP middleware (rag/server.py) so MCP tool impls can read the
# caller's IP without each tool having to receive a Context argument.
_client_ip_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "rag_client_ip", default=None
)
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "rag_request_id", default=None
)
_user_agent_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "rag_user_agent", default=None
)


def set_request_context(
    *,
    client_ip: Optional[str],
    user_agent: Optional[str],
    request_id: Optional[str] = None,
) -> str:
    """Stash per-request metadata for the duration of this asyncio task / thread.

    Returns the request id (minted if not supplied). Call from HTTP middleware.
    """
    rid = request_id or uuid.uuid4().hex[:16]
    _client_ip_var.set(client_ip)
    _user_agent_var.set(user_agent)
    _request_id_var.set(rid)
    return rid


def get_client_ip() -> Optional[str]:
    return _client_ip_var.get()


def get_request_id() -> Optional[str]:
    return _request_id_var.get()


def _daily_salt() -> str:
    base = os.environ.get("RAG_TELEMETRY_SALT", "lt-rag-default")
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return f"{base}:{day}"


def hash_ip(ip: Optional[str]) -> Optional[str]:
    """sha256(ip + daily-rotated salt), first 16 hex chars. None if ip is None."""
    if not ip:
        return None
    return hashlib.sha256(f"{ip}:{_daily_salt()}".encode("utf-8")).hexdigest()[:16]


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_QUERY_CHARS:
        return value[:MAX_QUERY_CHARS] + "…"
    return value


def log_query_event(
    *,
    surface: str,
    endpoint: str,
    query: Optional[str] = None,
    filters: Optional[dict] = None,
    result_count: Optional[int] = None,
    latency_ms: Optional[float] = None,
    status: str = "ok",
    error_type: Optional[str] = None,
) -> None:
    """Emit a single query event. Never raises — telemetry must not break requests.

    Args:
        surface: "http" or "mcp".
        endpoint: route path ("/api/ask") or MCP tool name ("ask_meetings").
        query: free-text query or question. Truncated to MAX_QUERY_CHARS.
        filters: applied filter dict (meeting_body, date_after, etc.); kept verbatim.
        result_count: number of items returned (citations, hits, etc.) when applicable.
        latency_ms: wall-clock handler latency.
        status: "ok" | "empty" | "error" | "rate_limited".
        error_type: free-form short tag ("validation", "internal", "rate_limit_hour"...).
    """
    try:
        payload = {
            "event": "rag.query",
            "surface": surface,
            "endpoint": endpoint,
            "query": _truncate(query),
            "filters": filters or None,
            "result_count": result_count,
            "latency_ms": None if latency_ms is None else round(latency_ms, 2),
            "status": status,
            "error_type": error_type,
            "ip_hash": hash_ip(_client_ip_var.get()),
            "user_agent": _user_agent_var.get(),
            "request_id": _request_id_var.get(),
        }
        logger.info("rag.query", extra={"telemetry": payload})
        # Also emit a stable, grep-friendly second line that survives any
        # formatter that doesn't render `extra`. CloudWatch Logs Insights
        # parses the JSON tail of the message automatically.
        logger.info(
            "rag.query %s",
            _compact_json(payload),
        )
    except Exception:  # pragma: no cover — telemetry must never raise
        logger.warning("telemetry emit failed", exc_info=True)


def _compact_json(payload: dict) -> str:
    import json as _json

    return _json.dumps(payload, separators=(",", ":"), default=str)
