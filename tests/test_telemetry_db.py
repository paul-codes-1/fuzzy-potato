"""Tests for the telemetry SQLite sink + analytics() aggregation.

The sink is best-effort and MUST never raise (a telemetry outage can't be
allowed to break a public request), so the never-raises cases cover both a
corrupt DB file (init fails) and a write that blows up (locked/failed).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from rag import telemetry


@pytest.fixture
def temp_telemetry_db(tmp_path, monkeypatch):
    """Point the sink at a throwaway DB and reset the cached connection."""
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setenv("RAG_TELEMETRY_DB", str(db_path))
    monkeypatch.delenv("JURISDICTION", raising=False)
    telemetry._reset_db_state_for_tests()
    telemetry.set_request_context(client_ip="10.0.0.1", user_agent="pytest/1.0")
    yield db_path
    telemetry._reset_db_state_for_tests()


def _fetch_events(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM rag_events ORDER BY id")]
    finally:
        conn.close()


def test_event_written_with_expected_columns(temp_telemetry_db):
    telemetry.log_query_event(
        surface="mcp",
        endpoint="ask_meetings",
        query="parks budget",
        filters={"date_after": "2026-01-01"},
        result_count=2,
        latency_ms=100.0,
        status="ok",
        model="gpt-4o",
    )
    events = _fetch_events(temp_telemetry_db)
    assert len(events) == 1
    e = events[0]
    assert e["transport"] == "mcp"
    assert e["endpoint"] == "ask_meetings"
    assert e["query"] == "parks budget"
    assert e["result_count"] == 2
    assert e["latency_ms"] == 100.0
    assert e["status"] == "ok"
    assert e["model_used"] == "gpt-4o"
    assert e["rate_limited"] == 0
    assert e["jurisdiction"] == "lfucg"  # default when JURISDICTION unset
    assert json.loads(e["filters"]) == {"date_after": "2026-01-01"}
    # IP is hashed, never stored raw.
    assert e["ip_hash"] and e["ip_hash"] != "10.0.0.1"


def test_rate_limited_status_sets_flag(temp_telemetry_db):
    telemetry.log_query_event(
        surface="http",
        endpoint="/api/ask",
        query="q",
        status="rate_limited",
        error_type="rate_limit_hour_limit",
    )
    e = _fetch_events(temp_telemetry_db)[0]
    assert e["rate_limited"] == 1
    assert e["status"] == "rate_limited"


def test_long_query_truncated_in_db(temp_telemetry_db):
    big = "z" * (telemetry.MAX_QUERY_CHARS + 40)
    telemetry.log_query_event(surface="http", endpoint="/api/ask", query=big, status="ok")
    e = _fetch_events(temp_telemetry_db)[0]
    # Same 200-char + ellipsis cap the log line uses.
    assert e["query"].endswith("…")
    assert len(e["query"]) == telemetry.MAX_QUERY_CHARS + 1


def test_never_raises_with_corrupt_db(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    db_path.write_bytes(b"definitely not a sqlite database " * 20)
    monkeypatch.setenv("RAG_TELEMETRY_DB", str(db_path))
    telemetry._reset_db_state_for_tests()
    telemetry.set_request_context(client_ip=None, user_agent=None)
    try:
        # Init will fail on the garbage file; must degrade to log-only, not raise.
        telemetry.log_query_event(surface="http", endpoint="/api/ask", query="hi", status="ok")
        # A second call hits the disabled-sink fast path — still must not raise.
        telemetry.log_query_event(surface="http", endpoint="/api/ask", query="hi2", status="ok")
    finally:
        telemetry._reset_db_state_for_tests()


def test_never_raises_when_write_fails(temp_telemetry_db, monkeypatch):
    class BoomConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def commit(self):  # pragma: no cover - not reached
            pass

    monkeypatch.setattr(telemetry, "_get_db", lambda: BoomConn())
    # A locked/failing write must be swallowed.
    telemetry.log_query_event(surface="http", endpoint="/api/ask", query="hi", status="ok")


def test_analytics_empty_db(temp_telemetry_db):
    a = telemetry.analytics(window_days=7)
    assert a["total"] == 0
    assert a["empty_result_rate"] == 0.0
    assert a["rate_limited_count"] == 0
    assert a["top_queries"] == []
    assert a["by_transport"] == {}
    assert a["by_endpoint"] == []
    assert a["latency_ms"] == {"p50": 0.0, "p95": 0.0}


def test_analytics_aggregates(temp_telemetry_db):
    for i in range(3):
        telemetry.log_query_event(
            surface="http", endpoint="/api/ask", query="repeated q",
            result_count=2, latency_ms=100.0 + i, status="ok", model="gpt-4o",
        )
    telemetry.log_query_event(
        surface="http", endpoint="/api/search", query="no hits",
        result_count=0, latency_ms=5.0, status="empty",
    )
    telemetry.log_query_event(
        surface="mcp", endpoint="ask_meetings", query="rl",
        status="rate_limited", error_type="rate_limit_hour_limit",
    )

    a = telemetry.analytics(window_days=7)
    assert a["total"] == 5
    assert a["by_transport"] == {"http": 4, "mcp": 1}
    assert a["rate_limited_count"] == 1
    assert a["empty_result_rate"] == round(1 / 5, 4)

    top = {q["query"]: q["count"] for q in a["top_queries"]}
    assert top["repeated q"] == 3

    # by_endpoint sorted by count desc → /api/ask (3) leads.
    assert a["by_endpoint"][0] == {"endpoint": "/api/ask", "count": 3}
    # rate_limited event had no latency → excluded from percentiles, which
    # are computed over the 4 timed events.
    assert a["latency_ms"]["p50"] > 0


# --- referer / origin columns (2026-09-12) ------------------------------

def test_referer_and_origin_recorded(temp_telemetry_db):
    telemetry.set_request_context(
        client_ip="10.0.0.1", user_agent="pytest/1.0",
        referer="https://meetings.lexingtonky.news/meeting/6865?highlight=x",
        origin="https://meetings.lexingtonky.news",
    )
    telemetry.log_query_event(surface="http", endpoint="/api/related", status="ok", result_count=5)
    row = _fetch_events(temp_telemetry_db)[0]
    assert row["referer"] == "https://meetings.lexingtonky.news/meeting/6865?highlight=x"
    assert row["origin"] == "https://meetings.lexingtonky.news"


def test_referer_absent_is_null_and_long_values_clipped(temp_telemetry_db):
    telemetry.set_request_context(client_ip=None, user_agent=None)
    telemetry.log_query_event(surface="mcp", endpoint="search_meetings", status="ok")
    telemetry.set_request_context(client_ip=None, user_agent=None, referer="x" * 2000, origin="")
    telemetry.log_query_event(surface="http", endpoint="/api/search", status="ok")
    rows = _fetch_events(temp_telemetry_db)
    assert rows[0]["referer"] is None and rows[0]["origin"] is None
    assert len(rows[1]["referer"]) == telemetry.MAX_HEADER_CHARS
    assert rows[1]["origin"] is None


def test_migration_adds_columns_to_pre_existing_db(tmp_path, monkeypatch):
    """A telemetry.db created by the old schema (no referer/origin) must be
    upgraded in place on open — prod has 17k rows in one."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    old_schema = telemetry._SCHEMA_SQL
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO rag_events (ts, transport, endpoint, status) VALUES ('2026-01-01T00:00:00+00:00','http','/api/ask','ok')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("RAG_TELEMETRY_DB", str(db_path))
    telemetry._reset_db_state_for_tests()
    try:
        telemetry.set_request_context(client_ip=None, user_agent=None, referer="https://a.example/x")
        telemetry.log_query_event(surface="http", endpoint="/api/ask", status="ok")
        rows = _fetch_events(db_path)
        assert {"referer", "origin"} <= set(rows[0].keys())
        assert rows[0]["referer"] is None          # legacy row
        assert rows[1]["referer"] == "https://a.example/x"
        # Re-open is idempotent (no "duplicate column" error).
        telemetry._reset_db_state_for_tests()
        telemetry.log_query_event(surface="http", endpoint="/api/ask", status="ok")
        assert len(_fetch_events(db_path)) == 3
    finally:
        telemetry._reset_db_state_for_tests()


def test_analytics_reports_top_referer_hosts(temp_telemetry_db):
    for ref in ("https://meetings.lexingtonky.news/meeting/1", "https://meetings.lexingtonky.news/", None,
                "https://feeds.lexingtonky.news/article/x"):
        telemetry.set_request_context(client_ip=None, user_agent=None, referer=ref)
        telemetry.log_query_event(surface="http", endpoint="/api/related", status="ok")
    out = telemetry.analytics(window_days=7)
    hosts = {r["referer"]: r["count"] for r in out["top_referers"]}
    assert hosts["https://meetings.lexingtonky.news"] == 2
    assert hosts["https://feeds.lexingtonky.news"] == 1
    assert hosts["(none)"] == 1
