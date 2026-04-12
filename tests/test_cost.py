"""Tests for the per-tenant cost attribution module (api/cost.py)."""

import logging
import os
import time
from datetime import datetime

import pytest

from api.cost import (
    MODEL_PRICING,
    CostTracker,
    calculate_cost,
    get_cost_tracker,
    init_cost_tracker,
)


@pytest.fixture
def tracker(tmp_path):
    db_path = str(tmp_path / "costs.db")
    return CostTracker(db_path)


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------

def test_init_db_creates_tables_and_indexes(tmp_path):
    db_path = str(tmp_path / "costs.db")
    tracker = CostTracker(db_path)
    assert os.path.exists(db_path)

    # Schema has the right columns
    cols = {
        r["name"]
        for r in tracker._conn.execute("PRAGMA table_info(cost_events)").fetchall()
    }
    assert cols == {
        "id",
        "tenant_id",
        "timestamp",
        "module",
        "model",
        "operation",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "request_id",
    }

    # Indexes present
    idx = {
        r["name"]
        for r in tracker._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cost_events'"
        ).fetchall()
    }
    assert "idx_cost_tenant_ts" in idx
    assert "idx_cost_ts" in idx


def test_singleton_init_and_get(tmp_path):
    init_cost_tracker(str(tmp_path))
    t = get_cost_tracker()
    assert isinstance(t, CostTracker)
    assert os.path.exists(os.path.join(str(tmp_path), "costs.db"))


# ---------------------------------------------------------------------------
# calculate_cost math
# ---------------------------------------------------------------------------

def test_calculate_cost_gpt_4o_math():
    # gpt-4o: $2.50/M input, $10.00/M output
    # 1M input + 500k output = 2.50 + 5.00 = 7.50
    cost = calculate_cost("gpt-4o", 1_000_000, 500_000)
    assert cost == pytest.approx(7.50, abs=1e-6)


def test_calculate_cost_embedding():
    # text-embedding-3-small: $0.02/M input, no output
    # 2,000,000 tokens = 0.04
    cost = calculate_cost("text-embedding-3-small", 2_000_000, 0)
    assert cost == pytest.approx(0.04, abs=1e-6)


def test_calculate_cost_claude_sonnet():
    # claude-3-5-sonnet: $3.00/M input, $15.00/M output
    # 200k input + 100k output = 0.60 + 1.50 = 2.10
    cost = calculate_cost("claude-3-5-sonnet-latest", 200_000, 100_000)
    assert cost == pytest.approx(2.10, abs=1e-6)


def test_calculate_cost_whisper_per_minute():
    # whisper-1: $0.006/min, passed via input_tokens as minutes
    cost = calculate_cost("whisper-1", 60, 0)
    assert cost == pytest.approx(0.36, abs=1e-6)


def test_calculate_cost_unknown_model_returns_zero(caplog):
    with caplog.at_level(logging.WARNING):
        cost = calculate_cost("mystery-model-9000", 100, 200)
    assert cost == 0.0
    assert any("unknown_model" in rec.message or "unknown_model" in str(rec)
               for rec in caplog.records)


# ---------------------------------------------------------------------------
# record_llm_call / record_embedding
# ---------------------------------------------------------------------------

def test_record_llm_call_inserts_row_with_correct_cost(tracker):
    cost = tracker.record_llm_call(
        tenant_id="elk-grove",
        model="gpt-4o",
        operation="rag.ask",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        request_id="req-abc",
    )
    # gpt-4o: 1M in * $2.50 + 1M out * $10 = 12.50
    assert cost == pytest.approx(12.50, abs=1e-6)

    row = tracker._conn.execute(
        "SELECT * FROM cost_events WHERE tenant_id = ?", ("elk-grove",)
    ).fetchone()
    assert row["tenant_id"] == "elk-grove"
    assert row["model"] == "gpt-4o"
    assert row["operation"] == "rag.ask"
    assert row["input_tokens"] == 1_000_000
    assert row["output_tokens"] == 1_000_000
    assert row["cost_usd"] == pytest.approx(12.50, abs=1e-6)
    assert row["request_id"] == "req-abc"
    assert row["module"] == "llm"


def test_record_embedding_writes_row_with_zero_output(tracker):
    cost = tracker.record_embedding(
        tenant_id="lex-ky",
        model="text-embedding-3-small",
        tokens=500_000,
        request_id="req-emb",
    )
    # 0.5M * $0.02 = 0.01
    assert cost == pytest.approx(0.01, abs=1e-6)

    row = tracker._conn.execute(
        "SELECT * FROM cost_events WHERE tenant_id = 'lex-ky'"
    ).fetchone()
    assert row["input_tokens"] == 500_000
    assert row["output_tokens"] == 0
    assert row["module"] == "embedding"
    assert row["operation"] == "embed"


def test_unknown_model_does_not_crash_record(tracker):
    # Must not raise; cost falls back to 0.
    cost = tracker.record_llm_call(
        tenant_id="tenant-x",
        model="future-model-2030",
        operation="op",
        input_tokens=1000,
        output_tokens=1000,
    )
    assert cost == 0.0
    row = tracker._conn.execute(
        "SELECT * FROM cost_events WHERE tenant_id = 'tenant-x'"
    ).fetchone()
    assert row is not None
    assert row["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# get_tenant_costs
# ---------------------------------------------------------------------------

def _seed_sample_events(tracker: CostTracker):
    """Seed sample events across two tenants, three days, two models."""
    now = time.time()
    day = 86400
    # tenant A - ~5 days ago
    tracker.record_llm_call(
        "tenant-a", "gpt-4o", "ask", 100_000, 50_000,
        timestamp=now - 5 * day,
    )
    # tenant A - ~3 days ago
    tracker.record_llm_call(
        "tenant-a", "gpt-4o-mini", "chat", 200_000, 80_000,
        timestamp=now - 3 * day,
    )
    # tenant A - recent (within last hour)
    tracker.record_embedding(
        "tenant-a", "text-embedding-3-small", 1_000_000,
        timestamp=now - 60,
    )
    # tenant B - recent
    tracker.record_llm_call(
        "tenant-b", "claude-3-5-sonnet-latest", "chat", 50_000, 20_000,
        timestamp=now - 60,
    )
    return now


def test_get_tenant_costs_filters_by_tenant_and_date(tracker):
    now = _seed_sample_events(tracker)
    day = 86400

    # Tenant A only, last 24h: only the embedding event
    result = tracker.get_tenant_costs(
        "tenant-a",
        start=now - day,
        end=now + 1,
        group_by="model",
    )
    assert result["total"]["calls"] == 1
    assert result["buckets"][0]["key"] == "text-embedding-3-small"
    # 1M * $0.02 = 0.02
    assert result["total"]["cost_usd"] == pytest.approx(0.02, abs=1e-6)


def test_group_by_day_returns_daily_buckets(tracker):
    _seed_sample_events(tracker)
    result = tracker.get_tenant_costs("tenant-a", group_by="day")
    # Three events for tenant-a on three distinct days
    assert len(result["buckets"]) == 3
    # Each bucket has ISO date key
    for b in result["buckets"]:
        # YYYY-MM-DD
        datetime.strptime(b["key"], "%Y-%m-%d")
    # Buckets sorted ascending
    keys = [b["key"] for b in result["buckets"]]
    assert keys == sorted(keys)


def test_group_by_model_returns_per_model_spend(tracker):
    _seed_sample_events(tracker)
    result = tracker.get_tenant_costs("tenant-a", group_by="model")
    models = {b["key"] for b in result["buckets"]}
    assert models == {"gpt-4o", "gpt-4o-mini", "text-embedding-3-small"}

    # Spot-check gpt-4o: 100k in * 2.50 + 50k out * 10 = 0.25 + 0.50 = 0.75
    gpt4o = [b for b in result["buckets"] if b["key"] == "gpt-4o"][0]
    assert gpt4o["cost_usd"] == pytest.approx(0.75, abs=1e-6)


def test_group_by_operation(tracker):
    _seed_sample_events(tracker)
    result = tracker.get_tenant_costs("tenant-a", group_by="operation")
    ops = {b["key"] for b in result["buckets"]}
    assert ops == {"ask", "chat", "embed"}


def test_group_by_invalid_raises(tracker):
    with pytest.raises(ValueError):
        tracker.get_tenant_costs("tenant-a", group_by="bogus")


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------

def test_get_admin_summary_totals_across_tenants(tracker):
    _seed_sample_events(tracker)
    summary = tracker.get_admin_summary()
    assert summary["active_tenants"] == 2
    assert summary["total_calls"] == 4
    # Sum of all events (rates per 1M tokens):
    #   a/gpt-4o:        100k*$2.50 + 50k*$10.00 = 0.25 + 0.50 = 0.75
    #   a/gpt-4o-mini:   200k*$0.15 + 80k*$0.60  = 0.03 + 0.048 = 0.078
    #   a/embedding:     1M*$0.02                = 0.02
    #   b/claude-sonnet: 50k*$3.00 + 20k*$15.00  = 0.15 + 0.30 = 0.45
    #   total = 0.75 + 0.078 + 0.02 + 0.45 = 1.298
    assert summary["total_cost_usd"] == pytest.approx(1.298, abs=1e-5)

    # by_tenant sorted descending by cost
    costs_by_tenant = {row["tenant_id"]: row["cost_usd"] for row in summary["by_tenant"]}
    # tenant-a = 0.75 + 0.078 + 0.02 = 0.848
    assert costs_by_tenant["tenant-a"] == pytest.approx(0.848, abs=1e-5)
    assert costs_by_tenant["tenant-b"] == pytest.approx(0.45, abs=1e-5)


def test_top_tenants_by_cost_ordering(tracker):
    _seed_sample_events(tracker)
    top = tracker.top_tenants_by_cost(limit=10)
    assert len(top) == 2
    # Descending by cost: tenant-a > tenant-b
    assert top[0]["tenant_id"] == "tenant-a"
    assert top[1]["tenant_id"] == "tenant-b"
    assert top[0]["cost_usd"] > top[1]["cost_usd"]


def test_top_tenants_limit(tracker):
    _seed_sample_events(tracker)
    top = tracker.top_tenants_by_cost(limit=1)
    assert len(top) == 1
    assert top[0]["tenant_id"] == "tenant-a"


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

def test_estimate_monthly_cost_extrapolates_from_last_7_days(tracker):
    now = time.time()
    # Put $7 of spend evenly across the last 7 days => $1/day => $30/mo
    # 1_000_000 out tokens * $10 = $10, so use half of that
    # Easier: use gpt-4o-mini: 100k in * 0.15 + 100k out * 0.60 = 0.075
    # We want 7 events totaling $7 so each event ~$1.
    # gpt-4o: 100k in + 90k out = 0.25 + 0.9 = 1.15 -> not clean
    # Let's just record two clean events: $3.50 + $3.50 at different days
    day = 86400
    tracker.record_llm_call(
        "tenant-z", "gpt-4o", "op",
        input_tokens=1_400_000, output_tokens=0,  # 1.4M * 2.50 = 3.5
        timestamp=now - 2 * day,
    )
    tracker.record_llm_call(
        "tenant-z", "gpt-4o", "op",
        input_tokens=1_400_000, output_tokens=0,
        timestamp=now - 5 * day,
    )

    est = tracker.estimate_monthly_cost("tenant-z")
    assert est["tenant_id"] == "tenant-z"
    assert est["window_days"] == 7
    assert est["window_cost_usd"] == pytest.approx(7.00, abs=1e-5)
    assert est["window_calls"] == 2
    # daily_avg = 7/7 = 1; 30d = 30
    assert est["daily_avg_usd"] == pytest.approx(1.00, abs=1e-5)
    assert est["projected_30d_usd"] == pytest.approx(30.00, abs=1e-5)


def test_estimate_monthly_cost_ignores_old_events(tracker):
    now = time.time()
    # An event from 20 days ago must NOT count toward the 7-day window
    tracker.record_llm_call(
        "tenant-z", "gpt-4o", "op",
        input_tokens=1_000_000, output_tokens=1_000_000,
        timestamp=now - 20 * 86400,
    )
    est = tracker.estimate_monthly_cost("tenant-z")
    assert est["window_cost_usd"] == 0.0
    assert est["projected_30d_usd"] == 0.0


def test_pricing_table_contains_required_models():
    """Sanity check: required models are all present with sensible rates."""
    required = {
        "gpt-4o",
        "gpt-4o-mini",
        "text-embedding-3-small",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "whisper-1",
    }
    assert required.issubset(MODEL_PRICING.keys())
    # Every entry has input rate
    for name, rates in MODEL_PRICING.items():
        assert "input" in rates, f"{name} missing input rate"
        assert rates["input"] >= 0
