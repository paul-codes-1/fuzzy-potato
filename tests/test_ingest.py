"""Tests for api/ingest.py - Chunking, embedding, and ChromaDB storage."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import (
    SAMPLE_SUMMARY, SAMPLE_SEGMENTS, SAMPLE_AGENDA, SAMPLE_MINUTES,
    SAMPLE_NOISY_SEGMENTS,
)


# ============================================================
# 1. Summary chunking tests
# ============================================================

class TestChunkSummary:
    """Test parsing summary.txt into section-based chunks."""

    def test_chunk_summary_splits_on_h2_headers(self):
        from api.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # The sample summary has 5 ## sections
        assert len(chunks) == 5

    def test_chunk_summary_preserves_section_text(self):
        from api.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # First chunk should be Meeting Overview
        assert "Meeting Overview" in chunks[0]["section_type"]
        assert "Mayor Linda Gorton" in chunks[0]["text"]

    def test_chunk_summary_sets_correct_metadata(self):
        from api.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"
            assert chunk["source"] == "summary"
            assert "section_type" in chunk
            assert "text" in chunk

    def test_chunk_summary_section_types_match_headers(self):
        from api.ingest import chunk_summary

        chunks = chunk_summary(SAMPLE_SUMMARY, clip_id=6669, date="2026-01-22", meeting_body="Council")
        section_types = [c["section_type"] for c in chunks]
        assert "Meeting Overview" in section_types
        assert "Key Decisions & Votes" in section_types
        assert "Public Comments & Citizen Input" in section_types

    def test_chunk_summary_empty_summary_returns_empty(self):
        from api.ingest import chunk_summary

        chunks = chunk_summary("", clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert chunks == []

    def test_chunk_summary_no_h2_headers_returns_single_chunk(self):
        from api.ingest import chunk_summary

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
        from api.ingest import clean_segments

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
        from api.ingest import clean_segments

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
        from api.ingest import clean_segments

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
        from api.ingest import clean_segments

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
        from api.ingest import clean_segments

        cleaned = clean_segments(SAMPLE_SEGMENTS)
        # Most of the sample segments are normal speech — should keep most of them
        # Only the "so" filler at start might be removed
        assert len(cleaned) >= len(SAMPLE_SEGMENTS) - 1

    def test_noisy_segments_fixture_cleaned(self):
        from api.ingest import clean_segments

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

    def test_two_consecutive_duplicates_collapsed(self):
        """Even 2 consecutive identical segments should be collapsed to 1."""
        from api.ingest import clean_segments

        segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello everyone."},
            {"start": 5.0, "end": 6.0, "text": "Thank you."},
            {"start": 6.0, "end": 7.0, "text": "Thank you."},
            {"start": 7.0, "end": 15.0, "text": "The meeting continues."},
        ]
        cleaned = clean_segments(segments)
        thank_you_count = sum(1 for s in cleaned if s["text"].strip() == "Thank you.")
        assert thank_you_count == 1

    def test_empty_segments_returns_empty(self):
        from api.ingest import clean_segments

        assert clean_segments([]) == []


# ============================================================
# 3. Transcript passage grouping tests
# ============================================================

class TestChunkTranscript:
    """Test grouping transcript segments into ~500-word passages with overlap."""

    def test_chunk_transcript_groups_segments(self):
        from api.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # With 9 short segments, should produce at least 1 chunk
        assert len(chunks) >= 1

    def test_chunk_transcript_sets_timestamps(self):
        from api.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        for chunk in chunks:
            assert "start_time" in chunk
            assert "end_time" in chunk
            assert chunk["start_time"] >= 0
            assert chunk["end_time"] >= chunk["start_time"]

    def test_chunk_transcript_start_matches_first_cleaned_segment(self):
        from api.ingest import chunk_transcript, clean_segments

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        # First chunk should start at the first *cleaned* segment's start time
        # (the "so" filler at index 0 is removed by clean_segments)
        cleaned = clean_segments(SAMPLE_SEGMENTS)
        assert chunks[0]["start_time"] == cleaned[0]["start"]

    def test_chunk_transcript_sets_correct_metadata(self):
        from api.ingest import chunk_transcript

        chunks = chunk_transcript(SAMPLE_SEGMENTS, clip_id=6669, date="2026-01-22", meeting_body="Council")
        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"
            assert chunk["source"] == "transcript"

    def test_chunk_transcript_empty_segments_returns_empty(self):
        from api.ingest import chunk_transcript

        chunks = chunk_transcript([], clip_id=6669, date="2026-01-22", meeting_body="Council")
        assert chunks == []

    def test_chunk_transcript_respects_word_limit(self):
        """Long transcripts should be split into multiple ~500-word passages."""
        from api.ingest import chunk_transcript

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
        from api.ingest import chunk_transcript

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
        from api.ingest import chunk_transcript

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
        from api.ingest import chunk_transcript

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
        from api.ingest import chunk_transcript

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

    def test_no_duplicate_final_chunk_when_last_emit_at_end(self):
        """When the last segment triggers an emit, no duplicate overlap chunk should follow."""
        from api.ingest import chunk_transcript

        # Single huge segment that exceeds target_words
        segments = [
            {"start": 0.0, "end": 60.0, "text": "word " * 600}
        ]
        chunks = chunk_transcript(segments, clip_id=1, date="2026-01-01", meeting_body="Test")
        # Should produce exactly 1 chunk, not 2
        assert len(chunks) == 1

    def test_no_duplicate_when_boundary_fires_at_last_segment(self):
        """When a topic boundary fires at the very last segment, no duplicate."""
        from api.ingest import chunk_transcript

        segments = []
        t = 0.0
        # Accumulate enough words, then end on a procedural phrase
        for i in range(30):
            segments.append({
                "start": t,
                "end": t + 5.0,
                "text": f"Discussion about item {i} with several words to build up size."
            })
            t += 5.0
        # Last segment is a procedural phrase that triggers boundary
        segments.append({
            "start": t,
            "end": t + 5.0,
            "text": "Moving on to the next item."
        })

        chunks = chunk_transcript(segments, clip_id=1, date="2026-01-01", meeting_body="Test")
        # Verify no two adjacent chunks have identical start_time and end_time
        for i in range(1, len(chunks)):
            same_start = chunks[i]["start_time"] == chunks[i-1]["start_time"]
            same_end = chunks[i]["end_time"] == chunks[i-1]["end_time"]
            assert not (same_start and same_end), f"Chunks {i-1} and {i} are duplicates"

    def test_falls_back_to_word_count_splitting(self):
        """Without boundaries, chunks should split at target_words like before."""
        from api.ingest import chunk_transcript

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
        from api.ingest import chunk_document

        chunks = chunk_document(SAMPLE_AGENDA, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["source"] == "agenda"

    def test_chunk_minutes_splits_by_sections(self):
        from api.ingest import chunk_document

        chunks = chunk_document(SAMPLE_MINUTES, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="minutes")
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["source"] == "minutes"

    def test_chunk_document_sets_correct_metadata(self):
        from api.ingest import chunk_document

        chunks = chunk_document(SAMPLE_AGENDA, clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        for chunk in chunks:
            assert chunk["clip_id"] == 6669
            assert chunk["date"] == "2026-01-22"
            assert chunk["meeting_body"] == "Council"

    def test_chunk_document_empty_text_returns_empty(self):
        from api.ingest import chunk_document

        chunks = chunk_document("", clip_id=6669, date="2026-01-22",
                                meeting_body="Council", source="agenda")
        assert chunks == []

    def test_chunk_document_respects_word_limit(self):
        """Long documents should be split into ~500-word chunks."""
        from api.ingest import chunk_document

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
        from api.ingest import store_chunks

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
        from api.ingest import store_chunks

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
        from api.ingest import store_chunks

        chunks = [
            {"text": "Chunk A", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Overview"},
            {"text": "Chunk B", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Votes"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        assert chroma_collection.count() == 2

    def test_store_chunks_calls_openai_embeddings(self, chroma_collection, mock_openai_batch_embeddings):
        from api.ingest import store_chunks

        chunks = [
            {"text": "Test embedding call", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "summary", "section_type": "Overview"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)
        mock_openai_batch_embeddings.embeddings.create.assert_called()

    def test_store_chunks_records_embedding_cost_for_tenant(self, chroma_collection, mock_openai_client):
        from api.ingest import EMBEDDING_MODEL, store_chunks

        embed_response = MagicMock()
        embed_response.data = [MagicMock(embedding=[0.1] * 1536)]
        embed_response.usage = MagicMock(total_tokens=123)
        mock_openai_client.embeddings.create.return_value = embed_response

        tracker = MagicMock()
        with patch("api.cost.get_cost_tracker", return_value=tracker):
            store_chunks(
                [{
                    "text": "Tenant-specific chunk",
                    "clip_id": 6669,
                    "date": "2026-01-22",
                    "meeting_body": "Council",
                    "source": "summary",
                    "section_type": "Overview",
                    "tenant_id": "tenant-a",
                }],
                chroma_collection,
                mock_openai_client,
            )

        tracker.record_embedding.assert_called_once_with(
            tenant_id="tenant-a",
            model=EMBEDDING_MODEL,
            tokens=123,
            request_id=None,
            operation="rag.ingest.embed_chunks",
        )


# ============================================================
# 6. Full clip ingestion tests
# ============================================================

class TestIngestClip:
    """Test ingesting a single clip from its output directory."""

    def test_ingest_clip_processes_all_sources(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from api.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        # Should have chunks from all sources: summary + transcript + agenda + minutes
        assert chroma_collection.count() > 0
        # Verify summary chunks are now ingested
        results = chroma_collection.get(where={"source": "summary"}, include=["metadatas"])
        assert len(results["ids"]) > 0

    def test_ingest_clip_stores_transcript_chunks(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from api.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        results = chroma_collection.get(where={"source": "transcript"}, include=["metadatas"])
        assert len(results["ids"]) >= 1

    def test_ingest_clip_stores_minutes_chunks(self, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings):
        from api.ingest import ingest_clip

        ingest_clip(6669, sample_clip_dir, chroma_collection, mock_openai_batch_embeddings)
        results = chroma_collection.get(where={"source": "minutes"}, include=["metadatas"])
        assert len(results["ids"]) >= 1

    def test_ingest_clip_skips_missing_files_gracefully(self, tmp_path, chroma_collection, mock_openai_batch_embeddings):
        """If a clip directory has only metadata and minutes, it should still work."""
        from api.ingest import ingest_clip

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
        from api.ingest import ingest_clip, load_rag_state, save_rag_state

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
        from api.ingest import load_rag_state

        state = load_rag_state(tmp_path)
        assert "ingested_clips" in state
        assert state["ingested_clips"] == []

    def test_save_and_load_rag_state_roundtrip(self, tmp_path):
        from api.ingest import load_rag_state, save_rag_state

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
        from api.ingest import store_chunks, get_stats

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
        assert stats["unique_clips"] == 2

    def test_get_stats_with_output_dir_uses_rag_state(self, tmp_path, chroma_collection, mock_openai_batch_embeddings):
        from api.ingest import store_chunks, get_stats, save_rag_state

        chunks = [
            {"text": "Chunk 1", "clip_id": 6669, "date": "2026-01-22",
             "meeting_body": "Council", "source": "transcript",
             "start_time": 0.0, "end_time": 60.0},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)

        # Save rag state with 3 clips (even though collection only has 1)
        save_rag_state({"ingested_clips": [6669, 6670, 6671]}, tmp_path)
        stats = get_stats(chroma_collection, output_dir=str(tmp_path))
        assert stats["total_chunks"] == 1
        assert stats["unique_clips"] == 3  # from rag_state, not collection


# ============================================================
# 9. Text truncation tests
# ============================================================

class TestTruncateText:
    """Test _truncate_text character-based truncation."""

    def test_short_text_returned_unchanged(self):
        from api.ingest import _truncate_text

        text = "Hello world"
        assert _truncate_text(text, max_chars=100) == text

    def test_text_at_exact_limit_returned_unchanged(self):
        from api.ingest import _truncate_text

        text = "a" * 100
        assert _truncate_text(text, max_chars=100) == text

    def test_long_text_truncated_at_word_boundary(self):
        from api.ingest import _truncate_text

        text = "word " * 100  # 500 chars
        result = _truncate_text(text, max_chars=50)
        assert len(result) <= 50
        assert not result.endswith(" ")  # should trim trailing space from word boundary

    def test_no_spaces_falls_back_to_char_limit(self):
        from api.ingest import _truncate_text

        text = "a" * 200  # no spaces at all
        result = _truncate_text(text, max_chars=100)
        assert len(result) == 100

    def test_spaces_only_in_first_half_uses_char_limit(self):
        from api.ingest import _truncate_text

        # Space at position 10, then no spaces for the rest
        text = "short word" + "x" * 190  # space at index 5
        result = _truncate_text(text, max_chars=100)
        # last_space = 5, which is <= max_chars//2 (50), so falls back to text[:100]
        assert len(result) == 100


# ============================================================
# 10. Embedding resilience tests
# ============================================================

class TestEmbedSingle:
    """Test _embed_single progressive truncation on token limit errors."""

    def test_happy_path_first_call_succeeds(self):
        from api.ingest import _embed_single

        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=[0.1] * 10)]
        client.embeddings.create.return_value = mock_resp

        result = _embed_single("Hello world", client)
        assert result == [0.1] * 10
        assert client.embeddings.create.call_count == 1

    def test_retries_on_token_limit_error(self):
        from api.ingest import _embed_single

        client = MagicMock()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("maximum context length exceeded")
            mock_resp = MagicMock()
            mock_resp.data = [MagicMock(embedding=[0.2] * 10)]
            return mock_resp

        client.embeddings.create.side_effect = side_effect

        result = _embed_single("x" * 20000, client)
        assert result == [0.2] * 10
        assert call_count[0] == 2

    def test_last_resort_2000_char_truncation(self):
        from api.ingest import _embed_single

        client = MagicMock()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            text_input = kwargs.get("input", [""])[0]
            # Always fail on token limit except when text is <= 2000 chars
            if len(text_input) > 2000:
                raise Exception("maximum context length exceeded")
            mock_resp = MagicMock()
            mock_resp.data = [MagicMock(embedding=[0.3] * 10)]
            return mock_resp

        client.embeddings.create.side_effect = side_effect

        result = _embed_single("x" * 50000, client)
        assert result == [0.3] * 10
        # Should have retried multiple times before hitting last resort
        assert call_count[0] > 2

    def test_non_token_limit_error_raises(self):
        from api.ingest import _embed_single

        client = MagicMock()
        client.embeddings.create.side_effect = Exception("network error")

        with pytest.raises(Exception, match="network error"):
            _embed_single("Hello", client)


class TestEmbedBatch:
    """Test _embed_batch fallback to one-at-a-time on token limit errors."""

    def test_batch_succeeds_returns_all_embeddings(self):
        from api.ingest import _embed_batch

        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [
            MagicMock(embedding=[0.1] * 10),
            MagicMock(embedding=[0.2] * 10),
        ]
        client.embeddings.create.return_value = mock_resp

        result = _embed_batch(["text1", "text2"], client)
        assert len(result) == 2

    def test_batch_fallback_to_single_on_token_limit(self):
        from api.ingest import _embed_batch

        client = MagicMock()
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            inputs = kwargs.get("input", [])
            if isinstance(inputs, list) and len(inputs) > 1:
                raise Exception("maximum context length exceeded")
            mock_resp = MagicMock()
            mock_resp.data = [MagicMock(embedding=[0.1] * 10)]
            return mock_resp

        client.embeddings.create.side_effect = side_effect

        result = _embed_batch(["text1", "text2"], client)
        assert len(result) == 2
        # First call was batch (failed), then 2 individual calls
        assert call_count[0] == 3

    def test_non_token_limit_error_raises(self):
        from api.ingest import _embed_batch

        client = MagicMock()
        client.embeddings.create.side_effect = RuntimeError("connection refused")

        with pytest.raises(RuntimeError, match="connection refused"):
            _embed_batch(["text1"], client)


# ============================================================
# 11. Chunk ID tests
# ============================================================

class TestChunkId:
    """Test deterministic chunk ID generation."""

    def test_same_chunk_produces_same_id(self):
        from api.ingest import _chunk_id

        chunk = {"clip_id": 6669, "source": "transcript", "text": "Hello world"}
        id1 = _chunk_id(chunk, 0)
        id2 = _chunk_id(chunk, 0)
        assert id1 == id2

    def test_different_text_produces_different_id(self):
        from api.ingest import _chunk_id

        chunk1 = {"clip_id": 6669, "source": "transcript", "text": "Hello world"}
        chunk2 = {"clip_id": 6669, "source": "transcript", "text": "Goodbye world"}
        id1 = _chunk_id(chunk1, 0)
        id2 = _chunk_id(chunk2, 0)
        assert id1 != id2

    def test_different_clip_produces_different_id(self):
        from api.ingest import _chunk_id

        chunk1 = {"clip_id": 6669, "source": "transcript", "text": "Hello world"}
        chunk2 = {"clip_id": 6670, "source": "transcript", "text": "Hello world"}
        assert _chunk_id(chunk1, 0) != _chunk_id(chunk2, 0)

    def test_different_source_produces_different_id(self):
        from api.ingest import _chunk_id

        chunk1 = {"clip_id": 6669, "source": "transcript", "text": "Hello world"}
        chunk2 = {"clip_id": 6669, "source": "minutes", "text": "Hello world"}
        assert _chunk_id(chunk1, 0) != _chunk_id(chunk2, 0)


# ============================================================
# 12. Clean segments Pass 3 (song lyrics) tests
# ============================================================

class TestCleanSegmentsPass3:
    """Test the song lyrics tail-stripping heuristic."""

    def test_strips_short_poetic_tail_without_procedural(self):
        from api.ingest import clean_segments

        # Build 100 normal segments, then 12 short poetic lines in last 10%
        segments = []
        for i in range(90):
            segments.append({
                "start": float(i),
                "end": float(i + 0.9),
                "text": f"Normal discussion about item {i} in the meeting today."
            })
        for i in range(12):
            segments.append({
                "start": float(90 + i),
                "end": float(91 + i),
                "text": f"La la la line {i}"  # short, no procedural
            })

        cleaned = clean_segments(segments)
        # Tail should be stripped — only ~90 segments remain
        assert len(cleaned) <= 91

    def test_preserves_tail_with_procedural_phrases(self):
        from api.ingest import clean_segments

        segments = []
        for i in range(90):
            segments.append({
                "start": float(i),
                "end": float(i + 0.9),
                "text": f"Normal discussion about item {i} in the meeting today."
            })
        # Last 10% contains a procedural phrase
        for i in range(12):
            text = f"Short line {i}"
            if i == 5:
                text = "Next item on the roll call."
            segments.append({
                "start": float(90 + i),
                "end": float(91 + i),
                "text": text,
            })

        cleaned = clean_segments(segments)
        # Tail should NOT be stripped because of procedural phrase
        assert len(cleaned) == 102

    def test_pass3_requires_minimum_100_segments(self):
        from api.ingest import clean_segments

        # Only 50 segments — Pass 3 needs >= 10 to even check,
        # but tail of 50*0.1=5 segments, needs 10 short poetic, can't trigger
        segments = []
        for i in range(50):
            segments.append({
                "start": float(i),
                "end": float(i + 0.9),
                "text": f"Short {i}"
            })
        cleaned = clean_segments(segments)
        # With 50 segments, tail = 5. Can't have 10 short poetic in 5 segments.
        assert len(cleaned) == 50
