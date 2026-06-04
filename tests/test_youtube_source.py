"""Tests for YouTubeSource (WS3 — the YouTube ingest adapter).

The whole adapter is exercised with ``yt-dlp`` MOCKED — no network. Covers:
  (a) list_meetings parses a --dump-single-json payload into MeetingRefs with
      assigned int ids.
  (b) id-map persistence + stability: running list_meetings twice doesn't
      reassign ids; a new video gets max+1; the map round-trips through
      source_ids.json.
  (c) reverse-map: get_clip_title / canonical_url / download_audio resolve
      int→videoId correctly.
  (d) canonical_url format.
  (e) make_source(cfg with source_type=youtube) returns YouTubeSource.
  (f) empty-shaped agenda/minutes dicts match GranicusSource's keys exactly.
  (g) Protocol conformance: isinstance(YouTubeSource(...), VideoSource).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from sources import GranicusSource, MeetingRef, VideoSource, YouTubeSource, make_source


def _noop_log(msg: str, level: str = "INFO") -> None:
    return None


def _yt_cfg(tmp_path: Path, channel_url: str = "https://www.youtube.com/@CityofParisKY", start_id: int = 1):
    """A minimal YouTube jurisdiction config (duck-typed).

    Carries a Granicus `first_clip_id=6669` to PROVE YouTube ignores it and
    seeds from `source_youtube_start_id` instead.
    """
    return SimpleNamespace(
        source_type="youtube",
        source_youtube_channel_url=channel_url,
        source_youtube_title_date_pattern="",
        source_youtube_start_id=start_id,
        first_clip_id=6669,
        default_view_id="",
        output_dir=str(tmp_path),
    )


# A representative `yt-dlp --flat-playlist --dump-single-json <channel>`
# payload: three uploads, out of chronological order on purpose.
_CHANNEL_JSON = {
    "id": "UCfake",
    "title": "City of Paris KY",
    "entries": [
        {"id": "vidB", "title": "Commission Meeting June 10", "upload_date": "20260610"},
        {"id": "vidA", "title": "Commission Meeting May 13", "upload_date": "20260513"},
        {"id": "vidC", "title": "Commission Meeting July 8", "upload_date": "20260708"},
    ],
}


def _mock_enumerate(payload):
    """Return a MagicMock CompletedProcess wrapping `payload` as JSON stdout."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = json.dumps(payload)
    return result


def _mock_print(stdout: str, returncode: int = 0):
    """Return a MagicMock CompletedProcess for a `yt-dlp --print` call."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


def _seed_id_map(tmp_path: Path, videos: dict) -> None:
    """Write source_ids.json directly so tests can control title/date=None."""
    (tmp_path / "source_ids.json").write_text(
        json.dumps({"version": 1, "videos": videos}, indent=2)
    )


# ---------------------------------------------------------------------------
# (a) list_meetings parsing + (b) id assignment chronological-ish
# ---------------------------------------------------------------------------

class TestListMeetings:
    def test_parses_payload_into_refs_with_int_ids(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            refs = src.list_meetings()

        # Sorted oldest-first: vidA (May) -> vidB (June) -> vidC (July).
        assert [r.date for r in refs] == ["2026-05-13", "2026-06-10", "2026-07-08"]
        assert [r.title for r in refs] == [
            "Commission Meeting May 13",
            "Commission Meeting June 10",
            "Commission Meeting July 8",
        ]
        # int ids assigned chronologically, seeded at start_id=1 (default).
        assert [r.clip_id for r in refs] == ["1", "2", "3"]
        # body is None (taxonomy parse stays in main.py).
        assert all(r.body is None for r in refs)

    def test_ids_seed_at_one_not_granicus_first_clip_id(self, tmp_path):
        # cfg carries first_clip_id=6669 (Granicus's), but YouTube must IGNORE
        # it and seed at source_youtube_start_id (default 1).
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        assert src.start_id == 1
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            refs = src.list_meetings()
        assert [r.clip_id for r in refs] == ["1", "2", "3"]

    def test_start_id_is_configurable(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path, start_id=100), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            refs = src.list_meetings()
        assert [r.clip_id for r in refs] == ["100", "101", "102"]

    def test_empty_channel_url_returns_empty(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path, channel_url=""), _noop_log)
        # subprocess.run should never be called.
        with patch("sources.youtube.subprocess.run") as m:
            assert src.list_meetings() == []
            m.assert_not_called()

    def test_enumerate_nonzero_returns_empty(self, tmp_path):
        # yt-dlp exits non-zero (e.g. channel unreachable) → graceful [].
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch(
            "sources.youtube.subprocess.run",
            return_value=_mock_print("", returncode=1),
        ):
            assert src.list_meetings() == []
        # No map written for a failed enumeration.
        assert not (tmp_path / "source_ids.json").exists()

    def test_enumerate_timeout_returns_empty(self, tmp_path):
        # yt-dlp hangs and times out → graceful [] (cron must not crash).
        import subprocess as _sp

        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch(
            "sources.youtube.subprocess.run",
            side_effect=_sp.TimeoutExpired(cmd="yt-dlp", timeout=120),
        ):
            assert src.list_meetings() == []

    def test_enumerate_bad_json_returns_empty(self, tmp_path):
        # yt-dlp prints non-JSON → graceful [].
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch(
            "sources.youtube.subprocess.run",
            return_value=_mock_print("not json at all"),
        ):
            assert src.list_meetings() == []


# ---------------------------------------------------------------------------
# (b) id-map persistence + stability
# ---------------------------------------------------------------------------

class TestIdMapStability:
    def test_running_twice_does_not_reassign(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            first = {r.title: r.clip_id for r in src.list_meetings()}
            second = {r.title: r.clip_id for r in src.list_meetings()}
        assert first == second

    def test_new_video_gets_max_plus_one(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            src.list_meetings()  # ids 1,2,3 for vidA,vidB,vidC

        # A 4th upload appears (newest). It must get id 4 even though it sorts
        # last by date — assignment is max(existing)+1, not positional.
        payload2 = json.loads(json.dumps(_CHANNEL_JSON))
        payload2["entries"].append(
            {"id": "vidD", "title": "Commission Meeting Aug 12", "upload_date": "20260812"}
        )
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(payload2)):
            refs = src.list_meetings()

        by_id = {r.clip_id: r.title for r in refs}
        assert by_id["4"] == "Commission Meeting Aug 12"
        # Original three are unchanged.
        assert by_id["1"] == "Commission Meeting May 13"
        assert by_id["2"] == "Commission Meeting June 10"
        assert by_id["3"] == "Commission Meeting July 8"

    def test_map_round_trips_through_disk(self, tmp_path):
        cfg = _yt_cfg(tmp_path)
        src = YouTubeSource(cfg, _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            src.list_meetings()

        # File exists with the chosen schema.
        path = tmp_path / "source_ids.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == 1
        assert set(data["videos"].keys()) == {"vidA", "vidB", "vidC"}
        assert data["videos"]["vidA"]["clip_id"] == 1
        assert data["videos"]["vidA"]["date"] == "2026-05-13"
        assert data["videos"]["vidA"]["title"] == "Commission Meeting May 13"

        # A fresh instance loads the same assignments (no network needed).
        src2 = YouTubeSource(cfg, _noop_log)
        assert src2._video_id_for(1) == "vidA"
        assert src2._video_id_for(2) == "vidB"
        assert src2._video_id_for(3) == "vidC"

    def test_known_video_id_stable_across_reorder(self, tmp_path):
        """If a later enumeration returns entries in a different order, the
        already-assigned ids must not move."""
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            before = {r.title: r.clip_id for r in src.list_meetings()}

        reordered = json.loads(json.dumps(_CHANNEL_JSON))
        reordered["entries"] = list(reversed(reordered["entries"]))
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(reordered)):
            after = {r.title: r.clip_id for r in src.list_meetings()}
        assert before == after


# ---------------------------------------------------------------------------
# (c) + (d) reverse-map resolution + canonical_url format
# ---------------------------------------------------------------------------

class TestReverseMap:
    def _seeded(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            src.list_meetings()
        return src

    def test_get_clip_title_uses_cached_map(self, tmp_path):
        src = self._seeded(tmp_path)
        # No subprocess needed — title is cached in the map.
        with patch("sources.youtube.subprocess.run") as m:
            assert src.get_clip_title(1) == "Commission Meeting May 13"
            m.assert_not_called()

    def test_canonical_url_format(self, tmp_path):
        src = self._seeded(tmp_path)
        assert src.canonical_url(1) == "https://www.youtube.com/watch?v=vidA"
        assert src.canonical_url(3) == "https://www.youtube.com/watch?v=vidC"

    def test_canonical_url_unmapped_does_not_crash(self, tmp_path):
        src = self._seeded(tmp_path)
        # Unknown id -> safe fallback, no exception.
        assert src.canonical_url(99999) == "https://www.youtube.com/"

    def test_get_metadata_reverse_maps_to_date(self, tmp_path):
        src = self._seeded(tmp_path)
        with patch("sources.youtube.subprocess.run") as m:
            meta = src.get_metadata(MeetingRef(clip_id="2"))
            m.assert_not_called()
        assert meta == {"date": "2026-06-10", "title": "Commission Meeting June 10"}

    def test_get_clip_title_fetches_when_cache_title_none(self, tmp_path):
        # Map entry has title=None → must do a live per-video yt-dlp call,
        # return the fetched title, and write it back into the map.
        _seed_id_map(tmp_path, {"vidA": {"clip_id": 1, "title": None, "date": "2026-01-01"}})
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)

        with patch(
            "sources.youtube.subprocess.run",
            return_value=_mock_print("Fetched Title\n"),
        ) as m:
            title = src.get_clip_title(1)

        # (a) subprocess called with the correct watch URL.
        assert m.call_count == 1
        called_cmd = m.call_args.args[0]
        assert "https://www.youtube.com/watch?v=vidA" in called_cmd
        # (b) the fetched title is returned.
        assert title == "Fetched Title"
        # (c) cached back into the in-memory map AND persisted to disk.
        assert src._videos["vidA"]["title"] == "Fetched Title"
        on_disk = json.loads((tmp_path / "source_ids.json").read_text())
        assert on_disk["videos"]["vidA"]["title"] == "Fetched Title"

    def test_get_metadata_fetches_when_cache_date_none(self, tmp_path):
        # Map entry has date=None → must run the single-video --print fallback,
        # return the fetched date, and cache it back.
        _seed_id_map(tmp_path, {"vidA": {"clip_id": 1, "title": None, "date": None}})
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)

        with patch(
            "sources.youtube.subprocess.run",
            return_value=_mock_print("20260513\tCommission Meeting May 13\n"),
        ) as m:
            meta = src.get_metadata(MeetingRef(clip_id="1"))

        assert m.call_count == 1
        called_cmd = m.call_args.args[0]
        assert "https://www.youtube.com/watch?v=vidA" in called_cmd
        assert meta == {"date": "2026-05-13", "title": "Commission Meeting May 13"}
        # Cached back to the map + disk.
        assert src._videos["vidA"]["date"] == "2026-05-13"
        on_disk = json.loads((tmp_path / "source_ids.json").read_text())
        assert on_disk["videos"]["vidA"]["date"] == "2026-05-13"

    def test_get_metadata_unavailable_video_degrades(self, tmp_path):
        # Deleted/unavailable video: yt-dlp returns non-zero/blank → no crash,
        # date/title stay None.
        _seed_id_map(tmp_path, {"vidA": {"clip_id": 1, "title": None, "date": None}})
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_print("", returncode=1)):
            meta = src.get_metadata(MeetingRef(clip_id="1"))
        assert meta == {"date": None, "title": None}

    def test_scrape_available_clips_returns_sorted_ints(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            assert src.scrape_available_clips() == [1, 2, 3]

    def test_download_audio_resolves_int_to_watch_url(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "2"
        clip_dir.mkdir(parents=True)

        captured = {}

        class _FakeProc:
            returncode = 0

            def __init__(self):
                self.stdout = self  # readline source

            def readline(self):
                return b""  # immediate EOF

            def wait(self):
                return 0

            def kill(self):
                pass

        def _fake_popen(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            # yt-dlp would write the file; simulate that.
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"\x00" * 1024)
            return _FakeProc()

        with patch("sources.youtube.subprocess.Popen", side_effect=_fake_popen):
            fn = src.download_audio(2, clip_dir, title="Commission Meeting June 10", date="2026-06-10")

        # Filename follows the GranicusSource convention exactly.
        assert fn == "2026-06-10_Commission_Meeting_June_10_audio.mp3"
        assert (clip_dir / fn).exists()
        # The yt-dlp command targeted the correct reverse-mapped watch URL.
        assert "https://www.youtube.com/watch?v=vidB" in captured["cmd"]
        # And used the same audio flags as GranicusSource.
        assert "-x" in captured["cmd"]
        assert "--audio-format" in captured["cmd"]
        assert "mp3" in captured["cmd"]

    def test_download_audio_unmapped_returns_none(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "nope"
        clip_dir.mkdir(parents=True)
        with patch("sources.youtube.subprocess.Popen") as m:
            assert src.download_audio(99999, clip_dir) is None
            m.assert_not_called()


# ---------------------------------------------------------------------------
# (e) make_source factory
# ---------------------------------------------------------------------------

class TestMakeSource:
    def test_youtube_for_youtube_type(self, tmp_path):
        cfg = _yt_cfg(tmp_path)
        src = make_source(cfg, _noop_log)
        assert isinstance(src, YouTubeSource)

    def test_granicus_still_default(self):
        cfg = SimpleNamespace(
            granicus_host="lfucg.granicus.com",
            default_view_id=14,
            listing_view_fallbacks=(14, 9),
            source_type="granicus",
        )
        assert isinstance(make_source(cfg, _noop_log), GranicusSource)


# ---------------------------------------------------------------------------
# (f) empty-shaped agenda/minutes dicts match GranicusSource keys
# ---------------------------------------------------------------------------

class TestEmptyDocsShape:
    def test_agenda_keys_match_granicus(self, tmp_path):
        yt = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        from config import get_config

        gran = GranicusSource(get_config(), _noop_log)
        yt_agenda = yt.download_agenda(1, tmp_path)
        assert yt_agenda == {"pdf_file": None, "txt_file": None, "text": None}
        # Same keys GranicusSource returns when no agenda exists.
        with patch("sources.granicus.requests.get", side_effect=Exception("no network")):
            gran_agenda = gran.download_agenda(1, tmp_path)
        assert set(yt_agenda.keys()) == set(gran_agenda.keys())

    def test_minutes_keys_match_granicus(self, tmp_path):
        yt = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        from config import get_config

        gran = GranicusSource(get_config(), _noop_log)
        yt_minutes = yt.download_minutes(1, tmp_path)
        assert yt_minutes == {"pdf_file": None, "html_file": None, "txt_file": None, "text": None}
        with patch("sources.granicus.requests.get", side_effect=Exception("no network")):
            gran_minutes = gran.download_minutes(1, tmp_path)
        assert set(yt_minutes.keys()) == set(gran_minutes.keys())


# ---------------------------------------------------------------------------
# (g) Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_youtube_source_satisfies_protocol(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        assert isinstance(src, VideoSource)

    def test_mirrors_settable_attributes(self, tmp_path):
        """The pipeline syncs view_id/force_reprocess/progress onto the source
        (main.py). YouTubeSource must accept those writes like GranicusSource."""
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        src.view_id = "9"
        src.force_reprocess = True
        src.progress = lambda msg: None
        assert src.view_id == "9"
        assert src.force_reprocess is True


# ---------------------------------------------------------------------------
# fetch_captions uses --write-auto-subs (YouTube auto captions)
# ---------------------------------------------------------------------------

class TestFetchCaptions:
    def test_uses_auto_subs_flag(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        with patch("sources.youtube.subprocess.run", return_value=_mock_enumerate(_CHANNEL_JSON)):
            src.list_meetings()

        clip_dir = tmp_path / "clips" / "1"
        clip_dir.mkdir(parents=True)
        captured = {}

        def _fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            # Simulate yt-dlp writing <template>.en.vtt
            out_idx = cmd.index("-o") + 1
            template = cmd[out_idx].replace(".%(ext)s", "")
            Path(template + ".en.vtt").write_text("WEBVTT\n\n")
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("sources.youtube.subprocess.run", side_effect=_fake_run):
            result = src.fetch_captions(1, clip_dir)

        assert result == clip_dir / "captions.vtt"
        assert result.exists()
        assert "--write-auto-subs" in captured["cmd"]
        assert "https://www.youtube.com/watch?v=vidA" in captured["cmd"]

    def test_returns_cached_vtt_without_refetch(self, tmp_path):
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        clip_dir = tmp_path / "clips" / "1"
        clip_dir.mkdir(parents=True)
        (clip_dir / "captions.vtt").write_text("WEBVTT\n")
        with patch("sources.youtube.subprocess.run") as m:
            assert src.fetch_captions(1, clip_dir) == clip_dir / "captions.vtt"
            m.assert_not_called()

    def test_unmapped_clip_returns_none_without_subprocess(self, tmp_path):
        # No id-map entry for this clip → return None WITHOUT calling yt-dlp.
        src = YouTubeSource(_yt_cfg(tmp_path), _noop_log)
        clip_dir = tmp_path / "clips" / "99999"
        clip_dir.mkdir(parents=True)
        with patch("sources.youtube.subprocess.run") as m:
            assert src.fetch_captions(99999, clip_dir) is None
            m.assert_not_called()


# ---------------------------------------------------------------------------
# main.py integration: --auto self-enumeration for non-Granicus sources, and
# the source_type guard keeping the Granicus path byte-identical.
# ---------------------------------------------------------------------------

class _FakeListSource:
    """Minimal source exposing only list_meetings (what the refresh uses)."""

    def __init__(self, refs):
        self._refs = refs

    def list_meetings(self):
        return self._refs


def _make_pipeline(tmp_path):
    import os

    os.environ.setdefault("OPENAI_API_KEY", "sk-test")
    from main import LFUCGPipeline

    return LFUCGPipeline(output_dir=str(tmp_path), verbose=False)


class TestAutoEnumeration:
    def test_refresh_writes_available_clips_json(self, tmp_path):
        pipe = _make_pipeline(tmp_path)
        pipe.source = _FakeListSource([
            MeetingRef(clip_id="3", title="Newest"),
            MeetingRef(clip_id="1", title="Oldest"),
            MeetingRef(clip_id="2", title=None),
        ])
        pipe._refresh_available_clips_from_source()

        data = json.loads((tmp_path / "available_clips.json").read_text())
        assert data["total_found"] == 3
        # Sorted by clip_id ascending; clip_id coerced str -> int.
        assert data["clips"] == [
            {"clip_id": 1, "title": "Oldest"},
            {"clip_id": 2, "title": None},
            {"clip_id": 3, "title": "Newest"},
        ]
        # And load_available_clips reads it back as sorted ints.
        assert pipe.load_available_clips() == [1, 2, 3]

    def test_refresh_degrades_on_source_error(self, tmp_path):
        pipe = _make_pipeline(tmp_path)

        class _Boom:
            def list_meetings(self):
                raise RuntimeError("yt-dlp exploded")

        pipe.source = _Boom()
        # Must NOT raise (cron --auto must survive).
        pipe._refresh_available_clips_from_source()
        assert not (tmp_path / "available_clips.json").exists()

    def test_auto_process_refreshes_for_youtube_source_type(self, tmp_path):
        from types import SimpleNamespace

        pipe = _make_pipeline(tmp_path)
        # Flip the resolved cfg to a YouTube source_type (frozen dataclass →
        # swap a duck-typed namespace carrying just what auto_process reads).
        pipe.cfg = SimpleNamespace(source_type="youtube")
        # Empty available list + patched process_range/retries → no real work.
        with patch.object(pipe, "_refresh_available_clips_from_source") as refresh, \
                patch.object(pipe, "load_available_clips", return_value=[]), \
                patch.object(pipe, "_retry_candidates", return_value=[]), \
                patch.object(pipe, "process_range", return_value={}):
            pipe.auto_process(max_clips=1)
        refresh.assert_called_once()

    def test_auto_process_does_not_refresh_for_granicus(self, tmp_path):
        pipe = _make_pipeline(tmp_path)
        # Default LFUCG cfg → source_type == "granicus".
        assert pipe.cfg.source_type == "granicus"
        with patch.object(pipe, "_refresh_available_clips_from_source") as refresh, \
                patch.object(pipe, "load_available_clips", return_value=[]), \
                patch.object(pipe, "_retry_candidates", return_value=[]), \
                patch.object(pipe, "process_range", return_value={}):
            pipe.auto_process(max_clips=1)
        refresh.assert_not_called()
