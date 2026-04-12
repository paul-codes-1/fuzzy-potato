"""FastAPI routes for the public meeting calendar system."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.calendar import get_calendar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    email: str
    meeting_body: Optional[str] = None
    days_before: int = 1

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("A valid email address is required")
        if len(v) > 254:
            raise ValueError("Email address is too long")
        return v

    @field_validator("days_before")
    @classmethod
    def validate_days_before(cls, v: int) -> int:
        if v < 1 or v > 14:
            raise ValueError("days_before must be between 1 and 14")
        return v


class UnsubscribeRequest(BaseModel):
    email: str
    meeting_body: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("A valid email address is required")
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
def calendar_view(
    meeting_body: Optional[str] = Query(None, description="Filter by meeting body"),
    months_back: int = Query(1, ge=0, le=12, description="Months of past meetings to include"),
    months_ahead: int = Query(3, ge=1, le=12, description="Months of future meetings to predict"),
    tenant: Tenant = Depends(require_tenant),
):
    """Get upcoming and recent meetings (combines historical and predicted).

    Returns meetings grouped by date, with predicted future meetings based on
    detected schedule patterns.
    """
    cal = get_calendar()
    view = cal.get_calendar_view(
        meeting_body=meeting_body,
        months_back=months_back,
        months_ahead=months_ahead,
    )
    view["tenant"] = tenant.id
    return view


@router.get("/ical")
def calendar_ical_all(tenant: Tenant = Depends(require_tenant)):
    """Download an iCalendar (.ics) feed with all meeting bodies."""
    cal = get_calendar()
    ics_content = cal.generate_ical()
    return Response(
        content=ics_content,
        media_type="text/calendar",
        headers={
            "Content-Disposition": "attachment; filename=civiclens_meetings.ics",
        },
    )


@router.get("/ical/{meeting_body}")
def calendar_ical_body(
    meeting_body: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Download an iCalendar (.ics) feed for a specific meeting body."""
    cal = get_calendar()

    # Validate meeting body exists
    bodies = [b["meeting_body"] for b in cal.get_meeting_bodies()]
    if meeting_body not in bodies:
        raise HTTPException(
            status_code=404,
            detail=f"Meeting body '{meeting_body}' not found. Available: {', '.join(bodies)}",
        )

    ics_content = cal.generate_ical(meeting_body=meeting_body)
    safe_name = meeting_body.replace(" ", "_").lower()
    return Response(
        content=ics_content,
        media_type="text/calendar",
        headers={
            "Content-Disposition": f"attachment; filename=civiclens_{safe_name}.ics",
        },
    )


@router.get("/schedule")
def calendar_schedules(tenant: Tenant = Depends(require_tenant)):
    """Get detected recurring schedule patterns per meeting body.

    Analyzes historical meeting data to identify patterns like
    "Council meets the first and third Tuesday of each month".
    """
    cal = get_calendar()

    # Re-detect to ensure fresh data
    profiles = cal.detect_schedules()
    if not profiles:
        profiles = cal.get_schedule_profiles()

    return {
        "schedules": profiles,
        "tenant": tenant.id,
    }


@router.post("/subscribe")
def calendar_subscribe(
    request: SubscribeRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Subscribe to meeting reminders via email.

    Receive a notification N days before a predicted meeting.
    """
    cal = get_calendar()
    result = cal.subscribe(
        email=request.email,
        meeting_body=request.meeting_body,
        days_before=request.days_before,
    )
    result["tenant"] = tenant.id
    return result


@router.post("/unsubscribe")
def calendar_unsubscribe(
    request: UnsubscribeRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Unsubscribe from meeting reminders."""
    cal = get_calendar()
    result = cal.unsubscribe(
        email=request.email,
        meeting_body=request.meeting_body,
    )
    result["tenant"] = tenant.id
    return result


@router.get("/google-url/{meeting_body}")
def calendar_google_url(
    meeting_body: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Get a Google Calendar subscribe URL for a meeting body.

    Returns a URL that opens Google Calendar with pre-filled event details
    including recurrence rules based on detected schedule patterns.
    """
    cal = get_calendar()

    bodies = [b["meeting_body"] for b in cal.get_meeting_bodies()]
    if meeting_body not in bodies:
        raise HTTPException(
            status_code=404,
            detail=f"Meeting body '{meeting_body}' not found. Available: {', '.join(bodies)}",
        )

    url = cal.google_calendar_url(meeting_body)
    return {
        "meeting_body": meeting_body,
        "google_calendar_url": url,
        "tenant": tenant.id,
    }


@router.get("/bodies")
def calendar_bodies(tenant: Tenant = Depends(require_tenant)):
    """List all meeting bodies with meeting counts and date ranges."""
    cal = get_calendar()
    return {
        "bodies": cal.get_meeting_bodies(),
        "tenant": tenant.id,
    }


@router.get("/reminders")
def calendar_reminders(tenant: Tenant = Depends(require_tenant)):
    """Get pending reminders that should be sent today."""
    cal = get_calendar()
    return {
        "reminders": cal.get_pending_reminders(),
        "tenant": tenant.id,
    }


@router.post("/ingest")
def calendar_ingest(tenant: Tenant = Depends(require_tenant)):
    """Re-ingest meeting data from clip metadata files.

    Scans all metadata.json files and updates the calendar database.
    Also re-runs schedule detection.
    """
    cal = get_calendar()
    count = cal.ingest_from_clips()
    profiles = cal.detect_schedules()
    return {
        "meetings_ingested": count,
        "schedules_detected": len(profiles),
        "tenant": tenant.id,
    }
