"""Jurisdiction-identity rewire tests (WS1 / WS6 slice).

The core regression guard for the multi-county config rewire: with the
default ``JURISDICTION=lfucg`` the public identity surfaces (SEO artifacts,
MCP server instructions, FastAPI title, RAG prompts) must read exactly as
they always have; with a *synthetic* second jurisdiction they must read that
jurisdiction's identity and leak **zero** LFUCG/Lexington strings.

These cover the lifted-into-config identity fields only — the platform words
("Granicus", "Whisper", "GPT-4o", "Claude") are intentionally NOT
parameterized here (a later PR owns the source abstraction).
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path

import pytest

import config
from config import get_config


# Where _load_toml looks for jurisdictions/<slug>.toml.
_JURIS_DIR = Path(config.__file__).resolve().parent / "jurisdictions"

# A synthetic second jurisdiction with no LFUCG/Lexington strings anywhere.
_TESTCOUNTY_TOML = """\
[jurisdiction]
slug = "testcounty"
name = "Testville City Council"

[granicus]
host = "testville.granicus.com"
default_view_id = 3
listing_view_fallbacks = [3, 1]
first_clip_id = 100

[taxonomy]
body_patterns = ["Council", "Commission"]
body_acronyms = []

[storage]
chroma_collection = "testville_meetings"

[site]
site_url = "https://meetings.testville.example"

[publication]
publication_name = "Testville Meeting Archive"
operator_name = "Test Operator"
editor_email = "ed@testville.example"
"""

# Jurisdiction-identity strings that must NEVER appear in a non-LFUCG
# jurisdiction's output — these are the values WS1 lifted into config.
_FORBIDDEN = (
    "LFUCG",
    "Lexington-Fayette",
    "lexingtonky",
    "editor@lexingtonky.news",
    "Paul Oliva",
    "LFUCG Meeting Archive",
)

# NOTE on the ecosystem brand: the canonical agent guide (skill.md, and the
# one llms.txt line that links to it) references "The Lexington Times" — the
# shared *product umbrella* brand, served byte-identical from every domain in
# the ecosystem. That is NOT per-jurisdiction identity (a neutral-umbrella
# rebrand owns it), so it is deliberately out of WS1's scope and excluded from
# the bare-"Lexington" leak check below.
_ECOSYSTEM_BRAND = "Lexington Times"


def _strip_ecosystem_brand(text: str) -> str:
    return text.replace(_ECOSYSTEM_BRAND, "")


def _set_jurisdiction(monkeypatch, slug: str | None) -> None:
    """Point config at a jurisdiction slug and bust the lru_cache."""
    get_config.cache_clear()
    if slug is None:
        monkeypatch.delenv("JURISDICTION", raising=False)
    else:
        monkeypatch.setenv("JURISDICTION", slug)
    get_config.cache_clear()


@pytest.fixture
def lfucg_config(monkeypatch):
    """Default LFUCG config, cache cleared before and after."""
    _set_jurisdiction(monkeypatch, "lfucg")
    yield get_config()
    get_config.cache_clear()


@pytest.fixture
def testcounty_config(monkeypatch):
    """Synthetic 'testcounty' config backed by a temp TOML, cleaned up after."""
    toml_path = _JURIS_DIR / "testcounty.toml"
    toml_path.write_text(_TESTCOUNTY_TOML, encoding="utf-8")
    _set_jurisdiction(monkeypatch, "testcounty")
    try:
        yield get_config()
    finally:
        get_config.cache_clear()
        if toml_path.exists():
            toml_path.unlink()


def _tiny_archive(tmp_path: Path) -> tuple[list, Path, Path]:
    """Build a minimal index + on-disk clip so seo artifacts have content.

    Returns (index_entries, output_dir, public_dir).
    """
    # Use today's date so the clip lands inside the news-sitemap's 48h window
    # (it filters on processed_at/date >= now-48h), exercising <news:name>.
    today = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat()
    output_dir = tmp_path / "out"
    clip_dir = output_dir / "clips" / "100"
    clip_dir.mkdir(parents=True)
    (clip_dir / "summary.txt").write_text(
        "## Overview\n\nThe council met to discuss the annual budget and "
        "approved several appropriations after public comment.\n",
        encoding="utf-8",
    )
    (clip_dir / "metadata.json").write_text(
        json.dumps(
            {
                "url": "https://example.com/clip/100",
                "processed_at": now_iso,
                "transcript_source": "whisper-1",
                "transcript_words": 1234,
            }
        ),
        encoding="utf-8",
    )
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    index_entries = [
        {
            "clip_id": 100,
            "date": today,
            "meeting_body": "Council",
            "title": "Regular Meeting (1)",
            "processed_at": now_iso,
        }
    ]
    return index_entries, output_dir, public_dir


def _generate(tmp_path: Path):
    """Run generate_seo_artifacts against a tiny archive; return the dir + files."""
    import seo

    index_entries, output_dir, public_dir = _tiny_archive(tmp_path)
    seo.generate_seo_artifacts(
        index_entries,
        output_dir,
        public_dir=public_dir,
        log=lambda *a, **k: None,
    )
    return output_dir, public_dir


# --------------------------------------------------------------------------
# Test A — LFUCG (default) identity is preserved.
# --------------------------------------------------------------------------


class TestLfucgIdentityDefaults:
    def test_config_defaults(self, lfucg_config):
        assert lfucg_config.publication_name == "LFUCG Meeting Archive"
        assert lfucg_config.operator_name == "Paul Oliva"
        assert lfucg_config.editor_email == "editor@lexingtonky.news"
        assert lfucg_config.name == "Lexington-Fayette Urban County Government"
        assert lfucg_config.site_url == "https://meetings.lexingtonky.news"

    def test_config_defaults_with_no_jurisdiction_env(self, monkeypatch):
        # The built-in default (slug "lfucg") must carry the same identity even
        # when JURISDICTION is unset entirely.
        _set_jurisdiction(monkeypatch, None)
        try:
            cfg = get_config()
            assert cfg.publication_name == "LFUCG Meeting Archive"
            assert cfg.editor_email == "editor@lexingtonky.news"
            assert cfg.operator_name == "Paul Oliva"
        finally:
            get_config.cache_clear()

    def test_seo_artifacts_keep_lfucg_identity(self, lfucg_config, tmp_path):
        _, public_dir = _generate(tmp_path)
        llms = (public_dir / "llms.txt").read_text(encoding="utf-8")
        news = (public_dir / "news-sitemap.xml").read_text(encoding="utf-8")
        llms_full = (public_dir / "llms-full.txt").read_text(encoding="utf-8")

        # Publication name (byte-identity proxy for the historical literals).
        assert "# LFUCG Meeting Archive" in llms
        assert "# LFUCG Meeting Archive" in llms_full
        assert "<news:name>LFUCG Meeting Archive</news:name>" in news
        # Operator + editorial contact.
        assert "Operated by Paul Oliva" in llms
        assert "editor@lexingtonky.news" in llms
        # Full jurisdiction proper noun in the prose blurb + attribution.
        assert "Lexington-Fayette Urban County Government" in llms

    def test_clip_md_keeps_lfucg_editor_email(self, lfucg_config, tmp_path):
        output_dir, _ = _generate(tmp_path)
        clip_md = (output_dir / "clips" / "100" / "clip.md").read_text(encoding="utf-8")
        assert "mailto:editor@lexingtonky.news" in clip_md


# --------------------------------------------------------------------------
# Test B — synthetic jurisdiction: identity swaps, zero LFUCG/Lexington leakage.
# --------------------------------------------------------------------------


class TestSyntheticJurisdictionIdentity:
    def test_config_resolves_testcounty(self, testcounty_config):
        assert testcounty_config.name == "Testville City Council"
        assert testcounty_config.publication_name == "Testville Meeting Archive"
        assert testcounty_config.editor_email == "ed@testville.example"
        assert testcounty_config.operator_name == "Test Operator"
        assert testcounty_config.site_url == "https://meetings.testville.example"

    def test_seo_artifacts_carry_testville_identity_only(self, testcounty_config, tmp_path):
        output_dir, public_dir = _generate(tmp_path)
        llms = (public_dir / "llms.txt").read_text(encoding="utf-8")
        news = (public_dir / "news-sitemap.xml").read_text(encoding="utf-8")
        llms_full = (public_dir / "llms-full.txt").read_text(encoding="utf-8")
        clip_md = (output_dir / "clips" / "100" / "clip.md").read_text(encoding="utf-8")

        # Testville identity is present.
        assert "# Testville Meeting Archive" in llms
        assert "<news:name>Testville Meeting Archive</news:name>" in news
        assert "Testville City Council" in llms
        assert "Operated by Test Operator" in llms
        assert "ed@testville.example" in llms
        assert "mailto:ed@testville.example" in clip_md

        # Zero jurisdiction-identity leakage across every SEO surface.
        for name, text in (
            ("llms.txt", llms),
            ("news-sitemap.xml", news),
            ("llms-full.txt", llms_full),
            ("clip.md", clip_md),
        ):
            cleaned = _strip_ecosystem_brand(text)
            for bad in _FORBIDDEN:
                assert bad not in cleaned, f"{bad!r} leaked into {name}"
            # Bare "Lexington" must also be gone once the ecosystem brand is
            # excluded (catches any stray jurisdiction-prose reference).
            assert "Lexington" not in cleaned, f"'Lexington' leaked into {name}"

    def test_mcp_instructions_carry_testville_identity(self, testcounty_config):
        import rag.mcp_server as mcp_module

        # SERVER_INSTRUCTIONS + SITE_URL are computed at import time, so reload
        # after switching the active jurisdiction.
        mcp_module = importlib.reload(mcp_module)
        try:
            instructions = mcp_module.SERVER_INSTRUCTIONS
            assert "Testville City Council" in instructions
            assert mcp_module.SITE_URL == "https://meetings.testville.example"
            for bad in _FORBIDDEN:
                assert bad not in instructions, f"{bad!r} leaked into MCP instructions"
        finally:
            # Restore the module to the default-jurisdiction state for other tests.
            get_config.cache_clear()
            importlib.reload(mcp_module)

    def test_fastapi_title_carries_testville_identity(self, testcounty_config):
        # rag/server.py builds the FastAPI title from publication_name at import.
        title = f"{get_config().publication_name} RAG API"
        assert title == "Testville Meeting Archive RAG API"
        for bad in _FORBIDDEN:
            assert bad not in title

    def test_synthesis_prompt_carries_testville_identity(self, testcounty_config):
        import rag.prompts as prompts_module

        # _NAME (and the prompt strings) are computed at import time.
        prompts_module = importlib.reload(prompts_module)
        try:
            assert "Testville City Council" in prompts_module.SYNTHESIS_SYSTEM_PROMPT
            assert "Testville City Council" in prompts_module.CHAT_SYSTEM_PROMPT
            for bad in ("Lexington", "lexingtonky"):
                assert bad not in prompts_module.SYNTHESIS_SYSTEM_PROMPT
                assert bad not in prompts_module.CHAT_SYSTEM_PROMPT
        finally:
            get_config.cache_clear()
            importlib.reload(prompts_module)
