#!/usr/bin/env python3
"""Rewrite the built SPA index.html's STATIC <title>/meta/canonical for the
active jurisdiction.

The SPA is one bundle; its index.html is built with LFUCG defaults baked in. At
runtime the React app overrides title/meta per route, so JS-running clients +
crawlers see the right jurisdiction — but no-JS social-card scrapers read the
static shell. This rewrites that shell so each county's deployed index.html
carries ITS OWN title/description/canonical/og: instead of LFUCG's.

Pure string replacement of the LFUCG literals (computed from the lfucg config,
not hard-coded) with the target jurisdiction's values (from build_site_config),
so it covers <title>, og:*, twitter:*, canonical, og:url, and the AI-agent
comment in one pass. Idempotent for LFUCG (no-op when target == lfucg).

Usage (target chosen by the JURISDICTION env var):
    JURISDICTION=paris uv run python scripts/render_index_html.py
    JURISDICTION=paris uv run python scripts/render_index_html.py path/to/index.html
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0]


def rewrite(html: str, lfucg: dict, target: dict) -> str:
    """Replace LFUCG identity literals in the built index.html with the target
    jurisdiction's. Order matters: do the longest/most-specific first."""
    src_host = _host(lfucg["site_url"])
    dst_host = _host(target["site_url"])
    replacements = [
        # Description prose carries the full jurisdiction name.
        (lfucg["jurisdiction_full_name"], target["jurisdiction_full_name"]),
        # Title / og:site_name / og:title / twitter:title all use archive_name.
        (lfucg["archive_name"], target["archive_name"]),
        # The AI-agent comment names the ecosystem/umbrella brand.
        (lfucg.get("brand", ""), target.get("brand", "")),
        # Canonical / og:url / comment links use the host.
        (src_host, dst_host),
    ]
    for old, new in replacements:
        if old and old != new:
            html = html.replace(old, new)
    return html


def main() -> int:
    from config import build_site_config, get_config
    from seo import _is_lexington_ecosystem

    # Target = whatever JURISDICTION resolves to right now.
    target = build_site_config(get_config())
    target["brand"] = (
        "The Lexington Times" if _is_lexington_ecosystem(target["site_url"]) else "Civic Memory"
    )

    # LFUCG literals = the baked-in index.html defaults. Resolve a fresh lfucg
    # config without disturbing the cached target.
    prev = os.environ.get("JURISDICTION")
    os.environ["JURISDICTION"] = "lfucg"
    get_config.cache_clear()
    lfucg = build_site_config(get_config())
    lfucg["brand"] = "The Lexington Times"
    if prev is None:
        os.environ.pop("JURISDICTION", None)
    else:
        os.environ["JURISDICTION"] = prev
    get_config.cache_clear()

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frontend/dist/index.html")
    if not path.exists():
        print(f"index.html not found at {path}", file=sys.stderr)
        return 1
    html = path.read_text(encoding="utf-8")
    out = rewrite(html, lfucg, target)
    path.write_text(out, encoding="utf-8")
    if out == html:
        print(f"index.html unchanged ({target['archive_name']} == built default)")
    else:
        print(f"index.html rewritten for {target['archive_name']} ({_host(target['site_url'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
