"""SEO + LLM-friendly site artifact generation.

Mirrors the pattern used by feeds.lexingtonky.news: a sitemap index pointing
at standard + news sitemaps, plus an llms.txt / llms-full.txt pair for the
emerging llmstxt.org convention used by Perplexity, Claude web fetch, etc.

Pure module function — no pipeline state required, just `index_entries` plus
the on-disk output dir for reading per-clip summaries. The output dir is
where `lfucg_output/clips/<id>/summary.txt` lives.
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from xml.sax.saxutils import escape

from config import get_config

# Granicus appends a part-number suffix like " (1)" to every clip title.
# Strip it for SEO so titles read naturally on Google / social cards.
_GRANICUS_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
# `## Header` lines and `[timestamp: MM:SS]` markers come from the v2
# narrative summary; remove both before pulling description text.
_TIMESTAMP_RE = re.compile(r"\[timestamp:\s*\d+:\d+\]")
_HEADER_RE = re.compile(r"^##\s.*$", flags=re.MULTILINE)


def _default_log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {message}")


def clean_title(title: Optional[str]) -> str:
    """Strip the Granicus ` (1)` part-number suffix from a clip title."""
    return _GRANICUS_SUFFIX_RE.sub("", (title or "").strip())


def format_long_date(iso_date: Optional[str]) -> str:
    """ISO `YYYY-MM-DD` → `Month Day, Year` (e.g. `April 30, 2026`).

    Falls back to the raw input on parse failure so callers don't need
    to special-case missing/malformed dates.
    """
    if not iso_date:
        return ""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def build_seo_title(title: Optional[str], iso_date: Optional[str]) -> str:
    """`{cleaned_title} - {Month Day, Year}` for sitemap/social/SERP titles.

    Example: `Urban County Council - April 30, 2026`. Drops either side
    when missing rather than emit a stray dash.
    """
    cleaned = clean_title(title)
    formatted = format_long_date(iso_date)
    if cleaned and formatted:
        return f"{cleaned} - {formatted}"
    return cleaned or formatted or f"{get_config().slug.upper()} Meeting"


def _format_revision_date(iso: Optional[str]) -> str:
    """ISO timestamp (`2026-04-30T20:34:59`) → `April 30, 2026`. Falls
    back to the date prefix on parse failure so we never emit garbage.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except ValueError:
        return iso[:10] if len(iso) >= 10 else iso


def build_clip_markdown(
    entry: Dict[str, Any],
    output_dir: Path,
    site_url: str,
) -> Optional[str]:
    """Render a per-clip Markdown alternate combining summary + facts + transcript.

    Returns None if the clip directory is missing entirely; otherwise always
    returns at least a frontmatter block — the AI disclosure must appear on
    every Markdown page, even when the source files are sparse.

    Format mirrors what an LLM agent would want to ingest:
      1. SEO title as H1
      2. Metadata block (source, date, body, last-revised, permalink)
      3. AI disclosure callout (mirrors the on-page <aside>)
      4. Narrative summary (from summary.txt) — kept verbatim
      5. Decisions list (from extracted_facts.json motions_and_votes)
      6. Full transcript (from transcript.txt) — last so length-capped
         agents that read top-to-bottom still get the structured data.
    """
    cfg = get_config()
    clip_id = entry.get("clip_id")
    if clip_id is None:
        return None
    clip_dir = output_dir / "clips" / str(clip_id)
    if not clip_dir.exists():
        return None

    title = entry.get("title")
    iso_date = entry.get("date")
    body = entry.get("meeting_body") or ""
    seo_title = build_seo_title(title, iso_date)
    long_date = format_long_date(iso_date)
    permalink = f"{site_url}/meeting/{clip_id}"

    # Pull metadata for source URL + revision timestamp + transcript word count.
    metadata: Dict[str, Any] = {}
    metadata_path = clip_dir / "metadata.json"
    if metadata_path.exists():
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            metadata = {}
    granicus_url = metadata.get("url") or ""
    revised_iso = metadata.get("summary_updated_at") or metadata.get("processed_at") or iso_date
    revised_human = _format_revision_date(revised_iso)
    word_count = metadata.get("transcript_words") or 0
    transcript_source = metadata.get("transcript_source") or "whisper-1"
    speakers = metadata.get("speakers") or []

    lines: List[str] = [
        (
            "<!-- AI/LLM agents: full guide to this archive — MCP servers, APIs, "
            f"citation rules, and how to verify us → {site_url}/skill.md -->"
        ),
        f"# {seo_title}",
        "",
    ]
    meta_bits = []
    if body:
        meta_bits.append(body)
    if long_date:
        meta_bits.append(long_date)
    if meta_bits:
        lines.append(f"> Auto-transcribed civic record · {' · '.join(meta_bits)}")
        lines.append("")

    lines.append(f"- **Permalink**: {permalink}")
    if granicus_url:
        lines.append(f"- **Source video**: {granicus_url}")
    if iso_date:
        lines.append(f"- **Date**: {iso_date}")
    if body:
        lines.append(f"- **Body**: {body}")
    if revised_human:
        lines.append(f"- **Last revised**: {revised_human}")
    if word_count:
        lines.append(f"- **Length**: {word_count:,} words")
    if speakers:
        lines.append(f"- **Speakers**: {', '.join(speakers)}")
    lines.append("")

    if transcript_source == "granicus_vtt":
        disclosure = (
            "> ⚠️ **Auto-generated content.** The transcript on this page is the "
            "Granicus stenographer's live closed-captioning track, captured at the "
            "time of broadcast (typos and broken sentences common). Speaker labels "
            "come from the same track. Structured facts were extracted with GPT-4o; "
            "the narrative summary was written by Anthropic Claude Sonnet. Verbatim "
            "wording and speaker attribution may contain errors. "
            f"See [methodology]({site_url}/about/methodology) or "
            f"[report a correction](mailto:{cfg.editor_email})."
        )
    elif transcript_source == "whisper-1+vtt-speakers":
        disclosure = (
            "> ⚠️ **Auto-generated content.** Audio from the official Granicus "
            "video was auto-transcribed by OpenAI Whisper-1, with speaker labels "
            "folded in from Granicus closed-captioning. Structured facts were "
            "extracted with GPT-4o; the narrative summary was written by "
            "Anthropic Claude Sonnet. Speaker labels and verbatim wording may "
            f"contain errors. See [methodology]({site_url}/about/methodology) "
            f"or [report a correction](mailto:{cfg.editor_email})."
        )
    elif transcript_source in ("civicclerk_minutes", "civicclerk_agenda"):
        # Document-driven record (PR-6: CivicClerk / Paris). No verbatim
        # transcript exists — the page is built from the official agenda +
        # minutes documents, which are the authoritative record of what the
        # meeting did (votes, motions, appropriations).
        _doc_phrase = (
            "official CivicClerk agenda and minutes"
            if transcript_source == "civicclerk_minutes"
            else "official CivicClerk agenda (minutes not yet published)"
        )
        disclosure = (
            "> ⚠️ **Auto-generated content.** This record is built from the "
            f"{_doc_phrase} — there is no verbatim transcript. Structured facts "
            "were extracted with GPT-4o; the narrative summary was written by "
            "Anthropic Claude Sonnet. "
            f"See [methodology]({site_url}/about/methodology) "
            f"or [report a correction](mailto:{cfg.editor_email})."
        )
    else:
        disclosure = (
            "> ⚠️ **Auto-generated content.** Audio from the official Granicus "
            "video was auto-transcribed by OpenAI Whisper-1. Structured facts "
            "were extracted with GPT-4o; the narrative summary was written by "
            "Anthropic Claude Sonnet. Speaker labels and verbatim wording may "
            f"contain errors. See [methodology]({site_url}/about/methodology) "
            f"or [report a correction](mailto:{cfg.editor_email})."
        )

    lines += [disclosure, "", "---", ""]

    # Narrative summary — verbatim, headers and all.
    summary_path = clip_dir / "summary.txt"
    if summary_path.exists():
        try:
            summary_text = summary_path.read_text(encoding="utf-8").strip()
        except OSError:
            summary_text = ""
        if summary_text:
            lines.append(summary_text)
            lines.append("")
            lines.append("---")
            lines.append("")

    # Structured decisions — extracted_facts is canonical for vote outcomes.
    facts_path = clip_dir / "extracted_facts.json"
    if facts_path.exists():
        try:
            with open(facts_path, "r", encoding="utf-8") as f:
                facts = json.load(f)
        except Exception:
            facts = {}
        votes = facts.get("motions_and_votes") or []
        if votes:
            lines.append("## Decisions")
            lines.append("")
            for v in votes:
                ident = v.get("identifier") or "Motion"
                desc = v.get("description") or ""
                outcome = v.get("outcome") or "unknown"
                tally = ""
                if v.get("ayes") is not None:
                    tally = f" ({v.get('ayes', 0)}-{v.get('nays', 0)})"
                line = f"- **{ident}** — {outcome}{tally}"
                if desc:
                    line += f": {desc}"
                lines.append(line)
            lines.append("")
            lines.append("---")
            lines.append("")

    # Full transcript — last because it's the longest section.
    transcript_file = (
        metadata.get("files", {}).get("transcript")
        if isinstance(metadata.get("files"), dict)
        else None
    )
    if transcript_file:
        transcript_path = clip_dir / transcript_file
        if transcript_path.exists():
            try:
                transcript_text = transcript_path.read_text(encoding="utf-8").strip()
            except OSError:
                transcript_text = ""
            if transcript_text:
                lines.append("## Full transcript")
                lines.append("")
                lines.append(transcript_text)
                lines.append("")

    return "\n".join(lines)


def build_seo_description(
    summary_path: Optional[Path] = None,
    *,
    fallback: str = "",
    max_chars: int = 200,
) -> str:
    """First substantive paragraph of `summary.txt`, cleaned + truncated.

    Strips `## ` section headers and `[timestamp: MM:SS]` markers so the
    description reads as natural prose. Truncated on a word boundary at
    `max_chars` to stay under Twitter's 200-char og:description cap while
    leaving Google's ~160-char snippet some headroom.
    """
    text = ""
    if summary_path and summary_path.exists():
        try:
            text = summary_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
    if not text:
        text = fallback or ""

    text = _HEADER_RE.sub("", text)
    text = _TIMESTAMP_RE.sub("", text)
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        cleaned = " ".join(para.split())
        if len(cleaned) >= 50:
            if len(cleaned) > max_chars:
                truncated = cleaned[: max_chars - 1].rsplit(" ", 1)[0]
                cleaned = truncated.rstrip(",.;:") + "…"
            return cleaned
    return ""


def _build_skill_md(site_url: str) -> str:
    """Return the contents of /skill.md — the canonical agent/LLM guide.

    This is the ecosystem-wide guide served verbatim from every Lexington
    Times domain (feeds / meetings / editorial): every MCP server, every
    machine-readable API, citation rules, and the trust manifest. The source
    of truth is the tracked template at ``frontend/skill.template.md`` so the
    three repo copies (feeds/docs/skill.md, lexingtonky.news/web-root/skill.md,
    and this one) can be kept byte-identical. ``site_url`` is accepted for
    signature stability; the doc itself uses absolute cross-domain URLs.
    """
    template = Path(__file__).resolve().parent / "frontend" / "skill.template.md"
    try:
        return template.read_text(encoding="utf-8")
    except OSError:
        # Fallback so the surface never 404s if the template is absent from a
        # build context — a minimal pointer to the index + both MCP servers.
        return (
            "# The Lexington Times — Agent & LLM Guide\n\n"
            f"See {site_url}/llms.txt for the agent index. MCP servers: "
            "https://feeds.lexingtonky.news/mcp and "
            "https://meetings.lexingtonky.news/api/mcp\n"
        )


def generate_seo_artifacts(
    index_entries: List[Dict[str, Any]],
    output_dir: Path,
    public_dir: Optional[Path] = None,
    site_url: Optional[str] = None,
    log: Callable[..., None] = _default_log,
) -> None:
    """Write sitemap.xml, sitemap_index.xml, news-sitemap.xml, llms.txt, skill.md, llms-full.txt.

    Args:
        index_entries: Same shape as `index.json`'s `clips` array — each entry
            must have `clip_id`, optional `date`, `meeting_body`, `title`,
            `agenda_preview`, `transcript_preview`.
        output_dir: Directory containing `clips/<id>/` subdirs. Used to read
            per-clip `summary.txt` and `metadata.json` for richer llms-full.txt.
        public_dir: Where to write the artifacts. Defaults to
            `<repo>/frontend/public` (alongside this file's package).
        site_url: Overrides `LFUCG_SITE_URL` env var. Used as the URL prefix in
            every generated link.
        log: Logger callback `(message, level=...)` — defaults to stdout.
    """
    cfg = get_config()
    site_url = (site_url or os.environ.get("LFUCG_SITE_URL") or cfg.site_url).rstrip("/")
    if public_dir is None:
        public_dir = Path(__file__).parent / "frontend" / "public"
    if not public_dir.exists():
        log(f"frontend/public not found at {public_dir}, skipping SEO artifacts", "WARNING")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat()

    valid_clips = [e for e in index_entries if e.get("clip_id") is not None]

    # ---- sitemap.xml — homepage, static routes, and every meeting URL.
    static_routes = [
        ("/", "daily", "1.0"),
        ("/ask", "weekly", "0.7"),
        ("/chat", "weekly", "0.6"),
        ("/about", "monthly", "0.6"),
        ("/about/methodology", "monthly", "0.5"),
        ("/corrections", "monthly", "0.4"),
    ]
    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, changefreq, priority in static_routes:
        sitemap_lines += [
            "  <url>",
            f"    <loc>{site_url}{path}</loc>",
            f"    <lastmod>{today}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
        ]
    for entry in valid_clips:
        clip_id = entry["clip_id"]
        lastmod = entry.get("date") or today
        sitemap_lines += [
            "  <url>",
            f"    <loc>{site_url}/meeting/{escape(str(clip_id))}</loc>",
            f"    <lastmod>{escape(str(lastmod))}</lastmod>",
            "    <changefreq>monthly</changefreq>",
            "    <priority>0.8</priority>",
            "  </url>",
        ]
    sitemap_lines.append("</urlset>")
    (public_dir / "sitemap.xml").write_text("\n".join(sitemap_lines) + "\n", encoding="utf-8")

    # ---- sitemap_index.xml — discovery entry-point referenced by robots.txt.
    index_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <sitemap>",
        f"    <loc>{site_url}/sitemap.xml</loc>",
        f"    <lastmod>{today}</lastmod>",
        "  </sitemap>",
        "  <sitemap>",
        f"    <loc>{site_url}/news-sitemap.xml</loc>",
        f"    <lastmod>{today}</lastmod>",
        "  </sitemap>",
        "</sitemapindex>",
    ]
    (public_dir / "sitemap_index.xml").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    # ---- news-sitemap.xml — last 48h of newly-PUBLISHED meeting articles,
    # in Google News sitemap format. Cutoff and publication_date both
    # track processed_at (when our summary went live), not the meeting
    # date — Google News expects publication_date to mark when the
    # article was published, and clips processed late (meeting date 3-7
    # days old) were getting silently excluded by downstream aggregators
    # enforcing the 2-day Google News freshness rule.
    cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")

    def _pub_date(entry: dict) -> str:
        # processed_at is ISO datetime; slice to date. Fall back to the
        # meeting date if processed_at is missing (older clips).
        proc = (entry.get("processed_at") or "")[:10]
        return proc or entry.get("date") or today

    news_clips = [e for e in valid_clips if _pub_date(e) >= cutoff]
    news_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">',
    ]
    for entry in news_clips:
        clip_id = entry["clip_id"]
        pub_date = _pub_date(entry)
        seo_title = build_seo_title(entry.get("title"), entry.get("date"))
        news_lines += [
            "  <url>",
            f"    <loc>{site_url}/meeting/{escape(str(clip_id))}</loc>",
            "    <news:news>",
            "      <news:publication>",
            f"        <news:name>{escape(cfg.publication_name)}</news:name>",
            "        <news:language>en</news:language>",
            "      </news:publication>",
            f"      <news:publication_date>{escape(str(pub_date))}</news:publication_date>",
            f"      <news:title>{escape(seo_title)}</news:title>",
            "    </news:news>",
            "  </url>",
        ]
    news_lines.append("</urlset>")
    (public_dir / "news-sitemap.xml").write_text("\n".join(news_lines) + "\n", encoding="utf-8")

    # ---- llms.txt — site-level index in markdown for AI agents (llmstxt.org).
    recent = valid_clips[:50]
    bodies = sorted({e.get("meeting_body") for e in valid_clips if e.get("meeting_body")})
    llms_lines = [
        f"# {cfg.publication_name}",
        "",
        f"> Searchable archive of {cfg.name} council and committee meetings — every clip downloaded, transcribed via Whisper, summarized via GPT-4o + Claude Sonnet, and indexed for semantic search. {len(valid_clips)} meetings hosted at {site_url}.",
        "",
        "## About",
        "",
        f"- Site: [{site_url}]({site_url})",
        f"- About page (operator + mission): [{site_url}/about]({site_url}/about)",
        f"- Methodology (audio source, model versions, accuracy caveats): [{site_url}/about/methodology]({site_url}/about/methodology)",
        f"- Corrections workflow: [{site_url}/corrections]({site_url}/corrections)",
        f"- Search index (JSON): [{site_url}/data/index.json]({site_url}/data/index.json)",
        f"- RAG Q&A (HTML): [{site_url}/ask]({site_url}/ask) — natural-language Q&A over the archive",
        f"- **Model Context Protocol (MCP) endpoint**: `{site_url}/api/mcp` — "
        f"native MCP server exposing the archive as five tools (`ask_meetings`, "
        f"`search_meetings`, `find_related_clips`, `get_meeting_clip`, "
        f"`list_recent_meetings`). For Claude Desktop, Cursor, NotebookLM, "
        f"or any MCP-aware client. Stateless POST-only HTTP transport, CORS-open, "
        f"no auth. See the *MCP server* section below.",
        f"- **Public API for agents** (CORS-open, no auth): see the *API for agents* section below — "
        f"`POST {site_url}/api/search` for full-text search, `POST {site_url}/api/ask` for RAG Q&A, "
        f"plus `/api/suggest`, `/api/facets`, `/api/related/{{clip_id}}`, `/api/chat`.",
        f"- **Agent/LLM guide (start here)**: [{site_url}/skill.md]({site_url}/skill.md) — "
        f"the canonical ecosystem guide: every Lexington Times MCP server + API, how to chain "
        f"`/api/search` + `clip.md`, when to use `/api/ask`, citation conventions, worked "
        f"examples, and how to verify us. Read this before research-style queries.",
        f"- **Trust manifest (JSON)**: [{site_url}/.well-known/llm-trust.json]({site_url}/.well-known/llm-trust.json) — "
        f"who runs this, sourcing, AI disclosure, and verification, for a programmatic trust check.",
        f"- Sitemap: [{site_url}/sitemap_index.xml]({site_url}/sitemap_index.xml)",
        f"- Full meeting dump: [{site_url}/llms-full.txt]({site_url}/llms-full.txt)",
        "",
        "## Per-meeting Markdown alternate",
        "",
        f"Every meeting has a Markdown alternate at `{site_url}/data/clips/<clip_id>/clip.md` "
        "served with `Content-Type: text/markdown` and open CORS. Combines the SEO-formatted "
        "title, source-video link, AI-generation disclosure, narrative summary, decisions list, "
        "and full transcript in one fetch. Preferable to scraping the HTML for agent ingestion.",
        "",
        "## MCP server",
        "",
        f"A native [Model Context Protocol](https://modelcontextprotocol.io) server is "
        f"mounted at `{site_url}/api/mcp` (stateless streamable-HTTP transport, CORS-open, "
        f"no auth). MCP-aware clients — Claude Desktop, Cursor, NotebookLM, or anything "
        f"built on an MCP SDK — can connect directly and the archive shows up as five "
        f"tools with typed schemas:",
        "",
        "- `ask_meetings(question, meeting_body?, date_after?, date_before?)` — synthesized RAG answer + cited Granicus video timestamps",
        "- `search_meetings(q, meeting_body?, speaker?, date_after?, date_before?, limit?)` — BM25 keyword search",
        "- `find_related_clips(clip_id, limit?)` — embedding-similarity neighbors for one clip",
        "- `get_meeting_clip(clip_id)` — full metadata + summary + Granicus URL for one meeting",
        "- `list_recent_meetings(limit?, meeting_body?)` — browse the newest meetings",
        "",
        "If you're integrating an AI agent and your client supports MCP, prefer this over the "
        "HTTP API below — the protocol is purpose-built for tool use and gives you typed "
        "schemas, proper citations, and standard error handling for free.",
        "",
        "## API for agents",
        "",
        "Direct HTTP/JSON endpoints for clients that don't speak MCP, or for shell-script "
        "integration. All endpoints are public, CORS-open "
        "(`Access-Control-Allow-Origin: *`), and return JSON. Use them instead of scraping "
        "search-result pages — they're faster, more accurate, and rate-limit-friendly. "
        "Recommended workflow: hit `/api/search` or `/api/ask` to find clips, then fetch "
        f"the per-clip Markdown alternate (`{site_url}/data/clips/<clip_id>/clip.md`) for "
        f"the full content.",
        "",
        "### POST /api/search — full-text BM25 search",
        "",
        "Body (JSON):",
        "",
        "```json",
        "{",
        '  "q": "short-term rentals",',
        '  "meeting_body": "Council",       // optional',
        '  "speaker": "Mayor Gorton",        // optional, exact match',
        '  "date_after": "2025-01-01",       // optional, inclusive YYYY-MM-DD',
        '  "date_before": "2025-12-31",      // optional, inclusive YYYY-MM-DD',
        '  "limit": 25                       // optional, 1-100, default 50',
        "}",
        "```",
        "",
        "Returns `{results: [{clip_id, date, meeting_body, title, speakers, "
        "snippet (HTML, with <mark> on matches), score, ...}], count}`. Title matches outrank "
        "facts > speakers > agenda > minutes > transcript. Use the `clip_id` to construct "
        f"`{site_url}/meeting/<clip_id>` (HTML) or `{site_url}/data/clips/<clip_id>/clip.md` "
        "(Markdown alternate).",
        "",
        "**Quoted-phrase mode.** Wrap the entire `q` value in double quotes "
        '(e.g. `{"q": "\\"comprehensive plan\\""}`) to require a contiguous '
        "phrase match instead of the default bag-of-words AND. Use this for "
        "ordinance names, official program titles, and other multi-word "
        "terms-of-art where exact wording matters more than recall.",
        "",
        f"Example: `curl -X POST {site_url}/api/search -H 'Content-Type: application/json' "
        '-d \'{"q":"vacancy tax","meeting_body":"Council","limit":5}\'`',
        "",
        "### POST /api/ask — natural-language Q&A (RAG)",
        "",
        "Synthesizes an answer over the entire archive using vector retrieval + GPT-4o. "
        "One-shot; for multi-turn use `/api/chat`.",
        "",
        "```json",
        "{",
        '  "question": "What has the city done about short-term rentals?",',
        '  "meeting_body": "Council",       // optional filter',
        '  "date_after": "2024-01-01",       // optional',
        '  "date_before": "2026-12-31"       // optional',
        "}",
        "```",
        "",
        "Returns `{question, answer, citations: [{clip_id, title, date, meeting_body, "
        "url, timestamp_seconds, snippet}], retrieved}`. Citations point at the exact "
        "video timestamps the answer was synthesized from.",
        "",
        "### GET /api/suggest?q=&limit=10 — autocomplete",
        "",
        "Prefix match over titles, meeting bodies, topics, speaker names. Returns "
        "`{results: [{term, kind, weight}]}` where `kind` is one of `title|body|topic|speaker`.",
        "",
        "### GET /api/facets — filter values",
        "",
        "Returns `{bodies: [...], speakers: [{name, count}], date_min, date_max}`. Use "
        "the `bodies` and `speakers` lists to drive the optional filters on `/api/search` "
        "and `/api/ask` — passing values not in this set will silently match nothing.",
        "",
        "### GET /api/related/{clip_id}?limit=5 — similar clips",
        "",
        "Returns `{results: [{clip_id, title, date, meeting_body, similarity}]}` ranked by "
        "embedding-centroid cosine similarity to the source clip's summary. Useful for "
        "expanding a single hit into a thread.",
        "",
        "### POST /api/chat — multi-turn RAG",
        "",
        "Same retrieval + synthesis as `/api/ask`, but takes a conversation history "
        "(`messages: [{role: 'user'|'assistant', content}, ...]`). The last message must "
        "be from the user. Optional `model_provider: 'openai' | 'anthropic'`.",
        "",
        "## Operator",
        "",
        f"Operated by {cfg.operator_name} as a civic-tech side project — see [{site_url}/about]({site_url}/about). "
        f"Editorial contact: {cfg.editor_email}.",
        "",
        "## How content is generated",
        "",
        f"Audio is pulled from the official {cfg.slug.upper()} Granicus video stream and transcribed by "
        "OpenAI Whisper-1. A two-pass summarization pipeline runs over each transcript: GPT-4o "
        "extracts structured facts (votes, motions, financial items, attendance, agenda items, "
        "public comments) into `extracted_facts.json`; Anthropic Claude Sonnet then writes a "
        "section-by-section narrative summary with `[timestamp: MM:SS]` markers for video "
        "deep-linking. Topic tags are generated by GPT-4o-mini. Agenda PDFs and official "
        "minutes are downloaded from Granicus when available; text extraction uses pdfplumber "
        "with Tesseract OCR fallback for scanned PDFs. Known accuracy limitations and the "
        f"corrections workflow are documented at [{site_url}/about/methodology]({site_url}/about/methodology). "
        "Every meeting page links back to the canonical Granicus video for verification.",
        "",
        "## Recent meetings",
        "",
    ]
    for entry in recent:
        clip_id = entry["clip_id"]
        seo_title = build_seo_title(entry.get("title"), entry.get("date"))
        llms_lines.append(f"- [{seo_title}]({site_url}/meeting/{clip_id})")
    llms_lines += [
        "",
        "## Meeting bodies covered",
        "",
    ]
    for body in bodies:
        count = sum(1 for e in valid_clips if e.get("meeting_body") == body)
        llms_lines.append(f"- {body} ({count} meetings)")
    llms_lines += [
        "",
        "## Attribution",
        "",
        "Source video, agendas, and minutes are public records published by the " + cfg.name + " on Granicus. Transcripts and summaries on this site are AI-generated for accessibility — verify against the official video and minutes for high-stakes use. When citing, please link to the canonical meeting page (e.g. `" + site_url + "/meeting/{id}`).",
        "",
    ]
    (public_dir / "llms.txt").write_text("\n".join(llms_lines), encoding="utf-8")

    # ---- skill.md — the canonical agent/LLM guide for the whole Lexington
    # Times ecosystem (every MCP server + API, citation rules, trust).
    # llms.txt documents WHAT this archive's API is; skill.md teaches an agent
    # HOW to use the whole system end-to-end and how to verify us. Its source
    # of truth is the tracked template frontend/skill.template.md (read by
    # _build_skill_md) so the copies served from feeds / meetings / editorial
    # stay byte-identical. Hand-edit the template, then regenerate.
    skill_md = _build_skill_md(site_url)
    (public_dir / "skill.md").write_text(skill_md, encoding="utf-8")

    # ---- llms-full.txt — recent meetings with summary previews for one-pull consumption.
    full_lines = [
        f"# {cfg.publication_name}",
        "",
        f"> {len(valid_clips)} {cfg.name} meetings, transcribed and summarized.",
        "",
        f"This file lists the {min(len(valid_clips), 200)} most recent meetings with summary previews. For full transcripts + structured facts, visit each meeting URL or fetch the per-clip data via {site_url}/data/clips/<clip_id>/.",
        "",
        f"Generated at {now_iso}.",
        "",
        "---",
        "",
    ]
    clips_dir = output_dir / "clips"
    for entry in valid_clips[:200]:
        clip_id = entry["clip_id"]
        clip_dir = clips_dir / str(clip_id)
        seo_title = build_seo_title(entry.get("title"), entry.get("date"))

        # Prefer the v2 narrative summary — fall back to the indexed previews if missing.
        preview = ""
        summary_path = clip_dir / "summary.txt"
        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as sf:
                    text = sf.read().strip()
                preview = text[:1200]
            except Exception:
                preview = ""
        if not preview:
            preview = (entry.get("agenda_preview") or entry.get("transcript_preview") or "").strip()

        # Pull topics from per-clip metadata (not in index.json schema).
        topics: List[str] = []
        metadata_path = clip_dir / "metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path, "r", encoding="utf-8") as mf:
                    m = json.load(mf)
                raw = m.get("topics")
                if isinstance(raw, list):
                    topics = [str(t) for t in raw if t]
            except Exception:
                pass

        block = [
            f"## {seo_title}",
            "",
            f"- URL: {site_url}/meeting/{clip_id}",
        ]
        if topics:
            block.append(f"- Topics: {', '.join(topics)}")
        block += [
            "",
            preview if preview else "(no preview available)",
            "",
            "---",
            "",
        ]
        full_lines += block
    (public_dir / "llms-full.txt").write_text("\n".join(full_lines), encoding="utf-8")

    # ---- Per-clip Markdown alternates — written next to each clip's
    # other artifacts so deploy.sh syncs them to S3 alongside the rest.
    # AI agents fetching `/data/clips/<id>/clip.md` get the same content
    # the HTML page renders, plus the disclosure and decisions block,
    # without parsing JS-rendered markup.
    md_written = 0
    for entry in valid_clips:
        markdown = build_clip_markdown(entry, output_dir, site_url)
        if markdown is None:
            continue
        try:
            (output_dir / "clips" / str(entry["clip_id"]) / "clip.md").write_text(
                markdown, encoding="utf-8"
            )
            md_written += 1
        except OSError as e:
            log(f"Failed to write clip.md for clip {entry['clip_id']}: {e}", "WARNING")

    log(
        f"Generated SEO artifacts: sitemap.xml ({len(valid_clips) + len(static_routes)} urls), "
        f"sitemap_index.xml, news-sitemap.xml ({len(news_clips)} urls), llms.txt, skill.md, "
        f"llms-full.txt, {md_written} per-clip clip.md alternates"
    )
