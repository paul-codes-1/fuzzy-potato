"""Per-jurisdiction configuration — the single source of truth for
everything that differs between civic jurisdictions.

The pipeline + RAG API were originally hard-coded to Lexington-Fayette
(LFUCG). This module lifts the jurisdiction-specific bits — Granicus host
and view IDs, the starting clip ID, the meeting-body taxonomy, the
ChromaDB collection name, and the public site URL — into one config
object loaded from ``jurisdictions/<slug>.toml``, where ``<slug>`` is the
``JURISDICTION`` env var (default ``"lfucg"``).

Resolution order per field is: **explicit env var (back-compat) > TOML
value > built-in LFUCG default**. The built-in defaults reproduce the
historical hard-coded values exactly, so behavior is byte-identical for
LFUCG even if ``jurisdictions/lfucg.toml`` is absent, and a pre-existing
``GRANICUS_HOST`` / ``FIRST_CLIP_ID`` / ``GRANICUS_VIEW_ID`` in ``.env``
still wins.

Onboarding a new county = drop a ``jurisdictions/<slug>.toml`` + run the
process with ``JURISDICTION=<slug>``. No code fork.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Built-in fallback = the historical LFUCG hard-coded values. Keeping these
# here means the system runs identically even if the TOML file is missing.
_LFUCG_DEFAULTS: dict = {
    "slug": "lfucg",
    "name": "Lexington-Fayette Urban County Government",
    "granicus_host": "lfucg.granicus.com",
    "default_view_id": 14,
    # Order is "most-trafficked first" so the typical case resolves on the
    # first request; non-council bodies live on their own views.
    "listing_view_fallbacks": [14, 9, 2, 4, 5, 6, 7, 8, 10, 13, 16, 17],
    "first_clip_id": 6669,
    "body_patterns": ["WQFB", "CAC", "LFUCG", "Council", "Commission", "Board", "Committee"],
    "body_acronyms": ["WQFB", "CAC", "LFUCG"],
    "chroma_collection": "lfucg_meetings",
    "site_url": "https://meetings.lexingtonky.news",
    # Video-source adapter selector (WS2). "granicus" is the only
    # implementation today; a later PR adds "youtube". Read from the
    # TOML's [source] section, key `type`. LFUCG is Granicus.
    "source_type": "granicus",
    # Public-facing identity (the prose/branding lifted out of seo.py,
    # rag/prompts.py, rag/mcp_server.py, rag/server.py). Defaults reproduce
    # the historical LFUCG literals so output stays byte-identical.
    "publication_name": "LFUCG Meeting Archive",
    "operator_name": "Paul Oliva",
    "editor_email": "editor@lexingtonky.news",
}


@dataclass(frozen=True)
class Jurisdiction:
    slug: str
    name: str
    granicus_host: str
    default_view_id: int
    listing_view_fallbacks: tuple[int, ...]
    first_clip_id: int
    body_patterns: tuple[str, ...]
    body_acronyms: frozenset[str]
    chroma_collection: str
    site_url: str
    source_type: str
    publication_name: str
    operator_name: str
    editor_email: str
    output_dir: str


def _load_toml(slug: str) -> dict:
    """Read jurisdictions/<slug>.toml and flatten its sections into our key
    space. Returns {} if the file or a TOML parser isn't available."""
    path = Path(__file__).resolve().parent / "jurisdictions" / f"{slug}.toml"
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+ (prod pins 3.11)
    except ModuleNotFoundError:  # pragma: no cover - 3.10 dev fallback
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    flat: dict = {}
    flat.update(raw.get("jurisdiction", {}))
    for sect, keys in (
        ("granicus", ("host", "default_view_id", "listing_view_fallbacks", "first_clip_id")),
        ("taxonomy", ("body_patterns", "body_acronyms")),
        ("storage", ("chroma_collection",)),
        ("site", ("site_url",)),
        ("source", ("type",)),
        ("publication", ("publication_name", "operator_name", "editor_email")),
    ):
        block = raw.get(sect, {})
        for key in keys:
            if key in block:
                # A few keys are renamed on flatten so they don't collide:
                # `granicus.host` -> `granicus_host`, `source.type` ->
                # `source_type`. Everything else keeps its key name.
                if (sect, key) == ("granicus", "host"):
                    flat_key = "granicus_host"
                elif (sect, key) == ("source", "type"):
                    flat_key = "source_type"
                else:
                    flat_key = key
                flat[flat_key] = block[key]
    return flat


@lru_cache(maxsize=None)
def get_config() -> Jurisdiction:
    """Resolve the active jurisdiction config (cached for the process)."""
    # Make sure a .env is loaded before reading env, regardless of the
    # caller's import order (some modules resolve config at import time).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # pragma: no cover - dotenv is a hard dep, but be safe
        pass

    slug = os.getenv("JURISDICTION", "lfucg")
    base = dict(_LFUCG_DEFAULTS)
    base.update(_load_toml(slug))

    # env var > toml/default for the historically env-driven fields.
    granicus_host = os.getenv("GRANICUS_HOST", base["granicus_host"])
    first_clip_id = int(os.getenv("FIRST_CLIP_ID", base["first_clip_id"]))
    default_view_id = int(os.getenv("GRANICUS_VIEW_ID", base["default_view_id"]))
    output_dir = os.getenv("LFUCG_OUTPUT_DIR", "./lfucg_output")

    return Jurisdiction(
        slug=base["slug"],
        name=base["name"],
        granicus_host=granicus_host,
        default_view_id=default_view_id,
        listing_view_fallbacks=tuple(base["listing_view_fallbacks"]),
        first_clip_id=first_clip_id,
        body_patterns=tuple(base["body_patterns"]),
        body_acronyms=frozenset(base["body_acronyms"]),
        chroma_collection=base["chroma_collection"],
        site_url=base["site_url"],
        source_type=base["source_type"],
        publication_name=base["publication_name"],
        operator_name=base["operator_name"],
        editor_email=base["editor_email"],
        output_dir=output_dir,
    )
