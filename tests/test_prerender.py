"""Tests for prerender.py — the per-clip HTML pages that replace the SPA-shell
fallback at /meeting/<id> (the GSC canonical fix)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import prerender
from prerender import (
    STATIC_ROUTES,
    build_clip_page,
    build_static_page,
    clip_fingerprint,
    generate_prerendered_pages,
    render_page,
    strip_shell_head,
    summary_to_html,
    template_is_valid,
)

SITE = "https://meetings.lexingtonky.news"

SHELL = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>LFUCG Meeting Archive</title>
    <meta name="description" content="Searchable archive of LFUCG meetings." />
    <link rel="canonical" href="https://meetings.lexingtonky.news/" />
    <meta property="og:title" content="LFUCG Meeting Archive" />
    <meta property="og:url" content="https://meetings.lexingtonky.news/" />
    <meta name="twitter:card" content="summary" />
    <script type="module" crossorigin src="/assets/index-C3SrTQmB.js"></script>
    <link rel="stylesheet" crossorigin href="/assets/index-3EpBQpVA.css">
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
"""

SUMMARY = """## Meeting Overview
- **Date**: August 29, 2026
- **Summary**: The committee reviewed the snow plan. [timestamp: 12:34]

## Key Decisions & Votes
1. **Contract**: Seven of eight contractors have no retainer. [timestamp: 1:02:03]

Closing remarks were brief.
"""


def _make_clip(tmp_path: Path, clip_id: int = 6865, *, summary: str | None = SUMMARY,
               facts: dict | None = None, source: str = "whisper-1+vtt-speakers") -> Path:
    clip_dir = tmp_path / "clips" / str(clip_id)
    clip_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "clip_id": clip_id,
        "url": f"https://lfucg.granicus.com/player/clip/{clip_id}?view_id=14",
        "date": "2026-08-29",
        "meeting_body": "Council",
        "title": "Council Work Session (1)",
        "files": {"transcript": "transcript.txt", "summary_txt": "summary.txt",
                  "agenda_pdf": "agenda.pdf"},
        "processed_at": "2026-08-30T02:00:00",
        "transcript_words": 12345,
        "transcript_source": source,
    }
    (clip_dir / "metadata.json").write_text(json.dumps(meta))
    (clip_dir / "transcript.txt").write_text("hello " * 50)
    (clip_dir / "agenda.pdf").write_bytes(b"%PDF-1.4")
    if summary is not None:
        (clip_dir / "summary.txt").write_text(summary)
    if facts is not None:
        (clip_dir / "extracted_facts.json").write_text(json.dumps(facts))
    return clip_dir


def _entry(clip_id: int = 6865) -> dict:
    return {"clip_id": clip_id, "date": "2026-08-29", "meeting_body": "Council",
            "title": "Council Work Session (1)", "transcript_words": 12345}


class TestTemplate:
    def test_valid_shell(self):
        assert template_is_valid(SHELL)

    def test_rejects_shell_without_assets_or_root(self):
        assert not template_is_valid("<html><head></head><body></body></html>")
        assert not template_is_valid(SHELL.replace('<div id="root"></div>', ""))

    def test_strip_removes_page_level_head_tags_only(self):
        out = strip_shell_head(SHELL)
        assert "<title>" not in out
        assert 'rel="canonical"' not in out
        assert 'name="description"' not in out
        assert "og:" not in out and "twitter:" not in out
        # The bundle references survive untouched.
        assert "/assets/index-C3SrTQmB.js" in out
        assert "/assets/index-3EpBQpVA.css" in out

    def test_render_page_injects_head_and_root(self):
        out = render_page(SHELL, head_html="    <title>X</title>", root_html="<p>fallback</p>")
        assert "<title>X</title>" in out
        assert '<div id="root"><p>fallback</p></div>' in out
        assert out.count("<title>") == 1

    def test_load_template_prefers_env_override(self, tmp_path, monkeypatch):
        path = tmp_path / "shell.html"
        path.write_text(SHELL)
        monkeypatch.setenv("PRERENDER_TEMPLATE", str(path))
        assert prerender.load_template(SITE, log=lambda *a, **k: None) == SHELL

    def test_load_template_rejects_invalid_override(self, tmp_path, monkeypatch):
        path = tmp_path / "shell.html"
        path.write_text("<html></html>")
        monkeypatch.setenv("PRERENDER_TEMPLATE", str(path))
        assert prerender.load_template(SITE, log=lambda *a, **k: None) is None


class TestSummaryToHtml:
    def test_sections_paragraphs_bullets_and_timestamps(self):
        out = summary_to_html(SUMMARY, "https://lfucg.granicus.com/player/clip/6865?view_id=14")
        assert "<h2>Meeting Overview</h2>" in out
        assert "<h2>Key Decisions &amp; Votes</h2>" in out
        assert "<ul><li><strong>Date</strong>: August 29, 2026</li>" in out
        assert "<p>Closing remarks were brief.</p>" in out
        # [timestamp: 12:34] -> Granicus deep link at 754s; 1:02:03 -> 3723s
        assert 'entrytime=754">12:34</a>' in out
        assert 'entrytime=3723">1:02:03</a>' in out
        assert "[timestamp" not in out

    def test_escapes_html(self):
        out = summary_to_html("## A <b>bad</b> header\nText with <script>alert(1)</script>")
        assert "<script>" not in out and "&lt;script&gt;" in out

    def test_empty(self):
        assert summary_to_html("") == ""
        assert summary_to_html("   \n") == ""


class TestBuildClipPage:
    def test_head_carries_per_clip_metadata(self, tmp_path):
        _make_clip(tmp_path, facts={"motions_and_votes": [
            {"identifier": "Ordinance 0016-26", "description": "Snow plan contract",
             "outcome": "passed", "ayes": 8, "nays": 0}]})
        page = build_clip_page(_entry(), tmp_path, SITE, SHELL)
        assert page is not None
        assert "<title>Council Work Session - August 29, 2026 | LFUCG Meeting Archive</title>" in page
        assert f'<link rel="canonical" href="{SITE}/meeting/6865" />' in page
        assert f'content="{SITE}/meeting/6865"' in page                      # og:url
        assert 'property="og:type" content="article"' in page
        assert f'<link rel="alternate" type="text/markdown" href="{SITE}/data/clips/6865/clip.md" />' in page
        # Exactly one of each page-level tag (shell copies stripped).
        assert page.count("<title>") == 1
        assert page.count('rel="canonical"') == 1
        assert page.count('property="og:title"') == 1
        assert page.count('name="description"') == 1
        # Description comes from the summary's first substantive paragraph.
        desc = re.search(r'<meta name="description" content="([^"]+)"', page).group(1)
        assert "snow plan" in desc.lower()
        # Bundle refs intact.
        assert "/assets/index-C3SrTQmB.js" in page

    def test_jsonld_graph_mirrors_frontend(self, tmp_path):
        _make_clip(tmp_path)
        page = build_clip_page(_entry(), tmp_path, SITE, SHELL)
        m = re.search(r'<script type="application/ld\+json" id="meeting-jsonld">(.*?)</script>', page, re.S)
        assert m
        graph = json.loads(m.group(1).replace("<\\/", "</"))
        types = {n["@type"] for n in graph["@graph"]}
        assert types == {"Article", "Organization", "WebSite"}
        article = next(n for n in graph["@graph"] if n["@type"] == "Article")
        assert article["@id"] == f"{SITE}/meeting/6865#article"
        assert article["datePublished"] == "2026-08-29"
        assert article["dateModified"] == "2026-08-30"
        assert article["wordCount"] == 12345
        assert article["isBasedOn"].startswith("https://lfucg.granicus.com/player/clip/6865")
        assert article["creativeWorkStatus"] == "Auto-transcribed"

    def test_root_fallback_body(self, tmp_path):
        _make_clip(tmp_path, facts={"motions_and_votes": [
            {"identifier": "Ordinance 0016-26", "description": "Snow plan contract",
             "outcome": "passed", "ayes": 8, "nays": 0}]})
        page = build_clip_page(_entry(), tmp_path, SITE, SHELL)
        root = page.split('<div id="root">', 1)[1]
        assert "<h1>Council Work Session</h1>" in root
        assert "August 29, 2026 &middot; Council &middot; 12,345 words" in root
        assert "Auto-generated content." in root
        assert 'href="https://lfucg.granicus.com/player/clip/6865?view_id=14"' in root
        assert 'href="/data/clips/6865/clip.md"' in root
        assert 'href="/data/clips/6865/transcript.txt"' in root
        assert 'href="/data/clips/6865/agenda.pdf"' in root
        assert "<h2>Summary</h2>" in root
        assert "<h2>Decisions</h2>" in root
        assert "<strong>Ordinance 0016-26</strong> &mdash; passed (8-0): Snow plan contract" in root

    def test_placeholder_disclosure_for_vtt(self, tmp_path):
        _make_clip(tmp_path, source="granicus_vtt")
        page = build_clip_page(_entry(), tmp_path, SITE, SHELL)
        assert "Whisper transcription pending" in page

    def test_missing_summary_falls_back_to_preview(self, tmp_path):
        _make_clip(tmp_path, summary=None)
        entry = {**_entry(), "agenda_preview": "II. Public Comment - Issues on Agenda about the snow plan and salt supply."}
        page = build_clip_page(entry, tmp_path, SITE, SHELL)
        assert "<h2>Summary</h2>" not in page
        assert "Issues on Agenda about the snow plan" in page

    def test_missing_clip_dir_returns_none(self, tmp_path):
        assert build_clip_page(_entry(999), tmp_path, SITE, SHELL) is None

    def test_title_with_script_tag_is_escaped_everywhere(self, tmp_path):
        clip_dir = _make_clip(tmp_path)
        meta = json.loads((clip_dir / "metadata.json").read_text())
        meta["title"] = 'Evil </script><script>alert(1)</script> (1)'
        (clip_dir / "metadata.json").write_text(json.dumps(meta))
        entry = {**_entry(), "title": meta["title"]}
        page = build_clip_page(entry, tmp_path, SITE, SHELL)
        assert "<script>alert(1)</script>" not in page


class TestStaticPages:
    def test_static_page_has_own_canonical(self):
        route = next(r for r in STATIC_ROUTES if r["key"] == "ask")
        page = build_static_page(route, SITE, SHELL)
        assert f'<link rel="canonical" href="{SITE}/ask" />' in page
        assert f'href="{SITE}/" />' not in page
        assert "<title>Ask a question | LFUCG Meeting Archive</title>" in page

    def test_about_title_uses_archive_name(self):
        route = next(r for r in STATIC_ROUTES if r["key"] == "about")
        page = build_static_page(route, SITE, SHELL)
        assert "<title>About the LFUCG Meeting Archive | LFUCG Meeting Archive</title>" in page


class TestGenerateIncremental:
    def test_writes_pages_and_state_then_skips_unchanged(self, tmp_path):
        _make_clip(tmp_path, 6865)
        _make_clip(tmp_path, 6866)
        entries = [_entry(6865), _entry(6866), _entry(4242)]  # 4242 has no dir
        log = lambda *a, **k: None  # noqa: E731
        stats = generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, log=log)
        assert stats == {"written": 2, "unchanged": 0, "skipped": 1, "static_written": len(STATIC_ROUTES)}
        assert (tmp_path / "prerender" / "meeting" / "6865").exists()
        assert (tmp_path / "prerender" / "ask").exists()
        state = json.loads((tmp_path / "prerender_state.json").read_text())
        assert set(state["clips"]) == {"6865", "6866"}

        # Second run: nothing changed -> nothing rewritten (mtimes preserved for s3 sync).
        before = (tmp_path / "prerender" / "meeting" / "6865").stat().st_mtime_ns
        stats2 = generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, log=log)
        assert stats2["written"] == 0 and stats2["unchanged"] == 2 and stats2["static_written"] == 0
        assert (tmp_path / "prerender" / "meeting" / "6865").stat().st_mtime_ns == before

    def test_changed_summary_rerenders_that_clip_only(self, tmp_path):
        d1 = _make_clip(tmp_path, 6865)
        _make_clip(tmp_path, 6866)
        entries = [_entry(6865), _entry(6866)]
        log = lambda *a, **k: None  # noqa: E731
        generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, log=log)
        (d1 / "summary.txt").write_text(SUMMARY + "\n## Added\nA brand new section about salt trucks.\n")
        import os
        os.utime(d1 / "summary.txt", ns=(1, 2_000_000_000_000_000_000))  # force a distinct mtime
        stats = generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, log=log)
        assert stats["written"] == 1 and stats["unchanged"] == 1
        assert "salt trucks" in (tmp_path / "prerender" / "meeting" / "6865").read_text()

    def test_template_change_forces_full_rerender(self, tmp_path):
        _make_clip(tmp_path, 6865)
        entries = [_entry(6865)]
        log = lambda *a, **k: None  # noqa: E731
        generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, log=log)
        new_shell = SHELL.replace("index-C3SrTQmB.js", "index-NEWHASH00.js")
        stats = generate_prerendered_pages(entries, tmp_path, SITE, template=new_shell, log=log)
        assert stats["written"] == 1
        assert "index-NEWHASH00.js" in (tmp_path / "prerender" / "meeting" / "6865").read_text()

    def test_full_flag_rerenders_even_when_fingerprint_matches(self, tmp_path):
        _make_clip(tmp_path, 6865)
        entries = [_entry(6865)]
        log = lambda *a, **k: None  # noqa: E731
        generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, log=log)
        # Same bytes -> still "unchanged" (write skipped) but the clip was re-rendered, not fingerprint-skipped.
        stats = generate_prerendered_pages(entries, tmp_path, SITE, template=SHELL, full=True, log=log)
        assert stats["unchanged"] == 1 and stats["skipped"] == 0

    def test_no_template_is_a_noop(self, tmp_path, monkeypatch):
        _make_clip(tmp_path, 6865)
        monkeypatch.setenv("PRERENDER_TEMPLATE", str(tmp_path / "missing.html"))
        stats = generate_prerendered_pages([_entry(6865)], tmp_path, SITE, log=lambda *a, **k: None)
        assert stats["written"] == 0
        assert not (tmp_path / "prerender").exists() or not any((tmp_path / "prerender").rglob("*"))

    def test_fingerprint_changes_with_template_and_files(self, tmp_path):
        d = _make_clip(tmp_path, 6865)
        a = clip_fingerprint(d, "sha-a", SITE)
        assert clip_fingerprint(d, "sha-b", SITE) != a
        (d / "extracted_facts.json").write_text("{}")
        assert clip_fingerprint(d, "sha-a", SITE) != a


def test_seo_artifacts_skip_prerender_when_disabled(tmp_path, monkeypatch):
    """generate_seo_artifacts must not touch the network under pytest."""
    import seo
    monkeypatch.setenv("PRERENDER_ENABLED", "0")
    _make_clip(tmp_path, 6865)
    public = tmp_path / "public"
    public.mkdir()
    seo.generate_seo_artifacts([_entry(6865)], tmp_path, public_dir=public,
                               site_url=SITE, log=lambda *a, **k: None)
    assert not (tmp_path / "prerender").exists()


def test_seo_artifacts_run_prerender_with_env_template(tmp_path, monkeypatch):
    import seo
    shell = tmp_path / "shell.html"
    shell.write_text(SHELL)
    monkeypatch.setenv("PRERENDER_TEMPLATE", str(shell))
    _make_clip(tmp_path, 6865)
    public = tmp_path / "public"
    public.mkdir()
    seo.generate_seo_artifacts([_entry(6865)], tmp_path, public_dir=public,
                               site_url=SITE, log=lambda *a, **k: None, prerender=True)
    page = (tmp_path / "prerender" / "meeting" / "6865").read_text()
    assert f'<link rel="canonical" href="{SITE}/meeting/6865" />' in page
