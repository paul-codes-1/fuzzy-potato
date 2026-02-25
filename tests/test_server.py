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
        with patch("rag.server.get_chroma_collection") as mock_coll, \
             patch("rag.server.load_clip_metadata") as mock_meta, \
             patch("rag.server.OpenAI"):
            mock_collection = MagicMock()
            mock_collection.count.return_value = 5000
            mock_coll.return_value = mock_collection
            mock_meta.return_value = {6669: {}, 6670: {}, 6671: {}}

            from rag.server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/api/health")
            data = response.json()
            assert data["status"] == "ok"
            assert data["chunks_indexed"] == 5000
            assert data["clips_indexed"] == 3
