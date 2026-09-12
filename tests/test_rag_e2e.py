"""End-to-end tests for the RAG pipeline: ingest -> query -> answer.

These tests verify the full path from clip files on disk through chunking,
embedding, vector-store storage, retrieval, deduplication, and synthesis.
Uses the backend-driven ``vecstore`` fixture (chroma by default;
``VECTOR_BACKEND=sqlite`` runs the same tests on sqlite-vec) and mocked
OpenAI/Anthropic clients.
"""

import json
from unittest.mock import MagicMock

import pytest

from rag.ingest import (
    ingest_clip,
    get_stats,
    chunk_summary,
    chunk_extracted_facts,
    chunk_transcript,
    chunk_document,
)
from rag.query import ask, deduplicate_results, build_synthesis_messages


class TestIngestProducesAllSourceTypes:
    """Verify that ingest_clip produces chunks from all 5 source types."""

    def test_all_five_sources_ingested(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        sources = set()
        for hit in vecstore.get_chunks():
            sources.add(hit["metadata"]["source"])

        assert "summary" in sources
        assert "facts" in sources
        assert "minutes" in sources
        assert "agenda" in sources
        assert "transcript" in sources

    def test_facts_chunks_contain_vote_data(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        hits = vecstore.get_chunks(filters={"source": "facts"})
        all_text = " ".join(h["document"] for h in hits)
        assert "Ordinance 0016-26" in all_text
        assert "passed" in all_text
        assert "8" in all_text  # ayes count

    def test_facts_chunks_contain_financial_data(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        hits = vecstore.get_chunks(
            filters={"source": "facts", "section_type": "financial"})
        assert len(hits) > 0
        assert "$18,040,000" in hits[0]["document"]

    def test_summary_chunks_have_section_types(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        hits = vecstore.get_chunks(filters={"source": "summary"})
        section_types = [h["metadata"]["section_type"] for h in hits]
        assert "Meeting Overview" in section_types
        assert "Key Decisions & Votes" in section_types

    def test_transcript_chunks_have_timestamps(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        hits = vecstore.get_chunks(filters={"source": "transcript"})
        assert len(hits) > 0
        # Transcript chunks should have start_time and end_time
        meta = hits[0]["metadata"]
        assert "start_time" in meta
        assert "end_time" in meta


class TestIngestToQueryPipeline:
    """Test the full ingest -> query -> answer path."""

    def test_query_returns_answer_with_sources(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        """Full pipeline: ingest a clip, query it, get an answer with sources."""
        # Ingest
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)
        assert vecstore.count() > 0

        # Query (mock OpenAI for both embedding and chat)
        mock_client = mock_openai_batch_embeddings
        mock_choice = MagicMock()
        mock_choice.message.content = "Ordinance 0016-26 passed 8-0, changing zoning from Agricultural to Residential. [Clip 6669, 25:15]"
        mock_chat_response = MagicMock()
        mock_chat_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_chat_response

        result = ask(
            question="What zoning changes were approved?",
            store=vecstore,
            openai_client=mock_client,
            clip_metadata={6669: {"title": "Urban County Council (1)"}},
        )

        assert result["answer"] is not None
        assert len(result["answer"]) > 0
        assert result["chunks_retrieved"] > 0
        assert len(result["sources"]) > 0
        assert result["sources"][0]["clip_id"] == 6669

    def test_query_sources_have_granicus_urls(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        mock_client = mock_openai_batch_embeddings
        mock_choice = MagicMock()
        mock_choice.message.content = "Answer text."
        mock_chat_response = MagicMock()
        mock_chat_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_chat_response

        result = ask(
            question="Tell me about the meeting",
            store=vecstore,
            openai_client=mock_client,
        )

        for source in result["sources"]:
            assert "granicus_url" in source
            assert "lfucg.granicus.com" in source["granicus_url"]

    def test_query_with_meeting_body_filter(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        mock_client = mock_openai_batch_embeddings
        mock_choice = MagicMock()
        mock_choice.message.content = "Answer."
        mock_chat_response = MagicMock()
        mock_chat_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_chat_response

        # Filter by correct body
        result = ask(
            question="What happened?",
            store=vecstore,
            openai_client=mock_client,
            filters={"meeting_body": "Council"},
        )
        assert result["chunks_retrieved"] > 0

        # Filter by wrong body — should still return (ChromaDB may return
        # empty results which triggers the "not enough info" fallback)
        result_wrong = ask(
            question="What happened?",
            store=vecstore,
            openai_client=mock_client,
            filters={"meeting_body": "Nonexistent Body"},
        )
        # Either no chunks or a fallback answer
        assert result_wrong["chunks_retrieved"] == 0 or "don't have enough" in result_wrong["answer"].lower()


class TestSourceDiversityInRetrieval:
    """Verify that deduplication ensures source diversity."""

    def test_dedup_limits_same_source_from_one_clip(self):
        """4 summary chunks from same clip should be limited to 2."""
        results = {
            "ids": [["a", "b", "c", "d"]],
            "documents": [["sum1", "sum2", "sum3", "sum4"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "summary"},
                {"clip_id": 6669, "source": "summary"},
                {"clip_id": 6669, "source": "summary"},
                {"clip_id": 6669, "source": "summary"},
            ]],
            "distances": [[0.1, 0.2, 0.3, 0.4]],
        }
        deduped = deduplicate_results(results)
        assert len(deduped["ids"]) == 2

    def test_dedup_allows_diverse_sources_from_one_clip(self):
        """Different sources from the same clip should all be kept up to max_per_clip."""
        results = {
            "ids": [["a", "b", "c", "d", "e"]],
            "documents": [["facts", "summary", "minutes", "transcript", "agenda"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "facts"},
                {"clip_id": 6669, "source": "summary"},
                {"clip_id": 6669, "source": "minutes"},
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "agenda"},
            ]],
            "distances": [[0.1, 0.15, 0.2, 0.25, 0.3]],
        }
        deduped = deduplicate_results(results)  # max_per_clip=3 (was 4 until 2026-09-12)
        assert len(deduped["ids"]) == 3
        sources = [m["source"] for m in deduped["metadatas"]]
        # Should have 3 different sources (4th/5th dropped by max_per_clip)
        assert len(set(sources)) == 3

    def test_dedup_balances_across_multiple_clips(self):
        """Multiple clips should each get fair representation."""
        results = {
            "ids": [["a1", "a2", "a3", "a4", "a5", "b1", "b2", "b3"]],
            "documents": [["d"] * 8],
            "metadatas": [[
                {"clip_id": 100, "source": "facts"},
                {"clip_id": 100, "source": "summary"},
                {"clip_id": 100, "source": "minutes"},
                {"clip_id": 100, "source": "transcript"},
                {"clip_id": 100, "source": "agenda"},
                {"clip_id": 200, "source": "facts"},
                {"clip_id": 200, "source": "summary"},
                {"clip_id": 200, "source": "transcript"},
            ]],
            "distances": [[0.1, 0.12, 0.14, 0.16, 0.18, 0.11, 0.13, 0.15]],
        }
        deduped = deduplicate_results(results)
        clip_100_count = sum(1 for m in deduped["metadatas"] if m["clip_id"] == 100)
        clip_200_count = sum(1 for m in deduped["metadatas"] if m["clip_id"] == 200)
        assert clip_100_count <= 3
        assert clip_200_count <= 4
        assert clip_200_count >= 1  # clip 200 should still be represented


class TestSynthesisPromptIncludesTimestamps:
    """Verify timestamps from summary chunks appear in synthesis context."""

    def test_summary_start_time_appears_in_context(self):
        chunks = [{
            "text": "Votes and Decisions\nOrdinance passed 8-0. [timestamp: 25:15]",
            "clip_id": 6669,
            "date": "2026-01-22",
            "meeting_body": "Council",
            "source": "summary",
            "title": "Urban County Council",
            "start_time": 1515,  # 25:15
        }]
        messages = build_synthesis_messages("What was voted on?", chunks)
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "25:15" in user_msg["content"]

    def test_transcript_timestamp_range_in_context(self):
        chunks = [{
            "text": "Discussion about zoning.",
            "clip_id": 6669,
            "date": "2026-01-22",
            "meeting_body": "Council",
            "source": "transcript",
            "title": "Urban County Council",
            "start_time": 120.0,
            "end_time": 180.0,
        }]
        messages = build_synthesis_messages("Zoning?", chunks)
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "2:00-3:00" in user_msg["content"]

    def test_facts_chunks_have_no_timestamp_no_crash(self):
        """Facts chunks have no start_time — synthesis should handle gracefully."""
        chunks = [{
            "text": "Ordinance 0016-26: passed (Ayes: 8, Nays: 0)",
            "clip_id": 6669,
            "date": "2026-01-22",
            "meeting_body": "Council",
            "source": "facts",
            "title": "Urban County Council",
        }]
        messages = build_synthesis_messages("Vote results?", chunks)
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Ordinance 0016-26" in user_msg["content"]
        assert "Timestamp" not in user_msg["content"]


class TestChunkExtractedFacts:
    """Test that extracted facts produce well-formed chunks."""

    def test_produces_chunks_for_each_category(self):
        from tests.conftest import SAMPLE_EXTRACTED_FACTS
        chunks = chunk_extracted_facts(SAMPLE_EXTRACTED_FACTS, 6669, "2026-01-22", "Council")

        sources = [c["section_type"] for c in chunks]
        assert "votes" in sources
        assert "financial" in sources
        assert "agenda_items" in sources
        assert "attendance" in sources

    def test_empty_facts_returns_empty(self):
        assert chunk_extracted_facts({}, 6669, "2026-01-22", "Council") == []
        assert chunk_extracted_facts(None, 6669, "2026-01-22", "Council") == []

    def test_vote_chunk_contains_identifiers_and_counts(self):
        from tests.conftest import SAMPLE_EXTRACTED_FACTS
        chunks = chunk_extracted_facts(SAMPLE_EXTRACTED_FACTS, 6669, "2026-01-22", "Council")
        vote_chunk = next(c for c in chunks if c["section_type"] == "votes")

        assert "Ordinance 0016-26" in vote_chunk["text"]
        assert "Ayes: 8" in vote_chunk["text"]
        assert "Ordinance 0052-26" in vote_chunk["text"]

    def test_financial_chunk_contains_amounts(self):
        from tests.conftest import SAMPLE_EXTRACTED_FACTS
        chunks = chunk_extracted_facts(SAMPLE_EXTRACTED_FACTS, 6669, "2026-01-22", "Council")
        fin_chunk = next(c for c in chunks if c["section_type"] == "financial")

        assert "$18,040,000" in fin_chunk["text"]

    def test_attendance_chunk_lists_members(self):
        from tests.conftest import SAMPLE_EXTRACTED_FACTS
        chunks = chunk_extracted_facts(SAMPLE_EXTRACTED_FACTS, 6669, "2026-01-22", "Council")
        att_chunk = next(c for c in chunks if c["section_type"] == "attendance")

        assert "Beasley" in att_chunk["text"]
        assert "Sheehan" in att_chunk["text"]

    def test_skips_empty_categories(self):
        """Facts with empty public_comments/appointments/contentious should not produce those chunks."""
        from tests.conftest import SAMPLE_EXTRACTED_FACTS
        chunks = chunk_extracted_facts(SAMPLE_EXTRACTED_FACTS, 6669, "2026-01-22", "Council")
        section_types = {c["section_type"] for c in chunks}

        assert "public_comments" not in section_types
        assert "contentious" not in section_types

    def test_all_chunks_have_correct_metadata(self):
        from tests.conftest import SAMPLE_EXTRACTED_FACTS
        chunks = chunk_extracted_facts(SAMPLE_EXTRACTED_FACTS, 6669, "2026-01-22", "Council")

        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"
            assert chunk["source"] == "facts"


class TestRagStateManagement:
    """Test that ingestion properly tracks state for resumability."""

    def test_ingest_clip_saves_state(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        from rag.ingest import load_rag_state

        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        state = load_rag_state(sample_clip_dir)
        assert 6669 in state["ingested_clips"]

    def test_skip_if_ingested_works(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        from rag.ingest import load_rag_state

        # First ingest
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)
        count_after_first = vecstore.count()

        # Second ingest with skip — should not add more chunks
        state = load_rag_state(sample_clip_dir)
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings,
                    skip_if_ingested=True, rag_state=state)
        assert vecstore.count() == count_after_first

    def test_stats_reflect_ingested_clips(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)

        stats = get_stats(vecstore, str(sample_clip_dir))
        assert stats["total_chunks"] > 0
        assert stats["unique_clips"] == 1
