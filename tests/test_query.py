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

    def test_date_only_filters_skip_chroma_clause(self):
        """Date filters are applied post-retrieval, not pushed into ChromaDB —
        the installed ChromaDB version rejects string $gte/$lte clauses."""
        from rag.query import build_chroma_filter

        assert build_chroma_filter({"date_after": "2025-01-01"}) is None
        assert build_chroma_filter({"date_before": "2026-01-01"}) is None
        assert build_chroma_filter({"date_after": "2025-01-01", "date_before": "2026-06-01"}) is None

    def test_combined_filters_only_pass_meeting_body(self):
        from rag.query import build_chroma_filter

        # meeting_body is the only non-date field, so the result is the body
        # condition alone — date filters are dropped (handled post-retrieval).
        result = build_chroma_filter({
            "meeting_body": "Council",
            "date_after": "2025-01-01",
            "date_before": "2026-06-01",
        })
        assert result == {"meeting_body": "Council"}

    def test_meeting_body_with_date_after(self):
        from rag.query import build_chroma_filter

        result = build_chroma_filter({
            "meeting_body": "Committee",
            "date_after": "2025-06-01",
        })
        assert result == {"meeting_body": "Committee"}


class TestDateInRange:
    """Date filtering happens post-retrieval, so the helper is exercised directly."""

    def test_no_filters_includes_everything(self):
        from rag.query import _date_in_range

        assert _date_in_range("2026-04-30", None, None) is True
        assert _date_in_range("", None, None) is True

    def test_date_after_excludes_earlier(self):
        from rag.query import _date_in_range

        assert _date_in_range("2025-12-31", "2026-01-01", None) is False
        assert _date_in_range("2026-01-01", "2026-01-01", None) is True
        assert _date_in_range("2026-04-30", "2026-01-01", None) is True

    def test_date_before_excludes_later(self):
        from rag.query import _date_in_range

        assert _date_in_range("2026-05-01", None, "2026-04-30") is False
        assert _date_in_range("2026-04-30", None, "2026-04-30") is True
        assert _date_in_range("2026-04-29", None, "2026-04-30") is True

    def test_combined_range(self):
        from rag.query import _date_in_range

        assert _date_in_range("2026-03-15", "2026-01-01", "2026-06-30") is True
        assert _date_in_range("2025-12-31", "2026-01-01", "2026-06-30") is False
        assert _date_in_range("2026-07-01", "2026-01-01", "2026-06-30") is False

    def test_empty_date_excluded_when_range_set(self):
        from rag.query import _date_in_range

        assert _date_in_range("", "2026-01-01", None) is False
        assert _date_in_range("", None, "2026-12-31") is False


class TestExtractTemporalSignals:
    """Pulls date filters and a recency hint out of a user's question so the
    RAG pipeline can narrow the chunk set without a separate filter UI."""

    def test_no_signals_returns_empty(self):
        from rag.query import extract_temporal_signals

        assert extract_temporal_signals("what did the council vote on") == {}

    def test_month_year_yields_full_month_range(self):
        from rag.query import extract_temporal_signals

        result = extract_temporal_signals("what happened in April 2026")
        assert result["date_after"] == "2026-04-01"
        assert result["date_before"] == "2026-04-30"

    def test_since_year_sets_only_lower_bound(self):
        from rag.query import extract_temporal_signals

        result = extract_temporal_signals("zoning changes since 2023")
        assert result.get("date_after") == "2023-01-01"
        assert "date_before" not in result

    def test_before_year_sets_upper_bound_to_end_of_prior_year(self):
        from rag.query import extract_temporal_signals

        result = extract_temporal_signals("ordinances before 2020")
        assert result.get("date_before") == "2019-12-31"
        assert "date_after" not in result

    def test_bare_year_yields_full_year_range(self):
        from rag.query import extract_temporal_signals

        result = extract_temporal_signals("budget in 2025")
        assert result["date_after"] == "2025-01-01"
        assert result["date_before"] == "2025-12-31"

    def test_recency_keyword_sets_prefer_recent(self):
        from rag.query import extract_temporal_signals

        for q in ["latest council meeting", "most recent vote", "what happened lately"]:
            assert extract_temporal_signals(q).get("prefer_recent") is True

    def test_february_handles_leap_and_non_leap(self):
        from rag.query import extract_temporal_signals

        # 2024 is a leap year — last day of Feb should be 29.
        leap = extract_temporal_signals("February 2024 meeting")
        assert leap["date_before"] == "2024-02-29"
        # 2025 is not — last day of Feb should be 28.
        non_leap = extract_temporal_signals("February 2025 meeting")
        assert non_leap["date_before"] == "2025-02-28"


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
        from rag.query import DEFAULT_ANTHROPIC_MODEL, chat

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

        # model_used now reports the actual configured model ID rather than
        # a hardcoded "claude-sonnet" label.
        assert result["model_used"] == DEFAULT_ANTHROPIC_MODEL
        mock_anthropic_client.messages.create.assert_called_once()

    def test_chat_uses_condensed_question_for_retrieval(self, mock_openai_client):
        from rag.query import chat

        mock_collection = self._make_mock_collection()
        clip_metadata = {6669: {"title": "Test Meeting", "date": "2026-01-08",
                                 "meeting_body": "Council"}}

        messages = [
            {"role": "user", "content": "First question about parks"},
            {"role": "assistant", "content": "Parks were discussed."},
            {"role": "user", "content": "What about the zoning vote?"},
        ]

        # Retrieval embeds the standalone (condensed) question, not the raw
        # follow-up — history-resolved entities are what make retrieval work.
        with patch("rag.query.condense_question",
                   return_value="What about the zoning vote?") as mock_condense:
            chat(
                messages=messages,
                collection=mock_collection,
                openai_client=mock_openai_client,
                clip_metadata=clip_metadata,
            )
        mock_condense.assert_called_once()

        embed_call = mock_openai_client.embeddings.create.call_args
        embed_input = embed_call.kwargs.get("input") or embed_call[1].get("input")
        assert embed_input == ["What about the zoning vote?"]

    def test_condense_question_single_message_passthrough(self, mock_openai_client):
        from rag.query import condense_question

        question = condense_question(
            [{"role": "user", "content": "What about parks?"}], mock_openai_client
        )
        assert question == "What about parks?"
        mock_openai_client.chat.completions.create.assert_not_called()

    def test_condense_question_failure_falls_back(self, mock_openai_client):
        from rag.query import condense_question

        mock_openai_client.chat.completions.create.side_effect = RuntimeError("api down")
        messages = [
            {"role": "user", "content": "First question about parks"},
            {"role": "assistant", "content": "Parks were discussed."},
            {"role": "user", "content": "What about the zoning vote?"},
        ]
        assert condense_question(messages, mock_openai_client) == "What about the zoning vote?"


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


# ============================================================
# Citation URL: honor non-Granicus canonical_url (PR-6 review fix #1)
# ============================================================

class TestClipCitationUrl:
    """clip_citation_url must keep Granicus byte-identical (fall back to the
    granicus deep-link) while citing a non-Granicus canonical_url verbatim."""

    def test_no_canonical_url_falls_back_to_granicus(self):
        """OLD already-ingested LFUCG chunks have no stored canonical_url."""
        from rag.query import clip_citation_url, granicus_clip_url
        assert clip_citation_url(6669, "", 90) == granicus_clip_url(6669, 90)

    def test_granicus_player_canonical_url_still_uses_deep_link(self):
        """A NEWLY ingested LFUCG chunk DOES carry a granicus player URL, but
        it must STILL resolve to the granicus_clip_url deep-link (with
        &entrytime=) — not the bare player URL — so LFUCG citations don't
        regress when re-ingested."""
        from rag.query import clip_citation_url, granicus_clip_url
        granicus_canonical = "https://lfucg.granicus.com/player/clip/6669?view_id=14&redirect=true"
        out = clip_citation_url(6669, granicus_canonical, 90)
        assert out == granicus_clip_url(6669, 90)
        assert "entrytime=90" in out

    def test_civicclerk_canonical_url_used_verbatim(self):
        """A Paris/CivicClerk clip cites its stored portal permalink as-is."""
        from rag.query import clip_citation_url
        cc = "https://parisky.portal.civicclerk.com/event/322"
        # Even with a non-zero timestamp arg, a doc-source URL is used verbatim
        # (no video deep-link exists for a document-driven record).
        assert clip_citation_url(5, cc, 90) == cc


class TestAskCitesCorrectUrl:
    """End-to-end through ask(): a granicus chunk cites the granicus URL; a
    civicclerk chunk cites its CivicClerk canonical_url."""

    def _synth(self, mock_client, text="answer"):
        choice = MagicMock()
        choice.message.content = text
        resp = MagicMock()
        resp.choices = [choice]
        mock_client.chat.completions.create.return_value = resp

    def test_granicus_clip_cites_granicus_url(
            self, chroma_collection, mock_openai_batch_embeddings):
        from rag.query import ask
        from rag.ingest import store_chunks

        # A granicus chunk carrying its (player-format) canonical_url.
        store_chunks([
            {"text": "Zoning ordinance passed 8-0.",
             "clip_id": 6669, "date": "2026-01-22", "meeting_body": "Council",
             "source": "summary", "section_type": "Key Decisions",
             "canonical_url": "https://lfucg.granicus.com/player/clip/6669?view_id=14&redirect=true"},
        ], chroma_collection, mock_openai_batch_embeddings)
        self._synth(mock_openai_batch_embeddings)

        result = ask(
            question="zoning?", collection=chroma_collection,
            openai_client=mock_openai_batch_embeddings,
            clip_metadata={6669: {"title": "Council", "date": "2026-01-22"}},
        )
        url = result["sources"][0]["granicus_url"]
        assert url.startswith("https://lfucg.granicus.com/player/clip/6669")
        assert "entrytime=" in url  # the deep-link form, not the bare permalink

    def test_civicclerk_clip_cites_civicclerk_url(
            self, chroma_collection, mock_openai_batch_embeddings):
        from rag.query import ask
        from rag.ingest import store_chunks

        store_chunks([
            {"text": "Motion to approve the budget passed 5-0.",
             "clip_id": 5, "date": "2026-05-12", "meeting_body": "City Commission",
             "source": "transcript", "start_time": 0.0, "end_time": 0.0,
             "transcript_source": "civicclerk_minutes",
             "canonical_url": "https://parisky.portal.civicclerk.com/event/322"},
        ], chroma_collection, mock_openai_batch_embeddings)
        self._synth(mock_openai_batch_embeddings)

        result = ask(
            question="budget?", collection=chroma_collection,
            openai_client=mock_openai_batch_embeddings,
            clip_metadata={5: {"title": "City Commission Meeting", "date": "2026-05-12"}},
        )
        assert result["sources"][0]["granicus_url"] == (
            "https://parisky.portal.civicclerk.com/event/322")
        # Crucially NOT a granicus URL.
        assert "granicus.com" not in result["sources"][0]["granicus_url"]


# ============================================================
# Grounding / anti-hallucination guards
# ============================================================

class TestGroundingGuards:
    """Distance gate, zero-chunk short-circuit, citation verification,
    deterministic synthesis params."""

    def _collection_with_distances(self, distances):
        mock_collection = MagicMock()
        mock_collection.count.return_value = len(distances)
        mock_collection.query.return_value = {
            "ids": [[f"id{i}" for i in range(len(distances))]],
            "documents": [[f"doc {i}" for i in range(len(distances))]],
            "metadatas": [[{"clip_id": 6000 + i, "date": "2026-01-08",
                            "meeting_body": "Council", "source": "summary"}
                           for i in range(len(distances))]],
            "distances": [list(distances)],
        }
        return mock_collection

    def test_far_chunks_never_reach_llm(self, mock_openai_client):
        from rag.query import MAX_DISTANCE, ask

        collection = self._collection_with_distances([MAX_DISTANCE + 0.05,
                                                      MAX_DISTANCE + 0.1])
        result = ask("Did the council ban pit bulls?", collection, mock_openai_client)

        # Everything was gated out → canned no-coverage answer, no LLM call.
        assert result["chunks_retrieved"] == 0
        assert result["sources"] == []
        mock_openai_client.chat.completions.create.assert_called_once()  # rewrite only

    def test_zero_chunks_short_circuits_without_synthesis(self, mock_openai_client):
        from rag.query import NO_COVERAGE_ANSWER, ask

        collection = self._collection_with_distances([0.2])
        # Date filter excludes the only chunk.
        result = ask("parks", collection, mock_openai_client,
                     filters={"date_after": "2027-01-01"})

        assert result["answer"] == NO_COVERAGE_ANSWER
        assert result["sources"] == []

    def test_synthesis_uses_low_temperature_and_max_tokens(self, mock_openai_client):
        from rag.query import SYNTHESIS_MAX_TOKENS, SYNTHESIS_TEMPERATURE, ask

        collection = self._collection_with_distances([0.2])
        ask("parks", collection, mock_openai_client)

        synth_call = mock_openai_client.chat.completions.create.call_args
        assert synth_call.kwargs["temperature"] == SYNTHESIS_TEMPERATURE
        assert synth_call.kwargs["max_tokens"] == SYNTHESIS_MAX_TOKENS

    def test_final_context_capped_at_top_k(self, mock_openai_client):
        from rag.query import ask

        collection = self._collection_with_distances([0.1 + i * 0.001 for i in range(40)])
        result = ask("parks", collection, mock_openai_client, top_k=5)

        assert result["chunks_retrieved"] == 5


class TestVerifyCitations:
    def test_invented_clip_id_stripped_and_logged(self):
        from rag.query import verify_citations

        sources = [{"clip_id": 6669}, {"clip_id": 6670}]
        answer = "The vote passed [Clip 6669]. Also discussed [Clip 9999, 12:34]."
        cleaned, out_sources = verify_citations(answer, sources)

        assert "[Clip 6669]" in cleaned
        assert "9999" not in cleaned
        assert out_sources[0]["clip_id"] == 6669
        assert out_sources[0]["cited"] is True
        assert out_sources[1]["cited"] is False

    def test_cited_sources_ordered_first(self):
        from rag.query import verify_citations

        sources = [{"clip_id": 1}, {"clip_id": 2}, {"clip_id": 3}]
        answer = "Only the last matters [Clip 3]."
        _, out_sources = verify_citations(answer, sources)

        assert [s["clip_id"] for s in out_sources] == [3, 1, 2]
        assert [s["cited"] for s in out_sources] == [True, False, False]

    def test_citation_with_timestamp_detected(self):
        from rag.query import verify_citations

        sources = [{"clip_id": 6669}]
        _, out_sources = verify_citations("See [Clip 6669, 1:02:03].", sources)
        assert out_sources[0]["cited"] is True

    def test_no_citations_marks_all_uncited(self):
        from rag.query import verify_citations

        sources = [{"clip_id": 1}, {"clip_id": 2}]
        answer, out_sources = verify_citations("No coverage found.", sources)
        assert answer == "No coverage found."
        assert all(s["cited"] is False for s in out_sources)


# ============================================================
# Synthesis model: env-configurable at call time + model_used
# ============================================================

class TestSynthesisModelConfig:
    """RAG_SYNTHESIS_MODEL is resolved inside ask()/chat() at call time (so a
    .env value applied after import wins); an explicit model= still beats env;
    ask() reports the model in model_used."""

    def _collection_one(self, distance=0.2):
        coll = MagicMock()
        coll.count.return_value = 1
        coll.query.return_value = {
            "ids": [["id1"]],
            "documents": [["Doc about zoning ordinance"]],
            "metadatas": [[{"clip_id": 6669, "date": "2026-01-08",
                            "meeting_body": "Council", "source": "summary"}]],
            "distances": [[distance]],
        }
        return coll

    def test_ask_returns_model_used_default(self, mock_openai_client, monkeypatch):
        monkeypatch.delenv("RAG_SYNTHESIS_MODEL", raising=False)
        from rag.query import DEFAULT_MODEL, ask

        result = ask("zoning?", self._collection_one(), mock_openai_client)
        assert result["model_used"] == DEFAULT_MODEL
        synth_call = mock_openai_client.chat.completions.create.call_args
        assert synth_call.kwargs["model"] == DEFAULT_MODEL

    def test_ask_honors_env_model(self, mock_openai_client, monkeypatch):
        monkeypatch.setenv("RAG_SYNTHESIS_MODEL", "gpt-4o-mini")
        from rag.query import ask

        result = ask("zoning?", self._collection_one(), mock_openai_client)
        assert result["model_used"] == "gpt-4o-mini"
        synth_call = mock_openai_client.chat.completions.create.call_args
        assert synth_call.kwargs["model"] == "gpt-4o-mini"

    def test_ask_explicit_model_beats_env(self, mock_openai_client, monkeypatch):
        monkeypatch.setenv("RAG_SYNTHESIS_MODEL", "gpt-4o-mini")
        from rag.query import ask

        result = ask("zoning?", self._collection_one(), mock_openai_client, model="gpt-4o")
        assert result["model_used"] == "gpt-4o"
        synth_call = mock_openai_client.chat.completions.create.call_args
        assert synth_call.kwargs["model"] == "gpt-4o"

    def test_ask_no_coverage_still_reports_model(self, mock_openai_client, monkeypatch):
        from rag.query import MAX_DISTANCE, ask

        monkeypatch.setenv("RAG_SYNTHESIS_MODEL", "gpt-4o-mini")
        # The only chunk is gated out by distance → no-coverage path.
        result = ask("zoning?", self._collection_one(distance=MAX_DISTANCE + 0.2),
                     mock_openai_client)
        assert result["chunks_retrieved"] == 0
        assert result["model_used"] == "gpt-4o-mini"

    def test_chat_openai_honors_env_model(self, mock_openai_client, monkeypatch):
        monkeypatch.setenv("RAG_SYNTHESIS_MODEL", "gpt-4o-mini")
        from rag.query import chat

        result = chat([{"role": "user", "content": "zoning?"}],
                      self._collection_one(), mock_openai_client)
        assert result["model_used"] == "gpt-4o-mini"
        synth_call = mock_openai_client.chat.completions.create.call_args
        assert synth_call.kwargs["model"] == "gpt-4o-mini"
