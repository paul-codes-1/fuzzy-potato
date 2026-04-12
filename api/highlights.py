"""Meeting highlights generator - identifies key moments from extracted facts and transcripts.

Produces a "SportsCenter highlights reel" of each meeting: contentious votes, large financial
items, impactful public comments, first readings, appointments, and recognitions. Each highlight
is scored by importance (1-10) and stored in SQLite for fast retrieval.
"""

import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Highlight data model
# ---------------------------------------------------------------------------

HIGHLIGHT_TYPES = {
    "contentious_vote",
    "unanimous_vote",
    "financial",
    "public_comment",
    "first_reading",
    "appointment",
    "recognition",
    "agenda_item",
}


@dataclass
class Highlight:
    id: str
    clip_id: int
    type: str
    title: str
    description: str
    start_time: Optional[float]
    end_time: Optional[float]
    importance_score: int  # 1-10
    meeting_date: str
    meeting_body: str
    meeting_title: str
    metadata: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        return d


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS highlights (
    id TEXT PRIMARY KEY,
    clip_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    start_time REAL,
    end_time REAL,
    importance_score INTEGER NOT NULL,
    meeting_date TEXT NOT NULL,
    meeting_body TEXT NOT NULL,
    meeting_title TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_highlights_clip ON highlights(clip_id);",
    "CREATE INDEX IF NOT EXISTS idx_highlights_type ON highlights(type);",
    "CREATE INDEX IF NOT EXISTS idx_highlights_date ON highlights(meeting_date);",
    "CREATE INDEX IF NOT EXISTS idx_highlights_score ON highlights(importance_score DESC);",
]


class HighlightsStore:
    """SQLite-backed storage for meeting highlights."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            for idx_sql in _CREATE_INDEXES:
                conn.execute(idx_sql)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_highlights(self, highlights: list[Highlight]):
        """Upsert a batch of highlights."""
        if not highlights:
            return
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO highlights
                   (id, clip_id, type, title, description, start_time, end_time,
                    importance_score, meeting_date, meeting_body, meeting_title,
                    metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        h.id, h.clip_id, h.type, h.title, h.description,
                        h.start_time, h.end_time, h.importance_score,
                        h.meeting_date, h.meeting_body, h.meeting_title,
                        json.dumps(h.metadata) if isinstance(h.metadata, dict) else h.metadata,
                        h.created_at,
                    )
                    for h in highlights
                ],
            )
            conn.commit()

    def delete_for_clip(self, clip_id: int):
        """Remove all highlights for a clip (before regenerating)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM highlights WHERE clip_id = ?", (clip_id,))
            conn.commit()

    def _row_to_highlight(self, row: sqlite3.Row) -> Highlight:
        d = dict(row)
        meta = d.pop("metadata", "{}")
        if isinstance(meta, str):
            meta = json.loads(meta)
        return Highlight(**d, metadata=meta)

    def get_for_clip(self, clip_id: int, limit: int = 50) -> list[Highlight]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM highlights WHERE clip_id = ? ORDER BY importance_score DESC, start_time ASC LIMIT ?",
                (clip_id, limit),
            ).fetchall()
        return [self._row_to_highlight(r) for r in rows]

    def get_top_for_clip(self, clip_id: int, top_n: int = 5) -> list[Highlight]:
        """Top N moments for a specific meeting."""
        return self.get_for_clip(clip_id, limit=top_n)

    def get_recent(self, limit: int = 20, offset: int = 0) -> list[Highlight]:
        """Top highlights across recent meetings, sorted by date then importance."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM highlights
                   ORDER BY meeting_date DESC, importance_score DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [self._row_to_highlight(r) for r in rows]

    def get_trending(self, days: int = 30, limit: int = 20) -> list[Highlight]:
        """Most important moments from the last N days."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM highlights
                   WHERE meeting_date >= ?
                   ORDER BY importance_score DESC, meeting_date DESC
                   LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [self._row_to_highlight(r) for r in rows]

    def get_by_type(self, highlight_type: str, limit: int = 50, offset: int = 0) -> list[Highlight]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM highlights
                   WHERE type = ?
                   ORDER BY meeting_date DESC, importance_score DESC
                   LIMIT ? OFFSET ?""",
                (highlight_type, limit, offset),
            ).fetchall()
        return [self._row_to_highlight(r) for r in rows]

    def count_by_type(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) as cnt FROM highlights GROUP BY type"
            ).fetchall()
        return {r["type"]: r["cnt"] for r in rows}

    def count_for_clip(self, clip_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM highlights WHERE clip_id = ?",
                (clip_id,),
            ).fetchone()
        return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Singleton store
# ---------------------------------------------------------------------------

_store: Optional[HighlightsStore] = None


def init_highlights(output_dir: str):
    global _store
    db_path = os.path.join(output_dir, "highlights.db")
    _store = HighlightsStore(db_path)


def get_highlights_store() -> HighlightsStore:
    if _store is None:
        raise RuntimeError("Highlights store not initialized. Call init_highlights() first.")
    return _store


# ---------------------------------------------------------------------------
# Highlight generation logic
# ---------------------------------------------------------------------------

def _parse_timestamp(ts_str: Optional[str]) -> Optional[float]:
    """Parse 'MM:SS' or 'HH:MM:SS' timestamp into seconds."""
    if not ts_str:
        return None
    parts = ts_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        return None
    return None


def _parse_dollar_amount(amount_str: Optional[str]) -> float:
    """Parse a dollar amount string like '$1,234,567' into a float."""
    if not amount_str:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", amount_str)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class HighlightsGenerator:
    """Generates highlights from extracted_facts.json and transcript segments.

    Identifies key moments: contentious votes, large financial items, public
    comments, first readings, appointments, and recognitions. Scores each
    highlight by importance.
    """

    def __init__(self, financial_threshold: float = 500_000):
        self.financial_threshold = financial_threshold

    def generate(
        self,
        clip_id: int,
        metadata: dict,
        extracted_facts: Optional[dict] = None,
        transcript_segments: Optional[list[dict]] = None,
    ) -> list[Highlight]:
        """Generate highlights for a single meeting clip.

        Args:
            clip_id: Granicus clip ID
            metadata: Clip metadata (date, meeting_body, title, etc.)
            extracted_facts: Parsed extracted_facts.json data
            transcript_segments: Parsed transcript segments JSON

        Returns:
            List of Highlight objects sorted by importance_score descending
        """
        meeting_date = metadata.get("date", "")
        meeting_body = metadata.get("meeting_body", "")
        meeting_title = metadata.get("title", "")
        now = datetime.now(timezone.utc).isoformat()

        highlights: list[Highlight] = []

        if extracted_facts:
            highlights.extend(
                self._extract_vote_highlights(
                    clip_id, extracted_facts, meeting_date, meeting_body, meeting_title, now
                )
            )
            highlights.extend(
                self._extract_financial_highlights(
                    clip_id, extracted_facts, meeting_date, meeting_body, meeting_title, now
                )
            )
            highlights.extend(
                self._extract_public_comment_highlights(
                    clip_id, extracted_facts, meeting_date, meeting_body, meeting_title, now
                )
            )
            highlights.extend(
                self._extract_agenda_highlights(
                    clip_id, extracted_facts, meeting_date, meeting_body, meeting_title, now
                )
            )
            highlights.extend(
                self._extract_appointment_highlights(
                    clip_id, extracted_facts, meeting_date, meeting_body, meeting_title, now
                )
            )
            highlights.extend(
                self._extract_contentious_highlights(
                    clip_id, extracted_facts, meeting_date, meeting_body, meeting_title, now
                )
            )

        # Sort by importance descending, then by start_time
        highlights.sort(key=lambda h: (-h.importance_score, h.start_time or 0))

        return highlights

    def generate_top_moments(
        self,
        clip_id: int,
        metadata: dict,
        extracted_facts: Optional[dict] = None,
        transcript_segments: Optional[list[dict]] = None,
        top_n: int = 5,
    ) -> list[Highlight]:
        """Generate and return only the top N highlights for a meeting."""
        all_highlights = self.generate(clip_id, metadata, extracted_facts, transcript_segments)
        return all_highlights[:top_n]

    # -----------------------------------------------------------------------
    # Vote highlights
    # -----------------------------------------------------------------------

    def _extract_vote_highlights(
        self, clip_id, facts, date, body, title, now
    ) -> list[Highlight]:
        highlights = []
        votes = facts.get("motions_and_votes", [])

        for i, vote in enumerate(votes):
            ayes = vote.get("ayes", 0) or 0
            nays = vote.get("nays", 0) or 0
            total = ayes + nays
            outcome = (vote.get("outcome") or "").lower()
            description = vote.get("description", "")
            identifier = vote.get("identifier", "")
            start_time = _parse_timestamp(vote.get("transcript_approx_time"))

            # Determine if this is contentious (close margins or nay votes)
            is_contentious = nays > 0 or outcome in ("failed", "tabled", "deferred")
            is_close = total > 0 and nays > 0 and (nays / total) >= 0.25

            if is_close or outcome in ("failed", "tabled"):
                # Contentious: high importance
                importance = 9 if outcome == "failed" else (8 if is_close else 7)
                h_type = "contentious_vote"
                vote_detail = f"{ayes}-{nays}"
                if vote.get("votes_against"):
                    opposed = ", ".join(vote["votes_against"])
                    h_title = f"{identifier or 'Motion'}: {outcome.title()} ({vote_detail}) - Opposed: {opposed}"
                else:
                    h_title = f"{identifier or 'Motion'}: {outcome.title()} ({vote_detail})"
            elif is_contentious:
                importance = 6
                h_type = "contentious_vote"
                h_title = f"{identifier or 'Motion'}: {outcome.title()} ({ayes}-{nays})"
            else:
                # Unanimous votes are lower importance unless it's something notable
                importance = 3
                h_type = "unanimous_vote"
                h_title = f"{identifier or 'Motion'}: Passed unanimously"

            # Check for first reading (boost importance)
            desc_lower = description.lower()
            if "first reading" in desc_lower or "first read" in desc_lower:
                h_type = "first_reading"
                importance = max(importance, 6)
                h_title = f"First Reading: {identifier or description[:60]}"

            highlights.append(Highlight(
                id=f"{clip_id}_vote_{i}",
                clip_id=clip_id,
                type=h_type,
                title=h_title,
                description=description,
                start_time=start_time,
                end_time=start_time + 120 if start_time else None,
                importance_score=importance,
                meeting_date=date,
                meeting_body=body,
                meeting_title=title,
                metadata={
                    "identifier": identifier,
                    "ayes": ayes,
                    "nays": nays,
                    "outcome": outcome,
                    "motion_by": vote.get("motion_by"),
                    "votes_for": vote.get("votes_for", []),
                    "votes_against": vote.get("votes_against", []),
                },
                created_at=now,
            ))

        return highlights

    # -----------------------------------------------------------------------
    # Financial highlights
    # -----------------------------------------------------------------------

    def _extract_financial_highlights(
        self, clip_id, facts, date, body, title, now
    ) -> list[Highlight]:
        highlights = []
        financial_items = facts.get("financial_items", [])

        for i, item in enumerate(financial_items):
            amount = _parse_dollar_amount(item.get("amount"))
            amount_str = item.get("amount", "")
            description = item.get("description", "")
            identifier = item.get("identifier")
            item_type = item.get("type", "")
            vendor = item.get("vendor_or_recipient", "")

            if amount < self.financial_threshold:
                continue

            # Scale importance by amount
            if amount >= 10_000_000:
                importance = 9
            elif amount >= 5_000_000:
                importance = 8
            elif amount >= 1_000_000:
                importance = 7
            else:
                importance = 6

            h_title = f"{amount_str}: {description[:80]}"
            if vendor:
                h_title += f" ({vendor})"

            highlights.append(Highlight(
                id=f"{clip_id}_financial_{i}",
                clip_id=clip_id,
                type="financial",
                title=h_title,
                description=f"{description}. Type: {item_type}. {f'Vendor: {vendor}' if vendor else ''}".strip(),
                start_time=None,
                end_time=None,
                importance_score=importance,
                meeting_date=date,
                meeting_body=body,
                meeting_title=title,
                metadata={
                    "amount": amount_str,
                    "amount_numeric": amount,
                    "identifier": identifier,
                    "type": item_type,
                    "vendor": vendor,
                },
                created_at=now,
            ))

        return highlights

    # -----------------------------------------------------------------------
    # Public comment highlights
    # -----------------------------------------------------------------------

    def _extract_public_comment_highlights(
        self, clip_id, facts, date, body, title, now
    ) -> list[Highlight]:
        highlights = []
        comments = facts.get("public_comments", [])

        # Emotional / impactful keyword detection
        impactful_keywords = {
            "opposed", "concerned", "against", "demand", "outraged", "safety",
            "dangerous", "children", "families", "emergency", "health",
            "discrimination", "equity", "justice", "accountability",
            "transparency", "corruption", "violation", "illegal",
        }

        for i, comment in enumerate(comments):
            speaker = comment.get("speaker", "Unknown")
            topic = comment.get("topic", "")
            summary = comment.get("summary", "")
            start_time = _parse_timestamp(comment.get("transcript_approx_time"))

            # Score based on keyword presence
            text_lower = f"{topic} {summary}".lower()
            keyword_hits = sum(1 for kw in impactful_keywords if kw in text_lower)

            if keyword_hits >= 3:
                importance = 7
            elif keyword_hits >= 1:
                importance = 5
            else:
                importance = 4

            highlights.append(Highlight(
                id=f"{clip_id}_comment_{i}",
                clip_id=clip_id,
                type="public_comment",
                title=f"{speaker} on {topic}" if topic else f"Public comment by {speaker}",
                description=summary,
                start_time=start_time,
                end_time=start_time + 180 if start_time else None,
                importance_score=importance,
                meeting_date=date,
                meeting_body=body,
                meeting_title=title,
                metadata={
                    "speaker": speaker,
                    "topic": topic,
                },
                created_at=now,
            ))

        return highlights

    # -----------------------------------------------------------------------
    # Agenda item highlights (first readings, notable items)
    # -----------------------------------------------------------------------

    def _extract_agenda_highlights(
        self, clip_id, facts, date, body, title, now
    ) -> list[Highlight]:
        highlights = []
        agenda_items = facts.get("agenda_items", [])

        for i, item in enumerate(agenda_items):
            item_title = item.get("title", "")
            identifier = item.get("identifier", "")
            item_type = (item.get("type") or "").lower()
            summary = item.get("summary", "")
            outcome = item.get("outcome", "")
            start_time = _parse_timestamp(item.get("transcript_approx_time"))

            # Check for first readings
            title_lower = item_title.lower()
            if "first reading" in title_lower or "first read" in title_lower:
                highlights.append(Highlight(
                    id=f"{clip_id}_agenda_{i}",
                    clip_id=clip_id,
                    type="first_reading",
                    title=f"First Reading: {identifier or item_title[:60]}",
                    description=summary or item_title,
                    start_time=start_time,
                    end_time=start_time + 120 if start_time else None,
                    importance_score=6,
                    meeting_date=date,
                    meeting_body=body,
                    meeting_title=title,
                    metadata={"identifier": identifier, "item_type": item_type},
                    created_at=now,
                ))

            # Check for recognition / proclamation items
            if item_type in ("proclamation", "recognition", "commendation"):
                highlights.append(Highlight(
                    id=f"{clip_id}_recognition_{i}",
                    clip_id=clip_id,
                    type="recognition",
                    title=f"Recognition: {item_title[:80]}",
                    description=summary or item_title,
                    start_time=start_time,
                    end_time=start_time + 120 if start_time else None,
                    importance_score=4,
                    meeting_date=date,
                    meeting_body=body,
                    meeting_title=title,
                    metadata={"identifier": identifier, "item_type": item_type},
                    created_at=now,
                ))

        return highlights

    # -----------------------------------------------------------------------
    # Appointment highlights
    # -----------------------------------------------------------------------

    def _extract_appointment_highlights(
        self, clip_id, facts, date, body, title, now
    ) -> list[Highlight]:
        highlights = []
        appointments = facts.get("appointments", [])

        for i, appt in enumerate(appointments):
            if isinstance(appt, dict):
                name = appt.get("name", appt.get("appointee", ""))
                position = appt.get("position", appt.get("board", ""))
                desc = appt.get("details", f"{name} appointed to {position}")
            elif isinstance(appt, str):
                name = appt
                position = ""
                desc = appt
            else:
                continue

            highlights.append(Highlight(
                id=f"{clip_id}_appointment_{i}",
                clip_id=clip_id,
                type="appointment",
                title=f"Appointment: {name}" + (f" to {position}" if position else ""),
                description=desc,
                start_time=None,
                end_time=None,
                importance_score=4,
                meeting_date=date,
                meeting_body=body,
                meeting_title=title,
                metadata={"name": name, "position": position},
                created_at=now,
            ))

        return highlights

    # -----------------------------------------------------------------------
    # Contentious / contested item highlights
    # -----------------------------------------------------------------------

    def _extract_contentious_highlights(
        self, clip_id, facts, date, body, title, now
    ) -> list[Highlight]:
        highlights = []
        contentious = facts.get("contentious_items", [])

        for i, item in enumerate(contentious):
            topic = item.get("topic", "Contested item")
            details = item.get("details", "")

            highlights.append(Highlight(
                id=f"{clip_id}_contentious_{i}",
                clip_id=clip_id,
                type="contentious_vote",
                title=f"Contested: {topic[:80]}",
                description=details,
                start_time=None,
                end_time=None,
                importance_score=8,
                meeting_date=date,
                meeting_body=body,
                meeting_title=title,
                metadata={"topic": topic},
                created_at=now,
            ))

        return highlights


# ---------------------------------------------------------------------------
# High-level API: generate and store highlights for a clip
# ---------------------------------------------------------------------------

def generate_and_store_highlights(
    clip_id: int,
    output_dir: str,
    financial_threshold: float = 500_000,
) -> list[Highlight]:
    """Generate highlights for a clip from its output files, store in DB.

    Reads metadata.json, extracted_facts.json, and transcript segments
    from the clip's output directory. Generates highlights and saves to
    the SQLite highlights database.

    Returns the list of generated highlights.
    """
    clip_dir = Path(output_dir) / "clips" / str(clip_id)
    metadata_path = clip_dir / "metadata.json"

    if not metadata_path.exists():
        logger.warning("No metadata.json for clip %d, skipping highlights", clip_id)
        return []

    with open(metadata_path) as f:
        metadata = json.load(f)

    files = metadata.get("files", {})

    # Load extracted facts if available
    extracted_facts = None
    facts_file = files.get("extracted_facts")
    if facts_file:
        facts_path = clip_dir / facts_file
        if facts_path.exists():
            with open(facts_path) as f:
                extracted_facts = json.load(f)

    # Load transcript segments if available
    transcript_segments = None
    segments_file = files.get("transcript_segments")
    if segments_file:
        segments_path = clip_dir / segments_file
        if segments_path.exists():
            with open(segments_path) as f:
                transcript_segments = json.load(f)

    # Generate highlights
    generator = HighlightsGenerator(financial_threshold=financial_threshold)
    highlights = generator.generate(
        clip_id=clip_id,
        metadata=metadata,
        extracted_facts=extracted_facts,
        transcript_segments=transcript_segments,
    )

    if not highlights:
        logger.info("No highlights generated for clip %d", clip_id)
        return []

    # Store in database
    store = get_highlights_store()
    store.delete_for_clip(clip_id)
    store.save_highlights(highlights)
    logger.info("Stored %d highlights for clip %d", len(highlights), clip_id)

    return highlights


def generate_all_highlights(output_dir: str, financial_threshold: float = 500_000) -> int:
    """Generate highlights for all clips in the output directory.

    Returns count of clips processed.
    """
    clips_dir = Path(output_dir) / "clips"
    if not clips_dir.is_dir():
        logger.warning("No clips directory at %s", clips_dir)
        return 0

    count = 0
    for name in sorted(clips_dir.iterdir()):
        if not name.is_dir():
            continue
        try:
            clip_id = int(name.name)
        except ValueError:
            continue

        try:
            highlights = generate_and_store_highlights(clip_id, output_dir, financial_threshold)
            if highlights:
                count += 1
        except Exception as e:
            logger.error("Failed to generate highlights for clip %d: %s", clip_id, e)

    logger.info("Generated highlights for %d clips", count)
    return count
