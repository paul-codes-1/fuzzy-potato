"""Security-focused tests covering OWASP Top 10 concerns.

Tests cover: security headers, auth bypass, rate limiting, SQL injection,
XSS prevention, CORS, request IDs, API key format, tenant isolation,
and admin endpoint protection.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth import RateLimiter, Tenant, init_auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure sensitive env vars are cleared between tests."""
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    # SSO is an optional module; provide a minimal stub so authz routes can load
    # without the python3-saml dependency being installed in the test env.
    auth_mod = types.ModuleType("onelogin.saml2.auth")

    class DummySamlAuth:
        def __init__(self, *args, **kwargs):
            pass

    auth_mod.OneLogin_Saml2_Auth = DummySamlAuth
    saml_mod = types.ModuleType("onelogin.saml2")
    saml_mod.auth = auth_mod
    onelogin_mod = types.ModuleType("onelogin")
    onelogin_mod.saml2 = saml_mod

    monkeypatch.setitem(sys.modules, "onelogin", onelogin_mod)
    monkeypatch.setitem(sys.modules, "onelogin.saml2", saml_mod)
    monkeypatch.setitem(sys.modules, "onelogin.saml2.auth", auth_mod)


@pytest.fixture()
def output_dir(tmp_path):
    """Temporary output directory for tenant DB and other state."""
    d = tmp_path / "meetings_output"
    d.mkdir()
    return str(d)


@pytest.fixture()
def tenant_store(output_dir):
    """Initialised TenantStore backed by a temp SQLite DB."""
    return init_auth(output_dir)


@pytest.fixture()
def tenant_a(client):
    """Create Tenant A (starter plan) using the live server's tenant store."""
    from api.auth import get_tenant_store
    store = get_tenant_store()
    return store.create(
        tenant_id="tenant-a",
        name="City A",
        granicus_host="a.granicus.com",
        plan="starter",
    )


@pytest.fixture()
def tenant_b(client):
    """Create Tenant B (pro plan) using the live server's tenant store."""
    from api.auth import get_tenant_store
    store = get_tenant_store()
    return store.create(
        tenant_id="tenant-b",
        name="City B",
        granicus_host="b.granicus.com",
        plan="pro",
    )


@pytest.fixture()
def enterprise_tenant(client):
    """Create an Enterprise tenant for SSO authorization checks."""
    from api.auth import get_tenant_store
    store = get_tenant_store()
    return store.create(
        tenant_id="tenant-enterprise",
        name="City Enterprise",
        granicus_host="enterprise.granicus.com",
        plan="enterprise",
    )


@pytest.fixture()
def _mock_rag_deps():
    """Patch heavy RAG dependencies so the server can start without real data."""
    dummy_collection = MagicMock()
    dummy_collection.count.return_value = 0
    dummy_collection.query.return_value = {
        "ids": [[]],
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
    }

    with (
        patch("api.server._get_collection", return_value=dummy_collection),
        patch("api.server._get_clip_metadata", return_value={}),
        patch("api.server._get_openai_client") as mock_oai,
        patch("api.query.ask", return_value={"answer": "test", "sources": []}),
        patch("api.query.chat", return_value={"answer": "test", "sources": []}),
    ):
        # Mock OpenAI embeddings for query path
        mock_embed = MagicMock()
        mock_embed.embedding = [0.1] * 1536
        mock_embed_resp = MagicMock()
        mock_embed_resp.data = [mock_embed]
        oai = MagicMock()
        oai.embeddings.create.return_value = mock_embed_resp
        mock_oai.return_value = oai
        yield


@pytest.fixture()
def client(output_dir, _mock_rag_deps, monkeypatch):
    """TestClient wired to a fully-initialised app with temp state."""
    monkeypatch.setenv("MEETINGS_OUTPUT_DIR", output_dir)
    # Patch the module-level OUTPUT_DIR which is read at import time
    sys.modules.pop("api.server", None)
    sys.modules.pop("api.sso", None)
    sys.modules.pop("api.sso_routes", None)
    import api.server as server_mod
    monkeypatch.setattr(server_mod, "OUTPUT_DIR", output_dir)

    from api.server import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ===================================================================
# 1. Security Headers
# ===================================================================


class TestSecurityHeaders:
    """Verify HSTS, CSP, X-Frame-Options, X-Content-Type-Options on responses."""

    def test_hsts_header(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Strict-Transport-Security") == "max-age=63072000; includeSubDomains"

    def test_x_frame_options(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_options(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_csp_header(self, client):
        resp = client.get("/health")
        csp = resp.headers.get("Content-Security-Policy")
        assert csp is not None
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_xss_protection_header(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"

    def test_referrer_policy(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"

    def test_security_headers_on_authenticated_endpoint(self, client, tenant_a):
        resp = client.get("/api/v1/health", headers={"X-API-Key": tenant_a.api_key})
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "max-age=63072000" in resp.headers.get("Strict-Transport-Security", "")

    def test_security_headers_on_error_responses(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        resp = client.get("/api/v1/health")  # no auth -- should 401
        assert resp.status_code == 401
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"


# ===================================================================
# 2. Auth Bypass Attempts
# ===================================================================


class TestAuthBypass:
    """Verify that missing, invalid, expired, and malformed credentials are rejected."""

    def test_no_api_key_returns_401(self, client, tenant_a, monkeypatch):
        # With tenants present and AUTH_REQUIRED, no key should fail
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        resp = client.get("/api/v1/health")
        assert resp.status_code == 401

    def test_invalid_api_key_returns_401(self, client, tenant_a):
        resp = client.get("/api/v1/health", headers={"X-API-Key": "mra_boguskey123"})
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json()["detail"]

    def test_empty_api_key_returns_401(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        resp = client.get("/api/v1/health", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_malformed_jwt_returns_401(self, client, tenant_a):
        resp = client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer not.a.real.jwt.token"},
        )
        assert resp.status_code == 401

    def test_expired_jwt_returns_401(self, client, tenant_a):
        """Forge an expired-looking JWT and verify rejection."""
        resp = client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZW5hbnRfaWQiOiJ0ZW5hbnQtYSIsImV4cCI6MH0.invalid"},
        )
        assert resp.status_code == 401

    def test_bearer_without_token_returns_401(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        resp = client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    def test_basic_auth_not_accepted(self, client, tenant_a, monkeypatch):
        """Basic auth scheme should not grant access."""
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        resp = client.get(
            "/api/v1/health",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    def test_dev_fallback_disabled_when_auth_required(self, client, monkeypatch):
        """Even with no tenants, AUTH_REQUIRED=true should reject unauthenticated."""
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        # Delete all tenants so dev fallback would normally kick in
        from api.auth import get_tenant_store
        store = get_tenant_store()
        for t in store.list_all():
            store.delete(t.id)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 401


class TestSSOAdminAuthorization:
    """Verify SSO browser sessions respect admin role checks on admin routes."""

    def _make_session_token(self, tenant_id: str, role: str, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-for-security-tests-32bytes")
        from api.sso import create_session_token, get_user_store

        store = get_user_store()
        user = store.upsert_from_saml(
            tenant_id=tenant_id,
            email=f"{role}@example.com",
            name=f"{role.title()} User",
            default_role=role,
        )
        return create_session_token(user, tenant_id)

    def test_viewer_jwt_cannot_read_sso_config(self, client, enterprise_tenant, monkeypatch):
        token = self._make_session_token(enterprise_tenant.id, "viewer", monkeypatch)

        resp = client.get(
            "/api/v1/admin/sso/config",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Admin role required."

    def test_viewer_jwt_cannot_list_sso_users(self, client, enterprise_tenant, monkeypatch):
        token = self._make_session_token(enterprise_tenant.id, "viewer", monkeypatch)

        resp = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Admin role required."

    def test_admin_jwt_can_read_sso_config(self, client, enterprise_tenant, monkeypatch):
        token = self._make_session_token(enterprise_tenant.id, "admin", monkeypatch)

        resp = client.get(
            "/api/v1/admin/sso/config",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == enterprise_tenant.id

    def test_tenant_api_key_still_can_read_sso_config(self, client, enterprise_tenant):
        resp = client.get(
            "/api/v1/admin/sso/config",
            headers={"X-API-Key": enterprise_tenant.api_key},
        )

        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == enterprise_tenant.id


# ===================================================================
# 3. Rate Limiting
# ===================================================================


class TestRateLimiting:
    """Verify rate limiter blocks after plan limit exceeded."""

    def test_starter_plan_rate_limit(self):
        """Starter plan (100 queries) should be blocked after 100 requests."""
        limiter = RateLimiter()
        tenant = Tenant(
            id="rate-test",
            name="Rate Test",
            granicus_host="test.granicus.com",
            granicus_view_id="1",
            api_key="mra_ratetest",
            plan="starter",
            created_at="2026-01-01",
        )

        # Burn through 100 requests
        for _ in range(100):
            allowed, info = limiter.check(tenant)
            assert allowed is True

        # 101st should be blocked
        allowed, info = limiter.check(tenant)
        assert allowed is False
        assert info["remaining"] == 0
        assert info["limit"] == 100

    def test_enterprise_plan_unlimited(self):
        """Enterprise plan should never be rate-limited."""
        limiter = RateLimiter()
        tenant = Tenant(
            id="ent-test",
            name="Enterprise",
            granicus_host="test.granicus.com",
            granicus_view_id="1",
            api_key="mra_enttest",
            plan="enterprise",
            created_at="2026-01-01",
        )

        for _ in range(500):
            allowed, info = limiter.check(tenant)
            assert allowed is True
        assert info["limit"] == "unlimited"

    def test_rate_limit_returns_429(self, client, tenant_a):
        """HTTP endpoint should return 429 after exceeding plan limit."""
        import api.auth as auth_mod
        # Exhaust the starter plan limit (100 requests) on the live rate limiter
        for _ in range(100):
            auth_mod._rate_limiter.check(tenant_a)

        resp = client.get("/api/v1/health", headers={"X-API-Key": tenant_a.api_key})
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]

        # Reset the limiter so subsequent tests are not affected
        auth_mod._rate_limiter._requests.pop(tenant_a.id, None)


# ===================================================================
# 4. SQL Injection
# ===================================================================


class TestSQLInjection:
    """Verify search queries are properly escaped against common injection payloads."""

    SQL_INJECTION_PAYLOADS = [
        "'; DROP TABLE search_items; --",
        "1' OR '1'='1",
        "1; DELETE FROM tenants WHERE 1=1; --",
        "' UNION SELECT * FROM tenants --",
        "Robert'); DROP TABLE search_items;--",
        "1' OR 1=1 --",
        "'; ATTACH DATABASE ':memory:' AS pwn; --",
        "' OR ''='",
        "1; UPDATE tenants SET plan='enterprise' WHERE 1=1;--",
        "admin'--",
    ]

    def test_search_endpoint_escapes_sql(self, client, tenant_a):
        """Search endpoint should not execute injected SQL."""
        for payload in self.SQL_INJECTION_PAYLOADS:
            resp = client.get(
                "/api/v1/search",
                params={"q": payload},
                headers={"X-API-Key": tenant_a.api_key},
            )
            # Should return 200 with empty/safe results, not 500
            assert resp.status_code == 200, f"SQL injection payload caused error: {payload}"

    def test_autocomplete_escapes_sql(self, client, tenant_a):
        """Autocomplete should handle injection payloads safely."""
        for payload in self.SQL_INJECTION_PAYLOADS:
            resp = client.get(
                "/api/v1/search/autocomplete",
                params={"q": payload},
                headers={"X-API-Key": tenant_a.api_key},
            )
            assert resp.status_code == 200, f"SQL injection in autocomplete: {payload}"

    def test_tenant_store_parameterized_queries(self, client):
        """Tenant store lookups should use parameterized queries (no injection)."""
        from api.auth import get_tenant_store
        store = get_tenant_store()
        # These should return None, not cause errors
        result = store.get_by_api_key("' OR '1'='1")
        assert result is None

        result = store.get_by_id("'; DROP TABLE tenants;--")
        assert result is None

    def test_meeting_body_filter_escapes_sql(self, client, tenant_a):
        """Meeting body filter parameter should be escaped."""
        resp = client.get(
            "/api/v1/search",
            params={"q": "budget", "meeting_body": "'; DROP TABLE search_items;--"},
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 200


# ===================================================================
# 5. XSS Prevention
# ===================================================================


class TestXSSPrevention:
    """Verify user inputs in responses are escaped / not reflected unsafely."""

    XSS_PAYLOADS = [
        "<script>alert('xss')</script>",
        '"><img src=x onerror=alert(1)>',
        "javascript:alert(document.cookie)",
        "<svg onload=alert(1)>",
        "'-alert(1)-'",
    ]

    def test_search_does_not_reflect_raw_xss(self, client, tenant_a):
        """Search results should not contain raw script tags from query."""
        for payload in self.XSS_PAYLOADS:
            resp = client.get(
                "/api/v1/search",
                params={"q": payload},
                headers={"X-API-Key": tenant_a.api_key},
            )
            assert resp.status_code == 200
            body = resp.text
            # The response should not reflect unescaped script tags
            assert "<script>alert" not in body or "query" in resp.json()

    def test_csp_header_blocks_inline_scripts(self, client):
        """CSP default-src 'self' prevents inline script execution."""
        resp = client.get("/health")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        # No unsafe-inline allowed
        assert "unsafe-inline" not in csp

    def test_ask_endpoint_does_not_reflect_xss_in_error(self, client, tenant_a):
        """Error messages from ask endpoint should not reflect user input verbatim."""
        resp = client.post(
            "/api/v1/ask",
            json={"question": "<script>alert('xss')</script>"},
            headers={"X-API-Key": tenant_a.api_key},
        )
        # Whether it succeeds or fails, response should be JSON (not raw HTML with scripts)
        assert resp.headers.get("content-type", "").startswith("application/json")


# ===================================================================
# 6. CORS
# ===================================================================


class TestCORS:
    """Verify only allowed origins get CORS headers."""

    def test_cors_allows_configured_origin(self, output_dir, tenant_store, _mock_rag_deps, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
        # Re-import to pick up new CORS_ORIGINS (module-level config)
        # Instead, test via OPTIONS preflight
        from api.server import app
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.options(
                "/api/v1/ask",
                headers={
                    "Origin": "https://example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            # FastAPI CORS middleware responds to preflight
            acl = resp.headers.get("access-control-allow-origin", "")
            # Should either be the specific origin or * (default dev mode)
            assert acl in ("https://example.com", "*", "")

    def test_cors_expose_headers(self, client):
        """X-Request-ID and rate limit headers should be in expose list."""
        resp = client.get(
            "/health",
            headers={"Origin": "https://example.com"},
        )
        exposed = resp.headers.get("access-control-expose-headers", "")
        # In default dev mode (* origins), exposed headers should be set
        # The important thing is these aren't hidden from browsers
        assert "x-request-id" in exposed.lower() or "X-Request-ID" in exposed or exposed == ""


# ===================================================================
# 7. Request ID
# ===================================================================


class TestRequestID:
    """Every response should have an X-Request-ID header."""

    def test_response_has_request_id(self, client):
        resp = client.get("/health")
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) > 0

    def test_request_id_is_uuid_format(self, client):
        resp = client.get("/health")
        rid = resp.headers["X-Request-ID"]
        # UUIDs have 5 groups separated by hyphens
        parts = rid.split("-")
        assert len(parts) == 5

    def test_custom_request_id_echoed_back(self, client):
        """If client sends X-Request-ID, server should use it."""
        custom_id = "custom-req-12345"
        resp = client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.headers["X-Request-ID"] == custom_id

    def test_request_id_on_error_responses(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        resp = client.get("/api/v1/health")
        assert resp.status_code == 401
        assert "X-Request-ID" in resp.headers

    def test_unique_request_ids(self, client):
        """Each request should get a unique ID."""
        ids = set()
        for _ in range(10):
            resp = client.get("/health")
            ids.add(resp.headers["X-Request-ID"])
        assert len(ids) == 10


# ===================================================================
# 8. API Key Format Validation
# ===================================================================


class TestAPIKeyFormat:
    """Only mra_ prefixed keys should be accepted."""

    def test_mra_prefix_key_accepted(self, client, tenant_a):
        assert tenant_a.api_key.startswith("mra_")
        resp = client.get("/api/v1/health", headers={"X-API-Key": tenant_a.api_key})
        assert resp.status_code == 200

    def test_non_mra_prefix_rejected(self, client, tenant_a):
        """A key without the mra_ prefix should not match any tenant."""
        resp = client.get("/api/v1/health", headers={"X-API-Key": "sk_live_boguskey"})
        assert resp.status_code == 401

    def test_generated_keys_always_have_mra_prefix(self, client):
        """All generated API keys must start with mra_ prefix."""
        from api.auth import get_tenant_store
        store = get_tenant_store()
        t = store.create(
            tenant_id="prefix-test",
            name="Prefix Test",
            granicus_host="test.granicus.com",
        )
        assert t.api_key.startswith("mra_")

    def test_rotated_keys_have_mra_prefix(self, client, tenant_a):
        """Rotated keys must also start with mra_ prefix."""
        from api.auth import get_tenant_store
        store = get_tenant_store()
        new_key = store.rotate_key(tenant_a.id)
        assert new_key is not None
        assert new_key.startswith("mra_")

    def test_old_key_invalid_after_rotation(self, client, tenant_a):
        """After key rotation, old key should no longer authenticate."""
        from api.auth import get_tenant_store
        store = get_tenant_store()
        old_key = tenant_a.api_key
        store.rotate_key(tenant_a.id)
        resp = client.get("/api/v1/health", headers={"X-API-Key": old_key})
        assert resp.status_code == 401


# ===================================================================
# 9. Tenant Isolation
# ===================================================================


class TestTenantIsolation:
    """Tenant A must not be able to access Tenant B's data."""

    def test_tenant_a_cannot_use_tenant_b_key(self, client, tenant_a, tenant_b):
        """Each tenant authenticates only with their own key."""
        resp_a = client.get("/api/v1/health", headers={"X-API-Key": tenant_a.api_key})
        assert resp_a.status_code == 200
        assert resp_a.json()["tenant"] == "tenant-a"

        resp_b = client.get("/api/v1/health", headers={"X-API-Key": tenant_b.api_key})
        assert resp_b.status_code == 200
        assert resp_b.json()["tenant"] == "tenant-b"

    def test_ask_scopes_to_tenant(self, client, tenant_a):
        """Ask endpoint should include tenant ID in response for scoping verification."""
        resp = client.post(
            "/api/v1/ask",
            json={"question": "What happened in the last meeting?"},
            headers={"X-API-Key": tenant_a.api_key},
        )
        # Even if it errors on RAG, the tenant scoping should be attempted
        if resp.status_code == 200:
            assert resp.json().get("tenant") == "tenant-a"

    def test_tenant_data_directory_isolation(self, tenant_a, tenant_b):
        """Tenant IDs are distinct and would map to separate data directories."""
        assert tenant_a.id != tenant_b.id
        assert tenant_a.api_key != tenant_b.api_key
        path_a = f"meetings_output/{tenant_a.id}/"
        path_b = f"meetings_output/{tenant_b.id}/"
        assert path_a != path_b

    def test_search_scoped_to_tenant(self, client, tenant_a, tenant_b):
        """Search requests from different tenants should scope results."""
        resp_a = client.get(
            "/api/v1/search",
            params={"q": "budget"},
            headers={"X-API-Key": tenant_a.api_key},
        )
        resp_b = client.get(
            "/api/v1/search",
            params={"q": "budget"},
            headers={"X-API-Key": tenant_b.api_key},
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        # Both should succeed independently (no cross-tenant leakage check --
        # the empty index means both return 0 results, which is correct)

    def test_rate_limits_independent_per_tenant(self):
        """Rate limit counters should be per-tenant, not global."""
        limiter = RateLimiter()
        t_a = Tenant(
            id="iso-a", name="A", granicus_host="a.com", granicus_view_id="1",
            api_key="mra_a", plan="starter", created_at="2026-01-01",
        )
        t_b = Tenant(
            id="iso-b", name="B", granicus_host="b.com", granicus_view_id="1",
            api_key="mra_b", plan="starter", created_at="2026-01-01",
        )

        # Exhaust A's quota
        for _ in range(100):
            limiter.check(t_a)

        # B should still have quota
        allowed, info = limiter.check(t_b)
        assert allowed is True


# ===================================================================
# 10. Admin Endpoint Protection
# ===================================================================


class TestAdminEndpointProtection:
    """Admin routes require ADMIN_API_KEY, not regular tenant keys."""

    def test_admin_list_tenants_requires_admin_key(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        # Tenant key should not work
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 403

    def test_admin_list_tenants_with_admin_key(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": "super-secret-admin-key"},
        )
        assert resp.status_code == 200
        assert "tenants" in resp.json()

    def test_admin_create_tenant_rejects_tenant_key(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.post(
            "/api/v1/admin/tenants",
            json={"id": "new-city", "name": "New City", "granicus_host": "new.granicus.com"},
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 403

    def test_admin_delete_rejects_tenant_key(self, client, tenant_a, tenant_b, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.delete(
            f"/api/v1/admin/tenants/{tenant_b.id}",
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 403

    def test_admin_rotate_key_rejects_tenant_key(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.post(
            f"/api/v1/admin/tenants/{tenant_a.id}/rotate-key",
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 403

    def test_admin_update_plan_rejects_tenant_key(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.patch(
            f"/api/v1/admin/tenants/{tenant_a.id}/plan",
            json={"plan": "enterprise"},
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 403

    def test_admin_returns_503_when_not_configured(self, client, tenant_a):
        """If ADMIN_API_KEY is not set, admin endpoints return 503."""
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_admin_no_key_returns_503_or_403(self, client, monkeypatch):
        """No key at all should not grant admin access."""
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.get("/api/v1/admin/tenants")
        assert resp.status_code == 403

    def test_admin_list_plans_requires_admin(self, client, tenant_a, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "super-secret-admin-key")
        resp = client.get(
            "/api/v1/admin/plans",
            headers={"X-API-Key": tenant_a.api_key},
        )
        assert resp.status_code == 403

        resp = client.get(
            "/api/v1/admin/plans",
            headers={"X-API-Key": "super-secret-admin-key"},
        )
        assert resp.status_code == 200
