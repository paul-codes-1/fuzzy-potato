"""FastAPI routes for the public status page and admin incident management."""

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from api.auth import require_admin
from api.status import (
    COMPONENTS,
    ComponentState,
    IncidentSeverity,
    IncidentStatus,
    get_status_monitor,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["status"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateIncidentRequest(BaseModel):
    title: str
    severity: str
    components_affected: list[str]
    message: str
    status: str = "investigating"

    @field_validator("severity")
    @classmethod
    def severity_valid(cls, v: str) -> str:
        valid = [s.value for s in IncidentSeverity]
        if v not in valid:
            raise ValueError(f"severity must be one of: {', '.join(valid)}")
        return v

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        valid = [s.value for s in IncidentStatus]
        if v not in valid:
            raise ValueError(f"status must be one of: {', '.join(valid)}")
        return v

    @field_validator("components_affected")
    @classmethod
    def components_valid(cls, v: list[str]) -> list[str]:
        for c in v:
            if c not in COMPONENTS:
                raise ValueError(f"Unknown component: {c}. Valid: {', '.join(COMPONENTS)}")
        return v


class UpdateIncidentRequest(BaseModel):
    status: str
    message: str

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        valid = [s.value for s in IncidentStatus]
        if v not in valid:
            raise ValueError(f"status must be one of: {', '.join(valid)}")
        return v


class ScheduleMaintenanceRequest(BaseModel):
    title: str
    components: list[str]
    scheduled_start: str
    scheduled_end: str

    @field_validator("components")
    @classmethod
    def components_valid(cls, v: list[str]) -> list[str]:
        for c in v:
            if c not in COMPONENTS:
                raise ValueError(f"Unknown component: {c}. Valid: {', '.join(COMPONENTS)}")
        return v


class UpdateComponentRequest(BaseModel):
    state: str

    @field_validator("state")
    @classmethod
    def state_valid(cls, v: str) -> str:
        valid = [s.value for s in ComponentState]
        if v not in valid:
            raise ValueError(f"state must be one of: {', '.join(valid)}")
        return v


# ---------------------------------------------------------------------------
# Public endpoints (no auth)
# ---------------------------------------------------------------------------

@router.get("/api/v1/status")
def get_status():
    """Public status summary -- all components and current state."""
    monitor = get_status_monitor()
    return monitor.status_summary()


@router.get("/api/v1/status/history")
def get_incident_history(days: int = 90):
    """Incident history for the last N days (default 90)."""
    monitor = get_status_monitor()
    incidents = monitor.get_incidents(days=days)
    return {"incidents": [asdict(i) for i in incidents]}


@router.get("/api/v1/status/uptime")
def get_uptime():
    """Uptime percentages per component across standard windows."""
    monitor = get_status_monitor()
    uptimes = monitor.get_all_uptimes()

    # Also include 90-day daily bars for each component
    daily = {}
    for comp in COMPONENTS:
        daily[comp] = monitor.get_daily_status(comp, days=90)

    return {"uptime": uptimes, "daily": daily}


# ---------------------------------------------------------------------------
# Admin endpoints (require ADMIN_API_KEY)
# ---------------------------------------------------------------------------

@router.post("/api/v1/admin/status/incident")
def admin_create_incident(
    request: CreateIncidentRequest,
    _: bool = Depends(require_admin),
):
    """Create a new incident."""
    monitor = get_status_monitor()
    incident = monitor.create_incident(
        title=request.title,
        severity=request.severity,
        components_affected=request.components_affected,
        message=request.message,
        status=request.status,
    )
    return asdict(incident)


@router.patch("/api/v1/admin/status/incident/{incident_id}")
def admin_update_incident(
    incident_id: str,
    request: UpdateIncidentRequest,
    _: bool = Depends(require_admin),
):
    """Add an update to an incident and change its status."""
    monitor = get_status_monitor()
    incident = monitor.update_incident(
        incident_id=incident_id,
        status=request.status,
        message=request.message,
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return asdict(incident)


@router.post("/api/v1/admin/status/maintenance")
def admin_schedule_maintenance(
    request: ScheduleMaintenanceRequest,
    _: bool = Depends(require_admin),
):
    """Schedule a maintenance window."""
    monitor = get_status_monitor()
    window = monitor.schedule_maintenance(
        title=request.title,
        components=request.components,
        scheduled_start=request.scheduled_start,
        scheduled_end=request.scheduled_end,
    )
    return asdict(window)


@router.patch("/api/v1/admin/status/component/{name}")
def admin_update_component(
    name: str,
    request: UpdateComponentRequest,
    _: bool = Depends(require_admin),
):
    """Manually override a component's status."""
    monitor = get_status_monitor()
    if not monitor.set_component_state(name, request.state):
        raise HTTPException(
            status_code=404,
            detail=f"Component not found or invalid state. Valid components: {', '.join(COMPONENTS)}",
        )
    return {"component": name, "state": request.state}
