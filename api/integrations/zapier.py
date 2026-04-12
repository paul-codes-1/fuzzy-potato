"""Zapier and Make (Integromat) integration for CivicLens meeting intelligence.

Provides:
- ZapierIntegration class with trigger and action definitions
- Polling trigger endpoints returning flat JSON items with `id` fields
- REST Hook subscription management (subscribe/unsubscribe for instant triggers)
- Action handlers that wrap RAG query, vote search, financial search, and meeting export
- Cursor-based polling using timestamps for deduplication

Zapier requirements met:
- All responses are flat JSON (no nested objects at the top level)
- Every item has a unique `id` field
- Polling triggers return newest items first (descending by timestamp)
- REST Hooks use subscribe/unsubscribe pattern for instant triggers
"""

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

TRIGGER_TYPES = {"new_meeting", "new_vote", "financial_alert", "keyword_match"}


@dataclass
class RestHook:
    """A REST Hook subscription from Zapier or Make."""

    id: str
    tenant_id: str
    trigger_type: str
    target_url: str
    config: dict  # trigger-specific config (e.g. threshold, keyword)
    active: bool
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "trigger_type": self.trigger_type,
            "target_url": self.target_url,
            "config": self.config,
            "active": self.active,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_HOOKS_TABLE = """
CREATE TABLE IF NOT EXISTS zapier_hooks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    target_url TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""

_CREATE_HOOKS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_zapier_hooks_tenant
    ON zapier_hooks(tenant_id, trigger_type);
"""

# Event log for polling -- stores flattened trigger items
_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS zapier_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    clip_id TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_CREATE_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_zapier_events_tenant_type
    ON zapier_events(tenant_id, trigger_type, created_at DESC);
"""


# ---------------------------------------------------------------------------
# Flat item helpers -- Zapier requires simple key-value, no nesting
# ---------------------------------------------------------------------------

def flatten_meeting(clip_id: str, metadata: dict, facts: dict | None = None) -> dict:
    """Flatten a meeting into a Zapier-compatible flat dict."""
    topics = metadata.get("topics", [])
    item = {
        "id": f"meeting_{clip_id}_{metadata.get('date', 'unknown')}",
        "clip_id": str(clip_id),
        "title": metadata.get("title", ""),
        "date": metadata.get("date", ""),
        "meeting_body": metadata.get("meeting_body", ""),
        "topics": ", ".join(topics) if topics else "",
        "topic_count": len(topics),
        "transcript_words": metadata.get("transcript_words", 0),
        "url": metadata.get("url", ""),
        "processed_at": metadata.get("processed_at", ""),
    }

    if facts:
        votes = facts.get("motions_and_votes", [])
        financial = facts.get("financial_items", [])
        public_comments = facts.get("public_comments", [])
        item["vote_count"] = len(votes)
        item["financial_item_count"] = len(financial)
        item["public_comment_count"] = len(public_comments)

        # Meeting info from facts
        info = facts.get("meeting_info", {})
        item["presiding_officer"] = info.get("presiding_officer", "")
        item["location"] = info.get("location", "")

        # Attendance summary
        attendance = facts.get("attendance", {})
        present = attendance.get("present", [])
        absent = attendance.get("absent", [])
        item["members_present"] = len(present)
        item["members_absent"] = len(absent)
        item["attendance_list"] = ", ".join(present) if present else ""
    else:
        item["vote_count"] = 0
        item["financial_item_count"] = 0
        item["public_comment_count"] = 0

    return item


def flatten_vote(vote: dict, clip_id: str, meeting_date: str = "",
                 meeting_body: str = "", meeting_title: str = "") -> dict:
    """Flatten a vote record into a Zapier-compatible flat dict."""
    vote_id = vote.get("id", str(uuid.uuid4()))
    votes_for = vote.get("votes_for", [])
    votes_against = vote.get("votes_against", [])

    return {
        "id": f"vote_{vote_id}",
        "clip_id": str(clip_id),
        "meeting_date": meeting_date,
        "meeting_body": meeting_body,
        "meeting_title": meeting_title,
        "identifier": vote.get("identifier", ""),
        "description": vote.get("description", ""),
        "motion_by": vote.get("motion_by", ""),
        "second_by": vote.get("second_by", ""),
        "outcome": vote.get("outcome", ""),
        "vote_type": vote.get("vote_type", ""),
        "ayes": vote.get("ayes", 0),
        "nays": vote.get("nays", 0),
        "abstentions": vote.get("abstentions", 0),
        "votes_for": ", ".join(votes_for) if votes_for else "",
        "votes_against": ", ".join(votes_against) if votes_against else "",
        "conditions": vote.get("conditions", "") or "",
        "transcript_time": vote.get("transcript_approx_time", ""),
    }


def flatten_financial(item: dict, clip_id: str, meeting_date: str = "",
                      meeting_body: str = "", meeting_title: str = "") -> dict:
    """Flatten a financial item into a Zapier-compatible flat dict."""
    item_id = item.get("id", str(uuid.uuid4()))
    return {
        "id": f"financial_{item_id}",
        "clip_id": str(clip_id),
        "meeting_date": meeting_date,
        "meeting_body": meeting_body,
        "meeting_title": meeting_title,
        "description": item.get("description", ""),
        "amount": item.get("amount", ""),
        "amount_cents": item.get("amount_cents") or 0,
        "type": item.get("type", ""),
        "identifier": item.get("identifier", "") or "",
        "vendor_or_recipient": item.get("vendor_or_recipient", "") or "",
    }


def flatten_keyword_match(keyword: str, clip_id: str, section: str,
                          matched_text: str, meeting_date: str = "",
                          meeting_body: str = "", meeting_title: str = "") -> dict:
    """Flatten a keyword match into a Zapier-compatible flat dict."""
    match_id = str(uuid.uuid4())
    return {
        "id": f"keyword_{match_id}",
        "clip_id": str(clip_id),
        "keyword": keyword,
        "matched_section": section,
        "matched_text": matched_text[:500],
        "meeting_date": meeting_date,
        "meeting_body": meeting_body,
        "meeting_title": meeting_title,
    }


# ---------------------------------------------------------------------------
# ZapierIntegration class
# ---------------------------------------------------------------------------

class ZapierIntegration:
    """Manages Zapier/Make integration: REST Hook subscriptions, event logging,
    and polling trigger data.

    Uses SQLite for persistent hook subscriptions and an event log that
    polling triggers read from.
    """

    def __init__(self, db_path: str, output_dir: str):
        self._db_path = db_path
        self._output_dir = output_dir
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(_CREATE_HOOKS_TABLE)
        self._conn.execute(_CREATE_HOOKS_INDEX)
        self._conn.execute(_CREATE_EVENTS_TABLE)
        self._conn.execute(_CREATE_EVENTS_INDEX)
        self._conn.commit()

    # ------------------------------------------------------------------
    # REST Hook subscription management
    # ------------------------------------------------------------------

    def subscribe(self, tenant_id: str, trigger_type: str,
                  target_url: str, config: dict | None = None) -> RestHook:
        """Subscribe a REST Hook for instant triggers."""
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError(
                f"Invalid trigger type '{trigger_type}'. "
                f"Must be one of: {', '.join(sorted(TRIGGER_TYPES))}"
            )

        hook = RestHook(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            trigger_type=trigger_type,
            target_url=target_url,
            config=config or {},
            active=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self._conn.execute(
            "INSERT INTO zapier_hooks (id, tenant_id, trigger_type, target_url, config, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (hook.id, hook.tenant_id, hook.trigger_type, hook.target_url,
             json.dumps(hook.config), hook.created_at),
        )
        self._conn.commit()

        logger.info(
            "zapier_hook_subscribed",
            extra={
                "hook_id": hook.id,
                "tenant_id": tenant_id,
                "trigger_type": trigger_type,
            },
        )
        return hook

    def unsubscribe(self, hook_id: str, tenant_id: str) -> bool:
        """Unsubscribe a REST Hook. Returns True if deleted."""
        cur = self._conn.execute(
            "DELETE FROM zapier_hooks WHERE id = ? AND tenant_id = ?",
            (hook_id, tenant_id),
        )
        self._conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info(
                "zapier_hook_unsubscribed",
                extra={"hook_id": hook_id, "tenant_id": tenant_id},
            )
        return deleted

    def get_hooks(self, tenant_id: str,
                  trigger_type: str | None = None) -> list[RestHook]:
        """List active REST Hooks for a tenant, optionally filtered by trigger type."""
        if trigger_type:
            rows = self._conn.execute(
                "SELECT * FROM zapier_hooks WHERE tenant_id = ? AND trigger_type = ? AND active = 1 "
                "ORDER BY created_at DESC",
                (tenant_id, trigger_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM zapier_hooks WHERE tenant_id = ? AND active = 1 "
                "ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [self._row_to_hook(r) for r in rows]

    def get_all_hooks_for_trigger(self, trigger_type: str) -> list[RestHook]:
        """Get all active hooks across all tenants for a trigger type.
        Used when firing events to all subscribers."""
        rows = self._conn.execute(
            "SELECT * FROM zapier_hooks WHERE trigger_type = ? AND active = 1",
            (trigger_type,),
        ).fetchall()
        return [self._row_to_hook(r) for r in rows]

    # ------------------------------------------------------------------
    # Event logging for polling triggers
    # ------------------------------------------------------------------

    def log_event(self, tenant_id: str, trigger_type: str,
                  clip_id: str, data: dict) -> str:
        """Log a trigger event for polling. Returns the event ID."""
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO zapier_events (id, tenant_id, trigger_type, clip_id, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, tenant_id, trigger_type, str(clip_id), json.dumps(data), now),
        )
        self._conn.commit()
        return event_id

    def poll_events(self, tenant_id: str, trigger_type: str,
                    limit: int = 50) -> list[dict]:
        """Poll trigger events for Zapier. Returns flat items newest-first.

        Zapier deduplicates by `id` field, so we return all recent items
        and let Zapier handle which ones are new.
        """
        rows = self._conn.execute(
            "SELECT * FROM zapier_events WHERE tenant_id = ? AND trigger_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (tenant_id, trigger_type, limit),
        ).fetchall()

        items = []
        for row in rows:
            item = json.loads(row["data"])
            # Ensure id field exists at top level (Zapier requirement)
            if "id" not in item:
                item["id"] = row["id"]
            items.append(item)

        return items

    # ------------------------------------------------------------------
    # Trigger: emit events when meetings are processed
    # ------------------------------------------------------------------

    def on_meeting_processed(self, clip_id: str, metadata: dict,
                             facts: dict | None = None,
                             tenant_id: str = "") -> list[dict]:
        """Called when a meeting finishes processing. Logs events for all
        applicable triggers and returns the list of hook URLs to fire."""
        flat_meeting = flatten_meeting(clip_id, metadata, facts)
        meeting_date = metadata.get("date", "")
        meeting_body = metadata.get("meeting_body", "")
        meeting_title = metadata.get("title", "")

        # 1. Log new_meeting event
        self.log_event(tenant_id, "new_meeting", clip_id, flat_meeting)

        # 2. Log new_vote events
        if facts:
            for vote in facts.get("motions_and_votes", []):
                flat = flatten_vote(vote, clip_id, meeting_date,
                                    meeting_body, meeting_title)
                self.log_event(tenant_id, "new_vote", clip_id, flat)

            # 3. Log financial_alert events (all items logged; threshold
            #    filtering happens at the polling/hook level)
            for item in facts.get("financial_items", []):
                flat = flatten_financial(item, clip_id, meeting_date,
                                        meeting_body, meeting_title)
                self.log_event(tenant_id, "financial_alert", clip_id, flat)

        return [flat_meeting]

    # ------------------------------------------------------------------
    # Action: ask a RAG question
    # ------------------------------------------------------------------

    def action_ask_question(self, question: str, collection, openai_client,
                            clip_metadata: dict, tenant_id: str = "",
                            meeting_body: str = "",
                            date_after: str = "",
                            date_before: str = "") -> dict:
        """Execute a RAG query and return Zapier-flat result."""
        from api.query import ask

        filters: dict[str, str] = {}
        if tenant_id and tenant_id != "dev":
            filters["tenant_id"] = tenant_id
        if meeting_body:
            filters["meeting_body"] = meeting_body
        if date_after:
            filters["date_after"] = date_after
        if date_before:
            filters["date_before"] = date_before

        result = ask(
            question=question,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            filters=filters if filters else None,
        )

        # Flatten for Zapier
        sources = result.get("sources", [])
        source_summaries = []
        for s in sources[:5]:
            title = s.get("title", "Unknown")
            date = s.get("date", "")
            ts = s.get("timestamp")
            ts_str = f" [{ts // 60}:{ts % 60:02d}]" if ts is not None else ""
            source_summaries.append(f"{title} ({date}){ts_str}")

        return {
            "id": str(uuid.uuid4()),
            "question": question,
            "answer": result.get("answer", ""),
            "source_count": len(sources),
            "sources": "; ".join(source_summaries),
            "filters_applied": json.dumps(result.get("filters_applied", {})),
        }

    # ------------------------------------------------------------------
    # Action: search votes (delegates to PolicyTracker)
    # ------------------------------------------------------------------

    def action_search_votes(self, tracker, member: str = "",
                            date_after: str = "", date_before: str = "",
                            outcome: str = "", keyword: str = "",
                            limit: int = 20) -> list[dict]:
        """Search votes and return Zapier-flat list."""
        result = tracker.search_votes(
            member=member, date_after=date_after, date_before=date_before,
            outcome=outcome, keyword=keyword, limit=limit,
        )

        flat_items = []
        for vote in result.get("votes", []):
            flat_items.append(flatten_vote(
                vote, vote.get("clip_id", ""),
                vote.get("meeting_date", ""),
                vote.get("meeting_body", ""),
            ))

        return flat_items

    # ------------------------------------------------------------------
    # Action: search financial items (delegates to PolicyTracker)
    # ------------------------------------------------------------------

    def action_search_financial(self, tracker, min_amount: float | None = None,
                                max_amount: float | None = None,
                                item_type: str = "", date_after: str = "",
                                date_before: str = "", keyword: str = "",
                                limit: int = 20) -> list[dict]:
        """Search financial items and return Zapier-flat list."""
        min_cents = int(min_amount * 100) if min_amount is not None else None
        max_cents = int(max_amount * 100) if max_amount is not None else None

        result = tracker.search_financial(
            min_amount=min_cents, max_amount=max_cents,
            item_type=item_type, date_after=date_after,
            date_before=date_before, keyword=keyword, limit=limit,
        )

        flat_items = []
        for item in result.get("items", []):
            flat_items.append(flatten_financial(
                item, item.get("clip_id", ""),
                item.get("meeting_date", ""),
                item.get("meeting_body", ""),
            ))

        return flat_items

    # ------------------------------------------------------------------
    # Action: export meeting data
    # ------------------------------------------------------------------

    def action_export_meeting(self, clip_id: str) -> dict:
        """Export a meeting's full data as a flat Zapier dict."""
        clips_dir = os.path.join(self._output_dir, "clips", str(clip_id))

        # Load metadata
        meta_path = os.path.join(clips_dir, "metadata.json")
        if not os.path.isfile(meta_path):
            return {"id": f"export_{clip_id}", "error": "Meeting not found",
                    "clip_id": str(clip_id)}

        with open(meta_path) as f:
            metadata = json.load(f)

        # Load facts if available
        facts = None
        facts_path = os.path.join(clips_dir, "extracted_facts.json")
        if os.path.isfile(facts_path):
            with open(facts_path) as f:
                facts = json.load(f)

        # Load summary if available
        summary = ""
        summary_path = os.path.join(clips_dir, "summary.txt")
        if os.path.isfile(summary_path):
            with open(summary_path) as f:
                summary = f.read()

        # Load transcript if available
        transcript = ""
        transcript_files = metadata.get("files", {})
        transcript_filename = transcript_files.get("transcript")
        if transcript_filename:
            transcript_path = os.path.join(clips_dir, transcript_filename)
            if os.path.isfile(transcript_path):
                with open(transcript_path) as f:
                    transcript = f.read()

        # Build flat result
        flat = flatten_meeting(clip_id, metadata, facts)
        flat["id"] = f"export_{clip_id}"
        flat["summary"] = summary[:10000]  # Zapier has field size limits
        flat["transcript_preview"] = transcript[:5000]
        flat["has_agenda"] = bool(transcript_files.get("agenda_txt"))
        flat["has_minutes"] = bool(transcript_files.get("minutes_txt"))
        flat["has_transcript"] = bool(transcript_filename)

        if facts:
            # Serialize key sections as JSON strings for Zapier
            flat["votes_json"] = json.dumps(facts.get("motions_and_votes", []))
            flat["financial_items_json"] = json.dumps(facts.get("financial_items", []))
            flat["public_comments_json"] = json.dumps(facts.get("public_comments", []))

        return flat

    # ------------------------------------------------------------------
    # Keyword search across meeting facts (for keyword_match trigger)
    # ------------------------------------------------------------------

    def search_keyword_in_facts(self, keyword: str, tenant_id: str = "",
                                limit: int = 50) -> list[dict]:
        """Search for a keyword across all meeting facts. Returns flat items."""
        clips_dir = os.path.join(self._output_dir, "clips")
        if not os.path.isdir(clips_dir):
            return []

        keyword_lower = keyword.lower()
        matches: list[dict] = []

        # Iterate clips in reverse order (newest first)
        clip_ids = sorted(os.listdir(clips_dir), reverse=True)

        for clip_id in clip_ids:
            if len(matches) >= limit:
                break

            clip_path = os.path.join(clips_dir, clip_id)
            facts_path = os.path.join(clip_path, "extracted_facts.json")
            meta_path = os.path.join(clip_path, "metadata.json")

            if not os.path.isfile(facts_path):
                continue

            try:
                with open(facts_path) as f:
                    facts = json.load(f)
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                continue

            facts_text = json.dumps(facts).lower()
            if keyword_lower not in facts_text:
                continue

            meeting_date = metadata.get("date", "")
            meeting_body = metadata.get("meeting_body", "")
            meeting_title = metadata.get("title", "")

            # Find specific matches in sections
            for item in facts.get("agenda_items", []):
                combined = f"{item.get('title', '')} {item.get('summary', '')}".lower()
                if keyword_lower in combined:
                    matches.append(flatten_keyword_match(
                        keyword, clip_id, "agenda_items",
                        f"{item.get('title', '')} - {item.get('summary', '')}",
                        meeting_date, meeting_body, meeting_title,
                    ))

            for vote in facts.get("motions_and_votes", []):
                combined = f"{vote.get('identifier', '')} {vote.get('description', '')}".lower()
                if keyword_lower in combined:
                    matches.append(flatten_keyword_match(
                        keyword, clip_id, "motions_and_votes",
                        f"{vote.get('identifier', '')} - {vote.get('description', '')}",
                        meeting_date, meeting_body, meeting_title,
                    ))

            for comment in facts.get("public_comments", []):
                combined = f"{comment.get('topic', '')} {comment.get('summary', '')}".lower()
                if keyword_lower in combined:
                    matches.append(flatten_keyword_match(
                        keyword, clip_id, "public_comments",
                        f"{comment.get('speaker', '')} on {comment.get('topic', '')}",
                        meeting_date, meeting_body, meeting_title,
                    ))

        return matches[:limit]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_hook(row) -> RestHook:
        return RestHook(
            id=row["id"],
            tenant_id=row["tenant_id"],
            trigger_type=row["trigger_type"],
            target_url=row["target_url"],
            config=json.loads(row["config"]),
            active=bool(row["active"]),
            created_at=row["created_at"],
        )

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_zapier_integration: Optional[ZapierIntegration] = None


def init_zapier(output_dir: str) -> ZapierIntegration:
    """Initialize the Zapier integration. Call once at startup."""
    global _zapier_integration
    db_path = os.path.join(output_dir, "zapier.db")
    _zapier_integration = ZapierIntegration(db_path, output_dir)
    return _zapier_integration


def get_zapier_integration() -> ZapierIntegration:
    if _zapier_integration is None:
        raise RuntimeError("Zapier integration not initialized -- call init_zapier() first")
    return _zapier_integration
