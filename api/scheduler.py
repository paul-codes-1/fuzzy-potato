"""Automated per-tenant meeting ingestion scheduler using APScheduler."""

import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from api.auth import get_tenant_store
from api.notifications import NotificationManager
from api.saved_searches import execute_saved_search, get_saved_searches_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

DEFAULT_CRON = "0 */6 * * *"  # Every 6 hours
DEFAULT_MAX_CLIPS = 5
DEFAULT_SAVED_SEARCH_ALERT_INTERVAL_MINUTES = 10
MAX_CONCURRENT_JOBS = 2
MAX_HISTORY_PER_TENANT = 50


@dataclass
class ScheduleConfig:
    tenant_id: str
    cron_expression: str = DEFAULT_CRON
    max_clips: int = DEFAULT_MAX_CLIPS
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class JobRun:
    id: int
    tenant_id: str
    started_at: str
    finished_at: Optional[str]
    status: str  # "running", "success", "error"
    clips_processed: int
    error_message: Optional[str]


# ---------------------------------------------------------------------------
# SQLite store for schedule configs and job history
# ---------------------------------------------------------------------------

_CREATE_SCHEDULES = """
CREATE TABLE IF NOT EXISTS schedules (
    tenant_id TEXT PRIMARY KEY,
    cron_expression TEXT NOT NULL DEFAULT '0 */6 * * *',
    max_clips INTEGER NOT NULL DEFAULT 5,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_CREATE_JOB_HISTORY = """
CREATE TABLE IF NOT EXISTS job_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    clips_processed INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (tenant_id) REFERENCES schedules(tenant_id)
);
"""

_CREATE_JOB_HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_job_history_tenant
ON job_history(tenant_id, started_at DESC);
"""


class SchedulerStore:
    """SQLite persistence for schedule configs and job execution history."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            _CREATE_SCHEDULES + _CREATE_JOB_HISTORY + _CREATE_JOB_HISTORY_INDEX
        )
        self._conn.commit()
        self._lock = threading.Lock()

    # -- schedule CRUD -------------------------------------------------------

    def get_schedule(self, tenant_id: str) -> Optional[ScheduleConfig]:
        row = self._conn.execute(
            "SELECT * FROM schedules WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if not row:
            return None
        return ScheduleConfig(
            tenant_id=row["tenant_id"],
            cron_expression=row["cron_expression"],
            max_clips=row["max_clips"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_schedule(self, config: ScheduleConfig) -> ScheduleConfig:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existing = self.get_schedule(config.tenant_id)
            if existing:
                self._conn.execute(
                    """UPDATE schedules
                       SET cron_expression = ?, max_clips = ?, enabled = ?, updated_at = ?
                       WHERE tenant_id = ?""",
                    (config.cron_expression, config.max_clips, int(config.enabled),
                     now, config.tenant_id),
                )
            else:
                self._conn.execute(
                    """INSERT INTO schedules
                       (tenant_id, cron_expression, max_clips, enabled, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (config.tenant_id, config.cron_expression, config.max_clips,
                     int(config.enabled), now, now),
                )
            self._conn.commit()
        return self.get_schedule(config.tenant_id)  # type: ignore[return-value]

    def list_all_schedules(self) -> list[ScheduleConfig]:
        rows = self._conn.execute(
            "SELECT * FROM schedules ORDER BY tenant_id"
        ).fetchall()
        return [
            ScheduleConfig(
                tenant_id=r["tenant_id"],
                cron_expression=r["cron_expression"],
                max_clips=r["max_clips"],
                enabled=bool(r["enabled"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def delete_schedule(self, tenant_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM schedules WHERE tenant_id = ?", (tenant_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    # -- job history ---------------------------------------------------------

    def start_job(self, tenant_id: str) -> int:
        """Record a job starting. Returns the job run ID."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO job_history (tenant_id, started_at, status, clips_processed)
                   VALUES (?, ?, 'running', 0)""",
                (tenant_id, now),
            )
            self._conn.commit()
            run_id = cur.lastrowid
            # Trim old history
            self._trim_history(tenant_id)
        return run_id  # type: ignore[return-value]

    def finish_job(
        self, run_id: int, status: str, clips_processed: int = 0, error_message: Optional[str] = None
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """UPDATE job_history
                   SET finished_at = ?, status = ?, clips_processed = ?, error_message = ?
                   WHERE id = ?""",
                (now, status, clips_processed, error_message, run_id),
            )
            self._conn.commit()

    def get_history(self, tenant_id: str, limit: int = 20) -> list[JobRun]:
        rows = self._conn.execute(
            """SELECT * FROM job_history
               WHERE tenant_id = ?
               ORDER BY started_at DESC
               LIMIT ?""",
            (tenant_id, limit),
        ).fetchall()
        return [self._row_to_job_run(r) for r in rows]

    def get_all_active_jobs(self) -> list[JobRun]:
        rows = self._conn.execute(
            "SELECT * FROM job_history WHERE status = 'running' ORDER BY started_at DESC"
        ).fetchall()
        return [self._row_to_job_run(r) for r in rows]

    def _row_to_job_run(self, row: sqlite3.Row) -> JobRun:
        return JobRun(
            id=row["id"],
            tenant_id=row["tenant_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            clips_processed=row["clips_processed"],
            error_message=row["error_message"],
        )

    def _trim_history(self, tenant_id: str):
        """Keep only the most recent MAX_HISTORY_PER_TENANT runs."""
        self._conn.execute(
            """DELETE FROM job_history
               WHERE tenant_id = ? AND id NOT IN (
                   SELECT id FROM job_history
                   WHERE tenant_id = ?
                   ORDER BY started_at DESC
                   LIMIT ?
               )""",
            (tenant_id, tenant_id, MAX_HISTORY_PER_TENANT),
        )

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _run_pipeline_for_tenant(
    tenant_id: str,
    max_clips: int,
    output_dir: str,
    store: "SchedulerStore",
):
    """Execute the meeting pipeline for a tenant as a subprocess."""
    run_id = store.start_job(tenant_id)
    logger.info("scheduler_job_started", extra={"tenant_id": tenant_id, "run_id": run_id})

    try:
        cmd = [
            sys.executable, "-m", "main",
            "--tenant-id", tenant_id,
            "--scrape",
            "--rag",
            "--max", str(max_clips),
            "--output-dir", output_dir,
            "--quiet",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max per job
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        # Try to parse clips processed from output
        clips_processed = _parse_clips_processed(result.stdout)

        if result.returncode == 0:
            store.finish_job(run_id, "success", clips_processed=clips_processed)
            logger.info(
                "scheduler_job_completed",
                extra={"tenant_id": tenant_id, "run_id": run_id, "clips": clips_processed},
            )
        else:
            error_msg = result.stderr[-500:] if result.stderr else f"Exit code {result.returncode}"
            store.finish_job(run_id, "error", clips_processed=clips_processed, error_message=error_msg)
            logger.error(
                "scheduler_job_failed",
                extra={"tenant_id": tenant_id, "run_id": run_id, "error": error_msg},
            )

    except subprocess.TimeoutExpired:
        store.finish_job(run_id, "error", error_message="Job timed out after 3600 seconds")
        logger.error("scheduler_job_timeout", extra={"tenant_id": tenant_id, "run_id": run_id})
    except Exception as e:
        store.finish_job(run_id, "error", error_message=str(e)[:500])
        logger.error(
            "scheduler_job_exception",
            extra={"tenant_id": tenant_id, "run_id": run_id, "error": str(e)},
        )


def _parse_clips_processed(stdout: str) -> int:
    """Try to count processed clips from pipeline stdout."""
    count = 0
    for line in stdout.splitlines():
        if "Successfully processed clip" in line or "Processing complete" in line:
            count += 1
    return count


def _build_saved_search_summary(saved, result_type: str, results) -> list[str]:
    """Build concise summary lines for a saved-search alert email."""
    if result_type == "meeting_search":
        items = (results or {}).get("results", []) if isinstance(results, dict) else []
        total = (results or {}).get("total", len(items)) if isinstance(results, dict) else len(items)
        lines = [f"{total} meeting result(s) matched this search."]
        for item in items[:3]:
            title = item.get("title") or item.get("meeting_title") or f"Clip {item.get('clip_id', 'unknown')}"
            date = item.get("date") or "Unknown date"
            lines.append(f"{date}: {title}")
        return lines

    if result_type == "vote_search":
        items = (results or {}).get("votes", []) if isinstance(results, dict) else []
        total = (results or {}).get("total", len(items)) if isinstance(results, dict) else len(items)
        lines = [f"{total} vote result(s) matched this search."]
        for vote in items[:3]:
            identifier = vote.get("identifier") or "Unnumbered item"
            outcome = vote.get("outcome") or "unknown outcome"
            description = vote.get("description") or "No description"
            lines.append(f"{identifier}: {description} ({outcome})")
        if isinstance(results, dict) and results.get("error"):
            lines.append(f"Backend note: {results['error']}")
        return lines

    if result_type in {"rag_ask", "rag_chat"}:
        return [
            "This saved question was not auto-run in email alerts.",
            "CivicLens keeps RAG alerts quota-safe by sending the saved prompt instead of triggering a new AI response.",
        ]

    return [f"Saved search type: {saved.search_type}"]


def _saved_search_match_count(result_type: str, results) -> Optional[int]:
    """Return a concrete match count when the result type exposes one."""
    if result_type == "meeting_search":
        if isinstance(results, dict):
            return int(results.get("total", len(results.get("results", []))))
        return 0
    if result_type == "vote_search":
        if isinstance(results, dict):
            return int(results.get("total", len(results.get("votes", []))))
        return 0
    return None


def _run_saved_search_alerts(
    output_dir: str,
    notification_manager: Optional[NotificationManager] = None,
    now: Optional[datetime] = None,
) -> dict[str, int]:
    """Poll due saved-search alerts, send emails, and mark successful sends."""
    manager = get_saved_searches_manager()
    check_time = now or datetime.now(timezone.utc)
    due = manager.due_for_alert(now=check_time)
    if not due:
        return {"due": 0, "sent": 0, "skipped": 0, "failed": 0}

    if notification_manager is None:
        smtp_host = os.environ.get("SMTP_HOST")
        if not smtp_host:
            for saved in due:
                manager.record_alert_check(
                    tenant_id=saved.tenant_id,
                    saved_search_id=saved.id,
                    status="smtp_not_configured",
                    error="SMTP_HOST not configured",
                    checked_at=check_time,
                )
            logger.info("saved_search_alerts_skipped_no_smtp_host", extra={"due": len(due)})
            return {"due": len(due), "sent": 0, "skipped": len(due), "failed": 0}
        notification_manager = NotificationManager()

    tenant_store = get_tenant_store()
    app_base_url = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    action_url = f"{app_base_url}/saved-searches" if app_base_url else None

    sent = 0
    skipped = 0
    failed = 0

    for saved in due:
        if not saved.user_email:
            manager.record_alert_check(
                tenant_id=saved.tenant_id,
                saved_search_id=saved.id,
                status="no_email",
                error="No saved user email is available for this alert.",
                checked_at=check_time,
            )
            skipped += 1
            logger.info(
                "saved_search_alert_skipped_no_email",
                extra={"tenant_id": saved.tenant_id, "saved_search_id": saved.id},
            )
            continue

        try:
            result_type, results = execute_saved_search(saved)
            match_count = _saved_search_match_count(result_type, results)
            if match_count == 0:
                manager.record_alert_check(
                    tenant_id=saved.tenant_id,
                    saved_search_id=saved.id,
                    status="no_results",
                    matched_count=0,
                    checked_at=check_time,
                )
                skipped += 1
                continue
            summary_lines = _build_saved_search_summary(saved, result_type, results)
            tenant = tenant_store.get_by_id(saved.tenant_id)
            tenant_name = tenant.name if tenant else saved.tenant_id
            delivered = notification_manager.send_saved_search_alert(
                to=saved.user_email,
                tenant_name=tenant_name,
                saved_search_name=saved.name,
                search_type=saved.search_type,
                query_text=saved.query_text,
                summary_lines=summary_lines,
                result_count=match_count,
                action_url=action_url,
            )
            if delivered:
                manager.record_alert_check(
                    tenant_id=saved.tenant_id,
                    saved_search_id=saved.id,
                    status="sent",
                    matched_count=match_count,
                    checked_at=check_time,
                    sent=True,
                )
                sent += 1
            else:
                manager.record_alert_check(
                    tenant_id=saved.tenant_id,
                    saved_search_id=saved.id,
                    status="send_failed",
                    matched_count=match_count,
                    error="SMTP delivery failed",
                    checked_at=check_time,
                )
                failed += 1
        except Exception as e:
            manager.record_alert_check(
                tenant_id=saved.tenant_id,
                saved_search_id=saved.id,
                status="error",
                error=str(e),
                checked_at=check_time,
            )
            failed += 1
            logger.exception(
                "saved_search_alert_failed",
                extra={"tenant_id": saved.tenant_id, "saved_search_id": saved.id},
            )

    logger.info(
        "saved_search_alerts_complete",
        extra={"due": len(due), "sent": sent, "skipped": skipped, "failed": failed},
    )
    return {"due": len(due), "sent": sent, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# MeetingScheduler
# ---------------------------------------------------------------------------

class MeetingScheduler:
    """Manages per-tenant cron jobs for automated meeting ingestion.

    Uses APScheduler with a ThreadPoolExecutor limited to MAX_CONCURRENT_JOBS
    to prevent overloading the system.
    """

    def __init__(self, output_dir: str):
        self._output_dir = output_dir
        self._db_path = os.path.join(output_dir, "scheduler.db")
        self._store = SchedulerStore(self._db_path)
        self._paused = False
        self._saved_search_alert_interval_minutes = max(
            1,
            int(
                os.environ.get(
                    "SAVED_SEARCH_ALERTS_INTERVAL_MINUTES",
                    str(DEFAULT_SAVED_SEARCH_ALERT_INTERVAL_MINUTES),
                )
            ),
        )

        executors = {
            "default": ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS),
        }
        job_defaults = {
            "coalesce": True,       # Collapse missed runs into one
            "max_instances": 1,     # One instance per job at a time
            "misfire_grace_time": 3600,  # Allow 1 hour grace for missed runs
        }

        self._scheduler = BackgroundScheduler(
            executors=executors,
            jobstores={"default": MemoryJobStore()},
            job_defaults=job_defaults,
        )

    @property
    def store(self) -> SchedulerStore:
        return self._store

    @property
    def is_paused(self) -> bool:
        return self._paused

    def start(self):
        """Start the scheduler and load all enabled tenant schedules."""
        self._scheduler.start()
        logger.info("scheduler_started")

        # Load existing schedules from DB
        schedules = self._store.list_all_schedules()
        loaded = 0
        for config in schedules:
            if config.enabled:
                self._add_or_update_job(config)
                loaded += 1
        self._add_saved_search_alert_job()

        logger.info("scheduler_loaded_jobs", extra={"count": loaded, "total": len(schedules)})

    def shutdown(self):
        """Gracefully shut down the scheduler."""
        logger.info("scheduler_shutting_down")
        self._scheduler.shutdown(wait=True)
        self._store.close()
        logger.info("scheduler_stopped")

    def register_tenant(self, tenant_id: str, cron_expression: str = DEFAULT_CRON, max_clips: int = DEFAULT_MAX_CLIPS, enabled: bool = True) -> ScheduleConfig:
        """Register or update a tenant's schedule."""
        config = ScheduleConfig(
            tenant_id=tenant_id,
            cron_expression=cron_expression,
            max_clips=max_clips,
            enabled=enabled,
        )
        config = self._store.upsert_schedule(config)

        if enabled and not self._paused:
            self._add_or_update_job(config)
        else:
            self._remove_job(tenant_id)

        logger.info(
            "scheduler_tenant_registered",
            extra={"tenant_id": tenant_id, "cron": cron_expression, "enabled": enabled},
        )
        return config

    def update_schedule(self, tenant_id: str, cron_expression: Optional[str] = None, max_clips: Optional[int] = None, enabled: Optional[bool] = None) -> Optional[ScheduleConfig]:
        """Update an existing tenant schedule. Returns None if not found."""
        existing = self._store.get_schedule(tenant_id)
        if not existing:
            return None

        if cron_expression is not None:
            existing.cron_expression = cron_expression
        if max_clips is not None:
            existing.max_clips = max_clips
        if enabled is not None:
            existing.enabled = enabled

        config = self._store.upsert_schedule(existing)

        if config.enabled and not self._paused:
            self._add_or_update_job(config)
        else:
            self._remove_job(tenant_id)

        return config

    def trigger_now(self, tenant_id: str) -> Optional[str]:
        """Trigger an immediate run for a tenant. Returns job ID or None if not found."""
        config = self._store.get_schedule(tenant_id)
        if not config:
            return None

        job_id = f"tenant_{tenant_id}"
        # Add a one-shot job that runs immediately
        run_job_id = f"tenant_{tenant_id}_manual_{int(time.time())}"
        self._scheduler.add_job(
            _run_pipeline_for_tenant,
            id=run_job_id,
            args=[tenant_id, config.max_clips, self._output_dir, self._store],
            replace_existing=False,
        )
        logger.info("scheduler_manual_trigger", extra={"tenant_id": tenant_id, "job_id": run_job_id})
        return run_job_id

    def get_schedule(self, tenant_id: str) -> Optional[ScheduleConfig]:
        return self._store.get_schedule(tenant_id)

    def get_history(self, tenant_id: str, limit: int = 20) -> list[JobRun]:
        return self._store.get_history(tenant_id, limit)

    def get_all_jobs(self) -> list[dict]:
        """Get all active scheduled jobs with their next run times."""
        jobs = self._scheduler.get_jobs()
        result = []
        for job in jobs:
            # Skip manual one-shot jobs
            if "_manual_" in job.id or not job.id.startswith("tenant_"):
                continue
            result.append({
                "job_id": job.id,
                "tenant_id": job.id.replace("tenant_", ""),
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "pending": job.pending,
            })
        return result

    def pause_all(self):
        """Pause all scheduled jobs."""
        self._paused = True
        self._scheduler.pause()
        logger.info("scheduler_paused_all")

    def resume_all(self):
        """Resume all scheduled jobs."""
        self._paused = False
        self._scheduler.resume()
        logger.info("scheduler_resumed_all")

    # -- internal helpers ----------------------------------------------------

    def _add_or_update_job(self, config: ScheduleConfig):
        """Add or reschedule a tenant's cron job."""
        job_id = f"tenant_{config.tenant_id}"
        try:
            trigger = CronTrigger.from_crontab(config.cron_expression)
        except ValueError as e:
            logger.error(
                "scheduler_invalid_cron",
                extra={"tenant_id": config.tenant_id, "cron": config.cron_expression, "error": str(e)},
            )
            return

        self._scheduler.add_job(
            _run_pipeline_for_tenant,
            trigger=trigger,
            id=job_id,
            args=[config.tenant_id, config.max_clips, self._output_dir, self._store],
            replace_existing=True,
        )

    def _remove_job(self, tenant_id: str):
        """Remove a tenant's scheduled job if it exists."""
        job_id = f"tenant_{tenant_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # Job may not exist

    def _add_saved_search_alert_job(self):
        """Add or update the shared saved-search alert poller."""
        self._scheduler.add_job(
            _run_saved_search_alerts,
            trigger=IntervalTrigger(minutes=self._saved_search_alert_interval_minutes),
            id="saved_search_alerts",
            args=[self._output_dir],
            replace_existing=True,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scheduler: Optional[MeetingScheduler] = None


def init_scheduler(output_dir: str) -> MeetingScheduler:
    """Initialize the global scheduler singleton."""
    global _scheduler
    _scheduler = MeetingScheduler(output_dir)
    return _scheduler


def get_scheduler() -> MeetingScheduler:
    """Get the global scheduler instance."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized -- call init_scheduler() first")
    return _scheduler
