"""Tests for rag/ingest.py - Chunking, embedding, and ChromaDB storage."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import (
    SAMPLE_SUMMARY, SAMPLE_SEGMENTS, SAMPLE_AGENDA, SAMPLE_MINUTES,
    SAMPLE_NOISY_SEGMENTS, SAMPLE_SEGMENTS_WITH_GAPS,
)


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
# 2. Transcript cleaning tests
# ============================================================

class TestCleanSegments:
    """Test filtering out noisy Whisper segments."""

    def test_dot_only_segments_removed(self):
        from rag.ingest import clean_segments

        segments = [
            {"start": 0.0, "end": 1.0, "text": "."},
            {"start": 1.0, "end": 2.0, "text": "..."},
            {"start": 2.0, "end": 10.0, "text": "Welcome to the meeting."},
        ]
        cleaned = clean_segments(segments)
        texts = [s["text"] for s in cleaned]
        assert "." not in texts
        assert "..." not in texts
        assert "Welcome to the meeting." in texts

    def test_music_symbol_segments_removed(self):
        from rag.ingest import clean_segments

        segments = [
            {"start": 0.0, "end": 1.0, "text": "\u266a"},
            {"start": 1.0, "end": 2.0, "text": "\U0001f3b5"},
            {"start": 2.0, "end": 3.0, "text": "Music"},
            {"start": 3.0, "end": 10.0, "text": "Good evening everyone."},
        ]
        cleaned = clean_segments(segments)
        assert len(cleaned) == 1
        assert cleaned[0]["text"] == "Good evening everyone."

    def test_repeated_phrase_runs_collapsed(self):
        from rag.ingest import clean_segments

        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello everyone."},
        ]
        # Add 7 identical segments
        for i in range(7):
            segments.append({"start": 5.0 + i, "end": 6.0 + i, "text": "Of the"})
        segments.append({"start": 12.0, "end": 20.0, "text": "The meeting continues."})

        cleaned = clean_segments(segments)
        of_the_count = sum(1 for s in cleaned if s["text"].strip() == "Of the")
        assert of_the_count == 1

    def test_non_ascii_gibberish_removed(self):
        from rag.ingest import clean_segments

        segments = [
            {"start": 0.0, "end": 5.0, "text": "Normal English text here."},
            {"start": 5.0, "end": 6.0, "text": "\u042b\u0444\u0432\u0430\u0444\u044b\u0432 \u0434\u0444\u044b\u0432\u0430 \u0444\u0434\u044b\u0432\u0430"},
            {"start": 6.0, "end": 7.0, "text": "\u10d9\u10d0\u10e0\u10d2\u10d8 \u10e1\u10d0\u10e6\u10d0\u10db\u10dd\u10d0"},
            {"start": 7.0, "end": 15.0, "text": "Back to normal speech here."},
        ]
        cleaned = clean_segments(segments)
        assert len(cleaned) == 2
        assert cleaned[0]["text"] == "Normal English text here."
        assert cleaned[1]["text"] == "Back to normal speech here."

    def test_normal_speech_preserved(self):
        from rag.ingest import clean_segments

        cleaned = clean_segments(SAMPLE_SEGMENTS)
        # Most of the sample segments are normal speech — should keep most of them
        # Only the "so" filler at start might be removed
        assert len(cleaned) >= len(SAMPLE_SEGMENTS) - 1

    def test_noisy_segments_fixture_cleaned(self):
        from rag.ingest import clean_segments

        cleaned = clean_segments(SAMPLE_NOISY_SEGMENTS)
        texts = [s["text"] for s in cleaned]
        # Should keep the two real speech segments
        assert "Welcome to the council meeting today." in texts
        assert "The first item of business is the roll call." in texts
        assert "Councilmember Beasley voted yes on the motion." in texts
        # Should remove music, dots, gibberish, filler
        assert "\u266a" not in texts
        assert "\U0001f3b5" not in texts
        assert "." not in texts
        assert "Music" not in texts
        # Repetition should be collapsed to 1
        of_the_count = sum(1 for t in texts if t.strip() == "Of the")
        assert of_the_count == 1

    def test_empty_segments_returns_empty(self):
        from rag.ingest import clean_segments

        assert clean_segments([]) == []


# ============================================================
# 3. Transcript passage grouping tests
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

    def test_chunk_transcript_start_matches_first_cleaned_segment(self):
        from rag.ingest import chunk_transcript, clean_segments

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # First chunk should start at the first *cleaned* segment's start time
        # (the "so" filler at index 0 is removed by clean_segments)
        cleaned = clean_segments(SAMPLE_SEGMENTS)
        assert chunks[0]["start_time"] == cleaned[0]["start"]

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

    def test_silence_gap_triggers_chunk_boundary(self):
        """A >5s silence gap should trigger a chunk boundary when enough words accumulated."""
        from rag.ingest import chunk_transcript, MIN_CHUNK_WORDS

        # Build segments: enough words before the gap, then a gap, then more words
        segments = []
        t = 0.0
        # Add enough segments to exceed MIN_CHUNK_WORDS before the gap
        for i in range(30):
            segments.append({
                "start": t,
                "end": t + 5.0,
                "text": f"This is segment number {i} with some words to accumulate towards the minimum."
            })
            t += 5.0
        # Add a silence gap of 10 seconds
        gap_start = t + 10.0
        for i in range(20):
            segments.append({
                "start": gap_start,
                "end": gap_start + 5.0,
                "text": f"After the gap segment {i} with more content to fill another chunk."
            })
            gap_start += 5.0

        chunks = chunk_transcript(segments, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # Should produce multiple chunks, with a boundary at or near the gap
        assert len(chunks) >= 2

    def test_procedural_phrase_triggers_chunk_boundary(self):
        """Procedural phrases like 'next item' should trigger a boundary."""
        from rag.ingest import chunk_transcript

        segments = []
        t = 0.0
        # Enough segments to accumulate >200 words
        for i in range(25):
            segments.append({
                "start": t,
                "end": t + 5.0,
                "text": f"Discussion about item {i} with several words to build up the chunk size."
            })
            t += 5.0
        # Procedural transition segment
        segments.append({
            "start": t,
            "end": t + 5.0,
            "text": "Moving on to the next item on the agenda."
        })
        t += 5.0
        for i in range(15):
            segments.append({
                "start": t,
                "end": t + 5.0,
                "text": f"New topic segment {i} covering a different subject for this chunk."
            })
            t += 5.0

        chunks = chunk_transcript(segments, clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert len(chunks) >= 2

    def test_min_word_threshold_prevents_tiny_chunks(self):
        """Boundaries should not create chunks smaller than MIN_CHUNK_WORDS."""
        from rag.ingest import chunk_transcript

        # Only 3 segments — too few words for MIN_CHUNK_WORDS, so boundary shouldn't trigger
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello everyone."},
            # Gap > 5 seconds
            {"start": 15.0, "end": 20.0, "text": "Moving on to the next item."},
            {"start": 20.0, "end": 25.0, "text": "Thank you."},
        ]
        chunks = chunk_transcript(segments, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # Should produce just 1 chunk (not enough words to split)
        assert len(chunks) == 1

    def test_falls_back_to_word_count_splitting(self):
        """Without boundaries, chunks should split at target_words like before."""
        from rag.ingest import chunk_transcript

        # Generate many segments with no gaps or procedural phrases
        segments = []
        for i in range(100):
            segments.append({
                "start": float(i * 2),
                "end": float(i * 2 + 1.9),
                "text": f"Segment {i} talking about various topics in the meeting discussion today."
            })
        chunks = chunk_transcript(segments, clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert len(chunks) > 1


# ============================================================
# 4. Agenda/minutes chunking tests
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
# 5. ChromaDB storage tests
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
# 6. Full clip ingestion tests
# ============================================================

class TestIngestClip:
    """Test ingesting a single clip from its output directory."""

    def test_ingest_clip_processes_all_sources(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        # Should have chunks from transcript + agenda + minutes (no summary)
        assert chroma_collection.count() > 0
        # Verify NO summary chunks
        results = chroma_collection.get(where={"source": "summary"}, include=["metadatas"])
        assert len(results["ids"]) == 0

    def test_ingest_clip_stores_transcript_chunks(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        results = chroma_collection.get(where={"source": "transcript"}, include=["metadatas"])
        assert len(results["ids"]) >= 1

    def test_ingest_clip_stores_minutes_chunks(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from rag.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        results = chroma_collection.get(where={"source": "minutes"}, include=["metadatas"])
        assert len(results["ids"]) >= 1

    def test_ingest_clip_skips_missing_files_gracefully(self, tmp_path, chroma_collection, mock_openai_batch_embeddings):
        """If a clip directory has only metadata and minutes, it should still work."""
        from rag.ingest import ingest_clip

        clip_dir = tmp_path / "clips" / "9999"
        clip_dir.mkdir(parents=True)
        meta = {
            "clip_id": 9999,
            "date": "2026-01-01",
            "meeting_body": "Committee",
            "title": "Test Meeting",
            "files": {"minutes_txt": "minutes.txt"},
        }
        (clip_dir / "metadata.json").write_text(json.dumps(meta))
        (clip_dir / "minutes.txt").write_text("COMMITTEE MEETING\nJanuary 1, 2026\n\nRoll Call\nMembers present: Smith, Jones.\n")

        ingest_clip(9999, tmp_path, chroma_collection, mock_openai_batch_embeddings)
        assert chroma_collection.count() >= 1


# ============================================================
# 7. Incremental ingestion tests
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
# 8. Stats output test
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
