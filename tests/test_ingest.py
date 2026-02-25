"""Tests for rag/ingest.py - Chunking, embedding, and ChromaDB storage."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import SAMPLE_SUMMARY, SAMPLE_SEGMENTS, SAMPLE_AGENDA, SAMPLE_MINUTES


# ============================================================
# 1. Summary chunking tests
# ============================================================

class TestChunkSummary:
    """Test parsing summary.txt into section-based chunks."""

    def test_chunk_summary_splits_on_h2_headers(self):
        from rag.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # The sample summary has 5 ## sections
        assert len(chunks) == 5

    def test_chunk_summary_preserves_section_text(self):
        from rag.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # First chunk should be Meeting Overview
        assert "Meeting Overview" in chunks[0]["section_type"]
        assert "Mayor Linda Gorton" in chunks[0]["text"]

    def test_chunk_summary_sets_correct_metadata(self):
        from rag.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"
            assert chunk["source"] == "summary"
            assert "section_type" in chunk
            assert "text" in chunk

    def test_chunk_summary_section_types_match_headers(self):
        from rag.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        section_types = [c["section_type"] for c in chunks]
        assert "Meeting Overview" in section_types
        assert "Key Decisions & Votes" in section_types
        assert "Public Comments & Citizen Input" in section_types

    def test_chunk_summary_empty_summary_returns_empty(self):
        from rag.ingest import chunk_summary

        chunks = chunk_summary("", clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert chunks == []

    def test_chunk_summary_no_h2_headers_returns_single_chunk(self):
        from rag.ingest import chunk_summary

        text = "This is a summary with no section headers.\nJust plain text."
        chunks = chunk_summary(text, clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert len(chunks) == 1
        assert chunks[0]["section_type"] == "General"


# ============================================================
# 2. Transcript passage grouping tests
# ============================================================

class TestChunkTranscript:
    """Test grouping transcript segments into ~500-word passages with overlap."""

    def test_chunk_transcript_groups_segments(self):
        from rag.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # With 9 short segments, should produce at least 1 chunk
        assert len(chunks) >= 1

    def test_chunk_transcript_sets_timestamps(self):
        from rag.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        for chunk in chunks:
            assert "start_time" in chunk
            assert "end_time" in chunk
            assert chunk["start_time"] >= 0
            assert chunk["end_time"] >= chunk["start_time"]

    def test_chunk_transcript_start_matches_first_segment(self):
        from rag.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # First chunk should start at the first segment's start time
        assert chunks[0]["start_time"] == SAMPLE_SEGMENTS[0]["start"]

    def test_chunk_transcript_sets_correct_metadata(self):
        from rag.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"
            assert chunk["source"] == "transcript"

    def test_chunk_transcript_empty_segments_returns_empty(self):
        from rag.ingest import chunk_transcript

        chunks = chunk_transcript([], clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert chunks == []

    def test_chunk_transcript_respects_word_limit(self):
        """Long transcripts should be split into multiple ~500-word passages."""
        from rag.ingest import chunk_transcript

        # Generate many segments to exceed 500 words
        long_segments = []
        for i in range(100):
            long_segments.append({
                "start": float(i * 10),
                "end": float(i * 10 + 9),
                "text": f"This is segment number {i} with enough words to make it a reasonable length for testing purposes here."
            })
        chunks = chunk_transcript(long_segments, clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert len(chunks) > 1

    def test_chunk_transcript_overlap_between_passages(self):
        """Adjacent passages should share some overlapping text."""
        from rag.ingest import chunk_transcript

        long_segments = []
        for i in range(100):
            long_segments.append({
                "start": float(i * 10),
                "end": float(i * 10 + 9),
                "text": f"Unique segment marker {i} with additional words to fill up the passage for overlap testing."
            })
        chunks = chunk_transcript(long_segments, clip_id=6669, date="2026-01-22", meeting_body="Council")
        if len(chunks) >= 2:
            # The end of chunk 0 should overlap with the start of chunk 1
            # Check that chunk 1 starts before chunk 0 ends (overlap)
            assert chunks[1]["start_time"] < chunks[0]["end_time"]


# ============================================================
# 3. Agenda/minutes chunking tests
# ============================================================

class TestChunkDocument:
    """Test splitting agenda/minutes text by section headers."""

    def test_chunk_agenda_splits_by_sections(self):
        from rag.ingest import chunk_document

        chunks = chunk_document(SAMPLE_AGENDA, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["source"] == "agenda"

    def test_chunk_minutes_splits_by_sections(self):
        from rag.ingest import chunk_document

        chunks = chunk_document(SAMPLE_MINUTES, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="minutes")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["source"] == "minutes"

    def test_chunk_document_sets_correct_metadata(self):
        from rag.ingest import chunk_document

        chunks = chunk_document(SAMPLE_AGENDA, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"

    def test_chunk_document_empty_text_returns_empty(self):
        from rag.ingest import chunk_document

        chunks = chunk_document("", clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        assert chunks == []

    def test_chunk_document_respects_word_limit(self):
        """Long documents should be split into ~500-word chunks."""
        from rag.ingest import chunk_document

        long_doc = "\n".join([f"Section {i}: " + "word " * 200 for i in range(5)])
        chunks = chunk_document(long_doc, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        for chunk in chunks:
            word_count = len(chunk["text"].split())
            assert word_count <= 600  # Allow some flexibility


# ============================================================
# 4. ChromaDB storage tests
# ============================================================

class TestStoreChunks:
    """Test storing chunks in ChromaDB with correct metadata."""

    def test_store_chunks_adds_to_collection(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import store_chunks

        chunks = [
            {
                "text": "Test chunk text",
                "clip_id": 6669,
                "date": "2026-01-22",
                "meeting_body": "Council",
                "source": "summary",
                "section_type": "Meeting Overview",
            }
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        assert chroma_collection.count() == 1

    def test_store_chunks_preserves_metadata(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import store_chunks

        chunks = [
            {
                "text": "Test chunk with metadata",
                "clip_id": 6669,
                "date": "2026-01-22",
                "meeting_body": "Council",
                "source": "transcript",
                "start_time": 60.0,
                "end_time": 120.0,
            }
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        result = chroma_collection.get(include=["metadatas"])
        meta = result["metadatas"][0]
        assert meta["clip_id"] == 6669
        assert meta["date"] == "2026-01-22"
        assert meta["meeting_body"] == "Council"
        assert meta["source"] == "transcript"
        assert meta["start_time"] == 60.0
        assert meta["end_time"] == 120.0

    def test_store_chunks_generates_unique_ids(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import store_chunks

        chunks = [
            {"text": "Chunk A", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Overview"},
            {"text": "Chunk B", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Votes"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        assert chroma_collection.count() == 2

    def test_store_chunks_calls_openai_embeddings(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import store_chunks

        chunks = [
            {"text": "Test embedding call", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Overview"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        mock_openai_batch_embeddings.embeddings.create.assert_called()


# ============================================================
# 5. Full clip ingestion tests
# ============================================================

class TestIngestClip:
    """Test ingesting a single clip from its output directory."""

    def test_ingest_clip_processes_all_sources(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        # Should have chunks from summary + transcript + agenda + minutes
        assert chroma_collection.count() > 0

    def test_ingest_clip_stores_summary_chunks(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        results = chroma_collection.get(where={"source": "summary"}, include=["metadatas"])
        assert len(results["ids"]) >= 1

    def test_ingest_clip_stores_transcript_chunks(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        results = chroma_collection.get(where={"source": "transcript"}, include=["metadatas"])
        assert len(results["ids"]) >= 1

    def test_ingest_clip_skips_missing_files_gracefully(self, tmp_path, chroma_collection, mock_openai_batch_embeddings):
        """If a clip directory has only metadata and summary, it should still work."""
        from rag.ingest import ingest_clip

        clip_dir = tmp_path / "clips" / "9999"
        clip_dir.mkdir(parents=True)
        meta = {
            "clip_id": 9999,
            "date": "2026-01-01",
            "meeting_body": "Committee",
            "title": "Test Meeting",
            "files": {"summary_txt": "summary.txt"},
        }
        (clip_dir / "metadata.json").write_text(json.dumps(meta))
        (clip_dir / "summary.txt").write_text("## Overview\nA short meeting.\n")

        ingest_clip(9999, tmp_path, chroma_collection, mock_openai_batch_embeddings)
        assert chroma_collection.count() >= 1


# ============================================================
# 6. Incremental ingestion tests
# ============================================================

class TestIncrementalIngestion:
    """Test that already-ingested clips are skipped."""

    def test_ingest_new_skips_already_ingested(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip, load_rag_state, save_rag_state

        # Ingest once
        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        count_after_first = chroma_collection.count()

        # Mark as ingested in state
        state = load_rag_state(sample_clip_dir)
        state["ingested_clips"].append(6669)
        save_rag_state(state, sample_clip_dir)

        # Try to ingest again — should skip
        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings,
                    skip_if_ingested=True)
        assert chroma_collection.count() == count_after_first

    def test_load_rag_state_returns_default_when_missing(self, tmp_path):
        from rag.ingest import load_rag_state

        state = load_rag_state(tmp_path)
        assert "ingested_clips" in state
        assert state["ingested_clips"] == []

    def test_save_and_load_rag_state_roundtrip(self, tmp_path):
        from rag.ingest import load_rag_state, save_rag_state

        state = {"ingested_clips": [6669, 6670]}
        save_rag_state(state, tmp_path)
        loaded = load_rag_state(tmp_path)
        assert loaded["ingested_clips"] == [6669, 6670]


# ============================================================
# 7. Stats output test
# ============================================================

class TestStats:
    """Test the --stats CLI output."""

    def test_get_stats_returns_counts(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import store_chunks, get_stats

        chunks = [
            {"text": "Chunk 1", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Overview"},
            {"text": "Chunk 2", "clip_id": 6670, "date": "2026-01-23",
             "meeting_body": "Committee", "source": "transcript",
             "start_time": 0.0, "end_time": 60.0},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        stats = get_stats(chroma_collection)
        assert stats["total_chunks"] == 2
        assert stats["unique_clips"] >= 1
