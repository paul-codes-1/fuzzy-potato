"""Saved searches manager: persist user queries for one-click re-run.

Users frequently re-run the same queries (vote searches, RAG questions,
meeting FTS lookups). This module provides a small SQLite-backed store
that remembers those queries and optionally flags them for scheduled
email alerts.
"""

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)
_MISSING = object()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_SEARCH_TYPES = {
    "meeting_search",  # SQLite FTS search via api/search.py
    "vote_search",     # tracker.search_votes or tracker votes table
    "rag_ask",         # single-turn RAG (api/query.py ask)
    "rag_chat",        # multi-turn RAG chat seed question
}

VALID_ALERT_FREQUENCIES = {"off", "daily", "weekly"}

_FREQUENCY_DELTAS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SavedSearch:
    id: str
    tenant_id: str
    user_id: str
    user_email: Optional[str]
    name: str
    search_type: str
    query_text: str
    filters: dict = field(default_factory=dict)
    alert_enabled: bool = False
    alert_frequency: str = "off"
    last_run_at: Optional[str] = None
    last_alert_sent_at: Optional[str] = None
    last_alert_checked_at: Optional[str] = None
    last_alert_status: Optional[str] = None
    last_alert_match_count: Optional[int] = None
    last_alert_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "name": self.name,
            "search_type": self.search_type,
            "query_text": self.query_text,
            "filters": self.filters,
            "alert_enabled": self.alert_enabled,
            "alert_frequency": self.alert_frequency,
            "last_run_at": self.last_run_at,
            "last_alert_sent_at": self.last_alert_sent_at,
            "last_alert_checked_at": self.last_alert_checked_at,
            "last_alert_status": self.last_alert_status,
            "last_alert_match_count": self.last_alert_match_count,
            "last_alert_error": self.last_alert_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS saved_searches (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_email TEXT,
    name TEXT NOT NULL,
    search_type TEXT NOT NULL,
    query_text TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    alert_enabled INTEGER NOT NULL DEFAULT 0,
    alert_frequency TEXT NOT NULL DEFAULT 'off',
    last_run_at TEXT,
    last_alert_sent_at TEXT,
    last_alert_checked_at TEXT,
    last_alert_status TEXT,
    last_alert_match_count INTEGER,
    last_alert_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_CREATE_INDEX_TENANT = """
CREATE INDEX IF NOT EXISTS idx_saved_searches_tenant
    ON saved_searches(tenant_id);
"""

_CREATE_INDEX_USER = """
CREATE INDEX IF NOT EXISTS idx_saved_searches_tenant_user
    ON saved_searches(tenant_id, user_id);
"""


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SavedSearchesManager:
    """SQLite-backed CRUD store for saved searches + alert bookkeeping."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        parent = os.path.dirname(db_path) or "."
        os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX_TENANT)
        self._conn.execute(_CREATE_INDEX_USER)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema."""
        existing_cols = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(saved_searches)")
        }
        migrations = [
            ("user_email", "TEXT"),
            ("last_alert_checked_at", "TEXT"),
            ("last_alert_status", "TEXT"),
            ("last_alert_match_count", "INTEGER"),
            ("last_alert_error", "TEXT"),
        ]
        for column_name, column_type in migrations:
            if column_name not in existing_cols:
                self._conn.execute(
                    f"ALTER TABLE saved_searches ADD COLUMN {column_name} {column_type}"
                )
        self._conn.commit()

    # -- helpers -------------------------------------------------------------

    def _row_to_model(self, row: sqlite3.Row) -> SavedSearch:
        return SavedSearch(
            id=row["id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            user_email=row["user_email"],
            name=row["name"],
            search_type=row["search_type"],
            query_text=row["query_text"],
            filters=json.loads(row["filters_json"] or "{}"),
            alert_enabled=bool(row["alert_enabled"]),
            alert_frequency=row["alert_frequency"] or "off",
            last_run_at=row["last_run_at"],
            last_alert_sent_at=row["last_alert_sent_at"],
            last_alert_checked_at=row["last_alert_checked_at"],
            last_alert_status=row["last_alert_status"],
            last_alert_match_count=row["last_alert_match_count"],
            last_alert_error=row["last_alert_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _alert_anchor_at(self, saved: SavedSearch) -> Optional[datetime]:
        checked = self._parse_iso(saved.last_alert_checked_at)
        if checked is not None:
            return checked
        return self._parse_iso(saved.last_alert_sent_at)

    @staticmethod
    def _validate(
        search_type: str,
        alert_frequency: str,
        name: str,
        query_text: str,
    ) -> None:
        if search_type not in VALID_SEARCH_TYPES:
            raise ValueError(
                f"Invalid search_type '{search_type}'. "
                f"Choose from: {', '.join(sorted(VALID_SEARCH_TYPES))}"
            )
        if alert_frequency not in VALID_ALERT_FREQUENCIES:
            raise ValueError(
                f"Invalid alert_frequency '{alert_frequency}'. "
                f"Choose from: {', '.join(sorted(VALID_ALERT_FREQUENCIES))}"
            )
        if not name or not name.strip():
            raise ValueError("name must not be empty")
        if len(name) > 200:
            raise ValueError("name must be under 200 characters")
        if not query_text or not query_text.strip():
            raise ValueError("query_text must not be empty")
        if len(query_text) > 4000:
            raise ValueError("query_text must be under 4000 characters")

    # -- CRUD ----------------------------------------------------------------

    def create(
        self,
        tenant_id: str,
        user_id: str,
        user_email: Optional[str],
        name: str,
        search_type: str,
        query_text: str,
        filters: Optional[dict] = None,
        alert_enabled: bool = False,
        alert_frequency: str = "off",
    ) -> SavedSearch:
        self._validate(search_type, alert_frequency, name, query_text)
        if alert_enabled and alert_frequency == "off":
            # Enabling alerts without a cadence is a no-op; force daily default.
            alert_frequency = "daily"

        now = datetime.now(timezone.utc).isoformat()
        sid = str(uuid.uuid4())
        filters_json = json.dumps(filters or {})
        normalized_email = (user_email or "").strip().lower() or None

        self._conn.execute(
            """
            INSERT INTO saved_searches
                (id, tenant_id, user_id, user_email, name, search_type, query_text,
                 filters_json, alert_enabled, alert_frequency,
                 last_run_at, last_alert_sent_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                sid,
                tenant_id,
                user_id,
                normalized_email,
                name.strip(),
                search_type,
                query_text.strip(),
                filters_json,
                1 if alert_enabled else 0,
                alert_frequency,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get(tenant_id, sid)  # type: ignore[return-value]

    def get(self, tenant_id: str, saved_search_id: str) -> Optional[SavedSearch]:
        row = self._conn.execute(
            "SELECT * FROM saved_searches WHERE id = ? AND tenant_id = ?",
            (saved_search_id, tenant_id),
        ).fetchone()
        return self._row_to_model(row) if row else None

    def list_for_tenant(self, tenant_id: str) -> list[SavedSearch]:
        rows = self._conn.execute(
            "SELECT * FROM saved_searches WHERE tenant_id = ? "
            "ORDER BY datetime(created_at) DESC",
            (tenant_id,),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def list_for_user(self, tenant_id: str, user_id: str) -> list[SavedSearch]:
        rows = self._conn.execute(
            "SELECT * FROM saved_searches "
            "WHERE tenant_id = ? AND user_id = ? "
            "ORDER BY datetime(created_at) DESC",
            (tenant_id, user_id),
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def update(
        self,
        tenant_id: str,
        saved_search_id: str,
        *,
        name: Optional[str] = None,
        query_text: Optional[str] = None,
        filters: Optional[dict] = None,
        alert_enabled: Optional[bool] = None,
        alert_frequency: Optional[str] = None,
        user_email: Optional[str] | object = _MISSING,
    ) -> Optional[SavedSearch]:
        existing = self.get(tenant_id, saved_search_id)
        if not existing:
            return None

        new_name = name if name is not None else existing.name
        new_query = query_text if query_text is not None else existing.query_text
        new_filters = filters if filters is not None else existing.filters
        new_alert_enabled = (
            alert_enabled if alert_enabled is not None else existing.alert_enabled
        )
        new_frequency = (
            alert_frequency if alert_frequency is not None else existing.alert_frequency
        )
        new_user_email = existing.user_email
        if user_email is not _MISSING:
            new_user_email = (str(user_email).strip().lower() or None) if user_email else None

        # Re-validate. search_type is immutable after creation.
        self._validate(existing.search_type, new_frequency, new_name, new_query)

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE saved_searches
            SET name = ?, query_text = ?, filters_json = ?, user_email = ?,
                alert_enabled = ?, alert_frequency = ?, updated_at = ?
            WHERE id = ? AND tenant_id = ?
            """,
            (
                new_name.strip(),
                new_query.strip(),
                json.dumps(new_filters or {}),
                new_user_email,
                1 if new_alert_enabled else 0,
                new_frequency,
                now,
                saved_search_id,
                tenant_id,
            ),
        )
        self._conn.commit()
        return self.get(tenant_id, saved_search_id)

    def delete(self, tenant_id: str, saved_search_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM saved_searches WHERE id = ? AND tenant_id = ?",
            (saved_search_id, tenant_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_run(self, tenant_id: str, saved_search_id: str) -> bool:
        """Stamp last_run_at; called after a successful Run action."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE saved_searches SET last_run_at = ?, updated_at = ? "
            "WHERE id = ? AND tenant_id = ?",
            (now, now, saved_search_id, tenant_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- Alerts --------------------------------------------------------------

    def due_for_alert(self, now: Optional[datetime] = None) -> list[SavedSearch]:
        """Return saved searches whose alert cadence has elapsed.

        A search is due when:
          - alert_enabled = 1
          - alert_frequency in {daily, weekly}
          - no prior alert check exists, or the last alert check is older than
            the configured cadence window
        """
        current = now or datetime.now(timezone.utc)
        rows = self._conn.execute(
            "SELECT * FROM saved_searches WHERE alert_enabled = 1"
        ).fetchall()

        due: list[SavedSearch] = []
        for row in rows:
            model = self._row_to_model(row)
            delta = _FREQUENCY_DELTAS.get(model.alert_frequency)
            if delta is None:
                continue
            anchor = self._alert_anchor_at(model)
            if anchor is None:
                due.append(model)
                continue
            if current - anchor >= delta:
                due.append(model)
        return due

    def mark_alert_sent(self, tenant_id: str, saved_search_id: str) -> bool:
        return self.record_alert_check(
            tenant_id=tenant_id,
            saved_search_id=saved_search_id,
            status="sent",
            sent=True,
        )

    def record_alert_check(
        self,
        tenant_id: str,
        saved_search_id: str,
        *,
        status: str,
        matched_count: Optional[int] = None,
        error: Optional[str] = None,
        checked_at: Optional[datetime] = None,
        sent: bool = False,
    ) -> bool:
        timestamp = checked_at or datetime.now(timezone.utc)
        checked_iso = timestamp.isoformat()
        error_text = (error or "").strip()[:500] or None
        cur = self._conn.execute(
            "UPDATE saved_searches SET last_alert_checked_at = ?, last_alert_status = ?, "
            "last_alert_match_count = ?, last_alert_error = ?, "
            "last_alert_sent_at = COALESCE(?, last_alert_sent_at), updated_at = ? "
            "WHERE id = ? AND tenant_id = ?",
            (
                checked_iso,
                status,
                matched_count,
                error_text,
                checked_iso if sent else None,
                checked_iso,
                saved_search_id,
                tenant_id,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def next_alert_due_at(
        self,
        saved: SavedSearch,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        if not saved.alert_enabled:
            return None
        delta = _FREQUENCY_DELTAS.get(saved.alert_frequency)
        if delta is None:
            return None
        current = now or datetime.now(timezone.utc)
        anchor = self._alert_anchor_at(saved)
        if anchor is None:
            return current.isoformat()
        return (anchor + delta).isoformat()

    def list_alert_statuses(
        self,
        tenant_id: Optional[str] = None,
        now: Optional[datetime] = None,
        include_disabled: bool = False,
    ) -> list[dict]:
        current = now or datetime.now(timezone.utc)
        query = "SELECT * FROM saved_searches WHERE 1=1"
        params: list[Any] = []
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if not include_disabled:
            query += " AND alert_enabled = 1"
        query += " ORDER BY datetime(created_at) DESC"

        rows = self._conn.execute(query, params).fetchall()
        statuses = []
        for row in rows:
            saved = self._row_to_model(row)
            next_due_at = self.next_alert_due_at(saved, now=current)
            due_now = False
            if next_due_at:
                next_due = self._parse_iso(next_due_at)
                due_now = next_due is not None and current >= next_due
            statuses.append(
                {
                    "id": saved.id,
                    "tenant_id": saved.tenant_id,
                    "user_id": saved.user_id,
                    "user_email": saved.user_email,
                    "name": saved.name,
                    "search_type": saved.search_type,
                    "query_text": saved.query_text,
                    "alert_enabled": saved.alert_enabled,
                    "alert_frequency": saved.alert_frequency,
                    "last_alert_sent_at": saved.last_alert_sent_at,
                    "last_alert_checked_at": saved.last_alert_checked_at,
                    "last_alert_status": saved.last_alert_status,
                    "last_alert_match_count": saved.last_alert_match_count,
                    "last_alert_error": saved.last_alert_error,
                    "next_due_at": next_due_at,
                    "due_now": due_now,
                    "deliverable": bool(saved.user_email),
                }
            )
        return statuses

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_manager: Optional[SavedSearchesManager] = None


def init_saved_searches_manager(output_dir: str) -> SavedSearchesManager:
    """Initialize the saved searches manager singleton."""
    global _manager
    db_path = os.path.join(output_dir, "saved_searches.db")
    _manager = SavedSearchesManager(db_path)
    logger.info("SavedSearchesManager initialized at %s", db_path)
    return _manager


def get_saved_searches_manager() -> SavedSearchesManager:
    if _manager is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        return init_saved_searches_manager(output_dir)
    return _manager


def execute_saved_search(saved: SavedSearch) -> tuple[str, Any]:
    """Re-run a saved search using the same rules as the API route."""
    filters = saved.filters or {}
    result_type = saved.search_type
    results: Any

    if saved.search_type == "meeting_search":
        from api.search import get_search_engine

        engine = get_search_engine()
        results = engine.search(
            query=saved.query_text,
            type_filter=filters.get("type_filter", "all"),
            limit=int(filters.get("limit", 20)),
            offset=int(filters.get("offset", 0)),
            meeting_body=filters.get("meeting_body"),
            date_after=filters.get("date_after"),
            date_before=filters.get("date_before"),
            tenant_id=saved.tenant_id,
        )
    elif saved.search_type == "vote_search":
        try:
            from api.tracker import get_policy_tracker

            tracker = get_policy_tracker()
            if hasattr(tracker, "search_votes"):
                results = tracker.search_votes(
                    tenant_id=saved.tenant_id,
                    query=saved.query_text,
                    **{
                        k: v
                        for k, v in filters.items()
                        if k in {"outcome", "member", "date_after", "date_before", "limit"}
                    },
                )
            else:
                results = {
                    "votes": [],
                    "total": 0,
                    "note": "vote search backend unavailable",
                }
        except Exception as e:
            logger.warning("vote_search backend failed: %s", e)
            results = {"votes": [], "total": 0, "error": str(e)}
    elif saved.search_type in ("rag_ask", "rag_chat"):
        results = {
            "redirect": "ask" if saved.search_type == "rag_ask" else "chat",
            "question": saved.query_text,
            "filters": filters,
            "note": "RAG saved searches are not auto-run in alerts to avoid extra AI charges.",
        }
    else:
        raise ValueError(f"Unsupported search_type: {saved.search_type}")

    return result_type, results
