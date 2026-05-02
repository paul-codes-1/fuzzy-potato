"""SQLite FTS5 search over the meeting archive.

Replaces the chunked client-side FlexSearch index. The DB lives at
``lfucg_output/search.db`` and is built by
:mod:`scripts.build_search_db`. Queries are sub-100ms across ~4,700
clips on a single connection.

This module is import-safe even when the DB doesn't exist — callers
get an empty result set instead of a hard failure, which keeps the
RAG server bootable in environments where search.db hasn't been
generated yet (e.g. local dev that only ran a partial pipeline).

Three public surfaces:

- :func:`search` — full-text search with BM25 ranking, body/speaker
  /date filters, and snippet HTML.
- :func:`suggest` — autocomplete over titles, bodies, topics, speakers.
- :func:`facets` — populates filter dropdowns (bodies + top speakers
  by frequency + min/max date).

Related-clips ("more like this") lives in :mod:`rag.related` so that
the search module has zero ChromaDB / OpenAI dependency.
"""

from __future__ import annotations

import html
import os
import re
import sqlite3
import threading
from typing import Optional

# BM25 column weights — title matches outrank everything, then facts
# (votes/$/IDs) since those are precision signals, then speakers, then
# the body documents. Order MUST match the FTS5 column order in
# scripts/build_search_db.py.
BM25_WEIGHTS = (10.0, 1.0, 2.0, 1.5, 5.0, 3.0)

# Snippet column priority for picking the "best" snippet to display.
# Tries facts first (most precise), then transcript, agenda, minutes,
# then title. Whatever has the first non-empty mark wins.
_SNIPPET_COLS = ("snip_facts", "snip_transcript", "snip_agenda", "snip_minutes", "snip_title")

# Sentinel markers passed to FTS5 snippet() so we can recognize the
# highlight boundaries after html-escaping the source text. Picked to
# never appear in real transcript content.
_MARK_OPEN = "\x01M\x01"
_MARK_CLOSE = "\x01/M\x01"

# Characters with FTS5 special meaning in non-phrase queries. Hyphen
# is the operator for NOT, so "short-term" parses as "short NOT term"
# (silently dropping rows matching "term") — strip it so users can
# type natural compound words.
_FTS_SPECIAL = re.compile(r'["()*\-:^]')

_conn_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}


def _db_path(output_dir: str) -> str:
    return os.path.join(output_dir, "search.db")


def get_connection(output_dir: str) -> Optional[sqlite3.Connection]:
    """Open (or reuse) the SQLite connection for this output dir.

    Connections are cached per-process; ``check_same_thread=False`` is
    safe here because all writes happen out-of-process via the
    builder, and SQLite serializes concurrent reads internally.
    Returns None if the DB file doesn't exist yet.
    """
    with _conn_lock:
        cached = _connections.get(output_dir)
        if cached is not None:
            return cached
        path = _db_path(output_dir)
        if not os.path.exists(path):
            return None
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _connections[output_dir] = conn
        return conn


def close_connections() -> None:
    """Test hook: drop cached connections so a freshly-built DB is reopened."""
    with _conn_lock:
        for conn in _connections.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _connections.clear()


def _sanitize_query(q: str) -> str:
    """Strip FTS5 syntax characters that would crash the query.

    Users type natural-language phrases. Quoted-phrase support is
    re-added downstream by detecting "..." wrapping; everything else
    is treated as bag-of-words AND search.
    """
    q = q.strip()
    if not q:
        return ""
    # If the user wrapped the whole thing in quotes, treat it as a
    # phrase query (FTS5's native quoted-phrase syntax).
    if len(q) > 2 and q.startswith('"') and q.endswith('"'):
        inner = q[1:-1].replace('"', "")
        return f'"{inner}"'
    # Otherwise strip syntax chars and treat as bag-of-words AND.
    cleaned = _FTS_SPECIAL.sub(" ", q)
    tokens = [t for t in cleaned.split() if t]
    return " ".join(tokens)


def _safe_snippet(raw: str) -> str:
    """HTML-escape the snippet, then re-insert <mark> tags.

    The transcript may contain stray ``<`` / ``>`` from typos or VTT
    artifacts. Escaping defends the frontend (which renders the
    snippet via ``dangerouslySetInnerHTML``) without losing the
    highlight markers we asked FTS5 to insert.
    """
    if not raw:
        return ""
    escaped = html.escape(raw, quote=False)
    return escaped.replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")


def _pick_snippet(row: sqlite3.Row) -> str:
    """Pick the first column-snippet containing a highlight marker.

    Falls back to the first non-empty snippet if none have a mark
    (rare — match was in an UNINDEXED filter column or stemming
    expansion put the match boundary just outside the window).
    """
    for col in _SNIPPET_COLS:
        snip = row[col] or ""
        if _MARK_OPEN in snip:
            return _safe_snippet(snip)
    for col in _SNIPPET_COLS:
        snip = row[col] or ""
        if snip:
            return _safe_snippet(snip)
    return ""


def search(
    query: str,
    output_dir: str,
    *,
    meeting_body: Optional[str] = None,
    speaker: Optional[str] = None,
    date_after: Optional[str] = None,
    date_before: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Run a BM25-ranked full-text search across the clips index.

    Returns at most ``limit`` rows, ordered by relevance (lowest BM25
    first — SQLite's bm25() returns negative scores). Filters are
    applied as plain WHERE clauses against UNINDEXED columns, so
    they cost effectively nothing.
    """
    conn = get_connection(output_dir)
    if conn is None:
        return []

    fts_query = _sanitize_query(query)
    if not fts_query:
        return []

    weights = ", ".join(str(w) for w in BM25_WEIGHTS)
    sql = f"""
        SELECT
            clip_id,
            date,
            meeting_body,
            speakers_csv,
            transcript_words,
            transcript_source,
            title AS title_text,
            snippet(clips_fts, 0, ?, ?, '…', 12) AS snip_title,
            snippet(clips_fts, 1, ?, ?, '…', 24) AS snip_transcript,
            snippet(clips_fts, 2, ?, ?, '…', 24) AS snip_agenda,
            snippet(clips_fts, 3, ?, ?, '…', 24) AS snip_minutes,
            snippet(clips_fts, 4, ?, ?, '…', 16) AS snip_facts,
            bm25(clips_fts, {weights}) AS score
        FROM clips_fts
        WHERE clips_fts MATCH ?
    """
    params: list[object] = []
    for _ in _SNIPPET_COLS:
        params.extend([_MARK_OPEN, _MARK_CLOSE])
    params.append(fts_query)
    if meeting_body:
        sql += " AND meeting_body = ?"
        params.append(meeting_body)
    if speaker:
        # Wrap in commas so "Hale" doesn't accidentally match
        # "Hale-Smith" but does match "Councilmember Hale".
        sql += " AND ',' || speakers_csv || ',' LIKE ?"
        params.append(f"%,{speaker},%")
    if date_after:
        sql += " AND date >= ?"
        params.append(date_after)
    if date_before:
        sql += " AND date <= ?"
        params.append(date_before)

    sql += " ORDER BY score LIMIT ?"
    params.append(int(limit))

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Malformed query made it past sanitization — give up rather
        # than 500'ing the endpoint.
        return []

    return [
        {
            "clip_id": int(r["clip_id"]),
            "date": r["date"] or None,
            "meeting_body": r["meeting_body"] or None,
            "title": r["title_text"],
            "speakers": [s for s in (r["speakers_csv"] or "").split(",") if s],
            "transcript_words": int(r["transcript_words"] or 0),
            "transcript_source": r["transcript_source"] or None,
            "snippet": _pick_snippet(r),
            "score": float(r["score"]),
        }
        for r in rows
    ]


def suggest(prefix: str, output_dir: str, *, limit: int = 10) -> list[dict]:
    """Autocomplete over titles, bodies, topics, and speaker names.

    Case-insensitive prefix match, ordered by frequency weight then
    alphabetical. Deduplicated by term so the same string doesn't
    appear twice if it lives in two suggest categories.
    """
    conn = get_connection(output_dir)
    if conn is None or not prefix.strip():
        return []
    p = prefix.strip()
    rows = conn.execute(
        """
        SELECT term, kind, weight
        FROM suggest_terms
        WHERE term LIKE ? COLLATE NOCASE
        ORDER BY weight DESC, term COLLATE NOCASE
        LIMIT ?
        """,
        (f"{p}%", int(limit)),
    ).fetchall()
    return [{"term": r["term"], "kind": r["kind"], "weight": int(r["weight"])} for r in rows]


def facets(output_dir: str, *, top_speakers: int = 30) -> dict:
    """Populate filter dropdowns: bodies, top speakers, date range.

    Cheap query; the suggest_terms table already has counts. Speakers
    are capped because VTT noise leaves a long tail of one-off
    labels (~hundreds of useless single-occurrence "speakers").
    """
    conn = get_connection(output_dir)
    if conn is None:
        return {"bodies": [], "speakers": [], "date_min": None, "date_max": None}

    bodies = [
        r["term"]
        for r in conn.execute(
            "SELECT term FROM suggest_terms WHERE kind = 'body' ORDER BY weight DESC, term"
        ).fetchall()
    ]
    speakers = [
        {"name": r["term"], "count": int(r["weight"])}
        for r in conn.execute(
            "SELECT term, weight FROM suggest_terms WHERE kind = 'speaker' "
            "ORDER BY weight DESC, term LIMIT ?",
            (int(top_speakers),),
        ).fetchall()
    ]
    row = conn.execute(
        "SELECT MIN(date) AS dmin, MAX(date) AS dmax FROM clips_fts WHERE date != ''"
    ).fetchone()
    return {
        "bodies": bodies,
        "speakers": speakers,
        "date_min": row["dmin"] if row else None,
        "date_max": row["dmax"] if row else None,
    }
