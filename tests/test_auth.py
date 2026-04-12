"""Tests for api/auth.py - TenantStore CRUD, API key format, RateLimiter, dev mode."""

import os
from unittest.mock import patch

import pytest

from api.auth import RateLimiter, Tenant, TenantStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """Create a TenantStore backed by a temp SQLite database."""
    db_path = str(tmp_path / "tenants.db")
    s = TenantStore(db_path)
    yield s
    s.close()


@pytest.fixture
def sample_tenant(store):
    """Create and return a sample tenant."""
    return store.create(
        tenant_id="test-city",
        name="Test City",
        granicus_host="test.granicus.com",
        granicus_view_id="14",
        plan="starter",
    )


# ============================================================
# 1. TenantStore CRUD
# ============================================================

class TestTenantStoreCRUD:

    def test_create_tenant(self, store):
        tenant = store.create(
            tenant_id="lex-ky",
            name="Lexington KY",
            granicus_host="lexington.granicus.com",
            granicus_view_id="14",
            plan="starter",
        )
        assert tenant.id == "lex-ky"
        assert tenant.name == "Lexington KY"
        assert tenant.granicus_host == "lexington.granicus.com"
        assert tenant.granicus_view_id == "14"
        assert tenant.plan == "starter"
        assert tenant.created_at is not None

    def test_get_by_api_key(self, store, sample_tenant):
        found = store.get_by_api_key(sample_tenant.api_key)
        assert found is not None
        assert found.id == sample_tenant.id
        assert found.name == sample_tenant.name

    def test_get_by_api_key_returns_none_for_unknown(self, store):
        assert store.get_by_api_key("mra_nonexistent") is None

    def test_get_by_id(self, store, sample_tenant):
        found = store.get_by_id("test-city")
        assert found is not None
        assert found.id == "test-city"

    def test_get_by_id_returns_none_for_unknown(self, store):
        assert store.get_by_id("nope") is None

    def test_list_all(self, store):
        store.create("a", "Tenant A", "a.granicus.com")
        store.create("b", "Tenant B", "b.granicus.com")
        tenants = store.list_all()
        assert len(tenants) == 2
        ids = {t.id for t in tenants}
        assert ids == {"a", "b"}

    def test_delete_tenant(self, store, sample_tenant):
        assert store.delete("test-city") is True
        assert store.get_by_id("test-city") is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("nope") is False

    def test_rotate_key(self, store, sample_tenant):
        old_key = sample_tenant.api_key
        new_key = store.rotate_key("test-city")
        assert new_key is not None
        assert new_key != old_key
        assert new_key.startswith("mra_")
        # Old key no longer works
        assert store.get_by_api_key(old_key) is None
        # New key works
        assert store.get_by_api_key(new_key) is not None

    def test_rotate_key_nonexistent_returns_none(self, store):
        assert store.rotate_key("nope") is None

    def test_update_plan(self, store, sample_tenant):
        assert store.update_plan("test-city", "pro") is True
        tenant = store.get_by_id("test-city")
        assert tenant.plan == "pro"

    def test_update_plan_invalid_raises(self, store, sample_tenant):
        with pytest.raises(ValueError, match="Invalid plan"):
            store.update_plan("test-city", "ultra")

    def test_update_plan_nonexistent_returns_false(self, store):
        assert store.update_plan("nope", "pro") is False

    def test_create_with_invalid_plan_raises(self, store):
        with pytest.raises(ValueError, match="Invalid plan"):
            store.create("bad", "Bad", "bad.com", plan="ultra")

    def test_duplicate_id_raises(self, store, sample_tenant):
        with pytest.raises(Exception):
            store.create("test-city", "Duplicate", "dup.com")


# ============================================================
# 2. API key format
# ============================================================

class TestAPIKeyFormat:

    def test_api_key_has_mra_prefix(self, store, sample_tenant):
        assert sample_tenant.api_key.startswith("mra_")

    def test_api_key_sufficient_entropy(self, store, sample_tenant):
        # token_urlsafe(32) produces 43 chars, so key should be > 40 chars total
        assert len(sample_tenant.api_key) > 40

    def test_api_keys_unique_across_tenants(self, store):
        t1 = store.create("t1", "T1", "t1.com")
        t2 = store.create("t2", "T2", "t2.com")
        assert t1.api_key != t2.api_key


# ============================================================
# 3. RateLimiter
# ============================================================

class TestRateLimiter:

    def test_allow_within_limit(self):
        limiter = RateLimiter()
        tenant = Tenant(
            id="t1", name="T1", granicus_host="", granicus_view_id="",
            api_key="mra_test", plan="starter", created_at="",
        )
        # Starter plan has 100 queries/month
        for _ in range(100):
            allowed, info = limiter.check(tenant)
            assert allowed is True
        assert info["remaining"] == 0

    def test_block_over_limit(self):
        limiter = RateLimiter()
        tenant = Tenant(
            id="t1", name="T1", granicus_host="", granicus_view_id="",
            api_key="mra_test", plan="starter", created_at="",
        )
        # Exhaust limit
        for _ in range(100):
            limiter.check(tenant)
        allowed, info = limiter.check(tenant)
        assert allowed is False
        assert info["remaining"] == 0

    def test_enterprise_unlimited(self):
        limiter = RateLimiter()
        tenant = Tenant(
            id="t1", name="T1", granicus_host="", granicus_view_id="",
            api_key="mra_test", plan="enterprise", created_at="",
        )
        for _ in range(5000):
            allowed, info = limiter.check(tenant)
            assert allowed is True
        assert info["limit"] == "unlimited"
        assert info["remaining"] == "unlimited"

    def test_pro_limit_is_1000(self):
        limiter = RateLimiter()
        tenant = Tenant(
            id="t1", name="T1", granicus_host="", granicus_view_id="",
            api_key="mra_test", plan="pro", created_at="",
        )
        # First request should show remaining = limit - 1
        allowed, info = limiter.check(tenant)
        assert allowed is True
        assert info["limit"] == 1000
        assert info["remaining"] == 999

    def test_rate_limiter_info_contains_required_keys(self):
        limiter = RateLimiter()
        tenant = Tenant(
            id="t1", name="T1", granicus_host="", granicus_view_id="",
            api_key="mra_test", plan="starter", created_at="",
        )
        _, info = limiter.check(tenant)
        assert "limit" in info
        assert "remaining" in info
        assert "reset_at" in info


# ============================================================
# 4. Dev mode fallback
# ============================================================

class TestDevModeFallback:

    @pytest.mark.anyio
    async def test_dev_mode_no_tenants_no_auth_required(self, store):
        """When no tenants exist and AUTH_REQUIRED is not set, return dev tenant."""
        from api.auth import require_tenant as _require_tenant
        import api.auth as auth_module

        old_store = auth_module._tenant_store
        old_limiter = auth_module._rate_limiter
        auth_module._tenant_store = store
        auth_module._rate_limiter = RateLimiter()
        try:
            with patch.dict(os.environ, {}, clear=False):
                # Remove AUTH_REQUIRED if set
                os.environ.pop("AUTH_REQUIRED", None)
                tenant = await _require_tenant(api_key=None)
                assert tenant.id == "dev"
                assert tenant.plan == "enterprise"
        finally:
            auth_module._tenant_store = old_store
            auth_module._rate_limiter = old_limiter

    @pytest.mark.anyio
    async def test_auth_required_env_rejects_without_key(self, store):
        """When AUTH_REQUIRED=1, reject requests with no key even if no tenants exist."""
        from fastapi import HTTPException
        from api.auth import require_tenant as _require_tenant
        import api.auth as auth_module

        old_store = auth_module._tenant_store
        old_limiter = auth_module._rate_limiter
        auth_module._tenant_store = store
        auth_module._rate_limiter = RateLimiter()
        try:
            with patch.dict(os.environ, {"AUTH_REQUIRED": "1"}):
                with pytest.raises(HTTPException) as exc_info:
                    await _require_tenant(api_key=None)
                assert exc_info.value.status_code == 401
        finally:
            auth_module._tenant_store = old_store
            auth_module._rate_limiter = old_limiter

    @pytest.mark.anyio
    async def test_invalid_key_returns_401(self, store, sample_tenant):
        """Providing an invalid API key returns 401."""
        from fastapi import HTTPException
        from api.auth import require_tenant as _require_tenant
        import api.auth as auth_module

        old_store = auth_module._tenant_store
        old_limiter = auth_module._rate_limiter
        auth_module._tenant_store = store
        auth_module._rate_limiter = RateLimiter()
        try:
            with pytest.raises(HTTPException) as exc_info:
                await _require_tenant(api_key="mra_bogus_key")
            assert exc_info.value.status_code == 401
        finally:
            auth_module._tenant_store = old_store
            auth_module._rate_limiter = old_limiter


# ============================================================
# 5. Schema migration (stripe fields)
# ============================================================

class TestSchemaMigration:

    def test_stripe_fields_added_on_init(self, tmp_path):
        """Creating TenantStore on a fresh DB should include stripe columns."""
        db_path = str(tmp_path / "tenants.db")
        store = TenantStore(db_path)
        tenant = store.create("t1", "T1", "t1.com")
        assert tenant.stripe_customer_id is None
        assert tenant.stripe_subscription_id is None
        store.close()

    def test_update_stripe_customer_id(self, store, sample_tenant):
        store.update_stripe_customer_id("test-city", "cus_12345")
        tenant = store.get_by_id("test-city")
        assert tenant.stripe_customer_id == "cus_12345"

    def test_update_stripe_subscription_id(self, store, sample_tenant):
        store.update_stripe_subscription_id("test-city", "sub_12345")
        tenant = store.get_by_id("test-city")
        assert tenant.stripe_subscription_id == "sub_12345"

    def test_get_by_stripe_customer_id(self, store, sample_tenant):
        store.update_stripe_customer_id("test-city", "cus_abc")
        found = store.get_by_stripe_customer_id("cus_abc")
        assert found is not None
        assert found.id == "test-city"

    def test_get_by_stripe_customer_id_unknown(self, store):
        assert store.get_by_stripe_customer_id("cus_nope") is None

    def test_migration_on_existing_db_without_stripe_cols(self, tmp_path):
        """Simulate an old DB without stripe columns, then re-open to trigger migration."""
        import sqlite3

        db_path = str(tmp_path / "old_tenants.db")
        # Create table without stripe columns
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                granicus_host TEXT NOT NULL,
                granicus_view_id TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL UNIQUE,
                plan TEXT NOT NULL DEFAULT 'starter',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO tenants VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("old-t", "Old Tenant", "old.com", "", "mra_oldkey", "starter", "2025-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        # Re-open with TenantStore -- should migrate
        store = TenantStore(db_path)
        tenant = store.get_by_id("old-t")
        assert tenant is not None
        assert tenant.stripe_customer_id is None
        assert tenant.stripe_subscription_id is None
        store.close()


# ============================================================
# 6. Tenant monthly_limit property
# ============================================================

class TestTenantMonthlyLimit:

    def test_starter_limit(self):
        t = Tenant(id="x", name="X", granicus_host="", granicus_view_id="",
                   api_key="k", plan="starter", created_at="")
        assert t.monthly_limit == 100

    def test_pro_limit(self):
        t = Tenant(id="x", name="X", granicus_host="", granicus_view_id="",
                   api_key="k", plan="pro", created_at="")
        assert t.monthly_limit == 1000

    def test_enterprise_limit_is_none(self):
        t = Tenant(id="x", name="X", granicus_host="", granicus_view_id="",
                   api_key="k", plan="enterprise", created_at="")
        assert t.monthly_limit is None
