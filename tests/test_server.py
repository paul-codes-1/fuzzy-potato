"""Tests for rag/server.py - FastAPI endpoints."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


# ============================================================
# 1. POST /api/ask tests
# ============================================================

class TestAskEndpoint:
    """Test the POST /api/ask endpoint."""

    def test_ask_returns_200_with_valid_question(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
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
        with patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.post("/api/ask", json={})
            assert response.status_code == 422

    def test_ask_response_matches_schema(self):
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
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
             patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
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
        with patch("rag.server.get_chroma_collection") as mock_coll, \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
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

        with patch("rag.server.get_chroma_collection") as mock_coll, \
             patch("rag.server.load_clip_metadata") as mock_meta, \
             patch("rag.server.OpenAI"):
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
        with patch("rag.server.ask") as mock_ask, \
             patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
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
        with patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_direct_health_before_collection_loaded(self):
        """Health check before any request loads the collection should still return ok."""
        import rag.server as server_module

        with patch("rag.server.get_chroma_collection"), \
             patch("rag.server.load_clip_metadata", return_value={}), \
             patch("rag.server.OpenAI"):
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
