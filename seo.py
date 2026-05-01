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
    return cleaned or formatted or "LFUCG Meeting"


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


def generate_seo_artifacts(
    index_entries: List[Dict[str, Any]],
    output_dir: Path,
    public_dir: Optional[Path] = None,
    site_url: Optional[str] = None,
    log: Callable[..., None] = _default_log,
) -> None:
    """Write sitemap.xml, sitemap_index.xml, news-sitemap.xml, llms.txt, llms-full.txt.

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
    site_url = (site_url or os.environ.get("LFUCG_SITE_URL", "https://meetings.lexingtonky.news")).rstrip("/")
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

    # ---- news-sitemap.xml — last 48h of meetings, in Google News sitemap format.
    cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")
    news_clips = [e for e in valid_clips if (e.get("date") or "0000-00-00") >= cutoff]
    news_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">',
    ]
    for entry in news_clips:
        clip_id = entry["clip_id"]
        date = entry.get("date") or today
        seo_title = build_seo_title(entry.get("title"), entry.get("date"))
        news_lines += [
            "  <url>",
            f"    <loc>{site_url}/meeting/{escape(str(clip_id))}</loc>",
            "    <news:news>",
            "      <news:publication>",
            "        <news:name>LFUCG Meeting Archive</news:name>",
            "        <news:language>en</news:language>",
            "      </news:publication>",
            f"      <news:publication_date>{escape(str(date))}</news:publication_date>",
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
        "# LFUCG Meeting Archive",
        "",
        f"> Searchable archive of Lexington-Fayette Urban County Government council and committee meetings — every clip downloaded, transcribed via Whisper, summarized via GPT-4o + Claude Sonnet, and indexed for semantic search. {len(valid_clips)} meetings hosted at {site_url}.",
        "",
        "## About",
        "",
        f"- Site: [{site_url}]({site_url})",
        f"- Search index (JSON): [{site_url}/data/index.json]({site_url}/data/index.json)",
        f"- RAG Q&A (HTML): [{site_url}/ask]({site_url}/ask) — natural-language Q&A over the archive",
        f"- Sitemap: [{site_url}/sitemap_index.xml]({site_url}/sitemap_index.xml)",
        f"- Full meeting dump: [{site_url}/llms-full.txt]({site_url}/llms-full.txt)",
        "",
        "## How content is generated",
        "",
        "Each meeting page contains: an AI-generated narrative summary (Claude Sonnet, sectioned with `[timestamp: MM:SS]` markers for video deep-linking), structured fact extraction (votes with roll calls, financial items, attendance, agenda items, public comments — all in `extracted_facts.json`), the official agenda PDF, the official minutes PDF (when published), and a full Whisper transcript with segment timestamps. Summaries cite specific timestamps that link back to the Granicus video player.",
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
        "Source video, agendas, and minutes are public records published by the Lexington-Fayette Urban County Government on Granicus. Transcripts and summaries on this site are AI-generated for accessibility — verify against the official video and minutes for high-stakes use. When citing, please link to the canonical meeting page (e.g. `" + site_url + "/meeting/{id}`).",
        "",
    ]
    (public_dir / "llms.txt").write_text("\n".join(llms_lines), encoding="utf-8")

    # ---- llms-full.txt — recent meetings with summary previews for one-pull consumption.
    full_lines = [
        "# LFUCG Meeting Archive",
        "",
        f"> {len(valid_clips)} Lexington-Fayette Urban County Government meetings, transcribed and summarized.",
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

    log(
        f"Generated SEO artifacts: sitemap.xml ({len(valid_clips) + len(static_routes)} urls), "
        f"sitemap_index.xml, news-sitemap.xml ({len(news_clips)} urls), llms.txt, llms-full.txt"
    )
