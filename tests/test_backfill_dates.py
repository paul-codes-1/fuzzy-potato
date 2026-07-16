"""Tests for the backfill_dates.py neighbor-window sanity check.

Fixtures replay the 2026-07-16 incident (ISSUE-backfill-dates-sanity-check.md):
a 2026-06-17 backfill pass wrote LLM-hallucinated 2024–2026 dates onto a
contiguous band of 2008-era council clips. Hermetic — no live Granicus calls.
"""

import json
from datetime import date

import pytest

from backfill_dates import (
    build_anchors,
    check_candidate,
    date_from_text,
    find_violations,
    interpolate_date,
    minutes_first_page_text,  # noqa: F401  (import proves pdfplumber path exists)
    parse_iso_date,
    resolve_date,
)

# clip_id -> (hallucinated date written 2026-06-17, true date per official minutes)
INCIDENT_FIXTURES = {
    600: ("2024-01-11", "2008-09-18"),
    602: ("2024-01-23", "2008-09-23"),
    608: ("2024-04-11", "2008-09-30"),
    621: ("2024-09-12", "2008-10-14"),
    624: ("2024-10-14", "2008-10-21"),
    629: ("2024-12-05", "2008-10-23"),
    646: ("2025-06-24", "2008-11-06"),
    649: ("2025-07-31", "2008-11-11"),
    659: ("2025-10-23", "2008-11-18"),
    662: ("2025-11-19", "2008-11-20"),
    664: ("2025-12-04", "2008-11-25"),
    669: ("2026-02-12", "2008-12-02"),
    672: ("2026-03-12", "2008-12-04"),
    679: ("2026-06-09", "2008-12-09"),
    680: ("2026-06-16", "2008-12-09"),
}

# Real neighbors from the incident report.
REAL_ANCHORS = {598: "2008-09-16", 615: "2008-10-07"}


def _d(iso):
    return date.fromisoformat(iso)


def _archive_scaffold():
    """A mini-archive of correctly-dated clips surrounding the incident band.

    Enough good clips on each side that the longest non-decreasing
    subsequence always runs through the real archive, never through the
    (internally consistent!) 15-clip band of hallucinated 2024–2026 dates.
    """
    dated = {cid: _d(iso) for cid, iso in REAL_ANCHORS.items()}
    # 2008 clips leading in (weekly-ish)
    for i, cid in enumerate((560, 570, 580, 590)):
        dated[cid] = date(2008, 7, 1) + (date(2008, 9, 9) - date(2008, 7, 1)) * i // 3
    # 2009–2010 clips trailing out
    d = date(2009, 1, 6)
    for cid in range(690, 1000, 10):
        dated[cid] = d
        d = d + (date(2010, 12, 14) - date(2009, 1, 6)) // 30
    return dated


def true_dated_map():
    dated = _archive_scaffold()
    dated.update({cid: _d(true) for cid, (_, true) in INCIDENT_FIXTURES.items()})
    return dated


def corrupted_dated_map():
    dated = _archive_scaffold()
    dated.update({cid: _d(bad) for cid, (bad, _) in INCIDENT_FIXTURES.items()})
    return dated


class TestIncidentReplay:
    @pytest.mark.parametrize("cid", sorted(INCIDENT_FIXTURES))
    def test_rejects_every_hallucinated_date(self, cid):
        bad, _ = INCIDENT_FIXTURES[cid]
        dated = true_dated_map()
        del dated[cid]  # pre-fix: this clip had no date yet
        anchors = build_anchors(dated)
        ok, detail = check_candidate(cid, _d(bad), anchors)
        assert not ok, f"clip {cid}: hallucinated {bad} must be rejected ({detail})"

    @pytest.mark.parametrize("cid", sorted(INCIDENT_FIXTURES))
    def test_accepts_every_true_date(self, cid):
        _, true = INCIDENT_FIXTURES[cid]
        dated = true_dated_map()
        del dated[cid]
        anchors = build_anchors(dated)
        ok, detail = check_candidate(cid, _d(true), anchors)
        assert ok, f"clip {cid}: true date {true} must be accepted ({detail})"

    def test_lnds_excludes_the_mutually_corroborating_bad_band(self):
        # The 15 bad dates are internally non-decreasing — nearest *dated*
        # neighbors would corroborate each other. The LNDS anchor set must
        # drop the whole band because the surrounding archive contradicts it.
        anchors = build_anchors(corrupted_dated_map())
        assert not (set(anchors) & set(INCIDENT_FIXTURES))

    def test_audit_flags_exactly_the_corrupted_band(self):
        violations = find_violations(corrupted_dated_map())
        assert {v["clip_id"] for v in violations} == set(INCIDENT_FIXTURES)

    def test_audit_clean_on_repaired_data(self):
        assert find_violations(true_dated_map()) == []


class TestFalsePositiveGuards:
    """Correctly-dated clips that superficially look suspicious (retrospective
    'Mayor Newberry' mentions, a commenter named James Newberry) must pass —
    only the neighbor window is a date signal, never narrative text."""

    GUARDS = {
        # cid: (true date, prev anchor, next anchor)
        2048: ("2011-06-09", (2040, "2011-05-26"), (2055, "2011-06-16")),
        4020: ("2016-07-07", (4010, "2016-06-28"), (4030, "2016-07-12")),
        6720: ("2026-03-12", (6710, "2026-03-05"), (6730, "2026-03-19")),
        6734: ("2026-03-26", (6730, "2026-03-19"), (6740, "2026-04-02")),
    }

    @pytest.mark.parametrize("cid", sorted(GUARDS))
    def test_guard_clips_pass(self, cid):
        true, (p_id, p_date), (n_id, n_date) = self.GUARDS[cid]
        anchors = {p_id: _d(p_date), n_id: _d(n_date)}
        ok, detail = check_candidate(cid, _d(true), anchors)
        assert ok, f"guard clip {cid} wrongly flagged ({detail})"

    def test_date_verified_clips_skipped_by_audit(self):
        dated = true_dated_map()
        dated[605] = _d("1999-01-01")  # wildly off, but hand-verified
        assert find_violations(dated, verified={605}) == []
        assert {v["clip_id"] for v in find_violations(dated)} == {605}


class TestWindowEdges:
    def test_one_sided_window_at_archive_tail(self):
        anchors = {100: _d("2026-01-06")}
        ok, _ = check_candidate(200, _d("2026-06-01"), anchors)  # within 365d
        assert ok
        ok, _ = check_candidate(200, _d("2028-01-01"), anchors)  # beyond 365d
        assert not ok

    def test_no_anchors_passes_open(self):
        ok, detail = check_candidate(1, _d("2026-01-01"), {})
        assert ok and "no anchors" in detail

    def test_out_of_order_neighbors_use_min_max_bounds(self):
        # Anchors are non-decreasing by construction, but guard the helper
        # against inverted inputs anyway.
        anchors = {10: _d("2009-06-01"), 30: _d("2009-05-01")}
        ok, _ = check_candidate(20, _d("2009-05-15"), anchors)
        assert ok


class TestInterpolation:
    def test_interpolates_between_anchors(self):
        anchors = {598: _d("2008-09-16"), 615: _d("2008-10-07")}
        est = interpolate_date(600, anchors)
        assert _d("2008-09-16") <= est <= _d("2008-10-07")

    def test_rejected_files_date_falls_through_to_interpolation(self, tmp_path, capsys):
        clip_dir = tmp_path / "600"
        clip_dir.mkdir()
        (clip_dir / "extracted_facts.json").write_text(
            json.dumps({"meeting_info": {"date": "2024-01-11"}}))
        dated = true_dated_map()
        del dated[600]
        anchors = build_anchors(dated)
        iso, source, extra = resolve_date(
            600, clip_dir, listing_map={}, rss_map={}, anchors=anchors,
            use_minutes=False)
        assert source == "interpolated"
        assert _d("2008-09-16") <= _d(iso) <= _d("2008-09-23")
        out = capsys.readouterr().out
        assert "REJECTED source=files clip=600 candidate=2024-01-11" in out

    def test_trusted_source_wins_without_rejection(self, tmp_path):
        clip_dir = tmp_path / "600"
        clip_dir.mkdir()
        dated = true_dated_map()
        del dated[600]
        anchors = build_anchors(dated)
        iso, source, _ = resolve_date(
            600, clip_dir, listing_map={600: "2008-09-18"}, rss_map={},
            anchors=anchors, use_minutes=False)
        assert (iso, source) == ("2008-09-18", "listing")

    def test_minutes_source_used_and_cached(self, tmp_path):
        clip_dir = tmp_path / "600"
        clip_dir.mkdir()
        dated = true_dated_map()
        del dated[600]
        anchors = build_anchors(dated)

        def fake_minutes(cid):
            assert cid == 600
            return "2008-09-18", "lfucg_a4ef810d3f1bb3cf6c187f4e60ddab3d.pdf"

        iso, source, extra = resolve_date(
            600, clip_dir, listing_map={}, rss_map={}, anchors=anchors,
            use_minutes=True, minutes_fetcher=fake_minutes)
        assert (iso, source) == ("2008-09-18", "minutes")
        assert extra["minutes_pdf_file"] == "lfucg_a4ef810d3f1bb3cf6c187f4e60ddab3d.pdf"


class TestMinutesDateParse:
    # First-page shape of LFUCG official minutes (clip 600's real opening line).
    MINUTES_FIXTURE = (
        "Minutes of the Lexington-Fayette Urban County Council\n"
        "The Urban County Council convened in regular session on "
        "September 18, 2008 at 7:00 P.M., Mayor Jim Newberry presiding.\n"
    )

    def test_extracts_meeting_date_from_first_page(self):
        assert date_from_text(self.MINUTES_FIXTURE) == "2008-09-18"

    def test_numeric_date_format(self):
        assert date_from_text("Work Session 10/23/2008 agenda") == "2008-10-23"


class TestMinutesFileNames:
    def test_matches_prefixed_and_bare_hash_names(self):
        from backfill_dates import MINUTES_FILE_RE
        prefixed = "/DocumentViewer.php?file=lfucg_a4ef810d3f1bb3cf6c187f4e60ddab3d.pdf&view=1"
        bare = "/DocumentViewer.php?file=a692c591dac5f47ac68fbdc7172c0709.pdf&view=1"  # clip 161
        assert MINUTES_FILE_RE.search(prefixed).group(1) == "lfucg_a4ef810d3f1bb3cf6c187f4e60ddab3d.pdf"
        assert MINUTES_FILE_RE.search(bare).group(1) == "a692c591dac5f47ac68fbdc7172c0709.pdf"


class TestParseIsoDate:
    def test_accepts_iso_and_datetime_prefix(self):
        assert parse_iso_date("2008-09-18") == _d("2008-09-18")
        assert parse_iso_date("2008-09-18T19:00:00") == _d("2008-09-18")

    def test_rejects_garbage(self):
        assert parse_iso_date(None) is None
        assert parse_iso_date("2008") is None
        assert parse_iso_date("September 18, 2008") is None
