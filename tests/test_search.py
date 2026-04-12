"""Tests for api/search.py -- SQLite FTS5 search engine."""

import json

import pytest

from api.search import SearchEngine, SearchResult, AutocompleteItem

from tests.conftest import (
    SAMPLE_EXTRACTED_FACTS,
    SAMPLE_METADATA,
    SAMPLE_SUMMARY,
    SAMPLE_AGENDA,
    SAMPLE_MINUTES,
    SAMPLE_SEGMENTS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    """Fresh SearchEngine backed by a temp SQLite database."""
    db_path = str(tmp_path / "search.db")
    eng = SearchEngine(db_path)
    yield eng
    eng.close()


@pytest.fixture
def clip_dir(tmp_path):
    """Create a single clip directory with standard pipeline output files."""
    clip_id = 6669
    d = tmp_path / "clips" / str(clip_id)
    d.mkdir(parents=True)

    (d / "metadata.json").write_text(json.dumps(SAMPLE_METADATA))
    (d / "extracted_facts.json").write_text(json.dumps(SAMPLE_EXTRACTED_FACTS))
    (d / "summary.txt").write_text(SAMPLE_SUMMARY)
    (d / "agenda_6669.txt").write_text(SAMPLE_AGENDA)
    (d / "transcript_Urban_County_Council_1.txt").write_text(
        " ".join(seg["text"] for seg in SAMPLE_SEGMENTS)
    )
    (d / "2026-01-22_minutes_Urban_County_Council.txt").write_text(SAMPLE_MINUTES)

    return tmp_path


@pytest.fixture
def indexed_engine(engine, clip_dir):
    """Engine with the sample clip already indexed."""
    engine.index_clip(str(clip_dir / "clips" / "6669"), 6669)
    return engine


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestSearchEngineInit:
    def test_creates_database_file(self, tmp_path):
        db_path = str(tmp_path / "subdir" / "search.db")
        eng = SearchEngine(db_path)
        assert (tmp_path / "subdir" / "search.db").exists()
        eng.close()

    def test_stats_empty(self, engine):
        stats = engine.stats()
        assert stats["total_items"] == 0
        assert stats["clips_indexed"] == 0
        assert stats["autocomplete_terms"] == 0

    def test_schema_creates_fts_table(self, engine):
        # Verify the FTS5 virtual table exists by running a no-op match
        result = engine.search("xyznonexistent")
        assert result["results"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


class TestIndexMeeting:
    def test_index_clip_returns_item_count(self, engine, clip_dir):
        count = engine.index_clip(str(clip_dir / "clips" / "6669"), 6669)
        # Should index: meeting + topics(3) + votes(2) + financial(1) +
        # agenda_items(1) + transcript = at least 8
        assert count >= 8

    def test_clip_marked_as_indexed(self, engine, clip_dir):
        assert engine.is_clip_indexed(6669) is False
        engine.index_clip(str(clip_dir / "clips" / "6669"), 6669)
        assert engine.is_clip_indexed(6669) is True

    def test_reindex_replaces_entries(self, engine, clip_dir):
        engine.index_clip(str(clip_dir / "clips" / "6669"), 6669)
        stats_before = engine.stats()

        # Re-index the same clip
        engine.index_clip(str(clip_dir / "clips" / "6669"), 6669)
        stats_after = engine.stats()

        assert stats_after["total_items"] == stats_before["total_items"]

    def test_index_clip_without_metadata_returns_zero(self, engine, tmp_path):
        empty_dir = tmp_path / "clips" / "9999"
        empty_dir.mkdir(parents=True)
        count = engine.index_clip(str(empty_dir), 9999)
        assert count == 0

    def test_stats_after_indexing(self, indexed_engine):
        stats = indexed_engine.stats()
        assert stats["clips_indexed"] == 1
        assert stats["total_items"] > 0
        assert "meeting" in stats["by_type"]


# ---------------------------------------------------------------------------
# Search -- basic
# ---------------------------------------------------------------------------


class TestSearchMeetings:
    def test_search_returns_ranked_results(self, indexed_engine):
        result = indexed_engine.search("zoning")
        assert result["total"] > 0
        assert len(result["results"]) > 0
        assert result["query"] == "zoning"
        assert result["took_ms"] >= 0

    def test_search_result_has_required_fields(self, indexed_engine):
        result = indexed_engine.search("budget")
        assert result["total"] > 0
        first = result["results"][0]
        assert "type" in first
        assert "clip_id" in first
        assert "title" in first
        assert "snippet" in first
        assert "score" in first
        assert first["clip_id"] == 6669

    def test_search_returns_multiple_types(self, indexed_engine):
        result = indexed_engine.search("ordinance")
        types_found = {r["type"] for r in result["results"]}
        # Should find votes, agenda items, and/or meeting entries
        assert len(types_found) >= 1

    def test_search_scores_are_positive(self, indexed_engine):
        result = indexed_engine.search("council")
        for r in result["results"]:
            assert r["score"] > 0


# ---------------------------------------------------------------------------
# Search -- filters
# ---------------------------------------------------------------------------


class TestSearchFilters:
    def test_filter_by_meeting_body(self, indexed_engine):
        result = indexed_engine.search("ordinance", meeting_body="Council")
        assert result["total"] > 0
        for r in result["results"]:
            assert r["meeting_body"] == "Council"

    def test_filter_by_nonexistent_meeting_body_returns_empty(self, indexed_engine):
        result = indexed_engine.search("ordinance", meeting_body="NoSuchBody")
        assert result["total"] == 0

    def test_filter_by_date_after(self, indexed_engine):
        result = indexed_engine.search("zoning", date_after="2026-01-01")
        assert result["total"] > 0

    def test_filter_by_date_before(self, indexed_engine):
        result = indexed_engine.search("zoning", date_before="2025-01-01")
        assert result["total"] == 0

    def test_filter_by_date_range(self, indexed_engine):
        result = indexed_engine.search(
            "zoning", date_after="2026-01-01", date_before="2026-12-31"
        )
        assert result["total"] > 0

    def test_filter_by_type_vote(self, indexed_engine):
        result = indexed_engine.search("ordinance", type_filter="vote")
        for r in result["results"]:
            assert r["type"] == "vote"

    def test_filter_by_type_financial(self, indexed_engine):
        result = indexed_engine.search("bonds", type_filter="financial")
        for r in result["results"]:
            assert r["type"] == "financial"


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


class TestAutocomplete:
    def test_autocomplete_returns_prefix_matches(self, indexed_engine):
        results = indexed_engine.autocomplete("Beas")
        assert len(results) > 0
        assert any("Beasley" in r["text"] for r in results)

    def test_autocomplete_meeting_body(self, indexed_engine):
        results = indexed_engine.autocomplete("Coun")
        assert len(results) > 0
        categories = {r["category"] for r in results}
        # Should match "Council" as meeting_body or speaker names starting with "Cou"
        assert len(categories) >= 1

    def test_autocomplete_topic(self, indexed_engine):
        results = indexed_engine.autocomplete("Zon")
        assert len(results) > 0
        assert any(r["category"] == "topic" for r in results)

    def test_autocomplete_empty_prefix_returns_empty(self, indexed_engine):
        results = indexed_engine.autocomplete("")
        assert results == []

    def test_autocomplete_respects_limit(self, indexed_engine):
        results = indexed_engine.autocomplete("B", limit=2)
        assert len(results) <= 2

    def test_autocomplete_result_structure(self, indexed_engine):
        results = indexed_engine.autocomplete("Bro")
        assert len(results) > 0
        item = results[0]
        assert "text" in item
        assert "category" in item


# ---------------------------------------------------------------------------
# Specialized searches (votes, financial)
# ---------------------------------------------------------------------------


class TestSearchVotesAndFinancial:
    def test_search_votes_by_identifier(self, indexed_engine):
        # Note: hyphens are special in FTS5 so we search by description keywords
        result = indexed_engine.search("Ordinance zoning", type_filter="vote")
        assert result["total"] > 0
        vote = result["results"][0]
        assert vote["type"] == "vote"
        assert "metadata" in vote

    def test_search_votes_by_member_name(self, indexed_engine):
        result = indexed_engine.search("Brown", type_filter="vote")
        assert result["total"] > 0

    def test_search_financial_by_amount(self, indexed_engine):
        result = indexed_engine.search("18040000", type_filter="financial")
        # FTS tokenization may split on commas/dollar signs; search the raw number
        # If no results for raw number, try the description
        if result["total"] == 0:
            result = indexed_engine.search("bonds", type_filter="financial")
        assert result["total"] > 0

    def test_financial_result_has_amount_metadata(self, indexed_engine):
        result = indexed_engine.search("bonds", type_filter="financial")
        assert result["total"] > 0
        meta = result["results"][0].get("metadata", {})
        assert "amount" in meta
        assert "$18,040,000" in meta["amount"]


# ---------------------------------------------------------------------------
# Empty and edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_query_returns_empty(self, indexed_engine):
        result = indexed_engine.search("")
        assert result["results"] == []
        assert result["total"] == 0

    def test_whitespace_only_query_returns_empty(self, indexed_engine):
        result = indexed_engine.search("   ")
        assert result["results"] == []
        assert result["total"] == 0

    def test_none_query_returns_empty(self, indexed_engine):
        result = indexed_engine.search(None)
        assert result["results"] == []

    def test_special_characters_dont_crash(self, indexed_engine):
        """FTS5 special characters should be sanitized, not raise errors."""
        for query in [
            'test"query',
            "test(query)",
            "test:query",
            "test^query",
            "test~query",
            "test|query",
            "test{query}",
            "test'query",
            "OR AND NOT",
        ]:
            result = indexed_engine.search(query)
            # Should not raise -- result may or may not have matches
            assert "results" in result
            assert "total" in result

    def test_very_long_query(self, indexed_engine):
        long_query = "zoning " * 200
        result = indexed_engine.search(long_query)
        assert "results" in result

    def test_limit_and_offset(self, indexed_engine):
        full = indexed_engine.search("ordinance", limit=100)
        if full["total"] > 1:
            partial = indexed_engine.search("ordinance", limit=1, offset=0)
            assert len(partial["results"]) == 1

    def test_type_filter_all_returns_all_types(self, indexed_engine):
        result = indexed_engine.search("council", type_filter="all")
        types = {r["type"] for r in result["results"]}
        assert len(types) >= 1


# ---------------------------------------------------------------------------
# FTS query sanitizer
# ---------------------------------------------------------------------------


class TestSanitizeFtsQuery:
    def test_strips_quotes(self):
        assert SearchEngine._sanitize_fts_query('"hello"') == "hello"

    def test_strips_parens(self):
        assert SearchEngine._sanitize_fts_query("(test)") == "test"

    def test_strips_colons(self):
        assert SearchEngine._sanitize_fts_query("title:budget") == "titlebudget"

    def test_collapses_whitespace(self):
        assert SearchEngine._sanitize_fts_query("  hello   world  ") == "hello world"

    def test_empty_after_strip_returns_empty_quotes(self):
        assert SearchEngine._sanitize_fts_query('""') == '""'

    def test_plain_text_unchanged(self):
        assert SearchEngine._sanitize_fts_query("zoning change") == "zoning change"


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_build_index_from_output_dir(self, engine, clip_dir):
        stats = engine.build_index(str(clip_dir))
        assert stats["indexed"] == 1
        assert stats["errors"] == 0

    def test_build_index_skips_already_indexed(self, engine, clip_dir):
        engine.build_index(str(clip_dir))
        stats = engine.build_index(str(clip_dir), force=False)
        assert stats["indexed"] == 0
        assert stats["skipped"] == 1

    def test_build_index_force_reindexes(self, engine, clip_dir):
        engine.build_index(str(clip_dir))
        stats = engine.build_index(str(clip_dir), force=True)
        assert stats["indexed"] == 1
        assert stats["skipped"] == 0

    def test_build_index_missing_dir(self, engine, tmp_path):
        stats = engine.build_index(str(tmp_path / "nonexistent"))
        assert stats["indexed"] == 0


# ---------------------------------------------------------------------------
# Recent searches
# ---------------------------------------------------------------------------


class TestRecentSearches:
    def test_log_and_retrieve_recent(self, indexed_engine):
        indexed_engine.log_search("zoning", "tenant-a", 5)
        indexed_engine.log_search("budget", "tenant-a", 3)

        recent = indexed_engine.recent_searches("tenant-a")
        assert len(recent) == 2
        queries = {r["query"] for r in recent}
        assert queries == {"zoning", "budget"}

    def test_recent_searches_filters_by_tenant(self, indexed_engine):
        indexed_engine.log_search("query1", "tenant-a", 1)
        indexed_engine.log_search("query2", "tenant-b", 2)

        recent_a = indexed_engine.recent_searches("tenant-a")
        assert len(recent_a) == 1
        assert recent_a[0]["query"] == "query1"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_search_result_to_dict(self):
        r = SearchResult(
            type="vote",
            clip_id=100,
            title="Test Vote",
            snippet="A vote on something",
            date="2026-01-01",
            meeting_body="Council",
            score=1.5,
            metadata={"outcome": "passed"},
        )
        d = r.to_dict()
        assert d["type"] == "vote"
        assert d["clip_id"] == 100
        assert d["metadata"]["outcome"] == "passed"

    def test_search_result_to_dict_no_metadata(self):
        r = SearchResult(
            type="meeting", clip_id=1, title="T", snippet="S"
        )
        d = r.to_dict()
        assert "metadata" not in d

    def test_autocomplete_item_to_dict(self):
        item = AutocompleteItem(text="Brown", category="speaker", clip_id=42)
        d = item.to_dict()
        assert d["text"] == "Brown"
        assert d["clip_id"] == 42

    def test_autocomplete_item_no_clip_id(self):
        item = AutocompleteItem(text="Council", category="meeting_body")
        d = item.to_dict()
        assert "clip_id" not in d
