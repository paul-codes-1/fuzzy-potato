"""FastAPI routes for the query response cache.

Exposes a handful of endpoints for inspecting and managing the cache.

Admin endpoints (require ``ADMIN_API_KEY``):

- ``GET  /api/v1/admin/cache/stats``
- ``POST /api/v1/admin/cache/invalidate/{tenant_id}``
- ``POST /api/v1/admin/cache/cleanup``

Tenant endpoints (require a valid tenant API key):

- ``GET  /api/v1/cache/stats`` -- the caller's own tenant-scoped view.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.auth import Tenant, require_admin, require_tenant
from api.query_cache import get_query_cache

router = APIRouter()


def _cache_or_503():
    cache = get_query_cache()
    if cache is None:
        raise HTTPException(
            status_code=503,
            detail="Query cache not initialized",
        )
    return cache


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@router.get("/api/v1/admin/cache/stats")
def admin_cache_stats(_: bool = Depends(require_admin)):
    """Global cache stats (entries, hits, misses, hit rate, bytes)."""
    cache = _cache_or_503()
    return cache.stats()


@router.post("/api/v1/admin/cache/invalidate/{tenant_id}")
def admin_cache_invalidate(tenant_id: str, _: bool = Depends(require_admin)):
    """Manual flush of a tenant's cache (e.g. after a schema/data fix)."""
    cache = _cache_or_503()
    removed = cache.invalidate_tenant(tenant_id)
    return {"tenant_id": tenant_id, "removed": removed}


@router.post("/api/v1/admin/cache/cleanup")
def admin_cache_cleanup(_: bool = Depends(require_admin)):
    """Run the expired-entry sweep on demand."""
    cache = _cache_or_503()
    removed = cache.cleanup_expired()
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Tenant self-view
# ---------------------------------------------------------------------------


@router.get("/api/v1/cache/stats")
def tenant_cache_stats(tenant: Tenant = Depends(require_tenant)):
    """Per-tenant cache stats for the authenticated caller."""
    cache = _cache_or_503()
    return cache.tenant_stats(tenant.id)
