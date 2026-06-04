"""CivicClerkAgendaSource — agenda/minutes via the CivicClerk JSON API.

WS4 adapter for jurisdictions on CivicClerk (in the Bluegrass survey: Paris,
KY — ``parisky.portal.civicclerk.com`` backed by
``parisky.api.civicclerk.com``). CivicClerk is the cleanest of the two agenda
platforms: a stable OData-shaped JSON API, no HTML scraping.

LIVE-INSPECTED SCHEMA (parisky.api.civicclerk.com, 2026-06-04)
==============================================================
The events list is an OData collection::

    GET /v1/Events?$orderby=startDateTime desc
        &$filter=startDateTime ge <iso> and startDateTime le <iso>
    -> {"@odata.context": "...", "value": [ <event>, ... ]}

Each ``<event>`` (only the fields we use)::

    {
      "id": 322,
      "eventName": "City Commission Meeting",
      "startDateTime": "2026-05-12T09:00:00Z",   # also "eventDate"
      "eventCategoryName": "City Commission",     # body / category
      "categoryName": "City Commission",
      "hasAgenda": true,
      "hasMedia": true,
      # Legacy single-file fields are EMPTY in practice (fileName=null):
      "agendaFile":  {"agendaId": 0,  "fileName": null, ...},
      "minutesFile": {"minutesId": 0, "fileName": null, ...},
      # The real documents live here:
      "publishedFiles": [
        {"fileId": 844, "type": "Agenda",        "fileType": 1,
         "name": "05.12.2026 Agenda",            "url": "stream/PARISKY/<uuid>.pdf"},
        {"fileId": 848, "type": "Agenda Packet", "fileType": 2,
         "name": "05.12.2026 Online & Media Packet", "url": "stream/.../<uuid>.pdf"},
        {"fileId": 858, "type": "Minutes",       "fileType": 4,
         "name": "05.12.2026 Minutes",           "url": "stream/.../<uuid>.pdf"}
      ]
    }

The ``publishedFiles[].url`` (``stream/PARISKY/<uuid>.pdf``) is NOT directly
fetchable (404). The working download endpoint, verified live, is keyed by
``fileId``::

    GET /v1/Meetings/GetMeetingFileStream(fileId=844,plainText=false)
    -> 200, Content-Type: application/pdf, body starts %PDF-1.7

``fileType`` legend (observed): 1 = Agenda, 2 = Agenda Packet, 4 = Minutes.
For the Table-of-Motions feature we want the **Agenda Packet** (the full
packet that can embed a prior meeting's Table of Motions) and fall back to the
plain Agenda. For minutes we want ``type == "Minutes"`` / ``fileType == 4``.

Config (``jurisdictions/<slug>.toml``)::

    [agenda]
    type = "civicclerk"
    api_base = "https://parisky.api.civicclerk.com"   # -> cfg.agenda_api_base
    # portal = "https://parisky.portal.civicclerk.com" # optional, for citations
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from documents import extract_pdf_text

from .agenda_base import (
    AgendaDoc,
    empty_agenda_result,
    empty_minutes_result,
)

# fileType legend observed on the live API.
_FILETYPE_AGENDA = 1
_FILETYPE_AGENDA_PACKET = 2
_FILETYPE_MINUTES = 4


def _norm_body(body: Optional[str]) -> str:
    """Lowercase + collapse whitespace for loose body comparison."""
    return " ".join((body or "").lower().split())


def _body_matches(want: Optional[str], have: Optional[str]) -> bool:
    """Best-effort body match: empty ``want`` matches anything; otherwise a
    loose substring test either direction (the video clip's body name and the
    portal's category rarely match verbatim — "City Commission Meeting" vs
    "City Commission")."""
    w, h = _norm_body(want), _norm_body(have)
    if not w or not h:
        return True
    return w in h or h in w


class CivicClerkAgendaSource:
    """CivicClerk JSON-API adapter implementing the ``AgendaSource`` protocol."""

    def __init__(self, cfg, log: Callable[..., None]):
        self.cfg = cfg
        self.log = log
        self.progress: Callable[[str], None] = lambda msg: None
        # e.g. "https://parisky.api.civicclerk.com" (no trailing slash).
        self.api_base: str = (getattr(cfg, "agenda_api_base", "") or "").rstrip("/")
        # Optional public portal URL (citations); not required for fetching.
        self.portal: str = (getattr(cfg, "agenda_portal", "") or "").rstrip("/")
        self.force_reprocess = False

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------
    def _events_for_date(self, date: str) -> List[Dict[str, Any]]:
        """Fetch events whose start date is the given ISO ``YYYY-MM-DD``.

        Uses an OData ``startDateTime`` range filter for the whole UTC day.
        Returns the raw event dicts (``value`` array), or ``[]`` on any error.
        Isolated for mocking in tests.
        """
        if not self.api_base:
            self.log("CivicClerkAgendaSource: no api_base configured", "WARNING")
            return []
        # Inclusive whole-day UTC window. CivicClerk stores startDateTime in Z.
        flt = (
            f"startDateTime ge {date}T00:00:00Z and "
            f"startDateTime le {date}T23:59:59Z"
        )
        url = f"{self.api_base}/v1/Events"
        params = {"$filter": flt, "$orderby": "startDateTime desc"}
        try:
            resp = requests.get(
                url, params=params, timeout=30, headers={"Accept": "application/json"}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self.log(f"CivicClerk events fetch failed for {date}: {e}", "WARNING")
            return []
        value = data.get("value") if isinstance(data, dict) else None
        return value or []

    def _file_stream_url(self, file_id: int) -> str:
        """The verified download endpoint for a published file id."""
        return (
            f"{self.api_base}/v1/Meetings/"
            f"GetMeetingFileStream(fileId={file_id},plainText=false)"
        )

    def _pick_doc(
        self, date: str, body: Optional[str], kind: str
    ) -> Optional[AgendaDoc]:
        """Pick the best agenda/minutes doc for ``date`` (+ best-effort body).

        ``kind`` is ``"agenda"`` or ``"minutes"``. For agendas, prefer the
        full **Agenda Packet** (fileType 2) — that's the one that can embed a
        prior meeting's Table of Motions — and fall back to a plain Agenda
        (fileType 1). For minutes, take fileType 4 / type "Minutes".
        """
        events = self._events_for_date(date)
        if not events:
            return None

        # Prefer body-matching events but keep the rest as a fallback so a
        # single same-day meeting still resolves when names diverge.
        matching = [e for e in events if _body_matches(body, e.get("eventCategoryName") or e.get("categoryName"))]
        candidates = matching or events

        for event in candidates:
            files = event.get("publishedFiles") or []
            ev_body = event.get("eventCategoryName") or event.get("categoryName")
            if kind == "agenda":
                packet = self._find_file(files, types={_FILETYPE_AGENDA_PACKET}, type_names={"agenda packet"})
                plain = self._find_file(files, types={_FILETYPE_AGENDA}, type_names={"agenda"})
                chosen = packet or plain
            else:  # minutes
                chosen = self._find_file(files, types={_FILETYPE_MINUTES}, type_names={"minutes"})
            if chosen is None:
                continue
            file_id = chosen.get("fileId")
            if not file_id:
                continue
            return AgendaDoc(
                date=date,
                url=self._file_stream_url(int(file_id)),
                kind=kind,
                body=ev_body,
                title=chosen.get("name"),
            )
        return None

    @staticmethod
    def _find_file(
        files: List[Dict[str, Any]], *, types: set, type_names: set
    ) -> Optional[Dict[str, Any]]:
        """Find the first published file matching either a numeric ``fileType``
        or a (lowercased) ``type`` string. Matching on both guards against the
        API changing one or the other."""
        for f in files:
            ft = f.get("fileType")
            tn = str(f.get("type") or "").strip().lower()
            if ft in types or tn in type_names:
                return f
        return None

    # ------------------------------------------------------------------
    # Download + extract (mirrors GranicusSource.download_agenda's flow:
    # cache check -> GET -> PDF magic-byte guard -> extract_pdf_text -> write)
    # ------------------------------------------------------------------
    def _download_and_extract_pdf(
        self, doc: AgendaDoc, clip_dir: Path, *, prefix: str
    ) -> Dict[str, Any]:
        """Fetch ``doc`` as a PDF into ``clip_dir`` and extract text.

        Returns the GranicusSource-shaped dict for the given ``prefix``
        (``"agenda"`` or ``"minutes"``). Files are named
        ``{date}_{prefix}_{kind}_civicclerk.pdf`` / ``.txt`` so they coexist
        with any Granicus-named files and the existing ``*agenda*`` /
        ``*minutes*`` globs (used by GranicusSource's cache check and by
        backfill) still discover them.
        """
        is_agenda = prefix == "agenda"
        result = empty_agenda_result() if is_agenda else empty_minutes_result()

        clip_dir.mkdir(parents=True, exist_ok=True)
        pdf_filename = f"{doc.date}_{prefix}_civicclerk.pdf"
        txt_filename = f"{doc.date}_{prefix}_civicclerk.txt"
        pdf_path = clip_dir / pdf_filename
        txt_path = clip_dir / txt_filename

        # Cache: reuse existing extracted text unless --force.
        existing_txt = list(clip_dir.glob(f"*{prefix}*.txt"))
        if existing_txt and not self.force_reprocess:
            txt_path = existing_txt[0]
            self.progress(f"{prefix.title()} text already exists - loading from file")
            try:
                result["text"] = txt_path.read_text(encoding="utf-8")
            except OSError:
                result["text"] = None
            existing_pdf = list(clip_dir.glob(f"*{prefix}*.pdf"))
            result["pdf_file"] = existing_pdf[0].name if existing_pdf else None
            result["txt_file"] = txt_path.name
            return result

        self.log(f"Downloading {prefix} from CivicClerk {doc.url}")
        try:
            resp = requests.get(doc.url, timeout=30, allow_redirects=True)
        except Exception as e:
            self.log(f"CivicClerk {prefix} download error: {e}", "WARNING")
            return result

        content_type = resp.headers.get("content-type", "")
        is_pdf = "pdf" in content_type.lower() or resp.content[:4] == b"%PDF"
        if resp.status_code != 200 or not is_pdf:
            self.progress(f"No PDF {prefix} available from CivicClerk for {doc.date}")
            return result

        with open(pdf_path, "wb") as fh:
            fh.write(resp.content)
        result["pdf_file"] = pdf_filename
        self.progress(f"Downloaded {prefix} PDF ({len(resp.content) / 1024:.1f} KB)")

        text = extract_pdf_text(pdf_path, log_fn=self.log, progress_fn=self.progress)
        if text:
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            result["txt_file"] = txt_filename
            result["text"] = text
            self.progress(f"Extracted {len(text)} chars from {prefix} PDF")
        else:
            self.progress(f"Could not extract text from {prefix} PDF")
        return result

    # ------------------------------------------------------------------
    # AgendaSource protocol
    # ------------------------------------------------------------------
    def fetch_for_date(
        self, date: str, body: Optional[str], clip_dir: Path
    ) -> Dict[str, Any]:
        if not date:
            return empty_agenda_result()
        doc = self._pick_doc(date, body, "agenda")
        if doc is None:
            self.progress(f"No CivicClerk agenda found for {date}")
            return empty_agenda_result()
        return self._download_and_extract_pdf(doc, clip_dir, prefix="agenda")

    def fetch_minutes_for_date(
        self, date: str, body: Optional[str], clip_dir: Path
    ) -> Dict[str, Any]:
        if not date:
            return empty_minutes_result()
        doc = self._pick_doc(date, body, "minutes")
        if doc is None:
            self.progress(f"No CivicClerk minutes found for {date}")
            return empty_minutes_result()
        return self._download_and_extract_pdf(doc, clip_dir, prefix="minutes")
