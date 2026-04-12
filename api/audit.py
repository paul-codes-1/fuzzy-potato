"""Append-only audit logging system backed by SQLite.

Designed for government compliance requirements (SOC 2, FISMA).
The audit log is immutable -- rows are only ever inserted, never updated or deleted.
Retention cleanup is handled by a separate purge method that respects per-tenant policies.
"""

import csv
import hashlib
import io
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_ACTIONS = {
    "query.asked",
    "query.answered",
    "meeting.processed",
    "meeting.deleted",
    "tenant.created",
    "tenant.updated",
    "tenant.deleted",
    "api_key.rotated",
    "webhook.registered",
    "webhook.deleted",
    "export.started",
    "export.downloaded",
    "schedule.updated",
    "branding.updated",
    "billing.subscription_changed",
    "api.request",
}

VALID_RESOURCE_TYPES = {
    "query",
    "meeting",
    "tenant",
    "api_key",
    "webhook",
    "export",
    "schedule",
    "branding",
    "billing",
    "api",
}

DEFAULT_RETENTION_DAYS = 365
ENTERPRISE_RETENTION_DAYS = 2555  # ~7 years


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    timestamp_unix REAL NOT NULL,
    tenant_id TEXT NOT NULL,
    user_api_key TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    request_id TEXT,
    integrity_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_retention_policies (
    tenant_id TEXT PRIMARY KEY,
    retention_days INTEGER NOT NULL DEFAULT 365,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_log(tenant_id, timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id, timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_log(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp_unix);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mask_api_key(api_key: Optional[str]) -> Optional[str]:
    """Mask an API key for safe logging: mra_***last4."""
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "***"
    prefix = api_key[:4] if api_key.startswith("mra_") else ""
    last4 = api_key[-4:]
    return f"{prefix}***{last4}"


def _compute_integrity_hash(
    timestamp: str,
    tenant_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str],
    details: Optional[str],
    previous_hash: Optional[str],
) -> str:
    """Compute a SHA-256 hash that chains this entry to the previous one.

    This creates a tamper-evident chain: altering any earlier row invalidates
    every subsequent hash, making unauthorized modifications detectable.
    """
    payload = f"{previous_hash or ''}|{timestamp}|{tenant_id}|{action}|{resource_type}|{resource_id or ''}|{details or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Append-only audit logger backed by SQLite.

    Thread-safe (check_same_thread=False + WAL mode).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()
        self._last_hash: Optional[str] = self._load_last_hash()

    def _load_last_hash(self) -> Optional[str]:
        """Load the integrity hash of the most recent audit entry."""
        row = self._conn.execute(
            "SELECT integrity_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["integrity_hash"] if row else None

    # ------------------------------------------------------------------
    # Core logging
    # ------------------------------------------------------------------

    def log(
        self,
        tenant_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        details: Optional[dict] = None,
        user_api_key: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> int:
        """Append an audit event. Returns the new row ID.

        Parameters
        ----------
        tenant_id : str
            The tenant that triggered the event.
        action : str
            One of the VALID_ACTIONS constants.
        resource_type : str
            Category of the affected resource.
        resource_id : str, optional
            Identifier of the specific resource (clip ID, tenant ID, etc.).
        details : dict, optional
            Arbitrary JSON-serialisable context about the event.
        user_api_key : str, optional
            The raw API key -- will be masked before storage.
        ip_address : str, optional
            Client IP address.
        request_id : str, optional
            Correlation ID for the HTTP request.
        """
        now = datetime.now(timezone.utc)
        ts_iso = now.isoformat()
        ts_unix = now.timestamp()

        masked_key = mask_api_key(user_api_key)
        details_json = json.dumps(details) if details else None

        integrity_hash = _compute_integrity_hash(
            timestamp=ts_iso,
            tenant_id=tenant_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details_json,
            previous_hash=self._last_hash,
        )

        cursor = self._conn.execute(
            "INSERT INTO audit_log "
            "(timestamp, timestamp_unix, tenant_id, user_api_key, action, "
            "resource_type, resource_id, details, ip_address, request_id, integrity_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts_iso,
                ts_unix,
                tenant_id,
                masked_key,
                action,
                resource_type,
                resource_id,
                details_json,
                ip_address,
                request_id or str(uuid.uuid4()),
                integrity_hash,
            ),
        )
        self._conn.commit()
        self._last_hash = integrity_hash

        return cursor.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Search / filter
    # ------------------------------------------------------------------

    def search(
        self,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
        request_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Search the audit log with optional filters.

        Returns {"total": int, "events": list[dict], "limit": int, "offset": int}.
        """
        conditions = []
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if resource_type:
            conditions.append("resource_type = ?")
            params.append(resource_type)
        if resource_id:
            conditions.append("resource_id = ?")
            params.append(resource_id)
        if date_after:
            conditions.append("timestamp >= ?")
            params.append(date_after)
        if date_before:
            conditions.append("timestamp <= ?")
            params.append(date_before)
        if request_id:
            conditions.append("request_id = ?")
            params.append(request_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Total count
        count_row = self._conn.execute(
            f"SELECT COUNT(*) as total FROM audit_log {where}", params
        ).fetchone()
        total = count_row["total"]

        # Paginated results
        rows = self._conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        events = [self._row_to_dict(r) for r in rows]

        return {
            "total": total,
            "events": events,
            "limit": limit,
            "offset": offset,
        }

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(
        self,
        tenant_id: Optional[str] = None,
        days: int = 30,
    ) -> dict:
        """Summary statistics: events by type and by day."""
        cutoff = time.time() - (days * 86400)

        tenant_clause = ""
        params: list = [cutoff]
        if tenant_id:
            tenant_clause = "AND tenant_id = ?"
            params.append(tenant_id)

        # Events by action
        action_rows = self._conn.execute(
            f"SELECT action, COUNT(*) as count FROM audit_log "
            f"WHERE timestamp_unix >= ? {tenant_clause} "
            f"GROUP BY action ORDER BY count DESC",
            params,
        ).fetchall()

        # Events by day
        day_rows = self._conn.execute(
            f"SELECT date(timestamp) as day, COUNT(*) as count FROM audit_log "
            f"WHERE timestamp_unix >= ? {tenant_clause} "
            f"GROUP BY day ORDER BY day DESC",
            params,
        ).fetchall()

        # Total
        total_row = self._conn.execute(
            f"SELECT COUNT(*) as total FROM audit_log "
            f"WHERE timestamp_unix >= ? {tenant_clause}",
            params,
        ).fetchone()

        return {
            "days": days,
            "total_events": total_row["total"],
            "by_action": [
                {"action": r["action"], "count": r["count"]} for r in action_rows
            ],
            "by_day": [
                {"date": r["day"], "count": r["count"]} for r in day_rows
            ],
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_csv(
        self,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
    ) -> str:
        """Export matching audit events as a CSV string."""
        conditions = []
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if date_after:
            conditions.append("timestamp >= ?")
            params.append(date_after)
        if date_before:
            conditions.append("timestamp <= ?")
            params.append(date_before)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = self._conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY id ASC", params
        ).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "timestamp", "tenant_id", "user_api_key", "action",
            "resource_type", "resource_id", "details", "ip_address",
            "request_id", "integrity_hash",
        ])
        for r in rows:
            writer.writerow([
                r["id"], r["timestamp"], r["tenant_id"], r["user_api_key"],
                r["action"], r["resource_type"], r["resource_id"],
                r["details"], r["ip_address"], r["request_id"],
                r["integrity_hash"],
            ])
        return output.getvalue()

    def export_json(
        self,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
    ) -> list[dict]:
        """Export matching audit events as a list of dicts."""
        conditions = []
        params: list = []

        if tenant_id:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if date_after:
            conditions.append("timestamp >= ?")
            params.append(date_after)
        if date_before:
            conditions.append("timestamp <= ?")
            params.append(date_before)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = self._conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY id ASC", params
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    def verify_integrity(self, tenant_id: Optional[str] = None) -> dict:
        """Verify the integrity hash chain for tamper detection.

        Returns {"valid": bool, "checked": int, "first_broken": int | None}.
        """
        conditions = []
        params: list = []
        if tenant_id:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = self._conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY id ASC", params
        ).fetchall()

        previous_hash: Optional[str] = None
        checked = 0
        first_broken: Optional[int] = None

        for row in rows:
            expected = _compute_integrity_hash(
                timestamp=row["timestamp"],
                tenant_id=row["tenant_id"],
                action=row["action"],
                resource_type=row["resource_type"],
                resource_id=row["resource_id"],
                details=row["details"],
                previous_hash=previous_hash,
            )
            checked += 1
            if expected != row["integrity_hash"]:
                first_broken = row["id"]
                break
            previous_hash = row["integrity_hash"]

        return {
            "valid": first_broken is None,
            "checked": checked,
            "first_broken_id": first_broken,
        }

    # ------------------------------------------------------------------
    # Retention / purge
    # ------------------------------------------------------------------

    def set_retention_policy(self, tenant_id: str, retention_days: int) -> None:
        """Set or update the retention policy for a tenant."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO audit_retention_policies (tenant_id, retention_days, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET retention_days = ?, updated_at = ?",
            (tenant_id, retention_days, now, retention_days, now),
        )
        self._conn.commit()

    def get_retention_policy(self, tenant_id: str) -> int:
        """Get the retention period (days) for a tenant. Returns default if not set."""
        row = self._conn.execute(
            "SELECT retention_days FROM audit_retention_policies WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        return row["retention_days"] if row else DEFAULT_RETENTION_DAYS

    def purge_expired(self) -> int:
        """Delete audit entries older than each tenant's retention policy.

        Returns the total number of rows deleted.
        """
        # Get all tenant retention policies
        policies = self._conn.execute(
            "SELECT tenant_id, retention_days FROM audit_retention_policies"
        ).fetchall()

        tenant_retention = {r["tenant_id"]: r["retention_days"] for r in policies}

        total_deleted = 0
        now = time.time()

        # Purge per-tenant with custom retention
        for tenant_id, days in tenant_retention.items():
            cutoff = now - (days * 86400)
            cur = self._conn.execute(
                "DELETE FROM audit_log WHERE tenant_id = ? AND timestamp_unix < ?",
                (tenant_id, cutoff),
            )
            total_deleted += cur.rowcount

        # Purge any remaining tenants using default retention
        known_tenants = set(tenant_retention.keys())
        if known_tenants:
            placeholders = ",".join("?" * len(known_tenants))
            default_cutoff = now - (DEFAULT_RETENTION_DAYS * 86400)
            cur = self._conn.execute(
                f"DELETE FROM audit_log WHERE tenant_id NOT IN ({placeholders}) AND timestamp_unix < ?",
                list(known_tenants) + [default_cutoff],
            )
            total_deleted += cur.rowcount
        else:
            default_cutoff = now - (DEFAULT_RETENTION_DAYS * 86400)
            cur = self._conn.execute(
                "DELETE FROM audit_log WHERE timestamp_unix < ?",
                (default_cutoff,),
            )
            total_deleted += cur.rowcount

        self._conn.commit()

        if total_deleted > 0:
            logger.info("audit_purge_completed", extra={"rows_deleted": total_deleted})

        return total_deleted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        # Parse details JSON back to dict if present
        if d.get("details"):
            try:
                d["details"] = json.loads(d["details"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_audit_logger: Optional[AuditLogger] = None


def init_audit(output_dir: str) -> AuditLogger:
    """Initialize the global audit logger. Call once at startup."""
    global _audit_logger
    db_path = os.path.join(output_dir, "audit.db")
    _audit_logger = AuditLogger(db_path)
    logger.info("audit_logger_initialized", extra={"db_path": db_path})
    return _audit_logger


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger singleton."""
    if _audit_logger is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        return init_audit(output_dir)
    return _audit_logger
