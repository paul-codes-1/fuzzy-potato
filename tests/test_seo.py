"""Unit tests for SEO helper functions in seo.py."""

import json

import pytest

from seo import (
    build_clip_markdown,
    build_seo_description,
    build_seo_title,
    clean_title,
    format_long_date,
)


class TestCleanTitle:
    def test_strips_part_number_suffix(self):
        assert clean_title("Urban County Council (1)") == "Urban County Council"

    def test_strips_multidigit_suffix(self):
        assert clean_title("Council Work Session (12)") == "Council Work Session"

    def test_handles_no_suffix(self):
        assert clean_title("Urban County Council") == "Urban County Council"

    def test_strips_trailing_whitespace(self):
        assert clean_title("  Council Meeting  ") == "Council Meeting"

    def test_handles_none(self):
        assert clean_title(None) == ""

    def test_handles_empty(self):
        assert clean_title("") == ""

    def test_does_not_strip_internal_parens(self):
        assert clean_title("Budget (BFED) Committee (1)") == "Budget (BFED) Committee"


class TestFormatLongDate:
    def test_formats_iso_date(self):
        assert format_long_date("2026-04-30") == "April 30, 2026"

    def test_strips_leading_zero_from_day(self):
        assert format_long_date("2026-01-05") == "January 5, 2026"

    def test_handles_december(self):
        assert format_long_date("2025-12-31") == "December 31, 2025"

    def test_returns_raw_on_invalid(self):
        assert format_long_date("not-a-date") == "not-a-date"

    def test_returns_empty_on_none(self):
        assert format_long_date(None) == ""

    def test_returns_empty_on_empty_string(self):
        assert format_long_date("") == ""


class TestBuildSeoTitle:
    def test_combines_title_and_date(self):
        result = build_seo_title("Urban County Council (1)", "2026-04-30")
        assert result == "Urban County Council - April 30, 2026"

    def test_falls_back_to_title_when_no_date(self):
        assert build_seo_title("Urban County Council (1)", None) == "Urban County Council"

    def test_falls_back_to_date_when_no_title(self):
        assert build_seo_title(None, "2026-04-30") == "April 30, 2026"

    def test_default_when_both_missing(self):
        assert build_seo_title(None, None) == "LFUCG Meeting"


class TestBuildSeoDescription:
    def test_pulls_first_paragraph_from_summary(self, tmp_path):
        summary = tmp_path / "summary.txt"
        summary.write_text(
            "## Meeting Overview\n[timestamp: 00:00]\n\n"
            "The Urban County Council convened on April 30, 2026, to consider "
            "two ordinances on second reading and several first-reading items.\n\n"
            "## Attendance\n\nAll council members present.\n"
        )
        result = build_seo_description(summary)
        assert "Urban County Council convened" in result
        assert "##" not in result
        assert "[timestamp:" not in result

    def test_truncates_at_max_chars(self, tmp_path):
        summary = tmp_path / "summary.txt"
        # Long single paragraph
        summary.write_text("word " * 200)
        result = build_seo_description(summary, max_chars=50)
        assert len(result) <= 51  # 49 chars + ellipsis
        assert result.endswith("…")

    def test_falls_back_when_no_file(self):
        result = build_seo_description(
            None,
            fallback="The agenda includes ordinances and budget items for review by the council.",
        )
        assert "agenda includes ordinances" in result

    def test_returns_empty_when_no_summary_no_fallback(self):
        assert build_seo_description(None) == ""

    def test_skips_short_paragraphs(self, tmp_path):
        summary = tmp_path / "summary.txt"
        summary.write_text("## Heading\n\nshort.\n\n" + ("Real content " * 20))
        result = build_seo_description(summary)
        assert "Real content" in result
        assert "short" not in result


class TestBuildClipMarkdown:
    """`build_clip_markdown` is the per-clip Markdown alternate emitted at
    /data/clips/<id>/clip.md so AI agents and non-JS crawlers can read the
    same content the SPA renders without parsing JS-rendered DOM."""

    def _setup_clip(self, tmp_path, clip_id=6757):
        clip_dir = tmp_path / "clips" / str(clip_id)
        clip_dir.mkdir(parents=True)
        return clip_dir

    def test_returns_none_for_missing_clip_dir(self, tmp_path):
        entry = {"clip_id": 99999, "date": "2026-04-30", "title": "Test (1)"}
        assert build_clip_markdown(entry, tmp_path, "https://example.com") is None

    def test_includes_seo_title_as_h1(self, tmp_path):
        self._setup_clip(tmp_path)
        entry = {
            "clip_id": 6757,
            "date": "2026-04-30",
            "title": "Urban County Council (1)",
            "meeting_body": "Council",
        }
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        # Top-of-file pointer for AI agents precedes the H1, then the SEO title.
        assert md.startswith("<!-- AI/LLM agents:")
        assert "/skill.md -->" in md.split("\n", 1)[0]
        assert "# Urban County Council - April 30, 2026\n" in md

    def test_includes_disclosure_block(self, tmp_path):
        self._setup_clip(tmp_path)
        entry = {"clip_id": 6757, "date": "2026-04-30", "title": "Test (1)"}
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        assert "Auto-generated content" in md
        assert "Whisper-1" in md
        assert "GPT-4o" in md
        assert "Claude Sonnet" in md
        assert "editor@lexingtonky.news" in md

    def test_includes_metadata_fields(self, tmp_path):
        clip_dir = self._setup_clip(tmp_path)
        (clip_dir / "metadata.json").write_text(json.dumps({
            "clip_id": 6757,
            "url": "https://lfucg.granicus.com/player/clip/6757?view_id=14",
            "transcript_words": 11300,
            "summary_updated_at": "2026-04-30T20:34:59",
            "files": {},
        }))
        entry = {"clip_id": 6757, "date": "2026-04-30", "title": "Urban County Council (1)", "meeting_body": "Council"}
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        assert "https://lfucg.granicus.com/player/clip/6757" in md
        assert "11,300 words" in md
        assert "April 30, 2026" in md  # last revised, formatted

    def test_includes_summary_text(self, tmp_path):
        clip_dir = self._setup_clip(tmp_path)
        (clip_dir / "summary.txt").write_text(
            "## Meeting Overview\n\nThe Urban County Council convened to consider zoning ordinances."
        )
        entry = {"clip_id": 6757, "date": "2026-04-30", "title": "Test (1)"}
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        assert "## Meeting Overview" in md
        assert "convened to consider zoning ordinances" in md

    def test_includes_decisions_from_facts(self, tmp_path):
        clip_dir = self._setup_clip(tmp_path)
        (clip_dir / "extracted_facts.json").write_text(json.dumps({
            "motions_and_votes": [
                {
                    "identifier": "Ordinance 0285-26",
                    "description": "Modifying zoning restrictions on South Broadway.",
                    "outcome": "passed",
                    "ayes": 13,
                    "nays": 0,
                }
            ]
        }))
        entry = {"clip_id": 6757, "date": "2026-04-30", "title": "Test (1)"}
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        assert "## Decisions" in md
        assert "Ordinance 0285-26" in md
        assert "passed" in md
        assert "(13-0)" in md
        assert "Modifying zoning restrictions" in md

    def test_includes_full_transcript(self, tmp_path):
        clip_dir = self._setup_clip(tmp_path)
        (clip_dir / "metadata.json").write_text(json.dumps({
            "clip_id": 6757,
            "files": {"transcript": "transcript.txt"},
        }))
        (clip_dir / "transcript.txt").write_text("Mayor Gorton: I now call this meeting to order.")
        entry = {"clip_id": 6757, "date": "2026-04-30", "title": "Test (1)"}
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        assert "## Full transcript" in md
        assert "I now call this meeting to order" in md

    def test_returns_skeleton_when_only_metadata_exists(self, tmp_path):
        """Even with no summary, facts, or transcript, the page must still
        render the disclosure + permalink so an agent that lands on a
        clip.md before processing finishes still gets a coherent stub."""
        self._setup_clip(tmp_path)
        entry = {"clip_id": 6757, "date": "2026-04-30", "title": "Test (1)"}
        md = build_clip_markdown(entry, tmp_path, "https://meetings.lexingtonky.news")
        assert "# Test - April 30, 2026" in md
        assert "Auto-generated content" in md
        assert "https://meetings.lexingtonky.news/meeting/6757" in md
        assert "## Full transcript" not in md  # no transcript file -> section absent
        assert "## Decisions" not in md
