"""Tests for rag/server.py - FastAPI endpoints."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from starlette.requests import Request as StarletteRequest


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Drop the in-process answer/related/facets caches and rate-limit state
    between tests so a cached response (or spent budget) from one test can't
    leak into the next. The caches are keyed on question/clip, so two tests
    posting the same question would otherwise collide."""
    import rag.server as srv
    from rag.rate_limit import limiter

    def _clear():
        srv._ask_cache.clear()
        srv._related_cache.clear()
        srv._facets_cache = None
        srv._daily_ask_day = None
        srv._daily_ask_count = 0
        limiter.reset()

    _clear()
    yield
    _clear()


# ============================================================
# 1. POST /api/ask tests
# ============================================================

class TestAskEndpoint:
    """Test the POST /api/ask endpoint."""

    def test_ask_returns_200_with_valid_question(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "Test answer",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/ask", json={"question": "What about zoning?"})
            assert response.status_code == 200
            data = response.json()
            assert "answer" in data

    def test_ask_returns_422_with_missing_question(self):
        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/ask", json={})
            assert response.status_code == 422

    def test_ask_response_matches_schema(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "Zoning was discussed.",
                "sources": [
                    {
                        "clip_id": 6669,
                        "date": "2026-01-22",
                        "title": "Council Meeting",
                        "meeting_body": "Council",
                        "timestamp": 120,
                        "excerpt": "Zoning ordinance...",
                        "granicus_url": "https://lfucg.granicus.com/player/clip/6669?view_id=14&entrytime=120",
                    }
                ],
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 5,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/ask", json={
                "question": "What about zoning?",
                "meeting_body": "Council",
            })
            data = response.json()
            assert data["answer"] == "Zoning was discussed."
            assert len(data["sources"]) == 1
            assert data["sources"][0]["clip_id"] == 6669
            assert data["chunks_retrieved"] == 5

    def test_ask_passes_filters_from_request(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "Answer",
                "sources": [],
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 0,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            client.post("/api/ask", json={
                "question": "test",
                "meeting_body": "Council",
                "date_after": "2025-01-01",
                "date_before": "2026-12-31",
            })

            call_kwargs = mock_ask.call_args.kwargs
            filters = call_kwargs.get("filters")
            assert filters["meeting_body"] == "Council"
            assert filters["date_after"] == "2025-01-01"
            assert filters["date_before"] == "2026-12-31"


# ============================================================
# 2. GET /api/health tests
# ============================================================

class TestHealthEndpoint:
    """Test the GET /api/health endpoint."""

    def test_health_returns_200(self):
        with patch("rag.server.get_vecstore") as mock_coll, \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_collection = MagicMock()
            mock_collection.count.return_value = 1000
            mock_coll.return_value = mock_collection

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/api/health")
            assert response.status_code == 200

    def test_health_returns_chunk_and_clip_counts(self):
        import rag.server as server_module

        with patch("rag.server.get_vecstore") as mock_coll, \
             patch("rag.server.load_clip_metadata") as mock_meta, \
             patch("rag.server.get_openai"):
            mock_collection = MagicMock()
            mock_collection.count.return_value = 5000
            mock_coll.return_value = mock_collection
            mock_meta.return_value = {6669: {}, 6670: {}, 6671: {}}

            # Directly set the cached globals so health endpoint sees them
            old_coll = server_module._store
            old_meta = server_module._clip_metadata
            server_module._store = mock_collection
            server_module._clip_metadata = {6669: {}, 6670: {}, 6671: {}}
            try:
                from fastapi.testclient import TestClient
                client = TestClient(server_module.app)
                response = client.get("/api/health")
                data = response.json()
                assert data["status"] == "ok"
                assert data["chunks_indexed"] == 5000
                assert data["clips_indexed"] == 3
            finally:
                server_module._store = old_coll
                server_module._clip_metadata = old_meta


# ============================================================
# 3. Direct route tests (App Runner compatibility)
# ============================================================

class TestDirectRoutes:
    """Test the direct /ask and /health routes (without /api/ prefix)."""

    def test_direct_ask_route_returns_200(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "Direct route answer",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/ask", json={"question": "What about zoning?"})
            assert response.status_code == 200
            assert response.json()["answer"] == "Direct route answer"

    def test_direct_health_route_returns_200(self):
        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_direct_chat_route_returns_200(self):
        with patch("rag.server.chat") as mock_chat, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Direct route answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/chat", json={
                "messages": [{"role": "user", "content": "test"}],
                "model_provider": "openai",
            })
            assert response.status_code == 200
            assert response.json()["content"] == "Direct route answer"

    def test_direct_health_before_collection_loaded(self):
        """Health check before any request loads the collection should still return ok."""
        import rag.server as server_module

        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            # Reset the cached globals to simulate pre-load state
            old_coll = server_module._store
            old_meta = server_module._clip_metadata
            server_module._store = None
            server_module._clip_metadata = None
            try:
                from fastapi.testclient import TestClient
                client = TestClient(server_module.app)
                response = client.get("/health")
                data = response.json()
                assert data["status"] == "ok"
                # Should NOT have chunks_indexed since collection not loaded
                assert "chunks_indexed" not in data
            finally:
                server_module._store = old_coll
                server_module._clip_metadata = old_meta


# ============================================================
# 4. POST /api/chat tests
# ============================================================

class TestChatEndpoint:
    """Test the POST /api/chat endpoint."""

    def test_chat_returns_200_with_valid_messages(self):
        with patch("rag.server.chat") as mock_chat, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Test answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 5,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "test"}],
                "model_provider": "openai",
            })
            assert response.status_code == 200
            data = response.json()
            assert data["content"] == "Test answer"

    def test_chat_returns_422_with_empty_messages(self):
        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [],
            })
            assert response.status_code == 422

    def test_chat_returns_422_with_missing_messages(self):
        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={})
            assert response.status_code == 422

    def test_chat_returns_422_with_invalid_model_provider(self):
        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "test"}],
                "model_provider": "invalid",
            })
            assert response.status_code == 422

    def test_chat_returns_422_with_invalid_role(self):
        with patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [{"role": "system", "content": "test"}],
            })
            assert response.status_code == 422

    def test_chat_response_matches_schema(self):
        with patch("rag.server.chat") as mock_chat, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Zoning was discussed.",
                "sources": [
                    {
                        "clip_id": 6669,
                        "date": "2026-01-22",
                        "title": "Council Meeting",
                        "meeting_body": "Council",
                        "timestamp": 120,
                        "excerpt": "Zoning ordinance...",
                        "granicus_url": "https://lfucg.granicus.com/player/clip/6669?view_id=14&entrytime=120",
                    }
                ],
                "model_used": "gpt-4o",
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 5,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "What about zoning?"}],
            })
            data = response.json()
            assert "role" in data
            assert "content" in data
            assert "sources" in data
            assert "model_used" in data
            assert "filters_applied" in data
            assert "chunks_retrieved" in data

    def test_chat_with_filters(self):
        with patch("rag.server.chat") as mock_chat, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 0,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "test"}],
                "meeting_body": "Council",
                "date_after": "2025-01-01",
                "date_before": "2026-12-31",
            })

            call_kwargs = mock_chat.call_args.kwargs
            filters = call_kwargs.get("filters")
            assert filters["meeting_body"] == "Council"
            assert filters["date_after"] == "2025-01-01"
            assert filters["date_before"] == "2026-12-31"

    def test_direct_chat_route(self):
        with patch("rag.server.chat") as mock_chat, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Direct route answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/chat", json={
                "messages": [{"role": "user", "content": "test"}],
            })
            assert response.status_code == 200


# ============================================================
# N. POST /admin/reload tests
# ============================================================

class TestAdminReload:
    """The token-guarded cache-drop hook the ingest crons call after a
    re-ingest. Must clear ChromaDB's process-wide system cache, not just the
    in-process collection reference, or freshly re-ingested chunks stay
    invisible until a full restart (the Paris canonical_url regression)."""

    def test_reload_disabled_without_token_env(self, monkeypatch):
        monkeypatch.delenv("RELOAD_TOKEN", raising=False)
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/admin/reload")
        assert resp.status_code == 404

    def test_reload_rejects_bad_token(self, monkeypatch):
        monkeypatch.setenv("RELOAD_TOKEN", "secret")
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/admin/reload", headers={"X-Reload-Token": "wrong"})
        assert resp.status_code == 403

    def test_reload_clears_chroma_system_cache_and_caches(self, monkeypatch):
        monkeypatch.setenv("RELOAD_TOKEN", "secret")
        import rag.server as srv

        # Prime the in-process caches so we can prove they get dropped.
        srv._store = object()
        srv._clip_metadata = {"x": 1}

        with patch(
            "chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache"
        ) as mock_clear, patch("rag.search.close_connections") as mock_close:
            from fastapi.testclient import TestClient

            client = TestClient(srv.app)
            resp = client.post("/admin/reload", headers={"X-Reload-Token": "secret"})

        assert resp.status_code == 200
        assert resp.json() == {"reloaded": True}
        # ChromaDB's shared in-memory segment cache must be cleared so the next
        # PersistentClient re-reads re-ingested chunks from disk.
        mock_clear.assert_called_once()
        mock_close.assert_called_once()
        assert srv._store is None
        assert srv._clip_metadata is None


# ============================================================
# N+1. GET /admin/analytics tests
# ============================================================

class TestAdminAnalytics:
    """Token-guarded (same guard as /admin/reload) analytics summary from the
    telemetry SQLite sink."""

    def test_analytics_disabled_without_token(self, monkeypatch):
        monkeypatch.delenv("RELOAD_TOKEN", raising=False)
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/admin/analytics")
        assert resp.status_code == 404

    def test_analytics_rejects_bad_token(self, monkeypatch):
        monkeypatch.setenv("RELOAD_TOKEN", "secret")
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/admin/analytics", headers={"X-Reload-Token": "wrong"})
        assert resp.status_code == 403

    def test_analytics_returns_summary_with_token(self, monkeypatch):
        monkeypatch.setenv("RELOAD_TOKEN", "secret")
        canned = {
            "window_days": 7, "total": 5, "empty_result_rate": 0.0,
            "rate_limited_count": 0, "top_queries": [],
            "by_transport": {"http": 5}, "by_endpoint": [],
            "latency_ms": {"p50": 1.0, "p95": 2.0},
        }
        with patch("rag.telemetry.analytics", return_value=canned) as mock_an:
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            resp = client.get("/admin/analytics?days=7", headers={"X-Reload-Token": "secret"})

        assert resp.status_code == 200
        assert resp.json() == canned
        mock_an.assert_called_once()
        assert mock_an.call_args.kwargs.get("window_days") == 7


# ============================================================
# N+2. /api/facets in-process cache
# ============================================================

class TestFacetsCache:
    """Facets are cached in-process (they only change on a reindex) and dropped
    by /admin/reload alongside the other caches."""

    def test_facets_cached_second_call(self):
        import rag.server as srv

        srv._facets_cache = None
        with patch("rag.server.search_facets", return_value={"bodies": ["Council"]}) as mock_facets:
            from fastapi.testclient import TestClient

            client = TestClient(srv.app)
            r1 = client.get("/api/facets")
            r2 = client.get("/api/facets")

        assert r1.json() == {"bodies": ["Council"]}
        assert r2.json() == {"bodies": ["Council"]}
        assert mock_facets.call_count == 1  # second call served from cache
        srv._facets_cache = None

    def test_facets_cache_invalidated_by_reload(self, monkeypatch):
        monkeypatch.setenv("RELOAD_TOKEN", "secret")
        import rag.server as srv

        srv._facets_cache = None
        with patch("rag.server.search_facets", return_value={"bodies": ["Council"]}) as mock_facets, \
             patch("chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache"), \
             patch("rag.search.close_connections"):
            from fastapi.testclient import TestClient

            client = TestClient(srv.app)
            client.get("/api/facets")  # populates cache (compute #1)
            resp = client.post("/admin/reload", headers={"X-Reload-Token": "secret"})
            assert resp.status_code == 200
            client.get("/api/facets")  # cache dropped → recompute (#2)

        assert mock_facets.call_count == 2
        srv._facets_cache = None


class TestHealthSha:
    """/api/health exposes the running git SHA so a deploy can be confirmed."""

    def test_health_reports_git_sha(self):
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        data = client.get("/api/health").json()
        assert "sha" in data
        assert isinstance(data["sha"], str) and data["sha"]

    def test_health_reports_backend_dims_and_max_distance(self, monkeypatch):
        monkeypatch.setenv("VECTOR_BACKEND", "sqlite")
        monkeypatch.setenv("RAG_EMBED_DIMS", "512")
        monkeypatch.setenv("RAG_MAX_DISTANCE", "0.75")
        from rag.server import app
        from fastapi.testclient import TestClient

        data = TestClient(app).get("/api/health").json()
        assert data["backend"] == "sqlite"
        assert data["dims"] == 512
        assert data["max_distance"] == 0.75


class TestAskCache:
    """Identical question+filters are served from the in-process answer cache
    (dropped on /admin/reload), so the second call makes no OpenAI call."""

    def test_second_identical_ask_served_from_cache(self):
        from rag.server import app

        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "cached answer", "sources": [{"clip_id": 1}],
                "model_used": "gpt-4o", "filters_applied": {}, "chunks_retrieved": 1,
            }
            client = TestClient(app)
            r1 = client.post("/api/ask", json={"question": "  What About Zoning? "})
            # Different casing / whitespace normalizes to the same cache key.
            r2 = client.post("/api/ask", json={"question": "what about zoning?"})

        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["answer"] == "cached answer"
        assert r2.json()["answer"] == "cached answer"
        assert mock_ask.call_count == 1  # second request hit the cache


# ============================================================
# N+3. Date-filter validation (backends diverge on malformed dates)
# ============================================================


class TestDateFilterValidation:
    """Non-YYYY-MM-DD date filters 422 at the model boundary — the sqlite
    vector backend would silently no-op a malformed bound while chroma's
    string post-filter would drop everything."""

    def _client(self):
        from fastapi.testclient import TestClient

        from rag.server import app

        return TestClient(app)

    def test_ask_rejects_malformed_dates(self):
        client = self._client()
        for bad in ("July 2025", "2025", "01/15/2026", "2025-1-5"):
            resp = client.post("/api/ask", json={
                "question": "zoning?", "date_after": bad,
            })
            assert resp.status_code == 422, bad
        resp = client.post("/api/ask", json={
            "question": "zoning?", "date_before": "last year",
        })
        assert resp.status_code == 422

    def test_chat_rejects_malformed_dates(self):
        client = self._client()
        resp = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "zoning?"}],
            "date_after": "2025/01/01",
        })
        assert resp.status_code == 422

    def test_search_rejects_malformed_dates(self):
        client = self._client()
        resp = client.post("/api/search", json={
            "q": "zoning", "date_before": "notadate",
        })
        assert resp.status_code == 422

    def test_valid_dates_pass_validation(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "ok", "sources": [], "filters_applied": {},
                "chunks_retrieved": 0,
            }
            client = self._client()
            resp = client.post("/api/ask", json={
                "question": "zoning?",
                "date_after": "2025-01-01",
                "date_before": "2025-12-31",
            })
            assert resp.status_code == 200
            assert mock_ask.call_args.kwargs["filters"] == {
                "date_after": "2025-01-01", "date_before": "2025-12-31",
            }

    def test_empty_date_strings_normalize_to_no_filter(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "ok", "sources": [], "filters_applied": {},
                "chunks_retrieved": 0,
            }
            client = self._client()
            resp = client.post("/api/ask", json={
                "question": "zoning?", "date_after": "  ", "date_before": "",
            })
            assert resp.status_code == 200
            assert mock_ask.call_args.kwargs["filters"] is None


# ============================================================
# N+4. Client-IP resolution for rate limiting (spoof-proofing)
# ============================================================


class TestClientIpResolution:
    """Behind CloudFront → Caddy → uvicorn, the real client must be read from
    X-Forwarded-For's second-from-last entry (the two proxy hops are appended),
    and forwarded headers must only be trusted when the socket peer is our own
    proxy. CF-Connecting-IP is never consulted."""

    def _req(self, peer, headers):
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        scope = {"type": "http", "headers": raw}
        if peer is not None:
            scope["client"] = (peer, 12345)
        return StarletteRequest(scope)

    def test_legit_cloudfront_caddy_resolves_real_client(self):
        from rag.server import _client_ip_from_request

        # peer = Caddy on loopback; XFF = "<real client>, <cloudfront edge>".
        req = self._req("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 130.176.1.9"})
        assert _client_ip_from_request(req) == "203.0.113.7"

    def test_forged_cf_connecting_ip_is_ignored(self):
        from rag.server import _client_ip_from_request

        # CF-Connecting-IP is never read; the real client still comes from XFF.
        req = self._req("127.0.0.1", {
            "cf-connecting-ip": "9.9.9.9",
            "x-forwarded-for": "203.0.113.7, 130.176.1.9",
        })
        assert _client_ip_from_request(req) == "203.0.113.7"

    def test_forged_leftmost_xff_is_ignored(self):
        from rag.server import _client_ip_from_request

        # Attacker prepends a spoofed entry; CloudFront still appends the true
        # viewer IP and Caddy appends CloudFront's edge, so [-2] lands on the
        # real client, never the client-chosen leftmost.
        req = self._req("127.0.0.1", {
            "x-forwarded-for": "6.6.6.6, 203.0.113.7, 130.176.1.9",
        })
        assert _client_ip_from_request(req) == "203.0.113.7"
        assert _client_ip_from_request(req) != "6.6.6.6"

    def test_direct_to_origin_forged_header_falls_back_to_peer(self):
        from rag.server import _client_ip_from_request

        # Socket peer is a public IP (not our proxy) → forwarded headers are
        # untrusted; the peer IP is the client.
        req = self._req("8.8.8.8", {
            "cf-connecting-ip": "9.9.9.9",
            "x-forwarded-for": "203.0.113.7, 130.176.1.9",
        })
        assert _client_ip_from_request(req) == "8.8.8.8"


# ============================================================
# N+5. Global daily expensive-tier backstop (OpenAI spend ceiling)
# ============================================================


class TestDailyAskCap:
    """A process-wide UTC-daily ceiling on expensive-tier calls that trips
    independent of the per-IP limiter (protects total OpenAI spend)."""

    def test_daily_cap_returns_429_when_exceeded(self, monkeypatch):
        monkeypatch.setenv("RAG_DAILY_ASK_CAP", "1")
        from rag.server import app

        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.get_openai"):
            mock_ask.return_value = {
                "answer": "a", "sources": [], "filters_applied": {},
                "chunks_retrieved": 0,
            }
            client = TestClient(app)
            # Two DIFFERENT questions so the answer cache can't serve the 2nd
            # (a cache hit bypasses the cap by design — it makes no OpenAI call).
            r1 = client.post("/api/ask", json={"question": "first question on zoning"})
            r2 = client.post("/api/ask", json={"question": "second question on parks"})

        assert r1.status_code == 200
        assert r2.status_code == 429
        assert r2.json()["reason"] == "daily_ask_cap"
