"""Public comment sentiment analysis and topic intelligence.

Analyzes public comments from extracted_facts.json to classify sentiment,
cluster topics, detect controversy, and track sentiment trends over time.
Uses GPT-4o-mini for classification and SQLite for storage.
"""

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def _coerce_token_count(value) -> int | None:
    """Convert a usage field to int only when it is actually numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _usage_value(usage, *names: str) -> int:
    """Read the first available integer token field from a usage object."""
    if usage is None:
        return 0
    for name in names:
        value = _coerce_token_count(getattr(usage, name, None))
        if value is not None:
            return value
    return 0


def _record_llm_cost(
    tenant_id: str | None,
    usage,
    *,
    model: str,
    operation: str,
    request_id: str | None = None,
) -> None:
    """Best-effort completion cost tracking; never raises."""
    if not tenant_id or usage is None:
        return

    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    if input_tokens <= 0 and output_tokens <= 0:
        return

    try:
        from api.cost import get_cost_tracker

        get_cost_tracker().record_llm_call(
            tenant_id=tenant_id,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            module="sentiment",
        )
    except Exception:
        logger.debug("cost tracking failed for sentiment llm call", exc_info=True)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

SENTIMENT_VALUES = {"positive", "negative", "neutral", "mixed"}


@dataclass
class CommentSentiment:
    id: str
    clip_id: str
    speaker: str
    topic: str
    sentiment: str
    confidence: float
    summary: str
    timestamp: str
    meeting_date: str
    meeting_body: str
    raw_topic: str  # original topic from extracted_facts
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "speaker": self.speaker,
            "topic": self.topic,
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "meeting_date": self.meeting_date,
            "meeting_body": self.meeting_body,
            "raw_topic": self.raw_topic,
            "created_at": self.created_at,
        }


@dataclass
class TopicSentiment:
    topic: str
    date: str
    avg_sentiment: float
    comment_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    mixed_count: int

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "date": self.date,
            "avg_sentiment": round(self.avg_sentiment, 3),
            "comment_count": self.comment_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "neutral_count": self.neutral_count,
            "mixed_count": self.mixed_count,
        }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_COMMENT_SENTIMENTS = """
CREATE TABLE IF NOT EXISTS comment_sentiments (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL,
    speaker TEXT NOT NULL,
    topic TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT NOT NULL,
    timestamp TEXT,
    meeting_date TEXT,
    meeting_body TEXT,
    raw_topic TEXT,
    created_at TEXT NOT NULL
);
"""

_CREATE_TOPIC_SENTIMENTS = """
CREATE TABLE IF NOT EXISTS topic_sentiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    date TEXT NOT NULL,
    avg_sentiment REAL NOT NULL,
    comment_count INTEGER NOT NULL DEFAULT 0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    neutral_count INTEGER NOT NULL DEFAULT 0,
    mixed_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(topic, date)
);
"""

_CREATE_TOPIC_CLUSTERS = """
CREATE TABLE IF NOT EXISTS topic_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_topic TEXT NOT NULL,
    variant TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cs_clip ON comment_sentiments(clip_id);",
    "CREATE INDEX IF NOT EXISTS idx_cs_speaker ON comment_sentiments(speaker);",
    "CREATE INDEX IF NOT EXISTS idx_cs_topic ON comment_sentiments(topic);",
    "CREATE INDEX IF NOT EXISTS idx_cs_sentiment ON comment_sentiments(sentiment);",
    "CREATE INDEX IF NOT EXISTS idx_cs_date ON comment_sentiments(meeting_date);",
    "CREATE INDEX IF NOT EXISTS idx_cs_body ON comment_sentiments(meeting_body);",
    "CREATE INDEX IF NOT EXISTS idx_ts_topic ON topic_sentiments(topic);",
    "CREATE INDEX IF NOT EXISTS idx_ts_date ON topic_sentiments(date);",
    "CREATE INDEX IF NOT EXISTS idx_tc_canonical ON topic_clusters(canonical_topic);",
]

# ---------------------------------------------------------------------------
# Sentiment numeric mapping
# ---------------------------------------------------------------------------

SENTIMENT_SCORE = {
    "positive": 1.0,
    "neutral": 0.0,
    "negative": -1.0,
    "mixed": 0.0,
}

# ---------------------------------------------------------------------------
# GPT-4o-mini prompt for sentiment classification
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """You are a sentiment analysis expert for public comments made at government meetings.

For each public comment, classify:
1. sentiment: one of "positive", "negative", "neutral", "mixed"
2. confidence: 0.0 to 1.0
3. normalized_topic: a short canonical topic label (e.g., "zoning", "short-term rentals", "public safety", "budget", "infrastructure")
4. summary: a one-sentence summary of the comment's sentiment and position

Rules:
- "positive" = supports the proposal or expresses approval
- "negative" = opposes the proposal or expresses concern/disapproval
- "neutral" = informational, procedural, or no clear position
- "mixed" = expresses both support and opposition, or conflicting views
- Normalize topics to short canonical forms (lowercase, no articles)
- Be precise with confidence: clear sentiment = 0.8-1.0, ambiguous = 0.4-0.6

Return a JSON array of objects with keys: sentiment, confidence, normalized_topic, summary"""


def _build_classify_prompt(comments: list[dict]) -> str:
    """Build the user prompt for batch classification."""
    lines = []
    for i, c in enumerate(comments):
        speaker = c.get("speaker", "Unknown")
        topic = c.get("topic", "General")
        summary = c.get("summary", "")
        lines.append(f"Comment {i + 1}:")
        lines.append(f"  Speaker: {speaker}")
        lines.append(f"  Topic: {topic}")
        lines.append(f"  Summary: {summary}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SentimentAnalyzer
# ---------------------------------------------------------------------------

class SentimentAnalyzer:
    """Analyzes public comment sentiment and stores results in SQLite."""

    def __init__(self, db_path: str, openai_client: Optional[OpenAI] = None):
        self._db_path = db_path
        self._openai = openai_client
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._conn()
        try:
            conn.execute(_CREATE_COMMENT_SENTIMENTS)
            conn.execute(_CREATE_TOPIC_SENTIMENTS)
            conn.execute(_CREATE_TOPIC_CLUSTERS)
            for idx in _CREATE_INDEXES:
                conn.execute(idx)
            conn.commit()
        finally:
            conn.close()

    def _get_openai(self) -> OpenAI:
        if self._openai is None:
            self._openai = OpenAI()
        return self._openai

    # ------------------------------------------------------------------
    # Core: classify comments via GPT-4o-mini
    # ------------------------------------------------------------------

    def classify_comments(
        self,
        comments: list[dict],
        tenant_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> list[dict]:
        """Classify a batch of public comments using GPT-4o-mini.

        Args:
            comments: list of dicts with speaker, topic, summary keys

        Returns:
            list of classification dicts with sentiment, confidence,
            normalized_topic, summary
        """
        if not comments:
            return []

        client = self._get_openai()
        prompt = _build_classify_prompt(comments)

        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _CLASSIFY_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            _record_llm_cost(
                tenant_id,
                getattr(resp, "usage", None),
                model="gpt-4o-mini",
                operation="public_comment_sentiment",
                request_id=request_id,
            )
            raw = json.loads(resp.choices[0].message.content)
            # Handle both {"results": [...]} and direct array
            if isinstance(raw, dict):
                results = raw.get("results", raw.get("comments", []))
            elif isinstance(raw, list):
                results = raw
            else:
                results = []
            return results
        except Exception:
            logger.error("Failed to classify comments via GPT-4o-mini", exc_info=True)
            # Fallback: return neutral for all
            return [
                {
                    "sentiment": "neutral",
                    "confidence": 0.5,
                    "normalized_topic": c.get("topic", "general"),
                    "summary": c.get("summary", ""),
                }
                for c in comments
            ]

    # ------------------------------------------------------------------
    # Analyze a single clip
    # ------------------------------------------------------------------

    def analyze_clip(
        self,
        clip_id: str,
        extracted_facts: dict,
        meeting_date: str = "",
        meeting_body: str = "",
        tenant_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> list[CommentSentiment]:
        """Analyze all public comments from a clip's extracted_facts.

        Args:
            clip_id: the clip identifier
            extracted_facts: parsed extracted_facts.json content
            meeting_date: ISO date string
            meeting_body: meeting body name

        Returns:
            list of CommentSentiment records saved to DB
        """
        comments = extracted_facts.get("public_comments", [])
        if not comments:
            logger.debug("No public comments in clip %s", clip_id)
            return []

        # Skip if already analyzed
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM comment_sentiments WHERE clip_id = ?",
                (str(clip_id),),
            ).fetchone()[0]
            if existing > 0:
                logger.debug("Clip %s already analyzed (%d comments)", clip_id, existing)
                rows = conn.execute(
                    "SELECT * FROM comment_sentiments WHERE clip_id = ?",
                    (str(clip_id),),
                ).fetchall()
                return [self._row_to_comment(r) for r in rows]
        finally:
            conn.close()

        # Classify via LLM
        classifications = self.classify_comments(
            comments,
            tenant_id=tenant_id,
            request_id=request_id,
        )

        # Store results
        now = datetime.now(timezone.utc).isoformat()
        results = []

        conn = self._conn()
        try:
            for i, comment in enumerate(comments):
                cls = classifications[i] if i < len(classifications) else {
                    "sentiment": "neutral",
                    "confidence": 0.5,
                    "normalized_topic": comment.get("topic", "general"),
                    "summary": comment.get("summary", ""),
                }

                sentiment = cls.get("sentiment", "neutral")
                if sentiment not in SENTIMENT_VALUES:
                    sentiment = "neutral"

                record = CommentSentiment(
                    id=str(uuid.uuid4()),
                    clip_id=str(clip_id),
                    speaker=comment.get("speaker", "Unknown"),
                    topic=cls.get("normalized_topic", comment.get("topic", "general")).lower().strip(),
                    sentiment=sentiment,
                    confidence=min(1.0, max(0.0, float(cls.get("confidence", 0.5)))),
                    summary=cls.get("summary", comment.get("summary", "")),
                    timestamp=comment.get("transcript_approx_time", ""),
                    meeting_date=meeting_date,
                    meeting_body=meeting_body or "",
                    raw_topic=comment.get("topic", ""),
                    created_at=now,
                )

                conn.execute(
                    """INSERT OR REPLACE INTO comment_sentiments
                       (id, clip_id, speaker, topic, sentiment, confidence,
                        summary, timestamp, meeting_date, meeting_body, raw_topic, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.id, record.clip_id, record.speaker, record.topic,
                        record.sentiment, record.confidence, record.summary,
                        record.timestamp, record.meeting_date, record.meeting_body,
                        record.raw_topic, record.created_at,
                    ),
                )
                results.append(record)

            conn.commit()
            logger.info("Analyzed %d comments for clip %s", len(results), clip_id)
        finally:
            conn.close()

        # Update aggregated topic sentiments
        self._update_topic_sentiments(meeting_date)

        return results

    # ------------------------------------------------------------------
    # Batch: analyze all clips in output directory
    # ------------------------------------------------------------------

    def analyze_all_clips(
        self,
        output_dir: str,
        max_clips: int = 0,
        tenant_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> int:
        """Scan output_dir/clips/ for extracted_facts.json and analyze each.

        Returns number of clips analyzed.
        """
        clips_dir = os.path.join(output_dir, "clips")
        if not os.path.isdir(clips_dir):
            logger.warning("Clips directory not found: %s", clips_dir)
            return 0

        analyzed = 0
        clip_dirs = sorted(os.listdir(clips_dir), reverse=True)

        for clip_name in clip_dirs:
            if max_clips and analyzed >= max_clips:
                break

            clip_path = os.path.join(clips_dir, clip_name)
            facts_path = os.path.join(clip_path, "extracted_facts.json")
            metadata_path = os.path.join(clip_path, "metadata.json")

            if not os.path.isfile(facts_path):
                continue

            try:
                with open(facts_path) as f:
                    facts = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to read %s", facts_path)
                continue

            # Read metadata for date and body
            meeting_date = ""
            meeting_body = ""
            if os.path.isfile(metadata_path):
                try:
                    with open(metadata_path) as f:
                        meta = json.load(f)
                    meeting_date = meta.get("date", "")
                    meeting_body = meta.get("meeting_body", "") or ""
                except (json.JSONDecodeError, OSError):
                    pass

            if not facts.get("public_comments"):
                continue

            self.analyze_clip(
                clip_name,
                facts,
                meeting_date,
                meeting_body,
                tenant_id=tenant_id,
                request_id=request_id,
            )
            analyzed += 1

        logger.info("Analyzed %d clips total", analyzed)
        return analyzed

    # ------------------------------------------------------------------
    # Aggregation: update topic_sentiments table
    # ------------------------------------------------------------------

    def _update_topic_sentiments(self, date: str = ""):
        """Recompute topic_sentiments from comment_sentiments."""
        conn = self._conn()
        try:
            # If date provided, only update that date's topics
            if date:
                topics = conn.execute(
                    "SELECT DISTINCT topic FROM comment_sentiments WHERE meeting_date = ?",
                    (date,),
                ).fetchall()
                topic_list = [r["topic"] for r in topics]
            else:
                topics = conn.execute(
                    "SELECT DISTINCT topic FROM comment_sentiments"
                ).fetchall()
                topic_list = [r["topic"] for r in topics]

            for topic in topic_list:
                if date:
                    rows = conn.execute(
                        """SELECT sentiment FROM comment_sentiments
                           WHERE topic = ? AND meeting_date = ?""",
                        (topic, date),
                    ).fetchall()
                    self._upsert_topic_sentiment(conn, topic, date, rows)
                else:
                    # Get all dates for this topic
                    dates = conn.execute(
                        "SELECT DISTINCT meeting_date FROM comment_sentiments WHERE topic = ?",
                        (topic,),
                    ).fetchall()
                    for d in dates:
                        dt = d["meeting_date"]
                        rows = conn.execute(
                            """SELECT sentiment FROM comment_sentiments
                               WHERE topic = ? AND meeting_date = ?""",
                            (topic, dt),
                        ).fetchall()
                        self._upsert_topic_sentiment(conn, topic, dt, rows)

            conn.commit()
        finally:
            conn.close()

    def _upsert_topic_sentiment(self, conn, topic: str, date: str, rows: list):
        counts = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0}
        for r in rows:
            s = r["sentiment"]
            if s in counts:
                counts[s] += 1

        total = sum(counts.values())
        if total == 0:
            return

        avg = sum(SENTIMENT_SCORE.get(r["sentiment"], 0) for r in rows) / total

        conn.execute(
            """INSERT INTO topic_sentiments
               (topic, date, avg_sentiment, comment_count,
                positive_count, negative_count, neutral_count, mixed_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(topic, date) DO UPDATE SET
                 avg_sentiment = excluded.avg_sentiment,
                 comment_count = excluded.comment_count,
                 positive_count = excluded.positive_count,
                 negative_count = excluded.negative_count,
                 neutral_count = excluded.neutral_count,
                 mixed_count = excluded.mixed_count""",
            (
                topic, date, avg, total,
                counts["positive"], counts["negative"],
                counts["neutral"], counts["mixed"],
            ),
        )

    # ------------------------------------------------------------------
    # Query: topics
    # ------------------------------------------------------------------

    def get_topics(
        self,
        limit: int = 20,
        meeting_body: str = "",
        date_after: str = "",
        date_before: str = "",
    ) -> list[dict]:
        """Get top topics by comment count with average sentiment."""
        conn = self._conn()
        try:
            query = """
                SELECT topic,
                       COUNT(*) as comment_count,
                       AVG(CASE sentiment
                           WHEN 'positive' THEN 1.0
                           WHEN 'negative' THEN -1.0
                           WHEN 'mixed' THEN 0.0
                           ELSE 0.0 END) as avg_sentiment,
                       SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) as negative,
                       SUM(CASE WHEN sentiment = 'neutral' THEN 1 ELSE 0 END) as neutral,
                       SUM(CASE WHEN sentiment = 'mixed' THEN 1 ELSE 0 END) as mixed
                FROM comment_sentiments
                WHERE 1=1
            """
            params: list[Any] = []

            if meeting_body:
                query += " AND meeting_body = ?"
                params.append(meeting_body)
            if date_after:
                query += " AND meeting_date >= ?"
                params.append(date_after)
            if date_before:
                query += " AND meeting_date <= ?"
                params.append(date_before)

            query += " GROUP BY topic ORDER BY comment_count DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "topic": r["topic"],
                    "comment_count": r["comment_count"],
                    "avg_sentiment": round(r["avg_sentiment"], 3),
                    "positive": r["positive"],
                    "negative": r["negative"],
                    "neutral": r["neutral"],
                    "mixed": r["mixed"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Query: topic trend over time
    # ------------------------------------------------------------------

    def get_topic_trend(
        self,
        topic: str,
        date_after: str = "",
        date_before: str = "",
    ) -> list[dict]:
        """Get sentiment trend for a topic over time."""
        conn = self._conn()
        try:
            query = """
                SELECT date, avg_sentiment, comment_count,
                       positive_count, negative_count, neutral_count, mixed_count
                FROM topic_sentiments
                WHERE topic = ?
            """
            params: list[Any] = [topic.lower().strip()]

            if date_after:
                query += " AND date >= ?"
                params.append(date_after)
            if date_before:
                query += " AND date <= ?"
                params.append(date_before)

            query += " ORDER BY date ASC"

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "date": r["date"],
                    "avg_sentiment": round(r["avg_sentiment"], 3),
                    "comment_count": r["comment_count"],
                    "positive": r["positive_count"],
                    "negative": r["negative_count"],
                    "neutral": r["neutral_count"],
                    "mixed": r["mixed_count"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Query: comments for a clip
    # ------------------------------------------------------------------

    def get_comments(
        self,
        clip_id: str = "",
        topic: str = "",
        speaker: str = "",
        sentiment: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Get analyzed comments with optional filters."""
        conn = self._conn()
        try:
            query = "SELECT * FROM comment_sentiments WHERE 1=1"
            params: list[Any] = []

            if clip_id:
                query += " AND clip_id = ?"
                params.append(str(clip_id))
            if topic:
                query += " AND topic = ?"
                params.append(topic.lower().strip())
            if speaker:
                query += " AND speaker LIKE ?"
                params.append(f"%{speaker}%")
            if sentiment and sentiment in SENTIMENT_VALUES:
                query += " AND sentiment = ?"
                params.append(sentiment)

            query += " ORDER BY meeting_date DESC, created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_comment(r).to_dict() for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Query: controversial topics
    # ------------------------------------------------------------------

    def get_controversial(
        self,
        limit: int = 10,
        min_comments: int = 2,
        meeting_body: str = "",
        date_after: str = "",
        date_before: str = "",
    ) -> list[dict]:
        """Find topics where sentiment is most divided.

        Controversy score = (min(positive, negative) / total) * comment_count
        High score means many comments with roughly even split.
        """
        conn = self._conn()
        try:
            query = """
                SELECT topic,
                       COUNT(*) as comment_count,
                       SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) as negative,
                       SUM(CASE WHEN sentiment = 'neutral' THEN 1 ELSE 0 END) as neutral,
                       SUM(CASE WHEN sentiment = 'mixed' THEN 1 ELSE 0 END) as mixed,
                       AVG(CASE sentiment
                           WHEN 'positive' THEN 1.0
                           WHEN 'negative' THEN -1.0
                           WHEN 'mixed' THEN 0.0
                           ELSE 0.0 END) as avg_sentiment
                FROM comment_sentiments
                WHERE 1=1
            """
            params: list[Any] = []

            if meeting_body:
                query += " AND meeting_body = ?"
                params.append(meeting_body)
            if date_after:
                query += " AND meeting_date >= ?"
                params.append(date_after)
            if date_before:
                query += " AND meeting_date <= ?"
                params.append(date_before)

            query += " GROUP BY topic HAVING COUNT(*) >= ?"
            params.append(min_comments)

            rows = conn.execute(query, params).fetchall()

            results = []
            for r in rows:
                pos = r["positive"]
                neg = r["negative"]
                total = r["comment_count"]
                # Controversy: high when positive and negative are balanced
                divergence = min(pos, neg) / max(total, 1)
                controversy_score = divergence * total

                results.append({
                    "topic": r["topic"],
                    "comment_count": total,
                    "positive": pos,
                    "negative": neg,
                    "neutral": r["neutral"],
                    "mixed": r["mixed"],
                    "avg_sentiment": round(r["avg_sentiment"], 3),
                    "controversy_score": round(controversy_score, 2),
                    "divergence": round(divergence, 3),
                })

            results.sort(key=lambda x: x["controversy_score"], reverse=True)
            return results[:limit]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Query: active speakers
    # ------------------------------------------------------------------

    def get_speakers(
        self,
        limit: int = 20,
        meeting_body: str = "",
        date_after: str = "",
        date_before: str = "",
    ) -> list[dict]:
        """Get most active public commenters with their topics and sentiment."""
        conn = self._conn()
        try:
            query = """
                SELECT speaker,
                       COUNT(*) as comment_count,
                       GROUP_CONCAT(DISTINCT topic) as topics,
                       AVG(CASE sentiment
                           WHEN 'positive' THEN 1.0
                           WHEN 'negative' THEN -1.0
                           WHEN 'mixed' THEN 0.0
                           ELSE 0.0 END) as avg_sentiment,
                       SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) as negative
                FROM comment_sentiments
                WHERE 1=1
            """
            params: list[Any] = []

            if meeting_body:
                query += " AND meeting_body = ?"
                params.append(meeting_body)
            if date_after:
                query += " AND meeting_date >= ?"
                params.append(date_after)
            if date_before:
                query += " AND meeting_date <= ?"
                params.append(date_before)

            query += " GROUP BY speaker ORDER BY comment_count DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "speaker": r["speaker"],
                    "comment_count": r["comment_count"],
                    "topics": r["topics"].split(",") if r["topics"] else [],
                    "avg_sentiment": round(r["avg_sentiment"], 3),
                    "positive": r["positive"],
                    "negative": r["negative"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Query: dashboard summary
    # ------------------------------------------------------------------

    def get_dashboard(
        self,
        meeting_body: str = "",
        date_after: str = "",
        date_before: str = "",
    ) -> dict:
        """Overall sentiment dashboard summary."""
        conn = self._conn()
        try:
            where = "WHERE 1=1"
            params: list[Any] = []

            if meeting_body:
                where += " AND meeting_body = ?"
                params.append(meeting_body)
            if date_after:
                where += " AND meeting_date >= ?"
                params.append(date_after)
            if date_before:
                where += " AND meeting_date <= ?"
                params.append(date_before)

            # Overall counts
            row = conn.execute(
                f"""SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) as positive,
                        SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) as negative,
                        SUM(CASE WHEN sentiment = 'neutral' THEN 1 ELSE 0 END) as neutral,
                        SUM(CASE WHEN sentiment = 'mixed' THEN 1 ELSE 0 END) as mixed,
                        COUNT(DISTINCT clip_id) as meetings_count,
                        COUNT(DISTINCT speaker) as speakers_count,
                        COUNT(DISTINCT topic) as topics_count
                    FROM comment_sentiments {where}""",
                params,
            ).fetchone()

            total = row["total"] or 0
            overview = {
                "total_comments": total,
                "positive": row["positive"] or 0,
                "negative": row["negative"] or 0,
                "neutral": row["neutral"] or 0,
                "mixed": row["mixed"] or 0,
                "meetings_analyzed": row["meetings_count"] or 0,
                "unique_speakers": row["speakers_count"] or 0,
                "unique_topics": row["topics_count"] or 0,
                "positive_ratio": round((row["positive"] or 0) / max(total, 1), 3),
                "negative_ratio": round((row["negative"] or 0) / max(total, 1), 3),
                "neutral_ratio": round((row["neutral"] or 0) / max(total, 1), 3),
            }

            # Trending topics (most comments in recent dates)
            trending = conn.execute(
                f"""SELECT topic, COUNT(*) as cnt
                    FROM comment_sentiments {where}
                    GROUP BY topic ORDER BY MAX(meeting_date) DESC, cnt DESC LIMIT 5""",
                params,
            ).fetchall()

            # Hot issues (most negative sentiment recently)
            hot = conn.execute(
                f"""SELECT topic, COUNT(*) as cnt,
                           AVG(CASE sentiment
                               WHEN 'positive' THEN 1.0
                               WHEN 'negative' THEN -1.0
                               ELSE 0.0 END) as avg_sent
                    FROM comment_sentiments {where}
                    GROUP BY topic
                    HAVING AVG(CASE sentiment WHEN 'negative' THEN 1.0 ELSE 0.0 END) > 0.3
                    ORDER BY avg_sent ASC LIMIT 5""",
                params,
            ).fetchall()

            # Meeting bodies breakdown
            bodies = conn.execute(
                f"""SELECT meeting_body, COUNT(*) as cnt
                    FROM comment_sentiments {where} AND meeting_body != ''
                    GROUP BY meeting_body ORDER BY cnt DESC""",
                params,
            ).fetchall()

            return {
                "overview": overview,
                "trending_topics": [
                    {"topic": r["topic"], "count": r["cnt"]} for r in trending
                ],
                "hot_issues": [
                    {
                        "topic": r["topic"],
                        "count": r["cnt"],
                        "avg_sentiment": round(r["avg_sent"], 3),
                    }
                    for r in hot
                ],
                "meeting_bodies": [
                    {"body": r["meeting_body"], "count": r["cnt"]} for r in bodies
                ],
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_comment(row) -> CommentSentiment:
        return CommentSentiment(
            id=row["id"],
            clip_id=row["clip_id"],
            speaker=row["speaker"],
            topic=row["topic"],
            sentiment=row["sentiment"],
            confidence=row["confidence"],
            summary=row["summary"],
            timestamp=row["timestamp"] or "",
            meeting_date=row["meeting_date"] or "",
            meeting_body=row["meeting_body"] or "",
            raw_topic=row["raw_topic"] or "",
            created_at=row["created_at"],
        )

    def get_stats(self) -> dict:
        """Return basic stats about the sentiment database."""
        conn = self._conn()
        try:
            comments = conn.execute("SELECT COUNT(*) FROM comment_sentiments").fetchone()[0]
            topics = conn.execute("SELECT COUNT(DISTINCT topic) FROM comment_sentiments").fetchone()[0]
            clips = conn.execute("SELECT COUNT(DISTINCT clip_id) FROM comment_sentiments").fetchone()[0]
            return {
                "total_comments": comments,
                "unique_topics": topics,
                "clips_analyzed": clips,
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_analyzer: Optional[SentimentAnalyzer] = None


def init_sentiment(output_dir: str, openai_client: Optional[OpenAI] = None):
    global _analyzer
    db_path = os.path.join(output_dir, "sentiment.db")
    _analyzer = SentimentAnalyzer(db_path, openai_client)
    logger.info("Sentiment analyzer initialized: %s", db_path)


def get_analyzer() -> SentimentAnalyzer:
    if _analyzer is None:
        raise RuntimeError("Sentiment analyzer not initialized. Call init_sentiment() first.")
    return _analyzer
