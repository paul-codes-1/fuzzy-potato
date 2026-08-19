"""AgendaSource protocol — a SEPARATE seam for agenda/minutes documents.

WS4 (see ``deploy/lightsail/COUNTY2_ENGINEERING_SCOPE.md`` §WS4 and
``MULTI_COUNTY_EXPANSION_SPEC.md`` §1). LFUCG's Granicus portal co-locates
the meeting *video* with its agenda + minutes + captions, so
``GranicusSource`` (a ``VideoSource``) owns all of it. No Bluegrass neighbor
works that way: their video lives on YouTube (or Facebook/Vimeo) and their
**structured agenda documents — if any — live on a SEPARATE portal**
(CivicPlus/CivicEngage Agenda Center, or CivicClerk). ``YouTubeSource``
therefore returns empty dicts from ``download_agenda``/``download_minutes``.

This module defines the optional, parallel seam that supplies those
documents for a jurisdiction whose *video* source has none. It is keyed by
**meeting date** (the video clip's date) — the agenda portal is matched to a
clip by date (+ best-effort body) exactly the way
``table_of_motions.resolve_target_clip`` already matches a table to a clip.

Design rules (see the PR-4 scope):

* **Additive / opt-in.** A jurisdiction with no ``[agenda]`` config block has
  no ``AgendaSource`` (``make_agenda_source`` returns ``None``), so the
  pipeline never touches this path. LFUCG is in exactly that bucket, so its
  Granicus agenda behavior is byte-identical.
* **Same return shape as ``GranicusSource``.** ``fetch_for_date`` returns the
  SAME dict ``GranicusSource.download_agenda`` returns
  (``{"pdf_file", "txt_file", "text"}``) and ``fetch_minutes_for_date``
  returns the SAME dict ``GranicusSource.download_minutes`` returns
  (``{"pdf_file", "html_file", "txt_file", "text"}``). That keeps everything
  downstream (topics, summary, ``table_of_motions``, RAG ingest) unchanged.
* **Reuse the existing extractors.** PDF text comes from
  ``documents.extract_pdf_text`` (pdfplumber + OCR fallback), HTML from
  ``documents.extract_html_text`` — the exact helpers ``GranicusSource``
  uses.

Concrete adapters: ``sources/civicclerk.py`` (CivicClerk JSON API) and
``sources/civicplus.py`` (CivicPlus Agenda Center HTML scrape).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable


# Empty-result constants matching GranicusSource's two shapes EXACTLY. Adapters
# return copies of these on a miss so the dict identity downstream is the same
# keys GranicusSource produces (verified against sources/granicus.py:
# download_agenda -> {"pdf_file", "txt_file", "text"};
# download_minutes -> {"pdf_file", "html_file", "txt_file", "text"}).
def empty_agenda_result() -> Dict[str, Any]:
    """The miss-shape for an agenda fetch (matches GranicusSource keys)."""
    return {"pdf_file": None, "txt_file": None, "text": None}


def empty_minutes_result() -> Dict[str, Any]:
    """The miss-shape for a minutes fetch (matches GranicusSource keys)."""
    return {"pdf_file": None, "html_file": None, "txt_file": None, "text": None}


@dataclass
class AgendaDoc:
    """A single agenda/minutes document discovered on a portal, before fetch.

    ``date`` is the ISO ``YYYY-MM-DD`` meeting date the document belongs to.
    ``url`` is a direct, fetchable URL to the PDF (or HTML) body. ``kind`` is
    ``"agenda"`` or ``"minutes"``. ``body`` is the portal's category/body name
    when known (CivicPlus category panel, CivicClerk ``eventCategoryName``),
    used for best-effort body matching. ``title`` is the document's display
    name when the portal exposes one (e.g. CivicClerk ``publishedFiles[].name``
    or the CivicPlus link text), useful for picking the agenda *packet* over a
    bare agenda.
    """

    date: str  # ISO YYYY-MM-DD
    url: str
    kind: str  # "agenda" | "minutes"
    body: Optional[str] = None
    title: Optional[str] = None


@runtime_checkable
class AgendaSource(Protocol):
    """Optional agenda-document portal, parallel to ``VideoSource``.

    ``main.py`` calls ONLY ``fetch_for_date`` / ``fetch_minutes_for_date``,
    and only when the *video* source returned an empty agenda/minutes result
    AND an ``AgendaSource`` is configured. Implementations read their
    jurisdiction config (``config.get_config()``) and a ``log`` callable at
    construction time. They own portal listing + per-meeting document URL
    discovery + the fetch; text extraction is delegated to ``documents.*``.
    """

    def fetch_for_date(
        self, date: str, body: Optional[str], clip_dir: Path,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find + download the agenda doc for ``date`` (best-effort ``body``).

        Returns the SAME shape ``GranicusSource.download_agenda`` returns:
        ``{"pdf_file", "txt_file", "text"}`` (all ``None`` on a miss).
        """
        ...

    def fetch_minutes_for_date(
        self, date: str, body: Optional[str], clip_dir: Path,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find + download the minutes doc for ``date`` (best-effort ``body``).

        Returns the SAME shape ``GranicusSource.download_minutes`` returns:
        ``{"pdf_file", "html_file", "txt_file", "text"}`` (all ``None`` on a
        miss).
        """
        ...
