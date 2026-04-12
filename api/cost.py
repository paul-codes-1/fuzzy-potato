"""Per-tenant LLM cost attribution store backed by SQLite.

Tracks OpenAI + Anthropic API spend per tenant so Finance can answer
"what does each tenant actually cost us in API fees?".

The pricing table below reflects publicly listed 2026 rates and should be
refreshed whenever providers adjust prices. Rates are expressed as USD per
1,000,000 input/output tokens except for whisper-1, which is billed per
minute of audio.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing table (USD)
# ---------------------------------------------------------------------------
#
# Source: OpenAI pricing page (https://openai.com/api/pricing/) and
# Anthropic pricing page (https://www.anthropic.com/pricing#anthropic-api).
# Rates captured 2026-01-15. Refresh if providers change their published
# numbers. All rates are USD per 1,000,000 tokens unless marked otherwise.
#
# "input" = prompt / cached-input tokens billed as input
# "output" = completion tokens
# "per_minute" is only used by whisper-1 (audio transcription)
#
MODEL_PRICING: dict[str, dict[str, float]] = {
    # --- OpenAI chat / reasoning models ---
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # --- OpenAI embeddings ---
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
    # --- OpenAI audio ---
    # whisper-1 is billed at $0.006 per minute of audio. We record token
    # columns as 0 for whisper rows and compute cost from the "minutes"
    # value the caller passes as input_tokens (see record_llm_call docs).
    "whisper-1": {"input": 0.0, "output": 0.0, "per_minute": 0.006},
    # --- Anthropic Claude models ---
    "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-latest": {"input": 0.80, "output": 4.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    # Aliases so callers can pass short names.
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-sonnet": {"input": 3.00, "output": 15.00},
}


_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS cost_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    module TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    request_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_cost_tenant_ts ON cost_events(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_cost_model ON cost_events(model);
"""


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Compute USD cost for a single LLM call.

    Unknown models fall back to 0.0 and emit a warning rather than raising,
    so instrumentation can never break a request.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        logger.warning(
            "cost.unknown_model",
            extra={"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens},
        )
        return 0.0

    # whisper-1 is billed per minute. Callers should pass minutes in the
    # input_tokens position when using record_llm_call for whisper.
    if "per_minute" in pricing and pricing.get("per_minute", 0) > 0:
        minutes = float(input_tokens)
        return round(minutes * pricing["per_minute"], 6)

    input_rate = pricing.get("input", 0.0)
    output_rate = pricing.get("output", 0.0)
    cost = (input_tokens / 1_000_000.0) * input_rate + (output_tokens / 1_000_000.0) * output_rate
    return round(cost, 6)


class CostTracker:
    """SQLite-backed cost event store."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self.init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_llm_call(
        self,
        tenant_id: str,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        request_id: Optional[str] = None,
        module: str = "llm",
        timestamp: Optional[float] = None,
    ) -> float:
        """Record a chat / completion call. Returns the computed cost in USD."""
        cost = calculate_cost(model, input_tokens, output_tokens)
        ts = timestamp if timestamp is not None else time.time()
        self._conn.execute(
            "INSERT INTO cost_events "
            "(tenant_id, timestamp, module, model, operation, input_tokens, output_tokens, cost_usd, request_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, ts, module, model, operation, int(input_tokens), int(output_tokens), cost, request_id),
        )
        self._conn.commit()
        return cost

    def record_embedding(
        self,
        tenant_id: str,
        model: str,
        tokens: int,
        request_id: Optional[str] = None,
        operation: str = "embed",
        timestamp: Optional[float] = None,
    ) -> float:
        """Record an embedding call. Embeddings only have input tokens."""
        return self.record_llm_call(
            tenant_id=tenant_id,
            model=model,
            operation=operation,
            input_tokens=tokens,
            output_tokens=0,
            request_id=request_id,
            module="embedding",
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_tenant_costs(
        self,
        tenant_id: str,
        start: Optional[float] = None,
        end: Optional[float] = None,
        group_by: str = "day",
    ) -> dict:
        """Return per-tenant cost breakdown.

        group_by: "day" | "model" | "operation"
        """
        start = start if start is not None else 0.0
        end = end if end is not None else time.time() + 1

        if group_by not in {"day", "model", "operation"}:
            raise ValueError(f"invalid group_by: {group_by}")

        if group_by == "day":
            rows = self._conn.execute(
                "SELECT date(timestamp, 'unixepoch') as bucket, "
                "SUM(input_tokens) as input_tokens, "
                "SUM(output_tokens) as output_tokens, "
                "SUM(cost_usd) as cost_usd, "
                "COUNT(*) as calls "
                "FROM cost_events "
                "WHERE tenant_id = ? AND timestamp >= ? AND timestamp < ? "
                "GROUP BY bucket ORDER BY bucket",
                (tenant_id, start, end),
            ).fetchall()
        else:
            col = "model" if group_by == "model" else "operation"
            rows = self._conn.execute(
                f"SELECT {col} as bucket, "
                "SUM(input_tokens) as input_tokens, "
                "SUM(output_tokens) as output_tokens, "
                "SUM(cost_usd) as cost_usd, "
                "COUNT(*) as calls "
                "FROM cost_events "
                "WHERE tenant_id = ? AND timestamp >= ? AND timestamp < ? "
                f"GROUP BY {col} ORDER BY cost_usd DESC",
                (tenant_id, start, end),
            ).fetchall()

        total_row = self._conn.execute(
            "SELECT SUM(input_tokens) as input_tokens, "
            "SUM(output_tokens) as output_tokens, "
            "SUM(cost_usd) as cost_usd, "
            "COUNT(*) as calls "
            "FROM cost_events "
            "WHERE tenant_id = ? AND timestamp >= ? AND timestamp < ?",
            (tenant_id, start, end),
        ).fetchone()

        return {
            "tenant_id": tenant_id,
            "start": _ts_to_iso(start),
            "end": _ts_to_iso(end),
            "group_by": group_by,
            "total": {
                "input_tokens": int(total_row["input_tokens"] or 0),
                "output_tokens": int(total_row["output_tokens"] or 0),
                "cost_usd": round(total_row["cost_usd"] or 0.0, 6),
                "calls": int(total_row["calls"] or 0),
            },
            "buckets": [
                {
                    "key": r["bucket"],
                    "input_tokens": int(r["input_tokens"] or 0),
                    "output_tokens": int(r["output_tokens"] or 0),
                    "cost_usd": round(r["cost_usd"] or 0.0, 6),
                    "calls": int(r["calls"] or 0),
                }
                for r in rows
            ],
        }

    def get_admin_summary(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> dict:
        """Cross-tenant totals + breakdown by model and by tenant."""
        start = start if start is not None else 0.0
        end = end if end is not None else time.time() + 1

        totals = self._conn.execute(
            "SELECT SUM(cost_usd) as cost_usd, "
            "SUM(input_tokens) as input_tokens, "
            "SUM(output_tokens) as output_tokens, "
            "COUNT(*) as calls, "
            "COUNT(DISTINCT tenant_id) as tenants "
            "FROM cost_events WHERE timestamp >= ? AND timestamp < ?",
            (start, end),
        ).fetchone()

        by_model = self._conn.execute(
            "SELECT model, SUM(cost_usd) as cost_usd, "
            "SUM(input_tokens) as input_tokens, "
            "SUM(output_tokens) as output_tokens, "
            "COUNT(*) as calls "
            "FROM cost_events WHERE timestamp >= ? AND timestamp < ? "
            "GROUP BY model ORDER BY cost_usd DESC",
            (start, end),
        ).fetchall()

        by_tenant = self._conn.execute(
            "SELECT tenant_id, SUM(cost_usd) as cost_usd, "
            "SUM(input_tokens) as input_tokens, "
            "SUM(output_tokens) as output_tokens, "
            "COUNT(*) as calls "
            "FROM cost_events WHERE timestamp >= ? AND timestamp < ? "
            "GROUP BY tenant_id ORDER BY cost_usd DESC LIMIT 20",
            (start, end),
        ).fetchall()

        return {
            "start": _ts_to_iso(start),
            "end": _ts_to_iso(end),
            "total_cost_usd": round(totals["cost_usd"] or 0.0, 6),
            "total_input_tokens": int(totals["input_tokens"] or 0),
            "total_output_tokens": int(totals["output_tokens"] or 0),
            "total_calls": int(totals["calls"] or 0),
            "active_tenants": int(totals["tenants"] or 0),
            "by_model": [
                {
                    "model": r["model"],
                    "cost_usd": round(r["cost_usd"] or 0.0, 6),
                    "input_tokens": int(r["input_tokens"] or 0),
                    "output_tokens": int(r["output_tokens"] or 0),
                    "calls": int(r["calls"] or 0),
                }
                for r in by_model
            ],
            "by_tenant": [
                {
                    "tenant_id": r["tenant_id"],
                    "cost_usd": round(r["cost_usd"] or 0.0, 6),
                    "input_tokens": int(r["input_tokens"] or 0),
                    "output_tokens": int(r["output_tokens"] or 0),
                    "calls": int(r["calls"] or 0),
                }
                for r in by_tenant
            ],
        }

    def top_tenants_by_cost(
        self,
        start: Optional[float] = None,
        end: Optional[float] = None,
        limit: int = 10,
    ) -> list[dict]:
        start = start if start is not None else 0.0
        end = end if end is not None else time.time() + 1
        rows = self._conn.execute(
            "SELECT tenant_id, SUM(cost_usd) as cost_usd, "
            "SUM(input_tokens) as input_tokens, "
            "SUM(output_tokens) as output_tokens, "
            "COUNT(*) as calls "
            "FROM cost_events WHERE timestamp >= ? AND timestamp < ? "
            "GROUP BY tenant_id ORDER BY cost_usd DESC LIMIT ?",
            (start, end, limit),
        ).fetchall()
        return [
            {
                "tenant_id": r["tenant_id"],
                "cost_usd": round(r["cost_usd"] or 0.0, 6),
                "input_tokens": int(r["input_tokens"] or 0),
                "output_tokens": int(r["output_tokens"] or 0),
                "calls": int(r["calls"] or 0),
            }
            for r in rows
        ]

    def estimate_monthly_cost(self, tenant_id: str) -> dict:
        """Extrapolate a 30-day projection from the last 7 days of usage."""
        now = time.time()
        window_days = 7
        window_start = now - window_days * 86400

        row = self._conn.execute(
            "SELECT SUM(cost_usd) as cost_usd, COUNT(*) as calls "
            "FROM cost_events WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, window_start),
        ).fetchone()

        window_cost = float(row["cost_usd"] or 0.0)
        window_calls = int(row["calls"] or 0)
        # Simple linear extrapolation: scale 7-day window to 30 days.
        daily_avg = window_cost / window_days if window_days else 0.0
        projected_30d = round(daily_avg * 30.0, 6)

        return {
            "tenant_id": tenant_id,
            "window_days": window_days,
            "window_cost_usd": round(window_cost, 6),
            "window_calls": window_calls,
            "daily_avg_usd": round(daily_avg, 6),
            "projected_30d_usd": projected_30d,
        }

    def close(self) -> None:
        self._conn.close()


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Module-level singleton (matches api/analytics.py pattern)
# ---------------------------------------------------------------------------

_cost_tracker: Optional[CostTracker] = None


def init_cost_tracker(output_dir: str) -> CostTracker:
    """Initialize the global cost tracker. Call once at startup."""
    global _cost_tracker
    db_path = os.path.join(output_dir, "costs.db")
    _cost_tracker = CostTracker(db_path)
    return _cost_tracker


def get_cost_tracker() -> CostTracker:
    """Return the global cost tracker singleton."""
    if _cost_tracker is None:
        raise RuntimeError("Cost tracker not initialized -- call init_cost_tracker() first")
    return _cost_tracker
