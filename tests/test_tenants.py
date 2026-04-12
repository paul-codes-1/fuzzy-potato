"""Tests for api/tenants.py - TenantManager facade and CLI subcommands."""

import subprocess
import sys

import pytest

from api.auth import PLAN_LIMITS, TenantStore
from api.tenants import TenantManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "tenants.db")
    s = TenantStore(db_path)
    yield s
    s.close()


@pytest.fixture
def manager(store):
    return TenantManager(store)


# ============================================================
# 1. TenantManager CRUD
# ============================================================

class TestTenantManager:

    def test_create_tenant(self, manager):
        tenant = manager.create(
            tenant_id="lex-ky",
            name="Lexington KY",
            granicus_host="lexington.granicus.com",
            granicus_view_id="14",
            plan="pro",
        )
        assert tenant.id == "lex-ky"
        assert tenant.plan == "pro"

    def test_get_tenant(self, manager):
        manager.create("t1", "T1", "t1.com")
        assert manager.get("t1") is not None
        assert manager.get("nope") is None

    def test_get_by_key(self, manager):
        tenant = manager.create("t1", "T1", "t1.com")
        found = manager.get_by_key(tenant.api_key)
        assert found is not None
        assert found.id == "t1"

    def test_list_tenants(self, manager):
        manager.create("a", "A", "a.com")
        manager.create("b", "B", "b.com")
        tenants = manager.list()
        assert len(tenants) == 2

    def test_delete_tenant(self, manager):
        manager.create("t1", "T1", "t1.com")
        assert manager.delete("t1") is True
        assert manager.get("t1") is None

    def test_delete_nonexistent(self, manager):
        assert manager.delete("nope") is False

    def test_rotate_key(self, manager):
        tenant = manager.create("t1", "T1", "t1.com")
        old_key = tenant.api_key
        new_key = manager.rotate_key("t1")
        assert new_key != old_key
        assert new_key.startswith("mra_")

    def test_update_plan(self, manager):
        manager.create("t1", "T1", "t1.com", plan="starter")
        assert manager.update_plan("t1", "enterprise") is True
        assert manager.get("t1").plan == "enterprise"

    def test_update_plan_invalid(self, manager):
        manager.create("t1", "T1", "t1.com")
        with pytest.raises(ValueError, match="Invalid plan"):
            manager.update_plan("t1", "mega")


# ============================================================
# 2. Plan definitions
# ============================================================

class TestPlanDefinitions:

    def test_plan_definitions_keys(self):
        defs = TenantManager.plan_definitions()
        assert set(defs.keys()) == {"starter", "pro", "enterprise"}

    def test_starter_has_numeric_limit(self):
        defs = TenantManager.plan_definitions()
        assert defs["starter"]["queries_per_month"] == 100

    def test_pro_has_numeric_limit(self):
        defs = TenantManager.plan_definitions()
        assert defs["pro"]["queries_per_month"] == 1000

    def test_enterprise_is_unlimited(self):
        defs = TenantManager.plan_definitions()
        assert defs["enterprise"]["queries_per_month"] == "unlimited"

    def test_plan_limits_match(self):
        """TenantManager.plan_definitions should be consistent with PLAN_LIMITS."""
        defs = TenantManager.plan_definitions()
        for plan, limit in PLAN_LIMITS.items():
            expected = limit if limit else "unlimited"
            assert defs[plan]["queries_per_month"] == expected


# ============================================================
# 3. CLI subcommands via subprocess
# ============================================================

class TestCLI:
    """Run api.tenants CLI subcommands via subprocess to verify arg parsing and output."""

    def _run(self, args, tmp_path, expect_success=True):
        cmd = [
            sys.executable, "-m", "api.tenants",
            "--output-dir", str(tmp_path),
        ] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if expect_success:
            assert result.returncode == 0, f"stderr: {result.stderr}"
        return result

    def test_cli_plans(self, tmp_path):
        result = self._run(["plans"], tmp_path)
        assert "starter" in result.stdout
        assert "pro" in result.stdout
        assert "enterprise" in result.stdout

    def test_cli_create_and_list(self, tmp_path):
        # Create
        result = self._run([
            "create",
            "--id", "cli-test",
            "--name", "CLI Test City",
            "--granicus-host", "cli.granicus.com",
            "--plan", "pro",
        ], tmp_path)
        assert "cli-test" in result.stdout
        assert "mra_" in result.stdout  # API key printed

        # List
        result = self._run(["list"], tmp_path)
        assert "cli-test" in result.stdout
        assert "CLI Test City" in result.stdout

    def test_cli_delete(self, tmp_path):
        self._run([
            "create", "--id", "del-me", "--name", "Delete Me",
            "--granicus-host", "del.com",
        ], tmp_path)
        result = self._run(["delete", "--id", "del-me"], tmp_path)
        assert "Deleted" in result.stdout

    def test_cli_delete_nonexistent(self, tmp_path):
        result = self._run(["delete", "--id", "nope"], tmp_path, expect_success=False)
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()

    def test_cli_rotate_key(self, tmp_path):
        self._run([
            "create", "--id", "rot-test", "--name", "Rotate Test",
            "--granicus-host", "rot.com",
        ], tmp_path)
        result = self._run(["rotate-key", "--id", "rot-test"], tmp_path)
        assert "Rotated" in result.stdout
        assert "mra_" in result.stdout

    def test_cli_update_plan(self, tmp_path):
        self._run([
            "create", "--id", "plan-test", "--name", "Plan Test",
            "--granicus-host", "plan.com",
        ], tmp_path)
        result = self._run(["update-plan", "--id", "plan-test", "--plan", "enterprise"], tmp_path)
        assert "enterprise" in result.stdout

    def test_cli_list_empty(self, tmp_path):
        result = self._run(["list"], tmp_path)
        assert "No tenants found" in result.stdout
