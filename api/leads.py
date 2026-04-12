"""Lead capture store backed by SQLite.

Collects marketing form submissions (demo requests, pricing inquiries,
partner/nonprofit applications), deduplicates near-identical submissions
within 24 hours, and exposes a minimal admin interface for triage.

Privacy note: IP addresses and User-Agent strings are hashed on write (sha256).
No raw network identifiers are persisted. Retention is enforced at the
application layer via administrative purge (see ``purge_older_than``).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


VALID_INTENTS = {
    "demo",
    "pilot",
    "pricing",
    "partnership",
    "nonprofit-discount",
    "support",
    "other",
}

VALID_STATUSES = {
    "new",
    "contacted",
    "qualified",
    "disqualified",
    "converted",
    "archived",
}

DEDUPE_WINDOW_SECONDS = 24 * 3600


_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    organization TEXT,
    role TEXT,
    phone TEXT,
    message TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT 'other',
    source_page TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    ip_address_hash TEXT,
    user_agent_hash TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    assigned_to TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_status_created ON leads(status, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_intent ON leads(intent);
"""


def _hash_value(value: Optional[str]) -> Optional[str]:
    """SHA-256 hash a string, returning the hex digest. ``None`` passes through."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _now() -> float:
    return time.time()


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass
class Lead:
    id: int
    public_id: str
    created_at: float
    updated_at: float
    name: str
    email: str
    organization: Optional[str]
    role: Optional[str]
    phone: Optional[str]
    message: str
    intent: str
    source_page: Optional[str]
    utm_source: Optional[str]
    utm_medium: Optional[str]
    utm_campaign: Optional[str]
    ip_address_hash: Optional[str]
    user_agent_hash: Optional[str]
    status: str
    assigned_to: Optional[str]
    notes: Optional[str]

    def to_dict(self, *, include_private_hashes: bool = False) -> dict:
        d = {
            "public_id": self.public_id,
            "created_at": _ts_to_iso(self.created_at),
            "updated_at": _ts_to_iso(self.updated_at),
            "name": self.name,
            "email": self.email,
            "organization": self.organization,
            "role": self.role,
            "phone": self.phone,
            "message": self.message,
            "intent": self.intent,
            "source_page": self.source_page,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "notes": self.notes,
        }
        if include_private_hashes:
            d["ip_address_hash"] = self.ip_address_hash
            d["user_agent_hash"] = self.user_agent_hash
        return d


class LeadStore:
    """SQLite-backed lead store."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_lead(self, row: sqlite3.Row) -> Lead:
        return Lead(**dict(row))

    @staticmethod
    def _normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    @staticmethod
    def _normalize_intent(intent: Optional[str]) -> str:
        intent = (intent or "other").strip().lower()
        # Accept common aliases from marketing CTAs.
        alias = {
            "nonprofit-pilot": "pilot",
            "nonprofit-demo": "demo",
            "partner": "partnership",
            "partner-referral": "partnership",
            "partner-solution": "partnership",
            "partner-strategic": "partnership",
        }
        intent = alias.get(intent, intent)
        if intent not in VALID_INTENTS:
            return "other"
        return intent

    # ------------------------------------------------------------------
    # Create / dedupe
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        email: str,
        message: str,
        organization: Optional[str] = None,
        role: Optional[str] = None,
        phone: Optional[str] = None,
        intent: Optional[str] = None,
        source_page: Optional[str] = None,
        utm_source: Optional[str] = None,
        utm_medium: Optional[str] = None,
        utm_campaign: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        now: Optional[float] = None,
    ) -> tuple[Lead, bool]:
        """Create a lead, or append to an existing record if the same email
        submitted within the dedupe window.

        Returns ``(lead, created)`` where ``created`` is ``False`` if an
        existing record was updated instead of a new one being inserted.
        """
        if not name or not name.strip():
            raise ValueError("name is required")
        if not email or "@" not in email:
            raise ValueError("valid email is required")
        if not message or not message.strip():
            raise ValueError("message is required")

        now = now if now is not None else _now()
        email_norm = self._normalize_email(email)
        intent_norm = self._normalize_intent(intent)
        ip_hash = _hash_value(ip_address)
        ua_hash = _hash_value(user_agent)

        # Dedupe window: same email within 24h -> append to message history
        cutoff = now - DEDUPE_WINDOW_SECONDS
        row = self._conn.execute(
            "SELECT * FROM leads WHERE email = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (email_norm, cutoff),
        ).fetchone()

        if row is not None:
            existing = self._row_to_lead(row)
            appended_message = (
                f"{existing.message}\n\n--- follow-up "
                f"{_ts_to_iso(now)} ---\n{message.strip()}"
            )
            self._conn.execute(
                "UPDATE leads SET message = ?, updated_at = ?, "
                "intent = CASE WHEN ? = 'other' THEN intent ELSE ? END "
                "WHERE id = ?",
                (appended_message, now, intent_norm, intent_norm, existing.id),
            )
            self._conn.commit()
            updated = self.get_by_id(existing.id)
            assert updated is not None
            return updated, False

        public_id = uuid.uuid4().hex
        cursor = self._conn.execute(
            "INSERT INTO leads (public_id, created_at, updated_at, name, email, "
            "organization, role, phone, message, intent, source_page, utm_source, "
            "utm_medium, utm_campaign, ip_address_hash, user_agent_hash, status, "
            "assigned_to, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, 'new', NULL, NULL)",
            (
                public_id,
                now,
                now,
                name.strip(),
                email_norm,
                (organization or "").strip() or None,
                (role or "").strip() or None,
                (phone or "").strip() or None,
                message.strip(),
                intent_norm,
                (source_page or "").strip() or None,
                (utm_source or "").strip() or None,
                (utm_medium or "").strip() or None,
                (utm_campaign or "").strip() or None,
                ip_hash,
                ua_hash,
            ),
        )
        self._conn.commit()
        lead = self.get_by_id(cursor.lastrowid)
        assert lead is not None
        return lead, True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_by_id(self, lead_id: int) -> Optional[Lead]:
        row = self._conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return self._row_to_lead(row) if row else None

    def get_by_public_id(self, public_id: str) -> Optional[Lead]:
        row = self._conn.execute(
            "SELECT * FROM leads WHERE public_id = ?", (public_id,)
        ).fetchone()
        return self._row_to_lead(row) if row else None

    def get(self, identifier: str | int) -> Optional[Lead]:
        """Look up a lead by public_id (preferred) or numeric id."""
        if isinstance(identifier, int):
            return self.get_by_id(identifier)
        if isinstance(identifier, str) and identifier.isdigit():
            lead = self.get_by_id(int(identifier))
            if lead is not None:
                return lead
        return self.get_by_public_id(str(identifier))

    def list(
        self,
        *,
        status: Optional[str] = None,
        intent: Optional[str] = None,
        search: Optional[str] = None,
        created_after: Optional[float] = None,
        created_before: Optional[float] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Lead]:
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if intent:
            clauses.append("intent = ?")
            params.append(self._normalize_intent(intent))
        if search:
            clauses.append(
                "(name LIKE ? OR organization LIKE ? OR email LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like])
        if created_after is not None:
            clauses.append("created_at >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at <= ?")
            params.append(created_before)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM leads {where} ORDER BY created_at DESC "
            f"LIMIT ? OFFSET ?"
        )
        params.extend([int(limit), int(offset)])
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_lead(r) for r in rows]

    def recent(self, limit: int = 20) -> list[Lead]:
        return self.list(limit=limit)

    def duplicates_by_email(self, email: str) -> list[Lead]:
        rows = self._conn.execute(
            "SELECT * FROM leads WHERE email = ? ORDER BY created_at DESC",
            (self._normalize_email(email),),
        ).fetchall()
        return [self._row_to_lead(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as c FROM leads GROUP BY status"
        ).fetchall()
        result = {s: 0 for s in VALID_STATUSES}
        for r in rows:
            result[r["status"]] = r["c"]
        return result

    def count_by_intent(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT intent, COUNT(*) as c FROM leads GROUP BY intent"
        ).fetchall()
        result = {i: 0 for i in VALID_INTENTS}
        for r in rows:
            if r["intent"] in result:
                result[r["intent"]] = r["c"]
        return result

    def count_since(self, cutoff_ts: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM leads WHERE created_at >= ?",
            (cutoff_ts,),
        ).fetchone()
        return row["c"] or 0

    def stats(self, now: Optional[float] = None) -> dict:
        now = now if now is not None else _now()
        return {
            "total": self.count_since(0),
            "today": self.count_since(now - 86400),
            "week": self.count_since(now - 7 * 86400),
            "month": self.count_since(now - 30 * 86400),
            "by_status": self.count_by_status(),
            "by_intent": self.count_by_intent(),
        }

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_status(
        self,
        lead_id: int | str,
        status: str,
        *,
        assigned_to: Optional[str] = None,
    ) -> Optional[Lead]:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}"
            )
        lead = self.get(lead_id)
        if lead is None:
            return None
        params = [status, _now()]
        sql = "UPDATE leads SET status = ?, updated_at = ?"
        if assigned_to is not None:
            sql += ", assigned_to = ?"
            params.append(assigned_to)
        sql += " WHERE id = ?"
        params.append(lead.id)
        self._conn.execute(sql, params)
        self._conn.commit()
        return self.get_by_id(lead.id)

    def add_note(self, lead_id: int | str, note: str, author: Optional[str] = None) -> Optional[Lead]:
        if not note or not note.strip():
            raise ValueError("note must not be empty")
        lead = self.get(lead_id)
        if lead is None:
            return None
        stamp = _ts_to_iso(_now())
        prefix = f"[{stamp}" + (f" by {author}]" if author else "]")
        entry = f"{prefix} {note.strip()}"
        new_notes = f"{lead.notes}\n{entry}" if lead.notes else entry
        self._conn.execute(
            "UPDATE leads SET notes = ?, updated_at = ? WHERE id = ?",
            (new_notes, _now(), lead.id),
        )
        self._conn.commit()
        return self.get_by_id(lead.id)

    # ------------------------------------------------------------------
    # Privacy / retention
    # ------------------------------------------------------------------

    def delete_by_email(self, email: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM leads WHERE email = ?",
            (self._normalize_email(email),),
        )
        self._conn.commit()
        return cursor.rowcount

    def purge_older_than(self, days: int) -> int:
        cutoff = _now() - days * 86400
        cursor = self._conn.execute(
            "DELETE FROM leads WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lead_store: Optional[LeadStore] = None


def init_lead_store(output_dir: str) -> LeadStore:
    """Initialize the global lead store. Call once at startup."""
    global _lead_store
    db_path = os.path.join(output_dir, "leads.db")
    _lead_store = LeadStore(db_path)
    return _lead_store


def get_lead_store() -> LeadStore:
    """Get the global lead store singleton."""
    if _lead_store is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        return init_lead_store(output_dir)
    return _lead_store
