"""CivicPlusAgendaSource — agenda/minutes via a CivicPlus Agenda Center scrape.

WS4 adapter for jurisdictions on a CivicPlus / CivicEngage **Agenda Center**
(in the Bluegrass survey: Georgetown, Woodford Co, Clark Co, Winchester,
Frankfort, Boyle Co, Danville). Unlike CivicClerk there is no JSON API — the
Agenda Center is a server-rendered HTML page — so this is a **best-effort HTML
scrape**, and parts of it are inherently fragile. The fragile parts are
clearly marked; see "FRAGILITY" below.

LIVE-INSPECTED STRUCTURE (www.georgetownky.gov/AgendaCenter, 2026-06-04)
=======================================================================
The page is a set of accordion category sections::

    <div class="listing ..." id="cat1">
      <h2 ...>City Council</h2>          <- category / body name
      ...
      <table id="category-panel-1" ...>
        <tbody>
          <tr class="catAgendaRow">
            <td>
              <h3 ...><strong aria-label="Agenda for June 8, 2026">Jun 8, 2026</strong></h3>
              <p>
                <a href="/AgendaCenter/ViewFile/Agenda/_06082026-370" target="_blank">
                  ... Agenda Packet (PDF)</a>
              </p>
            </td>
            <td class="minutes">
              <a href="/AgendaCenter/ViewFile/Minutes/_05182026-360">Minutes ...</a>
            </td>
            ...

Key, RELIABLE facts:
* PDF links are ``/AgendaCenter/ViewFile/{Agenda|Minutes}/_MMDDYYYY-NNN``.
  The date is machine-encoded in the slug (``_06082026-370`` -> 2026-06-08),
  so we parse the date from the HREF, not from locale-formatted display text.
  Verified the URL returns a real PDF (``%PDF-1.7``).
* The enclosing ``<div id="catN">`` carries an ``<h2>`` with the category /
  body name (e.g. "City Council", "Finance Committee") — used for body match.

FRAGILITY (why this is best-effort, not a stable API)
-----------------------------------------------------
1. **Only the server-rendered rows are scraped.** The Agenda Center shows the
   current year's rows per category in the initial HTML; older years load via
   an AJAX ``changeYear(...)`` call we do NOT drive. So this adapter sees the
   recent window only. That's fine for the going-forward Table-of-Motions hook
   (it needs the *next* meeting's packet, which is recent), but a deep
   historical backfill would miss older agendas. Flagged via
   ``self.recent_only = True``.
2. **CivicPlus markup varies by tenant + theme version.** The class names
   (``catAgendaRow``) and the ``/AgendaCenter/ViewFile/...`` href shape are
   the load-bearing anchors; both are stable across the CivicPlus tenants
   surveyed, but a heavily customized tenant could differ. We anchor on the
   HREF pattern (most stable) and degrade to "no doc found" rather than
   guessing wrong.

Config (``jurisdictions/<slug>.toml``)::

    [agenda]
    type = "civicplus"
    base_url = "https://www.georgetownky.gov"   # -> cfg.agenda_base_url
    # (the "/AgendaCenter" path is appended; trailing slashes are tolerated)
"""

from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from documents import extract_pdf_text

from .agenda_base import (
    AgendaDoc,
    empty_agenda_result,
    empty_minutes_result,
)

# The load-bearing, tenant-stable anchor: the ViewFile href shape. Captures
# kind (Agenda|Minutes) and the MMDDYYYY date slug.
_VIEWFILE_RE = re.compile(
    r"/AgendaCenter/ViewFile/(Agenda|Minutes)/_?(\d{2})(\d{2})(\d{4})-(\d+)",
    re.IGNORECASE,
)


def _norm_body(body: Optional[str]) -> str:
    return " ".join((body or "").lower().split())


def _body_matches(want: Optional[str], have: Optional[str]) -> bool:
    """Best-effort body match (loose substring either direction; empty matches
    anything). The video clip's body name and the Agenda Center category name
    rarely match verbatim ("City Council" vs "City Council Regular Meeting")."""
    w, h = _norm_body(want), _norm_body(have)
    if not w or not h:
        return True
    return w in h or h in w


def _iso_from_slug(mm: str, dd: str, yyyy: str) -> Optional[str]:
    """Convert an MMDDYYYY href slug to ISO ``YYYY-MM-DD`` (validated)."""
    try:
        return _date(int(yyyy), int(mm), int(dd)).isoformat()
    except (ValueError, TypeError):
        return None


class CivicPlusAgendaSource:
    """CivicPlus Agenda Center scraper implementing the ``AgendaSource`` protocol."""

    # The fragile-by-design flag (see module docstring FRAGILITY #1): we only
    # see the server-rendered (recent) rows, not AJAX-loaded historical years.
    recent_only: bool = True

    def __init__(self, cfg, log: Callable[..., None]):
        self.cfg = cfg
        self.log = log
        self.progress: Callable[[str], None] = lambda msg: None
        self.base_url: str = (getattr(cfg, "agenda_base_url", "") or "").rstrip("/")
        self.force_reprocess = False

    # ------------------------------------------------------------------
    # Listing scrape
    # ------------------------------------------------------------------
    def _agenda_center_url(self) -> str:
        return f"{self.base_url}/AgendaCenter"

    def _fetch_listing_html(self) -> Optional[str]:
        """GET the Agenda Center HTML. Isolated for mocking in tests."""
        if not self.base_url:
            self.log("CivicPlusAgendaSource: no base_url configured", "WARNING")
            return None
        try:
            resp = requests.get(
                self._agenda_center_url(),
                timeout=30,
                headers={
                    # A browser UA — some CivicPlus tenants 403 a bare client.
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    )
                },
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            self.log(f"CivicPlus Agenda Center fetch failed: {e}", "WARNING")
            return None

    def list_docs(self, html: Optional[str] = None) -> List[AgendaDoc]:
        """Parse the Agenda Center HTML into a list of :class:`AgendaDoc`.

        Walks the category accordion sections (``<div id="catN"><h2>NAME</h2>``)
        so each discovered ViewFile link carries its category/body name, and
        derives each doc's date from the machine-encoded href slug (NOT the
        locale-formatted display text). Pass ``html`` to parse a captured page
        (used by tests); otherwise the live page is fetched.
        """
        if html is None:
            html = self._fetch_listing_html()
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        docs: List[AgendaDoc] = []

        # Walk each category section so we can attach its body name. A section
        # is <div id="catN"> with a descendant <h2> naming the category. If the
        # tenant's markup doesn't expose sections we recognize, fall back to a
        # flat link sweep (body=None) below.
        sections = soup.select('div[id^="cat"]')
        recognized = False
        for sec in sections:
            sec_id = sec.get("id", "")
            # Only real category sections (id like "cat1"), not e.g. "category".
            if not re.fullmatch(r"cat\d+", sec_id):
                continue
            h2 = sec.find(["h2", "h3"])
            body_name = h2.get_text(strip=True) if h2 else None
            for a in sec.find_all("a", href=True):
                doc = self._doc_from_href(a["href"], body_name)
                if doc is not None:
                    recognized = True
                    docs.append(doc)

        if not recognized:
            # Fallback flat sweep: no category sections found, so body is
            # unknown. Still useful — date matching alone often resolves a
            # single same-day meeting.
            for a in soup.find_all("a", href=True):
                doc = self._doc_from_href(a["href"], None)
                if doc is not None:
                    docs.append(doc)

        # De-duplicate on (date, kind, url) — CivicPlus renders the same link
        # twice (a visible <a> plus a Download-popout <a>).
        seen = set()
        unique: List[AgendaDoc] = []
        for d in docs:
            key = (d.date, d.kind, d.url)
            if key in seen:
                continue
            seen.add(key)
            unique.append(d)
        return unique

    def _doc_from_href(self, href: str, body_name: Optional[str]) -> Optional[AgendaDoc]:
        m = _VIEWFILE_RE.search(href or "")
        if not m:
            return None
        kind_raw, mm, dd, yyyy, _meeting_id = m.groups()
        iso = _iso_from_slug(mm, dd, yyyy)
        if not iso:
            return None
        kind = "minutes" if kind_raw.lower() == "minutes" else "agenda"
        url = urljoin(self.base_url + "/", href.lstrip("/"))
        return AgendaDoc(date=iso, url=url, kind=kind, body=body_name)

    # ------------------------------------------------------------------
    # Pick + download + extract
    # ------------------------------------------------------------------
    def _pick_doc(
        self, date: str, body: Optional[str], kind: str
    ) -> Optional[AgendaDoc]:
        docs = [d for d in self.list_docs() if d.date == date and d.kind == kind]
        if not docs:
            return None
        # Prefer a body-matching doc. When NOTHING body-matches, only return a
        # doc if there's exactly ONE same-day option — otherwise return None
        # rather than guessing, so a clip whose body matches NEITHER of two
        # same-day meetings doesn't get the wrong meeting's agenda (and a
        # wrong Table of Motions) attached. An empty/None clip body still
        # matches a single doc via _body_matches's "empty matches anything".
        for d in docs:
            if _body_matches(body, d.body):
                return d
        return docs[0] if len(docs) == 1 else None

    def _download_and_extract_pdf(
        self, doc: AgendaDoc, clip_dir: Path, *, prefix: str
    ) -> Dict[str, Any]:
        """Fetch ``doc`` as a PDF + extract text. Same flow + return shape as
        ``GranicusSource.download_agenda``/``download_minutes`` (PDF branch).
        Files named ``{date}_{prefix}_civicplus.pdf`` / ``.txt`` so existing
        ``*agenda*`` / ``*minutes*`` globs still discover them."""
        is_agenda = prefix == "agenda"
        result = empty_agenda_result() if is_agenda else empty_minutes_result()

        clip_dir.mkdir(parents=True, exist_ok=True)
        pdf_filename = f"{doc.date}_{prefix}_civicplus.pdf"
        txt_filename = f"{doc.date}_{prefix}_civicplus.txt"
        pdf_path = clip_dir / pdf_filename
        txt_path = clip_dir / txt_filename

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

        self.log(f"Downloading {prefix} from CivicPlus {doc.url}")
        try:
            resp = requests.get(
                doc.url,
                timeout=30,
                allow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    )
                },
            )
        except Exception as e:
            self.log(f"CivicPlus {prefix} download error: {e}", "WARNING")
            return result

        content_type = resp.headers.get("content-type", "")
        is_pdf = "pdf" in content_type.lower() or resp.content[:4] == b"%PDF"
        if resp.status_code != 200 or not is_pdf:
            self.progress(f"No PDF {prefix} available from CivicPlus for {doc.date}")
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
            self.progress(f"No CivicPlus agenda found for {date}")
            return empty_agenda_result()
        return self._download_and_extract_pdf(doc, clip_dir, prefix="agenda")

    def fetch_minutes_for_date(
        self, date: str, body: Optional[str], clip_dir: Path
    ) -> Dict[str, Any]:
        if not date:
            return empty_minutes_result()
        doc = self._pick_doc(date, body, "minutes")
        if doc is None:
            self.progress(f"No CivicPlus minutes found for {date}")
            return empty_minutes_result()
        return self._download_and_extract_pdf(doc, clip_dir, prefix="minutes")
