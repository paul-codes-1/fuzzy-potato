"""Usage analytics store backed by SQLite."""

import csv
import io
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional


_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS query_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    question TEXT NOT NULL,
    model TEXT,
    response_time_ms REAL,
    chunks_retrieved INTEGER DEFAULT 0,
    meeting_body TEXT,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_processed_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    processing_time_seconds REAL,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_call_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status_code INTEGER,
    duration_ms REAL,
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_query_tenant_ts ON query_events(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_query_ts ON query_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_meeting_tenant_ts ON meeting_processed_events(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_api_tenant_ts ON api_call_events(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_api_ts ON api_call_events(timestamp);
"""


class AnalyticsStore:
    """SQLite-backed analytics event store."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def log_query(
        self,
        tenant_id: str,
        question: str,
        model: Optional[str] = None,
        response_time_ms: Optional[float] = None,
        chunks_retrieved: int = 0,
        meeting_body: Optional[str] = None,
    ):
        self._conn.execute(
            "INSERT INTO query_events (tenant_id, question, model, response_time_ms, chunks_retrieved, meeting_body, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, question, model, response_time_ms, chunks_retrieved, meeting_body, time.time()),
        )
        self._conn.commit()

    def log_meeting_processed(
        self,
        tenant_id: str,
        clip_id: str,
        processing_time_seconds: Optional[float] = None,
    ):
        self._conn.execute(
            "INSERT INTO meeting_processed_events (tenant_id, clip_id, processing_time_seconds, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (tenant_id, clip_id, processing_time_seconds, time.time()),
        )
        self._conn.commit()

    def log_api_call(
        self,
        tenant_id: str,
        endpoint: str,
        status_code: int,
        duration_ms: float,
    ):
        self._conn.execute(
            "INSERT INTO api_call_events (tenant_id, endpoint, status_code, duration_ms, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, endpoint, status_code, duration_ms, time.time()),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def _period_seconds(self, period: str) -> float:
        """Convert period string like '7d', '30d', '90d' to seconds."""
        multipliers = {"d": 86400, "w": 604800, "m": 2592000}
        unit = period[-1]
        amount = int(period[:-1])
        return amount * multipliers.get(unit, 86400)

    def _cutoff(self, period: str) -> float:
        return time.time() - self._period_seconds(period)

    def usage_summary(self, tenant_id: str, period: str = "30d") -> dict:
        """Current period usage summary for a tenant."""
        cutoff = self._cutoff(period)

        row = self._conn.execute(
            "SELECT COUNT(*) as total_queries, "
            "AVG(response_time_ms) as avg_response_time, "
            "MIN(timestamp) as first_query, "
            "MAX(timestamp) as last_query "
            "FROM query_events WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, cutoff),
        ).fetchone()

        meetings_row = self._conn.execute(
            "SELECT COUNT(*) as total FROM meeting_processed_events WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, cutoff),
        ).fetchone()

        return {
            "period": period,
            "total_queries": row["total_queries"],
            "avg_response_time_ms": round(row["avg_response_time"] or 0, 1),
            "meetings_processed": meetings_row["total"],
            "first_query": _ts_to_iso(row["first_query"]),
            "last_query": _ts_to_iso(row["last_query"]),
        }

    def queries_over_time(self, tenant_id: str, period: str = "7d") -> list[dict]:
        """Query volume grouped by day within the period."""
        cutoff = self._cutoff(period)
        rows = self._conn.execute(
            "SELECT date(timestamp, 'unixepoch') as day, COUNT(*) as count, "
            "AVG(response_time_ms) as avg_ms "
            "FROM query_events WHERE tenant_id = ? AND timestamp >= ? "
            "GROUP BY day ORDER BY day",
            (tenant_id, cutoff),
        ).fetchall()
        return [
            {"date": r["day"], "count": r["count"], "avg_response_time_ms": round(r["avg_ms"] or 0, 1)}
            for r in rows
        ]

    def popular_meeting_bodies(self, tenant_id: str, period: str = "30d", limit: int = 10) -> list[dict]:
        """Most queried meeting bodies."""
        cutoff = self._cutoff(period)
        rows = self._conn.execute(
            "SELECT meeting_body, COUNT(*) as count "
            "FROM query_events WHERE tenant_id = ? AND timestamp >= ? AND meeting_body IS NOT NULL AND meeting_body != '' "
            "GROUP BY meeting_body ORDER BY count DESC LIMIT ?",
            (tenant_id, cutoff, limit),
        ).fetchall()
        return [{"meeting_body": r["meeting_body"], "count": r["count"]} for r in rows]

    def peak_usage_hours(self, tenant_id: str, period: str = "30d") -> list[dict]:
        """Query distribution by hour of day (UTC)."""
        cutoff = self._cutoff(period)
        rows = self._conn.execute(
            "SELECT CAST(strftime('%H', timestamp, 'unixepoch') AS INTEGER) as hour, COUNT(*) as count "
            "FROM query_events WHERE tenant_id = ? AND timestamp >= ? "
            "GROUP BY hour ORDER BY hour",
            (tenant_id, cutoff),
        ).fetchall()
        return [{"hour": r["hour"], "count": r["count"]} for r in rows]

    def top_questions(self, tenant_id: str, period: str = "30d", limit: int = 20) -> list[dict]:
        """Most asked questions (exact match grouping)."""
        cutoff = self._cutoff(period)
        rows = self._conn.execute(
            "SELECT question, COUNT(*) as count, AVG(response_time_ms) as avg_ms "
            "FROM query_events WHERE tenant_id = ? AND timestamp >= ? "
            "GROUP BY question ORDER BY count DESC LIMIT ?",
            (tenant_id, cutoff, limit),
        ).fetchall()
        return [
            {"question": r["question"], "count": r["count"], "avg_response_time_ms": round(r["avg_ms"] or 0, 1)}
            for r in rows
        ]

    def recent_queries(self, tenant_id: str, limit: int = 50) -> list[dict]:
        """Most recent queries for a tenant."""
        rows = self._conn.execute(
            "SELECT question, model, response_time_ms, chunks_retrieved, meeting_body, timestamp "
            "FROM query_events WHERE tenant_id = ? ORDER BY timestamp DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
        return [
            {
                "question": r["question"],
                "model": r["model"],
                "response_time_ms": round(r["response_time_ms"] or 0, 1),
                "chunks_retrieved": r["chunks_retrieved"],
                "meeting_body": r["meeting_body"],
                "timestamp": _ts_to_iso(r["timestamp"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Admin / cross-tenant aggregation
    # ------------------------------------------------------------------

    def admin_overview(self, period: str = "30d") -> dict:
        """Cross-tenant overview for admin dashboard."""
        cutoff = self._cutoff(period)

        totals = self._conn.execute(
            "SELECT COUNT(*) as total_queries, "
            "COUNT(DISTINCT tenant_id) as active_tenants, "
            "AVG(response_time_ms) as avg_response_time "
            "FROM query_events WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()

        meetings = self._conn.execute(
            "SELECT COUNT(*) as total FROM meeting_processed_events WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()

        api_calls = self._conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors "
            "FROM api_call_events WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()

        # Per-tenant breakdown
        tenant_rows = self._conn.execute(
            "SELECT tenant_id, COUNT(*) as queries, AVG(response_time_ms) as avg_ms "
            "FROM query_events WHERE timestamp >= ? "
            "GROUP BY tenant_id ORDER BY queries DESC",
            (cutoff,),
        ).fetchall()

        return {
            "period": period,
            "total_queries": totals["total_queries"],
            "active_tenants": totals["active_tenants"],
            "avg_response_time_ms": round(totals["avg_response_time"] or 0, 1),
            "meetings_processed": meetings["total"],
            "total_api_calls": api_calls["total"],
            "api_errors": api_calls["errors"],
            "per_tenant": [
                {
                    "tenant_id": r["tenant_id"],
                    "queries": r["queries"],
                    "avg_response_time_ms": round(r["avg_ms"] or 0, 1),
                }
                for r in tenant_rows
            ],
        }

    def admin_revenue_metrics(self) -> dict:
        """Placeholder revenue metrics. In production this would pull from
        a billing system. Here we derive proxies from tenant activity."""
        # Count tenants by their query activity in last 30d
        cutoff_30d = self._cutoff("30d")
        cutoff_60d = self._cutoff("60d")

        active_now = self._conn.execute(
            "SELECT COUNT(DISTINCT tenant_id) as c FROM query_events WHERE timestamp >= ?",
            (cutoff_30d,),
        ).fetchone()["c"]

        active_prev = self._conn.execute(
            "SELECT COUNT(DISTINCT tenant_id) as c FROM query_events WHERE timestamp >= ? AND timestamp < ?",
            (cutoff_60d, cutoff_30d),
        ).fetchone()["c"]

        churned = max(0, active_prev - active_now) if active_prev > 0 else 0

        return {
            "active_tenants_current": active_now,
            "active_tenants_previous": active_prev,
            "churned_tenants": churned,
            "churn_rate": round(churned / active_prev * 100, 1) if active_prev > 0 else 0,
            "growth": active_now - active_prev,
            "note": "Revenue figures require billing system integration.",
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_queries_csv(self, tenant_id: str, period: str = "30d") -> str:
        """Export query events to CSV string."""
        cutoff = self._cutoff(period)
        rows = self._conn.execute(
            "SELECT tenant_id, question, model, response_time_ms, chunks_retrieved, meeting_body, "
            "datetime(timestamp, 'unixepoch') as time "
            "FROM query_events WHERE tenant_id = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (tenant_id, cutoff),
        ).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["tenant_id", "question", "model", "response_time_ms", "chunks_retrieved", "meeting_body", "time"])
        for r in rows:
            writer.writerow([r["tenant_id"], r["question"], r["model"], r["response_time_ms"], r["chunks_retrieved"], r["meeting_body"], r["time"]])
        return output.getvalue()

    def export_api_calls_csv(self, tenant_id: str, period: str = "30d") -> str:
        """Export API call events to CSV string."""
        cutoff = self._cutoff(period)
        rows = self._conn.execute(
            "SELECT tenant_id, endpoint, status_code, duration_ms, "
            "datetime(timestamp, 'unixepoch') as time "
            "FROM api_call_events WHERE tenant_id = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (tenant_id, cutoff),
        ).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["tenant_id", "endpoint", "status_code", "duration_ms", "time"])
        for r in rows:
            writer.writerow([r["tenant_id"], r["endpoint"], r["status_code"], r["duration_ms"], r["time"]])
        return output.getvalue()

    def close(self):
        self._conn.close()


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_analytics_store: Optional[AnalyticsStore] = None


def init_analytics(output_dir: str) -> AnalyticsStore:
    """Initialize the global analytics store. Call once at startup."""
    global _analytics_store
    db_path = os.path.join(output_dir, "analytics.db")
    _analytics_store = AnalyticsStore(db_path)
    return _analytics_store


def get_analytics_store() -> AnalyticsStore:
    """Get the global analytics store singleton."""
    if _analytics_store is None:
        raise RuntimeError("Analytics not initialized -- call init_analytics() first")
    return _analytics_store
