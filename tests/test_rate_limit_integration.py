"""Integration tests verifying rate-limiting is wired through HTTP + MCP surfaces."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rag import rate_limit
from rag import telemetry
from rag import mcp_server as mcp_mod


@pytest.fixture(autouse=True)
def _reset_limiter():
    rate_limit.limiter.reset()
    # Clear leaked contextvars from any prior test so get_client_ip() == None
    # and the rate limiter falls back to the "unknown" bucket consistently.
    telemetry._client_ip_var.set(None)
    telemetry._user_agent_var.set(None)
    telemetry._request_id_var.set(None)
    yield
    rate_limit.limiter.reset()


def test_http_ask_returns_429_when_rate_limited():
    cap, _ = rate_limit.EXPENSIVE_LIMITS
    # Exhaust the expensive budget for the test-client's loopback IP.
    for _ in range(cap):
        d = rate_limit.limiter.check("expensive", "testclient")
        assert d.allowed

    with patch("rag.server.ask") as mock_ask, \
         patch("rag.server.get_vecstore"), \
         patch("rag.server.load_clip_metadata", return_value={}), \
         patch("rag.server.get_openai"):
        mock_ask.return_value = {"answer": "x", "sources": []}

        from fastapi.testclient import TestClient
        from rag.server import app

        client = TestClient(app)
        response = client.post("/api/ask", json={"question": "hi"})
        assert response.status_code == 429
        body = response.json()
        assert body["error"].startswith("Rate limit")
        assert body["reason"] == "hour_limit"
        assert response.headers.get("retry-after")
        # Real handler should not have been invoked.
        mock_ask.assert_not_called()


def test_mcp_ask_meetings_returns_error_dict_when_rate_limited():
    cap, _ = rate_limit.EXPENSIVE_LIMITS
    for _ in range(cap):
        assert rate_limit.limiter.check("expensive", "unknown").allowed

    # Mock the real RAG path so a rate-limit failure can't accidentally pass by
    # falling through to a successful ask() call against OpenAI.
    with patch("rag.mcp_server.ask") as mock_ask:
        # No contextvar set → falls back to "unknown" bucket, which we just exhausted.
        result = mcp_mod.ask_meetings_impl(question="anything")
        mock_ask.assert_not_called()

    assert "error" in result
    assert "Rate limit exceeded" in result["error"]
    assert result["reason"] == "hour_limit"
    assert result["retry_after_seconds"] >= 1


def test_mcp_search_meetings_uses_cheap_tier():
    """Cheap-tier limit is much higher than expensive — exhausting expensive
    shouldn't block search_meetings."""
    cap, _ = rate_limit.EXPENSIVE_LIMITS
    for _ in range(cap):
        rate_limit.limiter.check("expensive", "unknown")

    with patch("rag.mcp_server.search_clips", return_value=[]) as mock_search:
        result = mcp_mod.search_meetings_impl(q="zoning")
        # Should succeed despite expensive being exhausted.
        assert "error" not in result
        mock_search.assert_called_once()
