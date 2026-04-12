"""FastAPI routes for the meeting ingestion scheduler."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_admin, require_tenant
from api.scheduler import get_scheduler, DEFAULT_CRON, DEFAULT_MAX_CLIPS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["scheduler"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class UpdateScheduleRequest(BaseModel):
    cron_expression: Optional[str] = None
    max_clips: Optional[int] = None
    enabled: Optional[bool] = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("cron_expression must not be empty")
        # Basic validation: should have 5 fields
        parts = v.split()
        if len(parts) != 5:
            raise ValueError("cron_expression must have exactly 5 fields (minute hour day month weekday)")
        return v

    @field_validator("max_clips")
    @classmethod
    def validate_max_clips(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 100):
            raise ValueError("max_clips must be between 1 and 100")
        return v


class TriggerNowRequest(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Tenant endpoints
# ---------------------------------------------------------------------------

@router.get("/schedule")
def get_schedule(tenant: Tenant = Depends(require_tenant)):
    """Get the tenant's current schedule configuration."""
    scheduler = get_scheduler()
    config = scheduler.get_schedule(tenant.id)
    if not config:
        return {
            "tenant_id": tenant.id,
            "cron_expression": DEFAULT_CRON,
            "max_clips": DEFAULT_MAX_CLIPS,
            "enabled": False,
            "created_at": None,
            "updated_at": None,
            "scheduled": False,
        }
    return {
        "tenant_id": config.tenant_id,
        "cron_expression": config.cron_expression,
        "max_clips": config.max_clips,
        "enabled": config.enabled,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
        "scheduled": True,
    }


@router.put("/schedule")
def update_schedule(
    request: UpdateScheduleRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Update the tenant's schedule configuration."""
    scheduler = get_scheduler()
    existing = scheduler.get_schedule(tenant.id)

    if existing:
        config = scheduler.update_schedule(
            tenant_id=tenant.id,
            cron_expression=request.cron_expression,
            max_clips=request.max_clips,
            enabled=request.enabled,
        )
    else:
        # Create new schedule
        config = scheduler.register_tenant(
            tenant_id=tenant.id,
            cron_expression=request.cron_expression or DEFAULT_CRON,
            max_clips=request.max_clips if request.max_clips is not None else DEFAULT_MAX_CLIPS,
            enabled=request.enabled if request.enabled is not None else True,
        )

    return {
        "tenant_id": config.tenant_id,
        "cron_expression": config.cron_expression,
        "max_clips": config.max_clips,
        "enabled": config.enabled,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


@router.get("/schedule/history")
def get_schedule_history(
    limit: int = 20,
    tenant: Tenant = Depends(require_tenant),
):
    """Get the tenant's job execution history."""
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")

    scheduler = get_scheduler()
    history = scheduler.get_history(tenant.id, limit=limit)
    return {
        "tenant_id": tenant.id,
        "runs": [
            {
                "id": run.id,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "clips_processed": run.clips_processed,
                "error_message": run.error_message,
            }
            for run in history
        ],
    }


@router.post("/schedule/run-now")
def trigger_run_now(tenant: Tenant = Depends(require_tenant)):
    """Trigger an immediate processing run for the tenant."""
    scheduler = get_scheduler()

    # Ensure tenant has a schedule registered (create default if not)
    config = scheduler.get_schedule(tenant.id)
    if not config:
        scheduler.register_tenant(tenant.id)

    job_id = scheduler.trigger_now(tenant.id)
    if not job_id:
        raise HTTPException(status_code=500, detail="Failed to trigger job")

    return {
        "tenant_id": tenant.id,
        "job_id": job_id,
        "status": "triggered",
        "message": "Processing job has been queued and will start shortly.",
    }


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/schedule/jobs")
def admin_list_jobs(_: bool = Depends(require_admin)):
    """List all active scheduled jobs across tenants."""
    scheduler = get_scheduler()
    jobs = scheduler.get_all_jobs()
    schedules = scheduler.store.list_all_schedules()
    active_runs = scheduler.store.get_all_active_jobs()

    return {
        "paused": scheduler.is_paused,
        "scheduled_jobs": jobs,
        "schedules": [
            {
                "tenant_id": s.tenant_id,
                "cron_expression": s.cron_expression,
                "max_clips": s.max_clips,
                "enabled": s.enabled,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in schedules
        ],
        "active_runs": [
            {
                "id": r.id,
                "tenant_id": r.tenant_id,
                "started_at": r.started_at,
                "status": r.status,
            }
            for r in active_runs
        ],
    }


@router.post("/admin/schedule/pause-all")
def admin_pause_all(_: bool = Depends(require_admin)):
    """Pause all scheduled processing across all tenants."""
    scheduler = get_scheduler()
    scheduler.pause_all()
    return {"status": "paused", "message": "All scheduled jobs have been paused."}


@router.post("/admin/schedule/resume-all")
def admin_resume_all(_: bool = Depends(require_admin)):
    """Resume all scheduled processing across all tenants."""
    scheduler = get_scheduler()
    scheduler.resume_all()
    return {"status": "resumed", "message": "All scheduled jobs have been resumed."}
