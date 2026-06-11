"""Tests for the VideoSource abstraction (WS2).

Covers:
  (a) GranicusSource URL builders / canonical_url are byte-identical to the
      strings the old LFUCGPipeline.clip_url/agenda_url/minutes_url produced.
  (b) fetch_date_from_listing parses both ViewPublisher date formats (hidden
      unix-timestamp span + displayed "Month D, YYYY" text) — same as before.
  (c) make_source returns GranicusSource for source_type="granicus" and
      falls back to GranicusSource (with a warning) for an unknown type.
  (d) A FakeSource implementing the Protocol proves the interface is the only
      thing the pipeline depends on (structural typing via runtime_checkable).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from sources import GranicusSource, MeetingRef, VideoSource, make_source


# A no-op logger matching LFUCGPipeline.log's (msg, level="INFO") signature.
def _noop_log(msg: str, level: str = "INFO") -> None:
    return None


def _lfucg_cfg():
    """The real LFUCG config (default jurisdiction)."""
    from config import get_config

    return get_config()


# ---------------------------------------------------------------------------
# (a) byte-identical URLs
# ---------------------------------------------------------------------------

# These are the EXACT strings the pre-refactor LFUCGPipeline.clip_url /
# agenda_url / minutes_url produced for clip 6669 with the default LFUCG
# config (granicus_host=lfucg.granicus.com, view_id=14). Captured from the
# live code before the refactor.
EXPECTED_CLIP_URL = "https://lfucg.granicus.com/player/clip/6669?view_id=14&redirect=true"
EXPECTED_AGENDA_URL = "https://lfucg.granicus.com/AgendaViewer.php?view_id=14&clip_id=6669"
EXPECTED_MINUTES_URL = "https://lfucg.granicus.com/MinutesViewer.php?view_id=14&clip_id=6669"


class TestGranicusUrls:
    def test_clip_url_byte_identical(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        assert src.clip_url(6669) == EXPECTED_CLIP_URL

    def test_agenda_url_byte_identical(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        assert src.agenda_url(6669) == EXPECTED_AGENDA_URL

    def test_minutes_url_byte_identical(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        assert src.minutes_url(6669) == EXPECTED_MINUTES_URL

    def test_canonical_url_equals_clip_url(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        assert src.canonical_url(6669) == EXPECTED_CLIP_URL
        # And it's the player permalink, used for citations.
        assert src.canonical_url(6770) == src.clip_url(6770)

    def test_view_id_override_propagates_to_urls(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        src.view_id = "9"  # pipeline sets this when --view-id is passed
        assert src.clip_url(6770) == (
            "https://lfucg.granicus.com/player/clip/6770?view_id=9&redirect=true"
        )


# ---------------------------------------------------------------------------
# (b) fetch_date_from_listing parsing
# ---------------------------------------------------------------------------

# ViewPublisher row layout #1: hidden unix-timestamp span (view 14 archive).
# 1736384400 == 2025-01-08 8:00 PM EST == 2025-01-09 01:00 UTC — an evening
# meeting that crosses midnight UTC, so this also guards the timezone fix
# (a UTC conversion would yield the next-day date 2025-01-09).
_LISTING_HTML_TS = """
<table><tr>
  <td><a href="/AgendaViewer.php?view_id=14&clip_id=6669">January 8 2026 WQFB meeting</a></td>
  <td><span style="display: none;">1736384400</span>January 8, 2025</td>
</tr></table>
"""

# ViewPublisher row layout #2: displayed "Month D, YYYY" with &nbsp; (view 9
# committee layout) and NO hidden timestamp span.
_LISTING_HTML_TEXT = """
<table><tr>
  <td><a href="/AgendaViewer.php?view_id=9&clip_id=6770">Task Force</a></td>
  <td>May&nbsp;13,&nbsp;2026</td>
</tr></table>
"""


def _resp(text: str):
    r = MagicMock()
    r.text = text
    r.raise_for_status = MagicMock()
    return r


class TestFetchDateFromListing:
    def test_parses_hidden_timestamp_span(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        with patch("sources.granicus.requests.get", return_value=_resp(_LISTING_HTML_TS)):
            assert src.fetch_date_from_listing(6669) == "2025-01-08"

    def test_parses_displayed_text_date(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        # Clip 6770 lives on view 9, not the default view 14 — so this also
        # exercises the LISTING_VIEW_FALLBACKS loop: the default view returns
        # no matching row, and only the fallback view (9) carries the clip.
        def _by_view(url, *args, **kwargs):
            return _resp(_LISTING_HTML_TEXT) if "view_id=9" in url else _resp("<table></table>")

        with patch("sources.granicus.requests.get", side_effect=_by_view) as mock_get:
            assert src.fetch_date_from_listing(6770) == "2026-05-13"
            # Prove the fallback was actually walked (default view tried first)
            # — and that each view was fetched only once (the str/int mix in
            # views_to_try used to fetch the default view twice).
            urls = [call.args[0] for call in mock_get.call_args_list]
            assert len(urls) == len(set(urls))
            assert mock_get.call_count >= 2

    def test_returns_none_when_clip_absent(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        with patch("sources.granicus.requests.get", return_value=_resp("<table></table>")):
            assert src.fetch_date_from_listing(99999) is None

    def test_get_metadata_delegates_to_listing(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        with patch("sources.granicus.requests.get", return_value=_resp(_LISTING_HTML_TS)):
            meta = src.get_metadata(MeetingRef(clip_id="6669"))
        assert meta == {"date": "2025-01-08"}


# ---------------------------------------------------------------------------
# (c) make_source factory
# ---------------------------------------------------------------------------

class TestMakeSource:
    def test_granicus_for_granicus_type(self):
        cfg = SimpleNamespace(
            granicus_host="lfucg.granicus.com",
            default_view_id=14,
            listing_view_fallbacks=(14, 9),
            source_type="granicus",
        )
        src = make_source(cfg, _noop_log)
        assert isinstance(src, GranicusSource)

    def test_unknown_type_falls_back_to_granicus_with_warning(self):
        cfg = SimpleNamespace(
            granicus_host="x.granicus.com",
            default_view_id=1,
            listing_view_fallbacks=(1,),
            source_type="bogusportal",
        )
        warnings: list[tuple[str, str]] = []

        def capture_log(msg, level="INFO"):
            warnings.append((msg, level))

        src = make_source(cfg, capture_log)
        assert isinstance(src, GranicusSource)
        assert any(level == "WARNING" and "bogusportal" in msg for msg, level in warnings)

    def test_missing_source_type_defaults_to_granicus(self):
        # cfg without a source_type attribute (defensive default).
        cfg = SimpleNamespace(
            granicus_host="x.granicus.com",
            default_view_id=1,
            listing_view_fallbacks=(1,),
        )
        src = make_source(cfg, _noop_log)
        assert isinstance(src, GranicusSource)

    def test_real_lfucg_config_makes_granicus(self):
        src = make_source(_lfucg_cfg(), _noop_log)
        assert isinstance(src, GranicusSource)
        assert src.clip_url(6669) == EXPECTED_CLIP_URL


# ---------------------------------------------------------------------------
# (d) FakeSource proves the pipeline depends only on the Protocol
# ---------------------------------------------------------------------------

class FakeSource:
    """A minimal in-memory VideoSource — no Granicus, no network.

    Proves the pipeline's contract is the interface, not GranicusSource.
    """

    def __init__(self):
        self.view_id = "1"
        self.force_reprocess = False
        self.progress = lambda msg: None
        self.calls: list[str] = []

    def list_meetings(self) -> List[MeetingRef]:
        return [MeetingRef(clip_id="1", title="Fake Meeting", date="2026-01-01")]

    def scrape_available_clips(self) -> List[int]:
        return [1]

    def get_clip_title(self, clip_id: int) -> Optional[str]:
        return "Fake Meeting"

    def get_metadata(self, ref: MeetingRef) -> Dict[str, Any]:
        return {"date": "2026-01-01"}

    def canonical_url(self, clip_id: int) -> str:
        return f"https://fake.example/clip/{clip_id}"

    def download_audio(self, clip_id, clip_dir, title=None, date=None) -> Optional[str]:
        self.calls.append("download_audio")
        return None

    def fetch_captions(self, clip_id, clip_dir) -> Optional[Path]:
        self.calls.append("fetch_captions")
        return None

    def download_agenda(self, clip_id, clip_dir, title=None, date=None) -> Dict[str, Any]:
        return {"pdf_file": None, "txt_file": None, "text": None}

    def download_minutes(self, clip_id, clip_dir, title=None, date=None) -> Dict[str, Any]:
        return {"pdf_file": None, "html_file": None, "txt_file": None, "text": None}


class TestProtocolConformance:
    def test_fake_source_satisfies_protocol(self):
        fake = FakeSource()
        # runtime_checkable Protocol — structural conformance check.
        assert isinstance(fake, VideoSource)

    def test_granicus_source_satisfies_protocol(self):
        src = GranicusSource(_lfucg_cfg(), _noop_log)
        assert isinstance(src, VideoSource)

    def test_pipeline_uses_only_the_interface(self):
        """Swap a FakeSource into a real pipeline and drive the date-lookup
        path that goes through the source — proves the pipeline only touches
        the protocol surface, not Granicus internals."""
        import os

        os.environ.setdefault("OPENAI_API_KEY", "sk-test")
        from main import LFUCGPipeline

        pipe = LFUCGPipeline(output_dir="/tmp/ws2_fake_source_test", verbose=False)
        pipe.source = FakeSource()

        # scrape_clip_metadata falls through to source.get_metadata when no
        # date is in the title. With FakeSource that yields 2026-01-01.
        meta = pipe.scrape_clip_metadata(1, title="Untitled clip")
        assert meta["date"] == "2026-01-01"
