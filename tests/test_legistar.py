"""Tests for sources/legistar.py — the Legistar Calendar.aspx AgendaSource.

Covers: row parsing (meeting rows vs mini-calendar noise, agenda vs minutes
links, accessible-rendition links ignored), the work-session parity title
matching (same-day council + work-session pairs are real — 7/1/2026), the
refuse-to-guess multi-candidate rule, and the download/extract flow shape.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from sources.legistar import LegistarAgendaSource, _title_matches


CAL_HTML = """
<html><body><table>
<tr class="rgRow">
  <td><a href="DepartmentDetail.aspx?ID=1">Urban County Council</a></td>
  <td>8/13/2026</td><td>6:00 PM</td>
  <td><a href="MeetingDetail.aspx?ID=1436451&amp;GUID=AA11">Meeting details</a></td>
  <td><a href="View.ashx?M=A&amp;ID=900001&amp;GUID=AA11">Agenda</a>
      <a href="View.ashx?M=AADA&amp;ID=900001&amp;GUID=AA11">Accessible Agenda</a></td>
  <td><a href="View.ashx?M=M&amp;ID=900002&amp;GUID=AA11">Minutes</a>
      <a href="View.ashx?M=AMDA&amp;ID=900002&amp;GUID=AA11">Accessible Minutes</a></td>
</tr>
<tr class="rgAltRow">
  <td><a href="DepartmentDetail.aspx?ID=2">Urban County Council Work Session</a></td>
  <td>7/1/2026</td><td>3:00 PM</td>
  <td><a href="MeetingDetail.aspx?ID=1352610&amp;GUID=BB22">Meeting details</a></td>
  <td><a href="View.ashx?M=M&amp;ID=900003&amp;GUID=BB22">Minutes</a></td>
</tr>
<tr class="rgRow">
  <td><a href="DepartmentDetail.aspx?ID=1">Urban County Council</a></td>
  <td>7/1/2026</td><td>6:00 PM</td>
  <td><a href="MeetingDetail.aspx?ID=1426619&amp;GUID=CC33">Meeting details</a></td>
  <td><a href="View.ashx?M=M&amp;ID=900004&amp;GUID=CC33">Minutes</a></td>
</tr>
<!-- month-navigation mini-calendar row: dates but no MeetingDetail link -->
<tr><td>27</td><td>28</td><td>29</td><td>7/27/2026</td></tr>
</table></body></html>
"""


def _source():
    cfg = SimpleNamespace(agenda_base_url="https://lexington.legistar.com")
    return LegistarAgendaSource(cfg, lambda *a, **k: None)


class TestListDocs:
    def test_parses_meeting_rows_and_kinds(self):
        docs = _source().list_docs(html=CAL_HTML)
        kinds = sorted((d.date, d.kind) for d in docs)
        assert kinds == [
            ("2026-07-01", "minutes"),
            ("2026-07-01", "minutes"),
            ("2026-08-13", "agenda"),
            ("2026-08-13", "minutes"),
        ]

    def test_accessible_renditions_ignored(self):
        # M=AADA / M=AMDA must not produce extra docs.
        docs = _source().list_docs(html=CAL_HTML)
        assert all("M=A&" in d.url or "M=M&" in d.url for d in docs)

    def test_body_names_attached(self):
        docs = _source().list_docs(html=CAL_HTML)
        ws = [d for d in docs if "900003" in d.url]
        assert ws[0].body == "Urban County Council Work Session"

    def test_mini_calendar_rows_skipped(self):
        docs = _source().list_docs(html=CAL_HTML)
        assert all(d.date != "2026-07-27" for d in docs)


class TestTitleMatching:
    def test_work_session_parity_required(self):
        assert _title_matches(
            "Council Work Session (1)", "Council",
            "Urban County Council Work Session")
        assert not _title_matches(
            "Council Work Session (1)", "Council", "Urban County Council")
        assert not _title_matches(
            "Urban County Council (1)", "Council",
            "Urban County Council Work Session")

    def test_substring_both_ways(self):
        assert _title_matches(
            "Urban County Council (1)", "Council", "Urban County Council")
        assert _title_matches(
            "Environmental Quality & Public Works (EQPW) Committee (1)",
            "Committee",
            "Environmental Quality & Public Works (EQPW) Committee")

    def test_falls_back_to_body_without_title(self):
        assert _title_matches(None, "Council", "Urban County Council")

    def test_empty_matches_anything(self):
        assert _title_matches(None, None, "Urban County Council")


class TestPickDoc:
    def test_same_day_pair_resolved_by_title(self, monkeypatch):
        src = _source()
        monkeypatch.setattr(src, "list_docs",
                            lambda html=None: _source().list_docs(html=CAL_HTML))
        ws = src._pick_doc("2026-07-01", "Council", "minutes",
                           "Council Work Session (1)")
        assert ws is not None and "900003" in ws.url
        ucc = src._pick_doc("2026-07-01", "Council", "minutes",
                            "Urban County Council (1)")
        assert ucc is not None and "900004" in ucc.url

    def test_ambiguous_without_title_refuses(self, monkeypatch):
        src = _source()
        monkeypatch.setattr(src, "list_docs",
                            lambda html=None: _source().list_docs(html=CAL_HTML))
        # body "Council" matches BOTH 7/1 meetings when no title is given
        # (parity can't disambiguate) — must refuse rather than guess.
        assert src._pick_doc("2026-07-01", "Council", "minutes", None) is None

    def test_no_docs_for_date(self, monkeypatch):
        src = _source()
        monkeypatch.setattr(src, "list_docs",
                            lambda html=None: _source().list_docs(html=CAL_HTML))
        assert src._pick_doc("2026-01-01", "Council", "minutes", "x") is None


class TestFetchMinutes:
    def test_download_flow_and_result_shape(self, tmp_path, monkeypatch):
        src = _source()
        monkeypatch.setattr(src, "list_docs",
                            lambda html=None: _source().list_docs(html=CAL_HTML))

        class FakeResp:
            status_code = 200
            headers = {"content-type": "application/pdf"}
            content = b"%PDF-1.4 fake"

        monkeypatch.setattr("sources.legistar.requests.get",
                            lambda *a, **k: FakeResp())
        monkeypatch.setattr("sources.legistar.extract_pdf_text",
                            lambda p, **k: "MINUTES TEXT " * 20)

        res = src.fetch_minutes_for_date(
            "2026-08-13", "Council", tmp_path, title="Urban County Council (1)")
        assert res["pdf_file"] == "2026-08-13_minutes_legistar.pdf"
        assert res["txt_file"] == "2026-08-13_minutes_legistar.txt"
        assert "MINUTES TEXT" in res["text"]
        assert (tmp_path / res["pdf_file"]).exists()
        assert (tmp_path / res["txt_file"]).exists()

    def test_non_pdf_response_is_a_miss(self, tmp_path, monkeypatch):
        src = _source()
        monkeypatch.setattr(src, "list_docs",
                            lambda html=None: _source().list_docs(html=CAL_HTML))

        class FakeResp:
            status_code = 200
            headers = {"content-type": "text/html"}
            content = b"<html>login page</html>"

        monkeypatch.setattr("sources.legistar.requests.get",
                            lambda *a, **k: FakeResp())
        res = src.fetch_minutes_for_date(
            "2026-08-13", "Council", tmp_path, title="Urban County Council (1)")
        assert res["pdf_file"] is None and res["text"] is None

    def test_no_date_is_empty(self, tmp_path):
        res = _source().fetch_minutes_for_date("", "Council", tmp_path)
        assert res["pdf_file"] is None
