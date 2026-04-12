"""End-to-end API flow tests using FastAPI TestClient.

Tests the full lifecycle: create tenant -> authenticate -> ask -> analytics,
plus auth rejection, rate limiting, health, webhooks, and CORS.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure slack_sdk is available as a mock so api.server can import cleanly
_slack_mock = MagicMock()
for mod in ["slack_sdk", "slack_sdk.errors", "slack_sdk.webhook"]:
    if mod not in sys.modules:
        sys.modules[mod] = _slack_mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(tmp_path):
    """Create a fresh app instance with auth/analytics/webhooks initialized to tmp_path."""
    output_dir = str(tmp_path)

    # Set env vars persistently (cleaned up in fixture teardown)
    os.environ["MEETINGS_OUTPUT_DIR"] = output_dir
    os.environ["ADMIN_API_KEY"] = "admin_secret_key"

    # Reset module-level singletons
    import api.server as server_mod
    import api.auth as auth_mod
    import api.analytics as analytics_mod
    import api.webhooks as webhooks_mod

    # Initialize auth, analytics, webhooks against tmp_path
    auth_mod._tenant_store = None
    auth_mod._rate_limiter = None
    analytics_mod._analytics_store = None
    webhooks_mod._webhook_manager = None

    from api.auth import init_auth, RateLimiter
    from api.analytics import init_analytics
    from api.webhooks import init_webhooks

    store = init_auth(output_dir)
    auth_mod._rate_limiter = RateLimiter()
    init_analytics(output_dir)
    init_webhooks(output_dir)

    # Reset lazy-loaded server singletons
    server_mod._collection = MagicMock()
    server_mod._collection.count.return_value = 100
    server_mod._clip_metadata = {}
    server_mod._openai_client = MagicMock()

    return server_mod.app, store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_and_store(tmp_path):
    """Provide a TestClient and TenantStore for E2E tests."""
    app, store = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    yield client, store


@pytest.fixture
def tenant_client(app_and_store):
    """Create a tenant and return (client, api_key, tenant_id)."""
    client, store = app_and_store
    # Create tenant via admin API
    resp = client.post(
        "/api/v1/admin/tenants",
        json={
            "id": "e2e-city",
            "name": "E2E Test City",
            "granicus_host": "e2e.granicus.com",
            "granicus_view_id": "14",
            "plan": "pro",
        },
        headers={"X-API-Key": "admin_secret_key"},
    )
    assert resp.status_code == 200
    api_key = resp.json()["api_key"]
    return client, api_key, "e2e-city"


# ============================================================
# 1. Full API flow: create tenant -> ask -> check analytics
# ============================================================

class TestFullFlow:

    def test_create_tenant_and_ask_question(self, tenant_client):
        client, api_key, tenant_id = tenant_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = {
                "answer": "Zoning was discussed in the January meeting.",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 3,
            }

            resp = client.post(
                "/api/v1/ask",
                json={"question": "What about zoning?"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "answer" in data
            assert data["tenant"] == tenant_id

    def test_ask_then_check_analytics(self, tenant_client):
        client, api_key, tenant_id = tenant_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = {
                "answer": "Answer",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }
            client.post(
                "/api/v1/ask",
                json={"question": "Budget?"},
                headers={"X-API-Key": api_key},
            )

        # Check analytics
        resp = client.get(
            "/api/v1/analytics/usage",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queries"] >= 1

    def test_admin_list_tenants(self, tenant_client):
        client, api_key, tenant_id = tenant_client
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": "admin_secret_key"},
        )
        assert resp.status_code == 200
        tenants = resp.json()["tenants"]
        ids = [t["id"] for t in tenants]
        assert tenant_id in ids

    def test_admin_update_plan(self, tenant_client):
        client, api_key, tenant_id = tenant_client
        resp = client.patch(
            f"/api/v1/admin/tenants/{tenant_id}/plan",
            json={"plan": "enterprise"},
            headers={"X-API-Key": "admin_secret_key"},
        )
        assert resp.status_code == 200
        assert resp.json()["plan"] == "enterprise"

    def test_admin_rotate_key(self, tenant_client):
        client, api_key, tenant_id = tenant_client
        resp = client.post(
            f"/api/v1/admin/tenants/{tenant_id}/rotate-key",
            headers={"X-API-Key": "admin_secret_key"},
        )
        assert resp.status_code == 200
        new_key = resp.json()["api_key"]
        assert new_key != api_key
        assert new_key.startswith("mra_")

    def test_admin_delete_tenant(self, tenant_client):
        client, api_key, tenant_id = tenant_client
        resp = client.delete(
            f"/api/v1/admin/tenants/{tenant_id}",
            headers={"X-API-Key": "admin_secret_key"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == tenant_id


# ============================================================
# 2. Auth rejection
# ============================================================

class TestAuthRejection:

    def test_no_api_key_returns_401(self, app_and_store):
        client, store = app_and_store
        # Create a tenant so dev mode doesn't kick in
        store.create("blocker", "Blocker", "b.com")
        resp = client.post("/api/v1/ask", json={"question": "test"})
        assert resp.status_code == 401

    def test_bad_api_key_returns_401(self, app_and_store):
        client, store = app_and_store
        store.create("blocker", "Blocker", "b.com")
        resp = client.post(
            "/api/v1/ask",
            json={"question": "test"},
            headers={"X-API-Key": "mra_totally_bogus"},
        )
        assert resp.status_code == 401

    def test_admin_endpoint_wrong_key_returns_403(self, app_and_store):
        client, _ = app_and_store
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": "wrong_admin_key"},
        )
        assert resp.status_code == 403

    def test_admin_endpoint_no_key_returns_403(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/api/v1/admin/tenants")
        assert resp.status_code == 403

    def test_admin_endpoint_with_tenant_key_returns_403(self, tenant_client):
        client, api_key, _ = tenant_client
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403


# ============================================================
# 3. Rate limiting
# ============================================================

class TestRateLimiting:

    def test_rate_limit_429(self, app_and_store):
        """Exhaust a starter tenant's rate limit and verify 429."""
        client, store = app_and_store
        tenant = store.create("rate-test", "Rate Test", "rate.com", plan="starter")

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = {
                "answer": "A",
                "sources": [],
                "filters_applied": {},
                "chunks_retrieved": 0,
            }

            # Exhaust 100 queries
            for i in range(100):
                resp = client.post(
                    "/api/v1/ask",
                    json={"question": f"q{i}"},
                    headers={"X-API-Key": tenant.api_key},
                )
                assert resp.status_code == 200, f"Request {i} failed with {resp.status_code}"

            # 101st should be rate limited
            resp = client.post(
                "/api/v1/ask",
                json={"question": "one too many"},
                headers={"X-API-Key": tenant.api_key},
            )
            assert resp.status_code == 429
            assert "Rate limit" in resp.json()["detail"]


# ============================================================
# 4. Health endpoint (no auth required)
# ============================================================

class TestHealthEndpoint:

    def test_health_no_auth_required(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_api_health_no_auth_required(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_v1_health_requires_auth(self, app_and_store):
        """The v1 health endpoint requires tenant auth."""
        client, store = app_and_store
        store.create("blocker", "B", "b.com")
        resp = client.get("/api/v1/health")
        assert resp.status_code == 401


# ============================================================
# 5. Webhook registration + delivery
# ============================================================

class TestWebhookE2E:

    def test_register_and_list_webhooks(self, tenant_client):
        client, api_key, tenant_id = tenant_client

        # Register
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["meeting.processed"],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        webhook_data = resp.json()
        assert "id" in webhook_data
        assert "secret" in webhook_data
        assert webhook_data["url"] == "https://example.com/hook"

        # List
        resp = client.get(
            "/api/v1/webhooks",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        webhooks = resp.json()["webhooks"]
        assert len(webhooks) == 1

    def test_delete_webhook(self, tenant_client):
        client, api_key, tenant_id = tenant_client

        # Register
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["meeting.processed"],
            },
            headers={"X-API-Key": api_key},
        )
        webhook_id = resp.json()["id"]

        # Delete
        resp = client.delete(
            f"/api/v1/webhooks/{webhook_id}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == webhook_id

    def test_register_webhook_invalid_event_returns_422(self, tenant_client):
        client, api_key, _ = tenant_client
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["bogus.event"],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_register_webhook_no_auth_returns_401(self, app_and_store):
        client, store = app_and_store
        store.create("blocker", "B", "b.com")
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["meeting.processed"],
            },
        )
        assert resp.status_code == 401


# ============================================================
# 6. CORS headers
# ============================================================

class TestCORSHeaders:

    def test_cors_headers_present_on_response(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        # CORS middleware should add access-control-allow-origin
        assert "access-control-allow-origin" in resp.headers

    def test_preflight_options_request(self, app_and_store):
        client, _ = app_and_store
        resp = client.options(
            "/api/v1/ask",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-headers" in resp.headers


# ============================================================
# 7. Security headers
# ============================================================

class TestSecurityHeaders:

    def test_security_headers_present(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "Strict-Transport-Security" in resp.headers
        assert "X-XSS-Protection" in resp.headers

    def test_request_id_header(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/health")
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_echoed(self, app_and_store):
        client, _ = app_and_store
        resp = client.get("/health", headers={"X-Request-ID": "my-custom-id"})
        assert resp.headers.get("X-Request-ID") == "my-custom-id"


# ============================================================
# 8. Admin plans endpoint
# ============================================================

class TestAdminPlans:

    def test_admin_list_plans(self, app_and_store):
        client, _ = app_and_store
        resp = client.get(
            "/api/v1/admin/plans",
            headers={"X-API-Key": "admin_secret_key"},
        )
        assert resp.status_code == 200
        plans = resp.json()["plans"]
        assert "starter" in plans
        assert "pro" in plans
        assert "enterprise" in plans


# ============================================================
# 9. Analytics routes E2E
# ============================================================

class TestAnalyticsE2E:

    def test_analytics_usage_endpoint(self, tenant_client):
        client, api_key, _ = tenant_client
        resp = client.get(
            "/api/v1/analytics/usage?period=30d",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "total_queries" in resp.json()

    def test_analytics_queries_endpoint(self, tenant_client):
        client, api_key, _ = tenant_client
        resp = client.get(
            "/api/v1/analytics/queries?period=7d",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_analytics_export_csv(self, tenant_client):
        client, api_key, _ = tenant_client
        resp = client.get(
            "/api/v1/analytics/export/queries?period=30d",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_analytics_recent(self, tenant_client):
        client, api_key, _ = tenant_client
        resp = client.get(
            "/api/v1/analytics/recent",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "queries" in resp.json()
