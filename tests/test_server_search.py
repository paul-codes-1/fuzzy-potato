"""Endpoint tests for /api/search, /api/suggest, /api/facets, /api/related.

We patch the underlying rag.search and rag.related modules so the
server tests don't depend on a built search.db or chromadb. The
search/related modules themselves have integration tests in
test_search.py.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from rag.server import app


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Clear the in-process caches so a cached related/facets result from one
    test can't satisfy another test's differently-mocked call."""
    import rag.server as srv
    from rag.rate_limit import limiter

    def _clear():
        srv._ask_cache.clear()
        srv._related_cache.clear()
        srv._facets_cache = None
        limiter.reset()

    _clear()
    yield
    _clear()


# --- /api/search --------------------------------------------------------

class TestSearchEndpoint:
    def test_search_returns_results(self):
        with patch("rag.server.search_clips") as mock_search:
            mock_search.return_value = [
                {
                    "clip_id": 6669,
                    "date": "2026-01-22",
                    "meeting_body": "Council",
                    "title": "Urban County Council",
                    "speakers": ["Mayor Gorton"],
                    "transcript_words": 6800,
                    "transcript_source": "whisper-1+vtt-speakers",
                    "snippet": "<mark>budget</mark> amendments",
                    "score": -1.5,
                },
            ]
            client = TestClient(app)
            resp = client.post("/api/search", json={"q": "budget"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["results"][0]["clip_id"] == 6669
            assert "<mark>" in data["results"][0]["snippet"]

    def test_search_passes_filters_through(self):
        with patch("rag.server.search_clips") as mock_search:
            mock_search.return_value = []
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "q": "rezoning",
                "meeting_body": "Council",
                "speaker": "Mayor Gorton",
                "date_after": "2024-01-01",
                "date_before": "2025-12-31",
                "limit": 25,
            })
            assert resp.status_code == 200
            kwargs = mock_search.call_args.kwargs
            assert kwargs["meeting_body"] == "Council"
            assert kwargs["speaker"] == "Mayor Gorton"
            assert kwargs["date_after"] == "2024-01-01"
            assert kwargs["date_before"] == "2025-12-31"
            assert kwargs["limit"] == 25

    def test_search_rejects_empty_query(self):
        client = TestClient(app)
        resp = client.post("/api/search", json={"q": "   "})
        assert resp.status_code == 422

    def test_search_clamps_limit_to_max(self):
        with patch("rag.server.search_clips") as mock_search:
            mock_search.return_value = []
            client = TestClient(app)
            resp = client.post("/api/search", json={"q": "anything", "limit": 9999})
            assert resp.status_code == 200
            assert mock_search.call_args.kwargs["limit"] <= 100

    def test_search_returns_500_on_internal_error(self):
        with patch("rag.server.search_clips", side_effect=RuntimeError("boom")):
            client = TestClient(app)
            resp = client.post("/api/search", json={"q": "anything"})
            assert resp.status_code == 500

    def test_direct_search_path_works(self):
        """App Runner uses the unprefixed /search path."""
        with patch("rag.server.search_clips") as mock_search:
            mock_search.return_value = []
            client = TestClient(app)
            resp = client.post("/search", json={"q": "anything"})
            assert resp.status_code == 200


# --- /api/suggest -------------------------------------------------------

class TestSuggestEndpoint:
    def test_suggest_returns_results(self):
        with patch("rag.server.search_suggest") as mock_suggest:
            mock_suggest.return_value = [
                {"term": "Planning Commission", "kind": "title", "weight": 50},
            ]
            client = TestClient(app)
            resp = client.get("/api/suggest", params={"q": "Plan"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"][0]["term"] == "Planning Commission"

    def test_suggest_empty_q_returns_empty(self):
        client = TestClient(app)
        resp = client.get("/api/suggest", params={"q": ""})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_suggest_clamps_limit(self):
        with patch("rag.server.search_suggest") as mock_suggest:
            mock_suggest.return_value = []
            client = TestClient(app)
            resp = client.get("/api/suggest", params={"q": "Plan", "limit": 9999})
            assert resp.status_code == 200
            assert mock_suggest.call_args.kwargs["limit"] <= 25


# --- /api/facets --------------------------------------------------------

class TestFacetsEndpoint:
    def test_facets_returns_payload(self):
        with patch("rag.server.search_facets") as mock_facets:
            mock_facets.return_value = {
                "bodies": ["Council", "Commission"],
                "speakers": [{"name": "Mayor Gorton", "count": 12}],
                "date_min": "2007-08-14",
                "date_max": "2026-04-30",
            }
            client = TestClient(app)
            resp = client.get("/api/facets")
            assert resp.status_code == 200
            data = resp.json()
            assert "Council" in data["bodies"]
            assert data["speakers"][0]["name"] == "Mayor Gorton"


# --- /api/related/{clip_id} --------------------------------------------

class TestRelatedEndpoint:
    def test_related_returns_results(self):
        with patch("rag.server.related_clips") as mock_related, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}):
            mock_related.return_value = [
                {
                    "clip_id": 5500,
                    "title": "Council Work Session",
                    "date": "2025-09-12",
                    "meeting_body": "Council",
                    "similarity": 0.87,
                },
            ]
            client = TestClient(app)
            resp = client.get("/api/related/6669")
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"][0]["clip_id"] == 5500
            assert data["results"][0]["similarity"] > 0

    def test_related_clamps_limit(self):
        with patch("rag.server.related_clips") as mock_related, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}):
            mock_related.return_value = []
            client = TestClient(app)
            resp = client.get("/api/related/6669", params={"limit": 999})
            assert resp.status_code == 200
            assert mock_related.call_args.kwargs["limit"] <= 20

    def test_related_is_cached_by_clip_and_limit(self):
        """Second identical /api/related call is served from the LRU (no
        second related_clips() call)."""
        with patch("rag.server.related_clips") as mock_related, \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}):
            mock_related.return_value = [{
                "clip_id": 5500, "title": "X", "date": "2025-01-01",
                "meeting_body": "Council", "similarity": 0.9,
            }]
            client = TestClient(app)
            r1 = client.get("/api/related/6669")
            r2 = client.get("/api/related/6669")
        assert r1.json() == r2.json()
        assert mock_related.call_count == 1

    def test_related_sets_cache_control_header(self):
        with patch("rag.server.related_clips", return_value=[]), \
             patch("rag.server.get_vecstore"), \
             patch("rag.server.load_clip_metadata", return_value={}):
            resp = TestClient(app).get("/api/related/6669")
        assert resp.headers.get("cache-control") == "public, max-age=3600"


class TestCacheControlHeaders:
    def test_facets_sets_cache_control_header(self):
        with patch("rag.server.search_facets", return_value={"bodies": []}):
            resp = TestClient(app).get("/api/facets")
        assert resp.headers.get("cache-control") == "public, max-age=3600"
