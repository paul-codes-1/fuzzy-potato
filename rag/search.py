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
import logging
import os
import re
import sqlite3
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# BM25 column weights — title matches outrank everything, then facts
# (votes/$/IDs) since those are precision signals, then speakers, then
# the body documents. Order MUST match the FTS5 column order in
# scripts/build_search_db.py.
BM25_WEIGHTS = (10.0, 1.0, 2.0, 1.5, 5.0, 3.0)

# The UI renders exactly ONE snippet per result (MeetingList's
# HighlightedSnippet). We used to compute five per-column snippet()
# windows and pick the first with a highlight; now FTS5 picks the
# best-matching column itself (column index -1) so only one window is
# built per returned row.
_SNIPPET_COL = "snip"

# English stopwords stripped from bag-of-words queries. FTS5's implicit
# AND meant "what did the council say about parks" required every clip to
# contain "what", "did", "say", "about" — the stopwords, not the topic,
# decided the result set. Kept small and conservative: only function
# words that carry no retrieval signal in meeting text.
_STOPWORDS = frozenset("""
a an the and or but if then than so as of at by for from in into on onto to
with without about over under between through during before after above below
up down out off again further once here there when where why how all any both
each few more most other some such no nor not only own same too very can will
just should now is are was were be been being am do does did doing have has had
having i me my myself we our ours you your yours he him his she her hers it its
they them their theirs what which who whom this that these those would could
might must shall may also ever every whatever whenever wherever whether while
did does done get got getting give given goes going say said says tell told
""".split())

# Question-word openers. A query that starts with one of these (or that runs
# longer than _QUESTION_MIN_TOKENS tokens) is natural language, not a
# keyword list, and gets the AND-first / OR-fallback treatment.
_QUESTION_OPENERS = frozenset(
    "who what when where why how did does do is are was were which can could "
    "should has have had will would".split()
)
_QUESTION_MIN_TOKENS = 6

# Sentinel markers passed to FTS5 snippet() so we can recognize the
# highlight boundaries after html-escaping the source text. Picked to
# never appear in real transcript content.
_MARK_OPEN = "\x01M\x01"
_MARK_CLOSE = "\x01/M\x01"

# FTS5 bareword allowlist. A denylist of "known-bad" characters keeps
# leaking new ones ( , . ' ! ? & / $ … ) into the query, each of which can
# raise an FTS5 syntax error that used to silently degrade to zero results.
# Instead, collapse everything that isn't a word char or whitespace to a
# space, leaving only barewords for the implicit-AND bag-of-words query.
# The quoted-phrase branch (below) runs BEFORE this, so intentional phrase
# queries keep their FTS5 native quoting.
_FTS_NON_BAREWORD = re.compile(r"[^\w\s]", re.UNICODE)

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


def _is_quoted_phrase(q: str) -> bool:
    q = q.strip()
    return len(q) > 2 and q.startswith('"') and q.endswith('"')


def query_tokens(q: str) -> list[str]:
    """Bareword tokens for the bag-of-words path: punctuation → boundary,
    lowercased (FTS5 keywords AND/OR/NOT are only operators in UPPERCASE,
    so lowercasing also neutralises a user-typed "OR"), punctuation-only
    tokens dropped, then English stopwords removed. If stripping stopwords
    would leave nothing ("what is it"), the original tokens are kept so
    the query still runs instead of silently returning nothing.
    """
    cleaned = _FTS_NON_BAREWORD.sub(" ", q or "")
    tokens = [t.lower() for t in cleaned.split() if t and any(ch.isalnum() for ch in t)]
    content = [t for t in tokens if t not in _STOPWORDS]
    return content or tokens


def looks_like_question(q: str) -> bool:
    """Natural-language question vs keyword list.

    True when the raw query ends with "?", opens with a question word, or
    runs longer than _QUESTION_MIN_TOKENS raw tokens. Quoted phrases are
    never questions (the user asked for exact wording).
    """
    raw = (q or "").strip()
    if not raw or _is_quoted_phrase(raw):
        return False
    if raw.endswith("?"):
        return True
    raw_tokens = [t.lower() for t in _FTS_NON_BAREWORD.sub(" ", raw).split() if t]
    if not raw_tokens:
        return False
    if raw_tokens[0] in _QUESTION_OPENERS:
        return True
    return len(raw_tokens) > _QUESTION_MIN_TOKENS


def _sanitize_query(q: str) -> str:
    """Strip FTS5 syntax characters that would crash the query.

    Users type natural-language phrases. Quoted-phrase support is
    re-added downstream by detecting "..." wrapping; everything else
    is treated as bag-of-words AND search over the content tokens
    (stopwords + punctuation-only tokens removed — see query_tokens).
    """
    q = q.strip()
    if not q:
        return ""
    # If the user wrapped the whole thing in quotes, treat it as a
    # phrase query (FTS5's native quoted-phrase syntax).
    if _is_quoted_phrase(q):
        inner = q[1:-1].replace('"', "")
        return f'"{inner}"'
    return " ".join(query_tokens(q))


def _or_query(q: str) -> str:
    """OR-joined form of the same content tokens (the recall fallback)."""
    tokens = query_tokens(q)
    return " OR ".join(tokens) if len(tokens) > 1 else " ".join(tokens)


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
    """The single FTS5-chosen best-column snippet, HTML-safe.

    FTS5's snippet(..., -1, ...) selects the column with the most phrase
    matches itself, so there is only one window to consider. It may lack
    a highlight marker when the match sat in an UNINDEXED filter column
    or stemming put the boundary just outside the window — still
    returned so the card shows context.
    """
    return _safe_snippet(row[_SNIPPET_COL] or "")


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

    def _run(match_expr: str, exclude_ids: set[int], n: int) -> list[sqlite3.Row]:
        return _run_fts(conn, match_expr, query, meeting_body=meeting_body,
                        speaker=speaker, date_after=date_after,
                        date_before=date_before, limit=n,
                        exclude_ids=exclude_ids)

    # Precision pass: every content token must be present (or the exact
    # phrase, for quoted queries). This is the whole answer for short
    # keyword queries.
    rows = list(_run(fts_query, set(), int(limit)))

    # Recall fallback for natural-language questions / long queries: rows
    # that carry ALL the terms rank first (that IS the "phrase bonus"), then
    # BM25-ranked rows matching ANY content term fill the remaining slots.
    # Under the old implicit-AND a 12-word question needed a clip to contain
    # all 12 words, which is why questions typed into the search box mostly
    # returned nothing.
    if len(rows) < int(limit) and looks_like_question(query):
        or_query = _or_query(query)
        if or_query and or_query != fts_query:
            have = {int(r["clip_id"]) for r in rows}
            rows += list(_run(or_query, have, int(limit) - len(rows)))

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


def _run_fts(
    conn: sqlite3.Connection,
    match_expr: str,
    raw_query: str,
    *,
    meeting_body: Optional[str],
    speaker: Optional[str],
    date_after: Optional[str],
    date_before: Optional[str],
    limit: int,
    exclude_ids: set[int],
) -> list[sqlite3.Row]:
    """One BM25-ranked FTS5 MATCH with the filter WHERE clauses applied."""
    weights = ", ".join(str(w) for w in BM25_WEIGHTS)
    # NOTE on snippet() perf: FTS5 does NOT compute the snippet() window for
    # every matching row. With `ORDER BY bm25(...) LIMIT N`, SQLite's sorter
    # keeps only the ranking key + rowid and evaluates the auxiliary snippet()
    # column lazily for just the top-N returned rows (measured on the uv-bundled
    # SQLite this serves under: ~9 ms for LIMIT 25 vs ~516 ms to snippet all
    # ~4k matches — a 57x gap that proves the deferral). An earlier "rank in a
    # subquery, snippet only the survivors" rewrite measured ~54% SLOWER here
    # because it adds a second MATCH scan + a rowid-IN materialization for no
    # gain — so this single-pass shape is deliberately kept. Column -1 lets
    # FTS5 choose the best-matching column (we used to build five windows).
    sql = f"""
        SELECT
            clip_id,
            date,
            meeting_body,
            speakers_csv,
            transcript_words,
            transcript_source,
            title AS title_text,
            snippet(clips_fts, -1, ?, ?, '…', 24) AS {_SNIPPET_COL},
            bm25(clips_fts, {weights}) AS score
        FROM clips_fts
        WHERE clips_fts MATCH ?
    """
    params: list[object] = [_MARK_OPEN, _MARK_CLOSE, match_expr]
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
    if exclude_ids:
        placeholders = ", ".join("?" for _ in exclude_ids)
        sql += f" AND clip_id NOT IN ({placeholders})"
        params.extend(int(i) for i in exclude_ids)

    sql += " ORDER BY score LIMIT ?"
    params.append(int(limit))

    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        # A malformed query made it past sanitization. Don't stay silent —
        # a zero-result return here is indistinguishable from a real
        # coverage gap in telemetry. Log it and re-raise so the caller
        # records status="error" (server + MCP tool both catch and tag it).
        logger.error("FTS query failed after sanitization: %r -> %r: %s",
                     raw_query, match_expr, e)
        raise


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
    # Escape LIKE wildcards in the user prefix so a typed "%" / "_" matches
    # those literal characters instead of "anything". Backslash first so we
    # don't double-escape our own escape char.
    escaped = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        r"""
        SELECT term, kind, weight
        FROM suggest_terms
        WHERE term LIKE ? ESCAPE '\' COLLATE NOCASE
        ORDER BY weight DESC, term COLLATE NOCASE
        LIMIT ?
        """,
        (f"{escaped}%", int(limit)),
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
