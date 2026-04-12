from __future__ import annotations

"""Stripe billing integration for CivicLens multi-tenant SaaS.

Handles customer creation, subscription management, usage metering,
and webhook processing for subscription lifecycle events.

Env vars:
    STRIPE_SECRET_KEY          - Stripe API secret key
    STRIPE_WEBHOOK_SECRET      - Webhook endpoint signing secret
    STRIPE_PRICE_STARTER       - Price ID for starter plan ($299/mo)
    STRIPE_PRICE_PRO           - Price ID for pro plan ($799/mo)
    STRIPE_PRICE_ENTERPRISE    - Price ID for enterprise plan ($2499/mo)
"""

import logging
import os
import time
import importlib
from typing import Optional

from api.auth import TenantStore, VALID_PLANS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan <-> Stripe price mapping
# ---------------------------------------------------------------------------

PLAN_PRICES = {
    "starter": "STRIPE_PRICE_STARTER",
    "pro": "STRIPE_PRICE_PRO",
    "enterprise": "STRIPE_PRICE_ENTERPRISE",
}


def _get_price_id(plan: str) -> str:
    """Resolve a plan name to a Stripe price ID from env vars."""
    env_key = PLAN_PRICES.get(plan)
    if not env_key:
        raise ValueError(f"Unknown plan: {plan}")
    price_id = os.environ.get(env_key)
    if not price_id:
        raise ValueError(f"Missing env var {env_key} for plan '{plan}'")
    return price_id


def _plan_from_price_id(price_id: str) -> Optional[str]:
    """Reverse lookup: Stripe price ID -> plan name."""
    for plan, env_key in PLAN_PRICES.items():
        if os.environ.get(env_key) == price_id:
            return plan
    return None


# ---------------------------------------------------------------------------
# Stripe client initialization
# ---------------------------------------------------------------------------

def _init_stripe():
    """Configure the stripe module with the secret key."""
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY environment variable is not set")
    _stripe().api_key = key


def _stripe():
    """Import Stripe lazily so tests and optional deployments can patch it."""
    return importlib.import_module("stripe")


# ---------------------------------------------------------------------------
# BillingManager
# ---------------------------------------------------------------------------

class BillingManager:
    """Manages Stripe billing operations for tenants."""

    def __init__(self, store: TenantStore):
        self._store = store
        _init_stripe()

    # -- Customer management -------------------------------------------------

    def create_customer(self, tenant_id: str, email: Optional[str] = None) -> str:
        """Create a Stripe customer for an existing tenant. Returns customer ID."""
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        if tenant.stripe_customer_id:
            logger.info("Tenant %s already has Stripe customer %s", tenant_id, tenant.stripe_customer_id)
            return tenant.stripe_customer_id

        stripe = _stripe()
        customer = stripe.Customer.create(
            name=tenant.name,
            email=email,
            metadata={"tenant_id": tenant_id},
            idempotency_key=f"create-customer-{tenant_id}",
        )

        self._store.update_stripe_customer_id(tenant_id, customer.id)
        logger.info("Created Stripe customer %s for tenant %s", customer.id, tenant_id)
        return customer.id

    # -- Subscription management ---------------------------------------------

    def create_subscription(self, tenant_id: str) -> stripe.Subscription:
        """Create a subscription for the tenant based on their current plan."""
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")
        if not tenant.stripe_customer_id:
            raise ValueError(f"Tenant {tenant_id} has no Stripe customer. Call create_customer first.")
        if tenant.stripe_subscription_id:
            raise ValueError(f"Tenant {tenant_id} already has subscription {tenant.stripe_subscription_id}")

        stripe = _stripe()
        price_id = _get_price_id(tenant.plan)

        subscription = stripe.Subscription.create(
            customer=tenant.stripe_customer_id,
            items=[{"price": price_id}],
            metadata={"tenant_id": tenant_id},
            idempotency_key=f"create-sub-{tenant_id}-{int(time.time())}",
        )

        self._store.update_stripe_subscription_id(tenant_id, subscription.id)
        logger.info("Created subscription %s for tenant %s (plan=%s)", subscription.id, tenant_id, tenant.plan)
        return subscription

    def create_checkout_session(
        self,
        tenant_id: str,
        success_url: str,
        cancel_url: str,
    ) -> stripe.checkout.Session:
        """Create a Stripe Checkout session for a new subscription."""
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        # Ensure customer exists
        if not tenant.stripe_customer_id:
            self.create_customer(tenant_id)
            tenant = self._store.get_by_id(tenant_id)

        stripe = _stripe()
        price_id = _get_price_id(tenant.plan)

        session = stripe.checkout.Session.create(
            customer=tenant.stripe_customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"tenant_id": tenant_id},
            idempotency_key=f"checkout-{tenant_id}-{int(time.time())}",
        )

        logger.info("Created checkout session %s for tenant %s", session.id, tenant_id)
        return session

    def create_portal_session(self, tenant_id: str, return_url: str) -> stripe.billing_portal.Session:
        """Create a Stripe Billing Portal session for self-service management."""
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")
        if not tenant.stripe_customer_id:
            raise ValueError(f"Tenant {tenant_id} has no Stripe customer.")

        stripe = _stripe()
        session = stripe.billing_portal.Session.create(
            customer=tenant.stripe_customer_id,
            return_url=return_url,
        )

        logger.info("Created portal session for tenant %s", tenant_id)
        return session

    def change_plan(self, tenant_id: str, new_plan: str) -> stripe.Subscription:
        """Upgrade or downgrade a tenant's subscription with proration.

        Updates both the Stripe subscription (prorated) and the local plan record.
        """
        if new_plan not in VALID_PLANS:
            raise ValueError(f"Invalid plan: {new_plan}")

        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")
        if not tenant.stripe_subscription_id:
            raise ValueError(f"Tenant {tenant_id} has no active subscription.")
        if tenant.plan == new_plan:
            raise ValueError(f"Tenant {tenant_id} is already on plan '{new_plan}'.")

        stripe = _stripe()
        new_price_id = _get_price_id(new_plan)

        # Retrieve the subscription to find the current item
        subscription = stripe.Subscription.retrieve(tenant.stripe_subscription_id)
        if not subscription.get("items", {}).get("data"):
            raise ValueError("Subscription has no items")

        current_item_id = subscription["items"]["data"][0]["id"]

        updated = stripe.Subscription.modify(
            tenant.stripe_subscription_id,
            items=[{
                "id": current_item_id,
                "price": new_price_id,
            }],
            proration_behavior="create_prorations",
            metadata={"tenant_id": tenant_id, "plan": new_plan},
            idempotency_key=f"change-plan-{tenant_id}-{new_plan}-{int(time.time())}",
        )

        self._store.update_plan(tenant_id, new_plan)
        logger.info("Changed plan for tenant %s: %s -> %s", tenant_id, tenant.plan, new_plan)
        return updated

    def cancel_subscription(self, tenant_id: str, at_period_end: bool = True) -> stripe.Subscription:
        """Cancel a tenant's subscription.

        By default cancels at the end of the current billing period.
        """
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")
        if not tenant.stripe_subscription_id:
            raise ValueError(f"Tenant {tenant_id} has no active subscription.")

        stripe = _stripe()
        if at_period_end:
            updated = stripe.Subscription.modify(
                tenant.stripe_subscription_id,
                cancel_at_period_end=True,
            )
        else:
            updated = stripe.Subscription.cancel(tenant.stripe_subscription_id)

        logger.info("Cancelled subscription for tenant %s (at_period_end=%s)", tenant_id, at_period_end)
        return updated

    # -- Usage metering ------------------------------------------------------

    def report_usage(self, tenant_id: str, quantity: int = 1) -> None:
        """Report query usage for metered billing.

        Creates a usage record on the tenant's subscription item.
        This is called after each successful API query.
        """
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            logger.warning("Cannot report usage: tenant %s not found", tenant_id)
            return
        if not tenant.stripe_subscription_id:
            logger.debug("Tenant %s has no subscription, skipping usage report", tenant_id)
            return

        try:
            stripe = _stripe()
            subscription = stripe.Subscription.retrieve(tenant.stripe_subscription_id)
            if not subscription.get("items", {}).get("data"):
                logger.warning("Subscription %s has no items", tenant.stripe_subscription_id)
                return

            item_id = subscription["items"]["data"][0]["id"]

            stripe.SubscriptionItem.create_usage_record(
                item_id,
                quantity=quantity,
                timestamp=int(time.time()),
                action="increment",
                idempotency_key=f"usage-{tenant_id}-{int(time.time() * 1000)}",
            )
            logger.debug("Reported %d query(ies) for tenant %s", quantity, tenant_id)
        except _stripe().StripeError as e:
            # Usage reporting should not block the API response
            logger.error("Failed to report usage for tenant %s: %s", tenant_id, e)

    def get_usage(self, tenant_id: str) -> dict:
        """Get current billing period usage for a tenant."""
        tenant = self._store.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")

        result = {
            "tenant_id": tenant_id,
            "plan": tenant.plan,
            "monthly_limit": tenant.monthly_limit,
        }

        if not tenant.stripe_subscription_id:
            result["subscription_status"] = "none"
            return result

        try:
            stripe = _stripe()
            subscription = stripe.Subscription.retrieve(tenant.stripe_subscription_id)
            result["subscription_status"] = subscription.get("status", "unknown")
            result["current_period_start"] = subscription.get("current_period_start")
            result["current_period_end"] = subscription.get("current_period_end")

            # Retrieve invoices for the current period to estimate usage
            if tenant.stripe_customer_id:
                upcoming = stripe.Invoice.upcoming(customer=tenant.stripe_customer_id)
                usage_lines = [
                    line for line in upcoming.get("lines", {}).get("data", [])
                    if line.get("quantity") is not None
                ]
                total_usage = sum(line.get("quantity", 0) for line in usage_lines)
                result["queries_used"] = total_usage
                result["amount_due_cents"] = upcoming.get("amount_due", 0)

        except _stripe().StripeError as e:
            logger.error("Failed to get usage for tenant %s: %s", tenant_id, e)
            result["error"] = str(e)

        return result

    # -- Webhook handling ----------------------------------------------------

    def handle_webhook_event(self, event: stripe.Event) -> dict:
        """Process a verified Stripe webhook event.

        Returns a dict with processing result for logging.
        """
        event_type = event["type"]
        data = event["data"]["object"]

        handler = {
            "invoice.payment_succeeded": self._handle_payment_succeeded,
            "invoice.payment_failed": self._handle_payment_failed,
            "customer.subscription.deleted": self._handle_subscription_canceled,
            "customer.subscription.updated": self._handle_subscription_updated,
            "checkout.session.completed": self._handle_checkout_completed,
        }.get(event_type)

        if handler is None:
            logger.debug("Ignoring unhandled webhook event: %s", event_type)
            return {"status": "ignored", "event_type": event_type}

        return handler(data)

    def _handle_payment_succeeded(self, invoice: dict) -> dict:
        customer_id = invoice.get("customer")
        tenant = self._store.get_by_stripe_customer_id(customer_id)
        if tenant is None:
            logger.warning("Payment succeeded for unknown customer: %s", customer_id)
            return {"status": "skipped", "reason": "unknown_customer"}

        logger.info(
            "Payment succeeded for tenant %s (amount: %s cents)",
            tenant.id,
            invoice.get("amount_paid"),
        )
        return {"status": "processed", "tenant_id": tenant.id, "event": "payment_succeeded"}

    def _handle_payment_failed(self, invoice: dict) -> dict:
        customer_id = invoice.get("customer")
        tenant = self._store.get_by_stripe_customer_id(customer_id)
        if tenant is None:
            logger.warning("Payment failed for unknown customer: %s", customer_id)
            return {"status": "skipped", "reason": "unknown_customer"}

        logger.warning(
            "Payment failed for tenant %s (attempt: %s)",
            tenant.id,
            invoice.get("attempt_count"),
        )
        # Could downgrade plan or disable tenant here in the future
        return {"status": "processed", "tenant_id": tenant.id, "event": "payment_failed"}

    def _handle_subscription_canceled(self, subscription: dict) -> dict:
        tenant_id = subscription.get("metadata", {}).get("tenant_id")
        if not tenant_id:
            customer_id = subscription.get("customer")
            tenant = self._store.get_by_stripe_customer_id(customer_id)
            tenant_id = tenant.id if tenant else None

        if not tenant_id:
            logger.warning("Subscription canceled for unknown tenant (sub: %s)", subscription.get("id"))
            return {"status": "skipped", "reason": "unknown_tenant"}

        # Downgrade to starter (free tier equivalent) on cancellation
        self._store.update_plan(tenant_id, "starter")
        self._store.update_stripe_subscription_id(tenant_id, None)
        logger.info("Subscription canceled for tenant %s, downgraded to starter", tenant_id)
        return {"status": "processed", "tenant_id": tenant_id, "event": "subscription_canceled"}

    def _handle_subscription_updated(self, subscription: dict) -> dict:
        tenant_id = subscription.get("metadata", {}).get("tenant_id")
        if not tenant_id:
            customer_id = subscription.get("customer")
            tenant = self._store.get_by_stripe_customer_id(customer_id)
            tenant_id = tenant.id if tenant else None

        if not tenant_id:
            logger.debug("Subscription updated for unknown tenant")
            return {"status": "skipped", "reason": "unknown_tenant"}

        # Sync plan from the subscription's price
        items = subscription.get("items", {}).get("data", [])
        if items:
            price_id = items[0].get("price", {}).get("id")
            if price_id:
                plan = _plan_from_price_id(price_id)
                if plan:
                    self._store.update_plan(tenant_id, plan)
                    logger.info("Synced plan for tenant %s to '%s' from subscription update", tenant_id, plan)

        return {"status": "processed", "tenant_id": tenant_id, "event": "subscription_updated"}

    def _handle_checkout_completed(self, session: dict) -> dict:
        tenant_id = session.get("metadata", {}).get("tenant_id")
        subscription_id = session.get("subscription")

        if not tenant_id:
            logger.warning("Checkout completed without tenant_id in metadata")
            return {"status": "skipped", "reason": "no_tenant_id"}

        if subscription_id:
            self._store.update_stripe_subscription_id(tenant_id, subscription_id)
            logger.info("Checkout completed for tenant %s, subscription %s", tenant_id, subscription_id)

        return {"status": "processed", "tenant_id": tenant_id, "event": "checkout_completed"}


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def verify_webhook_signature(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify a Stripe webhook signature and return the parsed event.

    Raises stripe.SignatureVerificationError on failure.
    """
    _init_stripe()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET environment variable is not set")

    stripe = _stripe()
    event = stripe.Webhook.construct_event(
        payload=payload,
        sig_header=sig_header,
        secret=webhook_secret,
    )
    return event
