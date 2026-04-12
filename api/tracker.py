"""Policy tracking and vote monitoring system.

Provides SQLite-backed alert rules, vote/financial search, and digest
generation for advocacy groups, journalists, and lobbyists.
"""

import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

ALERT_TYPES = {"keyword", "member", "financial", "vote"}


@dataclass
class Alert:
    id: str
    tenant_id: str
    name: str
    type: str  # keyword | member | financial | vote
    config: dict  # type-specific config
    enabled: bool = True
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "type": self.type,
            "config": self.config,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


@dataclass
class AlertMatch:
    id: str
    alert_id: str
    clip_id: str
    match_type: str
    matched_text: str
    context: dict
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "alert_id": self.alert_id,
            "clip_id": self.clip_id,
            "match_type": self.match_type,
            "matched_text": self.matched_text,
            "context": self.context,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# PolicyTracker — SQLite store for alerts, matches, and vote/financial search
# ---------------------------------------------------------------------------

_CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""

_CREATE_MATCHES_TABLE = """
CREATE TABLE IF NOT EXISTS alert_matches (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    match_type TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    context TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
);
"""

_CREATE_VOTES_TABLE = """
CREATE TABLE IF NOT EXISTS votes (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL,
    identifier TEXT,
    description TEXT,
    motion_by TEXT,
    second_by TEXT,
    outcome TEXT,
    vote_type TEXT,
    ayes INTEGER DEFAULT 0,
    nays INTEGER DEFAULT 0,
    abstentions INTEGER DEFAULT 0,
    votes_for TEXT,
    votes_against TEXT,
    conditions TEXT,
    transcript_approx_time TEXT,
    meeting_date TEXT,
    meeting_body TEXT
);
"""

_CREATE_FINANCIAL_TABLE = """
CREATE TABLE IF NOT EXISTS financial_items (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL,
    description TEXT,
    amount TEXT,
    amount_cents INTEGER,
    type TEXT,
    identifier TEXT,
    vendor_or_recipient TEXT,
    meeting_date TEXT,
    meeting_body TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_alerts_tenant ON alerts(tenant_id);",
    "CREATE INDEX IF NOT EXISTS idx_matches_alert ON alert_matches(alert_id);",
    "CREATE INDEX IF NOT EXISTS idx_matches_clip ON alert_matches(clip_id);",
    "CREATE INDEX IF NOT EXISTS idx_votes_clip ON votes(clip_id);",
    "CREATE INDEX IF NOT EXISTS idx_votes_member ON votes(motion_by);",
    "CREATE INDEX IF NOT EXISTS idx_votes_outcome ON votes(outcome);",
    "CREATE INDEX IF NOT EXISTS idx_votes_date ON votes(meeting_date);",
    "CREATE INDEX IF NOT EXISTS idx_financial_clip ON financial_items(clip_id);",
    "CREATE INDEX IF NOT EXISTS idx_financial_amount ON financial_items(amount_cents);",
    "CREATE INDEX IF NOT EXISTS idx_financial_date ON financial_items(meeting_date);",
]


def _parse_amount_cents(amount_str: str) -> Optional[int]:
    """Parse dollar amount string like '$18,040,000' into cents."""
    if not amount_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", amount_str)
    try:
        return int(float(cleaned) * 100)
    except (ValueError, TypeError):
        return None


class PolicyTracker:
    """SQLite-backed policy tracking and vote monitoring."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._conn()
        try:
            conn.execute(_CREATE_ALERTS_TABLE)
            conn.execute(_CREATE_MATCHES_TABLE)
            conn.execute(_CREATE_VOTES_TABLE)
            conn.execute(_CREATE_FINANCIAL_TABLE)
            for idx_sql in _CREATE_INDEXES:
                conn.execute(idx_sql)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Alert CRUD
    # ------------------------------------------------------------------

    def create_alert(self, tenant_id: str, name: str, alert_type: str, config: dict) -> Alert:
        if alert_type not in ALERT_TYPES:
            raise ValueError(f"Invalid alert type '{alert_type}'. Must be one of: {ALERT_TYPES}")
        alert = Alert(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            type=alert_type,
            config=config,
            enabled=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO alerts (id, tenant_id, name, type, config, enabled, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (alert.id, alert.tenant_id, alert.name, alert.type,
                 json.dumps(alert.config), 1, alert.created_at),
            )
            conn.commit()
        finally:
            conn.close()
        return alert

    def get_alert(self, alert_id: str, tenant_id: str) -> Optional[Alert]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM alerts WHERE id = ? AND tenant_id = ?",
                (alert_id, tenant_id),
            ).fetchone()
            if not row:
                return None
            return self._row_to_alert(row)
        finally:
            conn.close()

    def list_alerts(self, tenant_id: str) -> list[Alert]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
            return [self._row_to_alert(r) for r in rows]
        finally:
            conn.close()

    def update_alert(self, alert_id: str, tenant_id: str, **kwargs) -> Optional[Alert]:
        allowed = {"name", "config", "enabled"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get_alert(alert_id, tenant_id)

        set_parts = []
        params = []
        for k, v in updates.items():
            set_parts.append(f"{k} = ?")
            params.append(json.dumps(v) if k == "config" else v)
        params.extend([alert_id, tenant_id])

        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE alerts SET {', '.join(set_parts)} WHERE id = ? AND tenant_id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_alert(alert_id, tenant_id)

    def delete_alert(self, alert_id: str, tenant_id: str) -> bool:
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM alerts WHERE id = ? AND tenant_id = ?",
                (alert_id, tenant_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Alert matching
    # ------------------------------------------------------------------

    def scan_clip(self, clip_id: str, extracted_facts: dict, meeting_date: str = "",
                  meeting_body: str = "", tenant_id: Optional[str] = None) -> list[AlertMatch]:
        """Scan extracted_facts against all active alerts. Returns new matches."""
        conn = self._conn()
        try:
            if tenant_id:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE tenant_id = ? AND enabled = 1",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE enabled = 1"
                ).fetchall()
        finally:
            conn.close()

        alerts = [self._row_to_alert(r) for r in rows]
        matches = []

        for alert in alerts:
            new_matches = self._check_alert(alert, clip_id, extracted_facts, meeting_date, meeting_body)
            matches.extend(new_matches)

        # Persist matches
        if matches:
            conn = self._conn()
            try:
                for m in matches:
                    conn.execute(
                        "INSERT INTO alert_matches (id, alert_id, clip_id, match_type, "
                        "matched_text, context, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (m.id, m.alert_id, m.clip_id, m.match_type,
                         m.matched_text, json.dumps(m.context), m.created_at),
                    )
                conn.commit()
            finally:
                conn.close()

        return matches

    def get_matches(self, alert_id: str, tenant_id: str, limit: int = 50,
                    offset: int = 0) -> dict:
        """Get paginated matches for an alert."""
        # Verify ownership
        alert = self.get_alert(alert_id, tenant_id)
        if not alert:
            return {"matches": [], "total": 0, "limit": limit, "offset": offset}

        conn = self._conn()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM alert_matches WHERE alert_id = ?",
                (alert_id,),
            ).fetchone()[0]

            rows = conn.execute(
                "SELECT * FROM alert_matches WHERE alert_id = ? ORDER BY created_at DESC "
                "LIMIT ? OFFSET ?",
                (alert_id, limit, offset),
            ).fetchall()

            matches = []
            for r in rows:
                matches.append(AlertMatch(
                    id=r["id"], alert_id=r["alert_id"], clip_id=r["clip_id"],
                    match_type=r["match_type"], matched_text=r["matched_text"],
                    context=json.loads(r["context"]), created_at=r["created_at"],
                ).to_dict())

            return {"matches": matches, "total": total, "limit": limit, "offset": offset}
        finally:
            conn.close()

    def _check_alert(self, alert: Alert, clip_id: str, facts: dict,
                     meeting_date: str, meeting_body: str) -> list[AlertMatch]:
        """Run a single alert against extracted facts."""
        if alert.type == "keyword":
            return self._check_keyword(alert, clip_id, facts, meeting_date, meeting_body)
        elif alert.type == "member":
            return self._check_member(alert, clip_id, facts, meeting_date)
        elif alert.type == "financial":
            return self._check_financial(alert, clip_id, facts, meeting_date)
        elif alert.type == "vote":
            return self._check_vote(alert, clip_id, facts, meeting_date)
        return []

    def _check_keyword(self, alert: Alert, clip_id: str, facts: dict,
                       meeting_date: str, meeting_body: str) -> list[AlertMatch]:
        keywords = [k.lower() for k in alert.config.get("keywords", [])]
        if not keywords:
            return []

        matches = []
        facts_text = json.dumps(facts).lower()

        for kw in keywords:
            if kw in facts_text:
                # Find specific matches in agenda items, votes, public comments
                context_items = []
                for item in facts.get("agenda_items", []):
                    combined = f"{item.get('title', '')} {item.get('summary', '')}".lower()
                    if kw in combined:
                        context_items.append({"section": "agenda_items", "item": item})
                for vote in facts.get("motions_and_votes", []):
                    combined = f"{vote.get('identifier', '')} {vote.get('description', '')}".lower()
                    if kw in combined:
                        context_items.append({"section": "motions_and_votes", "item": vote})
                for comment in facts.get("public_comments", []):
                    combined = f"{comment.get('topic', '')} {comment.get('summary', '')}".lower()
                    if kw in combined:
                        context_items.append({"section": "public_comments", "item": comment})

                matches.append(AlertMatch(
                    id=str(uuid.uuid4()),
                    alert_id=alert.id,
                    clip_id=clip_id,
                    match_type="keyword",
                    matched_text=kw,
                    context={
                        "meeting_date": meeting_date,
                        "meeting_body": meeting_body,
                        "matched_sections": context_items[:5],
                    },
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
        return matches

    def _check_member(self, alert: Alert, clip_id: str, facts: dict,
                      meeting_date: str) -> list[AlertMatch]:
        members = [m.lower() for m in alert.config.get("members", [])]
        if not members:
            return []

        matches = []
        for vote in facts.get("motions_and_votes", []):
            motion_by = (vote.get("motion_by") or "").lower()
            second_by = (vote.get("second_by") or "").lower()
            votes_for = [v.lower() for v in vote.get("votes_for", [])]
            votes_against = [v.lower() for v in vote.get("votes_against", [])]
            all_participants = {motion_by, second_by} | set(votes_for) | set(votes_against)

            for member in members:
                if member in all_participants:
                    role = []
                    if member == motion_by:
                        role.append("motioned")
                    if member == second_by:
                        role.append("seconded")
                    if member in votes_for:
                        role.append("voted_for")
                    if member in votes_against:
                        role.append("voted_against")

                    matches.append(AlertMatch(
                        id=str(uuid.uuid4()),
                        alert_id=alert.id,
                        clip_id=clip_id,
                        match_type="member",
                        matched_text=member,
                        context={
                            "meeting_date": meeting_date,
                            "vote": vote,
                            "roles": role,
                        },
                        created_at=datetime.now(timezone.utc).isoformat(),
                    ))
        return matches

    def _check_financial(self, alert: Alert, clip_id: str, facts: dict,
                         meeting_date: str) -> list[AlertMatch]:
        threshold = alert.config.get("threshold_cents")
        if threshold is None:
            threshold_dollars = alert.config.get("threshold")
            if threshold_dollars is not None:
                threshold = int(float(threshold_dollars) * 100)
            else:
                return []

        matches = []
        for item in facts.get("financial_items", []):
            cents = _parse_amount_cents(item.get("amount", ""))
            if cents is not None and cents >= threshold:
                matches.append(AlertMatch(
                    id=str(uuid.uuid4()),
                    alert_id=alert.id,
                    clip_id=clip_id,
                    match_type="financial",
                    matched_text=item.get("amount", ""),
                    context={
                        "meeting_date": meeting_date,
                        "item": item,
                        "amount_cents": cents,
                    },
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
        return matches

    def _check_vote(self, alert: Alert, clip_id: str, facts: dict,
                    meeting_date: str) -> list[AlertMatch]:
        target_outcome = alert.config.get("outcome", "").lower()
        if not target_outcome:
            return []

        matches = []
        for vote in facts.get("motions_and_votes", []):
            if (vote.get("outcome") or "").lower() == target_outcome:
                matches.append(AlertMatch(
                    id=str(uuid.uuid4()),
                    alert_id=alert.id,
                    clip_id=clip_id,
                    match_type="vote",
                    matched_text=f"{vote.get('identifier', 'Unknown')} - {target_outcome}",
                    context={
                        "meeting_date": meeting_date,
                        "vote": vote,
                    },
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
        return matches

    # ------------------------------------------------------------------
    # Ingest votes and financial items from extracted_facts
    # ------------------------------------------------------------------

    def ingest_clip(self, clip_id: str, extracted_facts: dict,
                    meeting_date: str = "", meeting_body: str = ""):
        """Ingest votes and financial items from a clip into searchable tables."""
        conn = self._conn()
        try:
            # Remove old data for this clip (idempotent re-ingest)
            conn.execute("DELETE FROM votes WHERE clip_id = ?", (clip_id,))
            conn.execute("DELETE FROM financial_items WHERE clip_id = ?", (clip_id,))

            for vote in extracted_facts.get("motions_and_votes", []):
                conn.execute(
                    "INSERT INTO votes (id, clip_id, identifier, description, motion_by, "
                    "second_by, outcome, vote_type, ayes, nays, abstentions, votes_for, "
                    "votes_against, conditions, transcript_approx_time, meeting_date, meeting_body) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), clip_id,
                        vote.get("identifier"), vote.get("description"),
                        vote.get("motion_by"), vote.get("second_by"),
                        vote.get("outcome"), vote.get("vote_type"),
                        vote.get("ayes", 0), vote.get("nays", 0),
                        vote.get("abstentions", 0),
                        json.dumps(vote.get("votes_for", [])),
                        json.dumps(vote.get("votes_against", [])),
                        vote.get("conditions"),
                        vote.get("transcript_approx_time"),
                        meeting_date, meeting_body,
                    ),
                )

            for item in extracted_facts.get("financial_items", []):
                cents = _parse_amount_cents(item.get("amount", ""))
                conn.execute(
                    "INSERT INTO financial_items (id, clip_id, description, amount, amount_cents, "
                    "type, identifier, vendor_or_recipient, meeting_date, meeting_body) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), clip_id,
                        item.get("description"), item.get("amount"), cents,
                        item.get("type"), item.get("identifier"),
                        item.get("vendor_or_recipient"),
                        meeting_date, meeting_body,
                    ),
                )

            conn.commit()
            logger.info("ingested_clip_tracker", extra={"clip_id": clip_id})
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Vote queries
    # ------------------------------------------------------------------

    def search_votes(self, member: str = "", date_after: str = "",
                     date_before: str = "", outcome: str = "",
                     keyword: str = "", limit: int = 50, offset: int = 0) -> dict:
        """Search votes with filters."""
        where = []
        params = []

        if member:
            where.append(
                "(LOWER(motion_by) LIKE ? OR LOWER(second_by) LIKE ? "
                "OR LOWER(votes_for) LIKE ? OR LOWER(votes_against) LIKE ?)"
            )
            m = f"%{member.lower()}%"
            params.extend([m, m, m, m])
        if date_after:
            where.append("meeting_date >= ?")
            params.append(date_after)
        if date_before:
            where.append("meeting_date <= ?")
            params.append(date_before)
        if outcome:
            where.append("LOWER(outcome) = ?")
            params.append(outcome.lower())
        if keyword:
            where.append("(LOWER(description) LIKE ? OR LOWER(identifier) LIKE ?)")
            k = f"%{keyword.lower()}%"
            params.extend([k, k])

        where_clause = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._conn()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM votes{where_clause}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT * FROM votes{where_clause} ORDER BY meeting_date DESC, clip_id "
                f"LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            return {
                "votes": [self._row_to_vote(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        finally:
            conn.close()

    def member_voting_record(self, name: str) -> dict:
        """Get a council member's complete voting record."""
        name_lower = name.lower()
        conn = self._conn()
        try:
            # Votes where member participated
            rows = conn.execute(
                "SELECT * FROM votes WHERE LOWER(motion_by) LIKE ? OR LOWER(second_by) LIKE ? "
                "OR LOWER(votes_for) LIKE ? OR LOWER(votes_against) LIKE ? "
                "ORDER BY meeting_date DESC",
                (f"%{name_lower}%", f"%{name_lower}%",
                 f"%{name_lower}%", f"%{name_lower}%"),
            ).fetchall()

            votes = [self._row_to_vote(r) for r in rows]

            # Calculate stats
            motioned = sum(1 for v in votes if name_lower in (v.get("motion_by", "") or "").lower())
            seconded = sum(1 for v in votes if name_lower in (v.get("second_by", "") or "").lower())
            voted_for = 0
            voted_against = 0
            for v in votes:
                vf = [x.lower() for x in (v.get("votes_for") or [])]
                va = [x.lower() for x in (v.get("votes_against") or [])]
                if name_lower in vf:
                    voted_for += 1
                if name_lower in va:
                    voted_against += 1

            return {
                "member": name,
                "total_votes": len(votes),
                "motioned": motioned,
                "seconded": seconded,
                "voted_for": voted_for,
                "voted_against": voted_against,
                "votes": votes,
            }
        finally:
            conn.close()

    def vote_stats(self) -> dict:
        """Aggregate vote statistics."""
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
            passed = conn.execute(
                "SELECT COUNT(*) FROM votes WHERE LOWER(outcome) = 'passed'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM votes WHERE LOWER(outcome) = 'failed'"
            ).fetchone()[0]

            # Most active movers
            movers = conn.execute(
                "SELECT motion_by, COUNT(*) as cnt FROM votes "
                "WHERE motion_by IS NOT NULL AND motion_by != '' "
                "GROUP BY LOWER(motion_by) ORDER BY cnt DESC LIMIT 10"
            ).fetchall()

            # Most contested (votes with nays > 0)
            contested = conn.execute(
                "SELECT * FROM votes WHERE nays > 0 ORDER BY nays DESC LIMIT 10"
            ).fetchall()

            # Votes by month
            by_month = conn.execute(
                "SELECT SUBSTR(meeting_date, 1, 7) as month, COUNT(*) as cnt "
                "FROM votes WHERE meeting_date IS NOT NULL AND meeting_date != '' "
                "GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall()

            return {
                "total_votes": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
                "most_active_movers": [
                    {"member": r["motion_by"], "count": r["cnt"]} for r in movers
                ],
                "most_contested": [self._row_to_vote(r) for r in contested],
                "votes_by_month": [
                    {"month": r["month"], "count": r["cnt"]} for r in by_month
                ],
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Financial queries
    # ------------------------------------------------------------------

    def search_financial(self, min_amount: Optional[int] = None,
                         max_amount: Optional[int] = None,
                         item_type: str = "", date_after: str = "",
                         date_before: str = "", keyword: str = "",
                         limit: int = 50, offset: int = 0) -> dict:
        """Search financial items with filters. Amounts in cents."""
        where = []
        params = []

        if min_amount is not None:
            where.append("amount_cents >= ?")
            params.append(min_amount)
        if max_amount is not None:
            where.append("amount_cents <= ?")
            params.append(max_amount)
        if item_type:
            where.append("LOWER(type) = ?")
            params.append(item_type.lower())
        if date_after:
            where.append("meeting_date >= ?")
            params.append(date_after)
        if date_before:
            where.append("meeting_date <= ?")
            params.append(date_before)
        if keyword:
            where.append("(LOWER(description) LIKE ? OR LOWER(vendor_or_recipient) LIKE ?)")
            k = f"%{keyword.lower()}%"
            params.extend([k, k])

        where_clause = (" WHERE " + " AND ".join(where)) if where else ""

        conn = self._conn()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM financial_items{where_clause}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT * FROM financial_items{where_clause} "
                f"ORDER BY amount_cents DESC NULLS LAST LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            return {
                "items": [self._row_to_financial(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        finally:
            conn.close()

    def financial_summary(self) -> dict:
        """Aggregate financial statistics."""
        conn = self._conn()
        try:
            total_items = conn.execute(
                "SELECT COUNT(*) FROM financial_items"
            ).fetchone()[0]

            total_amount = conn.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) FROM financial_items WHERE amount_cents IS NOT NULL"
            ).fetchone()[0]

            # By type
            by_type = conn.execute(
                "SELECT type, COUNT(*) as cnt, COALESCE(SUM(amount_cents), 0) as total "
                "FROM financial_items WHERE type IS NOT NULL AND type != '' "
                "GROUP BY LOWER(type) ORDER BY total DESC"
            ).fetchall()

            # By month
            by_month = conn.execute(
                "SELECT SUBSTR(meeting_date, 1, 7) as month, "
                "COUNT(*) as cnt, COALESCE(SUM(amount_cents), 0) as total "
                "FROM financial_items WHERE meeting_date IS NOT NULL AND meeting_date != '' "
                "GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall()

            # Top items
            top_items = conn.execute(
                "SELECT * FROM financial_items WHERE amount_cents IS NOT NULL "
                "ORDER BY amount_cents DESC LIMIT 10"
            ).fetchall()

            return {
                "total_items": total_items,
                "total_amount_cents": total_amount,
                "total_amount_display": f"${total_amount / 100:,.2f}" if total_amount else "$0.00",
                "by_type": [
                    {
                        "type": r["type"],
                        "count": r["cnt"],
                        "total_cents": r["total"],
                        "total_display": f"${r['total'] / 100:,.2f}",
                    }
                    for r in by_type
                ],
                "by_month": [
                    {
                        "month": r["month"],
                        "count": r["cnt"],
                        "total_cents": r["total"],
                        "total_display": f"${r['total'] / 100:,.2f}",
                    }
                    for r in by_month
                ],
                "top_items": [self._row_to_financial(r) for r in top_items],
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Digest builder
    # ------------------------------------------------------------------

    def build_digest(self, tenant_id: str, since: Optional[str] = None) -> dict:
        """Build a digest of recent alert matches for a tenant."""
        conn = self._conn()
        try:
            query = (
                "SELECT m.*, a.name as alert_name, a.type as alert_type "
                "FROM alert_matches m JOIN alerts a ON m.alert_id = a.id "
                "WHERE a.tenant_id = ?"
            )
            params: list = [tenant_id]
            if since:
                query += " AND m.created_at >= ?"
                params.append(since)
            query += " ORDER BY m.created_at DESC LIMIT 100"

            rows = conn.execute(query, params).fetchall()

            items = []
            for r in rows:
                items.append({
                    "alert_name": r["alert_name"],
                    "alert_type": r["alert_type"],
                    "clip_id": r["clip_id"],
                    "match_type": r["match_type"],
                    "matched_text": r["matched_text"],
                    "context": json.loads(r["context"]),
                    "created_at": r["created_at"],
                })

            return {
                "tenant_id": tenant_id,
                "since": since,
                "total_matches": len(items),
                "matches": items,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Bulk ingest from output directory
    # ------------------------------------------------------------------

    def ingest_all(self, output_dir: str):
        """Ingest all clips from the output directory."""
        clips_dir = os.path.join(output_dir, "clips")
        if not os.path.isdir(clips_dir):
            logger.warning("no clips directory found at %s", clips_dir)
            return 0

        count = 0
        for clip_id in sorted(os.listdir(clips_dir)):
            clip_path = os.path.join(clips_dir, clip_id)
            facts_path = os.path.join(clip_path, "extracted_facts.json")
            meta_path = os.path.join(clip_path, "metadata.json")

            if not os.path.isfile(facts_path):
                continue

            try:
                with open(facts_path) as f:
                    facts = json.load(f)
                meeting_date = ""
                meeting_body = ""
                if os.path.isfile(meta_path):
                    with open(meta_path) as f:
                        meta = json.load(f)
                    meeting_date = meta.get("date", "")
                    meeting_body = meta.get("meeting_body", "")

                self.ingest_clip(clip_id, facts, meeting_date, meeting_body)
                count += 1
            except Exception:
                logger.warning("failed to ingest clip %s for tracker", clip_id, exc_info=True)

        logger.info("tracker_ingest_complete", extra={"clips_ingested": count})
        return count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_alert(row) -> Alert:
        return Alert(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            type=row["type"],
            config=json.loads(row["config"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_vote(row) -> dict:
        return {
            "id": row["id"],
            "clip_id": row["clip_id"],
            "identifier": row["identifier"],
            "description": row["description"],
            "motion_by": row["motion_by"],
            "second_by": row["second_by"],
            "outcome": row["outcome"],
            "vote_type": row["vote_type"],
            "ayes": row["ayes"],
            "nays": row["nays"],
            "abstentions": row["abstentions"],
            "votes_for": json.loads(row["votes_for"]) if row["votes_for"] else [],
            "votes_against": json.loads(row["votes_against"]) if row["votes_against"] else [],
            "conditions": row["conditions"],
            "transcript_approx_time": row["transcript_approx_time"],
            "meeting_date": row["meeting_date"],
            "meeting_body": row["meeting_body"],
        }

    @staticmethod
    def _row_to_financial(row) -> dict:
        return {
            "id": row["id"],
            "clip_id": row["clip_id"],
            "description": row["description"],
            "amount": row["amount"],
            "amount_cents": row["amount_cents"],
            "type": row["type"],
            "identifier": row["identifier"],
            "vendor_or_recipient": row["vendor_or_recipient"],
            "meeting_date": row["meeting_date"],
            "meeting_body": row["meeting_body"],
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tracker: Optional[PolicyTracker] = None


def init_tracker(output_dir: str) -> PolicyTracker:
    global _tracker
    db_path = os.path.join(output_dir, "tracker.db")
    _tracker = PolicyTracker(db_path)
    return _tracker


def get_tracker() -> PolicyTracker:
    if _tracker is None:
        raise RuntimeError("PolicyTracker not initialized. Call init_tracker() first.")
    return _tracker
