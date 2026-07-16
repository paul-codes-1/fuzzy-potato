"""Tests for scripts/meeting_alerts.py — hermetic (no SES, no Anthropic)."""

import json
from datetime import date

import pytest

from scripts import meeting_alerts as ma


SEGMENTS = [
    {"id": 0, "start": 246.2, "end": 248.2, "text": "thank you, everyone.", "speaker": None},
    {"id": 1, "start": 250.0, "end": 258.0,
     "text": "Next item is the CentrePointe tax increment financing report.", "speaker": None},
    {"id": 2, "start": 260.0, "end": 268.0,
     "text": "The TIF increment paid to City Center Parking was discussed.", "speaker": None},
]


class TestWatchlistParsing:
    def test_plain_terms_and_comments(self):
        wl = ma.parse_watchlist("# comment\n\nCentrePointe\ntax increment\n")
        assert [t for t, _ in wl] == ["CentrePointe", "tax increment"]

    def test_word_boundary_matching(self):
        wl = ma.parse_watchlist("TIF\n")
        _, pat = wl[0]
        assert pat.search("the TIF district")
        assert pat.search("a tif payment")  # case-insensitive
        assert not pat.search("certification")  # no substring hit inside words

    def test_regex_lines(self):
        wl = ma.parse_watchlist(r"re:ethics\s+commission" + "\n")
        _, pat = wl[0]
        assert pat.search("the Ethics  Commission met")

    def test_bad_regex_skipped(self, capsys):
        wl = ma.parse_watchlist("re:([unclosed\ngood term\n")
        assert [t for t, _ in wl] == ["good term"]
        assert "bad regex" in capsys.readouterr().out


class TestWatchlistMatching:
    def test_segments_give_timestamp_and_count(self):
        wl = ma.parse_watchlist("CentrePointe\nTIF\nzoning\n")
        hits = ma.match_watchlist(wl, SEGMENTS, [])
        by_term = {h["term"]: h for h in hits}
        assert set(by_term) == {"CentrePointe", "TIF"}
        assert by_term["CentrePointe"]["ts"] == 250.0
        assert by_term["TIF"]["count"] == 1
        assert "tax increment financing" in by_term["CentrePointe"]["context"]

    def test_flat_fallback_has_no_timestamp(self):
        wl = ma.parse_watchlist("greenway\n")
        hits = ma.match_watchlist(wl, None, [("agenda", "Discussion of the greenway plan.")])
        assert hits[0]["ts"] is None
        assert "(agenda)" in hits[0]["context"]

    def test_no_hits(self):
        wl = ma.parse_watchlist("flock\n")
        assert ma.match_watchlist(wl, SEGMENTS, []) == []


class TestRecency:
    def test_old_clip_not_recent(self):
        assert not ma.is_recent({"date": "2008-09-25"}, today=date(2026, 7, 16))

    def test_new_clip_recent(self):
        assert ma.is_recent({"date": "2026-07-10"}, today=date(2026, 7, 16))

    def test_undated_errs_toward_alerting(self):
        assert ma.is_recent({}, today=date(2026, 7, 16))


class TestDigest:
    META = {"title": "Urban County Council Meeting", "date": "2026-07-15"}

    def test_section_contains_links_and_hits(self):
        hits = [{"term": "TIF", "count": 2, "ts": 260.0, "context": "The TIF increment..."}]
        s = ma.clip_section(6800, self.META, "• [0:04:10] Something — happened.", hits)
        assert f"{ma.CFG.site_url}/meeting/6800" in s
        assert "player/clip/6800" in s
        assert "entrytime=260" in s
        assert '"TIF" ×2 — first at [0:04:20]' in s
        assert "• [0:04:10] Something" in s

    def test_fmt_ts(self):
        assert ma.fmt_ts(0) == "0:00:00"
        assert ma.fmt_ts(3725.9) == "1:02:05"

    def test_granicus_url(self):
        u = ma.granicus_url(42, 90.7)
        assert "player/clip/42" in u and "entrytime=90" in u


class TestScanState:
    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        clips = tmp_path / "clips"
        clips.mkdir()
        monkeypatch.setattr(ma, "CLIPS_DIR", clips)
        monkeypatch.setattr(ma, "STATE_FILE", tmp_path / "alerts_state.json")
        monkeypatch.setattr(ma, "WATCHLIST_FILE", tmp_path / "watchlist.txt")
        return tmp_path, clips

    def _mk_clip(self, clips, cid, clip_date, transcript="Meeting about the greenway plan."):
        d = clips / str(cid)
        d.mkdir()
        files = {}
        if transcript is not None:
            (d / "transcript_x.txt").write_text(transcript)
            files["transcript"] = "transcript_x.txt"
        (d / "metadata.json").write_text(json.dumps(
            {"title": f"Clip {cid}", "date": clip_date, "files": files}))

    def test_first_run_initializes_without_alerting(self, sandbox, capsys):
        tmp, clips = sandbox
        self._mk_clip(clips, 100, "2026-07-15")
        assert ma.run_scan(dry_run=False, with_leads=False) == 0
        state = json.loads((tmp / "alerts_state.json").read_text())
        assert state["seen"]["100"] == "preexisting"
        assert "alerts start with the next new clip" in capsys.readouterr().out

    def test_new_recent_clip_alerts_and_old_clip_skipped(self, sandbox, capsys, monkeypatch):
        tmp, clips = sandbox
        (tmp / "alerts_state.json").write_text(json.dumps({"seen": {}}))
        (tmp / "watchlist.txt").write_text("greenway\n")
        self._mk_clip(clips, 200, "2026-07-15")
        self._mk_clip(clips, 201, "2008-01-01")  # backfilled history: silent
        self._mk_clip(clips, 202, "2026-07-15", transcript=None)  # no transcript yet
        sent = {}
        monkeypatch.setattr(ma, "send_email", lambda s, b: sent.update(s=s, b=b) or True)
        assert ma.run_scan(dry_run=False, with_leads=False) == 0
        state = json.loads((tmp / "alerts_state.json").read_text())
        assert "skipped-old" in state["seen"]["201"]
        assert "skipped-no-transcript" in state["seen"]["202"]
        assert "200" in state["seen"]
        assert "greenway" in sent["b"] and "Clip 200" in sent["b"]

    def test_failed_send_leaves_state_unwritten_for_retry(self, sandbox, monkeypatch):
        tmp, clips = sandbox
        (tmp / "alerts_state.json").write_text(json.dumps({"seen": {}}))
        (tmp / "watchlist.txt").write_text("greenway\n")
        self._mk_clip(clips, 300, "2026-07-15")
        monkeypatch.setattr(ma, "send_email", lambda s, b: False)
        assert ma.run_scan(dry_run=False, with_leads=False) == 1
        assert json.loads((tmp / "alerts_state.json").read_text()) == {"seen": {}}

    def test_dry_run_writes_nothing(self, sandbox, capsys):
        tmp, clips = sandbox
        (tmp / "alerts_state.json").write_text(json.dumps({"seen": {}}))
        (tmp / "watchlist.txt").write_text("greenway\n")
        self._mk_clip(clips, 400, "2026-07-15")
        assert ma.run_scan(dry_run=True, with_leads=False) == 0
        assert json.loads((tmp / "alerts_state.json").read_text()) == {"seen": {}}
        assert "DRY RUN" in capsys.readouterr().out

    def test_max_clips_per_run_defers_excess(self, sandbox, monkeypatch, capsys):
        tmp, clips = sandbox
        (tmp / "alerts_state.json").write_text(json.dumps({"seen": {}}))
        monkeypatch.setattr(ma, "MAX_CLIPS_PER_RUN", 2)
        for cid in (500, 501, 502):
            self._mk_clip(clips, cid, "2026-07-15")
        monkeypatch.setattr(ma, "send_email", lambda s, b: True)
        assert ma.run_scan(dry_run=False, with_leads=False) == 0
        state = json.loads((tmp / "alerts_state.json").read_text())
        assert "500" in state["seen"] and "501" in state["seen"]
        assert "502" not in state["seen"]  # rolls to next run
        assert "deferred" in capsys.readouterr().out
