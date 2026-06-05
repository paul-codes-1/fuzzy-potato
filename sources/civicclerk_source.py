"""CivicClerkSource — a PRIMARY ``VideoSource`` for document-driven counties.

PR-6 (see ``deploy/lightsail/COUNTY2_ENGINEERING_SCOPE.md``). Paris, KY posts
its meeting *video* to YouTube, but **YouTube hard-blocks both downloads AND
captions from any datacenter / cloud IP** — verified live from the Lightsail
box: ``yt-dlp`` and ``youtube-transcript-api`` both return "Sign in to confirm
you're not a bot" / ``RequestBlocked`` regardless of ``player_client`` choice.
There is no audio, and no caption track, to work with from the server.

Paris's **CivicClerk** portal, however, serves the official **Agenda** and
**Minutes** documents for free with no bot-block (verified working). Those
documents — votes, motions, appropriations, decisions — ARE the authoritative
record of what a meeting did. So Paris is built as a **document-driven
archive**: each meeting's "transcript" is its official minutes (falling back to
the agenda when minutes aren't published yet), and the existing
facts → summary → RAG flow runs on that text unchanged. No YouTube, no audio,
no Whisper.

DESIGN — a VideoSource that reuses the WS4 CivicClerk API code
==============================================================
``CivicClerkSource`` implements the full ``VideoSource`` Protocol but is
*primary* (selected via ``[source] type = "civicclerk"``), unlike the WS4
``CivicClerkAgendaSource`` which is a *secondary* agenda fallback behind a
YouTube/Granicus video source. To avoid duplicating the CivicClerk API code
(events query, ``GetMeetingFileStream`` download, PDF extraction), this class
**composes** a ``CivicClerkAgendaSource`` internally and reuses its helpers
(``_events_for_date`` / ``_file_stream_url`` / ``_find_file`` /
``_download_and_extract_pdf``). The only genuinely new API method is
``_list_events`` — a back-catalog enumeration the agenda fallback never needed
(it only ever queries a single known date).

Synthetic clip-id map (COPIED from ``sources/youtube.py``)
----------------------------------------------------------
The pipeline assumes a monotonic integer ``clip_id`` everywhere
(``state.json``, ``clips/<id>/`` dirs, ``search.db``). CivicClerk events have
their own integer ``id``s, but they're a portal-internal sequence we don't
control (and could collide with a hypothetical Granicus tenant). So — exactly
like ``YouTubeSource`` — this maintains a persistent map ``event id (str) →
local int clip_id`` at ``<output_dir>/source_ids.json``, seeded at 1, stable /
monotonic / atomic-write. Every ``VideoSource`` method that takes an int
``clip_id`` reverse-maps int → event id internally.

``source_ids.json`` schema (shared with YouTubeSource's writer; the ``videos``
key holds whatever the active source's native id is — a YouTube videoId or, for
this source, a CivicClerk event id as a string)::

    {
      "version": 1,
      "videos": {
        "<event_id>": {"clip_id": <int>, "title": "<str|null>", "date": "<YYYY-MM-DD|null>", "body": "<str|null>"},
        ...
      }
    }

The map caches ``title`` / ``date`` / ``body`` so ``get_clip_title`` /
``canonical_url`` / ``get_metadata`` / ``download_minutes`` resolve int →
event id (and read cached metadata) without re-enumerating the portal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .base import MeetingRef
from .civicclerk import CivicClerkAgendaSource


class CivicClerkSource:
    """CivicClerk JSON-API adapter implementing the ``VideoSource`` Protocol.

    Reads its endpoint from ``cfg.agenda_api_base`` (the same ``[agenda]
    api_base`` WS4 already populates) and an optional public portal from
    ``cfg.agenda_portal``. Mirrors ``GranicusSource``'s settable attributes
    (``view_id``, ``force_reprocess``, ``progress``) so ``main.py``'s
    ``make_source`` + the sync block (``self.source.view_id = ...``) work
    unchanged — ``view_id`` is meaningless for CivicClerk but accepted/ignored.
    """

    def __init__(self, cfg, log: Callable[..., None]):
        self.cfg = cfg
        self.log = log
        self._progress: Callable[[str], None] = lambda msg: None

        # e.g. "https://parisky.api.civicclerk.com" (no trailing slash).
        self.api_base: str = (getattr(cfg, "agenda_api_base", "") or "").rstrip("/")
        # Optional public portal URL (citations); not required for fetching.
        self.portal: str = (getattr(cfg, "agenda_portal", "") or "").rstrip("/")

        # Seed for the synthetic integer id space (default 1; the Granicus
        # first_clip_id is portal-specific and meaningless here, same posture
        # as YouTubeSource). Reuses source_youtube_start_id so a county can
        # tune it via the same knob without a new config field.
        self.start_id: int = int(getattr(cfg, "source_youtube_start_id", 1))

        # Mirror GranicusSource's settable attributes so the pipeline's sync
        # block doesn't AttributeError. view_id is inert for CivicClerk.
        self.view_id = str(getattr(cfg, "default_view_id", ""))
        self._force_reprocess = False

        # Compose the WS4 agenda adapter for its CivicClerk API helpers
        # (_events_for_date / _file_stream_url / _find_file /
        # _download_and_extract_pdf / _pick_doc). We do NOT reimplement those.
        self._api = CivicClerkAgendaSource(cfg, log)

        # Persistent event id → int clip_id map (alongside state.json). Same
        # file + schema YouTubeSource uses (only one primary source is active
        # per jurisdiction, so there's no key collision).
        output_dir = Path(getattr(cfg, "output_dir", "./lfucg_output"))
        self.id_map_path = output_dir / "source_ids.json"
        # {event_id(str): {"clip_id": int, "title": str|None, "date": str|None, "body": str|None}}
        self._events: Dict[str, Dict[str, Any]] = {}
        self._load_id_map()

    # ------------------------------------------------------------------
    # progress / force_reprocess proxy onto the composed agenda adapter so
    # its cached-file checks + progress printing stay in sync with ours
    # (the pipeline sets self.source.progress / .force_reprocess once).
    # ------------------------------------------------------------------
    @property
    def progress(self) -> Callable[[str], None]:
        return self._progress

    @progress.setter
    def progress(self, fn: Callable[[str], None]) -> None:
        self._progress = fn
        self._api.progress = fn

    @property
    def force_reprocess(self) -> bool:
        return self._force_reprocess

    @force_reprocess.setter
    def force_reprocess(self, value: bool) -> None:
        self._force_reprocess = value
        self._api.force_reprocess = value

    # ------------------------------------------------------------------
    # Synthetic id map (event id <-> int clip_id) persistence.
    # Mirrors YouTubeSource._load_id_map / _save_id_map / _next_clip_id.
    # ------------------------------------------------------------------
    def _load_id_map(self) -> None:
        """Load source_ids.json if present; tolerate absence/corruption."""
        if not self.id_map_path.exists():
            self._events = {}
            return
        try:
            with self.id_map_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            videos = raw.get("videos", {}) if isinstance(raw, dict) else {}
            self._events = {}
            for eid, entry in videos.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    cid = int(entry["clip_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                self._events[str(eid)] = {
                    "clip_id": cid,
                    "title": entry.get("title"),
                    "date": entry.get("date"),
                    "body": entry.get("body"),
                }
        except Exception as e:  # pragma: no cover - defensive
            self.log(f"Could not read {self.id_map_path.name}: {e}", "WARNING")
            self._events = {}

    def _save_id_map(self) -> None:
        """Atomically persist the event id → clip_id map."""
        self.id_map_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "videos": self._events}
        tmp = self.id_map_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        tmp.replace(self.id_map_path)

    def _next_clip_id(self) -> int:
        """Next sequential int id: ``start_id`` when empty, else max+1."""
        if not self._events:
            return self.start_id
        current_max = max(v["clip_id"] for v in self._events.values())
        return max(current_max + 1, self.start_id)

    def _event_id_for(self, clip_id: int) -> Optional[str]:
        """Reverse-map an int clip_id back to its CivicClerk event id."""
        cid = int(clip_id)
        for eid, entry in self._events.items():
            if entry["clip_id"] == cid:
                return eid
        return None

    # ------------------------------------------------------------------
    # API: full back-catalog enumeration (the one method the agenda
    # fallback never needed — it only ever queries a single known date).
    # ------------------------------------------------------------------
    def _list_events(self, page_size: int = 100, max_pages: int = 50) -> List[Dict[str, Any]]:
        """Enumerate ALL past events via ``/v1/Events`` (paginated, newest
        first). Returns the raw event dicts. Isolated for mocking in tests.

        Uses an OData ``startDateTime le <now>`` filter so future placeholder
        events don't get clip ids, ``$orderby=startDateTime desc``, and
        ``$top``/``$skip`` paging. Stops when a page returns fewer than
        ``page_size`` rows (or ``max_pages`` is hit, a safety cap so a
        misbehaving API can't loop forever).
        """
        if not self.api_base:
            self.log("CivicClerkSource: no api_base configured", "WARNING")
            return []

        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flt = f"startDateTime le {now_iso}"
        url = f"{self.api_base}/v1/Events"

        events: List[Dict[str, Any]] = []
        for page in range(max_pages):
            params = {
                "$filter": flt,
                "$orderby": "startDateTime desc",
                "$top": page_size,
                "$skip": page * page_size,
            }
            try:
                resp = requests.get(
                    url, params=params, timeout=30,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                self.log(f"CivicClerk events list failed (page {page}): {e}", "WARNING")
                break
            value = data.get("value") if isinstance(data, dict) else None
            batch = value or []
            events.extend(batch)
            if len(batch) < page_size:
                break
        return events

    @staticmethod
    def _event_body(event: Dict[str, Any]) -> Optional[str]:
        """The portal's body/category for an event (matches the agenda
        adapter's preference: eventCategoryName, then categoryName)."""
        return event.get("eventCategoryName") or event.get("categoryName")

    @staticmethod
    def _event_date(event: Dict[str, Any]) -> Optional[str]:
        """ISO ``YYYY-MM-DD`` from the event's startDateTime (or eventDate)."""
        raw = event.get("startDateTime") or event.get("eventDate")
        if not raw:
            return None
        s = str(raw)
        # CivicClerk emits "2026-05-12T09:00:00Z"; the date is the leading 10.
        return s[:10] if len(s) >= 10 else None

    # ------------------------------------------------------------------
    # Discovery (VideoSource protocol)
    # ------------------------------------------------------------------
    def list_meetings(self) -> List[MeetingRef]:
        """Enumerate the CivicClerk portal's full back-catalog and return
        ``MeetingRef``s with assigned int ids.

        New events (not already in the map) get the next sequential int id;
        the map is persisted. Refs are emitted oldest-first so id assignment
        is chronological-ish (mirrors YouTubeSource).
        """
        raw_events = self._list_events()
        if not raw_events:
            return []

        parsed: List[Dict[str, Any]] = []
        for e in raw_events:
            if not isinstance(e, dict):
                continue
            eid = e.get("id")
            if eid is None:
                continue
            parsed.append({
                "id": str(eid),
                "title": e.get("eventName"),
                "date": self._event_date(e),
                "body": self._event_body(e),
            })

        # Oldest-first so id assignment is chronological-ish. Undated events
        # sort last (high sentinel), tie-broken by id for determinism.
        parsed.sort(key=lambda p: (p["date"] or "9999-99-99", p["id"]))

        changed = False
        for p in parsed:
            eid = p["id"]
            existing = self._events.get(eid)
            if existing is None:
                self._events[eid] = {
                    "clip_id": self._next_clip_id(),
                    "title": p["title"],
                    "date": p["date"],
                    "body": p["body"],
                }
                changed = True
            else:
                # Refresh metadata if we learned a value; NEVER reassign id.
                for field in ("title", "date", "body"):
                    if p[field] and existing.get(field) != p[field]:
                        existing[field] = p[field]
                        changed = True

        if changed:
            self._save_id_map()

        refs: List[MeetingRef] = []
        for p in parsed:
            entry = self._events[p["id"]]
            refs.append(
                MeetingRef(
                    clip_id=str(entry["clip_id"]),
                    title=entry.get("title"),
                    date=entry.get("date"),
                    body=entry.get("body"),
                )
            )
        return refs

    def scrape_available_clips(self) -> List[int]:
        """Return the int ids (sorted), via ``list_meetings``."""
        refs = self.list_meetings()
        return sorted(int(r.clip_id) for r in refs)

    def get_clip_title(self, clip_id: int) -> Optional[str]:
        """Reverse-map → event id → cached title."""
        event_id = self._event_id_for(clip_id)
        if event_id is None:
            self.log(f"No event mapped for clip {clip_id}", "WARNING")
            return None
        return self._events[event_id].get("title")

    def get_metadata(self, ref: MeetingRef) -> Dict[str, Any]:
        """Per-clip metadata — the authoritative date + title from the cached
        map. The body-taxonomy parse stays in main.py (config-driven)."""
        event_id = self._event_id_for(int(ref.clip_id))
        if event_id is None:
            return {"date": None, "title": None}
        entry = self._events[event_id]
        return {"date": entry.get("date"), "title": entry.get("title")}

    # ------------------------------------------------------------------
    # URL builders / citations (VideoSource protocol)
    # ------------------------------------------------------------------
    def canonical_url(self, clip_id: int) -> str:
        """Best-effort public CivicClerk portal permalink used for citations.

        The portal renders a meeting at ``<portal>/event/<event_id>``. When no
        public portal is configured we fall back to the API event resource so
        citations still resolve to *something* rather than crashing.
        """
        event_id = self._event_id_for(clip_id)
        base = self.portal or self.api_base
        if event_id is None:
            self.log(f"canonical_url: no event for clip {clip_id}", "WARNING")
            return base or ""
        if self.portal:
            return f"{self.portal}/event/{event_id}"
        # API fallback (no public portal configured).
        return f"{self.api_base}/v1/Events({event_id})"

    # ------------------------------------------------------------------
    # Media (VideoSource protocol) — document-driven: NO audio, NO captions.
    # ------------------------------------------------------------------
    def download_audio(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """No audio for a document-driven source (YouTube blocks downloads
        from the server). Always ``None`` — the pipeline's document-driven
        branch keys on exactly this."""
        return None

    def fetch_captions(self, clip_id: int, clip_dir: Path) -> Optional[Path]:
        """No caption track for a document-driven source (YouTube blocks
        captions from the server too). Always ``None``."""
        return None

    # ------------------------------------------------------------------
    # Documents (VideoSource protocol) — the actual content. Reverse-map the
    # int clip_id → event, then reuse the WS4 agenda adapter's pick + fetch
    # keyed on the event's date + body, returning the SAME dict shape
    # GranicusSource returns.
    # ------------------------------------------------------------------
    def _event_date_body(self, clip_id: int) -> tuple[Optional[str], Optional[str]]:
        """Resolve a clip id to its (date, body) from the cached map."""
        event_id = self._event_id_for(clip_id)
        if event_id is None:
            return None, None
        entry = self._events[event_id]
        return entry.get("date"), entry.get("body")

    def download_agenda(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch + extract the Agenda (Packet) PDF for this clip's meeting.

        Returns the SAME shape ``GranicusSource.download_agenda`` returns:
        ``{"pdf_file", "txt_file", "text"}`` (all ``None`` on a miss). Prefers
        the date carried by the caller (``date`` arg, populated by the
        pipeline) but falls back to the cached map's date.
        """
        ev_date, ev_body = self._event_date_body(clip_id)
        use_date = date or ev_date
        if not use_date:
            self.log(f"download_agenda: no date for clip {clip_id}", "WARNING")
            from .agenda_base import empty_agenda_result
            return empty_agenda_result()
        return self._api.fetch_for_date(use_date, ev_body, clip_dir)

    def download_minutes(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch + extract the Minutes PDF for this clip's meeting.

        Returns the SAME shape ``GranicusSource.download_minutes`` returns:
        ``{"pdf_file", "html_file", "txt_file", "text"}`` (all ``None`` on a
        miss). The minutes are the authoritative content the pipeline's
        document-driven branch uses as the clip's transcript.
        """
        ev_date, ev_body = self._event_date_body(clip_id)
        use_date = date or ev_date
        if not use_date:
            self.log(f"download_minutes: no date for clip {clip_id}", "WARNING")
            from .agenda_base import empty_minutes_result
            return empty_minutes_result()
        return self._api.fetch_minutes_for_date(use_date, ev_body, clip_dir)
