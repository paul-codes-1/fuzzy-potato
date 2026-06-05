"""Tests for the YouTube video matcher (scripts/match_youtube_videos.py).

Network-free: exercises date parsing, kind classification, and the
date+kind matching logic against fixed fixtures.
"""
import json

from scripts.match_youtube_videos import (
    build_videos_by_date,
    classify_kind,
    match_clip_to_video,
    parse_title_date,
    run,
)


class TestParseTitleDate:
    def test_long_format(self):
        assert parse_title_date("City of Paris Regular Commission Meeting April 14, 2026") == "2026-04-14"

    def test_long_format_colon(self):
        assert parse_title_date("City of Paris Regular Commission Meeting: February 24, 2026") == "2026-02-24"

    def test_ordinal(self):
        assert parse_title_date("Commission Meeting March 9th, 2021") == "2021-03-09"

    def test_abbrev_month(self):
        assert parse_title_date("Special Meeting Sept 5, 2025") == "2025-09-05"

    def test_mmddyyyy_numeric(self):
        assert parse_title_date("The City of Paris Regular Commission Meeting 12102024") == "2024-12-10"

    def test_no_date(self):
        assert parse_title_date("City of Paris Commission Meeting") is None

    def test_invalid_date_rejected(self):
        assert parse_title_date("Meeting February 30, 2026") is None


class TestClassifyKind:
    def test_regular_default(self):
        assert classify_kind("City Commission Meeting") == "regular"

    def test_workshop(self):
        assert classify_kind("Workshop: Stoner Creek Property") == "workshop"

    def test_special(self):
        assert classify_kind("Special Called Meeting") == "special"

    def test_budget(self):
        assert classify_kind("Budget Workshop") in ("budget", "workshop")  # budget keyword first

    def test_joint(self):
        assert classify_kind("Joint City & County Budget Meeting") == "joint"


class TestMatching:
    VIDEOS = [
        ("vREG414", "City of Paris Regular Commission Meeting April 14, 2026"),
        ("vREG224", "City of Paris Regular Commission Meeting: February 24, 2026"),
        ("vREG512", "City of Paris Regular Commission Meeting May 12, 2026"),
    ]

    def test_regular_matches_same_date(self):
        by_date = build_videos_by_date(self.VIDEOS)
        clip = {"date": "2026-02-24", "title": "City Commission Meeting", "meeting_body": "Commission"}
        assert match_clip_to_video(clip, by_date) == "vREG224"

    def test_workshop_does_not_match_regular_video_same_date(self):
        # 2026-04-14 has only the REGULAR video; a same-day workshop must NOT
        # borrow it.
        by_date = build_videos_by_date(self.VIDEOS)
        clip = {"date": "2026-04-14", "title": "Workshop: Stoner Creek Property", "meeting_body": ""}
        assert match_clip_to_video(clip, by_date) is None

    def test_regular_matches_on_shared_date(self):
        by_date = build_videos_by_date(self.VIDEOS)
        clip = {"date": "2026-04-14", "title": "City Commission Meeting", "meeting_body": "Commission"}
        assert match_clip_to_video(clip, by_date) == "vREG414"

    def test_duplicate_clips_same_date_both_match(self):
        by_date = build_videos_by_date(self.VIDEOS)
        c12 = {"date": "2026-05-12", "title": "City Commission Meeting", "meeting_body": "Commission"}
        c13 = {"date": "2026-05-12", "title": "City Commission Meeting", "meeting_body": "Commission"}
        assert match_clip_to_video(c12, by_date) == "vREG512"
        assert match_clip_to_video(c13, by_date) == "vREG512"

    def test_no_video_for_date(self):
        by_date = build_videos_by_date(self.VIDEOS)
        clip = {"date": "2026-03-17", "title": "Budget Worshop", "meeting_body": ""}
        assert match_clip_to_video(clip, by_date) is None

    def test_missing_date(self):
        by_date = build_videos_by_date(self.VIDEOS)
        assert match_clip_to_video({"title": "x"}, by_date) is None


class TestRunWritesMetadata:
    def _clip(self, d, cid, date_str, title, body=""):
        cd = d / "clips" / str(cid)
        cd.mkdir(parents=True)
        (cd / "metadata.json").write_text(
            json.dumps({"clip_id": cid, "date": date_str, "title": title, "meeting_body": body}),
            encoding="utf-8",
        )
        return cd

    def test_run_stamps_matched_clips_only(self, tmp_path, monkeypatch):
        import scripts.match_youtube_videos as m

        monkeypatch.setattr(
            m, "list_channel_videos",
            lambda url, timeout=90: [
                ("vREG224", "Regular Commission Meeting February 24, 2026"),
                ("vREG414", "Regular Commission Meeting April 14, 2026"),
            ],
        )
        self._clip(tmp_path, 3, "2026-02-24", "City Commission Meeting", "Commission")
        self._clip(tmp_path, 8, "2026-04-14", "City Commission Meeting", "Commission")
        self._clip(tmp_path, 9, "2026-04-14", "Workshop: Stoner Creek Property")
        self._clip(tmp_path, 5, "2026-03-17", "Budget Worshop")

        stats = m.run(tmp_path, "https://youtube.com/@x", dry_run=False, log=lambda *a: None)
        assert stats["matched"] == 2

        def vurl(cid):
            return json.loads((tmp_path / "clips" / str(cid) / "metadata.json").read_text()).get("video_url")

        assert vurl(3) == "https://www.youtube.com/watch?v=vREG224"
        assert vurl(8) == "https://www.youtube.com/watch?v=vREG414"
        assert vurl(9) is None  # workshop, same-day regular video not borrowed
        assert vurl(5) is None  # no video that date

    def test_run_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        import scripts.match_youtube_videos as m

        monkeypatch.setattr(
            m, "list_channel_videos",
            lambda url, timeout=90: [("vREG224", "Regular Commission Meeting February 24, 2026")],
        )
        self._clip(tmp_path, 3, "2026-02-24", "City Commission Meeting", "Commission")
        m.run(tmp_path, "https://youtube.com/@x", dry_run=True, log=lambda *a: None)
        meta = json.loads((tmp_path / "clips" / "3" / "metadata.json").read_text())
        assert "video_url" not in meta
