"""Unit tests for the structured-logging telemetry helper."""

from __future__ import annotations

import json
import logging
import re

import pytest

from rag import telemetry


@pytest.fixture
def capture_logs(caplog):
    caplog.set_level(logging.INFO, logger="rag.telemetry")
    return caplog


def test_log_query_event_emits_json_payload(capture_logs):
    telemetry.set_request_context(client_ip="10.0.0.1", user_agent="pytest/1.0")
    telemetry.log_query_event(
        surface="http",
        endpoint="/api/search",
        query="short term rentals",
        filters={"meeting_body": "Council"},
        result_count=7,
        latency_ms=42.7,
        status="ok",
    )
    json_lines = [
        m for m in capture_logs.messages if m.startswith("rag.query {")
    ]
    assert json_lines, f"expected one rag.query JSON line; got {capture_logs.messages}"
    payload = json.loads(json_lines[-1][len("rag.query "):])
    assert payload["event"] == "rag.query"
    assert payload["surface"] == "http"
    assert payload["endpoint"] == "/api/search"
    assert payload["query"] == "short term rentals"
    assert payload["filters"] == {"meeting_body": "Council"}
    assert payload["result_count"] == 7
    assert payload["latency_ms"] == 42.7
    assert payload["status"] == "ok"
    assert payload["user_agent"] == "pytest/1.0"
    assert re.fullmatch(r"[0-9a-f]{16}", payload["ip_hash"])
    assert payload["request_id"]  # non-empty


def test_long_query_is_truncated(capture_logs):
    telemetry.set_request_context(client_ip=None, user_agent=None)
    big = "a" * (telemetry.MAX_QUERY_CHARS + 50)
    telemetry.log_query_event(surface="http", endpoint="/api/ask", query=big, status="ok")
    json_line = [m for m in capture_logs.messages if m.startswith("rag.query {")][-1]
    payload = json.loads(json_line[len("rag.query "):])
    assert payload["query"].endswith("…")
    assert len(payload["query"]) == telemetry.MAX_QUERY_CHARS + 1  # ellipsis char


def test_ip_hash_is_stable_within_day_and_not_raw_ip():
    telemetry.set_request_context(client_ip="203.0.113.5", user_agent=None)
    h1 = telemetry.hash_ip("203.0.113.5")
    h2 = telemetry.hash_ip("203.0.113.5")
    assert h1 == h2
    assert h1 != "203.0.113.5"
    assert len(h1) == 16


def test_ip_hash_returns_none_for_none():
    assert telemetry.hash_ip(None) is None
    assert telemetry.hash_ip("") is None


def test_emit_never_raises_when_payload_is_weird(monkeypatch, capture_logs):
    telemetry.set_request_context(client_ip="1.1.1.1", user_agent=None)
    # Force the JSON serializer to blow up on a non-serializable value;
    # the helper should still not raise.
    class NoJson:
        def __repr__(self):
            raise RuntimeError("boom")

    # Should not raise.
    telemetry.log_query_event(
        surface="http",
        endpoint="/api/ask",
        query="hi",
        filters={"bad": NoJson()},
        status="ok",
    )
