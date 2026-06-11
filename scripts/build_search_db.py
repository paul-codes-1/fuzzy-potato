#!/usr/bin/env python3
"""Build the SQLite FTS5 search index from the clips/ tree.

Replaces the chunked FlexSearch JSON index that the frontend used to
download and rebuild on every page load. The DB lives at
``lfucg_output/search.db`` and is queried server-side via
``rag/search.py`` (see :mod:`rag.search`).

The builder is idempotent: it builds a fresh DB into ``search.db.tmp``
and ``os.replace()``s it onto ``search.db`` atomically, so a co-located
long-lived reader keeps serving the old index until it reopens (no
torn-read / stale-fd window). Full archive (~4,700 clips) takes ~30s on
a modern laptop.

Schema (one row per clip):
    title           — searchable, weighted highest in BM25
    transcript      — searchable, the bulk of the corpus
    agenda          — searchable
    minutes         — searchable
    facts           — searchable, flattened from extracted_facts.json
    speakers_text   — searchable, lets users find clips where a person spoke
    clip_id         — UNINDEXED, integer
    date            — UNINDEXED, ISO YYYY-MM-DD
    meeting_body    — UNINDEXED
    speakers_csv    — UNINDEXED, comma-joined for filter LIKE matching
    transcript_words — UNINDEXED
    transcript_source — UNINDEXED

A separate ``suggest_terms`` table powers /api/suggest autocomplete:
titles, meeting bodies, topics, speaker names, agenda identifiers.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "lfucg_output"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "search.db"

# Allow imports from repo root (for `python scripts/build_search_db.py`).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import get_config  # noqa: E402


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _flatten_facts(facts: dict) -> str:
    """Render extracted_facts.json into a single searchable text blob.

    The output is consumed by FTS5 tokenization, so structure doesn't
    matter — we just need the salient strings (vote outcomes, dollar
    amounts, agenda identifiers, public-comment topics) to be present.
    """
    if not facts:
        return ""

    parts: list[str] = []

    info = facts.get("meeting_info") or {}
    if info.get("presiding_officer"):
        parts.append(f"Presiding: {info['presiding_officer']}")
    if info.get("location"):
        parts.append(f"Location: {info['location']}")

    for v in facts.get("motions_and_votes") or []:
        line = f"Vote {v.get('identifier', '')}: {v.get('description', '')} — {v.get('outcome', '')}"
        if v.get("ayes") is not None:
            line += f" ayes:{v['ayes']} nays:{v.get('nays', 0)}"
        if v.get("motion_by"):
            line += f" motion by {v['motion_by']}"
        parts.append(line)

    for f in facts.get("financial_items") or []:
        line = f"{f.get('amount', '')} {f.get('description', '')}"
        if f.get("identifier"):
            line += f" ({f['identifier']})"
        if f.get("vendor_or_recipient"):
            line += f" — {f['vendor_or_recipient']}"
        parts.append(line)

    for a in facts.get("agenda_items") or []:
        line = f"Agenda {a.get('identifier', '')}: {a.get('title', '')}"
        if a.get("summary"):
            line += f" — {a['summary']}"
        parts.append(line)

    for c in facts.get("public_comments") or []:
        speaker = c.get("speaker") or "Speaker"
        topic = c.get("topic") or ""
        summary = c.get("summary") or ""
        parts.append(f"Public comment by {speaker} on {topic}: {summary}")

    # Schema keys are person/body_or_role (see EXTRACTION_SCHEMA in
    # summary_v2.py); fall back to the legacy appointee/position keys in
    # case any older facts files carry them.
    for ap in facts.get("appointments") or []:
        person = ap.get("person") or ap.get("appointee") or ""
        role = ap.get("body_or_role") or ap.get("position") or ""
        parts.append(f"Appointment {person} to {role}")

    # Schema keys are topic/details; legacy fallback to description.
    for c in facts.get("contentious_items") or []:
        topic = c.get("topic") or c.get("description") or ""
        details = c.get("details") or ""
        parts.append(f"Contentious: {topic} {details}".rstrip())

    return "\n".join(p for p in parts if p)


def _speakers_from_segments(segments_path: Path) -> list[str]:
    """Pull the distinct speaker labels from a segments JSON file."""
    if not segments_path.exists():
        return []
    try:
        with segments_path.open() as f:
            segments = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(segments, list):
        return []
    seen: dict[str, None] = {}
    for s in segments:
        sp = (s or {}).get("speaker")
        if sp and isinstance(sp, str) and sp not in seen:
            seen[sp] = None
    return list(seen.keys())


def _collect_clip(clip_dir: Path) -> dict | None:
    """Load every searchable artifact for a single clip into one row dict."""
    metadata_path = clip_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        with metadata_path.open() as f:
            metadata = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    clip_id = metadata.get("clip_id")
    if clip_id is None:
        return None

    files = metadata.get("files", {}) or {}
    transcript = _read_text(clip_dir / files.get("transcript", "")) if files.get("transcript") else ""
    agenda = _read_text(clip_dir / files.get("agenda_txt", "")) if files.get("agenda_txt") else ""
    minutes = _read_text(clip_dir / files.get("minutes_txt", "")) if files.get("minutes_txt") else ""

    facts_text = ""
    facts_path = clip_dir / files.get("extracted_facts", "")
    if files.get("extracted_facts") and facts_path.exists():
        try:
            with facts_path.open() as f:
                facts_text = _flatten_facts(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    # Prefer metadata.speakers (set during VTT enrichment); fall back to
    # scanning the segments file for clips where the metadata field is
    # missing but per-segment labels exist.
    speakers = metadata.get("speakers") or []
    if not speakers and files.get("transcript_segments"):
        speakers = _speakers_from_segments(clip_dir / files["transcript_segments"])
    speakers_text = " ".join(speakers)
    speakers_csv = ",".join(speakers)

    body = metadata.get("meeting_body") or ""
    if body:
        # Same config-driven normalization main.py uses — the acronym set
        # is per-jurisdiction, not a hard-coded LFUCG list.
        acronyms = set(get_config().body_acronyms)
        body = body.upper() if body.upper() in acronyms else body.title()

    return {
        "clip_id": int(clip_id),
        "title": metadata.get("title") or f"Clip {clip_id}",
        "date": metadata.get("date") or "",
        "meeting_body": body,
        "transcript": transcript,
        "agenda": agenda,
        "minutes": minutes,
        "facts": facts_text,
        "speakers_text": speakers_text,
        "speakers_csv": speakers_csv,
        "transcript_words": int(metadata.get("transcript_words") or 0),
        "transcript_source": metadata.get("transcript_source") or "",
        "topics": metadata.get("topics") or [],
    }


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS clips_fts;
        DROP TABLE IF EXISTS suggest_terms;
        CREATE VIRTUAL TABLE clips_fts USING fts5(
            title,
            transcript,
            agenda,
            minutes,
            facts,
            speakers_text,
            clip_id UNINDEXED,
            date UNINDEXED,
            meeting_body UNINDEXED,
            speakers_csv UNINDEXED,
            transcript_words UNINDEXED,
            transcript_source UNINDEXED,
            tokenize = 'porter unicode61 remove_diacritics 2'
        );
        CREATE TABLE suggest_terms (
            term TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX idx_suggest_terms_kind ON suggest_terms(kind);
        """
    )


def _insert_clip(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO clips_fts (
            title, transcript, agenda, minutes, facts, speakers_text,
            clip_id, date, meeting_body, speakers_csv,
            transcript_words, transcript_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["title"],
            row["transcript"],
            row["agenda"],
            row["minutes"],
            row["facts"],
            row["speakers_text"],
            row["clip_id"],
            row["date"],
            row["meeting_body"],
            row["speakers_csv"],
            row["transcript_words"],
            row["transcript_source"],
        ),
    )


def _build_suggest_terms(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """Build the autocomplete dictionary from titles, bodies, topics, speakers.

    Frequency-weighted so the suggest endpoint can return more useful
    matches first (e.g. "Council" outranks one-off topic mentions).
    """
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if row["title"]:
            counts[(row["title"].strip(), "title")] += 1
        if row["meeting_body"]:
            counts[(row["meeting_body"].strip(), "body")] += 1
        for topic in row["topics"]:
            if isinstance(topic, str) and topic.strip():
                counts[(topic.strip(), "topic")] += 1
        if row["speakers_csv"]:
            for sp in row["speakers_csv"].split(","):
                sp = sp.strip()
                if sp:
                    counts[(sp, "speaker")] += 1

    conn.executemany(
        "INSERT OR REPLACE INTO suggest_terms (term, kind, weight) VALUES (?, ?, ?)",
        [(term, kind, weight) for (term, kind), weight in counts.items()],
    )
    return len(counts)


def build(output_dir: Path, db_path: Path, *, verbose: bool = True) -> dict:
    clips_dir = output_dir / "clips"
    if not clips_dir.is_dir():
        raise SystemExit(f"clips dir not found: {clips_dir}")

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build into a sibling temp file, then atomically os.replace() it onto
    # db_path. A co-located long-lived reader (rag/search.py caches the
    # sqlite connection for the process lifetime) keeps serving the OLD,
    # complete DB off its open fd until it reopens — so it never sees a
    # half-written or unlinked file. (Pre-co-location this used
    # unlink()+recreate, which on one shared box would hand the reader a
    # dangling fd → stale results or "database disk image is malformed".)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(tmp_path)
    try:
        _create_schema(conn)

        rows: list[dict] = []
        skipped = 0
        for entry in sorted(clips_dir.iterdir()):
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            row = _collect_clip(entry)
            if row is None:
                skipped += 1
                continue
            # Skip clips with literally no searchable content. The
            # title alone doesn't count — a fallback "Clip N" would
            # bloat the index without offering any real match surface.
            if not any((row["transcript"], row["agenda"], row["minutes"], row["facts"])):
                skipped += 1
                continue
            rows.append(row)
            _insert_clip(conn, row)

        suggest_count = _build_suggest_terms(conn, rows)
        conn.commit()
        conn.execute("INSERT INTO clips_fts(clips_fts) VALUES('optimize')")
        conn.commit()
    finally:
        conn.close()

    # Atomic swap — the moment the new index becomes visible at db_path.
    os.replace(tmp_path, db_path)

    stats = {
        "clips_indexed": len(rows),
        "skipped": skipped,
        "suggest_terms": suggest_count,
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size,
    }
    if verbose:
        size_mb = stats["db_size_bytes"] / 1024 / 1024
        print(
            f"Built {db_path} ({size_mb:.1f} MB) — "
            f"{stats['clips_indexed']} clips, {stats['suggest_terms']} suggest terms, "
            f"{stats['skipped']} skipped"
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Pipeline output dir (contains clips/)",
    )
    p.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Where to write search.db (default: <output-dir>/search.db)",
    )
    args = p.parse_args(argv)

    output_dir = args.output_dir
    db_path = args.db_path or (output_dir / "search.db")
    build(output_dir, db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
