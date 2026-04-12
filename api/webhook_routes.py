"""FastAPI routes for webhook management (per-tenant, authenticated)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.webhooks import VALID_EVENT_TYPES, get_webhook_manager

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RegisterWebhookRequest(BaseModel):
    url: str
    events: list[str]
    secret: Optional[str] = None

    @field_validator("url")
    @classmethod
    def url_must_be_https(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        if not v.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("Webhook URL must use HTTPS (localhost exempt for development)")
        return v

    @field_validator("events")
    @classmethod
    def events_must_be_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one event type is required")
        invalid = set(v) - VALID_EVENT_TYPES
        if invalid:
            raise ValueError(
                f"Invalid event types: {', '.join(sorted(invalid))}. "
                f"Valid: {', '.join(sorted(VALID_EVENT_TYPES))}"
            )
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("")
async def register_webhook(
    request: RegisterWebhookRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Register a new webhook for the authenticated tenant."""
    manager = get_webhook_manager()
    try:
        webhook = manager.register(
            tenant_id=tenant.id,
            url=request.url,
            events=request.events,
            secret=request.secret,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Include the secret only on creation so the caller can store it
    result = webhook.to_dict()
    result["secret"] = webhook.secret
    return result


@router.get("")
async def list_webhooks(tenant: Tenant = Depends(require_tenant)):
    """List all webhooks for the authenticated tenant."""
    manager = get_webhook_manager()
    webhooks = manager.list_webhooks(tenant.id)
    return {"webhooks": [w.to_dict() for w in webhooks]}


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Delete a webhook belonging to the authenticated tenant."""
    manager = get_webhook_manager()
    if not manager.unregister(tenant.id, webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"deleted": webhook_id}


@router.get("/{webhook_id}/deliveries")
async def get_deliveries(
    webhook_id: str,
    limit: int = 50,
    tenant: Tenant = Depends(require_tenant),
):
    """Get recent delivery log for a webhook."""
    manager = get_webhook_manager()

    # Verify webhook belongs to tenant
    webhook = manager.get_webhook(tenant.id, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    deliveries = manager.get_deliveries(tenant.id, webhook_id, limit=limit)
    return {"deliveries": [d.to_dict() for d in deliveries]}


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Send a test event to a specific webhook."""
    manager = get_webhook_manager()

    webhook = manager.get_webhook(tenant.id, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    delivery = await manager.send_test_event(tenant.id, webhook_id)
    if not delivery:
        raise HTTPException(status_code=500, detail="Failed to send test event")

    return {
        "delivery": delivery.to_dict(),
        "success": delivery.success,
    }
