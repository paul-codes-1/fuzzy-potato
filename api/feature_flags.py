"""Feature flag system for plan-gated, gradual rollout, and per-tenant feature control.

SQLite-backed feature flags with:
- Plan-tier gating (starter / pro / enterprise)
- Percentage-based gradual rollout (consistent hashing on tenant_id)
- Per-tenant overrides (force enable or disable)
"""

import hashlib
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException

from api.auth import Tenant, require_tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan hierarchy (higher index = higher tier)
# ---------------------------------------------------------------------------

PLAN_HIERARCHY = ["starter", "pro", "enterprise"]


def _plan_rank(plan: str) -> int:
    """Return numeric rank for a plan name. Unknown plans get -1."""
    try:
        return PLAN_HIERARCHY.index(plan)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# Feature flag dataclass
# ---------------------------------------------------------------------------

@dataclass
class FeatureFlag:
    name: str
    description: str
    default_enabled: bool = True
    plan_minimum: str = "starter"
    rollout_percentage: int = 100
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Built-in flag definitions
# ---------------------------------------------------------------------------

BUILTIN_FLAGS: list[dict] = [
    {
        "name": "api_access",
        "description": "Programmatic API access",
        "default_enabled": True,
        "plan_minimum": "starter",
        "rollout_percentage": 100,
    },
    {
        "name": "chat_anthropic",
        "description": "Multi-turn chat powered by Anthropic models",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "sentiment_analysis",
        "description": "Public comment sentiment analysis and trend tracking",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "slack_integration",
        "description": "Slack bot with slash commands and notifications",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "custom_branding",
        "description": "Per-tenant white-label branding (logo, colors, CSS)",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "data_export",
        "description": "Bulk data export and FOIA request handler",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "webhooks",
        "description": "Webhook subscriptions with HMAC-signed delivery",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "push_notifications",
        "description": "Web Push notifications via VAPID",
        "default_enabled": True,
        "plan_minimum": "pro",
        "rollout_percentage": 100,
    },
    {
        "name": "teams_integration",
        "description": "Microsoft Teams webhooks and Adaptive Cards",
        "default_enabled": True,
        "plan_minimum": "enterprise",
        "rollout_percentage": 100,
    },
    {
        "name": "zapier_integration",
        "description": "Zapier triggers and actions for workflow automation",
        "default_enabled": True,
        "plan_minimum": "enterprise",
        "rollout_percentage": 100,
    },
    {
        "name": "sso_saml",
        "description": "SAML 2.0 Single Sign-On with per-tenant IdP config",
        "default_enabled": True,
        "plan_minimum": "enterprise",
        "rollout_percentage": 100,
    },
    {
        "name": "report_builder",
        "description": "Custom report builder with scheduled delivery",
        "default_enabled": True,
        "plan_minimum": "enterprise",
        "rollout_percentage": 100,
    },
]

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_FLAGS_TABLE = """
CREATE TABLE IF NOT EXISTS feature_flags (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    default_enabled INTEGER NOT NULL DEFAULT 1,
    plan_minimum TEXT NOT NULL DEFAULT 'starter',
    rollout_percentage INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_CREATE_OVERRIDES_TABLE = """
CREATE TABLE IF NOT EXISTS feature_flag_overrides (
    flag_name TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (flag_name, tenant_id)
);
"""


# ---------------------------------------------------------------------------
# FeatureFlagManager
# ---------------------------------------------------------------------------

class FeatureFlagManager:
    """SQLite-backed feature flag manager with plan gating, gradual rollout,
    and per-tenant overrides.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_FLAGS_TABLE)
        self._conn.execute(_CREATE_OVERRIDES_TABLE)
        self._conn.commit()
        self._seed_builtin_flags()

    # -- seeding -------------------------------------------------------------

    def _seed_builtin_flags(self):
        """Insert built-in flags if they don't already exist."""
        now = datetime.now(timezone.utc).isoformat()
        for flag_def in BUILTIN_FLAGS:
            existing = self._conn.execute(
                "SELECT name FROM feature_flags WHERE name = ?", (flag_def["name"],)
            ).fetchone()
            if not existing:
                self._conn.execute(
                    "INSERT INTO feature_flags (name, description, default_enabled, "
                    "plan_minimum, rollout_percentage, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        flag_def["name"],
                        flag_def["description"],
                        1 if flag_def["default_enabled"] else 0,
                        flag_def["plan_minimum"],
                        flag_def["rollout_percentage"],
                        now,
                        now,
                    ),
                )
        self._conn.commit()

    # -- read ----------------------------------------------------------------

    def _row_to_flag(self, row: sqlite3.Row) -> FeatureFlag:
        d = dict(row)
        d["default_enabled"] = bool(d["default_enabled"])
        return FeatureFlag(**d)

    def get_flag(self, flag_name: str) -> Optional[FeatureFlag]:
        row = self._conn.execute(
            "SELECT * FROM feature_flags WHERE name = ?", (flag_name,)
        ).fetchone()
        return self._row_to_flag(row) if row else None

    def list_flags(self) -> list[FeatureFlag]:
        rows = self._conn.execute(
            "SELECT * FROM feature_flags ORDER BY name"
        ).fetchall()
        return [self._row_to_flag(r) for r in rows]

    def get_overrides(self, flag_name: str) -> dict[str, bool]:
        """Return {tenant_id: enabled} overrides for a flag."""
        rows = self._conn.execute(
            "SELECT tenant_id, enabled FROM feature_flag_overrides WHERE flag_name = ?",
            (flag_name,),
        ).fetchall()
        return {r["tenant_id"]: bool(r["enabled"]) for r in rows}

    # -- write ---------------------------------------------------------------

    def update_flag(
        self,
        flag_name: str,
        *,
        description: Optional[str] = None,
        default_enabled: Optional[bool] = None,
        plan_minimum: Optional[str] = None,
        rollout_percentage: Optional[int] = None,
    ) -> Optional[FeatureFlag]:
        """Update a flag's configuration. Returns updated flag or None if not found."""
        flag = self.get_flag(flag_name)
        if flag is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        updates: list[str] = []
        params: list = []

        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if default_enabled is not None:
            updates.append("default_enabled = ?")
            params.append(1 if default_enabled else 0)
        if plan_minimum is not None:
            if plan_minimum not in PLAN_HIERARCHY:
                raise ValueError(
                    f"Invalid plan_minimum '{plan_minimum}'. "
                    f"Choose from: {', '.join(PLAN_HIERARCHY)}"
                )
            updates.append("plan_minimum = ?")
            params.append(plan_minimum)
        if rollout_percentage is not None:
            if not (0 <= rollout_percentage <= 100):
                raise ValueError("rollout_percentage must be between 0 and 100")
            updates.append("rollout_percentage = ?")
            params.append(rollout_percentage)

        if not updates:
            return flag

        updates.append("updated_at = ?")
        params.append(now)
        params.append(flag_name)

        self._conn.execute(
            f"UPDATE feature_flags SET {', '.join(updates)} WHERE name = ?",
            params,
        )
        self._conn.commit()
        logger.info("Updated feature flag '%s': %s", flag_name, updates)
        return self.get_flag(flag_name)

    def set_override(
        self, flag_name: str, tenant_id: str, enabled: bool
    ) -> bool:
        """Set a per-tenant override for a flag. Returns True on success."""
        flag = self.get_flag(flag_name)
        if flag is None:
            return False

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO feature_flag_overrides "
            "(flag_name, tenant_id, enabled, created_at) VALUES (?, ?, ?, ?)",
            (flag_name, tenant_id, 1 if enabled else 0, now),
        )
        self._conn.commit()
        logger.info(
            "Set override for flag '%s' tenant '%s': enabled=%s",
            flag_name, tenant_id, enabled,
        )
        return True

    def remove_override(self, flag_name: str, tenant_id: str) -> bool:
        """Remove a per-tenant override. Returns True if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM feature_flag_overrides WHERE flag_name = ? AND tenant_id = ?",
            (flag_name, tenant_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- evaluation ----------------------------------------------------------

    def _rollout_hash(self, flag_name: str, tenant_id: str) -> int:
        """Consistent hash (0-99) for rollout bucketing.

        Uses SHA-256 of flag_name + tenant_id so the same tenant always lands
        in the same bucket for a given flag, but different flags give
        independent distributions.
        """
        digest = hashlib.sha256(f"{flag_name}:{tenant_id}".encode()).hexdigest()
        return int(digest[:8], 16) % 100

    def is_enabled(self, flag_name: str, tenant_id: str, tenant_plan: str = "starter") -> bool:
        """Check if a feature flag is enabled for a given tenant.

        Evaluation order:
        1. Per-tenant override (force enable / disable) -- highest priority
        2. Flag disabled globally (default_enabled=False) -- returns False
        3. Plan gating -- tenant plan must meet minimum tier
        4. Rollout percentage -- consistent hash determines inclusion
        """
        flag = self.get_flag(flag_name)
        if flag is None:
            # Unknown flags default to disabled
            return False

        # 1. Check per-tenant override
        override = self._conn.execute(
            "SELECT enabled FROM feature_flag_overrides "
            "WHERE flag_name = ? AND tenant_id = ?",
            (flag_name, tenant_id),
        ).fetchone()
        if override is not None:
            return bool(override["enabled"])

        # 2. Global toggle
        if not flag.default_enabled:
            return False

        # 3. Plan gating
        if _plan_rank(tenant_plan) < _plan_rank(flag.plan_minimum):
            return False

        # 4. Rollout percentage
        if flag.rollout_percentage >= 100:
            return True
        if flag.rollout_percentage <= 0:
            return False
        return self._rollout_hash(flag_name, tenant_id) < flag.rollout_percentage

    def flags_for_tenant(
        self, tenant_id: str, tenant_plan: str = "starter"
    ) -> dict[str, bool]:
        """Return {flag_name: enabled} for every defined flag."""
        flags = self.list_flags()
        return {f.name: self.is_enabled(f.name, tenant_id, tenant_plan) for f in flags}

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton -- initialized by init_feature_flags()
# ---------------------------------------------------------------------------

_manager: Optional[FeatureFlagManager] = None


def init_feature_flags(output_dir: str) -> FeatureFlagManager:
    """Initialize the feature flag manager. Call once at startup."""
    global _manager
    db_path = os.path.join(output_dir, "feature_flags.db")
    _manager = FeatureFlagManager(db_path)
    logger.info("Feature flag manager initialized (%s)", db_path)
    return _manager


def get_feature_flag_manager() -> FeatureFlagManager:
    """Return the singleton FeatureFlagManager. Raises if not initialized."""
    if _manager is None:
        raise RuntimeError(
            "Feature flags not initialized -- call init_feature_flags() first"
        )
    return _manager


# ---------------------------------------------------------------------------
# FastAPI dependency: require_feature(flag_name)
# ---------------------------------------------------------------------------

def require_feature(flag_name: str):
    """Return a FastAPI dependency that raises 403 if the flag is not enabled
    for the requesting tenant.

    Usage::

        @router.get("/api/v1/some-endpoint")
        def my_endpoint(
            tenant: Tenant = Depends(require_tenant),
            _flag: bool = Depends(require_feature("sentiment_analysis")),
        ):
            ...
    """

    async def _check(tenant: Tenant = Depends(require_tenant)) -> bool:
        manager = get_feature_flag_manager()
        if not manager.is_enabled(flag_name, tenant.id, tenant.plan):
            flag = manager.get_flag(flag_name)
            plan_min = flag.plan_minimum if flag else "enterprise"
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Feature '{flag_name}' is not available on your current plan "
                    f"({tenant.plan}). Upgrade to {plan_min} or higher to access "
                    f"this feature."
                ),
            )
        return True

    return _check
