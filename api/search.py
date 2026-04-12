"""Full-text search engine backed by SQLite FTS5.

Indexes meeting titles, transcript text, vote descriptions, financial items,
public comment topics, speaker names, and agenda items from existing clip data.
Supports categorized search results, autocomplete, and incremental updates.
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

RESULT_TYPES = {"meeting", "vote", "financial", "speaker", "topic", "agenda_item"}


@dataclass
class SearchResult:
    type: str  # meeting | vote | financial | speaker | topic | agenda_item
    clip_id: int
    title: str
    snippet: str
    date: str = ""
    meeting_body: str = ""
    score: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "clip_id": self.clip_id,
            "title": self.title,
            "snippet": self.snippet,
            "date": self.date,
            "meeting_body": self.meeting_body,
            "score": self.score,
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class AutocompleteItem:
    text: str
    category: str  # meeting_body | speaker | topic | recent
    clip_id: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"text": self.text, "category": self.category}
        if self.clip_id is not None:
            d["clip_id"] = self.clip_id
        return d


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
-- Core searchable content (one row per searchable item)
CREATE TABLE IF NOT EXISTS search_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,        -- meeting | vote | financial | speaker | topic | agenda_item
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    meeting_body TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

-- FTS5 virtual table over search_items
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    title,
    body,
    content='search_items',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync with content table
CREATE TRIGGER IF NOT EXISTS search_items_ai AFTER INSERT ON search_items BEGIN
    INSERT INTO search_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS search_items_ad AFTER DELETE ON search_items BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body)
        VALUES('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS search_items_au AFTER UPDATE ON search_items BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body)
        VALUES('delete', old.id, old.title, old.body);
    INSERT INTO search_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

-- Autocomplete terms (deduplicated)
CREATE TABLE IF NOT EXISTS autocomplete_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    category TEXT NOT NULL,         -- meeting_body | speaker | topic
    clip_id INTEGER,
    UNIQUE(term, category)
);

-- Track which clips have been indexed
CREATE TABLE IF NOT EXISTS indexed_clips (
    clip_id INTEGER PRIMARY KEY,
    indexed_at TEXT NOT NULL
);

-- Recent searches
CREATE TABLE IF NOT EXISTS recent_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    searched_at TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_search_items_clip ON search_items(clip_id);
CREATE INDEX IF NOT EXISTS idx_search_items_type ON search_items(item_type);
CREATE INDEX IF NOT EXISTS idx_recent_searches_tenant ON recent_searches(tenant_id, searched_at);
"""


# ---------------------------------------------------------------------------
# SearchEngine
# ---------------------------------------------------------------------------

class SearchEngine:
    """SQLite FTS5-backed search engine for meeting data."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def is_clip_indexed(self, clip_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM indexed_clips WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row is not None

    def index_clip(self, clip_dir: str, clip_id: int) -> int:
        """Index a single clip from its output directory. Returns number of items indexed."""
        clip_path = Path(clip_dir)
        count = 0

        # Remove existing entries for this clip (supports re-indexing)
        self._conn.execute("DELETE FROM search_items WHERE clip_id = ?", (clip_id,))

        # Load metadata
        meta_path = clip_path / "metadata.json"
        if not meta_path.exists():
            logger.warning("No metadata.json for clip %s, skipping", clip_id)
            return 0

        with open(meta_path) as f:
            meta = json.load(f)

        date = meta.get("date", "")
        meeting_body = meta.get("meeting_body", "")
        title = meta.get("title", "")
        topics = meta.get("topics", [])

        # 1. Index the meeting itself
        topic_text = ", ".join(topics) if topics else ""
        self._insert_item(
            clip_id=clip_id,
            item_type="meeting",
            title=title,
            body=topic_text,
            date=date,
            meeting_body=meeting_body,
            metadata={"topics": topics, "url": meta.get("url", "")},
        )
        count += 1

        # 2. Index topics as separate items for filtering
        for topic in topics:
            self._insert_item(
                clip_id=clip_id,
                item_type="topic",
                title=topic,
                body=f"{topic} discussed in {title}",
                date=date,
                meeting_body=meeting_body,
            )
            self._add_autocomplete(topic, "topic", clip_id)
            count += 1

        # 3. Index extracted facts
        facts_path = clip_path / "extracted_facts.json"
        if facts_path.exists():
            with open(facts_path) as f:
                facts = json.load(f)
            count += self._index_facts(facts, clip_id, date, meeting_body, title)

        # 4. Index transcript text (first ~2000 words for search)
        transcript_files = list(clip_path.glob("transcript_*.txt"))
        if transcript_files:
            with open(transcript_files[0]) as f:
                transcript_text = f.read()
            # Truncate for indexing (FTS handles large text but we want
            # reasonable snippet generation)
            words = transcript_text.split()
            truncated = " ".join(words[:2000])
            self._insert_item(
                clip_id=clip_id,
                item_type="meeting",
                title=f"Transcript: {title}",
                body=truncated,
                date=date,
                meeting_body=meeting_body,
                metadata={"source": "transcript"},
            )
            count += 1

        # Add autocomplete terms
        self._add_autocomplete(meeting_body, "meeting_body") if meeting_body else None

        # Mark clip as indexed
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._conn.execute(
            "INSERT OR REPLACE INTO indexed_clips (clip_id, indexed_at) VALUES (?, ?)",
            (clip_id, now),
        )
        self._conn.commit()
        return count

    def _index_facts(
        self, facts: dict, clip_id: int, date: str, meeting_body: str, title: str
    ) -> int:
        count = 0

        # Votes / Motions
        for vote in facts.get("motions_and_votes", []):
            desc = vote.get("description", "")
            identifier = vote.get("identifier", "") or ""
            outcome = vote.get("outcome", "")
            motion_by = vote.get("motion_by", "")
            second_by = vote.get("second_by", "")
            vote_title = identifier if identifier else desc[:80]
            body_parts = [desc]
            if outcome:
                body_parts.append(f"Outcome: {outcome}")
            if motion_by:
                body_parts.append(f"Motion by {motion_by}")
            if second_by:
                body_parts.append(f"Seconded by {second_by}")
            votes_for = vote.get("votes_for", [])
            votes_against = vote.get("votes_against", [])
            if votes_for:
                body_parts.append(f"Ayes: {', '.join(votes_for)}")
            if votes_against:
                body_parts.append(f"Nays: {', '.join(votes_against)}")

            self._insert_item(
                clip_id=clip_id,
                item_type="vote",
                title=vote_title,
                body=". ".join(body_parts),
                date=date,
                meeting_body=meeting_body,
                metadata={
                    "outcome": outcome,
                    "ayes": vote.get("ayes"),
                    "nays": vote.get("nays"),
                    "timestamp": vote.get("transcript_approx_time", ""),
                },
            )
            count += 1

            # Index individual voters as speakers
            all_voters = set((votes_for or []) + (votes_against or []))
            if motion_by:
                all_voters.add(motion_by)
            if second_by:
                all_voters.add(second_by)
            for name in all_voters:
                self._add_autocomplete(name, "speaker", clip_id)

        # Financial items
        for item in facts.get("financial_items", []):
            desc = item.get("description", "")
            amount = item.get("amount", "")
            vendor = item.get("vendor_or_recipient", "") or ""
            fin_type = item.get("type", "") or ""
            body_parts = [desc]
            if amount:
                body_parts.append(f"Amount: {amount}")
            if vendor:
                body_parts.append(f"Vendor/Recipient: {vendor}")
            if fin_type:
                body_parts.append(f"Type: {fin_type}")

            self._insert_item(
                clip_id=clip_id,
                item_type="financial",
                title=desc[:100] if desc else "Financial item",
                body=". ".join(body_parts),
                date=date,
                meeting_body=meeting_body,
                metadata={"amount": amount, "type": fin_type, "vendor": vendor},
            )
            count += 1

        # Public comments -- index speakers
        for comment in facts.get("public_comments", []):
            speaker = comment.get("speaker", "")
            topic = comment.get("topic", "")
            summary = comment.get("summary", "")
            self._insert_item(
                clip_id=clip_id,
                item_type="speaker",
                title=speaker or "Public comment",
                body=f"{topic}. {summary}".strip(),
                date=date,
                meeting_body=meeting_body,
                metadata={
                    "role": "public_commenter",
                    "timestamp": comment.get("transcript_approx_time", ""),
                },
            )
            if speaker:
                self._add_autocomplete(speaker, "speaker", clip_id)
            count += 1

        # Agenda items
        for agenda in facts.get("agenda_items", []):
            identifier = agenda.get("identifier", "") or ""
            a_title = agenda.get("title", "")
            a_type = agenda.get("type", "") or ""
            summary = agenda.get("summary", "")
            outcome = agenda.get("outcome", "") or ""
            full_title = f"{identifier} {a_title}".strip() if identifier else a_title
            body_parts = [summary]
            if outcome:
                body_parts.append(f"Outcome: {outcome}")
            if a_type:
                body_parts.append(f"Type: {a_type}")

            self._insert_item(
                clip_id=clip_id,
                item_type="agenda_item",
                title=full_title,
                body=". ".join(body_parts),
                date=date,
                meeting_body=meeting_body,
                metadata={
                    "outcome": outcome,
                    "type": a_type,
                    "timestamp": agenda.get("transcript_approx_time", ""),
                },
            )
            count += 1

        # Attendance -- index as speakers
        attendance = facts.get("attendance", {})
        for name in attendance.get("present", []):
            self._add_autocomplete(name, "speaker", clip_id)

        # Appointments
        for appt in facts.get("appointments", []):
            name = appt.get("name", "") or appt.get("appointee", "")
            if name:
                self._add_autocomplete(name, "speaker", clip_id)

        return count

    def _insert_item(
        self,
        clip_id: int,
        item_type: str,
        title: str,
        body: str,
        date: str,
        meeting_body: str,
        metadata: dict | None = None,
    ):
        self._conn.execute(
            """INSERT INTO search_items
               (clip_id, item_type, title, body, date, meeting_body, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (clip_id, item_type, title, body, date, meeting_body, json.dumps(metadata or {})),
        )

    def _add_autocomplete(self, term: str, category: str, clip_id: int | None = None):
        if not term or not term.strip():
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO autocomplete_terms (term, category, clip_id) VALUES (?, ?, ?)",
            (term.strip(), category, clip_id),
        )

    # ------------------------------------------------------------------
    # Build index from all clips
    # ------------------------------------------------------------------

    def build_index(self, output_dir: str, force: bool = False) -> dict:
        """Walk output_dir/clips/ and index every clip. Returns stats."""
        clips_dir = Path(output_dir) / "clips"
        if not clips_dir.exists():
            logger.warning("Clips directory not found: %s", clips_dir)
            return {"indexed": 0, "skipped": 0, "errors": 0}

        indexed = 0
        skipped = 0
        errors = 0

        for clip_dir in sorted(clips_dir.iterdir()):
            if not clip_dir.is_dir():
                continue
            try:
                clip_id = int(clip_dir.name)
            except ValueError:
                continue

            if not force and self.is_clip_indexed(clip_id):
                skipped += 1
                continue

            try:
                n = self.index_clip(str(clip_dir), clip_id)
                if n > 0:
                    indexed += 1
                    logger.info("Indexed clip %d (%d items)", clip_id, n)
                else:
                    skipped += 1
            except Exception:
                errors += 1
                logger.error("Error indexing clip %d", clip_id, exc_info=True)

        return {"indexed": indexed, "skipped": skipped, "errors": errors}

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        type_filter: str = "all",
        limit: int = 20,
        offset: int = 0,
        meeting_body: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        """Full-text search with optional type and metadata filters.

        Returns dict with keys: results (list), total (int), query, took_ms.
        Results are ranked: exact title match > title match > body match.
        """
        start = time.perf_counter()

        if not query or not query.strip():
            return {"results": [], "total": 0, "query": query, "took_ms": 0}

        clean_q = query.strip()

        # Build FTS5 match expression. We boost title matches by weighting.
        # FTS5 bm25() returns negative scores; more negative = better match.
        # We use column weights: title=10.0, body=1.0
        fts_query = self._sanitize_fts_query(clean_q)

        # Build WHERE conditions for the content table
        conditions = []
        params = []

        if type_filter and type_filter != "all" and type_filter in RESULT_TYPES:
            conditions.append("s.item_type = ?")
            params.append(type_filter)

        if meeting_body:
            conditions.append("s.meeting_body = ?")
            params.append(meeting_body)

        if date_after:
            conditions.append("s.date >= ?")
            params.append(date_after)

        if date_before:
            conditions.append("s.date <= ?")
            params.append(date_before)

        where_clause = ""
        if conditions:
            where_clause = "AND " + " AND ".join(conditions)

        # Query: join FTS results with content table for filtering and metadata
        sql = f"""
            SELECT s.*, bm25(search_fts, 10.0, 1.0) AS rank,
                   snippet(search_fts, 0, '<mark>', '</mark>', '...', 32) AS title_snippet,
                   snippet(search_fts, 1, '<mark>', '</mark>', '...', 48) AS body_snippet
            FROM search_fts
            JOIN search_items s ON s.id = search_fts.rowid
            WHERE search_fts MATCH ?
            {where_clause}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        all_params = [fts_query] + params + [limit, offset]

        try:
            rows = self._conn.execute(sql, all_params).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("FTS query failed for '%s': %s", clean_q, e)
            return {"results": [], "total": 0, "query": query, "took_ms": 0}

        # Count total matches (without LIMIT)
        count_sql = f"""
            SELECT COUNT(*) as cnt
            FROM search_fts
            JOIN search_items s ON s.id = search_fts.rowid
            WHERE search_fts MATCH ?
            {where_clause}
        """
        count_params = [fts_query] + params
        try:
            total = self._conn.execute(count_sql, count_params).fetchone()["cnt"]
        except sqlite3.OperationalError:
            total = len(rows)

        results = []
        for row in rows:
            meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            snippet = row["body_snippet"] or row["title_snippet"] or ""
            results.append(SearchResult(
                type=row["item_type"],
                clip_id=row["clip_id"],
                title=row["title_snippet"] or row["title"],
                snippet=snippet,
                date=row["date"],
                meeting_body=row["meeting_body"],
                score=abs(row["rank"]),
                metadata=meta,
            ))

        took_ms = round((time.perf_counter() - start) * 1000, 1)

        return {
            "results": [r.to_dict() for r in results],
            "total": total,
            "query": query,
            "took_ms": took_ms,
        }

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    def autocomplete(self, prefix: str, limit: int = 10) -> list[dict]:
        """Fast prefix-based autocomplete from indexed terms and meeting titles."""
        if not prefix or len(prefix) < 1:
            return []

        clean = prefix.strip().lower()
        results = []

        # 1. Match autocomplete terms (speakers, topics, meeting bodies)
        rows = self._conn.execute(
            """SELECT DISTINCT term, category, clip_id
               FROM autocomplete_terms
               WHERE lower(term) LIKE ?
               ORDER BY term
               LIMIT ?""",
            (f"{clean}%", limit),
        ).fetchall()

        for row in rows:
            results.append(AutocompleteItem(
                text=row["term"],
                category=row["category"],
                clip_id=row["clip_id"],
            ).to_dict())

        # 2. Also match meeting titles via FTS prefix query
        remaining = limit - len(results)
        if remaining > 0:
            try:
                # FTS5 prefix query
                fts_prefix = self._sanitize_fts_query(clean) + "*"
                title_rows = self._conn.execute(
                    """SELECT DISTINCT s.title, s.clip_id, s.item_type
                       FROM search_fts
                       JOIN search_items s ON s.id = search_fts.rowid
                       WHERE search_fts MATCH ?
                       AND s.item_type = 'meeting'
                       LIMIT ?""",
                    (fts_prefix, remaining),
                ).fetchall()
                for row in title_rows:
                    results.append(AutocompleteItem(
                        text=row["title"],
                        category="meeting",
                        clip_id=row["clip_id"],
                    ).to_dict())
            except sqlite3.OperationalError:
                pass  # FTS prefix query can fail on special characters

        return results[:limit]

    # ------------------------------------------------------------------
    # Recent searches
    # ------------------------------------------------------------------

    def log_search(self, query: str, tenant_id: str, result_count: int):
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._conn.execute(
            "INSERT INTO recent_searches (query, tenant_id, searched_at, result_count) VALUES (?, ?, ?, ?)",
            (query.strip(), tenant_id, now, result_count),
        )
        self._conn.commit()

    def recent_searches(self, tenant_id: str = "", limit: int = 10) -> list[dict]:
        """Return recent unique searches, most recent first."""
        if tenant_id:
            rows = self._conn.execute(
                """SELECT query, MAX(searched_at) as last_searched, SUM(result_count) as total_results
                   FROM recent_searches
                   WHERE tenant_id = ?
                   GROUP BY query
                   ORDER BY last_searched DESC
                   LIMIT ?""",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT query, MAX(searched_at) as last_searched, SUM(result_count) as total_results
                   FROM recent_searches
                   GROUP BY query
                   ORDER BY last_searched DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        return [
            {"query": row["query"], "last_searched": row["last_searched"],
             "result_count": row["total_results"]}
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        items = self._conn.execute("SELECT COUNT(*) as cnt FROM search_items").fetchone()["cnt"]
        clips = self._conn.execute("SELECT COUNT(*) as cnt FROM indexed_clips").fetchone()["cnt"]
        terms = self._conn.execute("SELECT COUNT(*) as cnt FROM autocomplete_terms").fetchone()["cnt"]
        type_counts = {}
        for row in self._conn.execute(
            "SELECT item_type, COUNT(*) as cnt FROM search_items GROUP BY item_type"
        ).fetchall():
            type_counts[row["item_type"]] = row["cnt"]
        return {
            "total_items": items,
            "clips_indexed": clips,
            "autocomplete_terms": terms,
            "by_type": type_counts,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Sanitize user input for FTS5 MATCH expression.

        Removes special FTS5 operators that could cause syntax errors.
        Wraps multi-word queries in quotes for phrase matching when appropriate.
        """
        # Remove FTS5 special characters that could break the query
        cleaned = query.strip()
        # Remove characters that have special meaning in FTS5
        for char in ['"', "'", "(", ")", "{", "}", ":", "^", "~", "|"]:
            cleaned = cleaned.replace(char, "")
        # Collapse whitespace
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            return '""'
        # If single word, return as-is; multi-word gets OR treatment
        # (FTS5 implicit AND is default, which is what we want)
        return cleaned

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: SearchEngine | None = None


def init_search(output_dir: str) -> SearchEngine:
    """Initialize the search engine singleton."""
    global _engine
    db_path = os.path.join(output_dir, "search.db")
    _engine = SearchEngine(db_path)
    logger.info("Search engine initialized at %s", db_path)
    return _engine


def get_search_engine() -> SearchEngine:
    if _engine is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        engine = init_search(output_dir)
        try:
            if engine.stats().get("clips_indexed", 0) == 0:
                engine.build_index(output_dir)
        except Exception:
            logger.warning("Failed to build search index during lazy initialization", exc_info=True)
        return engine
    return _engine
