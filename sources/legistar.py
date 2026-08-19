"""Legistar calendar scraper implementing the ``AgendaSource`` protocol.

LFUCG publishes official minutes to lexington.legistar.com within days of a
meeting, while its Granicus MinutesViewer lags ~2 months for council meetings
and has NEVER carried work-session minutes. This adapter is the SECONDARY
fallback behind the Granicus video source (WS4 pattern): it only runs when
Granicus returned no document, so Granicus-served docs stay byte-identical.

Listing discovery scrapes ``Calendar.aspx``. The page is a Telerik WebForms
app, so "show the whole year" is a ``__doPostBack`` on the period dropdown —
``_fetch_listing_html`` does the GET (for the hidden form fields) then POSTs
the "This Year" selection. If the postback fails we degrade to the GET's
current-month view rather than erroring the pipeline.

FRAGILITY / limits, in the CivicPlus adapter's spirit:
  1. "This Year" only — clips older than the current calendar year won't find
     documents here (``recent_only``). Granicus remains the archive source.
  2. The Legistar Web API (webapi.legistar.com/v1/lexington/Events) would be
     cleaner, but Lexington's tenant returns "'Agenda Draft Status' ... is not
     setup in settings" for every Events query (checked 2026-08-19) — a
     client-side Legistar misconfiguration we can't fix. Recheck occasionally;
     if they fix it, prefer the API over this scrape.
  3. Same-day meetings ("Urban County Council" + "Urban County Council Work
     Session" share a date) are disambiguated by TITLE with a work-session
     parity rule — see ``_title_matches``. Without a title we refuse to guess
     when more than one same-day doc matches.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .agenda_base import (
    AgendaDoc,
    empty_agenda_result,
    empty_minutes_result,
)
from documents import extract_pdf_text

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# View.ashx?M=A → agenda PDF, M=M → minutes PDF. (M=AADA / M=AMDA are the
# "accessible" HTML renditions; M=IC is the iCal.)
_VIEWASHX_RE = re.compile(
    r"View\.ashx\?M=(A|M)&(?:amp;)?ID=(\d+)&(?:amp;)?GUID=([A-Fa-f0-9-]+)"
)
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DUP_SUFFIX_RE = re.compile(r"\s*\(\s*\d+\s*\)\s*$")


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").lower().split())


def _title_matches(want_title: Optional[str], want_body: Optional[str],
                   have_body: Optional[str]) -> bool:
    """Match a clip against a Legistar meeting name.

    Prefers the clip TITLE (e.g. "Council Work Session (1)") over the coarse
    taxonomy body ("Council") because same-day council + work-session pairs
    are real (7/1/2026). Rule: the "work session" flag must AGREE on both
    sides, then the de-work-sessioned names must substring-match either way
    ("council" ⊂ "urban county council").
    """
    w = _norm(_DUP_SUFFIX_RE.sub("", want_title or "")) or _norm(want_body)
    h = _norm(have_body)
    if not w or not h:
        return True
    if ("work session" in w) != ("work session" in h):
        return False
    w2 = w.replace("work session", "").strip()
    h2 = h.replace("work session", "").strip()
    if not w2 or not h2:  # both were pure "work session" titles
        return True
    return w2 in h2 or h2 in w2


class LegistarAgendaSource:
    """Legistar ``Calendar.aspx`` scraper implementing ``AgendaSource``."""

    recent_only: bool = True  # "This Year" listing only (see module docstring)

    def __init__(self, cfg, log: Callable[..., None]):
        self.cfg = cfg
        self.log = log
        self.progress: Callable[[str], None] = lambda msg: None
        self.base_url: str = (getattr(cfg, "agenda_base_url", "") or "").rstrip("/")
        self.force_reprocess = False
        # One listing fetch per pipeline run — the weekly backfill asks about
        # up to 150 clips and the postback dance is 2 requests a pop.
        self._docs_cache: Optional[List[AgendaDoc]] = None

    # ------------------------------------------------------------------
    # Listing scrape
    # ------------------------------------------------------------------
    def _calendar_url(self) -> str:
        return f"{self.base_url}/Calendar.aspx"

    def _fetch_listing_html(self) -> Optional[str]:
        """GET Calendar.aspx, then POST the "This Year" period selection.

        Falls back to the plain GET body (current month + upcoming) when the
        postback fails. Isolated for mocking in tests.
        """
        if not self.base_url:
            self.log("LegistarAgendaSource: no base_url configured", "WARNING")
            return None
        headers = {"User-Agent": _UA}
        try:
            session = requests.Session()
            resp = session.get(self._calendar_url(), timeout=30, headers=headers)
            resp.raise_for_status()
            get_html = resp.text
        except Exception as e:
            self.log(f"Legistar calendar fetch failed: {e}", "WARNING")
            return None

        hidden = dict(re.findall(
            r'<input type="hidden" name="([^"]+)"[^>]*value="([^"]*)"', get_html))
        data = {
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$lstYears",
            "__EVENTARGUMENT": '{"Command":"Select","Index":1}',
            "__VIEWSTATE": hidden.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": hidden.get("__VIEWSTATEGENERATOR", ""),
            "__EVENTVALIDATION": hidden.get("__EVENTVALIDATION", ""),
            "ctl00_ContentPlaceHolder1_lstYears_ClientState": json.dumps(
                {"logEntries": [], "value": "", "text": "This Year",
                 "enabled": True}),
            "ctl00$ContentPlaceHolder1$lstYears": "This Year",
        }
        try:
            resp = session.post(
                self._calendar_url(), data=data, timeout=60, headers=headers)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            self.log(
                f"Legistar 'This Year' postback failed ({e}) — "
                "falling back to the current-month listing", "WARNING")
            return get_html

    def list_docs(self, html: Optional[str] = None) -> List[AgendaDoc]:
        """Parse the calendar grid into :class:`AgendaDoc` rows.

        A meeting row is any ``<tr>`` linking to ``MeetingDetail.aspx``; the
        meeting name is the row's first non-empty cell, the date the first
        ``m/d/yyyy`` in the row, and each ``View.ashx?M=A|M`` link becomes one
        doc. Pass ``html`` to parse a captured page (tests); otherwise the
        live listing is fetched once and cached for the instance's lifetime.
        """
        fetched_live = False
        if html is None:
            if self._docs_cache is not None:
                return self._docs_cache
            html = self._fetch_listing_html()
            fetched_live = True
            if not html:
                return []

        soup = BeautifulSoup(html, "lxml")
        docs: List[AgendaDoc] = []
        for tr in soup.find_all("tr"):
            if not tr.find("a", href=re.compile(r"MeetingDetail\.aspx", re.I)):
                continue
            row_text = tr.get_text(" ", strip=True)
            dm = _DATE_RE.search(row_text)
            if not dm:
                continue
            mm, dd, yyyy = dm.groups()
            try:
                iso = f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
            except ValueError:
                continue
            body_name = None
            for td in tr.find_all("td"):
                cell = td.get_text(" ", strip=True)
                if cell:
                    body_name = cell
                    break
            for a in tr.find_all("a", href=True):
                vm = _VIEWASHX_RE.search(a["href"])
                if not vm:
                    continue
                kind = "minutes" if vm.group(1) == "M" else "agenda"
                url = urljoin(self.base_url + "/", a["href"])
                docs.append(AgendaDoc(
                    date=iso, url=url, kind=kind, body=body_name))

        # De-dup on (date, kind, url) — defensive; RadGrid renders once but a
        # cached+live merge or a repeated row must not double a doc.
        seen = set()
        unique: List[AgendaDoc] = []
        for d in docs:
            key = (d.date, d.kind, d.url)
            if key in seen:
                continue
            seen.add(key)
            unique.append(d)

        if fetched_live:
            self._docs_cache = unique
        return unique

    # ------------------------------------------------------------------
    # Pick + download + extract
    # ------------------------------------------------------------------
    def _pick_doc(self, date: str, body: Optional[str], kind: str,
                  title: Optional[str]) -> Optional[AgendaDoc]:
        docs = [d for d in self.list_docs() if d.date == date and d.kind == kind]
        if not docs:
            return None
        if not title and len(docs) > 1:
            # Without a title the work-session parity rule silently assumes
            # "not a work session" — on a day with both a council meeting and
            # a work session that guess attaches the wrong meeting's minutes.
            # Titles are always present for processed clips, so just refuse.
            self.log(
                f"Legistar: {len(docs)} same-day {kind} docs for {date} and "
                f"no clip title to disambiguate — skipping", "WARNING")
            return None
        matched = [d for d in docs if _title_matches(title, body, d.body)]
        if len(matched) == 1:
            return matched[0]
        if matched:
            # More than one same-day doc still matches — refuse to guess so a
            # clip can never get the wrong meeting's minutes attached.
            self.log(
                f"Legistar: {len(matched)} same-day {kind} candidates for "
                f"{date} ({title or body!r}) — skipping rather than guessing",
                "WARNING")
        return None

    def _download_and_extract_pdf(
        self, doc: AgendaDoc, clip_dir: Path, *, prefix: str
    ) -> Dict[str, Any]:
        """Fetch ``doc`` as a PDF + extract text. Same flow + return shape as
        ``GranicusSource.download_agenda``/``download_minutes`` (PDF branch).
        Files named ``{date}_{prefix}_legistar.pdf`` / ``.txt`` so existing
        ``*agenda*`` / ``*minutes*`` globs still discover them."""
        is_agenda = prefix == "agenda"
        result = empty_agenda_result() if is_agenda else empty_minutes_result()

        clip_dir.mkdir(parents=True, exist_ok=True)
        pdf_filename = f"{doc.date}_{prefix}_legistar.pdf"
        txt_filename = f"{doc.date}_{prefix}_legistar.txt"
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

        self.log(f"Downloading {prefix} from Legistar {doc.url}")
        try:
            resp = requests.get(
                doc.url, timeout=60, allow_redirects=True,
                headers={"User-Agent": _UA})
        except Exception as e:
            self.log(f"Legistar {prefix} download error: {e}", "WARNING")
            return result

        content_type = resp.headers.get("content-type", "")
        is_pdf = "pdf" in content_type.lower() or resp.content[:4] == b"%PDF"
        if resp.status_code != 200 or not is_pdf:
            self.progress(f"No PDF {prefix} available from Legistar for {doc.date}")
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
        self, date: str, body: Optional[str], clip_dir: Path,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not date:
            return empty_agenda_result()
        doc = self._pick_doc(date, body, "agenda", title)
        if doc is None:
            self.progress(f"No Legistar agenda found for {date}")
            return empty_agenda_result()
        return self._download_and_extract_pdf(doc, clip_dir, prefix="agenda")

    def fetch_minutes_for_date(
        self, date: str, body: Optional[str], clip_dir: Path,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not date:
            return empty_minutes_result()
        doc = self._pick_doc(date, body, "minutes", title)
        if doc is None:
            self.progress(f"No Legistar minutes found for {date}")
            return empty_minutes_result()
        return self._download_and_extract_pdf(doc, clip_dir, prefix="minutes")
