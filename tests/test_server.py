"""Tests for api/server.py - FastAPI endpoints."""

from unittest.mock import MagicMock, patch



# ============================================================
# 1. POST /api/ask tests
# ============================================================

class TestAskEndpoint:
    """Test the POST /api/ask endpoint."""

    def test_ask_returns_200_with_valid_question(self):
        with patch("api.server.ask") as mock_ask, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_ask.return_value = {
                "answer": "Test answer",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/ask", json={"question": "What about zoning?"})
            assert response.status_code == 200
            data = response.json()
            assert "answer" in data

    def test_ask_returns_422_with_missing_question(self):
        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/ask", json={})
            assert response.status_code == 422

    def test_ask_response_matches_schema(self):
        with patch("api.server.ask") as mock_ask, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
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
                        "granicus_url": "https://example.granicus.com/player/clip/6669?view_id=14&entrytime=120",
                    }
                ],
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 5,
            }

            from api.server import app
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
        with patch("api.server.ask") as mock_ask, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_ask.return_value = {
                "answer": "Answer",
                "sources": [],
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 0,
            }

            from api.server import app
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

    def test_ask_threads_tenant_and_request_id(self):
        with patch("api.server.ask") as mock_ask, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_ask.return_value = {
                "answer": "Answer",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            client.post("/api/ask", json={"question": "test"})

            call_kwargs = mock_ask.call_args.kwargs
            assert call_kwargs["tenant_id"] == "dev"
            assert call_kwargs["request_id"]


# ============================================================
# 2. GET /api/health tests
# ============================================================

class TestHealthEndpoint:
    """Test the GET /api/health endpoint."""

    def test_health_returns_200(self):
        with patch("api.server.get_chroma_collection") as mock_coll, \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_collection = MagicMock()
            mock_collection.count.return_value = 1000
            mock_coll.return_value = mock_collection

            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/api/health")
            assert response.status_code == 200

    def test_health_returns_chunk_and_clip_counts(self):
        import api.server as server_module

        with patch("api.server.get_chroma_collection") as mock_coll, \
             patch("api.server.load_clip_metadata") as mock_meta, \
             patch("api.server.OpenAI"):
            mock_collection = MagicMock()
            mock_collection.count.return_value = 5000
            mock_coll.return_value = mock_collection
            mock_meta.return_value = {6669: {}, 6670: {}, 6671: {}}

            # Directly set the cached globals so health endpoint sees them
            old_coll = server_module._collection
            old_meta = server_module._clip_metadata
            server_module._collection = mock_collection
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
                server_module._collection = old_coll
                server_module._clip_metadata = old_meta


# ============================================================
# 3. Direct route tests (App Runner compatibility)
# ============================================================

class TestDirectRoutes:
    """Test the direct /ask and /health routes (without /api/ prefix)."""

    def test_direct_ask_route_returns_200(self):
        with patch("api.server.ask") as mock_ask, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_ask.return_value = {
                "answer": "Direct route answer",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/ask", json={"question": "What about zoning?"})
            assert response.status_code == 200
            assert response.json()["answer"] == "Direct route answer"

    def test_direct_health_route_returns_200(self):
        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_direct_chat_route_returns_200(self):
        with patch("api.server.chat") as mock_chat, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Direct route answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from api.server import app
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
        import api.server as server_module

        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            # Reset the cached globals to simulate pre-load state
            old_coll = server_module._collection
            old_meta = server_module._clip_metadata
            server_module._collection = None
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
                server_module._collection = old_coll
                server_module._clip_metadata = old_meta


# ============================================================
# 4. POST /api/chat tests
# ============================================================

class TestChatEndpoint:
    """Test the POST /api/chat endpoint."""

    def test_chat_returns_200_with_valid_messages(self):
        with patch("api.server.chat") as mock_chat, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Test answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 5,
            }

            from api.server import app
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
        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [],
            })
            assert response.status_code == 422

    def test_chat_returns_422_with_missing_messages(self):
        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={})
            assert response.status_code == 422

    def test_chat_returns_422_with_invalid_model_provider(self):
        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "test"}],
                "model_provider": "invalid",
            })
            assert response.status_code == 422

    def test_chat_returns_422_with_invalid_role(self):
        with patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/chat", json={
                "messages": [{"role": "system", "content": "test"}],
            })
            assert response.status_code == 422

    def test_chat_response_matches_schema(self):
        with patch("api.server.chat") as mock_chat, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
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
                        "granicus_url": "https://example.granicus.com/player/clip/6669?view_id=14&entrytime=120",
                    }
                ],
                "model_used": "gpt-4o",
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 5,
            }

            from api.server import app
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
        with patch("api.server.chat") as mock_chat, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 0,
            }

            from api.server import app
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

    def test_chat_threads_tenant_and_request_id(self):
        with patch("api.server.chat") as mock_chat, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "test"}],
                "model_provider": "openai",
            })

            call_kwargs = mock_chat.call_args.kwargs
            assert call_kwargs["tenant_id"] == "dev"
            assert call_kwargs["request_id"]

    def test_direct_chat_route(self):
        with patch("api.server.chat") as mock_chat, \
             patch("api.server.get_chroma_collection"), \
             patch("api.server.load_clip_metadata", return_value={}), \
             patch("api.server.OpenAI"):
            mock_chat.return_value = {
                "role": "assistant",
                "content": "Direct route answer",
                "sources": [],
                "model_used": "gpt-4o",
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            from api.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/chat", json={
                "messages": [{"role": "user", "content": "test"}],
            })
            assert response.status_code == 200
