"""Unit tests for SEO helper functions in seo.py."""

import pytest

from seo import (
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
