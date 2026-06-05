"""Video-source adapters (the portal seam — see ``sources/base.py``).

``make_source(cfg, log)`` returns the concrete ``VideoSource`` for the active
jurisdiction, keyed on ``cfg.source_type`` (default ``"granicus"``).
"""

from __future__ import annotations

from typing import Callable, Optional

from .agenda_base import AgendaDoc, AgendaSource
from .base import MeetingRef, VideoSource
from .civicclerk import CivicClerkAgendaSource
from .civicclerk_source import CivicClerkSource
from .civicplus import CivicPlusAgendaSource
from .granicus import GranicusSource
from .youtube import YouTubeSource

__all__ = [
    "MeetingRef",
    "VideoSource",
    "GranicusSource",
    "YouTubeSource",
    "CivicClerkSource",
    "make_source",
    "AgendaSource",
    "AgendaDoc",
    "CivicClerkAgendaSource",
    "CivicPlusAgendaSource",
    "make_agenda_source",
]


# Registry of known source types → constructor. Extend here when a new
# portal adapter lands.
_SOURCES: dict[str, type] = {
    "granicus": GranicusSource,
    "youtube": YouTubeSource,
    # PR-6: a PRIMARY document-driven source (no video/audio/captions). The
    # clip's content is its official CivicClerk minutes/agenda. Distinct from
    # the WS4 CivicClerkAgendaSource, which is a SECONDARY agenda fallback
    # behind a video source.
    "civicclerk": CivicClerkSource,
}

# Registry of known agenda-portal types → constructor (WS4). Extend here when a
# new structured-agenda adapter lands (e.g. Legistar, Swagit).
_AGENDA_SOURCES: dict[str, type] = {
    "civicclerk": CivicClerkAgendaSource,
    "civicplus": CivicPlusAgendaSource,
}


def make_source(cfg, log: Callable[..., None]) -> VideoSource:
    """Construct the VideoSource for ``cfg.source_type``.

    Defaults to GranicusSource. An unknown/empty source type falls back to
    Granicus with a warning rather than crashing the pipeline — a malformed
    TOML degrading to the historical behavior matches the config module's
    "fall back to LFUCG defaults" posture.
    """
    source_type = getattr(cfg, "source_type", "granicus") or "granicus"
    cls = _SOURCES.get(source_type)
    if cls is None:
        log(
            f"Unknown source_type={source_type!r}; falling back to GranicusSource",
            "WARNING",
        )
        cls = GranicusSource
    return cls(cfg, log)


def make_agenda_source(
    cfg, log: Callable[..., None]
) -> Optional[AgendaSource]:
    """Construct the optional AgendaSource for ``cfg.agenda_type`` (WS4).

    Returns ``None`` when ``agenda_type`` is empty — i.e. the jurisdiction has
    no separate agenda portal. LFUCG is in exactly that bucket (its agendas
    come from Granicus in-band), so this returns ``None`` for LFUCG and the
    pipeline's agenda-fallback path never runs — keeping Granicus behavior
    byte-identical.

    An unknown/unrecognized agenda_type also returns ``None`` (with a warning)
    rather than crashing — a malformed TOML degrades to "no agenda source",
    matching the config module's "fall back to defaults" posture.
    """
    agenda_type = (getattr(cfg, "agenda_type", "") or "").strip()
    if not agenda_type:
        return None
    cls = _AGENDA_SOURCES.get(agenda_type)
    if cls is None:
        log(
            f"Unknown agenda_type={agenda_type!r}; no agenda source built",
            "WARNING",
        )
        return None
    return cls(cfg, log)
