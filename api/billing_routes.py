"""FastAPI billing routes for Stripe integration.

Endpoints:
    POST /api/v1/billing/checkout  - Create Stripe Checkout session
    POST /api/v1/billing/portal    - Create Stripe Billing Portal session
    POST /api/v1/webhooks/stripe   - Stripe webhook handler
    GET  /api/v1/billing/usage     - Current billing period usage
"""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import Tenant, get_tenant_store, require_admin, require_tenant
from api.billing import BillingManager, verify_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy singleton for BillingManager
# ---------------------------------------------------------------------------

_billing_manager: BillingManager | None = None


def _get_billing_manager() -> BillingManager:
    global _billing_manager
    if _billing_manager is None:
        store = get_tenant_store()
        _billing_manager = BillingManager(store)
    return _billing_manager


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CheckoutRequest(BaseModel):
    success_url: str
    cancel_url: str


class PortalRequest(BaseModel):
    return_url: str


class ChangePlanRequest(BaseModel):
    plan: str


# ---------------------------------------------------------------------------
# Billing endpoints (tenant-authenticated)
# ---------------------------------------------------------------------------

@router.post("/api/v1/billing/checkout")
def create_checkout(request: CheckoutRequest, tenant: Tenant = Depends(require_tenant)):
    """Create a Stripe Checkout session for the tenant's current plan."""
    if tenant.id == "dev":
        raise HTTPException(status_code=400, detail="Billing not available in dev mode.")

    try:
        bm = _get_billing_manager()
        session = bm.create_checkout_session(
            tenant_id=tenant.id,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.StripeError as e:
        logger.error("Stripe error creating checkout for tenant %s: %s", tenant.id, e)
        raise HTTPException(status_code=502, detail="Billing service error.")


@router.post("/api/v1/billing/portal")
def create_portal(request: PortalRequest, tenant: Tenant = Depends(require_tenant)):
    """Create a Stripe Billing Portal session for self-service management."""
    if tenant.id == "dev":
        raise HTTPException(status_code=400, detail="Billing not available in dev mode.")

    try:
        bm = _get_billing_manager()
        session = bm.create_portal_session(
            tenant_id=tenant.id,
            return_url=request.return_url,
        )
        return {"portal_url": session.url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.StripeError as e:
        logger.error("Stripe error creating portal for tenant %s: %s", tenant.id, e)
        raise HTTPException(status_code=502, detail="Billing service error.")


@router.get("/api/v1/billing/usage")
def get_usage(tenant: Tenant = Depends(require_tenant)):
    """Get current billing period usage for the authenticated tenant."""
    if tenant.id == "dev":
        return {"tenant_id": "dev", "plan": "enterprise", "subscription_status": "dev_mode"}

    try:
        bm = _get_billing_manager()
        return bm.get_usage(tenant.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.StripeError as e:
        logger.error("Stripe error getting usage for tenant %s: %s", tenant.id, e)
        raise HTTPException(status_code=502, detail="Billing service error.")


# ---------------------------------------------------------------------------
# Admin billing endpoints
# ---------------------------------------------------------------------------

@router.post("/api/v1/admin/billing/{tenant_id}/change-plan")
def admin_change_plan(
    tenant_id: str,
    request: ChangePlanRequest,
    _: bool = Depends(require_admin),
):
    """Admin: change a tenant's plan with Stripe proration."""
    try:
        bm = _get_billing_manager()
        subscription = bm.change_plan(tenant_id, request.plan)
        return {
            "tenant_id": tenant_id,
            "new_plan": request.plan,
            "subscription_id": subscription.id,
            "status": subscription.get("status"),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.StripeError as e:
        logger.error("Stripe error changing plan for tenant %s: %s", tenant_id, e)
        raise HTTPException(status_code=502, detail="Billing service error.")


@router.post("/api/v1/admin/billing/{tenant_id}/cancel")
def admin_cancel_subscription(
    tenant_id: str,
    _: bool = Depends(require_admin),
):
    """Admin: cancel a tenant's subscription at period end."""
    try:
        bm = _get_billing_manager()
        subscription = bm.cancel_subscription(tenant_id, at_period_end=True)
        return {
            "tenant_id": tenant_id,
            "subscription_id": subscription.id,
            "cancel_at_period_end": subscription.get("cancel_at_period_end"),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.StripeError as e:
        logger.error("Stripe error canceling subscription for tenant %s: %s", tenant_id, e)
        raise HTTPException(status_code=502, detail="Billing service error.")


# ---------------------------------------------------------------------------
# Stripe webhook (no auth -- verified by signature)
# ---------------------------------------------------------------------------

@router.post("/api/v1/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events with signature verification."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header.")

    try:
        event = verify_webhook_signature(payload, sig_header)
    except stripe.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature.")
    except RuntimeError as e:
        logger.error("Webhook config error: %s", e)
        raise HTTPException(status_code=500, detail="Webhook not configured.")

    logger.info("Received Stripe webhook: %s (id=%s)", event["type"], event["id"])

    try:
        bm = _get_billing_manager()
        result = bm.handle_webhook_event(event)
        logger.info("Webhook result: %s", result)
        return {"received": True, **result}
    except Exception as e:
        logger.error("Error processing webhook %s: %s", event["type"], e, exc_info=True)
        # Return 200 to prevent Stripe retries for processing errors
        return {"received": True, "status": "error", "detail": str(e)}
