"""Pre-rendered per-clip HTML pages — the SEO fix for the meeting archive.

Problem (GSC, 2026-09): CloudFront serves the SPA shell (``index.html`` via
the S3 404 → 200 custom-error fallback) for EVERY ``/meeting/<id>`` path.
That shell carried a hard-coded ``<link rel="canonical" href="<site>/">``
and homepage title/description, so Google saw 2,847 meeting pages that all
claimed to be the homepage: 3,340 "crawled – not indexed", 708 duplicates.

Fix: emit one real HTML document per clip and upload it as the S3 object
``meeting/<id>`` (no extension, ``Content-Type: text/html``) so the exact
path resolves to a 200 with per-clip ``<title>``, description, canonical,
og:/twitter: cards, a ``text/markdown`` alternate, JSON-LD, and a plain-HTML
fallback body inside ``<div id="root">`` that React replaces on mount
(``createRoot().render`` clears the container's existing children).

The page IS the live SPA shell — same ``/assets/<hash>.js`` + CSS — with the
head rewritten and the root pre-filled, so the bundle boots exactly as it
does today. The template is resolved in this order:

1. ``PRERENDER_TEMPLATE`` — explicit path (local dev / tests).
2. ``<site_url>/index.html`` fetched live — what is actually deployed, so
   the asset hashes always match production (the box has no dist/).
3. ``frontend/dist/index.html`` — workstation fallback.

Generation is incremental: ``lfucg_output/prerender_state.json`` records a
fingerprint per clip (template hash + the mtimes/sizes of metadata.json,
summary.txt and extracted_facts.json); a clip is re-rendered only when that
changes, so the 6-hourly cron touches a handful of files. ``full=True``
re-renders everything (a new bundle changes the template hash, which also
triggers a full pass on the next incremental run). Output lives under
``lfucg_output/prerender/`` — ``meeting/<id>`` plus the flat static routes —
and ``deploy/lightsail/sync_data_s3.sh`` ships it to the bucket ROOT with an
explicit text/html content type and a ``/meeting/*`` invalidation.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import build_site_config, get_config
from seo import (
    build_seo_description,
    build_seo_title,
    clean_title,
    format_long_date,
)

PRERENDER_DIRNAME = "prerender"
STATE_FILENAME = "prerender_state.json"
TEMPLATE_FETCH_TIMEOUT = 15

# Flat static routes that live in the sitemap and used to inherit the
# homepage canonical. (/about/methodology is deliberately absent: a file
# named ``about`` and a directory ``about/`` can't coexist on disk, and the
# SPA sets that page's canonical client-side anyway.)
STATIC_ROUTES: List[Dict[str, str]] = [
    {"key": "ask", "path": "/ask", "title": "Ask a question",
     "description": "Ask a natural-language question about {name} meetings and get an AI-synthesized answer with citations to the exact meeting video timestamps."},
    {"key": "chat", "path": "/chat", "title": "Chat",
     "description": "Multi-turn chat over the {name} meeting archive — votes, budgets, zoning, and more, grounded in transcripts, minutes, and agendas."},
    {"key": "about", "path": "/about", "title": "About the {archive}",
     "description": "Who runs the {archive}, why it exists, and who it is for."},
    {"key": "corrections", "path": "/corrections", "title": "Corrections",
     "description": "How to report a transcription or summary error in the {archive}, our turnaround commitment, and the log of past corrections."},
]

_TIMESTAMP_RE = re.compile(r"\[timestamp:\s*(?:(\d+):)?(\d+):(\d{2})\]")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEAD_STRIP_RES = [
    re.compile(r"\s*<title>.*?</title>", re.DOTALL | re.IGNORECASE),
    re.compile(r'\s*<meta\s+name="description"[^>]*/?>', re.IGNORECASE),
    re.compile(r'\s*<link\s+rel="canonical"[^>]*/?>', re.IGNORECASE),
    re.compile(r'\s*<meta\s+(?:property|name)="(?:og|twitter):[^"]*"[^>]*/?>', re.IGNORECASE),
    re.compile(r'\s*<link\s+rel="alternate"\s+type="text/markdown"[^>]*/?>', re.IGNORECASE),
    re.compile(r'\s*<script\s+type="application/ld\+json"[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE),
]
_ROOT_RE = re.compile(r'<div\s+id="root"\s*>\s*</div>', re.IGNORECASE)


def _default_log(message: str, level: str = "INFO") -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {level}: {message}")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def template_is_valid(template: str) -> bool:
    """A usable SPA shell has the root div and at least one hashed asset."""
    return bool(template) and bool(_ROOT_RE.search(template)) and "/assets/" in template


def load_template(site_url: str, log: Callable[..., None] = _default_log) -> Optional[str]:
    """Resolve the SPA shell to pre-render into (see module docstring)."""
    override = os.environ.get("PRERENDER_TEMPLATE")
    if override:
        try:
            text = Path(override).read_text(encoding="utf-8")
        except OSError as e:
            log(f"prerender: PRERENDER_TEMPLATE unreadable ({e})", "WARNING")
            return None
        if template_is_valid(text):
            return text
        log("prerender: PRERENDER_TEMPLATE is not a valid SPA shell", "WARNING")
        return None

    url = f"{site_url.rstrip('/')}/index.html"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fuzzy-potato-prerender/1.0",
                                                   "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=TEMPLATE_FETCH_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        if template_is_valid(text):
            return text
        log(f"prerender: live shell at {url} failed validation; trying local dist", "WARNING")
    except Exception as e:  # network / DNS / TLS — fall through to local dist
        log(f"prerender: could not fetch live shell {url} ({e}); trying local dist", "WARNING")

    local = Path(__file__).resolve().parent / "frontend" / "dist" / "index.html"
    if local.exists():
        try:
            text = local.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if template_is_valid(text):
            log(f"prerender: using local {local} as template (asset hashes must match prod!)", "WARNING")
            return text
    log("prerender: no usable template — skipping pre-rendered pages", "WARNING")
    return None


def strip_shell_head(template: str) -> str:
    """Remove the shell's page-level head tags (title, description,
    canonical, og:/twitter:, markdown alternate, JSON-LD) so the per-page
    block is the only copy. The SPA's setMetaTag/setCanonical helpers
    find-and-update by attribute, so a single tag per name stays single."""
    out = template
    for rx in _HEAD_STRIP_RES:
        out = rx.sub("", out)
    return out


def render_page(template: str, *, head_html: str, root_html: str) -> str:
    """Inject ``head_html`` before ``</head>`` and ``root_html`` into ``#root``."""
    base = strip_shell_head(template)
    if "</head>" not in base:
        raise ValueError("template has no </head>")
    base = re.sub(r"\s*</head>", lambda _m: f"\n{head_html}\n  </head>", base, count=1)
    if not _ROOT_RE.search(base):
        raise ValueError("template has no empty <div id=\"root\">")
    return _ROOT_RE.sub(lambda _m: f'<div id="root">{root_html}</div>', base, count=1)


# ---------------------------------------------------------------------------
# Head block + JSON-LD
# ---------------------------------------------------------------------------

def _meta(attr: str, key: str, value: str) -> str:
    return f'<meta {attr}="{html.escape(key, quote=True)}" content="{html.escape(value or "", quote=True)}" />'


def build_head_block(*, title: str, description: str, canonical: str, og_type: str,
                     site_name: str, markdown_url: Optional[str] = None,
                     jsonld: Optional[dict] = None, jsonld_id: str = "meeting-jsonld") -> str:
    lines = [
        f"    <title>{html.escape(title)}</title>",
        "    " + _meta("name", "description", description),
        f'    <link rel="canonical" href="{html.escape(canonical, quote=True)}" />',
        "    " + _meta("property", "og:title", title),
        "    " + _meta("property", "og:description", description),
        "    " + _meta("property", "og:type", og_type),
        "    " + _meta("property", "og:url", canonical),
        "    " + _meta("property", "og:site_name", site_name),
        "    " + _meta("name", "twitter:card", "summary"),
        "    " + _meta("name", "twitter:title", title),
        "    " + _meta("name", "twitter:description", description),
    ]
    if markdown_url:
        lines.append(f'    <link rel="alternate" type="text/markdown" href="{html.escape(markdown_url, quote=True)}" />')
    if jsonld:
        # "</" can't appear unescaped inside a script; JSON-encode with the
        # slash escaped so a stray "</script>" in a title can't break out.
        payload = json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
        lines.append(f'    <script type="application/ld+json" id="{html.escape(jsonld_id, quote=True)}">{payload}</script>')
    return "\n".join(lines)


def _organization_node(site: dict) -> dict:
    site_url = site["site_url"]
    founder = {
        "@type": "Person",
        "@id": f"{site_url}/about/{(site.get('operator_name') or 'operator').lower().replace(' ', '-')}#person",
        "name": site.get("operator_name"),
    }
    if site.get("operator_author_url"):
        founder["sameAs"] = [site["operator_author_url"]]
    return {
        "@type": "Organization",
        "@id": f"{site_url}#organization",
        "name": site["archive_name"],
        "url": site_url,
        "description": site.get("description"),
        "founder": founder,
    }


def _website_node(site: dict) -> dict:
    site_url = site["site_url"]
    return {
        "@type": "WebSite",
        "@id": f"{site_url}#website",
        "url": site_url,
        "name": site["archive_name"],
        "publisher": {"@id": f"{site_url}#organization"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": f"{site_url}/?q={{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    }


def build_meeting_graph(site: dict, *, clip_id: Any, title: str, iso_date: str,
                        description: str, granicus_url: str, date_modified: str,
                        transcript_words: int) -> dict:
    """Mirror of frontend/src/utils/seo.js::buildMeetingGraph (keep in sync)."""
    site_url = site["site_url"]
    url = f"{site_url}/meeting/{clip_id}"
    seo_title = build_seo_title(title, iso_date)
    document_driven = (site.get("source") or {}).get("kind") == "document"
    producer = (
        "GPT-4o (fact extraction), Anthropic Claude Sonnet (narrative summary)"
        if document_driven else
        "OpenAI Whisper-1 (audio→text), GPT-4o (fact extraction), Anthropic Claude Sonnet (narrative summary)"
    )
    article: Dict[str, Any] = {
        "@type": "Article",
        "@id": f"{url}#article",
        "headline": seo_title,
        "name": seo_title,
        "url": url,
        "mainEntityOfPage": url,
        "datePublished": iso_date,
        "dateModified": date_modified or iso_date,
        "author": {"@id": f"{site_url}#organization"},
        "publisher": {"@id": f"{site_url}#organization"},
        "isPartOf": {"@id": f"{site_url}#website"},
        "inLanguage": "en-US",
        "creativeWorkStatus": "Auto-summarized" if document_driven else "Auto-transcribed",
        "producer": {"@type": "Organization", "name": producer},
    }
    if granicus_url:
        article["isBasedOn"] = granicus_url
    if description:
        article["description"] = description
    if transcript_words:
        article["wordCount"] = int(transcript_words)
    return {"@context": "https://schema.org",
            "@graph": [article, _organization_node(site), _website_node(site)]}


# ---------------------------------------------------------------------------
# Body (the no-JS fallback React replaces on mount)
# ---------------------------------------------------------------------------

def _inline_md(text: str, granicus_url: str) -> str:
    """Escape, then bold + clickable [timestamp: MM:SS] markers."""
    out = html.escape(text)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)

    def _ts(m: re.Match) -> str:
        hours = int(m.group(1)) if m.group(1) else 0
        secs = hours * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        label = f"{hours}:{int(m.group(2)):02d}:{m.group(3)}" if hours else f"{int(m.group(2))}:{m.group(3)}"
        if granicus_url:
            sep = "&amp;" if "?" in granicus_url else "?"
            href = f"{html.escape(granicus_url, quote=True)}{sep}entrytime={secs}"
            return f'<a class="prerender-ts" href="{href}">{label}</a>'
        return label

    return _TIMESTAMP_RE.sub(_ts, out)


def summary_to_html(summary_text: str, granicus_url: str = "") -> str:
    """Render the v2 narrative summary (## sections, paragraphs, - bullets)."""
    if not summary_text or not summary_text.strip():
        return ""
    parts: List[str] = []
    for raw_section in re.split(r"^##\s+", summary_text.strip(), flags=re.MULTILINE):
        section = raw_section.strip()
        if not section:
            continue
        lines = section.split("\n", 1)
        header = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if not body and not summary_text.lstrip().startswith("## "):
            # No headers at all: the whole text is one body.
            body, header = header, ""
        if header:
            parts.append(f"<h2>{_inline_md(header, granicus_url)}</h2>")
        for para in re.split(r"\n\s*\n", body):
            para = para.strip()
            if not para:
                continue
            plines = [ln.rstrip() for ln in para.split("\n")]
            if all(ln.lstrip().startswith(("- ", "* ", "• ")) for ln in plines if ln.strip()):
                items = "".join(
                    f"<li>{_inline_md(ln.lstrip()[2:].strip(), granicus_url)}</li>"
                    for ln in plines if ln.strip()
                )
                parts.append(f"<ul>{items}</ul>")
            else:
                parts.append(f"<p>{_inline_md(' '.join(ln.strip() for ln in plines), granicus_url)}</p>")
    return "\n".join(parts)


def _disclosure_text(transcript_source: str, site: dict) -> str:
    if transcript_source == "granicus_vtt":
        return ("The transcript on this page is the Granicus stenographer's live closed-captioning "
                "track (Whisper transcription pending). Structured facts were extracted with GPT-4o; "
                "the narrative summary was written by Anthropic Claude. Wording and speaker "
                "attribution may contain errors.")
    if transcript_source in ("civicclerk_minutes", "civicclerk_agenda"):
        return ("This record is built from the official CivicClerk documents — there is no verbatim "
                "transcript. Structured facts were extracted with GPT-4o; the narrative summary was "
                "written by Anthropic Claude.")
    if transcript_source == "whisper-large-v3-local":
        return ("Audio from the official Granicus video was auto-transcribed with OpenAI's open-source "
                "Whisper large-v3-turbo model. Structured facts were extracted with GPT-4o; the "
                "narrative summary was written by Anthropic Claude. Wording may contain errors.")
    label = "with speaker labels folded in from Granicus closed-captioning" \
        if transcript_source == "whisper-1+vtt-speakers" else ""
    return (f"Audio from the official {(site.get('source') or {}).get('platform', 'Granicus')} video "
            f"was auto-transcribed by OpenAI Whisper{(' ' + label) if label else ''}. Structured facts "
            "were extracted with GPT-4o; the narrative summary was written by Anthropic Claude. "
            "Speaker labels and verbatim wording may contain errors.")


def build_clip_root_html(entry: Dict[str, Any], clip_dir: Path, site: dict, *,
                         metadata: dict, summary_text: str, facts: dict) -> str:
    clip_id = entry.get("clip_id")
    title = clean_title(entry.get("title")) or f"Meeting {clip_id}"
    iso_date = entry.get("date") or metadata.get("date") or ""
    body = entry.get("meeting_body") or metadata.get("meeting_body") or ""
    granicus_url = metadata.get("url") or ""
    words = int(metadata.get("transcript_words") or entry.get("transcript_words") or 0)
    files = metadata.get("files") or {}
    transcript_file = files.get("transcript") if isinstance(files, dict) else None
    transcript_source = metadata.get("transcript_source") or "whisper-1"

    meta_bits = [b for b in (format_long_date(iso_date), body, f"{words:,} words" if words else "") if b]
    links = []
    if granicus_url:
        links.append(f'<a href="{html.escape(granicus_url, quote=True)}" rel="noopener">Watch the official video</a>')
    links.append(f'<a href="/data/clips/{clip_id}/clip.md" type="text/markdown">Markdown version</a>')
    if transcript_file and (clip_dir / transcript_file).exists():
        links.append(f'<a href="/data/clips/{clip_id}/{html.escape(str(transcript_file), quote=True)}">Full transcript (text)</a>')
    if files.get("agenda_pdf"):
        links.append(f'<a href="/data/clips/{clip_id}/{html.escape(str(files["agenda_pdf"]), quote=True)}">Agenda (PDF)</a>')
    if files.get("minutes_pdf"):
        links.append(f'<a href="/data/clips/{clip_id}/{html.escape(str(files["minutes_pdf"]), quote=True)}">Official minutes (PDF)</a>')

    out = [
        '<main class="prerender container">',
        '<p><a href="/">&larr; Back to all meetings</a></p>',
        f"<h1>{html.escape(title)}</h1>",
    ]
    if meta_bits:
        out.append(f'<p class="meeting-meta">{" &middot; ".join(html.escape(b) for b in meta_bits)}</p>')
    out.append(
        '<aside class="ai-disclosure" role="note"><strong>Auto-generated content.</strong> '
        f"{html.escape(_disclosure_text(transcript_source, site))} "
        'See <a href="/about/methodology">methodology</a> or '
        f'<a href="mailto:{html.escape(site.get("contact_email") or "", quote=True)}">report a correction</a>.</aside>'
    )
    out.append(f'<p class="prerender-links">{" &middot; ".join(links)}</p>')

    summary_html = summary_to_html(summary_text, granicus_url)
    if summary_html:
        out.append('<section class="prerender-summary"><h2>Summary</h2>')
        out.append(summary_html)
        out.append("</section>")
    else:
        preview = (entry.get("agenda_preview") or entry.get("transcript_preview") or "").strip()
        if preview:
            out.append(f'<section class="prerender-summary"><p>{html.escape(preview)}</p></section>')

    votes = (facts or {}).get("motions_and_votes") or []
    if votes:
        out.append('<section class="prerender-decisions"><h2>Decisions</h2><ul>')
        for v in votes:
            ident = v.get("identifier") or "Motion"
            desc = v.get("description") or ""
            outcome = v.get("outcome") or "unknown"
            tally = f" ({v.get('ayes', 0)}-{v.get('nays', 0)})" if v.get("ayes") is not None else ""
            line = f"<strong>{html.escape(str(ident))}</strong> &mdash; {html.escape(str(outcome))}{html.escape(tally)}"
            if desc:
                line += f": {html.escape(str(desc))}"
            out.append(f"<li>{line}</li>")
        out.append("</ul></section>")
    out.append("</main>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Per-clip page
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_clip_page(entry: Dict[str, Any], output_dir: Path, site_url: str,
                    template: str, site: Optional[dict] = None) -> Optional[str]:
    """Full HTML document for one clip, or None if the clip dir is missing."""
    clip_id = entry.get("clip_id")
    if clip_id is None:
        return None
    clip_dir = output_dir / "clips" / str(clip_id)
    if not clip_dir.exists():
        return None
    site = site or build_site_config(get_config())
    site = {**site, "site_url": site_url.rstrip("/")}

    metadata = _read_json(clip_dir / "metadata.json")
    facts = _read_json(clip_dir / "extracted_facts.json")
    summary_path = clip_dir / "summary.txt"
    try:
        summary_text = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    except OSError:
        summary_text = ""

    title = entry.get("title") or metadata.get("title")
    iso_date = entry.get("date") or metadata.get("date") or ""
    seo_title = build_seo_title(title, iso_date)
    description = build_seo_description(
        summary_path,
        fallback=entry.get("agenda_preview") or entry.get("transcript_preview") or "",
    ) or site.get("description") or ""
    canonical = f"{site['site_url']}/meeting/{clip_id}"
    revised_iso = metadata.get("summary_updated_at") or metadata.get("processed_at") or iso_date
    graph = build_meeting_graph(
        site, clip_id=clip_id, title=title, iso_date=iso_date, description=description,
        granicus_url=metadata.get("url") or "",
        date_modified=(revised_iso or "")[:10] if revised_iso else iso_date,
        transcript_words=int(metadata.get("transcript_words") or 0),
    )
    head = build_head_block(
        title=f"{seo_title} | {site['archive_name']}",
        description=description,
        canonical=canonical,
        og_type="article",
        site_name=site["archive_name"],
        markdown_url=f"{site['site_url']}/data/clips/{clip_id}/clip.md",
        jsonld=graph,
    )
    root = build_clip_root_html(entry, clip_dir, site, metadata=metadata,
                                summary_text=summary_text, facts=facts)
    return render_page(template, head_html=head, root_html=root)


def build_static_page(route: Dict[str, str], site_url: str, template: str,
                      site: Optional[dict] = None) -> str:
    site = site or build_site_config(get_config())
    site = {**site, "site_url": site_url.rstrip("/")}
    fmt = {"name": site.get("jurisdiction_full_name", ""), "archive": site["archive_name"]}
    title = route["title"].format(**fmt)
    description = route["description"].format(**fmt)
    canonical = f"{site['site_url']}{route['path']}"
    head = build_head_block(
        title=f"{title} | {site['archive_name']}",
        description=description,
        canonical=canonical,
        og_type="website",
        site_name=site["archive_name"],
    )
    root = (
        '<main class="prerender container">'
        f"<h1>{html.escape(title)}</h1><p>{html.escape(description)}</p>"
        '<p><a href="/">&larr; Back to all meetings</a></p></main>'
    )
    return render_page(template, head_html=head, root_html=root)


# ---------------------------------------------------------------------------
# Incremental driver
# ---------------------------------------------------------------------------

def _stat_sig(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return "-"
    return f"{st.st_mtime_ns}:{st.st_size}"


def clip_fingerprint(clip_dir: Path, template_sha: str, site_url: str) -> str:
    parts = [template_sha, site_url,
             _stat_sig(clip_dir / "metadata.json"),
             _stat_sig(clip_dir / "summary.txt"),
             _stat_sig(clip_dir / "extracted_facts.json")]
    return _sha1("|".join(parts))


def _load_state(path: Path) -> dict:
    data = _read_json(path)
    if not isinstance(data.get("clips"), dict):
        data["clips"] = {}
    return data


def _write_if_changed(path: Path, content: str) -> bool:
    """Write only when the bytes differ so unchanged files keep their mtime
    (aws s3 sync compares mtime+size — a rewrite would re-upload everything)."""
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


def generate_prerendered_pages(
    index_entries: List[Dict[str, Any]],
    output_dir: Path,
    site_url: Optional[str] = None,
    *,
    template: Optional[str] = None,
    full: bool = False,
    log: Callable[..., None] = _default_log,
) -> Dict[str, int]:
    """Render ``meeting/<id>`` for every clip (incrementally) + the static routes.

    Returns ``{"written", "unchanged", "skipped", "static_written"}``.
    Never raises on a single bad clip — logs and continues.
    """
    cfg = get_config()
    site_url = (site_url or os.environ.get("LFUCG_SITE_URL") or cfg.site_url).rstrip("/")
    output_dir = Path(output_dir)
    stats = {"written": 0, "unchanged": 0, "skipped": 0, "static_written": 0}

    if template is None:
        template = load_template(site_url, log)
    if not template:
        return stats
    if not template_is_valid(template):
        log("prerender: template failed validation — skipping", "WARNING")
        return stats

    site = build_site_config(cfg)
    template_sha = _sha1(template)
    out_root = output_dir / PRERENDER_DIRNAME
    meetings_dir = out_root / "meeting"
    meetings_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / STATE_FILENAME
    state = _load_state(state_path)
    if state.get("template_sha") != template_sha:
        if state.get("template_sha"):
            log("prerender: SPA shell changed (new asset hashes) — re-rendering every page")
        full = True
    new_clips: Dict[str, str] = {}

    for entry in index_entries:
        clip_id = entry.get("clip_id")
        if clip_id is None:
            continue
        key = str(clip_id)
        clip_dir = output_dir / "clips" / key
        if not clip_dir.exists():
            stats["skipped"] += 1
            continue
        fp = clip_fingerprint(clip_dir, template_sha, site_url)
        target = meetings_dir / key
        if not full and state["clips"].get(key) == fp and target.exists():
            new_clips[key] = fp
            stats["unchanged"] += 1
            continue
        try:
            page = build_clip_page(entry, output_dir, site_url, template, site)
        except Exception as e:  # one bad clip must not sink the batch
            log(f"prerender: clip {key} failed: {e}", "WARNING")
            stats["skipped"] += 1
            continue
        if page is None:
            stats["skipped"] += 1
            continue
        if _write_if_changed(target, page):
            stats["written"] += 1
        else:
            stats["unchanged"] += 1
        new_clips[key] = fp

    for route in STATIC_ROUTES:
        try:
            page = build_static_page(route, site_url, template, site)
            if _write_if_changed(out_root / route["key"], page):
                stats["static_written"] += 1
        except Exception as e:
            log(f"prerender: static page {route['key']} failed: {e}", "WARNING")

    state.update({
        "template_sha": template_sha,
        "site_url": site_url,
        "generated_at": datetime.now().isoformat(),
        "clips": new_clips,
    })
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, state_path)

    log(
        f"Pre-rendered pages: {stats['written']} written, {stats['unchanged']} unchanged, "
        f"{stats['skipped']} skipped, {stats['static_written']} static pages updated "
        f"→ {out_root}"
    )
    return stats
