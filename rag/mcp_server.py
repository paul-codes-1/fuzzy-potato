"""Model Context Protocol server for the LFUCG meeting archive.

Exposes the existing RAG + search API as MCP tools so any MCP-aware
client (Claude Desktop, Cursor, NotebookLM, custom agents) can query
the archive via the standard MCP protocol instead of HTTP/JSON.

Mounted at `/api/mcp` (and aliased at `/mcp` for direct App Runner
access) on the same FastAPI app that serves /api/ask, /api/search,
etc. CloudFront only routes `/api/*` to App Runner, so the public
URL is `https://meetings.lexingtonky.news/api/mcp`. Stateless
transport — every POST gets a fresh server instance, no session
state. CORS-open by design (matches the philosophy of the rest of
the public API).

Tools exposed:
- ask_meetings        — natural-language Q&A (RAG-synthesized answer + citations)
- search_meetings     — full-text BM25 search with filters
- find_related_clips  — "more like this" by embedding similarity
- get_meeting_clip    — fetch one meeting's title/date/body/summary + Granicus URL
- list_recent_meetings — browse the newest N meetings

The tool *implementations* are module-level functions. `build_mcp_server`
just registers them. This separation lets tests call tools directly
without spinning up the HTTP transport.
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Optional

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from clients import get_openai
from config import get_config
from rag.ingest import get_chroma_collection
from rag.query import ask, clip_citation_url, load_clip_metadata
from rag.rate_limit import check as rate_check
from rag.related import related as related_clips
from rag.search import search as search_clips
from rag.telemetry import log_query_event

logger = logging.getLogger(__name__)

_CFG = get_config()

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")
# Prefer the configured site_url; keep LFUCG_SITE_URL env as a back-compat
# override (the historical knob).
SITE_URL = (os.environ.get("LFUCG_SITE_URL") or _CFG.site_url).rstrip("/")

# Provenance + citation wording differs for document-driven jurisdictions
# (e.g. Paris on CivicClerk — no Granicus, no audio/Whisper) vs video ones
# (LFUCG on Granicus). LFUCG keeps its exact historical wording.
if _CFG.source_type == "civicclerk":
    _PROVENANCE = (
        "Every meeting record is built from the official CivicClerk agenda & minutes "
        "documents, summarized via GPT-4o + Claude Sonnet, and indexed for both keyword "
        "search (BM25) and semantic search (RAG)."
    )
    _GET_CLIP_LINE = (
        "  • get_meeting_clip — fetch full metadata + summary + source document URL for "
        "one clip.\n"
    )
    _CITE_LINE = (
        "Every clip links back to its official meeting record (agenda & minutes, plus a "
        "meeting-video link where available). Always cite the meeting URL when quoting."
    )
else:
    _PROVENANCE = (
        "Every clip is downloaded from Granicus, transcribed via Whisper, summarized via "
        "GPT-4o + Claude Sonnet, and indexed for both keyword search (BM25) and semantic "
        "search (RAG)."
    )
    _GET_CLIP_LINE = (
        "  • get_meeting_clip — fetch full metadata + summary + Granicus video URL for one "
        "clip.\n"
    )
    _CITE_LINE = (
        "Every clip links back to the canonical Granicus video at the exact timestamp the "
        "answer was synthesized from. Always cite the meeting URL when quoting."
    )

SERVER_INSTRUCTIONS = (
    f"Searchable archive of {_CFG.name} "
    f"council and committee meetings. {_PROVENANCE}\n\n"
    "Pick a tool by intent:\n"
    "  • ask_meetings — natural-language Q&A across the whole archive (one-shot, returns "
    "a synthesized answer + cited clips with video timestamps).\n"
    "  • search_meetings — keyword search when you want a list of matching clips with "
    "snippets, optionally filtered by meeting body, speaker, or date range.\n"
    "  • find_related_clips — given one clip_id, find similar clips by embedding "
    "centroid (useful for expanding a single hit into a thread).\n"
    f"{_GET_CLIP_LINE}"
    "  • list_recent_meetings — browse the newest meetings (optionally filtered by body).\n\n"
    f"{_CITE_LINE}"
)


# Per-process caches — same pattern as rag/server.py. Reused across MCP requests
# for the lifetime of the process; the App Runner container will warm them on
# the first call, identical to the HTTP API.
_collection = None
_clip_metadata = None


def _get_collection():
    global _collection
    if _collection is None:
        _collection = get_chroma_collection(OUTPUT_DIR)
    return _collection


def _get_clip_metadata():
    global _clip_metadata
    if _clip_metadata is None:
        _clip_metadata = load_clip_metadata(OUTPUT_DIR)
    return _clip_metadata


def _meeting_url(clip_id: int) -> str:
    return f"{SITE_URL}/meeting/{clip_id}"


def _clip_md_url(clip_id: int) -> str:
    return f"{SITE_URL}/data/clips/{clip_id}/clip.md"


def _rate_limited(tool: str, *, query: Optional[str] = None,
                  filters: Optional[dict] = None, tier: str = "cheap") -> Optional[dict]:
    """Apply tier-appropriate rate limit. Returns a structured error dict when
    the limit is hit (and emits a rate_limited telemetry event), else None."""
    decision = rate_check(tier)
    if decision.allowed:
        return None
    log_query_event(
        surface="mcp",
        endpoint=tool,
        query=query,
        filters=filters,
        status="rate_limited",
        error_type=f"rate_limit_{decision.reason}",
    )
    return {
        "error": (
            f"Rate limit exceeded — try again in {decision.retry_after_seconds} seconds."
        ),
        "retry_after_seconds": decision.retry_after_seconds,
        "reason": decision.reason,
    }


# When set (RAG_SUSPENDED=1 in the box .env), the two Chroma-backed tools
# return a maintenance notice instead of loading the vector store — lets the
# MCP connector register and the SQLite-backed tools (search_meetings,
# get_meeting_clip, list_recent_meetings) keep working while the vector
# store is offline (see RAG_CAPACITY_PLAN.md).
def _rag_suspended_notice() -> Optional[dict]:
    if os.environ.get("RAG_SUSPENDED", "").strip() in ("1", "true", "yes"):
        return {
            "error": "temporarily_offline",
            "message": (
                "Semantic Q&A is temporarily offline for a vector-store "
                "rebuild. Use search_meetings (keyword search) instead — "
                "it covers the full archive."
            ),
        }
    return None


# ---------- tool implementations ----------


def ask_meetings_impl(
    question: str,
    meeting_body: Optional[str] = None,
    date_after: Optional[str] = None,
    date_before: Optional[str] = None,
) -> dict:
    """Synthesize a cited natural-language answer from the LFUCG meeting archive.

    Use this when the user wants a direct answer rather than a list of
    meetings. Returns the answer text plus structured citations with
    Granicus video deep-links at the exact timestamp the answer was
    synthesized from. Falls back to "no coverage" when retrieval finds
    nothing relevant.

    Args:
        question: Free-text question (1-2000 chars). E.g.
          "What has the city done about short-term rentals?"
        meeting_body: Optional filter, e.g. "Council" or "Planning Commission".
          See list_recent_meetings to discover valid bodies.
        date_after: Optional inclusive YYYY-MM-DD lower bound.
        date_before: Optional inclusive YYYY-MM-DD upper bound.
    """
    suspended = _rag_suspended_notice()
    if suspended is not None:
        return suspended

    question = (question or "").strip()
    if not question:
        return {"error": "question must not be empty"}
    if len(question) > 2000:
        return {"error": "question must be under 2000 characters"}

    filters = {}
    if meeting_body:
        filters["meeting_body"] = meeting_body
    if date_after:
        filters["date_after"] = date_after
    if date_before:
        filters["date_before"] = date_before

    rl = _rate_limited("ask_meetings", query=question, filters=filters or None, tier="expensive")
    if rl is not None:
        return rl

    started = time.monotonic()
    try:
        result = ask(
            question=question,
            collection=_get_collection(),
            openai_client=get_openai(),
            clip_metadata=_get_clip_metadata(),
            filters=filters or None,
        )
    except Exception as exc:
        log_query_event(
            surface="mcp",
            endpoint="ask_meetings",
            query=question,
            filters=filters or None,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.exception("ask_meetings failed")
        return {"error": "Could not synthesize an answer; please try again later."}

    sources = result.get("sources", [])
    log_query_event(
        surface="mcp",
        endpoint="ask_meetings",
        query=question,
        filters=filters or None,
        result_count=len(sources) if isinstance(sources, list) else None,
        latency_ms=(time.monotonic() - started) * 1000,
        status="ok" if sources else "empty",
        model=result.get("model_used") if isinstance(result, dict) else None,
    )
    return {
        "question": question,
        "answer": result.get("answer", ""),
        "sources": sources,
        "filters_applied": result.get("filters_applied", {}),
        "chunks_retrieved": result.get("chunks_retrieved", 0),
    }


def search_meetings_impl(
    q: str,
    meeting_body: Optional[str] = None,
    speaker: Optional[str] = None,
    date_after: Optional[str] = None,
    date_before: Optional[str] = None,
    limit: int = 25,
) -> dict:
    """Full-text BM25 search across LFUCG meeting transcripts, agendas, minutes, and facts.

    Returns ranked clips with snippets containing <mark>...</mark> highlights
    on matched terms. Pass the resulting `clip_id` to get_meeting_clip for the
    full body or to find_related_clips for similar meetings.

    Wrap `q` in double quotes (e.g. `"\\"comprehensive plan\\""`) to require an
    exact phrase match instead of the default bag-of-words AND.

    Args:
        q: Free-text query (1-200 chars).
        meeting_body: Optional exact-match filter (e.g. "Council").
        speaker: Optional exact-match filter on a speaker name (e.g. "Mayor Gorton").
        date_after: Optional inclusive YYYY-MM-DD.
        date_before: Optional inclusive YYYY-MM-DD.
        limit: Max results, 1-100, default 25.
    """
    q = (q or "").strip()
    if not q:
        return {"error": "q must not be empty"}
    if len(q) > 200:
        return {"error": "q must be under 200 characters"}
    limit = max(1, min(int(limit or 25), 100))

    filters = {
        k: v
        for k, v in {
            "meeting_body": meeting_body,
            "speaker": speaker,
            "date_after": date_after,
            "date_before": date_before,
            "limit": limit,
        }.items()
        if v
    }

    rl = _rate_limited("search_meetings", query=q, filters=filters or None, tier="cheap")
    if rl is not None:
        return rl

    started = time.monotonic()
    try:
        results = search_clips(
            query=q,
            output_dir=OUTPUT_DIR,
            meeting_body=meeting_body,
            speaker=speaker,
            date_after=date_after,
            date_before=date_before,
            limit=limit,
        )
    except Exception as exc:
        log_query_event(
            surface="mcp",
            endpoint="search_meetings",
            query=q,
            filters=filters or None,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.exception("search_meetings failed")
        return {"error": "Search failed; please try again later."}

    # Decorate with canonical URLs so the agent doesn't have to know the
    # site-URL convention. Snippet HTML (with <mark>) is preserved verbatim.
    for r in results:
        cid = r.get("clip_id")
        if cid is not None:
            r["url"] = _meeting_url(cid)
            r["markdown_url"] = _clip_md_url(cid)

    log_query_event(
        surface="mcp",
        endpoint="search_meetings",
        query=q,
        filters=filters or None,
        result_count=len(results),
        latency_ms=(time.monotonic() - started) * 1000,
        status="ok" if results else "empty",
    )
    return {"q": q, "count": len(results), "results": results}


def find_related_clips_impl(clip_id: int, limit: int = 5) -> dict:
    """Find LFUCG meeting clips similar to a given clip by embedding centroid.

    Useful for expanding a single hit (from search_meetings or ask_meetings
    citations) into a thread of related coverage. Similarity is cosine
    similarity over the source clip's summary embeddings (1.0 = identical
    content, 0.0 = unrelated).

    Args:
        clip_id: The Granicus clip ID to find neighbors for.
        limit: Max results, 1-20, default 5.
    """
    suspended = _rag_suspended_notice()
    if suspended is not None:
        return suspended

    limit = max(1, min(int(limit or 5), 20))
    filters = {"clip_id": int(clip_id), "limit": limit}

    rl = _rate_limited("find_related_clips", filters=filters, tier="cheap")
    if rl is not None:
        return rl

    started = time.monotonic()
    try:
        results = related_clips(
            int(clip_id),
            _get_collection(),
            _get_clip_metadata(),
            limit=limit,
        )
    except Exception as exc:
        log_query_event(
            surface="mcp",
            endpoint="find_related_clips",
            filters=filters,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error",
            error_type="internal",
        )
        logger.exception("find_related_clips failed")
        return {"error": "Could not find related clips; please try again later."}

    for r in results:
        cid = r.get("clip_id")
        if cid is not None:
            r["url"] = _meeting_url(cid)
            r["markdown_url"] = _clip_md_url(cid)

    log_query_event(
        surface="mcp",
        endpoint="find_related_clips",
        filters=filters,
        result_count=len(results),
        latency_ms=(time.monotonic() - started) * 1000,
        status="ok" if results else "empty",
    )
    return {"clip_id": int(clip_id), "count": len(results), "results": results}


def get_meeting_clip_impl(clip_id: int) -> dict:
    """Fetch metadata, summary, and Granicus video URL for one LFUCG meeting clip.

    Returns enough information to render a single-meeting page or follow-up
    question: title, date, meeting body, speakers, topics, transcript word
    count, narrative summary text, the canonical Granicus video URL (no
    timestamp), and the Markdown alternate URL (which contains the full
    transcript + agenda + minutes).

    Args:
        clip_id: The Granicus clip ID, e.g. returned from search_meetings.
    """
    cid = int(clip_id)
    filters = {"clip_id": cid}

    rl = _rate_limited("get_meeting_clip", filters=filters, tier="cheap")
    if rl is not None:
        return rl

    started = time.monotonic()
    meta_dict = _get_clip_metadata()
    # load_clip_metadata returns dict keyed by str(clip_id).
    entry = meta_dict.get(str(cid)) or meta_dict.get(cid)
    if not entry:
        log_query_event(
            surface="mcp",
            endpoint="get_meeting_clip",
            filters=filters,
            latency_ms=(time.monotonic() - started) * 1000,
            status="empty",
            error_type="not_found",
        )
        return {"error": f"No clip found for id {cid}"}

    # Try to surface the narrative summary if it exists on disk. Best-effort
    # — if the file has been pruned or hasn't been generated yet we just
    # omit it and return the metadata.
    summary_text: Optional[str] = None
    try:
        summary_path = os.path.join(OUTPUT_DIR, "clips", str(cid), "summary.txt")
        if os.path.isfile(summary_path):
            with open(summary_path, "r", encoding="utf-8") as fh:
                summary_text = fh.read()
    except Exception:
        logger.warning("could not read summary for clip %s", cid, exc_info=True)

    log_query_event(
        surface="mcp",
        endpoint="get_meeting_clip",
        filters=filters,
        result_count=1,
        latency_ms=(time.monotonic() - started) * 1000,
        status="ok",
    )
    return {
        "clip_id": cid,
        "title": entry.get("title"),
        "date": entry.get("date"),
        "meeting_body": entry.get("meeting_body"),
        "speakers": entry.get("speakers", []),
        "topics": entry.get("topics", []),
        "transcript_words": entry.get("transcript_words"),
        "transcript_source": entry.get("transcript_source"),
        "summary": summary_text,
        # Honor a non-Granicus canonical_url (Paris/CivicClerk) from the clip's
        # metadata; Granicus + old LFUCG clips fall back to the Granicus
        # deep-link (byte-identical).
        "granicus_url": clip_citation_url(cid, entry.get("url", "")),
        "url": _meeting_url(cid),
        "markdown_url": _clip_md_url(cid),
    }


def list_recent_meetings_impl(limit: int = 20, meeting_body: Optional[str] = None) -> dict:
    """List the most recent LFUCG meetings, newest first.

    Useful for browsing rather than searching, or for grounding an agent's
    reply in a "here's what happened recently" pass before calling other
    tools. Returns lightweight metadata only — call get_meeting_clip for
    the full record.

    Args:
        limit: Max items, 1-100, default 20.
        meeting_body: Optional filter (exact match), e.g. "Council".
    """
    limit = max(1, min(int(limit or 20), 100))
    filters = {"limit": limit}
    if meeting_body:
        filters["meeting_body"] = meeting_body

    rl = _rate_limited("list_recent_meetings", filters=filters, tier="cheap")
    if rl is not None:
        return rl

    started = time.monotonic()
    meta_dict = _get_clip_metadata()
    rows = []
    for raw_cid, entry in meta_dict.items():
        try:
            cid = int(raw_cid)
        except (ValueError, TypeError):
            continue
        if meeting_body and entry.get("meeting_body") != meeting_body:
            continue
        rows.append(
            {
                "clip_id": cid,
                "date": entry.get("date") or "",
                "meeting_body": entry.get("meeting_body"),
                "title": entry.get("title"),
                "topics": entry.get("topics", []),
                "url": _meeting_url(cid),
                "markdown_url": _clip_md_url(cid),
            }
        )
    # ISO YYYY-MM-DD sorts lexicographically; clip_id breaks date-ties stably.
    rows.sort(key=lambda r: (r["date"], r["clip_id"]), reverse=True)
    rows = rows[:limit]
    log_query_event(
        surface="mcp",
        endpoint="list_recent_meetings",
        filters=filters,
        result_count=len(rows),
        latency_ms=(time.monotonic() - started) * 1000,
        status="ok" if rows else "empty",
    )
    return {
        "count": len(rows),
        "meeting_body": meeting_body,
        "results": rows,
    }


def _as_async_tool(impl):
    """Wrap a sync tool impl so it runs in a worker thread.

    The MCP SDK calls non-async tool functions inline on the event loop, so
    a synchronous ask_meetings (rewrite → embeddings → Chroma → gpt-4o,
    5-15s) would freeze every other request on the server — including
    /health, which can trip health-check restarts. The wrapper preserves
    the impl's signature and docstring so FastMCP derives the same tool
    schema; tests keep calling the sync ``*_impl`` functions directly.
    """

    @functools.wraps(impl)
    async def _tool(*args, **kwargs):
        return await anyio.to_thread.run_sync(
            functools.partial(impl, *args, **kwargs)
        )

    return _tool


def build_mcp_server() -> FastMCP:
    """Build the FastMCP server and register all tools."""
    mcp = FastMCP(
        # Slug-derived so each jurisdiction's MCP serverInfo.name is its own
        # (LFUCG stays "lfucg-meeting-archive" byte-identical).
        name=f"{_CFG.slug}-meeting-archive",
        instructions=SERVER_INSTRUCTIONS,
        stateless_http=True,
        # FastMCP's internal route defaults to "/mcp"; we mount the app
        # itself at "/mcp" in rag/server.py, so set inner to "/" to avoid
        # the doubled "/mcp/mcp" URL that the default would produce.
        streamable_http_path="/",
        # DNS-rebinding protection rejects unknown Host headers — useful
        # for localhost-bound dev servers. This service is a public,
        # CORS-open API behind CloudFront, so the protection adds no real
        # safety and would block legitimate clients (and the test
        # harness, which sends Host=testserver).
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    mcp.add_tool(_as_async_tool(ask_meetings_impl), name="ask_meetings")
    mcp.add_tool(_as_async_tool(search_meetings_impl), name="search_meetings")
    mcp.add_tool(_as_async_tool(find_related_clips_impl), name="find_related_clips")
    mcp.add_tool(_as_async_tool(get_meeting_clip_impl), name="get_meeting_clip")
    mcp.add_tool(_as_async_tool(list_recent_meetings_impl), name="list_recent_meetings")
    return mcp


# Module-level singleton. Built at import time so the FastAPI lifespan in
# rag/server.py can wire it without an extra factory call.
mcp_server: FastMCP = build_mcp_server()
