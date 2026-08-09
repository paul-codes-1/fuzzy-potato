"""Tests for CivicClerkSource (PR-6 — the primary DOCUMENT-DRIVEN source).

All HTTP is MOCKED — no live network (a real-API smoke against parisky is fine
to run by hand, but never in the suite). Covers:
  (a) list_meetings enumerates /v1/Events into MeetingRefs with int ids, and
      the synthetic event-id → clip-id map is stable/monotonic across runs.
  (b) reverse-map resolution: get_clip_title / get_metadata / canonical_url /
      download_agenda / download_minutes resolve int → event id correctly.
  (c) agenda/minutes fetch return the SAME dict shape GranicusSource returns
      (parity), and reuse the WS4 CivicClerk API code (extract_pdf_text).
  (d) download_audio / fetch_captions return None (no media for a doc source).
  (e) make_source(cfg with source_type=civicclerk) returns CivicClerkSource.
  (f) Protocol conformance: isinstance(CivicClerkSource(...), VideoSource).
  (g) the minutes-as-content pipeline path: process_clip on a document-driven
      source writes a transcript artifact with transcript_source ==
      "civicclerk_minutes" (falling back to "civicclerk_agenda").
  (h) Granicus/YouTube do NOT trigger the minutes-as-content branch
      (byte-identity guard).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sources import (
    CivicClerkSource,
    GranicusSource,
    MeetingRef,
    VideoSource,
    YouTubeSource,
    make_source,
)
from sources.agenda_base import empty_agenda_result, empty_minutes_result


def _noop_log(msg: str, level: str = "INFO") -> None:
    return None


def _cc_cfg(tmp_path: Path, *, portal: str = "https://parisky.portal.civicclerk.com",
            start_id: int = 1):
    """A minimal CivicClerk (document-driven) jurisdiction config (duck-typed).

    Carries a Granicus first_clip_id=6669 to PROVE CivicClerk ignores it and
    seeds the synthetic id space from start_id instead.
    """
    return SimpleNamespace(
        source_type="civicclerk",
        agenda_type="civicclerk",
        agenda_api_base="https://parisky.api.civicclerk.com",
        agenda_portal=portal,
        agenda_base_url="",
        source_youtube_start_id=start_id,
        first_clip_id=6669,
        default_view_id="",
        output_dir=str(tmp_path),
    )


# A representative /v1/Events page (newest-first), three events. The newest
# carries an Agenda + Agenda Packet + Minutes; the others vary.
_EVENTS_PAGE = [
    {
        "id": 322,
        "eventName": "City Commission Meeting",
        "startDateTime": "2026-05-12T09:00:00Z",
        "eventCategoryName": "City Commission",
        "categoryName": "City Commission",
        "publishedFiles": [
            {"fileId": 844, "type": "Agenda", "fileType": 1,
             "name": "05.12.2026 Agenda", "url": "stream/PARISKY/a.pdf"},
            {"fileId": 848, "type": "Agenda Packet", "fileType": 2,
             "name": "05.12.2026 Packet", "url": "stream/PARISKY/p.pdf"},
            {"fileId": 858, "type": "Minutes", "fileType": 4,
             "name": "05.12.2026 Minutes", "url": "stream/PARISKY/m.pdf"},
        ],
    },
    {
        "id": 300,
        "eventName": "City Commission Meeting",
        "startDateTime": "2026-04-14T09:00:00Z",
        "eventCategoryName": "City Commission",
        "categoryName": "City Commission",
        "publishedFiles": [
            {"fileId": 800, "type": "Agenda Packet", "fileType": 2,
             "name": "04.14.2026 Packet", "url": "stream/PARISKY/p2.pdf"},
        ],
    },
    {
        "id": 280,
        "eventName": "Planning Commission",
        "startDateTime": "2026-03-10T18:00:00Z",
        "eventCategoryName": "Planning Commission",
        "categoryName": "Planning Commission",
        "publishedFiles": [],
    },
]


def _mock_pdf_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/pdf"}
    resp.content = b"%PDF-1.7\n...fake..."
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# (a) list_meetings + id-map stability
# ---------------------------------------------------------------------------

class TestListMeetings:
    def test_parses_events_into_refs_with_int_ids(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            refs = src.list_meetings()

        # Sorted oldest-first: 280 (Mar) -> 300 (Apr) -> 322 (May).
        assert [r.date for r in refs] == ["2026-03-10", "2026-04-14", "2026-05-12"]
        assert [r.title for r in refs] == [
            "Planning Commission",
            "City Commission Meeting",
            "City Commission Meeting",
        ]
        # int ids assigned chronologically, seeded at start_id=1.
        assert [r.clip_id for r in refs] == ["1", "2", "3"]
        # body carried from the CivicClerk category (not None like YouTube).
        assert [r.body for r in refs] == [
            "Planning Commission", "City Commission", "City Commission"]

    def test_ids_seed_at_start_id_not_granicus_first_clip_id(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        assert src.start_id == 1
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            refs = src.list_meetings()
        assert [r.clip_id for r in refs] == ["1", "2", "3"]

    def test_start_id_configurable(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path, start_id=100), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            refs = src.list_meetings()
        assert [r.clip_id for r in refs] == ["100", "101", "102"]

    def test_running_twice_does_not_reassign(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            first = {r.title + r.date: r.clip_id for r in src.list_meetings()}
            second = {r.title + r.date: r.clip_id for r in src.list_meetings()}
        assert first == second

    def test_new_event_gets_max_plus_one(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            src.list_meetings()  # ids 1,2,3

        page2 = list(_EVENTS_PAGE) + [{
            "id": 400, "eventName": "City Commission Meeting",
            "startDateTime": "2026-06-09T09:00:00Z",
            "eventCategoryName": "City Commission", "categoryName": "City Commission",
            "publishedFiles": [],
        }]
        with patch.object(src, "_list_events", return_value=page2):
            refs = src.list_meetings()
        by_id = {r.clip_id: r.date for r in refs}
        # The new (newest) event gets max+1 == 4, even though it sorts last.
        assert by_id["4"] == "2026-06-09"
        # Original three unchanged.
        assert by_id["1"] == "2026-03-10"
        assert by_id["3"] == "2026-05-12"

    def test_map_round_trips_through_disk(self, tmp_path):
        cfg = _cc_cfg(tmp_path)
        src = CivicClerkSource(cfg, _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            src.list_meetings()

        path = tmp_path / "source_ids.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == 1
        assert set(data["videos"].keys()) == {"322", "300", "280"}
        # 280 (oldest) got clip_id 1.
        assert data["videos"]["280"]["clip_id"] == 1
        assert data["videos"]["280"]["date"] == "2026-03-10"

        # A fresh instance loads the same assignments (no network).
        src2 = CivicClerkSource(cfg, _noop_log)
        assert src2._event_id_for(1) == "280"
        assert src2._event_id_for(3) == "322"

    def test_empty_api_base_returns_empty(self, tmp_path):
        cfg = _cc_cfg(tmp_path)
        cfg.agenda_api_base = ""
        src = CivicClerkSource(cfg, _noop_log)
        # _list_events bails without api_base → no requests.
        with patch("sources.civicclerk_source.requests.get") as m:
            assert src.list_meetings() == []
            m.assert_not_called()

    def test_scrape_available_clips_returns_sorted_ints(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            assert src.scrape_available_clips() == [1, 2, 3]


# ---------------------------------------------------------------------------
# _list_events pagination + filter shape
# ---------------------------------------------------------------------------

class TestListEventsPagination:
    def test_paginates_until_short_page(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        calls = []

        def fake_get(url, params=None, timeout=None, headers=None):
            calls.append(params)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            # Page 0 full (page_size), page 1 short → stop.
            skip = params["$skip"]
            if skip == 0:
                resp.json = MagicMock(return_value={"value": [{"id": i} for i in range(100)]})
            else:
                resp.json = MagicMock(return_value={"value": [{"id": 999}]})
            return resp

        with patch("sources.civicclerk_source.requests.get", side_effect=fake_get):
            events = src._list_events(page_size=100, max_pages=10)

        assert len(events) == 101
        assert len(calls) == 2  # stopped after the short page
        # OData filter restricts to past events.
        assert "startDateTime le " in calls[0]["$filter"]
        assert calls[0]["$orderby"] == "startDateTime desc"
        assert calls[0]["$skip"] == 0
        assert calls[1]["$skip"] == 100

    def test_network_error_returns_what_it_has(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch("sources.civicclerk_source.requests.get",
                   side_effect=Exception("boom")):
            assert src._list_events() == []

    def test_max_pages_cap_logs_warning(self, tmp_path):
        """PR-6 fix #5: when every page is full and we exhaust max_pages, warn
        that the catalog may be truncated (so a larger jurisdiction notices)."""
        logs = []
        src = CivicClerkSource(_cc_cfg(tmp_path), lambda msg, level="INFO": logs.append((level, msg)))

        def fake_get(url, params=None, timeout=None, headers=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            # ALWAYS return a full page → never short-circuits → cap is hit.
            resp.json = MagicMock(return_value={"value": [{"id": i} for i in range(10)]})
            return resp

        with patch("sources.civicclerk_source.requests.get", side_effect=fake_get):
            events = src._list_events(page_size=10, max_pages=3)

        assert len(events) == 30  # 3 full pages
        warnings = [m for lvl, m in logs if lvl == "WARNING"]
        assert any("max_pages=3" in m and "truncated" in m for m in warnings)

    def test_short_page_does_not_log_cap_warning(self, tmp_path):
        """The normal end-of-catalog path (short final page) must NOT warn."""
        logs = []
        src = CivicClerkSource(_cc_cfg(tmp_path), lambda msg, level="INFO": logs.append((level, msg)))

        def fake_get(url, params=None, timeout=None, headers=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            skip = params["$skip"]
            resp.json = MagicMock(
                return_value={"value": [{"id": i} for i in range(10)]} if skip == 0
                else {"value": [{"id": 99}]})  # short page → stop
            return resp

        with patch("sources.civicclerk_source.requests.get", side_effect=fake_get):
            src._list_events(page_size=10, max_pages=50)

        assert not any("truncated" in m for lvl, m in logs)


# ---------------------------------------------------------------------------
# (b) reverse-map resolution + (canonical_url)
# ---------------------------------------------------------------------------

class TestReverseMap:
    def _seeded(self, tmp_path, **kw):
        src = CivicClerkSource(_cc_cfg(tmp_path, **kw), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            src.list_meetings()
        return src

    def test_get_clip_title_uses_cached_map(self, tmp_path):
        src = self._seeded(tmp_path)
        assert src.get_clip_title(3) == "City Commission Meeting"

    def test_get_metadata_returns_date_and_title(self, tmp_path):
        src = self._seeded(tmp_path)
        assert src.get_metadata(MeetingRef(clip_id="3")) == {
            "date": "2026-05-12", "title": "City Commission Meeting"}

    def test_get_metadata_unmapped(self, tmp_path):
        src = self._seeded(tmp_path)
        assert src.get_metadata(MeetingRef(clip_id="99999")) == {
            "date": None, "title": None}

    def test_canonical_url_uses_portal(self, tmp_path):
        src = self._seeded(tmp_path)
        assert src.canonical_url(3) == (
            "https://parisky.portal.civicclerk.com/event/322")

    def test_canonical_url_falls_back_to_api_when_no_portal(self, tmp_path):
        src = self._seeded(tmp_path, portal="")
        assert src.canonical_url(3) == (
            "https://parisky.api.civicclerk.com/v1/Events(322)")

    def test_canonical_url_unmapped_does_not_crash(self, tmp_path):
        src = self._seeded(tmp_path)
        # Unknown id → base portal, no exception.
        assert src.canonical_url(99999) == "https://parisky.portal.civicclerk.com"


# ---------------------------------------------------------------------------
# (c) agenda/minutes fetch + shape parity (reuses WS4 API code)
# ---------------------------------------------------------------------------

class TestDocumentFetch:
    def _seeded(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch.object(src, "_list_events", return_value=list(_EVENTS_PAGE)):
            src.list_meetings()
        return src

    def test_download_agenda_fetches_packet_and_extracts(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "3"
        clip_dir.mkdir(parents=True)
        # clip 3 -> event 322 -> date 2026-05-12, body "City Commission".
        with patch.object(src._api, "_events_for_date",
                          return_value=list(_EVENTS_PAGE)), \
             patch("sources.civicclerk.requests.get", return_value=_mock_pdf_response()), \
             patch("sources.civicclerk.extract_pdf_text", return_value="AGENDA BODY"):
            result = src.download_agenda(3, clip_dir)
        assert result["text"] == "AGENDA BODY"
        # GranicusSource agenda shape.
        assert set(result.keys()) == {"pdf_file", "txt_file", "text"}

    def test_download_minutes_fetches_and_extracts(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "3"
        clip_dir.mkdir(parents=True)
        with patch.object(src._api, "_events_for_date",
                          return_value=list(_EVENTS_PAGE)), \
             patch("sources.civicclerk.requests.get", return_value=_mock_pdf_response()), \
             patch("sources.civicclerk.extract_pdf_text", return_value="MINUTES BODY"):
            result = src.download_minutes(3, clip_dir)
        assert result["text"] == "MINUTES BODY"
        # GranicusSource minutes shape.
        assert set(result.keys()) == {"pdf_file", "html_file", "txt_file", "text"}

    def test_agenda_shape_matches_granicus_on_miss(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "3"
        clip_dir.mkdir(parents=True)
        # No same-day event → empty agenda shape.
        with patch.object(src._api, "_events_for_date", return_value=[]):
            result = src.download_agenda(3, clip_dir)
        assert result == empty_agenda_result()

    def test_minutes_shape_matches_granicus_on_miss(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "3"
        clip_dir.mkdir(parents=True)
        with patch.object(src._api, "_events_for_date", return_value=[]):
            result = src.download_minutes(3, clip_dir)
        assert result == empty_minutes_result()

    def test_download_agenda_unmapped_no_date_returns_empty(self, tmp_path):
        src = self._seeded(tmp_path)
        clip_dir = tmp_path / "clips" / "99999"
        clip_dir.mkdir(parents=True)
        # Unmapped clip + no explicit date → empty shape (no fetch).
        with patch("sources.civicclerk.requests.get") as m:
            result = src.download_agenda(99999, clip_dir)
            m.assert_not_called()
        assert result == empty_agenda_result()


# ---------------------------------------------------------------------------
# (d) no media for a document source
# ---------------------------------------------------------------------------

class TestNoMedia:
    def test_download_audio_returns_none(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch("sources.civicclerk_source.requests.get") as m:
            assert src.download_audio(1, tmp_path) is None
            m.assert_not_called()

    def test_fetch_captions_returns_none(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        with patch("sources.civicclerk_source.requests.get") as m:
            assert src.fetch_captions(1, tmp_path) is None
            m.assert_not_called()


# ---------------------------------------------------------------------------
# (e) make_source factory
# ---------------------------------------------------------------------------

class TestMakeSource:
    def test_civicclerk_for_civicclerk_type(self, tmp_path):
        src = make_source(_cc_cfg(tmp_path), _noop_log)
        assert isinstance(src, CivicClerkSource)

    def test_granicus_still_default(self):
        cfg = SimpleNamespace(
            granicus_host="lfucg.granicus.com", default_view_id=14,
            listing_view_fallbacks=(14, 9), source_type="granicus")
        assert isinstance(make_source(cfg, _noop_log), GranicusSource)


# ---------------------------------------------------------------------------
# (f) settable attributes + (g) protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_satisfies_protocol(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        assert isinstance(src, VideoSource)

    def test_mirrors_settable_attributes(self, tmp_path):
        src = CivicClerkSource(_cc_cfg(tmp_path), _noop_log)
        src.view_id = "9"
        src.force_reprocess = True
        src.progress = lambda msg: None
        assert src.view_id == "9"
        assert src.force_reprocess is True
        # force_reprocess + progress proxy onto the composed API adapter.
        assert src._api.force_reprocess is True


# ---------------------------------------------------------------------------
# (g) + (h) minutes-as-content pipeline path + byte-identity guard
# ---------------------------------------------------------------------------

def _make_pipeline(tmp_path):
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")
    from main import LFUCGPipeline
    return LFUCGPipeline(output_dir=str(tmp_path), verbose=False)


class TestMinutesAsContent:
    def _doc_driven_pipeline(self, tmp_path):
        pipe = _make_pipeline(tmp_path)
        # Flip the pipeline to a document-driven source. We don't drive the
        # real CivicClerk API in process_clip — we patch the source's doc
        # fetches + captions + title/date so the branch is exercised in
        # isolation.
        pipe.cfg = SimpleNamespace(source_type="civicclerk")
        # agenda_source must be None on the document-driven path (the primary
        # source owns documents); assert that contract in a dedicated test.
        pipe.agenda_source = None
        return pipe

    def test_process_clip_writes_minutes_as_transcript(self, tmp_path):
        pipe = self._doc_driven_pipeline(tmp_path)
        clip_dir = tmp_path / "clips" / "5"

        src = MagicMock()
        src.get_clip_title.return_value = "City Commission Meeting"
        src.canonical_url.return_value = "https://parisky.portal.civicclerk.com/event/322"
        src.fetch_captions.return_value = None
        src.download_audio.return_value = None  # document-driven: no audio
        src.download_minutes.return_value = {
            "pdf_file": "2026-05-12_minutes_civicclerk.pdf",
            "html_file": None,
            "txt_file": "2026-05-12_minutes_civicclerk.txt",
            "text": "Motion by Smith to approve the budget. Passed 5-0.",
        }
        src.download_agenda.return_value = {
            "pdf_file": "2026-05-12_agenda_civicclerk.pdf",
            "txt_file": "2026-05-12_agenda_civicclerk.txt",
            "text": "I. Call to order. II. Budget.",
        }
        pipe.source = src

        # No captions, audio None → document-driven branch. apply_captions
        # uses the source's fetch_captions (None) → no transcript_text.
        with patch.object(pipe, "scrape_clip_metadata",
                          return_value={"date": "2026-05-12",
                                        "meeting_body": "City Commission",
                                        "title": "City Commission Meeting"}), \
             patch.object(pipe, "generate_search_index"), \
             patch.object(pipe, "apply_tables_from_agenda"):
            ok = pipe.process_clip(5)

        assert ok is True
        meta = json.loads((clip_dir / "metadata.json").read_text())
        # The KEY assertion: minutes drove the transcript.
        assert meta["transcript_source"] == "civicclerk_minutes"
        assert meta["models"]["transcribe"] == "civicclerk_minutes"
        # A transcript artifact + segments file exist with the minutes text.
        tfile = clip_dir / meta["files"]["transcript"]
        assert tfile.exists()
        assert "Motion by Smith" in tfile.read_text()
        segs = json.loads((clip_dir / meta["files"]["transcript_segments"]).read_text())
        assert segs[0]["text"].startswith("Motion by Smith")

    def test_process_clip_does_not_double_fetch_docs(self, tmp_path):
        """PR-6 fix #3: the transcript-content step + steps 6/6b must fetch the
        agenda/minutes ONCE each (not twice). Under force_reprocess the disk
        cache is bypassed, so a second call would be a real second network hit."""
        pipe = self._doc_driven_pipeline(tmp_path)
        pipe._force_reprocess = True  # force: cache bypassed → double-fetch would show

        src = MagicMock()
        src.get_clip_title.return_value = "City Commission Meeting"
        src.canonical_url.return_value = "https://parisky.portal.civicclerk.com/event/322"
        src.fetch_captions.return_value = None
        src.download_audio.return_value = None
        src.download_minutes.return_value = {
            "pdf_file": "m.pdf", "html_file": None, "txt_file": "m.txt",
            "text": "Motion to approve passed 5-0.",
        }
        src.download_agenda.return_value = {
            "pdf_file": "a.pdf", "txt_file": "a.txt", "text": "I. Call to order.",
        }
        pipe.source = src

        with patch.object(pipe, "scrape_clip_metadata",
                          return_value={"date": "2026-05-12",
                                        "meeting_body": "City Commission",
                                        "title": "City Commission Meeting"}), \
             patch.object(pipe, "generate_search_index"), \
             patch.object(pipe, "apply_tables_from_agenda"):
            ok = pipe.process_clip(9)

        assert ok is True
        # Each document fetched exactly ONCE despite the transcript step +
        # steps 6/6b both needing the result.
        assert src.download_minutes.call_count == 1
        assert src.download_agenda.call_count == 1
        # And steps 6/6b still populated the file metadata from the reused docs.
        meta = json.loads((tmp_path / "clips" / "9" / "metadata.json").read_text())
        assert meta["files"]["minutes_txt"] == "m.txt"
        assert meta["files"]["agenda_txt"] == "a.txt"

    def test_process_clip_falls_back_to_agenda_when_no_minutes(self, tmp_path):
        pipe = self._doc_driven_pipeline(tmp_path)
        clip_dir = tmp_path / "clips" / "6"

        src = MagicMock()
        src.get_clip_title.return_value = "City Commission Meeting"
        src.canonical_url.return_value = "https://x/event/322"
        src.fetch_captions.return_value = None
        src.download_audio.return_value = None
        src.download_minutes.return_value = empty_minutes_result()  # no minutes
        src.download_agenda.return_value = {
            "pdf_file": "a.pdf", "txt_file": "a.txt",
            "text": "I. Call to order. II. Rezoning hearing.",
        }
        pipe.source = src

        with patch.object(pipe, "scrape_clip_metadata",
                          return_value={"date": "2026-05-12",
                                        "meeting_body": "City Commission",
                                        "title": "City Commission Meeting"}), \
             patch.object(pipe, "generate_search_index"), \
             patch.object(pipe, "apply_tables_from_agenda"):
            ok = pipe.process_clip(6)

        assert ok is True
        meta = json.loads((clip_dir / "metadata.json").read_text())
        assert meta["transcript_source"] == "civicclerk_agenda"
        assert "Rezoning hearing" in (clip_dir / meta["files"]["transcript"]).read_text()

    def test_process_clip_fails_when_no_documents(self, tmp_path):
        pipe = self._doc_driven_pipeline(tmp_path)
        src = MagicMock()
        src.get_clip_title.return_value = "City Commission Meeting"
        src.canonical_url.return_value = "https://x/event/322"
        src.fetch_captions.return_value = None
        src.download_audio.return_value = None
        src.download_minutes.return_value = empty_minutes_result()
        src.download_agenda.return_value = empty_agenda_result()
        pipe.source = src

        with patch.object(pipe, "scrape_clip_metadata",
                          return_value={"date": "2026-05-12",
                                        "meeting_body": "City Commission",
                                        "title": "X"}), \
             patch.object(pipe, "generate_search_index"), \
             patch.object(pipe, "apply_tables_from_agenda"):
            ok = pipe.process_clip(7)

        assert ok is False
        # failed_clips is now a dict keyed by clip_id → {..., "reason"}.
        reasons = [f["reason"] for f in pipe.state["failed_clips"].values()]
        assert "no_documents" in reasons

    def test_document_driven_source_skips_ws4_agenda_source(self, tmp_path, monkeypatch):
        """The document-driven primary owns docs → no parallel WS4 fallback.

        Uses the REAL jurisdictions/paris.toml (source_type=civicclerk) via the
        config loader so the pipeline __init__ sees a complete Jurisdiction.
        """
        import os
        os.environ.setdefault("OPENAI_API_KEY", "sk-test")
        import config as config_mod
        from main import LFUCGPipeline

        monkeypatch.setenv("JURISDICTION", "paris")
        config_mod.get_config.cache_clear()
        try:
            pipe = LFUCGPipeline(output_dir=str(tmp_path), verbose=False)
            assert pipe.cfg.source_type == "civicclerk"
            assert isinstance(pipe.source, CivicClerkSource)
            # Even though [agenda] type=civicclerk is set, the document-driven
            # path must NOT also build a parallel WS4 agenda_source.
            assert pipe.agenda_source is None
        finally:
            config_mod.get_config.cache_clear()


class TestByteIdentityGuard:
    """Granicus / YouTube paths must NEVER take the minutes-as-content branch."""

    def test_is_document_driven_false_for_granicus(self, tmp_path):
        pipe = _make_pipeline(tmp_path)
        assert pipe.cfg.source_type == "granicus"
        assert pipe._is_document_driven() is False

    def test_is_document_driven_false_for_youtube(self, tmp_path):
        pipe = _make_pipeline(tmp_path)
        pipe.cfg = SimpleNamespace(source_type="youtube")
        assert pipe._is_document_driven() is False

    def test_is_document_driven_true_for_civicclerk(self, tmp_path):
        pipe = _make_pipeline(tmp_path)
        pipe.cfg = SimpleNamespace(source_type="civicclerk")
        assert pipe._is_document_driven() is True

    def test_granicus_clip_with_no_captions_takes_whisper_path_not_docs(self, tmp_path):
        """A Granicus clip with NO captions must fall to the Whisper branch
        (download_audio), NOT the minutes-as-content branch — proving the gate
        is source_type, not merely 'audio is None'."""
        pipe = _make_pipeline(tmp_path)
        assert pipe.cfg.source_type == "granicus"

        src = MagicMock()
        src.get_clip_title.return_value = "Council Work Session"
        src.canonical_url.return_value = "https://lfucg.granicus.com/player/clip/6669"
        src.fetch_captions.return_value = None  # no VTT
        # download_audio returns None → the Whisper branch records a
        # download_failed miss (NOT a no_documents miss).
        src.download_audio.return_value = None
        pipe.source = src

        with patch.object(pipe, "scrape_clip_metadata",
                          return_value={"date": "2026-05-12",
                                        "meeting_body": "Council", "title": "X"}), \
             patch.object(pipe, "generate_search_index"):
            ok = pipe.process_clip(6669)

        assert ok is False
        # failed_clips is now a dict keyed by clip_id → {..., "reason"}.
        reasons = [f["reason"] for f in pipe.state["failed_clips"].values()]
        # The Whisper branch fired (download_failed), NOT the doc branch.
        assert "download_failed" in reasons
        assert "no_documents" not in reasons
        # download_minutes/agenda were NOT consulted as content (doc branch
        # never ran). They may be called later in steps 6/6b, but the
        # transcript-source branch did not.
        src.download_audio.assert_called_once()
