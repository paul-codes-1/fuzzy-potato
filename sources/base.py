"""VideoSource protocol — the swappable seam for civic video portals.

The pipeline (``main.py``) was originally hard-wired to LFUCG's Granicus
portal: clip-URL construction, ``ViewPublisher.php`` scraping, yt-dlp audio
downloads, ``AgendaViewer.php`` / ``MinutesViewer.php`` fetches, and the
WebVTT caption pull all lived as methods on ``LFUCGPipeline``. None of the
Bluegrass neighbor jurisdictions run Granicus (see
``deploy/lightsail/MULTI_COUNTY_EXPANSION_SPEC.md`` §1), so this module
defines the minimal interface that a *future* portal (YouTube, Legistar,
CivicClerk, …) reimplements, and ``GranicusSource`` is the first — and so
far only — concrete implementation (a behavior-preserving lift of the old
methods).

Everything *downstream* of the source stays in the pipeline and is
portal-agnostic: Whisper transcription, the two-pass summary, the
Table-of-Motions extraction, the WebVTT *parsing* (``granicus_captions.py``
— only the *fetch* is portal-specific), RAG ingestion, and SEO artifacts.

See ``deploy/lightsail/COUNTY2_ENGINEERING_SCOPE.md`` §WS2 and
``MULTI_COUNTY_EXPANSION_SPEC.md`` §2.4 for the design.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class MeetingRef:
    """A stable reference to one meeting video as the source exposes it.

    ``clip_id`` is the source's native identifier — a Granicus ``clip_id``,
    a Legistar ``EventId``, a YouTube ``videoId``. ``title`` / ``date`` /
    ``body`` are populated when the listing exposes them (the listing scrape
    may know the date even before the per-clip metadata pass runs).
    """

    clip_id: str
    title: Optional[str] = None
    date: Optional[str] = None  # ISO YYYY-MM-DD if known
    body: Optional[str] = None


@runtime_checkable
class VideoSource(Protocol):
    """Everything jurisdiction/portal-specific. ``main.py`` calls ONLY these.

    Implementations read their jurisdiction config (``config.get_config()``)
    and a ``log`` callable at construction time. They own URL construction,
    listing enumeration, media/document downloads, and the caption *fetch*;
    they do NOT own transcription, summarization, or VTT parsing.
    """

    # --- discovery -----------------------------------------------------
    def list_meetings(self) -> List[MeetingRef]:
        """Enumerate available meetings (Granicus: scrape ViewPublisher)."""
        ...

    def get_metadata(self, ref: MeetingRef) -> Dict[str, Any]:
        """Per-clip portal metadata — at minimum the authoritative date.

        For Granicus this is the ``ViewPublisher`` listing date lookup; the
        body-taxonomy parse stays in the pipeline (it's config-driven and
        portal-agnostic). Returns a dict that may include ``date`` and
        ``title``.
        """
        ...

    # --- URL builders / citations -------------------------------------
    def canonical_url(self, clip_id: int) -> str:
        """The public source-video permalink used for citations."""
        ...

    # --- media + documents --------------------------------------------
    def download_audio(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """Download the meeting audio into ``clip_dir``. Returns filename."""
        ...

    def fetch_captions(self, clip_id: int, clip_dir: Path) -> Optional[Path]:
        """Fetch the raw WebVTT caption track. Parsing stays in the pipeline."""
        ...

    def download_agenda(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download + extract the agenda packet (``{}``-shaped if absent)."""
        ...

    def download_minutes(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download + extract official minutes (``{}``-shaped if absent)."""
        ...
