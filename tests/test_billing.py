"""Tests for api/billing.py -- Stripe billing integration."""

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock the stripe module before importing billing, in case it is not installed
# ---------------------------------------------------------------------------

_mock_stripe = MagicMock()
_mock_stripe.StripeError = Exception  # so except clauses work


@pytest.fixture(autouse=True)
def _patch_stripe(monkeypatch):
    """Ensure `import stripe` resolves to our mock for every test."""
    monkeypatch.setitem(sys.modules, "stripe", _mock_stripe)
    # Reset call counts between tests
    _mock_stripe.reset_mock()
    yield


@pytest.fixture(autouse=True)
def _stripe_env(monkeypatch):
    """Set required Stripe env vars so BillingManager.__init__ succeeds."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake_key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fake")
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter_123")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_456")
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_ent_789")


@pytest.fixture
def tenant_store(tmp_path):
    """Create a real TenantStore backed by a temp SQLite database."""
    from api.auth import TenantStore

    store = TenantStore(str(tmp_path / "tenants.db"))
    yield store
    store.close()


@pytest.fixture
def sample_tenant(tenant_store):
    """Create a starter-plan tenant for billing tests."""
    return tenant_store.create(
        tenant_id="test-city",
        name="Test City",
        granicus_host="test.granicus.com",
        granicus_view_id="1",
        plan="starter",
    )


@pytest.fixture
def billing_manager(tenant_store):
    from api.billing import BillingManager

    return BillingManager(store=tenant_store)


# -------------------------------------------------------------------------
# Initialization
# -------------------------------------------------------------------------


class TestBillingManagerInit:
    def test_init_sets_stripe_api_key(self, billing_manager):
        """BillingManager.__init__ should call _init_stripe which sets stripe.api_key."""
        assert _mock_stripe.api_key == "sk_test_fake_key"

    def test_init_without_stripe_key_raises(self, tenant_store, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY")
        from api.billing import BillingManager

        with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
            BillingManager(store=tenant_store)


# -------------------------------------------------------------------------
# Checkout session
# -------------------------------------------------------------------------


class TestCreateCheckoutSession:
    def test_creates_session_for_existing_tenant(
        self, billing_manager, tenant_store, sample_tenant
    ):
        # Give tenant a stripe customer id so it skips create_customer
        tenant_store.update_stripe_customer_id("test-city", "cus_abc")

        mock_session = MagicMock()
        mock_session.id = "cs_test_session"
        _mock_stripe.checkout.Session.create.return_value = mock_session

        result = billing_manager.create_checkout_session(
            tenant_id="test-city",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        assert result.id == "cs_test_session"
        _mock_stripe.checkout.Session.create.assert_called_once()
        call_kwargs = _mock_stripe.checkout.Session.create.call_args[1]
        assert call_kwargs["customer"] == "cus_abc"
        assert call_kwargs["mode"] == "subscription"
        assert call_kwargs["success_url"] == "https://example.com/success"

    def test_auto_creates_customer_if_missing(
        self, billing_manager, tenant_store, sample_tenant
    ):
        mock_customer = MagicMock()
        mock_customer.id = "cus_new"
        _mock_stripe.Customer.create.return_value = mock_customer

        mock_session = MagicMock()
        mock_session.id = "cs_test_auto"
        _mock_stripe.checkout.Session.create.return_value = mock_session

        result = billing_manager.create_checkout_session(
            tenant_id="test-city",
            success_url="https://example.com/ok",
            cancel_url="https://example.com/no",
        )

        assert result.id == "cs_test_auto"
        _mock_stripe.Customer.create.assert_called_once()

    def test_raises_for_unknown_tenant(self, billing_manager):
        with pytest.raises(ValueError, match="Tenant not found"):
            billing_manager.create_checkout_session(
                tenant_id="nonexistent",
                success_url="https://x.com/ok",
                cancel_url="https://x.com/no",
            )


# -------------------------------------------------------------------------
# Webhook handling
# -------------------------------------------------------------------------


class TestHandleWebhook:
    def _make_event(self, event_type: str, data_object: dict) -> dict:
        return {"type": event_type, "data": {"object": data_object}}

    def test_payment_succeeded(
        self, billing_manager, tenant_store, sample_tenant
    ):
        tenant_store.update_stripe_customer_id("test-city", "cus_pay")

        event = self._make_event(
            "invoice.payment_succeeded",
            {"customer": "cus_pay", "amount_paid": 29900},
        )
        result = billing_manager.handle_webhook_event(event)

        assert result["status"] == "processed"
        assert result["tenant_id"] == "test-city"
        assert result["event"] == "payment_succeeded"

    def test_payment_succeeded_unknown_customer(self, billing_manager):
        event = self._make_event(
            "invoice.payment_succeeded",
            {"customer": "cus_unknown", "amount_paid": 0},
        )
        result = billing_manager.handle_webhook_event(event)
        assert result["status"] == "skipped"
        assert result["reason"] == "unknown_customer"

    def test_payment_failed(
        self, billing_manager, tenant_store, sample_tenant
    ):
        tenant_store.update_stripe_customer_id("test-city", "cus_fail")

        event = self._make_event(
            "invoice.payment_failed",
            {"customer": "cus_fail", "attempt_count": 2},
        )
        result = billing_manager.handle_webhook_event(event)

        assert result["status"] == "processed"
        assert result["event"] == "payment_failed"
        assert result["tenant_id"] == "test-city"

    def test_payment_failed_unknown_customer(self, billing_manager):
        event = self._make_event(
            "invoice.payment_failed",
            {"customer": "cus_ghost", "attempt_count": 1},
        )
        result = billing_manager.handle_webhook_event(event)
        assert result["status"] == "skipped"

    def test_subscription_canceled_downgrades_to_starter(
        self, billing_manager, tenant_store, sample_tenant
    ):
        # Upgrade tenant to pro first
        tenant_store.update_plan("test-city", "pro")
        tenant_store.update_stripe_customer_id("test-city", "cus_cancel")
        tenant_store.update_stripe_subscription_id("test-city", "sub_cancel")

        event = self._make_event(
            "customer.subscription.deleted",
            {
                "id": "sub_cancel",
                "customer": "cus_cancel",
                "metadata": {"tenant_id": "test-city"},
            },
        )
        result = billing_manager.handle_webhook_event(event)

        assert result["status"] == "processed"
        assert result["event"] == "subscription_canceled"

        # Verify tenant was downgraded
        tenant = tenant_store.get_by_id("test-city")
        assert tenant.plan == "starter"
        assert tenant.stripe_subscription_id is None

    def test_subscription_canceled_resolves_via_customer_id(
        self, billing_manager, tenant_store, sample_tenant
    ):
        tenant_store.update_plan("test-city", "pro")
        tenant_store.update_stripe_customer_id("test-city", "cus_resolve")
        tenant_store.update_stripe_subscription_id("test-city", "sub_resolve")

        # No tenant_id in metadata -- should resolve via customer lookup
        event = self._make_event(
            "customer.subscription.deleted",
            {
                "id": "sub_resolve",
                "customer": "cus_resolve",
                "metadata": {},
            },
        )
        result = billing_manager.handle_webhook_event(event)
        assert result["status"] == "processed"
        assert result["tenant_id"] == "test-city"

    def test_subscription_updated_syncs_plan(
        self, billing_manager, tenant_store, sample_tenant
    ):
        tenant_store.update_stripe_customer_id("test-city", "cus_upd")

        event = self._make_event(
            "customer.subscription.updated",
            {
                "metadata": {"tenant_id": "test-city"},
                "items": {
                    "data": [{"price": {"id": "price_pro_456"}}]
                },
            },
        )
        result = billing_manager.handle_webhook_event(event)

        assert result["status"] == "processed"
        tenant = tenant_store.get_by_id("test-city")
        assert tenant.plan == "pro"

    def test_checkout_completed_sets_subscription_id(
        self, billing_manager, tenant_store, sample_tenant
    ):
        event = self._make_event(
            "checkout.session.completed",
            {
                "metadata": {"tenant_id": "test-city"},
                "subscription": "sub_new_123",
            },
        )
        result = billing_manager.handle_webhook_event(event)

        assert result["status"] == "processed"
        tenant = tenant_store.get_by_id("test-city")
        assert tenant.stripe_subscription_id == "sub_new_123"

    def test_unhandled_event_type_ignored(self, billing_manager):
        event = self._make_event("some.unknown.event", {})
        result = billing_manager.handle_webhook_event(event)
        assert result["status"] == "ignored"


# -------------------------------------------------------------------------
# Usage / get_usage
# -------------------------------------------------------------------------


class TestGetUsage:
    def test_usage_without_subscription(
        self, billing_manager, sample_tenant
    ):
        result = billing_manager.get_usage("test-city")

        assert result["tenant_id"] == "test-city"
        assert result["plan"] == "starter"
        assert result["monthly_limit"] == 100
        assert result["subscription_status"] == "none"

    def test_usage_with_subscription(
        self, billing_manager, tenant_store, sample_tenant
    ):
        tenant_store.update_stripe_customer_id("test-city", "cus_usage")
        tenant_store.update_stripe_subscription_id("test-city", "sub_usage")

        _mock_stripe.Subscription.retrieve.return_value = {
            "status": "active",
            "current_period_start": 1700000000,
            "current_period_end": 1702600000,
        }
        _mock_stripe.Invoice.upcoming.return_value = {
            "amount_due": 29900,
            "lines": {
                "data": [
                    {"quantity": 42},
                    {"quantity": 8},
                ]
            },
        }

        result = billing_manager.get_usage("test-city")

        assert result["subscription_status"] == "active"
        assert result["queries_used"] == 50
        assert result["amount_due_cents"] == 29900

    def test_usage_unknown_tenant_raises(self, billing_manager):
        with pytest.raises(ValueError, match="Tenant not found"):
            billing_manager.get_usage("no-such-tenant")


# -------------------------------------------------------------------------
# Plan limits
# -------------------------------------------------------------------------


class TestPlanLimits:
    def test_starter_limit_is_100(self):
        from api.auth import PLAN_LIMITS

        assert PLAN_LIMITS["starter"] == 100

    def test_pro_limit_is_1000(self):
        from api.auth import PLAN_LIMITS

        assert PLAN_LIMITS["pro"] == 1000

    def test_enterprise_unlimited(self):
        from api.auth import PLAN_LIMITS

        assert PLAN_LIMITS["enterprise"] is None

    def test_monthly_limit_property(self, tenant_store):
        tenant = tenant_store.create(
            tenant_id="limit-test",
            name="Limit Test",
            granicus_host="test.granicus.com",
            plan="pro",
        )
        assert tenant.monthly_limit == 1000

    def test_rate_limiter_enforces_starter_limit(self):
        from api.auth import RateLimiter, Tenant

        limiter = RateLimiter()
        tenant = Tenant(
            id="rate-test",
            name="Rate Test",
            granicus_host="test.granicus.com",
            granicus_view_id="1",
            api_key="mra_fake",
            plan="starter",
            created_at="2026-01-01T00:00:00Z",
        )

        # Use up the full quota
        for _ in range(100):
            allowed, info = limiter.check(tenant)
            assert allowed is True

        # 101st request should be denied
        allowed, info = limiter.check(tenant)
        assert allowed is False
        assert info["remaining"] == 0

    def test_rate_limiter_enterprise_unlimited(self):
        from api.auth import RateLimiter, Tenant

        limiter = RateLimiter()
        tenant = Tenant(
            id="ent-test",
            name="Enterprise",
            granicus_host="test.granicus.com",
            granicus_view_id="1",
            api_key="mra_fake",
            plan="enterprise",
            created_at="2026-01-01T00:00:00Z",
        )

        for _ in range(200):
            allowed, info = limiter.check(tenant)
            assert allowed is True
            assert info["limit"] == "unlimited"


# -------------------------------------------------------------------------
# Verify webhook signature helper
# -------------------------------------------------------------------------


class TestVerifyWebhookSignature:
    def test_calls_stripe_construct_event(self, monkeypatch):
        from api.billing import verify_webhook_signature

        mock_event = {"type": "test", "data": {"object": {}}}
        _mock_stripe.Webhook.construct_event.return_value = mock_event

        result = verify_webhook_signature(b"raw_payload", "sig_header_value")

        _mock_stripe.Webhook.construct_event.assert_called_once_with(
            payload=b"raw_payload",
            sig_header="sig_header_value",
            secret="whsec_test_fake",
        )
        assert result == mock_event

    def test_raises_without_webhook_secret(self, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")
        from api.billing import verify_webhook_signature

        with pytest.raises(RuntimeError, match="STRIPE_WEBHOOK_SECRET"):
            verify_webhook_signature(b"payload", "sig")
