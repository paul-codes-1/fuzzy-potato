"""Tests for the WS4 structured-agenda fetcher (sources/agenda_base.py +
civicclerk.py + civicplus.py + make_agenda_source + the main.py fallback).

NO live network: all HTTP is mocked. CivicClerk is exercised against a
representative JSON payload captured from the LIVE parisky.api.civicclerk.com
API (2026-06-04); CivicPlus against a representative HTML snippet captured from
the LIVE www.georgetownky.gov/AgendaCenter page. The pipeline-fallback tests
assert the guard: the fallback fires only when the video source returned an
empty agenda/minutes AND an agenda_source is configured — and NEVER for
LFUCG/granicus-with-no-agenda-config.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sources import (
    CivicClerkAgendaSource,
    CivicPlusAgendaSource,
    make_agenda_source,
)
from sources.agenda_base import (
    AgendaDoc,
    empty_agenda_result,
    empty_minutes_result,
)
from sources.granicus import GranicusSource


def _log(*_a, **_k):
    pass


# ---------------------------------------------------------------------------
# Representative payloads (trimmed from real live responses)
# ---------------------------------------------------------------------------

# Captured shape from GET /v1/Events on parisky.api.civicclerk.com. Two events
# on 2026-05-12 (a real duplicate the live API returns) to exercise body
# matching; the matching one carries an Agenda, an Agenda Packet, and Minutes.
CIVICCLERK_EVENTS_2026_05_12 = {
    "@odata.context": "https://parisky.api.civicclerk.com/v1/$metadata#Events",
    "value": [
        {
            "id": 322,
            "eventName": "City Commission Meeting",
            "startDateTime": "2026-05-12T09:00:00Z",
            "eventDate": "2026-05-12T09:00:00Z",
            "eventCategoryName": "City Commission",
            "categoryName": "City Commission",
            "hasAgenda": True,
            "hasMedia": True,
            "agendaFile": {"agendaId": 0, "fileName": None},
            "minutesFile": {"minutesId": 0, "fileName": None},
            "publishedFiles": [
                {
                    "fileId": 844,
                    "type": "Agenda",
                    "fileType": 1,
                    "name": "05.12.2026 Agenda",
                    "url": "stream/PARISKY/10159085-1d30-4aea-956d-6817b1c922dd.pdf",
                },
                {
                    "fileId": 848,
                    "type": "Agenda Packet",
                    "fileType": 2,
                    "name": "05.12.2026 Online & Media Packet",
                    "url": "stream/PARISKY/7c36372f-4c60-4f5f-9883-4fd23878fc3c.pdf",
                },
                {
                    "fileId": 858,
                    "type": "Minutes",
                    "fileType": 4,
                    "name": "05.12.2026 Minutes",
                    "url": "stream/PARISKY/42b3f06e-0971-4441-82f2-beb5c7df0a41.pdf",
                },
            ],
        },
        {
            "id": 354,
            "eventName": "Budget Workshop",
            "startDateTime": "2026-05-12T13:00:00Z",
            "eventCategoryName": "Special Workshop",
            "categoryName": "Special Workshop",
            "hasAgenda": False,
            "publishedFiles": [],
        },
    ],
}

# TWO same-day events of DIFFERENT bodies, each carrying its OWN agenda packet.
# Exercises the no-wrong-doc guard: a clip whose body matches NEITHER must get
# no doc (rather than the first one's packet, which would feed a wrong Table of
# Motions). A clip body matching one of them still resolves to that one.
CIVICCLERK_TWO_BODY_EVENTS = [
    {
        "id": 400,
        "eventName": "City Commission Meeting",
        "startDateTime": "2026-06-09T09:00:00Z",
        "eventCategoryName": "City Commission",
        "categoryName": "City Commission",
        "publishedFiles": [
            {"fileId": 901, "type": "Agenda Packet", "fileType": 2,
             "name": "Commission Packet", "url": "stream/PARISKY/a.pdf"},
        ],
    },
    {
        "id": 401,
        "eventName": "Budget Workshop",
        "startDateTime": "2026-06-09T13:00:00Z",
        "eventCategoryName": "Budget Workshop",
        "categoryName": "Budget Workshop",
        "publishedFiles": [
            {"fileId": 902, "type": "Agenda Packet", "fileType": 2,
             "name": "Workshop Packet", "url": "stream/PARISKY/b.pdf"},
        ],
    },
]

# ONE same-day event whose body matches NEITHER a queried "Zoning Board" clip
# — the single-option convenience path should still hand it back.
CIVICCLERK_ONE_NONMATCHING_EVENT = [
    {
        "id": 410,
        "eventName": "City Commission Meeting",
        "startDateTime": "2026-06-10T09:00:00Z",
        "eventCategoryName": "City Commission",
        "categoryName": "City Commission",
        "publishedFiles": [
            {"fileId": 910, "type": "Agenda Packet", "fileType": 2,
             "name": "Commission Packet", "url": "stream/PARISKY/c.pdf"},
        ],
    },
]

# Trimmed but structurally faithful CivicPlus Agenda Center HTML: two category
# accordion sections (City Council / Finance Committee), each with one row, and
# the same link rendered twice (visible + Download popout) as the real page
# does — to exercise de-duplication. HREF slug carries the machine date.
CIVICPLUS_HTML = """
<div class="listing listingCollapse" id="cat1">
  <h2 tabindex="0" role="button" aria-controls="category-panel-1">City Council</h2>
  <table id="category-panel-1" summary="List of Agendas">
    <tbody>
      <tr class="catAgendaRow">
        <td>
          <h3 class="noMargin" id="h405122026-356">
            <a id="_05122026-356" name="_05122026-356"></a>
            <strong aria-label="Agenda for May 12, 2026"><abbr title="May">May</abbr> 12, 2026</strong>
          </h3>
          <p>
            <a id="05122026-356" href="/AgendaCenter/ViewFile/Agenda/_05122026-356" target="_blank">
              City Council Regular Meeting Agenda Packet (PDF)</a>
          </p>
        </td>
        <td class="minutes">
          <a href="/AgendaCenter/ViewFile/Minutes/_05122026-356">Minutes (PDF)</a>
        </td>
        <td class="downloads">
          <div class="popout"><ol>
            <li><a class="pdf" href="/AgendaCenter/ViewFile/Agenda/_05122026-356">Agenda</a></li>
          </ol></div>
        </td>
      </tr>
    </tbody>
  </table>
</div>
<div class="listing listingCollapse" id="cat2">
  <h2 tabindex="0" role="button" aria-controls="category-panel-2">Finance Committee</h2>
  <table id="category-panel-2" summary="List of Agendas">
    <tbody>
      <tr class="catAgendaRow">
        <td>
          <h3 class="noMargin" id="h405122026-360">
            <strong aria-label="Agenda for May 12, 2026">May 12, 2026</strong>
          </h3>
          <p>
            <a href="/AgendaCenter/ViewFile/Agenda/_05122026-360" target="_blank">
              Finance Committee Agenda (PDF)</a>
          </p>
        </td>
        <td class="minutes"></td>
      </tr>
    </tbody>
  </table>
</div>
"""

PDF_BYTES = b"%PDF-1.7\n...fake pdf body..."


def _mock_pdf_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/pdf"}
    resp.content = PDF_BYTES
    resp.raise_for_status = MagicMock()
    return resp


def _mock_json_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(return_value=payload)
    resp.raise_for_status = MagicMock()
    return resp


def _mock_html_response(html):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# empty-result shape == GranicusSource keys EXACTLY
# ---------------------------------------------------------------------------

def test_empty_agenda_shape_matches_granicus(tmp_path):
    """empty_agenda_result must have the same keys GranicusSource.download_agenda
    returns on a miss."""
    cfg = SimpleNamespace(granicus_host="x", default_view_id=1,
                          listing_view_fallbacks=())
    gsrc = GranicusSource(cfg, _log)
    # Granicus miss: a non-PDF response -> returns the empty shape.
    miss = MagicMock()
    miss.status_code = 200
    miss.headers = {"content-type": "text/html"}
    miss.content = b"<html>nope</html>"
    with patch("sources.granicus.requests.get", return_value=miss):
        granicus_result = gsrc.download_agenda(1, tmp_path)
    assert set(granicus_result.keys()) == set(empty_agenda_result().keys())
    assert set(empty_agenda_result().keys()) == {"pdf_file", "txt_file", "text"}


def test_empty_minutes_shape_matches_granicus(tmp_path):
    cfg = SimpleNamespace(granicus_host="x", default_view_id=1,
                          listing_view_fallbacks=())
    gsrc = GranicusSource(cfg, _log)
    miss = MagicMock()
    miss.status_code = 404
    miss.headers = {"content-type": "text/html"}
    miss.content = b""
    with patch("sources.granicus.requests.get", return_value=miss):
        granicus_result = gsrc.download_minutes(1, tmp_path)
    assert set(granicus_result.keys()) == set(empty_minutes_result().keys())
    assert set(empty_minutes_result().keys()) == {
        "pdf_file", "html_file", "txt_file", "text"}


# ---------------------------------------------------------------------------
# make_agenda_source registry
# ---------------------------------------------------------------------------

def test_make_agenda_source_none_for_empty_config():
    cfg = SimpleNamespace(agenda_type="")
    assert make_agenda_source(cfg, _log) is None


def test_make_agenda_source_none_for_missing_attr():
    # A cfg with no agenda_type attribute at all (defensive getattr default).
    cfg = SimpleNamespace()
    assert make_agenda_source(cfg, _log) is None


def test_make_agenda_source_civicclerk():
    cfg = SimpleNamespace(agenda_type="civicclerk",
                          agenda_api_base="https://parisky.api.civicclerk.com",
                          agenda_portal="", agenda_base_url="")
    src = make_agenda_source(cfg, _log)
    assert isinstance(src, CivicClerkAgendaSource)
    assert src.api_base == "https://parisky.api.civicclerk.com"


def test_make_agenda_source_civicplus():
    cfg = SimpleNamespace(agenda_type="civicplus", agenda_api_base="",
                          agenda_portal="", agenda_base_url="https://www.georgetownky.gov/")
    src = make_agenda_source(cfg, _log)
    assert isinstance(src, CivicPlusAgendaSource)
    assert src.base_url == "https://www.georgetownky.gov"  # trailing slash stripped


def test_make_agenda_source_unknown_returns_none():
    cfg = SimpleNamespace(agenda_type="legistar")
    assert make_agenda_source(cfg, _log) is None


# ---------------------------------------------------------------------------
# CivicClerk: listing + document parse + download
# ---------------------------------------------------------------------------

def _civicclerk_src():
    cfg = SimpleNamespace(agenda_type="civicclerk",
                          agenda_api_base="https://parisky.api.civicclerk.com",
                          agenda_portal="", agenda_base_url="")
    return CivicClerkAgendaSource(cfg, _log)


def test_civicclerk_picks_agenda_packet_over_plain_agenda():
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_EVENTS_2026_05_12["value"]):
        doc = src._pick_doc("2026-05-12", "City Commission", "agenda")
    assert doc is not None
    # Prefer the Agenda Packet (fileType 2 / fileId 848) for Table-of-Motions.
    assert "GetMeetingFileStream(fileId=848" in doc.url
    assert doc.kind == "agenda"
    assert doc.body == "City Commission"


def test_civicclerk_picks_minutes():
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_EVENTS_2026_05_12["value"]):
        doc = src._pick_doc("2026-05-12", "City Commission", "minutes")
    assert doc is not None
    assert "GetMeetingFileStream(fileId=858" in doc.url
    assert doc.kind == "minutes"


def test_civicclerk_file_stream_url_shape():
    src = _civicclerk_src()
    assert src._file_stream_url(844) == (
        "https://parisky.api.civicclerk.com/v1/Meetings/"
        "GetMeetingFileStream(fileId=844,plainText=false)"
    )


def test_civicclerk_date_filter_built_and_parsed():
    """_events_for_date issues an OData range filter and returns the value[]."""
    src = _civicclerk_src()
    captured = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        return _mock_json_response(CIVICCLERK_EVENTS_2026_05_12)

    with patch("sources.civicclerk.requests.get", side_effect=fake_get):
        events = src._events_for_date("2026-05-12")
    assert captured["url"].endswith("/v1/Events")
    assert "startDateTime ge 2026-05-12T00:00:00Z" in captured["params"]["$filter"]
    assert "startDateTime le 2026-05-12T23:59:59Z" in captured["params"]["$filter"]
    assert len(events) == 2


def test_civicclerk_fetch_for_date_downloads_and_extracts(tmp_path):
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_EVENTS_2026_05_12["value"]), \
         patch("sources.civicclerk.requests.get", return_value=_mock_pdf_response()), \
         patch("sources.civicclerk.extract_pdf_text", return_value="AGENDA TEXT BODY"):
        result = src.fetch_for_date("2026-05-12", "City Commission", tmp_path)
    assert result["pdf_file"] == "2026-05-12_agenda_civicclerk.pdf"
    assert result["txt_file"] == "2026-05-12_agenda_civicclerk.txt"
    assert result["text"] == "AGENDA TEXT BODY"
    assert (tmp_path / "2026-05-12_agenda_civicclerk.pdf").read_bytes() == PDF_BYTES
    assert (tmp_path / "2026-05-12_agenda_civicclerk.txt").read_text() == "AGENDA TEXT BODY"


def test_civicclerk_no_events_returns_empty_shape(tmp_path):
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date", return_value=[]):
        result = src.fetch_for_date("2026-05-12", "City Commission", tmp_path)
    assert result == empty_agenda_result()


def test_civicclerk_minutes_returns_minutes_shape(tmp_path):
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_EVENTS_2026_05_12["value"]), \
         patch("sources.civicclerk.requests.get", return_value=_mock_pdf_response()), \
         patch("sources.civicclerk.extract_pdf_text", return_value="MINUTES TEXT"):
        result = src.fetch_minutes_for_date("2026-05-12", "City Commission", tmp_path)
    assert set(result.keys()) == {"pdf_file", "html_file", "txt_file", "text"}
    assert result["text"] == "MINUTES TEXT"


def test_civicclerk_empty_date_returns_empty(tmp_path):
    src = _civicclerk_src()
    assert src.fetch_for_date("", "City Commission", tmp_path) == empty_agenda_result()


def test_civicclerk_multi_body_day_no_match_returns_none():
    """Two same-day events of different bodies + a clip body matching NEITHER
    → return None (no doc) rather than guessing the wrong meeting's packet."""
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_TWO_BODY_EVENTS):
        doc = src._pick_doc("2026-06-09", "Planning Commission", "agenda")
    assert doc is None


def test_civicclerk_multi_body_day_matching_body_still_resolves():
    """With two same-day events, a clip body matching ONE resolves to that
    one's packet (not the other)."""
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_TWO_BODY_EVENTS):
        doc = src._pick_doc("2026-06-09", "Budget Workshop", "agenda")
    assert doc is not None
    assert doc.body == "Budget Workshop"
    assert "GetMeetingFileStream(fileId=902" in doc.url


def test_civicclerk_single_event_nonmatching_body_still_returns_it():
    """Exactly one same-day event + a non-matching body → convenience path
    still returns that one doc (single-meeting-per-day stays unaffected)."""
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_ONE_NONMATCHING_EVENT):
        doc = src._pick_doc("2026-06-10", "Zoning Board", "agenda")
    assert doc is not None
    assert "GetMeetingFileStream(fileId=910" in doc.url


def test_civicclerk_single_event_empty_body_resolves():
    """An empty/None clip body still matches the single same-day event."""
    src = _civicclerk_src()
    with patch.object(src, "_events_for_date",
                      return_value=CIVICCLERK_ONE_NONMATCHING_EVENT):
        doc = src._pick_doc("2026-06-10", None, "agenda")
    assert doc is not None
    assert "GetMeetingFileStream(fileId=910" in doc.url


# ---------------------------------------------------------------------------
# CivicPlus: listing + PDF-link parse + date matching + download
# ---------------------------------------------------------------------------

def _civicplus_src():
    cfg = SimpleNamespace(agenda_type="civicplus", agenda_api_base="",
                          agenda_portal="", agenda_base_url="https://www.georgetownky.gov")
    return CivicPlusAgendaSource(cfg, _log)


def test_civicplus_list_docs_parses_and_dedupes():
    src = _civicplus_src()
    docs = src.list_docs(html=CIVICPLUS_HTML)
    # cat1 row renders the Agenda link twice (visible + popout) + a Minutes
    # link; cat2 renders one Agenda link. After de-dup on (date,kind,url):
    # 2 City Council (agenda + minutes) + 1 Finance Committee (agenda) = 3.
    kinds = sorted((d.kind, d.body, d.url) for d in docs)
    assert len(docs) == 3
    bodies = {d.body for d in docs}
    assert bodies == {"City Council", "Finance Committee"}


def test_civicplus_date_from_href_slug():
    src = _civicplus_src()
    docs = src.list_docs(html=CIVICPLUS_HTML)
    for d in docs:
        assert d.date == "2026-05-12"  # _05122026-NNN -> 2026-05-12
        assert d.url.startswith("https://www.georgetownky.gov/AgendaCenter/ViewFile/")


def test_civicplus_body_match_picks_finance_committee():
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=CIVICPLUS_HTML):
        doc = src._pick_doc("2026-05-12", "Finance Committee", "agenda")
    assert doc is not None
    assert doc.body == "Finance Committee"
    assert "_05122026-360" in doc.url


def test_civicplus_body_match_picks_city_council():
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=CIVICPLUS_HTML):
        doc = src._pick_doc("2026-05-12", "City Council", "agenda")
    assert doc is not None
    assert doc.body == "City Council"
    assert "_05122026-356" in doc.url


def test_civicplus_no_match_for_other_date():
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=CIVICPLUS_HTML):
        doc = src._pick_doc("2026-06-01", "City Council", "agenda")
    assert doc is None


def test_civicplus_multi_doc_day_no_match_returns_none():
    """Two same-day agendas of different bodies (City Council, Finance
    Committee) + a clip body matching NEITHER → None (no doc), not the first
    one's PDF — avoids attaching the wrong meeting's Table of Motions."""
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=CIVICPLUS_HTML):
        doc = src._pick_doc("2026-05-12", "Planning Commission", "agenda")
    assert doc is None


# A single-section page: one same-day agenda whose body is "City Council".
CIVICPLUS_SINGLE_SECTION_HTML = """
<div class="listing" id="cat1">
  <h2 aria-controls="category-panel-1">City Council</h2>
  <table id="category-panel-1">
    <tbody>
      <tr class="catAgendaRow">
        <td>
          <strong aria-label="Agenda for May 12, 2026">May 12, 2026</strong>
          <a href="/AgendaCenter/ViewFile/Agenda/_05122026-356">Agenda (PDF)</a>
        </td>
      </tr>
    </tbody>
  </table>
</div>
"""


def test_civicplus_single_doc_nonmatching_body_still_returns_it():
    """Exactly one same-day agenda + a non-matching clip body → convenience
    path still returns that one doc (single-meeting-per-day unaffected)."""
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html",
                      return_value=CIVICPLUS_SINGLE_SECTION_HTML):
        doc = src._pick_doc("2026-05-12", "Zoning Board", "agenda")
    assert doc is not None
    assert "_05122026-356" in doc.url


def test_civicplus_single_doc_empty_body_resolves():
    """An empty/None clip body still matches the single same-day doc."""
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html",
                      return_value=CIVICPLUS_SINGLE_SECTION_HTML):
        doc = src._pick_doc("2026-05-12", None, "agenda")
    assert doc is not None
    assert "_05122026-356" in doc.url


def test_civicplus_fetch_for_date_downloads_and_extracts(tmp_path):
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=CIVICPLUS_HTML), \
         patch("sources.civicplus.requests.get", return_value=_mock_pdf_response()), \
         patch("sources.civicplus.extract_pdf_text", return_value="CP AGENDA TEXT"):
        result = src.fetch_for_date("2026-05-12", "City Council", tmp_path)
    assert result["pdf_file"] == "2026-05-12_agenda_civicplus.pdf"
    assert result["txt_file"] == "2026-05-12_agenda_civicplus.txt"
    assert result["text"] == "CP AGENDA TEXT"


def test_civicplus_minutes_link_discovered_and_kind_minutes():
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=CIVICPLUS_HTML):
        doc = src._pick_doc("2026-05-12", "City Council", "minutes")
    assert doc is not None
    assert doc.kind == "minutes"
    assert "/ViewFile/Minutes/_05122026-356" in doc.url


def test_civicplus_empty_html_returns_empty(tmp_path):
    src = _civicplus_src()
    with patch.object(src, "_fetch_listing_html", return_value=""):
        result = src.fetch_for_date("2026-05-12", "City Council", tmp_path)
    assert result == empty_agenda_result()


def test_civicplus_recent_only_flag_documented():
    """The fragility flag (server-rendered rows only) is exposed for callers."""
    assert _civicplus_src().recent_only is True


# ---------------------------------------------------------------------------
# Pipeline fallback guard (the critical LFUCG byte-identity test)
# ---------------------------------------------------------------------------

class _FakePipeline:
    """Just enough of LFUCGPipeline to exercise _agenda_with_fallback /
    _minutes_with_fallback in isolation (without OpenAI/config side-effects)."""

    from main import LFUCGPipeline
    _doc_result_empty = staticmethod(LFUCGPipeline._doc_result_empty)
    _agenda_with_fallback = LFUCGPipeline._agenda_with_fallback
    _minutes_with_fallback = LFUCGPipeline._minutes_with_fallback

    def __init__(self, source, agenda_source):
        self.source = source
        self.agenda_source = agenda_source

    def log(self, *_a, **_k):
        pass


def test_fallback_never_runs_for_lfucg_granicus_no_agenda_config(tmp_path):
    """LFUCG: agenda_source is None → the Granicus result passes through and
    no fallback is attempted (byte-identity)."""
    granicus_agenda = {"pdf_file": "a.pdf", "txt_file": "a.txt", "text": "real granicus agenda"}
    source = MagicMock()
    source.download_agenda.return_value = granicus_agenda
    pipe = _FakePipeline(source, agenda_source=None)
    result = pipe._agenda_with_fallback(123, tmp_path, title="T", meeting_date="2026-05-12", body="Council")
    assert result is granicus_agenda
    source.download_agenda.assert_called_once()


def test_fallback_not_run_when_granicus_returns_real_agenda(tmp_path):
    """Even WITH an agenda_source configured, a non-empty video-source agenda
    wins and the fallback is not called."""
    real = {"pdf_file": "a.pdf", "txt_file": "a.txt", "text": "real"}
    source = MagicMock()
    source.download_agenda.return_value = real
    agenda_source = MagicMock()
    pipe = _FakePipeline(source, agenda_source=agenda_source)
    result = pipe._agenda_with_fallback(1, tmp_path, title="T", meeting_date="2026-05-12", body="Council")
    assert result is real
    agenda_source.fetch_for_date.assert_not_called()


def test_fallback_runs_when_video_empty_and_agenda_source_configured(tmp_path):
    """YouTube county: empty video-source agenda + configured agenda_source →
    the fallback fires and its result is used."""
    source = MagicMock()
    source.download_agenda.return_value = empty_agenda_result()
    fallback_result = {"pdf_file": "f.pdf", "txt_file": "f.txt", "text": "from civicclerk"}
    agenda_source = MagicMock()
    agenda_source.fetch_for_date.return_value = fallback_result
    pipe = _FakePipeline(source, agenda_source=agenda_source)
    result = pipe._agenda_with_fallback(1, tmp_path, title="T", meeting_date="2026-05-12", body="City Commission")
    assert result is fallback_result
    agenda_source.fetch_for_date.assert_called_once_with("2026-05-12", "City Commission", tmp_path)


def test_fallback_not_run_when_no_meeting_date(tmp_path):
    """No date → nothing to key the portal lookup on; fallback skipped."""
    source = MagicMock()
    source.download_agenda.return_value = empty_agenda_result()
    agenda_source = MagicMock()
    pipe = _FakePipeline(source, agenda_source=agenda_source)
    result = pipe._agenda_with_fallback(1, tmp_path, title="T", meeting_date=None, body="X")
    assert result == empty_agenda_result()
    agenda_source.fetch_for_date.assert_not_called()


def test_fallback_keeps_video_result_when_fallback_also_empty(tmp_path):
    """If the agenda portal also has nothing, return the (empty) video result
    rather than swapping in a different empty object."""
    video_empty = empty_agenda_result()
    source = MagicMock()
    source.download_agenda.return_value = video_empty
    agenda_source = MagicMock()
    agenda_source.fetch_for_date.return_value = empty_agenda_result()
    pipe = _FakePipeline(source, agenda_source=agenda_source)
    result = pipe._agenda_with_fallback(1, tmp_path, title="T", meeting_date="2026-05-12", body="X")
    assert result is video_empty


def test_fallback_swallows_agenda_source_exception(tmp_path):
    """A throwing agenda portal must not crash the clip — the video result is
    returned and the error is logged."""
    video_empty = empty_agenda_result()
    source = MagicMock()
    source.download_agenda.return_value = video_empty
    agenda_source = MagicMock()
    agenda_source.fetch_for_date.side_effect = RuntimeError("boom")
    pipe = _FakePipeline(source, agenda_source=agenda_source)
    result = pipe._agenda_with_fallback(1, tmp_path, title="T", meeting_date="2026-05-12", body="X")
    assert result is video_empty


def test_minutes_fallback_runs_when_video_empty(tmp_path):
    source = MagicMock()
    source.download_minutes.return_value = empty_minutes_result()
    fallback_result = {"pdf_file": "m.pdf", "html_file": None, "txt_file": "m.txt", "text": "minutes"}
    agenda_source = MagicMock()
    agenda_source.fetch_minutes_for_date.return_value = fallback_result
    pipe = _FakePipeline(source, agenda_source=agenda_source)
    result = pipe._minutes_with_fallback(1, tmp_path, title="T", meeting_date="2026-05-12", body="Council")
    assert result is fallback_result
    agenda_source.fetch_minutes_for_date.assert_called_once()


def test_minutes_fallback_never_runs_for_lfucg(tmp_path):
    """LFUCG: agenda_source is None → a REAL non-empty Granicus minutes result
    passes through unchanged and no fallback is attempted (byte-identity).

    Uses a genuinely non-empty result so the test would FAIL if the
    `agenda_source is None` guard were broken (a None agenda_source can't be
    called) — mirrors the agenda guard test rather than passing vacuously."""
    granicus_minutes = {"pdf_file": "m.pdf", "html_file": None,
                        "txt_file": "m.txt", "text": "real granicus minutes"}
    source = MagicMock()
    source.download_minutes.return_value = granicus_minutes
    pipe = _FakePipeline(source, agenda_source=None)
    result = pipe._minutes_with_fallback(1, tmp_path, title="T", meeting_date="2026-05-12", body="Council")
    assert result is granicus_minutes
    source.download_minutes.assert_called_once()


# ---------------------------------------------------------------------------
# _doc_result_empty edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result,expected", [
    (None, True),
    ({}, True),
    ({"pdf_file": None, "txt_file": None, "text": None}, True),
    ({"pdf_file": None, "html_file": None, "txt_file": None, "text": None}, True),
    ({"pdf_file": "x.pdf", "txt_file": None, "text": None}, False),
    ({"pdf_file": None, "txt_file": None, "text": "some text"}, False),
    ({"pdf_file": None, "html_file": "x.html", "txt_file": None, "text": None}, False),
])
def test_doc_result_empty(result, expected):
    from main import LFUCGPipeline
    assert LFUCGPipeline._doc_result_empty(result) is expected
