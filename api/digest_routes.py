"""FastAPI routes for the email digest subscription system."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_admin, require_tenant
from api.digest import get_digest_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/digest", tags=["digest"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    email: str
    frequency: str = "weekly"
    meeting_bodies: Optional[list[str]] = None
    topics: Optional[list[str]] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("A valid email address is required")
        if len(v) > 254:
            raise ValueError("Email address is too long")
        return v

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: str) -> str:
        if v not in ("daily", "weekly"):
            raise ValueError("frequency must be 'daily' or 'weekly'")
        return v


class UpdatePreferencesRequest(BaseModel):
    email: str
    frequency: Optional[str] = None
    meeting_bodies: Optional[list[str]] = None
    topics: Optional[list[str]] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("A valid email address is required")
        return v

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("daily", "weekly"):
            raise ValueError("frequency must be 'daily' or 'weekly'")
        return v


class PreviewRequest(BaseModel):
    frequency: str = "weekly"
    since_days: int = 7

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: str) -> str:
        if v not in ("daily", "weekly"):
            raise ValueError("frequency must be 'daily' or 'weekly'")
        return v

    @field_validator("since_days")
    @classmethod
    def validate_since_days(cls, v: int) -> int:
        if v < 1 or v > 90:
            raise ValueError("since_days must be between 1 and 90")
        return v


class SendNowRequest(BaseModel):
    frequency: Optional[str] = None

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("daily", "weekly"):
            raise ValueError("frequency must be 'daily' or 'weekly'")
        return v


# ---------------------------------------------------------------------------
# Subscriber endpoints
# ---------------------------------------------------------------------------

@router.post("/subscribe")
def subscribe(request: SubscribeRequest, tenant: Tenant = Depends(require_tenant)):
    """Subscribe to the email digest.

    Starts a double opt-in flow: a confirmation email is sent to the
    provided address. The subscription becomes active only after
    the recipient clicks the confirmation link.
    """
    manager = get_digest_manager()
    try:
        result = manager.subscribe(
            tenant_id=tenant.id,
            email=request.email,
            frequency=request.frequency,
            meeting_bodies=request.meeting_bodies,
            topics=request.topics,
        )
        result["tenant"] = tenant.id
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Digest subscribe failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process subscription.")


@router.get("/confirm")
def confirm_subscription(
    subscriber_id: str = Query(..., description="Subscriber ID from confirmation email"),
    token: str = Query(..., description="Confirmation token"),
):
    """Confirm a digest subscription (double opt-in).

    This endpoint is called when the subscriber clicks the confirmation
    link in their email. No authentication required.
    """
    manager = get_digest_manager()
    result = manager.confirm(subscriber_id, token)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/preferences")
def get_preferences(
    email: str = Query(..., description="Subscriber email address"),
    tenant: Tenant = Depends(require_tenant),
):
    """Get digest preferences for a subscriber."""
    manager = get_digest_manager()
    prefs = manager.get_preferences(tenant.id, email)
    if prefs is None:
        raise HTTPException(status_code=404, detail="No active subscription found for this email.")
    prefs["tenant"] = tenant.id
    return prefs


@router.put("/preferences")
def update_preferences(
    request: UpdatePreferencesRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Update digest preferences (frequency, meeting bodies, topics)."""
    manager = get_digest_manager()
    try:
        result = manager.update_preferences(
            tenant_id=tenant.id,
            email=request.email,
            frequency=request.frequency,
            meeting_bodies=request.meeting_bodies,
            topics=request.topics,
        )
        if result["status"] == "error":
            raise HTTPException(status_code=404, detail=result["message"])
        result["tenant"] = tenant.id
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/unsubscribe")
def unsubscribe(token: str = Query(..., description="Unsubscribe token")):
    """One-click unsubscribe (CAN-SPAM compliant).

    No authentication required. The token uniquely identifies the
    subscription and cannot be guessed.
    """
    manager = get_digest_manager()
    result = manager.unsubscribe_by_token(token)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/preview")
def preview_digest(
    request: PreviewRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Preview what the next digest email would look like.

    Generates a digest without sending it, useful for testing.
    """
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=request.since_days)).isoformat()

    manager = get_digest_manager()
    # Build a mock subscriber dict for filtering
    mock_subscriber = {
        "frequency": request.frequency,
        "meeting_bodies": "[]",
        "topics": "[]",
        "last_digest_at": since,
    }
    digest = manager.generate_digest(tenant.id, subscriber=mock_subscriber, since=since)
    if digest is None:
        return {
            "status": "empty",
            "message": f"No meetings found in the last {request.since_days} days.",
            "tenant": tenant.id,
        }

    return {
        "status": "preview",
        "subject": digest["subject"],
        "html_body": digest["html_body"],
        "meeting_count": digest["meeting_count"],
        "votes_count": digest["votes_count"],
        "financial_count": digest["financial_count"],
        "tenant": tenant.id,
    }


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/subscribers", dependencies=[Depends(require_admin)])
def admin_list_subscribers(
    tenant_id: str = Query(..., description="Tenant ID to list subscribers for"),
    active_only: bool = Query(True, description="Only show active subscribers"),
):
    """List all digest subscribers for a tenant (admin only)."""
    manager = get_digest_manager()
    subscribers = manager.list_subscribers(tenant_id, active_only=active_only)
    return {
        "subscribers": subscribers,
        "count": len(subscribers),
        "tenant_id": tenant_id,
    }


@router.post("/admin/send-now", dependencies=[Depends(require_admin)])
def admin_send_now(
    request: SendNowRequest,
    tenant_id: str = Query(..., description="Tenant ID to send digests for"),
):
    """Trigger immediate digest send for a tenant (admin only).

    If frequency is specified, only sends to subscribers with that frequency.
    Otherwise sends to all active, confirmed subscribers.
    """
    manager = get_digest_manager()

    if request.frequency:
        result = manager.send_digests(tenant_id, frequency=request.frequency)
    else:
        result = manager.send_immediate(tenant_id)

    result["tenant_id"] = tenant_id
    return result
