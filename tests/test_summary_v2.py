"""Tests for the two-pass summary generation pipeline."""

import json
from unittest.mock import MagicMock, patch


from summary_v2 import (
    build_extraction_prompt,
    extract_meeting_facts,
    generate_section,
    generate_summary_v2,
    _get_relevant_data,
    _generate_agenda_item_sections,
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

    def test_includes_schema(self):
        prompt = build_extraction_prompt("transcript", None, None)
        assert "motions_and_votes" in prompt
        assert "financial_items" in prompt


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

    def test_records_cost_when_usage_present(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_response.usage = MagicMock(prompt_tokens=123, completion_tokens=45)
        mock_client.chat.completions.create.return_value = mock_response
        tracker = MagicMock()

        with patch("api.cost.get_cost_tracker", return_value=tracker):
            extract_meeting_facts(
                mock_client,
                "transcript",
                None,
                None,
                tenant_id="tenant-a",
                request_id="req-1",
            )

        tracker.record_llm_call.assert_called_once_with(
            tenant_id="tenant-a",
            model="gpt-4o",
            operation="summary_v2_extract",
            input_tokens=123,
            output_tokens=45,
            request_id="req-1",
            module="summary_v2",
        )


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
        mock_response.content = [MagicMock()]
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
        mock_response.content = [MagicMock()]
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
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "## Test\nContent"
        mock_client.messages.create.return_value = mock_response

        generate_section(
            mock_client, "Test", "instruction", "{}",
            "Council", "2026-01-22", model="claude-sonnet-4-20250514",
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["temperature"] == 0.3

    def test_records_cost_when_usage_present(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "## Test\nContent"
        mock_response.usage = MagicMock(input_tokens=210, output_tokens=80)
        mock_client.messages.create.return_value = mock_response
        tracker = MagicMock()

        with patch("api.cost.get_cost_tracker", return_value=tracker):
            generate_section(
                mock_client,
                "Test",
                "instruction",
                "{}",
                "Council",
                "2026-01-22",
                tenant_id="tenant-a",
                request_id="req-2",
            )

        tracker.record_llm_call.assert_called_once_with(
            tenant_id="tenant-a",
            model="claude-sonnet-4-20250514",
            operation="summary_v2_narrate",
            input_tokens=210,
            output_tokens=80,
            request_id="req-2",
            module="summary_v2",
        )


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
            resp.content = [MagicMock()]
            resp.content[0].text = "## Section\nGenerated content for this section."
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
        from api.ingest import chunk_summary

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
        from api.ingest import chunk_summary

        text = """## Attendance
Present: Beasley, Boone, Brown.
"""
        chunks = chunk_summary(text, 6669, "2026-01-22", "Council")
        assert "start_time" not in chunks[0]
