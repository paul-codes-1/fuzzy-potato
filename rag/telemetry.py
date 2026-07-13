"""Structured logging + SQLite sink for RAG query traffic.

Every query event does two things (both inside a never-raises guard):

1. Emits one JSON line to the ``rag.telemetry`` logger — the box's
   ``lfucg-rag`` systemd unit sends stdout/stderr to the journal, so
   ``journalctl -u lfucg-rag | grep 'rag.query {'`` is the grep-able view.
2. Appends a row to ``${LFUCG_OUTPUT_DIR}/telemetry.db`` (WAL, thread-safe),
   which backs the token-guarded ``GET /admin/analytics`` endpoint
   (top queries / empty-result rate / volume by transport + endpoint /
   p50-p95 latency / rate-limited count).

Since the 2026-06-11 App Runner→Lightsail migration the box's filesystem is
persistent (co-located pipeline + RAG API share ``lfucg_output/`` on local
disk), so a local SQLite file survives restarts — the same posture feeds
uses on EC2. The sink is best-effort: any DB failure is logged and swallowed
so a telemetry outage can never break a public request.

Privacy posture: free-text query/question is capped at 200 chars before
emission/persistence. Raw client IPs are hashed (sha256, daily-rotated
salt) — never the raw IP. No request body fields beyond what the API
itself accepts.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
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


# ------------------------------------------------------------------
# SQLite sink — one row per query event, backs GET /admin/analytics.
# ------------------------------------------------------------------

_db_lock = threading.Lock()
_db_conn: Optional[sqlite3.Connection] = None
_db_init_failed = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rag_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  transport TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  query TEXT,
  filters TEXT,
  result_count INTEGER,
  latency_ms REAL,
  status TEXT,
  error_type TEXT,
  rate_limited INTEGER NOT NULL DEFAULT 0,
  model_used TEXT,
  jurisdiction TEXT,
  ip_hash TEXT,
  user_agent TEXT,
  request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_rag_events_ts ON rag_events(ts);
CREATE INDEX IF NOT EXISTS idx_rag_events_endpoint ON rag_events(endpoint);
CREATE INDEX IF NOT EXISTS idx_rag_events_transport ON rag_events(transport);
"""


def _telemetry_db_path() -> str:
    """Resolve the telemetry DB path at call time.

    Honors an explicit ``RAG_TELEMETRY_DB`` override (tests point it at a temp
    file); otherwise co-locates ``telemetry.db`` next to the rest of the box's
    persistent data under ``LFUCG_OUTPUT_DIR``.
    """
    override = os.environ.get("RAG_TELEMETRY_DB")
    if override:
        return override
    output_dir = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")
    return os.path.join(output_dir, "telemetry.db")


def _get_db() -> Optional[sqlite3.Connection]:
    """Return the shared telemetry connection, opening it lazily.

    MUST be called while holding ``_db_lock`` — the connection is shared across
    the FastAPI threadpool and the MCP asyncio worker threads, so opens and
    writes are serialized under the one lock (``check_same_thread=False`` +
    WAL keeps that cheap). Returns None (sink disabled) if init ever fails, so
    a bad/read-only data dir degrades to log-only instead of raising.
    """
    global _db_conn, _db_init_failed
    if _db_conn is not None:
        return _db_conn
    if _db_init_failed:
        return None
    try:
        path = _telemetry_db_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _db_conn = conn
        return _db_conn
    except Exception:
        logger.warning("telemetry: sqlite sink init failed; disabling", exc_info=True)
        _db_init_failed = True
        return None


def _reset_db_state_for_tests() -> None:
    """Close the cached connection and clear the disabled flag. Test-only."""
    global _db_conn, _db_init_failed
    with _db_lock:
        if _db_conn is not None:
            try:
                _db_conn.close()
            except Exception:
                pass
        _db_conn = None
        _db_init_failed = False


def _write_event_row(
    *,
    transport: str,
    endpoint: str,
    query: Any,
    filters: Optional[dict],
    result_count: Optional[int],
    latency_ms: Optional[float],
    status: str,
    error_type: Optional[str],
    model_used: Optional[str],
) -> None:
    """Best-effort append of one event row. Never raises."""
    with _db_lock:
        conn = _get_db()
        if conn is None:
            return
        try:
            import json as _json

            conn.execute(
                """
                INSERT INTO rag_events (
                  ts, transport, endpoint, query, filters, result_count,
                  latency_ms, status, error_type, rate_limited, model_used,
                  jurisdiction, ip_hash, user_agent, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    transport,
                    endpoint,
                    None if query is None else str(query),
                    None if not filters else _json.dumps(filters, default=str),
                    result_count,
                    None if latency_ms is None else round(latency_ms, 2),
                    status,
                    error_type,
                    1 if status == "rate_limited" else 0,
                    model_used,
                    os.environ.get("JURISDICTION", "lfucg"),
                    hash_ip(_client_ip_var.get()),
                    _user_agent_var.get(),
                    _request_id_var.get(),
                ),
            )
            conn.commit()
        except Exception:
            logger.warning("telemetry: sqlite write failed", exc_info=True)


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
    model: Optional[str] = None,
) -> None:
    """Emit + persist a single query event. Never raises — telemetry must not break requests.

    Args:
        surface: "http" or "mcp".
        endpoint: route path ("/api/ask") or MCP tool name ("ask_meetings").
        query: free-text query or question. Truncated to MAX_QUERY_CHARS.
        filters: applied filter dict (meeting_body, date_after, etc.); kept verbatim.
        result_count: number of items returned (citations, hits, etc.) when applicable.
        latency_ms: wall-clock handler latency.
        status: "ok" | "empty" | "error" | "rate_limited".
        error_type: free-form short tag ("validation", "internal", "rate_limit_hour"...).
        model: synthesis model actually used (wired from ask()/chat()'s model_used
            on the success path); None for non-LLM endpoints.
    """
    try:
        truncated_query = _truncate(query)
        payload = {
            "event": "rag.query",
            "surface": surface,
            "endpoint": endpoint,
            "query": truncated_query,
            "filters": filters or None,
            "result_count": result_count,
            "latency_ms": None if latency_ms is None else round(latency_ms, 2),
            "status": status,
            "error_type": error_type,
            "model": model,
            "ip_hash": hash_ip(_client_ip_var.get()),
            "user_agent": _user_agent_var.get(),
            "request_id": _request_id_var.get(),
        }
        logger.info("rag.query", extra={"telemetry": payload})
        # Also emit a stable, grep-friendly second line that survives any
        # formatter that doesn't render `extra`. journalctl / CloudWatch Logs
        # Insights parse the JSON tail of the message automatically.
        logger.info(
            "rag.query %s",
            _compact_json(payload),
        )
        # Persist to the SQLite sink (best-effort — its own never-raises guard).
        _write_event_row(
            transport=surface,
            endpoint=endpoint,
            query=truncated_query,
            filters=filters,
            result_count=result_count,
            latency_ms=latency_ms,
            status=status,
            error_type=error_type,
            model_used=model,
        )
    except Exception:  # pragma: no cover — telemetry must never raise
        logger.warning("telemetry emit failed", exc_info=True)


def _compact_json(payload: dict) -> str:
    import json as _json

    return _json.dumps(payload, separators=(",", ":"), default=str)


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile on an ascending-sorted list (0.0 if empty)."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * p))
    return round(sorted_vals[idx], 2)


def analytics(window_days: int = 7, *, top_queries_limit: int = 20) -> dict:
    """Aggregate the SQLite sink for the /admin/analytics endpoint.

    Returns a JSON-serializable dict with: total events, empty-result rate,
    rate-limited count, top queries (by count), volume by transport and by
    endpoint, and p50/p95 handler latency — all over the trailing
    ``window_days``. Robust to a missing/empty DB (returns zeroes).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    empty = {
        "window_days": window_days,
        "total": 0,
        "empty_result_rate": 0.0,
        "rate_limited_count": 0,
        "top_queries": [],
        "by_transport": {},
        "by_endpoint": [],
        "latency_ms": {"p50": 0.0, "p95": 0.0},
    }
    with _db_lock:
        conn = _get_db()
        if conn is None:
            return empty
        try:
            cur = conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN status='empty' THEN 1 ELSE 0 END), "
                "SUM(rate_limited) "
                "FROM rag_events WHERE ts >= ?",
                (cutoff,),
            )
            total, empties, rate_limited = cur.fetchone()
            total = total or 0
            empties = empties or 0
            rate_limited = rate_limited or 0

            top_rows = conn.execute(
                "SELECT query, COUNT(*) AS c, MAX(ts) AS last_seen "
                "FROM rag_events "
                "WHERE ts >= ? AND query IS NOT NULL AND query != '' "
                "GROUP BY query ORDER BY c DESC, last_seen DESC LIMIT ?",
                (cutoff, top_queries_limit),
            ).fetchall()

            transport_rows = conn.execute(
                "SELECT transport, COUNT(*) FROM rag_events WHERE ts >= ? "
                "GROUP BY transport",
                (cutoff,),
            ).fetchall()

            endpoint_rows = conn.execute(
                "SELECT endpoint, COUNT(*) AS c FROM rag_events WHERE ts >= ? "
                "GROUP BY endpoint ORDER BY c DESC",
                (cutoff,),
            ).fetchall()

            lat_rows = conn.execute(
                "SELECT latency_ms FROM rag_events "
                "WHERE ts >= ? AND latency_ms IS NOT NULL ORDER BY latency_ms",
                (cutoff,),
            ).fetchall()
        except Exception:
            logger.warning("telemetry: analytics query failed", exc_info=True)
            return empty

    lats = [r[0] for r in lat_rows]
    return {
        "window_days": window_days,
        "total": total,
        "empty_result_rate": round(empties / total, 4) if total else 0.0,
        "rate_limited_count": rate_limited,
        "top_queries": [
            {"query": r[0], "count": r[1], "last_seen": r[2]} for r in top_rows
        ],
        "by_transport": {r[0]: r[1] for r in transport_rows},
        "by_endpoint": [{"endpoint": r[0], "count": r[1]} for r in endpoint_rows],
        "latency_ms": {"p50": _percentile(lats, 0.5), "p95": _percentile(lats, 0.95)},
    }
