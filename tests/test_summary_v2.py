"""Tests for the two-pass summary generation pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from summary_v2 import (
    build_extraction_prompt,
    build_timestamped_transcript,
    extract_meeting_facts,
    generate_section,
    generate_summary_v2,
    _get_relevant_data,
    _generate_agenda_item_sections,
    _validate_facts,
    SECTIONS,
)


# ============================================================
# Sample extracted facts for testing
# ============================================================

SAMPLE_FACTS = {
    "meeting_info": {
        "date": "2026-01-22",
        "time": "6:00 PM",
        "body": "Urban County Council",
        "presiding_officer": "Mayor Linda Gorton",
        "location": "Council Chambers",
    },
    "attendance": {
        "present": ["Beasley", "Boone", "Brown", "Curtis", "Ellinger"],
        "absent": [],
        "late": [],
    },
    "motions_and_votes": [
        {
            "identifier": "Ordinance 0016-26",
            "description": "Zoning change from Agricultural-Rural to Medium Density Residential",
            "motion_by": "Brown",
            "second_by": "Curtis",
            "outcome": "passed",
            "vote_type": "roll_call",
            "ayes": 8,
            "nays": 0,
            "abstentions": 0,
            "votes_for": ["Beasley", "Boone", "Brown", "Curtis"],
            "votes_against": [],
            "conditions": None,
            "transcript_approx_time": "25:15",
        }
    ],
    "financial_items": [
        {
            "description": "General Obligation Bonds",
            "amount": "$18,040,000",
            "type": "appropriation",
            "identifier": None,
            "vendor_or_recipient": None,
        }
    ],
    "public_comments": [],
    "agenda_items": [
        {
            "identifier": "Ordinance 0016-26",
            "title": "Zoning Change - Agricultural to Residential",
            "type": "ordinance",
            "summary": "Changed zone from Agricultural-Rural to Medium Density Residential in District 12.",
            "key_speakers": ["Brown"],
            "outcome": "approved",
            "transcript_approx_time": "25:15",
        }
    ],
    "appointments": [],
    "contentious_items": [],
}


# ============================================================
# Pass 1: Extraction tests
# ============================================================

class TestBuildExtractionPrompt:

    def test_includes_transcript(self):
        prompt = build_extraction_prompt("Hello world", None, None)
        assert "MEETING TRANSCRIPT:" in prompt
        assert "Hello world" in prompt

    def test_includes_agenda_when_provided(self):
        prompt = build_extraction_prompt("transcript", "agenda text", None)
        assert "MEETING AGENDA:" in prompt
        assert "agenda text" in prompt

    def test_includes_minutes_when_provided(self):
        prompt = build_extraction_prompt("transcript", None, "minutes text")
        assert "OFFICIAL MEETING MINUTES:" in prompt
        assert "minutes text" in prompt

    def test_truncates_long_minutes(self):
        long_minutes = "x" * 40000
        prompt = build_extraction_prompt("transcript", None, long_minutes)
        # Should be truncated to 30000 chars
        assert len(long_minutes) > 30000
        assert "x" * 30000 in prompt
        assert "x" * 40000 not in prompt

    def test_truncates_long_agenda(self):
        long_agenda = "a" * 40000
        prompt = build_extraction_prompt("transcript", long_agenda, None)
        # Agenda packets run hundreds of pages — capped at 30000 chars
        assert "a" * 30000 in prompt
        assert "a" * 40000 not in prompt

    def test_includes_schema(self):
        prompt = build_extraction_prompt("transcript", None, None)
        assert "motions_and_votes" in prompt
        assert "financial_items" in prompt

    def test_includes_timestamp_copy_rule(self):
        prompt = build_extraction_prompt("transcript", None, None)
        # Pass 1 must copy timestamps from [H:MM:SS] markers, never invent them
        assert "[H:MM:SS]" in prompt
        assert "never" in prompt.lower()


# ============================================================
# Timestamp-marker interleaving (anti-hallucination anchors)
# ============================================================

class TestBuildTimestampedTranscript:

    def test_interleaves_markers_every_interval(self):
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Call to order."},
            {"start": 5.0, "end": 28.0, "text": "Roll call."},
            {"start": 31.0, "end": 60.0, "text": "First item."},
            {"start": 62.0, "end": 90.0, "text": "Discussion continues."},
        ]
        result = build_timestamped_transcript(segments, marker_interval=30.0)
        # Marker at the first segment, then again once 30s have elapsed
        assert "[0:00:00]" in result
        assert "[0:00:31]" in result
        assert "[0:01:02]" in result
        assert "Call to order. Roll call." in result

    def test_does_not_mark_every_segment(self):
        segments = [
            {"start": float(i), "end": float(i + 1), "text": f"seg{i}"}
            for i in range(20)
        ]
        result = build_timestamped_transcript(segments, marker_interval=30.0)
        # 20 one-second segments within 30s → only the opening marker
        assert result.count("[0:") == 1

    def test_hour_format(self):
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Opening."},
            {"start": 4530.0, "end": 4540.0, "text": "Late item."},
        ]
        result = build_timestamped_transcript(segments)
        assert "[1:15:30]" in result

    def test_no_timing_falls_back_to_plain_join(self):
        # Document-driven clips: single synthetic segment with start/end 0
        segments = [{"start": 0.0, "end": 0.0, "text": "Minutes text here."}]
        result = build_timestamped_transcript(segments)
        assert result == "Minutes text here."
        assert "[" not in result

    def test_empty_segments(self):
        assert build_timestamped_transcript([]) == ""

    def test_skips_empty_segment_text(self):
        segments = [
            {"start": 0.0, "end": 5.0, "text": "  "},
            {"start": 5.0, "end": 10.0, "text": "Real text."},
        ]
        result = build_timestamped_transcript(segments)
        assert result == "[0:00:05] Real text."


class TestExtractMeetingFacts:

    def test_parses_valid_json_response(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(SAMPLE_FACTS)
        mock_client.chat.completions.create.return_value = mock_response

        result = extract_meeting_facts(mock_client, "transcript", None, None)
        assert result["meeting_info"]["date"] == "2026-01-22"
        assert len(result["motions_and_votes"]) == 1

    def test_returns_empty_dict_on_invalid_json(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not json at all"
        mock_client.chat.completions.create.return_value = mock_response

        result = extract_meeting_facts(mock_client, "transcript", None, None)
        assert result == {}

    def test_extracts_json_from_code_fence(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "Here are the facts:\n```json\n" + json.dumps(SAMPLE_FACTS) + "\n```"
        )
        mock_client.chat.completions.create.return_value = mock_response

        result = extract_meeting_facts(mock_client, "transcript", None, None)
        assert result["meeting_info"]["body"] == "Urban County Council"

    def test_uses_json_object_response_format(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_client.chat.completions.create.return_value = mock_response

        extract_meeting_facts(mock_client, "transcript", None, None)
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["temperature"] == 0.1


# ============================================================
# Schema validation of extracted facts
# ============================================================

class TestValidateFacts:

    def test_coerces_numeric_string_vote_counts(self):
        facts = _validate_facts({
            "motions_and_votes": [
                {"ayes": "8", "nays": 0, "abstentions": "junk", "outcome": "passed"}
            ],
        })
        motion = facts["motions_and_votes"][0]
        assert motion["ayes"] == 8
        assert motion["nays"] == 0
        assert motion["abstentions"] is None

    def test_non_list_name_fields_become_empty_lists(self):
        facts = _validate_facts({
            "motions_and_votes": [
                {"votes_for": "Beasley", "votes_against": None}
            ],
            "attendance": {"present": "everyone", "absent": None},
        })
        motion = facts["motions_and_votes"][0]
        assert motion["votes_for"] == []
        assert motion["votes_against"] == []
        assert facts["attendance"]["present"] == []
        assert facts["attendance"]["absent"] == []
        assert facts["attendance"]["late"] == []

    def test_name_lists_drop_non_string_entries(self):
        facts = _validate_facts({
            "motions_and_votes": [
                {"votes_for": ["Beasley", 7, None, "Boone"]}
            ],
        })
        assert facts["motions_and_votes"][0]["votes_for"] == ["Beasley", "Boone"]

    def test_non_list_sections_become_empty_lists(self):
        facts = _validate_facts({
            "motions_and_votes": None,
            "financial_items": "none",
            "public_comments": {"speaker": "x"},
            "agenda_items": None,
            "appointments": None,
            "contentious_items": None,
        })
        for field in ("motions_and_votes", "financial_items", "public_comments",
                      "agenda_items", "appointments", "contentious_items"):
            assert facts[field] == []

    def test_drops_non_dict_motion_entries(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="summary_v2"):
            facts = _validate_facts({
                "motions_and_votes": [
                    "Motion to approve the docket",
                    {"identifier": "Ordinance 1", "outcome": "PASSED"},
                ],
            })
        assert len(facts["motions_and_votes"]) == 1
        assert facts["motions_and_votes"][0]["identifier"] == "Ordinance 1"
        assert any("non-dict motion" in r.message for r in caplog.records)

    def test_lowercases_outcome(self):
        facts = _validate_facts({
            "motions_and_votes": [{"outcome": "Passed"}],
        })
        assert facts["motions_and_votes"][0]["outcome"] == "passed"

    def test_empty_dict_stays_empty(self):
        # Callers treat {} as "Pass 1 failed" — don't grow a skeleton
        assert _validate_facts({}) == {}

    def test_non_dict_payload_returns_empty(self):
        assert _validate_facts(["not", "a", "dict"]) == {}

    def test_valid_facts_pass_through(self):
        facts = _validate_facts(json.loads(json.dumps(SAMPLE_FACTS)))
        assert facts["motions_and_votes"][0]["ayes"] == 8
        assert facts["attendance"]["present"] == SAMPLE_FACTS["attendance"]["present"]

    def test_applied_by_extract_meeting_facts(self):
        dirty = dict(SAMPLE_FACTS)
        dirty["motions_and_votes"] = [{"ayes": "8", "outcome": "Passed"}]
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(dirty)
        mock_client.chat.completions.create.return_value = mock_response

        result = extract_meeting_facts(mock_client, "transcript", None, None)
        assert result["motions_and_votes"][0]["ayes"] == 8
        assert result["motions_and_votes"][0]["outcome"] == "passed"


# ============================================================
# Section data helpers
# ============================================================

class TestGetRelevantData:

    def test_meeting_overview_includes_counts(self):
        data = json.loads(_get_relevant_data(SAMPLE_FACTS, "Meeting Overview"))
        assert data["motions_and_votes_count"] == 1
        assert data["agenda_items_count"] == 1

    def test_votes_section_returns_motions(self):
        data = json.loads(_get_relevant_data(SAMPLE_FACTS, "Votes and Decisions"))
        assert "motions_and_votes" in data
        assert len(data["motions_and_votes"]) == 1

    def test_financial_section_returns_items(self):
        data = json.loads(_get_relevant_data(SAMPLE_FACTS, "Budget and Financial Actions"))
        assert data["financial_items"][0]["amount"] == "$18,040,000"


class TestGenerateAgendaItemSections:

    def test_generates_sections_for_significant_items(self):
        facts = {
            "motions_and_votes": [],
            "agenda_items": [
                {
                    "identifier": None,
                    "title": "Black Church Coalition Presentation",
                    "type": "presentation",
                    "summary": "The Black Church Coalition presented their community outreach program for 2026, focusing on youth engagement and neighborhood safety initiatives.",
                    "key_speakers": ["Rev. Johnson"],
                    "outcome": "informational",
                },
            ],
        }
        sections = _generate_agenda_item_sections(facts)
        assert len(sections) == 1
        assert sections[0]["name"] == "Black Church Coalition Presentation"

    def test_skips_trivial_items(self):
        facts = {
            "motions_and_votes": [],
            "agenda_items": [
                {
                    "title": "Roll Call",
                    "summary": "Roll call taken.",
                    "type": "discussion",
                },
            ],
        }
        sections = _generate_agenda_item_sections(facts)
        assert len(sections) == 0


# ============================================================
# Section conditions
# ============================================================

class TestSectionConditions:

    def test_attendance_requires_present_list(self):
        cond = next(s for s in SECTIONS if s["name"] == "Attendance")["condition"]
        assert cond(SAMPLE_FACTS) is True
        assert cond({"attendance": {"present": []}}) is False

    def test_votes_requires_motions(self):
        cond = next(s for s in SECTIONS if s["name"] == "Votes and Decisions")["condition"]
        assert cond(SAMPLE_FACTS) is True
        assert cond({"motions_and_votes": []}) is False

    def test_public_comment_skipped_when_empty(self):
        cond = next(s for s in SECTIONS if s["name"] == "Public Comment")["condition"]
        assert cond(SAMPLE_FACTS) is False

    def test_contested_items_skipped_when_empty(self):
        cond = next(s for s in SECTIONS if s["name"] == "Contested Items")["condition"]
        assert cond(SAMPLE_FACTS) is False


# ============================================================
# Pass 2: Narrative generation
# ============================================================

class TestGenerateSection:

    def test_returns_section_with_header(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text")]
        mock_response.content[0].text = "## Meeting Overview\nThe council met on Jan 22."
        mock_client.messages.create.return_value = mock_response

        result = generate_section(
            mock_client, "Meeting Overview", "Write overview",
            "{}", "Council", "2026-01-22",
        )
        assert result.startswith("## Meeting Overview")

    def test_adds_header_if_missing(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text")]
        mock_response.content[0].text = "The council met on Jan 22."
        mock_client.messages.create.return_value = mock_response

        result = generate_section(
            mock_client, "Meeting Overview", "Write overview",
            "{}", "Council", "2026-01-22",
        )
        assert result.startswith("## Meeting Overview")

    def test_uses_correct_model(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text")]
        mock_response.content[0].text = "## Test\nContent"
        mock_client.messages.create.return_value = mock_response

        generate_section(
            mock_client, "Test", "instruction", "{}",
            "Council", "2026-01-22", model="claude-sonnet-4-6",
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"
        # Haiku 5.5 400s on non-default sampling params — never send one.
        assert "temperature" not in call_kwargs

    def _response(self, text, stop_reason="end_turn"):
        resp = MagicMock()
        resp.content = [MagicMock(type="text")]
        resp.content[0].text = text
        resp.stop_reason = stop_reason
        return resp

    def test_skips_leading_thinking_block(self):
        """Haiku 5.5 can open a response with a thinking block; only text counts."""
        mock_client = MagicMock()
        resp = MagicMock()
        thinking = MagicMock(type="thinking"); thinking.text = "SHOULD NOT APPEAR"
        text = MagicMock(type="text"); text.text = "## Test\nBody."
        resp.content = [thinking, text]
        resp.stop_reason = "end_turn"
        mock_client.messages.create.return_value = resp

        result = generate_section(
            mock_client, "Test", "instruction", "{}", "Council", "2026-01-22",
        )
        assert result == "## Test\nBody."

    def test_no_retry_when_end_turn(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._response("## Test\nDone.")

        generate_section(
            mock_client, "Test", "instruction", "{}", "Council", "2026-01-22",
        )
        assert mock_client.messages.create.call_count == 1

    def test_retries_once_with_more_tokens_when_truncated(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            self._response("## Test\nTruncated mid-", stop_reason="max_tokens"),
            self._response("## Test\nComplete text."),
        ]

        result = generate_section(
            mock_client, "Test", "instruction", "{}", "Council", "2026-01-22",
        )
        assert result == "## Test\nComplete text."
        assert mock_client.messages.create.call_count == 2
        first, second = mock_client.messages.create.call_args_list
        assert first[1]["max_tokens"] == 2000
        assert second[1]["max_tokens"] == 4000

    def test_keeps_truncated_text_when_retry_also_truncates(self, caplog):
        import logging

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            self._response("## Test\nStill trunc-", stop_reason="max_tokens"),
            self._response("## Test\nStill trunc again-", stop_reason="max_tokens"),
        ]

        with caplog.at_level(logging.WARNING, logger="summary_v2"):
            result = generate_section(
                mock_client, "Test", "instruction", "{}", "Council", "2026-01-22",
            )
        # Keeps the (retried) truncated text but logs loudly — no third call
        assert result == "## Test\nStill trunc again-"
        assert mock_client.messages.create.call_count == 2
        assert any("truncated" in r.message for r in caplog.records)


# ============================================================
# End-to-end: generate_summary_v2
# ============================================================

class TestGenerateSummaryV2:

    def _mock_openai(self):
        client = MagicMock()
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(SAMPLE_FACTS)
        client.chat.completions.create.return_value = response
        return client

    def _mock_anthropic(self):
        client = MagicMock()

        def create_response(**kwargs):
            # Extract section name from user message
            user_msg = kwargs["messages"][0]["content"]
            resp = MagicMock()
            resp.content = [MagicMock(type="text")]
            resp.content[0].text = f"## Section\nGenerated content for this section."
            return resp

        client.messages.create.side_effect = create_response
        return client

    def test_returns_summary_and_facts(self):
        summary, facts = generate_summary_v2(
            openai_client=self._mock_openai(),
            anthropic_client=self._mock_anthropic(),
            transcript="Welcome to the meeting.",
            agenda_text=None,
            minutes_text=None,
        )
        assert summary is not None
        assert facts is not None
        assert "## " in summary

    def test_returns_none_on_extraction_failure(self):
        mock_openai = MagicMock()
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "invalid json"
        mock_openai.chat.completions.create.return_value = response

        summary, facts = generate_summary_v2(
            openai_client=mock_openai,
            anthropic_client=self._mock_anthropic(),
            transcript="Hello",
            agenda_text=None,
            minutes_text=None,
        )
        assert summary is None
        assert facts is None

    def test_skips_sections_with_no_data(self):
        # Facts with no public comments, appointments, or contested items
        openai = self._mock_openai()
        anthropic = self._mock_anthropic()

        summary, facts = generate_summary_v2(
            openai_client=openai,
            anthropic_client=anthropic,
            transcript="Hello",
            agenda_text=None,
            minutes_text=None,
        )

        # Check that anthropic was NOT called for Public Comment, Appointments, Contested Items
        # (they have empty arrays in SAMPLE_FACTS)
        call_messages = [
            call[1]["messages"][0]["content"]
            for call in anthropic.messages.create.call_args_list
        ]
        section_names = [msg.split('"')[1] for msg in call_messages]
        assert "Public Comment" not in section_names
        assert "Appointments" not in section_names
        assert "Contested Items" not in section_names

    def test_calls_log_fn(self):
        logs = []
        generate_summary_v2(
            openai_client=self._mock_openai(),
            anthropic_client=self._mock_anthropic(),
            transcript="Hello",
            agenda_text=None,
            minutes_text=None,
            log_fn=logs.append,
        )
        assert any("Pass 1" in msg for msg in logs)
        assert any("Pass 2" in msg for msg in logs)


# ============================================================
# Timestamp parsing in chunk_summary
# ============================================================

class TestChunkSummaryTimestamps:

    def test_extracts_timestamp_from_section(self):
        from rag.ingest import chunk_summary

        text = """## Meeting Overview
The council met on January 22. [timestamp: 0:45] They discussed zoning changes.

## Votes and Decisions
Ordinance 0016-26 passed 8-0. [timestamp: 25:15]
"""
        chunks = chunk_summary(text, 6669, "2026-01-22", "Council")
        overview = next(c for c in chunks if c["section_type"] == "Meeting Overview")
        votes = next(c for c in chunks if c["section_type"] == "Votes and Decisions")

        assert overview.get("start_time") == 45
        assert votes.get("start_time") == 25 * 60 + 15

    def test_no_timestamp_means_no_start_time(self):
        from rag.ingest import chunk_summary

        text = """## Attendance
Present: Beasley, Boone, Brown.
"""
        chunks = chunk_summary(text, 6669, "2026-01-22", "Council")
        assert "start_time" not in chunks[0]
