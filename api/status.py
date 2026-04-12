"""Status monitoring with SQLite storage for the CivicLens platform."""

import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and models
# ---------------------------------------------------------------------------

class ComponentState(str, Enum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    MAINTENANCE = "maintenance"


class IncidentStatus(str, Enum):
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class IncidentSeverity(str, Enum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


COMPONENTS = ["API", "Pipeline", "RAG Search", "Slack Integration", "Widget"]


@dataclass
class IncidentUpdate:
    id: str
    status: str
    message: str
    created_at: str


@dataclass
class Incident:
    id: str
    title: str
    status: str
    severity: str
    components_affected: list[str]
    updates: list[IncidentUpdate]
    created_at: str
    resolved_at: Optional[str] = None


@dataclass
class MaintenanceWindow:
    id: str
    title: str
    components: list[str]
    scheduled_start: str
    scheduled_end: str
    created_at: str


# ---------------------------------------------------------------------------
# StatusMonitor
# ---------------------------------------------------------------------------

_status_monitor: Optional["StatusMonitor"] = None


def get_status_monitor() -> "StatusMonitor":
    if _status_monitor is None:
        raise RuntimeError("StatusMonitor not initialized. Call init_status() first.")
    return _status_monitor


def init_status(output_dir: str) -> "StatusMonitor":
    global _status_monitor
    _status_monitor = StatusMonitor(output_dir)
    return _status_monitor


class StatusMonitor:
    """Tracks component health, incidents, and uptime in SQLite."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.db_path = os.path.join(output_dir, "status.db")
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS components (
                    name TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'operational',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS component_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT NOT NULL,
                    state TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_comp_hist_time
                    ON component_history(component, recorded_at);

                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    components_affected TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS incident_updates (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (incident_id) REFERENCES incidents(id)
                );

                CREATE TABLE IF NOT EXISTS maintenance_windows (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    components TEXT NOT NULL,
                    scheduled_start TEXT NOT NULL,
                    scheduled_end TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    latency_ms REAL,
                    error TEXT,
                    checked_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_health_time
                    ON health_checks(component, checked_at);
            """)
            # Seed components if empty
            now = datetime.now(timezone.utc).isoformat()
            for name in COMPONENTS:
                conn.execute(
                    "INSERT OR IGNORE INTO components (name, state, updated_at) VALUES (?, 'operational', ?)",
                    (name, now),
                )
            conn.commit()
        finally:
            conn.close()

    # -- Component state ---------------------------------------------------

    def get_all_components(self) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT name, state, updated_at FROM components ORDER BY name").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_component_state(self, name: str, state: str) -> bool:
        if state not in [s.value for s in ComponentState]:
            return False
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE components SET state = ?, updated_at = ? WHERE name = ?",
                (state, now, name),
            )
            if cur.rowcount == 0:
                return False
            # Record history point
            conn.execute(
                "INSERT INTO component_history (component, state, recorded_at) VALUES (?, ?, ?)",
                (name, state, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    # -- Uptime calculation ------------------------------------------------

    def get_uptime(self, component: str, hours: int) -> float:
        """Calculate uptime percentage for a component over the last N hours.

        Uses health check records. If no checks exist, returns 100.0 (assumed operational).
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as total, SUM(success) as ok FROM health_checks WHERE component = ? AND checked_at >= ?",
                (component, since),
            ).fetchone()
            total = row["total"]
            if total == 0:
                return 100.0
            ok = row["ok"] or 0
            return round((ok / total) * 100, 3)
        finally:
            conn.close()

    def get_all_uptimes(self) -> dict:
        """Return uptime percentages for all components across standard windows."""
        result = {}
        for comp in COMPONENTS:
            result[comp] = {
                "24h": self.get_uptime(comp, 24),
                "7d": self.get_uptime(comp, 7 * 24),
                "30d": self.get_uptime(comp, 30 * 24),
                "90d": self.get_uptime(comp, 90 * 24),
            }
        return result

    # -- Daily uptime bars (for 90-day view) --------------------------------

    def get_daily_status(self, component: str, days: int = 90) -> list[dict]:
        """Return per-day status for the last N days.

        Each entry: {date, status, uptime_pct}. Status is worst state that day.
        """
        conn = self._conn()
        try:
            results = []
            today = datetime.now(timezone.utc).date()
            for i in range(days - 1, -1, -1):
                day = today - timedelta(days=i)
                day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat()
                day_end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()

                row = conn.execute(
                    "SELECT COUNT(*) as total, SUM(success) as ok FROM health_checks WHERE component = ? AND checked_at >= ? AND checked_at <= ?",
                    (component, day_start, day_end),
                ).fetchone()
                total = row["total"]
                if total == 0:
                    pct = 100.0
                    status = "operational"
                else:
                    ok = row["ok"] or 0
                    pct = round((ok / total) * 100, 2)
                    if pct >= 99.5:
                        status = "operational"
                    elif pct >= 95.0:
                        status = "degraded"
                    elif pct >= 80.0:
                        status = "partial_outage"
                    else:
                        status = "major_outage"

                results.append({"date": day.isoformat(), "status": status, "uptime_pct": pct})
            return results
        finally:
            conn.close()

    # -- Incidents ---------------------------------------------------------

    def create_incident(
        self,
        title: str,
        severity: str,
        components_affected: list[str],
        message: str,
        status: str = "investigating",
    ) -> Incident:
        now = datetime.now(timezone.utc).isoformat()
        incident_id = f"inc-{uuid.uuid4().hex[:12]}"
        update_id = f"upd-{uuid.uuid4().hex[:12]}"

        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO incidents (id, title, status, severity, components_affected, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (incident_id, title, status, severity, json.dumps(components_affected), now),
            )
            conn.execute(
                "INSERT INTO incident_updates (id, incident_id, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (update_id, incident_id, status, message, now),
            )
            # Set affected components to degraded/outage
            comp_state = "major_outage" if severity == "critical" else "partial_outage" if severity == "major" else "degraded"
            for comp in components_affected:
                self.set_component_state(comp, comp_state)
            conn.commit()

            return Incident(
                id=incident_id,
                title=title,
                status=status,
                severity=severity,
                components_affected=components_affected,
                updates=[IncidentUpdate(id=update_id, status=status, message=message, created_at=now)],
                created_at=now,
            )
        finally:
            conn.close()

    def update_incident(self, incident_id: str, status: str, message: str) -> Optional[Incident]:
        now = datetime.now(timezone.utc).isoformat()
        update_id = f"upd-{uuid.uuid4().hex[:12]}"

        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if not row:
                return None

            resolved_at = now if status == "resolved" else None
            conn.execute(
                "UPDATE incidents SET status = ?, resolved_at = COALESCE(?, resolved_at) WHERE id = ?",
                (status, resolved_at, incident_id),
            )
            conn.execute(
                "INSERT INTO incident_updates (id, incident_id, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (update_id, incident_id, status, message, now),
            )

            # If resolved, restore affected components to operational
            if status == "resolved":
                components = json.loads(row["components_affected"])
                for comp in components:
                    self.set_component_state(comp, "operational")

            conn.commit()
            return self._load_incident(conn, incident_id)
        finally:
            conn.close()

    def get_incidents(self, days: int = 90) -> list[Incident]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id FROM incidents WHERE created_at >= ? ORDER BY created_at DESC",
                (since,),
            ).fetchall()
            return [self._load_incident(conn, r["id"]) for r in rows]
        finally:
            conn.close()

    def _load_incident(self, conn: sqlite3.Connection, incident_id: str) -> Incident:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        updates = conn.execute(
            "SELECT * FROM incident_updates WHERE incident_id = ? ORDER BY created_at ASC",
            (incident_id,),
        ).fetchall()
        return Incident(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            severity=row["severity"],
            components_affected=json.loads(row["components_affected"]),
            updates=[
                IncidentUpdate(id=u["id"], status=u["status"], message=u["message"], created_at=u["created_at"])
                for u in updates
            ],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    # -- Maintenance windows -----------------------------------------------

    def schedule_maintenance(
        self, title: str, components: list[str], scheduled_start: str, scheduled_end: str
    ) -> MaintenanceWindow:
        now = datetime.now(timezone.utc).isoformat()
        maint_id = f"mnt-{uuid.uuid4().hex[:12]}"

        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO maintenance_windows (id, title, components, scheduled_start, scheduled_end, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (maint_id, title, json.dumps(components), scheduled_start, scheduled_end, now),
            )
            conn.commit()
            return MaintenanceWindow(
                id=maint_id,
                title=title,
                components=components,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                created_at=now,
            )
        finally:
            conn.close()

    def get_upcoming_maintenance(self) -> list[MaintenanceWindow]:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM maintenance_windows WHERE scheduled_end >= ? ORDER BY scheduled_start ASC",
                (now,),
            ).fetchall()
            return [
                MaintenanceWindow(
                    id=r["id"],
                    title=r["title"],
                    components=json.loads(r["components"]),
                    scheduled_start=r["scheduled_start"],
                    scheduled_end=r["scheduled_end"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    # -- Health checks -----------------------------------------------------

    def record_health_check(self, component: str, success: bool, latency_ms: float = 0, error: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO health_checks (component, success, latency_ms, error, checked_at) VALUES (?, ?, ?, ?, ?)",
                (component, int(success), latency_ms, error, now),
            )
            conn.commit()
        finally:
            conn.close()

    async def run_health_checks(self, base_url: str = "http://127.0.0.1:8000"):
        """Run periodic health checks against local services."""
        # Check API health
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                start = time.perf_counter()
                resp = await client.get(f"{base_url}/health")
                latency = round((time.perf_counter() - start) * 1000, 1)
                ok = resp.status_code == 200
                self.record_health_check("API", ok, latency)
                if not ok:
                    self.set_component_state("API", "degraded")
                else:
                    self.set_component_state("API", "operational")
        except Exception as e:
            self.record_health_check("API", False, error=str(e))
            self.set_component_state("API", "major_outage")

        # Check RAG Search (ChromaDB)
        try:
            from api.ingest import get_chroma_collection
            start = time.perf_counter()
            coll = get_chroma_collection(self.output_dir)
            count = coll.count()
            latency = round((time.perf_counter() - start) * 1000, 1)
            self.record_health_check("RAG Search", True, latency)
            self.set_component_state("RAG Search", "operational")
        except Exception as e:
            self.record_health_check("RAG Search", False, error=str(e))
            self.set_component_state("RAG Search", "major_outage")

        # Check Pipeline state freshness
        try:
            state_path = os.path.join(self.output_dir, "state.json")
            if os.path.exists(state_path):
                mtime = os.path.getmtime(state_path)
                age_hours = (time.time() - mtime) / 3600
                # If state hasn't been updated in 48 hours, consider degraded
                ok = age_hours < 48
                self.record_health_check("Pipeline", ok, latency_ms=0)
                self.set_component_state("Pipeline", "operational" if ok else "degraded")
            else:
                self.record_health_check("Pipeline", True)
                self.set_component_state("Pipeline", "operational")
        except Exception as e:
            self.record_health_check("Pipeline", False, error=str(e))

        # Widget and Slack are passive -- just record a successful check if no incident
        for passive in ("Widget", "Slack Integration"):
            conn = self._conn()
            try:
                row = conn.execute("SELECT state FROM components WHERE name = ?", (passive,)).fetchone()
                if row and row["state"] == "operational":
                    self.record_health_check(passive, True)
            finally:
                conn.close()

    # -- Aggregate status --------------------------------------------------

    def overall_status(self) -> str:
        """Return the worst component state as the overall system status."""
        priority = {
            "major_outage": 0,
            "partial_outage": 1,
            "degraded": 2,
            "maintenance": 3,
            "operational": 4,
        }
        components = self.get_all_components()
        if not components:
            return "operational"
        worst = min(components, key=lambda c: priority.get(c["state"], 4))
        return worst["state"]

    def status_summary(self) -> dict:
        """Full public status summary."""
        components = self.get_all_components()
        active_incidents = [
            i for i in self.get_incidents(days=7)
            if i.status != "resolved"
        ]
        maintenance = self.get_upcoming_maintenance()

        return {
            "status": self.overall_status(),
            "components": components,
            "active_incidents": [asdict(i) for i in active_incidents],
            "scheduled_maintenance": [asdict(m) for m in maintenance],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
