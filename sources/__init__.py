"""Video-source adapters (the portal seam — see ``sources/base.py``).

``make_source(cfg, log)`` returns the concrete ``VideoSource`` for the active
jurisdiction, keyed on ``cfg.source_type`` (default ``"granicus"``).
"""

from __future__ import annotations

from typing import Callable

from .base import MeetingRef, VideoSource
from .granicus import GranicusSource
from .youtube import YouTubeSource

__all__ = [
    "MeetingRef",
    "VideoSource",
    "GranicusSource",
    "YouTubeSource",
    "make_source",
]


# Registry of known source types → constructor. Extend here when a new
# portal adapter lands.
_SOURCES: dict[str, type] = {
    "granicus": GranicusSource,
    "youtube": YouTubeSource,
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
