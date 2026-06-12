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
    # Video-source adapter selector (WS2). "granicus" is the default; WS3
    # adds "youtube". Read from the TOML's [source] section, key `type`.
    # LFUCG is Granicus.
    "source_type": "granicus",
    # YouTube adapter opts (WS3). Empty for LFUCG/Granicus so behavior is
    # untouched. Read from the TOML's [source.youtube] subtable:
    # `channel_url` (the channel/playlist to enumerate), optional
    # `title_date_pattern` (a per-jurisdiction title→date regex escape hatch
    # for counties whose video titles don't carry a clean upload_date), and
    # optional `start_id` (the first synthetic clip id to hand out; defaults
    # to 1 — the Granicus `first_clip_id` is portal-specific and meaningless
    # for YouTube, so YouTube ids start at 1, not 6669).
    "source_youtube_channel_url": "",
    "source_youtube_title_date_pattern": "",
    "source_youtube_start_id": 1,
    # Agenda-document source adapter (WS4). SEPARATE from the video source:
    # YouTube counties have no in-band agendas, so a structured-agenda portal
    # (CivicClerk / CivicPlus) supplies agenda/minutes keyed by meeting date.
    # Read from the TOML's [agenda] section. Empty `agenda_type` means NO
    # agenda source — LFUCG is in this bucket, so Granicus's own download_agenda
    # is used exactly as today and behavior is byte-identical.
    #   [agenda] type = "civicclerk" | "civicplus"
    #   CivicClerk:  api_base = "https://<tenant>.api.civicclerk.com"
    #                portal   = "https://<tenant>.portal.civicclerk.com" (optional)
    #   CivicPlus:   base_url = "https://<host>"  (the /AgendaCenter path is appended)
    "agenda_type": "",
    "agenda_api_base": "",
    "agenda_portal": "",
    "agenda_base_url": "",
    # Public-facing identity (the prose/branding lifted out of seo.py,
    # rag/prompts.py, rag/mcp_server.py, rag/server.py). Defaults reproduce
    # the historical LFUCG literals so output stays byte-identical.
    "publication_name": "LFUCG Meeting Archive",
    "operator_name": "Paul Oliva",
    "editor_email": "editor@lexingtonky.news",
    # Frontend SPA identity overrides (WS-frontend). The React SPA is a single
    # bundle served to every jurisdiction; it fetches /data/site.json at boot
    # for all jurisdiction-specific strings (header, tagline, footer, video
    # provider, chat copy, …). `build_site_config()` DERIVES that JSON from the
    # fields above so the LFUCG defaults stay byte-identical; a TOML `[frontend]`
    # table deep-merges on top for per-county copy (e.g. document-driven cities
    # that have no "transcript"/"video", or a different short name). Stored raw.
    "_frontend": {},
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
    source_youtube_channel_url: str
    source_youtube_title_date_pattern: str
    source_youtube_start_id: int
    agenda_type: str
    agenda_api_base: str
    agenda_portal: str
    agenda_base_url: str
    publication_name: str
    operator_name: str
    editor_email: str
    frontend_overrides: dict
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
        # [agenda] (WS4): the structured-agenda portal. `type` -> agenda_type;
        # `api_base`/`portal`/`base_url` -> agenda_api_base/agenda_portal/
        # agenda_base_url. Absent for Granicus jurisdictions (LFUCG), so the
        # defaults stay empty and no agenda source is built.
        ("agenda", ("type", "api_base", "portal", "base_url")),
        ("publication", ("publication_name", "operator_name", "editor_email")),
    ):
        block = raw.get(sect, {})
        for key in keys:
            if key in block:
                # A few keys are renamed on flatten so they don't collide:
                # `granicus.host` -> `granicus_host`, `source.type` ->
                # `source_type`, and every `agenda.*` key gets an `agenda_`
                # prefix. Everything else keeps its key name.
                if (sect, key) == ("granicus", "host"):
                    flat_key = "granicus_host"
                elif (sect, key) == ("source", "type"):
                    flat_key = "source_type"
                elif sect == "agenda":
                    flat_key = f"agenda_{key}"
                else:
                    flat_key = key
                flat[flat_key] = block[key]

    # [source.youtube] is a nested subtable under [source]; flatten its keys
    # into the source_youtube_* namespace. Absent for Granicus jurisdictions
    # (e.g. LFUCG), so the defaults stay empty and Granicus is unaffected.
    youtube_block = raw.get("source", {}).get("youtube", {})
    if isinstance(youtube_block, dict):
        if "channel_url" in youtube_block:
            flat["source_youtube_channel_url"] = youtube_block["channel_url"]
        if "title_date_pattern" in youtube_block:
            flat["source_youtube_title_date_pattern"] = youtube_block["title_date_pattern"]
        if "start_id" in youtube_block:
            flat["source_youtube_start_id"] = youtube_block["start_id"]

    # [frontend] — free-form SPA copy overrides, passed through raw and
    # deep-merged onto the derived defaults by build_site_config(). Supports
    # nested tables ([frontend.video], [frontend.chat], [frontend.source]).
    frontend_block = raw.get("frontend", {})
    if isinstance(frontend_block, dict) and frontend_block:
        flat["_frontend"] = frontend_block
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
    toml_values = _load_toml(slug)
    if slug != "lfucg":
        # Fail loud instead of silently degrading to the LFUCG defaults — a
        # second county serving LFUCG host/views/identity is a real failure
        # mode (MULTI_COUNTY_EXPANSION_SPEC §2.7). The lfucg/unset path keeps
        # the historical degrade-to-defaults behavior per the module docstring.
        if not toml_values:
            toml_path = Path(__file__).resolve().parent / "jurisdictions" / f"{slug}.toml"
            if not toml_path.exists():
                raise RuntimeError(
                    f"JURISDICTION={slug!r} is set but jurisdictions/{slug}.toml "
                    "does not exist — create it (see MULTI_COUNTY_EXPANSION_SPEC "
                    "§2.3/§2.6) or unset JURISDICTION"
                )
            raise RuntimeError(
                f"jurisdictions/{slug}.toml exists but loaded empty — no TOML "
                "parser is available (requires Python 3.11+ tomllib or the "
                "tomli package) or the file defines no recognized keys"
            )
        toml_slug = toml_values.get("slug")
        if toml_slug is not None and toml_slug != slug:
            raise RuntimeError(
                f"jurisdictions/{slug}.toml declares slug={toml_slug!r} but "
                f"JURISDICTION={slug!r} — the TOML's slug must match its filename"
            )
    base.update(toml_values)

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
        source_youtube_channel_url=base["source_youtube_channel_url"],
        source_youtube_title_date_pattern=base["source_youtube_title_date_pattern"],
        source_youtube_start_id=int(base["source_youtube_start_id"]),
        agenda_type=base["agenda_type"],
        agenda_api_base=base["agenda_api_base"],
        agenda_portal=base["agenda_portal"],
        agenda_base_url=base["agenda_base_url"],
        publication_name=base["publication_name"],
        operator_name=base["operator_name"],
        editor_email=base["editor_email"],
        frontend_overrides=dict(base.get("_frontend", {})),
        output_dir=output_dir,
    )


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto a copy of ``base`` (override wins).
    Nested dicts merge key-by-key; scalars/lists replace."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def build_site_config(cfg: "Jurisdiction | None" = None) -> dict:
    """Build the runtime ``site.json`` the React SPA fetches at boot.

    Everything is DERIVED from the resolved :class:`Jurisdiction` so the LFUCG
    defaults reproduce the historical hard-coded SPA strings byte-for-byte, then
    the jurisdiction's TOML ``[frontend]`` table deep-merges on top. A new county
    needs zero code: drop a ``[frontend]`` block (or rely on the derivations).

    `source.kind` drives whether the SPA talks about "transcripts/video"
    (Granicus/YouTube cities) or "agenda & minutes documents" (CivicClerk
    document-driven cities). `video.provider` chooses the meeting-page player:
    ``granicus`` (embed by clip id), ``youtube`` (per-clip ``video_url`` from the
    clip metadata), or ``none`` (hide the section).
    """
    cfg = cfg or get_config()
    host = cfg.granicus_host
    granicus_base = f"https://{host}" if host else ""
    st = cfg.source_type
    source_kind = "document" if st == "civicclerk" else "video"
    platform = {
        "granicus": "Granicus",
        "civicclerk": "CivicClerk",
        "youtube": "YouTube",
    }.get(st, "Granicus")
    short = cfg.slug.upper()
    derived = {
        "archive_name": cfg.publication_name,
        "jurisdiction_full_name": cfg.name,
        "jurisdiction_short_name": short,
        "site_url": cfg.site_url.rstrip("/"),
        "contact_email": cfg.editor_email,
        "operator_name": cfg.operator_name,
        "operator_bio": f"Operated by {cfg.operator_name} as a civic-tech side project.",
        "operator_author_url": "",
        "tagline": f"{cfg.name} Meeting Transcripts & Summaries",
        "description": (
            f"Searchable archive of {cfg.name} council and committee meetings "
            "— transcripts, summaries, agendas, and minutes."
        ),
        "default_seo_title": f"{short} Meeting",
        "records_table_name": "Table of Motions",
        # The /ask "Coverage note" (transcription backlog, GitHub donate link) is
        # LFUCG-specific data — only shown on LFUCG unless a TOML override sets it.
        "show_coverage_note": cfg.slug == "lfucg",
        "source": {
            "kind": source_kind,
            "platform": platform,
            "has_transcript": source_kind == "video",
        },
        "video": {
            # Granicus cities embed the Granicus player; everyone else defaults
            # to "none" until a TOML [frontend.video] override (e.g. youtube).
            "provider": "granicus" if st == "granicus" else "none",
            "granicus_base_url": granicus_base,
            "granicus_view_id": cfg.default_view_id,
        },
        "chat": {
            "title": f"Chat{short}",
            "description": (
                f"Ask questions about {cfg.name} council meetings, votes, "
                "budgets, and more."
            ),
            "placeholder": f"Ask a question about {cfg.name} meetings...",
            # Starter chips on the /chat empty state. Generic derivations any
            # county's archive can answer; jurisdictions with richer coverage
            # override with curated questions (LFUCG below, or TOML
            # [frontend.chat] suggested_questions).
            "suggested_questions": [
                "What was decided at the most recent meeting?",
                "What zoning or development items have been discussed recently?",
                "What budget items were approved this year?",
                "Who voted against something recently?",
            ],
        },
        # The feeds.lexingtonky.news cross-link ("Read the article on …") is an
        # LFUCG-only integration. Off (and blank, to avoid leaking the LFUCG
        # host) for every other jurisdiction unless a [frontend.feeds] override
        # turns it on.
        "feeds": (
            {
                "enabled": True,
                "base_url": "https://feeds.lexingtonky.news",
                "source_id": "lfucg-meeting-archive",
                "publication_name": "Lexington Times",
            }
            if cfg.slug == "lfucg"
            else {"enabled": False, "base_url": "", "source_id": "", "publication_name": ""}
        ),
    }
    # LFUCG's historical SPA copy that the generic derivation can't reproduce
    # (the full operator bio, the feeds author link). Pinned here so the LFUCG
    # site.json stays byte-identical; other jurisdictions never see it.
    if cfg.slug == "lfucg":
        derived["operator_bio"] = "Operated by Paul Oliva as a civic-tech side project."
        derived["operator_author_url"] = "https://lexingtonky.news/author/paulmoliva/"
        derived["chat"]["title"] = "ChatLFUCG"
        derived["chat"]["description"] = (
            "Ask questions about Lexington city council meetings, votes, "
            "budgets, and more."
        )
        derived["chat"]["placeholder"] = "Ask a question about Lexington city meetings..."
        # Curated, RAG-verified showcase questions: each reliably produces a
        # cited, specific answer (named roll call, multi-year STR timeline,
        # ARPA spending breakdown, USB debate with named speakers).
        derived["chat"]["suggested_questions"] = [
            "How did each council member vote on the Government Center lease-to-own ordinance in December 2025?",
            "What has the council done about short-term rental regulations?",
            "How has the council spent the ARPA pandemic relief money?",
            "What has the council discussed about expanding the urban service boundary?",
        ]

    return _deep_merge(derived, cfg.frontend_overrides)
