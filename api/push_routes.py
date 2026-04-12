"""FastAPI routes for Web Push subscription management."""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.push import get_push_manager, get_vapid_public_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/push", tags=["push"])

OUTPUT_DIR = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class PushKeysModel(BaseModel):
    p256dh: str
    auth: str


class SubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeysModel

    @field_validator("endpoint")
    @classmethod
    def endpoint_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("endpoint must not be empty")
        if not v.startswith("https://"):
            raise ValueError("endpoint must be an HTTPS URL")
        return v


class UnsubscribeRequest(BaseModel):
    endpoint: str

    @field_validator("endpoint")
    @classmethod
    def endpoint_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("endpoint must not be empty")
        return v


class TestNotificationRequest(BaseModel):
    title: str = "Test Notification"
    body: str = "This is a test push notification from CivicLens."


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/vapid-key")
def get_vapid_key(tenant: Tenant = Depends(require_tenant)):
    """Return the VAPID public key needed for client push subscription."""
    try:
        public_key = get_vapid_public_key(OUTPUT_DIR)
        return {"public_key": public_key}
    except Exception as e:
        logger.error("Failed to get VAPID public key: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve VAPID key.")


@router.post("/subscribe")
def subscribe(request: SubscribeRequest, tenant: Tenant = Depends(require_tenant)):
    """Register a Web Push subscription for the authenticated tenant."""
    manager = get_push_manager()
    keys = {"p256dh": request.keys.p256dh, "auth": request.keys.auth}
    ok = manager.store.add(
        tenant_id=tenant.id,
        endpoint=request.endpoint,
        keys=keys,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to store subscription.")

    logger.info(
        "Push subscription registered: tenant=%s endpoint=%s",
        tenant.id,
        request.endpoint[:60],
    )
    return {"status": "subscribed", "tenant_id": tenant.id}


@router.delete("/subscribe")
def unsubscribe(request: UnsubscribeRequest, tenant: Tenant = Depends(require_tenant)):
    """Unregister a Web Push subscription."""
    manager = get_push_manager()
    removed = manager.store.remove(request.endpoint)
    if not removed:
        raise HTTPException(status_code=404, detail="Subscription not found.")

    logger.info(
        "Push subscription removed: tenant=%s endpoint=%s",
        tenant.id,
        request.endpoint[:60],
    )
    return {"status": "unsubscribed"}


@router.post("/test")
def send_test(
    request: TestNotificationRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Send a test push notification to all of the tenant's subscriptions."""
    manager = get_push_manager()
    result = manager.send_to_tenant(tenant.id, {
        "title": request.title,
        "body": request.body,
        "tag": "test",
        "url": "/",
    })

    if result["total"] == 0:
        raise HTTPException(
            status_code=404,
            detail="No push subscriptions found for this tenant. Subscribe first.",
        )

    return {
        "status": "sent",
        "sent": result["sent"],
        "failed": result["failed"],
        "total": result["total"],
    }


@router.get("/stats")
def subscription_stats(tenant: Tenant = Depends(require_tenant)):
    """Return push subscription count for the authenticated tenant."""
    manager = get_push_manager()
    count = manager.store.count(tenant.id)
    return {"tenant_id": tenant.id, "subscriptions": count}
