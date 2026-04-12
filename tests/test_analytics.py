"""Tests for api/analytics.py - AnalyticsStore event logging, aggregation, export."""

import csv
import io
import time

import pytest

from api.analytics import AnalyticsStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def analytics(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    store = AnalyticsStore(db_path)
    yield store
    store.close()


def _seed_queries(analytics, tenant_id="t1", count=5, meeting_body="Council"):
    """Helper: log N queries for a tenant."""
    for i in range(count):
        analytics.log_query(
            tenant_id=tenant_id,
            question=f"Question {i}",
            model="gpt-4o",
            response_time_ms=100.0 + i * 10,
            chunks_retrieved=5,
            meeting_body=meeting_body,
        )


# ============================================================
# 1. Event logging
# ============================================================

class TestEventLogging:

    def test_log_query(self, analytics):
        analytics.log_query(
            tenant_id="t1",
            question="What about zoning?",
            model="gpt-4o",
            response_time_ms=150.5,
            chunks_retrieved=8,
            meeting_body="Council",
        )
        summary = analytics.usage_summary("t1", "30d")
        assert summary["total_queries"] == 1

    def test_log_meeting_processed(self, analytics):
        analytics.log_meeting_processed(
            tenant_id="t1",
            clip_id="6669",
            processing_time_seconds=120.5,
        )
        summary = analytics.usage_summary("t1", "30d")
        assert summary["meetings_processed"] == 1

    def test_log_api_call(self, analytics):
        analytics.log_api_call(
            tenant_id="t1",
            endpoint="/api/v1/ask",
            status_code=200,
            duration_ms=250.0,
        )
        # API calls tracked in admin overview
        overview = analytics.admin_overview("30d")
        assert overview["total_api_calls"] == 1

    def test_multiple_queries_counted(self, analytics):
        _seed_queries(analytics, count=10)
        summary = analytics.usage_summary("t1", "30d")
        assert summary["total_queries"] == 10

    def test_log_query_without_optional_fields(self, analytics):
        analytics.log_query(tenant_id="t1", question="simple query")
        summary = analytics.usage_summary("t1", "30d")
        assert summary["total_queries"] == 1


# ============================================================
# 2. Aggregation queries
# ============================================================

class TestAggregation:

    def test_usage_summary(self, analytics):
        _seed_queries(analytics, count=5)
        analytics.log_meeting_processed("t1", "clip1")
        summary = analytics.usage_summary("t1", "30d")
        assert summary["total_queries"] == 5
        assert summary["meetings_processed"] == 1
        assert summary["avg_response_time_ms"] > 0
        assert summary["period"] == "30d"

    def test_usage_summary_empty_tenant(self, analytics):
        summary = analytics.usage_summary("empty", "30d")
        assert summary["total_queries"] == 0
        assert summary["meetings_processed"] == 0
        assert summary["avg_response_time_ms"] == 0

    def test_queries_over_time(self, analytics):
        _seed_queries(analytics, count=3)
        data = analytics.queries_over_time("t1", "7d")
        assert len(data) >= 1
        assert data[0]["count"] == 3
        assert "date" in data[0]
        assert "avg_response_time_ms" in data[0]

    def test_popular_meeting_bodies(self, analytics):
        _seed_queries(analytics, count=5, meeting_body="Council")
        _seed_queries(analytics, count=3, meeting_body="Planning")
        bodies = analytics.popular_meeting_bodies("t1", "30d")
        assert len(bodies) == 2
        # Council should be first (more queries)
        assert bodies[0]["meeting_body"] == "Council"
        assert bodies[0]["count"] == 5
        assert bodies[1]["meeting_body"] == "Planning"

    def test_popular_meeting_bodies_excludes_null(self, analytics):
        analytics.log_query(tenant_id="t1", question="q1", meeting_body=None)
        analytics.log_query(tenant_id="t1", question="q2", meeting_body="")
        analytics.log_query(tenant_id="t1", question="q3", meeting_body="Council")
        bodies = analytics.popular_meeting_bodies("t1", "30d")
        assert len(bodies) == 1
        assert bodies[0]["meeting_body"] == "Council"

    def test_peak_usage_hours(self, analytics):
        _seed_queries(analytics, count=5)
        hours = analytics.peak_usage_hours("t1", "30d")
        assert len(hours) >= 1
        assert "hour" in hours[0]
        assert "count" in hours[0]

    def test_top_questions(self, analytics):
        # Log the same question multiple times
        for _ in range(3):
            analytics.log_query(tenant_id="t1", question="What about zoning?", response_time_ms=100)
        analytics.log_query(tenant_id="t1", question="Budget?", response_time_ms=50)
        top = analytics.top_questions("t1", "30d")
        assert top[0]["question"] == "What about zoning?"
        assert top[0]["count"] == 3

    def test_recent_queries(self, analytics):
        _seed_queries(analytics, count=3)
        recent = analytics.recent_queries("t1", limit=10)
        assert len(recent) == 3
        assert "question" in recent[0]
        assert "timestamp" in recent[0]

    def test_admin_overview(self, analytics):
        _seed_queries(analytics, tenant_id="t1", count=5)
        _seed_queries(analytics, tenant_id="t2", count=3)
        analytics.log_api_call("t1", "/ask", 200, 100.0)
        analytics.log_api_call("t1", "/ask", 500, 200.0)
        overview = analytics.admin_overview("30d")
        assert overview["total_queries"] == 8
        assert overview["active_tenants"] == 2
        assert overview["total_api_calls"] == 2
        assert overview["api_errors"] == 1
        assert len(overview["per_tenant"]) == 2


# ============================================================
# 3. Period filtering
# ============================================================

class TestPeriodFiltering:

    def test_7d_period(self, analytics):
        _seed_queries(analytics, count=5)
        summary = analytics.usage_summary("t1", "7d")
        assert summary["total_queries"] == 5
        assert summary["period"] == "7d"

    def test_30d_period(self, analytics):
        _seed_queries(analytics, count=5)
        summary = analytics.usage_summary("t1", "30d")
        assert summary["total_queries"] == 5

    def test_90d_period(self, analytics):
        _seed_queries(analytics, count=5)
        summary = analytics.usage_summary("t1", "90d")
        assert summary["total_queries"] == 5

    def test_period_filtering_excludes_old_data(self, analytics):
        """Manually insert an old query event and verify it's excluded from short periods."""

        old_timestamp = time.time() - (40 * 86400)  # 40 days ago
        analytics._conn.execute(
            "INSERT INTO query_events (tenant_id, question, model, response_time_ms, chunks_retrieved, meeting_body, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t1", "Old question", "gpt-4o", 100.0, 5, "Council", old_timestamp),
        )
        analytics._conn.commit()

        # Also add a recent query
        analytics.log_query(tenant_id="t1", question="Recent", response_time_ms=50)

        summary_7d = analytics.usage_summary("t1", "7d")
        assert summary_7d["total_queries"] == 1  # Only recent

        summary_30d = analytics.usage_summary("t1", "30d")
        assert summary_30d["total_queries"] == 1  # Old one is 40 days ago

        summary_90d = analytics.usage_summary("t1", "90d")
        assert summary_90d["total_queries"] == 2  # Both included


# ============================================================
# 4. CSV export
# ============================================================

class TestCSVExport:

    def test_export_queries_csv_format(self, analytics):
        _seed_queries(analytics, count=3)
        csv_data = analytics.export_queries_csv("t1", "30d")
        reader = csv.reader(io.StringIO(csv_data))
        rows = list(reader)
        # Header + 3 data rows
        assert len(rows) == 4
        header = rows[0]
        assert "tenant_id" in header
        assert "question" in header
        assert "model" in header
        assert "response_time_ms" in header
        assert "time" in header

    def test_export_queries_csv_empty(self, analytics):
        csv_data = analytics.export_queries_csv("empty-tenant", "30d")
        reader = csv.reader(io.StringIO(csv_data))
        rows = list(reader)
        assert len(rows) == 1  # Header only

    def test_export_api_calls_csv_format(self, analytics):
        analytics.log_api_call("t1", "/ask", 200, 100.0)
        analytics.log_api_call("t1", "/chat", 500, 200.0)
        csv_data = analytics.export_api_calls_csv("t1", "30d")
        reader = csv.reader(io.StringIO(csv_data))
        rows = list(reader)
        assert len(rows) == 3  # Header + 2 data rows
        header = rows[0]
        assert "endpoint" in header
        assert "status_code" in header

    def test_export_queries_csv_data_correctness(self, analytics):
        analytics.log_query(
            tenant_id="t1",
            question="Test Q",
            model="gpt-4o",
            response_time_ms=123.4,
            chunks_retrieved=7,
            meeting_body="Planning",
        )
        csv_data = analytics.export_queries_csv("t1", "30d")
        reader = csv.reader(io.StringIO(csv_data))
        rows = list(reader)
        data_row = rows[1]
        assert data_row[0] == "t1"  # tenant_id
        assert data_row[1] == "Test Q"  # question
        assert data_row[2] == "gpt-4o"  # model


# ============================================================
# 5. Revenue metrics (admin)
# ============================================================

class TestRevenueMetrics:

    def test_admin_revenue_metrics_empty(self, analytics):
        metrics = analytics.admin_revenue_metrics()
        assert metrics["active_tenants_current"] == 0
        assert metrics["churned_tenants"] == 0
        assert metrics["churn_rate"] == 0

    def test_admin_revenue_metrics_with_data(self, analytics):
        _seed_queries(analytics, tenant_id="t1", count=5)
        _seed_queries(analytics, tenant_id="t2", count=3)
        metrics = analytics.admin_revenue_metrics()
        assert metrics["active_tenants_current"] == 2
        assert "note" in metrics
