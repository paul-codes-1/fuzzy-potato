"""Tests for scripts/render_index_html.rewrite — the per-jurisdiction
index.html static-meta rewriter."""
from scripts.render_index_html import rewrite

LFUCG = {
    "site_url": "https://meetings.lexingtonky.news",
    "archive_name": "LFUCG Meeting Archive",
    "jurisdiction_full_name": "Lexington-Fayette Urban County Government",
}
PARIS = {
    "site_url": "https://paris.civicmemory.news",
    "archive_name": "Paris Meeting Archive",
    "jurisdiction_full_name": "City of Paris, Kentucky",
}

INDEX = (
    '<title>LFUCG Meeting Archive</title>\n'
    '<meta name="description" content="Searchable archive of Lexington-Fayette '
    'Urban County Government council and committee meetings." />\n'
    '<link rel="canonical" href="https://meetings.lexingtonky.news/" />\n'
    '<meta property="og:site_name" content="LFUCG Meeting Archive" />\n'
    '<meta property="og:url" content="https://meetings.lexingtonky.news/" />\n'
    '<!-- guide → https://meetings.lexingtonky.news/skill.md -->\n'
)


def test_rewrites_all_lfucg_literals_to_paris():
    out = rewrite(INDEX, LFUCG, PARIS)
    assert "Paris Meeting Archive" in out
    assert "City of Paris, Kentucky" in out
    assert "paris.civicmemory.news" in out
    # Zero LFUCG identity remains.
    assert "LFUCG" not in out
    assert "lexingtonky.news" not in out
    assert "Lexington-Fayette" not in out


def test_canonical_and_ogurl_point_at_target_host():
    out = rewrite(INDEX, LFUCG, PARIS)
    assert 'href="https://paris.civicmemory.news/"' in out
    assert 'content="https://paris.civicmemory.news/"' in out
    assert "https://paris.civicmemory.news/skill.md" in out


def test_noop_when_target_is_lfucg():
    out = rewrite(INDEX, LFUCG, LFUCG)
    assert out == INDEX
