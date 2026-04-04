"""Tests for rag/query.py - Retrieval, filtering, deduplication, and synthesis."""

import json
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# 1. Filter building tests
# ============================================================

class TestBuildChromaFilter:
    """Test building ChromaDB where clauses from filter dicts."""

    def test_no_filters_returns_none(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter(None)
        assert result is None

    def test_empty_filters_returns_none(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({})
        assert result is None

    def test_meeting_body_filter(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({"meeting_body": "Council"})
        assert result == {"meeting_body": "Council"}

    def test_date_after_filter(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({"date_after": "2025-01-01"})
        assert result == {"date": {"$gte": "2025-01-01"}}

    def test_date_before_filter(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({"date_before": "2026-01-01"})
        assert result == {"date": {"$lte": "2026-01-01"}}

    def test_combined_filters(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({
            "meeting_body": "Council",
            "date_after": "2025-01-01",
            "date_before": "2026-06-01",
        })
        assert "$and" in result
        conditions = result["$and"]
        assert {"meeting_body": "Council"} in conditions
        assert {"date": {"$gte": "2025-01-01"}} in conditions
        assert {"date": {"$lte": "2026-06-01"}} in conditions

    def test_meeting_body_with_date_after(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({
            "meeting_body": "Committee",
            "date_after": "2025-06-01",
        })
        assert "$and" in result
        conditions = result["$and"]
        assert len(conditions) == 2


# ============================================================
# 2. Deduplication tests
# ============================================================

class TestDeduplicate:
    """Test deduplication: max 4 chunks per clip (default), max 2 per source, highest-scored first."""

    def test_dedup_respects_max_per_clip(self):
        from rag.query import deduplicate_results

        results = {
            "ids": [["a", "b", "c", "d", "e", "f"]],
            "documents": [["doc_a", "doc_b", "doc_c", "doc_d", "doc_e", "doc_f"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "minutes"},
                {"clip_id": 6669, "source": "agenda"},
                {"clip_id": 6669, "source": "facts"},
                {"clip_id": 6669, "source": "summary"},
                {"clip_id": 6670, "source": "minutes"},
            ]],
            "distances": [[0.1, 0.2, 0.3, 0.35, 0.4, 0.15]],
        }
        deduped = deduplicate_results(results)  # default max_per_clip=4
        clip_6669_count = sum(1 for m in deduped["metadatas"] if m["clip_id"] == 6669)
        assert clip_6669_count == 4  # keeps 4, drops the 5th

    def test_dedup_limits_per_source_within_clip(self):
        from rag.query import deduplicate_results

        # 4 transcript chunks from same clip — should keep only 2
        results = {
            "ids": [["a", "b", "c", "d"]],
            "documents": [["doc_a", "doc_b", "doc_c", "doc_d"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "transcript"},
            ]],
            "distances": [[0.1, 0.2, 0.3, 0.4]],
        }
        deduped = deduplicate_results(results)
        assert len(deduped["ids"]) == 2  # max_per_source_per_clip=2

    def test_dedup_keeps_highest_scored_first(self):
        from rag.query import deduplicate_results

        results = {
            "ids": [["a", "b", "c", "d"]],
            "documents": [["worst", "best", "middle", "okay"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "minutes"},
                {"clip_id": 6669, "source": "agenda"},
                {"clip_id": 6669, "source": "facts"},
            ]],
            "distances": [[0.5, 0.1, 0.3, 0.4]],  # lower distance = better
        }
        deduped = deduplicate_results(results, max_per_clip=3)
        # Should keep the three with lowest distances (0.1, 0.3, 0.4)
        assert len(deduped["documents"]) == 3
        assert "best" in deduped["documents"]
        assert "middle" in deduped["documents"]
        assert "okay" in deduped["documents"]
        assert "worst" not in deduped["documents"]

    def test_dedup_preserves_different_clips(self):
        from rag.query import deduplicate_results

        results = {
            "ids": [["a", "b"]],
            "documents": [["doc_a", "doc_b"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6670, "source": "minutes"},
            ]],
            "distances": [[0.1, 0.2]],
        }
        deduped = deduplicate_results(results, max_per_clip=4)
        assert len(deduped["documents"]) == 2


# ============================================================
# 3. Synthesis prompt construction tests
# ============================================================

class TestBuildSynthesisPrompt:
    """Test that the synthesis prompt is well-formed."""

    def test_prompt_includes_question(self):
        from rag.query import build_synthesis_messages

        messages = build_synthesis_messages(
            question="What about zoning?",
            chunks=[{
                "text": "Zoning discussion content",
                "clip_id": 6669,
                "date": "2026-01-22",
                "meeting_body": "Council",
                "source": "summary",
                "title": "Urban County Council (1)",
            }],
        )
        # Should have system and user messages
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "What about zoning?" in user_msg["content"]

    def test_prompt_includes_chunk_context(self):
        from rag.query import build_synthesis_messages

        messages = build_synthesis_messages(
            question="What about zoning?",
            chunks=[{
                "text": "Zoning ordinance passed 8-0",
                "clip_id": 6669,
                "date": "2026-01-22",
                "meeting_body": "Council",
                "source": "summary",
                "title": "Urban County Council (1)",
            }],
        )
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Zoning ordinance passed 8-0" in user_msg["content"]
        assert "6669" in user_msg["content"]
        assert "2026-01-22" in user_msg["content"]

    def test_prompt_includes_timestamp_for_transcript(self):
        from rag.query import build_synthesis_messages

        messages = build_synthesis_messages(
            question="What was discussed?",
            chunks=[{
                "text": "Transcript passage here",
                "clip_id": 6669,
                "date": "2026-01-22",
                "meeting_body": "Council",
                "source": "transcript",
                "title": "Urban County Council (1)",
                "start_time": 60.0,
                "end_time": 120.0,
            }],
        )
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "1:00-2:00" in user_msg["content"]  # timestamp should appear as MM:SS

    def test_system_prompt_instructs_citation(self):
        from rag.query import build_synthesis_messages

        messages = build_synthesis_messages(
            question="test",
            chunks=[{"text": "t", "clip_id": 1, "date": "d", "meeting_body": "m",
                      "source": "summary", "title": "t"}],
        )
        system_msg = messages[0]["content"]
        assert "cite" in system_msg.lower() or "citation" in system_msg.lower()


# ============================================================
# 4. Full ask() flow tests
# ============================================================

class TestAsk:
    """Test the full ask() function with mocked dependencies."""

    def test_ask_returns_answer_and_sources(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.query import ask
        from rag.ingest import store_chunks

        # Seed the collection with some chunks
        chunks = [
            {"text": "Zoning ordinance 0016-26 passed 8-0 for Canebrake Dr.",
             "clip_id": 6669, "date": "2026-01-22", "meeting_body": "Council",
             "source": "summary", "section_type": "Key Decisions"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)

        # Mock the chat completion for synthesis
        mock_choice = MagicMock()
        mock_choice.message.content = "The zoning ordinance was passed 8-0."
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]
        mock_openai_batch_embeddings.chat.completions.create.return_value = mock_chat_resp

        # We also need clip metadata for title lookup
        result = ask(
            question="What about zoning?",
            collection=chroma_collection,
            openai_client=mock_openai_batch_embeddings,
            clip_metadata={6669: {"title": "Urban County Council (1)", "date": "2026-01-22",
                                   "meeting_body": "Council"}},
        )

        assert "answer" in result
        assert "sources" in result
        assert isinstance(result["sources"], list)

    def test_ask_response_has_correct_structure(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.query import ask
        from rag.ingest import store_chunks

        chunks = [
            {"text": "Budget discussion for parks.",
             "clip_id": 6670, "date": "2026-01-23", "meeting_body": "Committee",
             "source": "transcript", "start_time": 120.0, "end_time": 180.0},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)

        mock_choice = MagicMock()
        mock_choice.message.content = "Parks budget was discussed."
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]
        mock_openai_batch_embeddings.chat.completions.create.return_value = mock_chat_resp

        result = ask(
            question="What about parks?",
            collection=chroma_collection,
            openai_client=mock_openai_batch_embeddings,
            clip_metadata={6670: {"title": "Committee Meeting", "date": "2026-01-23",
                                   "meeting_body": "Committee"}},
        )

        assert "answer" in result
        assert "sources" in result
        assert "chunks_retrieved" in result
        # Check source structure
        if result["sources"]:
            source = result["sources"][0]
            assert "clip_id" in source
            assert "date" in source
            assert "granicus_url" in source

    def test_ask_with_no_results_returns_clear_message(self, mock_openai_batch_embeddings):
        """When collection is empty, should return a 'not enough info' answer."""
        import chromadb
        from rag.query import ask

        client = chromadb.Client()
        empty_collection = client.get_or_create_collection("empty_test")

        mock_choice = MagicMock()
        mock_choice.message.content = "Not enough information."
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]
        mock_openai_batch_embeddings.chat.completions.create.return_value = mock_chat_resp

        result = ask(
            question="random query",
            collection=empty_collection,
            openai_client=mock_openai_batch_embeddings,
            clip_metadata={},
        )

        assert "answer" in result
        client.delete_collection("empty_test")

    def test_ask_passes_filters_to_chromadb(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.query import ask
        from rag.ingest import store_chunks

        chunks = [
            {"text": "Council meeting about zoning",
             "clip_id": 6669, "date": "2026-01-22", "meeting_body": "Council",
             "source": "summary", "section_type": "Overview"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)

        mock_choice = MagicMock()
        mock_choice.message.content = "Answer."
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]
        mock_openai_batch_embeddings.chat.completions.create.return_value = mock_chat_resp

        result = ask(
            question="zoning",
            collection=chroma_collection,
            openai_client=mock_openai_batch_embeddings,
            filters={"meeting_body": "Council"},
            clip_metadata={6669: {"title": "Council", "date": "2026-01-22",
                                   "meeting_body": "Council"}},
        )

        assert "filters_applied" in result

    def test_ask_calls_openai_chat_with_wellformed_messages(self, chroma_collection, mock_openai_batch_embeddings):
        from rag.query import ask
        from rag.ingest import store_chunks

        chunks = [
            {"text": "Test content",
             "clip_id": 6669, "date": "2026-01-22", "meeting_body": "Council",
             "source": "summary", "section_type": "Overview"},
        ]
        store_chunks(chunks, chroma_collection, mock_openai_batch_embeddings)

        mock_choice = MagicMock()
        mock_choice.message.content = "Answer."
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]
        mock_openai_batch_embeddings.chat.completions.create.return_value = mock_chat_resp

        ask(
            question="test",
            collection=chroma_collection,
            openai_client=mock_openai_batch_embeddings,
            clip_metadata={6669: {"title": "Council", "date": "2026-01-22",
                                   "meeting_body": "Council"}},
        )

        # Verify the chat completion was called with proper messages
        call_kwargs = mock_openai_batch_embeddings.chat.completions.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        assert messages[0]["role"] == "system"
        assert any(m["role"] == "user" for m in messages)


# ============================================================
# 5. build_synthesis_messages edge cases
# ============================================================

class TestBuildSynthesisEdgeCases:
    """Test edge cases in synthesis message building."""

    def test_empty_chunks_produces_valid_messages(self):
        from rag.query import build_synthesis_messages

        messages = build_synthesis_messages(question="What happened?", chunks=[])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "What happened?" in messages[1]["content"]


# ============================================================
# 6. load_clip_metadata tests
# ============================================================

class TestLoadClipMetadata:
    """Test loading clip metadata from disk."""

    def test_missing_directory_returns_empty(self, tmp_path):
        from rag.query import load_clip_metadata

        result = load_clip_metadata(str(tmp_path / "nonexistent"))
        assert result == {}

    def test_loads_valid_metadata(self, tmp_path):
        from rag.query import load_clip_metadata

        clips_dir = tmp_path / "clips" / "6669"
        clips_dir.mkdir(parents=True)
        meta = {"clip_id": 6669, "title": "Council Meeting", "date": "2026-01-22"}
        (clips_dir / "metadata.json").write_text(json.dumps(meta))

        result = load_clip_metadata(str(tmp_path))
        assert 6669 in result
        assert result[6669]["title"] == "Council Meeting"

    def test_skips_malformed_json(self, tmp_path):
        from rag.query import load_clip_metadata

        clips_dir = tmp_path / "clips" / "6669"
        clips_dir.mkdir(parents=True)
        (clips_dir / "metadata.json").write_text("{invalid json")

        # Should not raise, just skip
        result = load_clip_metadata(str(tmp_path))
        assert result == {}

    def test_skips_metadata_missing_clip_id(self, tmp_path):
        from rag.query import load_clip_metadata

        clips_dir = tmp_path / "clips" / "6669"
        clips_dir.mkdir(parents=True)
        meta = {"title": "No clip_id field"}
        (clips_dir / "metadata.json").write_text(json.dumps(meta))

        result = load_clip_metadata(str(tmp_path))
        assert result == {}

    def test_loads_multiple_clips(self, tmp_path):
        from rag.query import load_clip_metadata

        for clip_id in [6669, 6670, 6671]:
            clip_dir = tmp_path / "clips" / str(clip_id)
            clip_dir.mkdir(parents=True)
            meta = {"clip_id": clip_id, "title": f"Meeting {clip_id}"}
            (clip_dir / "metadata.json").write_text(json.dumps(meta))

        result = load_clip_metadata(str(tmp_path))
        assert len(result) == 3
        assert all(cid in result for cid in [6669, 6670, 6671])


# ============================================================
# 7. Deduplication edge cases
# ============================================================

class TestDeduplicateEdgeCases:
    """Test deduplication edge cases."""

    def test_dedup_empty_results(self):
        from rag.query import deduplicate_results

        results = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        deduped = deduplicate_results(results)
        assert deduped["ids"] == []

    def test_dedup_no_ids_key(self):
        from rag.query import deduplicate_results

        results = {"ids": [], "documents": [], "metadatas": [], "distances": []}
        deduped = deduplicate_results(results)
        assert deduped["ids"] == []

    def test_dedup_with_explicit_max_per_clip_2(self):
        from rag.query import deduplicate_results

        results = {
            "ids": [["a", "b", "c"]],
            "documents": [["doc_a", "doc_b", "doc_c"]],
            "metadatas": [[
                {"clip_id": 6669, "source": "transcript"},
                {"clip_id": 6669, "source": "minutes"},
                {"clip_id": 6669, "source": "agenda"},
            ]],
            "distances": [[0.1, 0.2, 0.3]],
        }
        deduped = deduplicate_results(results, max_per_clip=2)
        assert len(deduped["ids"]) == 2
