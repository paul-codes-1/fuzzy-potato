"""Tests for rag/mcp_server.py — MCP tool implementations.

The HTTP transport itself is exercised end-to-end via test_server_mcp_mount.py;
these tests cover the tool *implementations* directly so we can assert
filtering/decoration/error-handling without spinning up the MCP session
manager.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from rag import mcp_server as mcp_module


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    """Each test starts with cold module-level caches and no inherited
    RAG_SUSPENDED (the prod box sets it in .env — tests that need it set
    it explicitly)."""
    monkeypatch.delenv("RAG_SUSPENDED", raising=False)
    mcp_module._store = None
    mcp_module._clip_metadata = None
    yield
    mcp_module._store = None
    mcp_module._clip_metadata = None


# ============================================================
# build_mcp_server registration
# ============================================================


class TestBuildMcpServer:
    def test_registers_all_five_tools(self):
        server = mcp_module.build_mcp_server()
        names = {t.name for t in server._tool_manager.list_tools()}
        assert names == {
            "ask_meetings",
            "search_meetings",
            "find_related_clips",
            "get_meeting_clip",
            "list_recent_meetings",
        }

    def test_server_is_stateless_http(self):
        server = mcp_module.build_mcp_server()
        # FastMCP exposes settings via .settings; stateless_http should be True
        # so each POST gets a fresh transport (matches feeds and the docs).
        assert server.settings.stateless_http is True

    def test_module_level_singleton_exists(self):
        # rag/server.py mounts mcp_module.mcp_server, so the singleton must
        # exist at import time and be a FastMCP instance.
        from mcp.server.fastmcp import FastMCP

        assert isinstance(mcp_module.mcp_server, FastMCP)


# ============================================================
# ask_meetings_impl
# ============================================================


class TestAskMeetings:
    def test_empty_question_returns_error(self):
        result = mcp_module.ask_meetings_impl(question="")
        assert result == {"error": "question must not be empty"}

    def test_whitespace_question_returns_error(self):
        result = mcp_module.ask_meetings_impl(question="   ")
        assert result == {"error": "question must not be empty"}

    def test_too_long_question_returns_error(self):
        result = mcp_module.ask_meetings_impl(question="x" * 2001)
        assert result["error"].startswith("question must be under 2000")

    def test_filters_passed_through(self):
        with patch.object(mcp_module, "ask") as mock_ask, \
             patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "get_openai", return_value=MagicMock()):
            mock_ask.return_value = {
                "answer": "ok", "sources": [], "filters_applied": {}, "chunks_retrieved": 0,
            }
            mcp_module.ask_meetings_impl(
                question="hi",
                meeting_body="Council",
                date_after="2025-01-01",
                date_before="2025-12-31",
            )
            kwargs = mock_ask.call_args.kwargs
            assert kwargs["filters"] == {
                "meeting_body": "Council",
                "date_after": "2025-01-01",
                "date_before": "2025-12-31",
            }

    def test_no_filters_passes_none(self):
        with patch.object(mcp_module, "ask") as mock_ask, \
             patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "get_openai", return_value=MagicMock()):
            mock_ask.return_value = {
                "answer": "", "sources": [], "filters_applied": {}, "chunks_retrieved": 0,
            }
            mcp_module.ask_meetings_impl(question="hi")
            assert mock_ask.call_args.kwargs["filters"] is None

    def test_underlying_exception_caught(self):
        with patch.object(mcp_module, "ask", side_effect=RuntimeError("boom")), \
             patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "get_openai", return_value=MagicMock()):
            result = mcp_module.ask_meetings_impl(question="hi")
            assert "error" in result
            # Raw exception text must NOT leak to anonymous public clients.
            assert "boom" not in result["error"]

    def test_response_shape(self):
        with patch.object(mcp_module, "ask") as mock_ask, \
             patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "get_openai", return_value=MagicMock()):
            mock_ask.return_value = {
                "answer": "Yes, the council voted 8-0.",
                "sources": [{"clip_id": 6669, "title": "Council Meeting"}],
                "filters_applied": {"meeting_body": "Council"},
                "chunks_retrieved": 4,
            }
            result = mcp_module.ask_meetings_impl(question="vote?", meeting_body="Council")
            assert result["question"] == "vote?"
            assert result["answer"] == "Yes, the council voted 8-0."
            assert result["sources"] == [{"clip_id": 6669, "title": "Council Meeting"}]
            assert result["filters_applied"] == {"meeting_body": "Council"}
            assert result["chunks_retrieved"] == 4


# ============================================================
# search_meetings_impl
# ============================================================


class TestSearchMeetings:
    def test_empty_q_returns_error(self):
        result = mcp_module.search_meetings_impl(q="")
        assert result == {"error": "q must not be empty"}

    def test_too_long_q_returns_error(self):
        result = mcp_module.search_meetings_impl(q="x" * 201)
        assert result["error"].startswith("q must be under 200")

    def test_limit_clamped_high(self):
        with patch.object(mcp_module, "search_clips", return_value=[]) as mock_search:
            mcp_module.search_meetings_impl(q="zoning", limit=999)
            assert mock_search.call_args.kwargs["limit"] == 100

    def test_limit_zero_falls_to_default(self):
        # 0 is treated as "missing" (Python falsiness): default 25 applies.
        with patch.object(mcp_module, "search_clips", return_value=[]) as mock_search:
            mcp_module.search_meetings_impl(q="zoning", limit=0)
            assert mock_search.call_args.kwargs["limit"] == 25

    def test_limit_negative_clamped_to_one(self):
        with patch.object(mcp_module, "search_clips", return_value=[]) as mock_search:
            mcp_module.search_meetings_impl(q="zoning", limit=-5)
            assert mock_search.call_args.kwargs["limit"] == 1

    def test_decorates_results_with_urls(self):
        with patch.object(mcp_module, "search_clips") as mock_search:
            mock_search.return_value = [
                {"clip_id": 6669, "title": "Hi", "snippet": "<mark>zoning</mark>"},
                {"clip_id": 7000, "title": "Bye", "snippet": "..."},
            ]
            result = mcp_module.search_meetings_impl(q="zoning")
            assert result["count"] == 2
            assert result["results"][0]["url"].endswith("/meeting/6669")
            assert result["results"][0]["markdown_url"].endswith("/data/clips/6669/clip.md")
            # snippet HTML preserved verbatim (no escape, no strip)
            assert result["results"][0]["snippet"] == "<mark>zoning</mark>"

    def test_filters_passed_through(self):
        with patch.object(mcp_module, "search_clips", return_value=[]) as mock_search:
            mcp_module.search_meetings_impl(
                q="budget",
                meeting_body="Council",
                speaker="Mayor Gorton",
                date_after="2025-01-01",
                date_before="2025-12-31",
                limit=10,
            )
            kwargs = mock_search.call_args.kwargs
            assert kwargs["meeting_body"] == "Council"
            assert kwargs["speaker"] == "Mayor Gorton"
            assert kwargs["date_after"] == "2025-01-01"
            assert kwargs["date_before"] == "2025-12-31"

    def test_underlying_exception_caught(self):
        with patch.object(mcp_module, "search_clips", side_effect=RuntimeError("boom")):
            result = mcp_module.search_meetings_impl(q="zoning")
            assert "error" in result


# ============================================================
# find_related_clips_impl
# ============================================================


class TestFindRelatedClips:
    def test_decorates_with_urls(self):
        with patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "related_clips") as mock_related:
            mock_related.return_value = [
                {"clip_id": 7000, "title": "Related", "similarity": 0.91},
            ]
            result = mcp_module.find_related_clips_impl(clip_id=6669, limit=5)
            assert result["clip_id"] == 6669
            assert result["count"] == 1
            assert result["results"][0]["url"].endswith("/meeting/7000")
            assert result["results"][0]["markdown_url"].endswith("/data/clips/7000/clip.md")

    def test_limit_clamped_high(self):
        with patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "related_clips", return_value=[]) as mock_related:
            mcp_module.find_related_clips_impl(clip_id=6669, limit=999)
            assert mock_related.call_args.kwargs["limit"] == 20

    def test_underlying_exception_caught(self):
        with patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "related_clips", side_effect=RuntimeError("boom")):
            result = mcp_module.find_related_clips_impl(clip_id=6669)
            assert "error" in result


# ============================================================
# get_meeting_clip_impl
# ============================================================


class TestGetMeetingClip:
    def test_unknown_clip_returns_error(self):
        with patch.object(mcp_module, "_get_clip_metadata", return_value={}):
            result = mcp_module.get_meeting_clip_impl(clip_id=99999)
            assert result == {"error": "No clip found for id 99999"}

    def test_returns_full_metadata(self, tmp_path):
        # Use a tmp output dir + write a fake summary so we exercise the
        # "summary on disk" branch without touching the real archive.
        clip_dir = tmp_path / "clips" / "6669"
        clip_dir.mkdir(parents=True)
        (clip_dir / "summary.txt").write_text("## Section 1\nThe council voted.")
        with patch.object(mcp_module, "OUTPUT_DIR", str(tmp_path)), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={
                 "6669": {
                     "title": "Council Meeting",
                     "date": "2026-01-22",
                     "meeting_body": "Council",
                     "speakers": ["Mayor Gorton"],
                     "topics": ["budget"],
                     "transcript_words": 5000,
                     "transcript_source": "whisper-1",
                 }
             }):
            result = mcp_module.get_meeting_clip_impl(clip_id=6669)
            assert result["clip_id"] == 6669
            assert result["title"] == "Council Meeting"
            assert result["date"] == "2026-01-22"
            assert result["meeting_body"] == "Council"
            assert result["speakers"] == ["Mayor Gorton"]
            assert result["topics"] == ["budget"]
            assert result["transcript_words"] == 5000
            assert result["transcript_source"] == "whisper-1"
            assert result["summary"] == "## Section 1\nThe council voted."
            assert result["granicus_url"]  # whatever shape the helper returns
            assert result["url"].endswith("/meeting/6669")
            assert result["markdown_url"].endswith("/data/clips/6669/clip.md")

    def test_missing_summary_file_is_tolerated(self, tmp_path):
        with patch.object(mcp_module, "OUTPUT_DIR", str(tmp_path)), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={
                 "6669": {"title": "x"},
             }):
            result = mcp_module.get_meeting_clip_impl(clip_id=6669)
            assert result["summary"] is None
            assert result["title"] == "x"


# ============================================================
# list_recent_meetings_impl
# ============================================================


class TestListRecentMeetings:
    def _meta(self):
        return {
            "100": {"title": "Old", "date": "2024-01-01", "meeting_body": "Council"},
            "200": {"title": "Mid", "date": "2025-06-01", "meeting_body": "Planning"},
            "300": {"title": "New", "date": "2026-03-01", "meeting_body": "Council"},
            "350": {"title": "Newer same-day", "date": "2026-03-01", "meeting_body": "Council"},
            "400": {"title": "Newest", "date": "2026-04-01", "meeting_body": "WQFB"},
        }

    def test_orders_by_date_desc_then_clip_id_desc(self):
        with patch.object(mcp_module, "_get_clip_metadata", return_value=self._meta()):
            result = mcp_module.list_recent_meetings_impl(limit=10)
            ids = [r["clip_id"] for r in result["results"]]
            # Newest date first; same-date ties broken by clip_id desc.
            assert ids == [400, 350, 300, 200, 100]

    def test_filter_by_body(self):
        with patch.object(mcp_module, "_get_clip_metadata", return_value=self._meta()):
            result = mcp_module.list_recent_meetings_impl(meeting_body="Council")
            ids = [r["clip_id"] for r in result["results"]]
            assert ids == [350, 300, 100]
            assert result["meeting_body"] == "Council"

    def test_limit_caps_results(self):
        with patch.object(mcp_module, "_get_clip_metadata", return_value=self._meta()):
            result = mcp_module.list_recent_meetings_impl(limit=2)
            assert result["count"] == 2
            assert [r["clip_id"] for r in result["results"]] == [400, 350]

    def test_limit_clamped_high(self):
        with patch.object(mcp_module, "_get_clip_metadata", return_value=self._meta()):
            # 999 should clamp to 100; we only have 5 entries so all return.
            result = mcp_module.list_recent_meetings_impl(limit=999)
            assert result["count"] == 5

    def test_decorates_with_urls(self):
        with patch.object(mcp_module, "_get_clip_metadata", return_value=self._meta()):
            result = mcp_module.list_recent_meetings_impl(limit=1)
            assert result["results"][0]["url"].endswith("/meeting/400")
            assert result["results"][0]["markdown_url"].endswith("/data/clips/400/clip.md")

    def test_skips_non_int_keys(self):
        bad = {**self._meta(), "not-a-number": {"title": "junk", "date": "2026-12-31"}}
        with patch.object(mcp_module, "_get_clip_metadata", return_value=bad):
            result = mcp_module.list_recent_meetings_impl(limit=10)
            assert all(isinstance(r["clip_id"], int) for r in result["results"])
            assert result["count"] == 5  # the non-int key dropped


# ============================================================
# URL helpers
# ============================================================


class TestUrlHelpers:
    def test_meeting_url_format(self):
        assert mcp_module._meeting_url(6669).endswith("/meeting/6669")

    def test_clip_md_url_format(self):
        assert mcp_module._clip_md_url(6669).endswith("/data/clips/6669/clip.md")

    def test_site_url_strips_trailing_slash(self):
        # Module-level — already stripped at import time. Just verify shape.
        assert not mcp_module.SITE_URL.endswith("/")


# ============================================================
# RAG_SUSPENDED guard (570f843) — must short-circuit BEFORE the store
# ============================================================


class TestRagSuspendedGuard:
    """With RAG_SUSPENDED set, the two vector-backed tools return the
    maintenance notice WITHOUT constructing the vector store — the whole
    point of the flag is serving the MCP endpoint while the store is
    offline for the rebuild."""

    def _forbid_store(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("store factory must not be called while suspended")

        monkeypatch.setattr(mcp_module, "get_vecstore", _boom)

    def test_ask_meetings_returns_notice_without_store(self, monkeypatch):
        monkeypatch.setenv("RAG_SUSPENDED", "1")
        self._forbid_store(monkeypatch)
        result = mcp_module.ask_meetings_impl(question="What about zoning?")
        assert result["error"] == "temporarily_offline"
        assert "search_meetings" in result["message"]

    def test_find_related_clips_returns_notice_without_store(self, monkeypatch):
        monkeypatch.setenv("RAG_SUSPENDED", "1")
        self._forbid_store(monkeypatch)
        result = mcp_module.find_related_clips_impl(6669)
        assert result["error"] == "temporarily_offline"

    def test_flag_unset_does_not_suspend(self, monkeypatch):
        monkeypatch.delenv("RAG_SUSPENDED", raising=False)
        assert mcp_module._rag_suspended_notice() is None

    def test_flag_zero_does_not_suspend(self, monkeypatch):
        monkeypatch.setenv("RAG_SUSPENDED", "0")
        assert mcp_module._rag_suspended_notice() is None


# ============================================================
# Date-filter validation (backends diverge on malformed dates)
# ============================================================


class TestDateFilterValidation:
    """Non-YYYY-MM-DD date filters must be rejected at the tool boundary —
    the sqlite backend would silently no-op the filter while chroma would
    return nothing."""

    def test_ask_meetings_rejects_bad_date_after(self, monkeypatch):
        monkeypatch.delenv("RAG_SUSPENDED", raising=False)
        result = mcp_module.ask_meetings_impl(
            question="zoning?", date_after="July 2025")
        assert result == {"error": "date_after must be YYYY-MM-DD"}

    def test_ask_meetings_rejects_bad_date_before(self, monkeypatch):
        monkeypatch.delenv("RAG_SUSPENDED", raising=False)
        result = mcp_module.ask_meetings_impl(
            question="zoning?", date_before="01/15/2026")
        assert result == {"error": "date_before must be YYYY-MM-DD"}

    def test_search_meetings_rejects_bad_dates(self):
        with patch.object(mcp_module, "search_clips") as mock_search:
            result = mcp_module.search_meetings_impl(q="zoning", date_after="2025")
            assert result == {"error": "date_after must be YYYY-MM-DD"}
            result = mcp_module.search_meetings_impl(q="zoning", date_before="last year")
            assert result == {"error": "date_before must be YYYY-MM-DD"}
            mock_search.assert_not_called()

    def test_valid_dates_still_accepted(self, monkeypatch):
        monkeypatch.delenv("RAG_SUSPENDED", raising=False)
        with patch.object(mcp_module, "ask") as mock_ask, \
             patch.object(mcp_module, "_get_store", return_value=MagicMock()), \
             patch.object(mcp_module, "_get_clip_metadata", return_value={}), \
             patch.object(mcp_module, "get_openai", return_value=MagicMock()):
            mock_ask.return_value = {
                "answer": "ok", "sources": [], "filters_applied": {}, "chunks_retrieved": 0,
            }
            result = mcp_module.ask_meetings_impl(
                question="hi", date_after="2025-01-01", date_before="2025-12-31")
            assert "error" not in result
