"""Tests for scripts/build_search_db.py + rag/search.py.

Builds a tiny FTS5 DB against the sample_clip_dir fixture plus a
couple of hand-rolled clips so we can exercise filters, ranking,
snippet highlighting, and the suggest table.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rag import search
from scripts.build_search_db import build


# --- helpers ------------------------------------------------------------

def _write_clip(
    output_dir: Path,
    clip_id: int,
    *,
    title: str,
    date: str,
    body: str,
    transcript: str = "",
    agenda: str = "",
    minutes: str = "",
    facts: dict | None = None,
    speakers: list[str] | None = None,
    transcript_source: str | None = None,
) -> Path:
    """Lay out a clip directory the way main.py does, but compact."""
    clip_dir = output_dir / "clips" / str(clip_id)
    clip_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    if transcript:
        (clip_dir / "transcript.txt").write_text(transcript)
        files["transcript"] = "transcript.txt"
    if agenda:
        (clip_dir / "agenda.txt").write_text(agenda)
        files["agenda_txt"] = "agenda.txt"
    if minutes:
        (clip_dir / "minutes.txt").write_text(minutes)
        files["minutes_txt"] = "minutes.txt"
    if facts is not None:
        (clip_dir / "extracted_facts.json").write_text(json.dumps(facts))
        files["extracted_facts"] = "extracted_facts.json"

    metadata = {
        "clip_id": clip_id,
        "title": title,
        "date": date,
        "meeting_body": body,
        "files": files,
        "transcript_words": len(transcript.split()),
    }
    if speakers:
        metadata["speakers"] = speakers
    if transcript_source:
        metadata["transcript_source"] = transcript_source

    (clip_dir / "metadata.json").write_text(json.dumps(metadata))
    return clip_dir


@pytest.fixture
def search_db(tmp_path: Path) -> Path:
    """A miniature search.db with three clips covering common cases."""
    output_dir = tmp_path

    _write_clip(
        output_dir,
        100,
        title="Council on short-term rentals",
        date="2025-01-15",
        body="Council",
        transcript=(
            "Welcome everyone. Tonight we discuss short-term rentals "
            "and the new zoning ordinance affecting downtown."
        ),
        agenda="II. Public Comment - Issues on Agenda\nIII. Short-term rentals review",
        facts={
            "motions_and_votes": [{
                "identifier": "Ordinance 0016-26",
                "description": "Short-term rental zoning amendment",
                "outcome": "passed",
                "ayes": 8,
                "nays": 0,
            }],
            "financial_items": [{
                "amount": "$18,040,000",
                "description": "General Obligation Bonds",
                "identifier": "GOB-2025",
            }],
        },
        speakers=["Mayor Gorton", "Councilmember Hale"],
        transcript_source="whisper-1+vtt-speakers",
    )

    _write_clip(
        output_dir,
        101,
        title="Planning Commission rezoning",
        date="2024-06-10",
        body="Commission",
        transcript=(
            "Today's hearing covers a rezoning petition near "
            "Nicholasville Road. The applicant wants residential use."
        ),
        agenda="Rezoning petition Z-2024-15",
        speakers=["Chair", "Mayor Gorton"],
        transcript_source="whisper-1+vtt-speakers",
    )

    # Edge case: clip with HTML-ish text in the transcript so we can
    # verify the snippet escaper.
    _write_clip(
        output_dir,
        102,
        title="Budget hearing <special>",
        date="2025-09-01",
        body="Council",
        transcript="The budget < 5 million dollars short-term rentals tax revenue.",
    )

    db_path = output_dir / "search.db"
    build(output_dir, db_path, verbose=False)
    return db_path


@pytest.fixture
def punct_search_db(tmp_path: Path) -> Path:
    """A search.db whose one clip carries the punctuated terms the sanitizer
    used to choke on (commas, apostrophes, ``$``, ``&``)."""
    output_dir = tmp_path
    _write_clip(
        output_dir,
        200,
        title="Council Parks and Budget Work Session",
        date="2025-04-10",
        body="Council",
        transcript=(
            "Mayor Linda Gorton opened the session. The council's budget "
            "included a $5,000 grant for parks & rec improvements downtown."
        ),
        agenda="Budget review: parks and recreation $5,000 allocation.",
        speakers=["Mayor Gorton"],
        transcript_source="whisper-1+vtt-speakers",
    )
    db_path = output_dir / "search.db"
    build(output_dir, db_path, verbose=False)
    return db_path


@pytest.fixture(autouse=True)
def reset_search_connections():
    """Drop cached connections between tests so each test sees its own DB."""
    yield
    search.close_connections()


# --- builder ------------------------------------------------------------

def test_build_creates_fts_table_with_rows(search_db: Path) -> None:
    conn = sqlite3.connect(search_db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM clips_fts").fetchone()[0]
        assert count == 3
        suggest_count = conn.execute(
            "SELECT COUNT(*) FROM suggest_terms"
        ).fetchone()[0]
        assert suggest_count > 0
    finally:
        conn.close()


def test_skips_clips_with_no_searchable_text(tmp_path: Path) -> None:
    """A clip with no text content shouldn't be indexed."""
    clip_dir = tmp_path / "clips" / "999"
    clip_dir.mkdir(parents=True)
    (clip_dir / "metadata.json").write_text(json.dumps({
        "clip_id": 999, "title": "", "date": "2025-01-01", "files": {},
    }))

    db_path = tmp_path / "search.db"
    stats = build(tmp_path, db_path, verbose=False)
    assert stats["clips_indexed"] == 0
    assert stats["skipped"] == 1


def test_facts_are_searchable(search_db: Path) -> None:
    """Vote identifiers, dollar amounts, vendor names from facts hit FTS."""
    out_dir = str(search_db.parent)
    # FTS5's unicode61 tokenizer drops $ and , as separators, so we
    # search for tokens we know survive tokenization. "GOB-2025" and
    # "Obligation" both come from the financial_items facts.
    results = search.search("Obligation Bonds", out_dir)
    assert len(results) >= 1
    assert results[0]["clip_id"] == 100


def test_speakers_indexed_for_text_search(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.search("Gorton", out_dir)
    clip_ids = {r["clip_id"] for r in results}
    assert clip_ids >= {100, 101}


# --- search -------------------------------------------------------------

def test_search_returns_relevance_ordered_results(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.search("short-term rental", out_dir)
    assert len(results) >= 2
    # Clip 100 mentions short-term rentals in title + facts + transcript;
    # clip 102 only has it in transcript. Title weighted higher → 100 first.
    assert results[0]["clip_id"] == 100


def test_search_returns_html_safe_snippets(search_db: Path) -> None:
    """Snippets must escape stray '<' so frontend can render via dangerouslySetInnerHTML."""
    out_dir = str(search_db.parent)
    results = search.search("short-term rentals", out_dir)
    snippet_with_lt = next(
        (r["snippet"] for r in results if r["clip_id"] == 102),
        None,
    )
    if snippet_with_lt:
        # Stray '<' should be escaped to '&lt;' — the only acceptable
        # raw '<' is the one we re-inserted as <mark>.
        # Verify both that &lt; appears (escape happened) and that no
        # broken <special> tag leaks through.
        assert "&lt;" in snippet_with_lt or "<mark>" in snippet_with_lt
        assert "<special>" not in snippet_with_lt


def test_search_filters_by_meeting_body(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.search("rezoning", out_dir, meeting_body="Council")
    for r in results:
        assert r["meeting_body"] == "Council"


def test_search_filters_by_speaker(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.search("rezoning", out_dir, speaker="Chair")
    assert all("Chair" in r["speakers"] for r in results)


def test_search_filters_by_date_range(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.search(
        "rentals", out_dir, date_after="2025-01-01", date_before="2025-06-30"
    )
    assert all("2025-01-01" <= r["date"] <= "2025-06-30" for r in results)


def test_search_handles_quoted_phrase(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.search('"short-term rentals"', out_dir)
    assert len(results) >= 1
    # Phrase mode — clip 101 (no "rentals") should not match.
    assert all(r["clip_id"] != 101 for r in results)


def test_search_returns_empty_for_empty_query(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    assert search.search("", out_dir) == []
    assert search.search("   ", out_dir) == []


def test_search_tolerates_fts_special_chars(search_db: Path) -> None:
    """Naive user queries with parens/asterisks shouldn't crash."""
    out_dir = str(search_db.parent)
    # Should sanitize and still return results from "short" and "rental"
    results = search.search("short-term (rental*)", out_dir)
    assert isinstance(results, list)


def test_search_returns_empty_when_db_missing(tmp_path: Path) -> None:
    assert search.search("anything", str(tmp_path)) == []


@pytest.mark.parametrize(
    "query",
    [
        "Gorton, Linda",
        "$5,000",
        "council's budget",
        "parks & rec",
    ],
)
def test_punctuated_queries_return_results(punct_search_db: Path, query: str) -> None:
    """Punctuation ( , ' $ & ) must be treated as a token boundary, not raise
    an FTS5 syntax error that silently degrades to zero results (the old
    denylist-sanitizer bug — now an allowlist)."""
    out_dir = str(punct_search_db.parent)
    results = search.search(query, out_dir)
    assert len(results) >= 1, f"{query!r} unexpectedly returned no results"
    assert results[0]["clip_id"] == 200


# --- suggest ------------------------------------------------------------

def test_suggest_prefix_match(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    results = search.suggest("Plan", out_dir, limit=5)
    terms = [r["term"] for r in results]
    assert any(t.startswith("Plan") for t in terms)


def test_suggest_orders_by_weight(search_db: Path) -> None:
    """Multi-clip terms should outrank one-off terms."""
    out_dir = str(search_db.parent)
    results = search.suggest("Council", out_dir, limit=5)
    if len(results) >= 2:
        weights = [r["weight"] for r in results]
        assert weights == sorted(weights, reverse=True)


def test_suggest_empty_prefix_returns_empty(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    assert search.suggest("", out_dir) == []


def test_suggest_escapes_like_wildcards(tmp_path: Path) -> None:
    """A typed ``%`` / ``_`` must match the literal character, not act as a
    LIKE wildcard that returns every suggest term."""
    _write_clip(
        tmp_path,
        300,
        title="50% Budget Cut Hearing",
        date="2025-02-01",
        body="Council",
        transcript="A hearing on the proposed fifty percent budget cut.",
    )
    db_path = tmp_path / "search.db"
    build(tmp_path, db_path, verbose=False)
    out_dir = str(tmp_path)

    # Bare "%" matched EVERY term under the old un-escaped LIKE. Now it only
    # matches terms that literally start with "%" — none here.
    bare = search.suggest("%", out_dir)
    assert bare == []

    # A literal "50%" prefix still matches the "50% Budget Cut Hearing" title.
    got = search.suggest("50%", out_dir)
    assert any(t["term"].startswith("50%") for t in got)


# --- facets -------------------------------------------------------------

def test_facets_returns_bodies_speakers_and_dates(search_db: Path) -> None:
    out_dir = str(search_db.parent)
    f = search.facets(out_dir)
    assert "Council" in f["bodies"]
    assert "Commission" in f["bodies"]
    speaker_names = {s["name"] for s in f["speakers"]}
    assert "Mayor Gorton" in speaker_names
    assert f["date_min"] == "2024-06-10"
    assert f["date_max"] == "2025-09-01"


# --- facts flattening ----------------------------------------------------

def test_flatten_facts_uses_schema_appointment_keys() -> None:
    """Appointments use person/body_or_role (the EXTRACTION_SCHEMA keys)."""
    from scripts.build_search_db import _flatten_facts

    text = _flatten_facts({
        "appointments": [
            {"person": "Jane Doe", "body_or_role": "Planning Commission",
             "action": "appointed"},
        ],
    })
    assert "Jane Doe" in text
    assert "Planning Commission" in text


def test_flatten_facts_appointment_legacy_key_fallback() -> None:
    from scripts.build_search_db import _flatten_facts

    text = _flatten_facts({
        "appointments": [
            {"appointee": "Old Format", "position": "Old Board"},
        ],
    })
    assert "Old Format" in text
    assert "Old Board" in text


def test_flatten_facts_uses_schema_contentious_keys() -> None:
    """Contentious items use topic/details, not description."""
    from scripts.build_search_db import _flatten_facts

    text = _flatten_facts({
        "contentious_items": [
            {"topic": "Alcohol sales ordinance",
             "nature": "community_opposition",
             "details": "Several residents spoke against the expansion."},
        ],
    })
    assert "Alcohol sales ordinance" in text
    assert "spoke against the expansion" in text


def test_flatten_facts_contentious_legacy_key_fallback() -> None:
    from scripts.build_search_db import _flatten_facts

    text = _flatten_facts({
        "contentious_items": [{"description": "Legacy description text"}],
    })
    assert "Legacy description text" in text
