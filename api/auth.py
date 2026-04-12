"""API key authentication and rate limiting middleware for the multi-tenant API."""

import os
import sqlite3
import time
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

import logging as _logging

_auth_logger = _logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tenant model
# ---------------------------------------------------------------------------

PLAN_LIMITS = {
    "starter": 100,       # queries per month
    "pro": 1000,
    "enterprise": None,   # unlimited
}

VALID_PLANS = set(PLAN_LIMITS.keys())


@dataclass
class Tenant:
    id: str
    name: str
    granicus_host: str
    granicus_view_id: str
    api_key: str
    plan: str
    created_at: str
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None

    @property
    def monthly_limit(self) -> Optional[int]:
        return PLAN_LIMITS.get(self.plan)


# ---------------------------------------------------------------------------
# SQLite tenant store
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    granicus_host TEXT NOT NULL,
    granicus_view_id TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'starter',
    created_at TEXT NOT NULL
);
"""


class TenantStore:
    """Thin SQLite wrapper for tenant CRUD.  Thread-safe via check_same_thread=False
    (FastAPI may serve requests from different threads).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        self._migrate()

    # -- schema migration ----------------------------------------------------

    def _migrate(self):
        """Add columns introduced after the initial schema."""
        cursor = self._conn.execute("PRAGMA table_info(tenants)")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        migrations = [
            ("stripe_customer_id", "TEXT"),
            ("stripe_subscription_id", "TEXT"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing_cols:
                self._conn.execute(f"ALTER TABLE tenants ADD COLUMN {col_name} {col_type}")
        self._conn.commit()

    # -- helpers -------------------------------------------------------------

    def _row_to_tenant(self, row: sqlite3.Row) -> Tenant:
        d = dict(row)
        # Handle optional billing fields that may be NULL
        d.setdefault("stripe_customer_id", None)
        d.setdefault("stripe_subscription_id", None)
        return Tenant(**d)

    # -- CRUD ----------------------------------------------------------------

    def get_by_api_key(self, api_key: str) -> Optional[Tenant]:
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE api_key = ?", (api_key,)
        ).fetchone()
        return self._row_to_tenant(row) if row else None

    def get_by_id(self, tenant_id: str) -> Optional[Tenant]:
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        return self._row_to_tenant(row) if row else None

    def list_all(self) -> list[Tenant]:
        rows = self._conn.execute("SELECT * FROM tenants ORDER BY created_at DESC").fetchall()
        return [self._row_to_tenant(r) for r in rows]

    def create(
        self,
        tenant_id: str,
        name: str,
        granicus_host: str,
        granicus_view_id: str = "",
        plan: str = "starter",
    ) -> Tenant:
        if plan not in VALID_PLANS:
            raise ValueError(f"Invalid plan '{plan}'. Choose from: {', '.join(sorted(VALID_PLANS))}")
        api_key = f"mra_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO tenants (id, name, granicus_host, granicus_view_id, api_key, plan, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, name, granicus_host, granicus_view_id, api_key, plan, now),
        )
        self._conn.commit()

        # Schedule onboarding email sequence for the new tenant
        try:
            from api.onboarding_emails import get_onboarding_manager
            manager = get_onboarding_manager()
            # Use tenant name as a placeholder; admin_email is not available here
            # so callers should invoke schedule_sequence() separately with the real email.
            _auth_logger.debug(
                "Onboarding email manager available for tenant %s; "
                "call schedule_sequence() with admin_email to start drip campaign.",
                tenant_id,
            )
        except (RuntimeError, ImportError):
            _auth_logger.debug(
                "Onboarding email manager not initialized; skipping sequence for tenant %s",
                tenant_id,
            )

        return self.get_by_id(tenant_id)  # type: ignore[return-value]

    def delete(self, tenant_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def rotate_key(self, tenant_id: str) -> Optional[str]:
        new_key = f"mra_{secrets.token_urlsafe(32)}"
        cur = self._conn.execute(
            "UPDATE tenants SET api_key = ? WHERE id = ?", (new_key, tenant_id)
        )
        self._conn.commit()
        return new_key if cur.rowcount > 0 else None

    def update_plan(self, tenant_id: str, plan: str) -> bool:
        if plan not in VALID_PLANS:
            raise ValueError(f"Invalid plan '{plan}'. Choose from: {', '.join(sorted(VALID_PLANS))}")
        cur = self._conn.execute(
            "UPDATE tenants SET plan = ? WHERE id = ?", (plan, tenant_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- billing fields ------------------------------------------------------

    def update_stripe_customer_id(self, tenant_id: str, customer_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE tenants SET stripe_customer_id = ? WHERE id = ?", (customer_id, tenant_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_stripe_subscription_id(self, tenant_id: str, subscription_id: Optional[str]) -> bool:
        cur = self._conn.execute(
            "UPDATE tenants SET stripe_subscription_id = ? WHERE id = ?", (subscription_id, tenant_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_by_stripe_customer_id(self, customer_id: str) -> Optional[Tenant]:
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE stripe_customer_id = ?", (customer_id,)
        ).fetchone()
        return self._row_to_tenant(row) if row else None

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# In-memory rate limiter (sliding window counter per tenant)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple in-memory rate limiter using a sliding 30-day window.

    Stores (tenant_id -> list of request timestamps).  Evicts timestamps
    older than 30 days on each check.  Resets on process restart -- acceptable
    for a single-instance deployment.  For distributed deployments, swap this
    for a Redis-based counter.
    """

    WINDOW_SECONDS = 30 * 24 * 3600  # 30 days

    def __init__(self):
        self._requests: dict[str, list[float]] = {}

    def check(self, tenant: Tenant) -> tuple[bool, dict]:
        """Return (allowed, info_dict).  info_dict always contains
        remaining / limit / reset_at for inclusion in response headers.
        """
        limit = tenant.monthly_limit
        if limit is None:
            return True, {"limit": "unlimited", "remaining": "unlimited", "reset_at": None}

        now = time.time()
        cutoff = now - self.WINDOW_SECONDS

        # Evict stale entries
        timestamps = self._requests.get(tenant.id, [])
        timestamps = [t for t in timestamps if t > cutoff]
        self._requests[tenant.id] = timestamps

        used = len(timestamps)
        remaining = max(0, limit - used)
        reset_at = int(cutoff + self.WINDOW_SECONDS)

        info = {"limit": limit, "remaining": remaining, "reset_at": reset_at}

        if used >= limit:
            return False, info

        # Record this request
        timestamps.append(now)
        info["remaining"] = remaining - 1
        return True, info


# ---------------------------------------------------------------------------
# FastAPI dependency for authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Module-level singletons -- initialized by init_auth()
_tenant_store: Optional[TenantStore] = None
_rate_limiter: Optional[RateLimiter] = None


def init_auth(output_dir: str) -> TenantStore:
    """Initialize the tenant store and rate limiter.  Call once at startup."""
    global _tenant_store, _rate_limiter
    db_path = os.path.join(output_dir, "tenants.db")
    _tenant_store = TenantStore(db_path)
    _rate_limiter = RateLimiter()
    return _tenant_store


def get_tenant_store() -> TenantStore:
    if _tenant_store is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        return init_auth(output_dir)
    return _tenant_store


def _auth_required() -> bool:
    return os.environ.get("AUTH_REQUIRED", "").lower() in ("1", "true", "yes")


def _build_dev_tenant() -> Tenant:
    return Tenant(
        id="dev",
        name="Development",
        granicus_host=os.environ.get("GRANICUS_HOST", ""),
        granicus_view_id=os.environ.get("GRANICUS_VIEW_ID", ""),
        api_key="dev",
        plan="enterprise",
        created_at=datetime.now(timezone.utc).isoformat(),
        stripe_customer_id=None,
        stripe_subscription_id=None,
    )


async def _resolve_tenant(
    request: Optional[Request],
    api_key: Optional[str],
    *,
    allow_legacy_dev_fallback: bool = False,
) -> Tenant:
    """Resolve the current tenant from API key, JWT session, or dev fallback.

    Authentication methods (checked in order):
    1. X-API-Key header -- for programmatic / API access
    2. Authorization: Bearer <jwt> header -- for SSO browser sessions
    3. civiclens_session cookie -- for SSO browser sessions (fallback)

    In development mode (no tenants exist and AUTH_REQUIRED is not set),
    returns a default dev tenant so existing clients keep working.
    """
    store = get_tenant_store()

    # --- Try API key first (existing behavior) ---
    if api_key:
        tenant = store.get_by_api_key(api_key)
        if tenant is None:
            raise HTTPException(status_code=401, detail="Invalid API key.")

        # Rate limiting
        allowed, info = _rate_limiter.check(tenant)  # type: ignore[union-attr]
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Plan '{tenant.plan}' allows {info['limit']} queries/month.",
                headers={"Retry-After": "3600"},
            )
        return tenant

    # --- Try JWT Bearer token (SSO sessions) ---
    jwt_token = _extract_jwt_token(request)
    if jwt_token:
        try:
            from api.sso import verify_session_token
            payload = verify_session_token(jwt_token)
            tenant_id = payload.get("tenant_id")
            if not tenant_id:
                raise HTTPException(status_code=401, detail="JWT missing tenant_id claim.")

            tenant = store.get_by_id(tenant_id)
            if tenant is None:
                raise HTTPException(status_code=401, detail="Tenant from JWT not found.")

            # Rate limiting applies to JWT sessions too
            allowed, info = _rate_limiter.check(tenant)  # type: ignore[union-attr]
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Plan '{tenant.plan}' allows {info['limit']} queries/month.",
                    headers={"Retry-After": "3600"},
                )

            # Stash SSO user info on request state for downstream use
            if request is not None:
                request.state.sso_user = payload
            return tenant
        except HTTPException:
            raise
        except Exception as e:
            _auth_logger.debug("JWT verification failed: %s", e)
            raise HTTPException(status_code=401, detail="Invalid or expired session token.")

    # --- Dev mode fallback ---
    auth_required = _auth_required()
    if not auth_required:
        if allow_legacy_dev_fallback:
            return _build_dev_tenant()
        if len(store.list_all()) == 0:
            return _build_dev_tenant()

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide X-API-Key header or Authorization: Bearer <token>.",
    )


async def require_tenant(
    request: Request = None,
    api_key: Optional[str] = Security(_api_key_header),
) -> Tenant:
    """Primary auth dependency for authenticated API routes."""
    return await _resolve_tenant(request, api_key)


async def require_tenant_legacy(
    request: Request = None,
    api_key: Optional[str] = Security(_api_key_header),
) -> Tenant:
    """Backward-compatible auth for legacy endpoints.

    Older single-tenant deployments historically hit `/ask` and `/chat`
    without provisioning API keys. In dev mode only, continue allowing that
    behavior; production should set `AUTH_REQUIRED=true`.
    """
    return await _resolve_tenant(request, api_key, allow_legacy_dev_fallback=True)


def _extract_jwt_token(request: Optional[Request]) -> Optional[str]:
    """Extract JWT from Authorization header or session cookie."""
    if request is None:
        return None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return request.cookies.get("civiclens_session")


async def require_admin(api_key: Optional[str] = Security(_api_key_header)) -> bool:
    """FastAPI dependency for admin-only endpoints.

    Validates against the ADMIN_API_KEY environment variable.
    """
    admin_key = os.environ.get("ADMIN_API_KEY")
    if not admin_key:
        raise HTTPException(
            status_code=503,
            detail="Admin API is not configured. Set ADMIN_API_KEY environment variable.",
        )
    if not api_key or api_key != admin_key:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return True
