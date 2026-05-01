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


# ============================================================
# 8. build_chat_synthesis_messages tests
# ============================================================

class TestBuildChatSynthesisMessages:
    """Test building multi-turn chat synthesis messages."""

    def test_includes_conversation_history(self):
        from rag.query import build_chat_synthesis_messages

        history = [
            {"role": "user", "content": "What about zoning?"},
            {"role": "assistant", "content": "Zoning was discussed in clip 6669."},
            {"role": "user", "content": "Tell me more about that vote."},
        ]
        chunks = [{"text": "Test chunk", "clip_id": 6669, "date": "2026-01-22",
                    "meeting_body": "Council", "source": "summary", "title": "Test"}]

        result = build_chat_synthesis_messages(history, chunks)
        # System + 2 history messages + augmented last user message = 4 total
        assert result[0]["role"] == "system"
        non_system = result[1:]
        assert len(non_system) == 3
        # First two are history (user + assistant), last is augmented user
        assert non_system[0]["role"] == "user"
        assert non_system[1]["role"] == "assistant"
        assert non_system[2]["role"] == "user"
        assert "Meeting excerpts:" in non_system[2]["content"]

    def test_injects_context_in_last_user_message(self):
        from rag.query import build_chat_synthesis_messages

        history = [
            {"role": "user", "content": "What about zoning?"},
            {"role": "assistant", "content": "Zoning was discussed."},
            {"role": "user", "content": "Tell me more."},
        ]
        chunks = [{"text": "Zoning chunk", "clip_id": 6669, "date": "2026-01-22",
                    "meeting_body": "Council", "source": "summary", "title": "Test"}]

        result = build_chat_synthesis_messages(history, chunks)
        # Only the last message should have "Meeting excerpts:"
        last_msg = result[-1]
        assert "Meeting excerpts:" in last_msg["content"]
        # Earlier user messages should NOT have context injected
        for msg in result[1:-1]:
            if msg["role"] == "user":
                assert "Meeting excerpts:" not in msg["content"]

    def test_strips_extra_keys_from_history(self):
        from rag.query import build_chat_synthesis_messages

        history = [
            {"role": "user", "content": "test", "sources": [], "model": "gpt-4o", "timestamp": "2026-01-01"},
        ]
        chunks = [{"text": "chunk", "clip_id": 1, "date": "d", "meeting_body": "m",
                    "source": "summary", "title": "t"}]

        result = build_chat_synthesis_messages(history, chunks)
        for msg in result:
            assert set(msg.keys()) == {"role", "content"}

    def test_trims_to_max_history(self):
        from rag.query import MAX_HISTORY_PAIRS, build_chat_synthesis_messages

        # Create 30 messages (15 pairs) + final user message = 31 messages
        history = []
        for i in range(15):
            history.append({"role": "user", "content": f"Question {i}"})
            history.append({"role": "assistant", "content": f"Answer {i}"})
        history.append({"role": "user", "content": "Final question"})

        chunks = [{"text": "chunk", "clip_id": 1, "date": "d", "meeting_body": "m",
                    "source": "summary", "title": "t"}]

        result = build_chat_synthesis_messages(history, chunks)
        # Should be: system + trimmed history (MAX_HISTORY_PAIRS * 2 - 1) + augmented last
        # = 1 + MAX_HISTORY_PAIRS * 2
        assert len(result) <= MAX_HISTORY_PAIRS * 2 + 1 + 1

    def test_single_message_works(self):
        from rag.query import build_chat_synthesis_messages

        history = [{"role": "user", "content": "What happened?"}]
        chunks = [{"text": "Meeting content", "clip_id": 6669, "date": "2026-01-22",
                    "meeting_body": "Council", "source": "summary", "title": "Test"}]

        result = build_chat_synthesis_messages(history, chunks)
        assert result[0]["role"] == "system"
        assert result[-1]["role"] == "user"
        assert "Meeting excerpts:" in result[-1]["content"]
        assert "What happened?" in result[-1]["content"]


# ============================================================
# 9. synthesize_with_anthropic tests
# ============================================================

class TestSynthesizeWithAnthropic:
    """Test Anthropic synthesis routing."""

    def test_calls_anthropic_with_correct_format(self, mock_anthropic_client):
        from rag.query import synthesize_with_anthropic

        messages = [
            {"role": "system", "content": "You are a research assistant."},
            {"role": "user", "content": "What about zoning?"},
        ]

        synthesize_with_anthropic(messages, mock_anthropic_client)

        call_kwargs = mock_anthropic_client.messages.create.call_args
        # System should be a top-level param, not in messages
        assert call_kwargs.kwargs["system"] == "You are a research assistant."
        # Messages passed should not include the system role
        for msg in call_kwargs.kwargs["messages"]:
            assert msg["role"] != "system"

    def test_returns_response_text(self, mock_anthropic_client):
        from rag.query import synthesize_with_anthropic

        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Test question."},
        ]

        result = synthesize_with_anthropic(messages, mock_anthropic_client)
        assert result == "This is an Anthropic-synthesized answer."


# ============================================================
# 10. chat() function tests
# ============================================================

class TestChat:
    """Test the multi-turn chat() function."""

    def _make_mock_collection(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 1
        mock_collection.query.return_value = {
            "ids": [["id1"]],
            "documents": [["Test document about zoning"]],
            "metadatas": [[{"clip_id": 6669, "date": "2026-01-08",
                            "meeting_body": "Council", "source": "summary",
                            "title": "Test Meeting"}]],
            "distances": [[0.5]],
        }
        return mock_collection

    def test_chat_returns_correct_structure(self, mock_openai_client):
        from rag.query import chat

        mock_collection = self._make_mock_collection()
        clip_metadata = {6669: {"title": "Test Meeting", "date": "2026-01-08",
                                 "meeting_body": "Council"}}

        result = chat(
            messages=[{"role": "user", "content": "What about zoning?"}],
            collection=mock_collection,
            openai_client=mock_openai_client,
            clip_metadata=clip_metadata,
        )

        assert "role" in result
        assert "content" in result
        assert "sources" in result
        assert "model_used" in result
        assert "filters_applied" in result
        assert "chunks_retrieved" in result
        assert result["role"] == "assistant"

    def test_chat_with_openai(self, mock_openai_client):
        from rag.query import chat

        mock_collection = self._make_mock_collection()
        clip_metadata = {6669: {"title": "Test Meeting", "date": "2026-01-08",
                                 "meeting_body": "Council"}}

        result = chat(
            messages=[{"role": "user", "content": "test"}],
            collection=mock_collection,
            openai_client=mock_openai_client,
            clip_metadata=clip_metadata,
            model_provider="openai",
        )

        assert result["model_used"] == "gpt-4o"
        # 2 calls: query rewrite (gpt-4o-mini) + synthesis (gpt-4o)
        assert mock_openai_client.chat.completions.create.call_count == 2

    def test_chat_with_anthropic(self, mock_openai_client, mock_anthropic_client):
        from rag.query import chat

        mock_collection = self._make_mock_collection()
        clip_metadata = {6669: {"title": "Test Meeting", "date": "2026-01-08",
                                 "meeting_body": "Council"}}

        result = chat(
            messages=[{"role": "user", "content": "test"}],
            collection=mock_collection,
            openai_client=mock_openai_client,
            clip_metadata=clip_metadata,
            anthropic_client=mock_anthropic_client,
            model_provider="anthropic",
        )

        assert result["model_used"] == "claude-sonnet"
        mock_anthropic_client.messages.create.assert_called_once()

    def test_chat_uses_latest_user_message_for_retrieval(self, mock_openai_client):
        from rag.query import chat

        mock_collection = self._make_mock_collection()
        clip_metadata = {6669: {"title": "Test Meeting", "date": "2026-01-08",
                                 "meeting_body": "Council"}}

        messages = [
            {"role": "user", "content": "First question about parks"},
            {"role": "assistant", "content": "Parks were discussed."},
            {"role": "user", "content": "What about the zoning vote?"},
        ]

        chat(
            messages=messages,
            collection=mock_collection,
            openai_client=mock_openai_client,
            clip_metadata=clip_metadata,
        )

        # The embedding should be created from the LAST user message
        embed_call = mock_openai_client.embeddings.create.call_args
        embed_input = embed_call.kwargs.get("input") or embed_call[1].get("input")
        assert embed_input == ["What about the zoning vote?"]


# ============================================================
# Query rewrite tests — exercise the actual rewrite_query path
# ============================================================

class TestRewriteQuery:
    """Tests for rewrite_query() — the LLM-based query expansion that the
    pre-existing chat tests bypass via the JSON-decode fallback."""

    def _client_returning(self, content: str):
        """Build a mock OpenAI client whose chat.completions.create returns
        ``content`` as the message body."""
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response
        return client

    def test_returns_parsed_list_for_valid_json(self):
        from rag.query import rewrite_query

        client = self._client_returning('["zoning vote", "rezoning ordinance", "council vote"]')
        out = rewrite_query("Tell me about the zoning vote", client)
        assert out == ["zoning vote", "rezoning ordinance", "council vote"]

    def test_caps_at_max_rewritten_queries(self):
        from rag.query import rewrite_query, MAX_REWRITTEN_QUERIES

        # LLM returns 8 queries — should be truncated.
        many = [f"q{i}" for i in range(8)]
        client = self._client_returning(json.dumps(many))
        out = rewrite_query("anything", client)
        assert len(out) == MAX_REWRITTEN_QUERIES
        assert out == many[:MAX_REWRITTEN_QUERIES]

    def test_falls_back_to_original_on_json_decode_error(self):
        from rag.query import rewrite_query

        client = self._client_returning("not json at all { broken")
        out = rewrite_query("original question", client)
        assert out == ["original question"]

    def test_falls_back_to_original_on_api_exception(self):
        from rag.query import rewrite_query

        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("upstream timeout")
        out = rewrite_query("original question", client)
        assert out == ["original question"]

    def test_falls_back_when_response_is_not_a_list(self):
        from rag.query import rewrite_query

        # Valid JSON but wrong shape — a dict, not a list of strings.
        client = self._client_returning('{"queries": ["a", "b"]}')
        out = rewrite_query("original", client)
        assert out == ["original"]

    def test_falls_back_when_list_contains_non_strings(self):
        from rag.query import rewrite_query

        client = self._client_returning('["good query", 42, null]')
        out = rewrite_query("original", client)
        assert out == ["original"]

    def test_falls_back_on_empty_list(self):
        from rag.query import rewrite_query

        client = self._client_returning("[]")
        out = rewrite_query("original", client)
        assert out == ["original"]

    def test_uses_gpt_4o_mini(self):
        """The rewrite pass should use the cheap model — GPT-4o is reserved
        for synthesis."""
        from rag.query import rewrite_query

        client = self._client_returning('["q"]')
        rewrite_query("anything", client)
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"
