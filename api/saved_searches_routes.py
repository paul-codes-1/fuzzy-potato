"""FastAPI routes for saved searches (per-tenant, authenticated)."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.saved_searches import (
    VALID_ALERT_FREQUENCIES,
    VALID_SEARCH_TYPES,
    execute_saved_search,
    get_saved_searches_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/saved-searches", tags=["saved-searches"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateSavedSearchRequest(BaseModel):
    name: str
    search_type: str
    query_text: str
    filters: Optional[dict] = None
    alert_enabled: bool = False
    alert_frequency: str = "off"

    @field_validator("search_type")
    @classmethod
    def validate_search_type(cls, v: str) -> str:
        if v not in VALID_SEARCH_TYPES:
            raise ValueError(
                f"Invalid search_type. Choose from: {', '.join(sorted(VALID_SEARCH_TYPES))}"
            )
        return v

    @field_validator("alert_frequency")
    @classmethod
    def validate_frequency(cls, v: str) -> str:
        if v not in VALID_ALERT_FREQUENCIES:
            raise ValueError(
                f"Invalid alert_frequency. Choose from: {', '.join(sorted(VALID_ALERT_FREQUENCIES))}"
            )
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 200:
            raise ValueError("name must be under 200 characters")
        return v

    @field_validator("query_text")
    @classmethod
    def validate_query_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query_text must not be empty")
        if len(v) > 4000:
            raise ValueError("query_text must be under 4000 characters")
        return v


class UpdateSavedSearchRequest(BaseModel):
    name: Optional[str] = None
    query_text: Optional[str] = None
    filters: Optional[dict] = None
    alert_enabled: Optional[bool] = None
    alert_frequency: Optional[str] = None

    @field_validator("alert_frequency")
    @classmethod
    def validate_frequency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in VALID_ALERT_FREQUENCIES:
            raise ValueError(
                f"Invalid alert_frequency. Choose from: {', '.join(sorted(VALID_ALERT_FREQUENCIES))}"
            )
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_user_id(request: Request, tenant: Tenant) -> str:
    """Resolve the "user" for a saved search.

    If an SSO JWT session is present, use the subject. Otherwise fall back
    to a tenant-scoped default user so API-key clients can still save
    searches.
    """
    sso_user = getattr(request.state, "sso_user", None) if request else None
    if sso_user:
        user_id = sso_user.get("sub") or sso_user.get("email")
        if user_id:
            return str(user_id)
    return f"{tenant.id}:api-key"


def _resolve_user_email(request: Request) -> Optional[str]:
    """Resolve the signed-in user's email when one is available."""
    sso_user = getattr(request.state, "sso_user", None) if request else None
    if not sso_user:
        return None
    email = (sso_user.get("email") or "").strip().lower()
    return email or None


def _summarize_alert_statuses(items: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    for item in items:
        status = item.get("last_alert_status") or "never_checked"
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "total_alerts": len(items),
        "deliverable": sum(1 for item in items if item.get("deliverable")),
        "due_now": sum(1 for item in items if item.get("due_now")),
        "status_counts": status_counts,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("")
async def create_saved_search(
    body: CreateSavedSearchRequest,
    request: Request,
    tenant: Tenant = Depends(require_tenant),
):
    """Create a new saved search for the current user."""
    manager = get_saved_searches_manager()
    try:
        saved = manager.create(
            tenant_id=tenant.id,
            user_id=_resolve_user_id(request, tenant),
            user_email=_resolve_user_email(request),
            name=body.name,
            search_type=body.search_type,
            query_text=body.query_text,
            filters=body.filters or {},
            alert_enabled=body.alert_enabled,
            alert_frequency=body.alert_frequency,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return saved.to_dict()


@router.get("")
async def list_saved_searches(
    request: Request,
    tenant: Tenant = Depends(require_tenant),
):
    """List saved searches for the current user within their tenant."""
    manager = get_saved_searches_manager()
    user_id = _resolve_user_id(request, tenant)
    items = manager.list_for_user(tenant.id, user_id)
    return {"saved_searches": [s.to_dict() for s in items]}


@router.get("/alerts/status")
async def get_saved_search_alert_status(
    tenant: Tenant = Depends(require_tenant),
):
    """Tenant-facing operational view of saved-search alert health."""
    manager = get_saved_searches_manager()
    items = manager.list_alert_statuses(tenant_id=tenant.id)
    return {
        "tenant_id": tenant.id,
        "summary": _summarize_alert_statuses(items),
        "saved_searches": items,
    }


@router.get("/{saved_search_id}")
async def get_saved_search(
    saved_search_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    manager = get_saved_searches_manager()
    saved = manager.get(tenant.id, saved_search_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return saved.to_dict()


@router.patch("/{saved_search_id}")
async def update_saved_search(
    saved_search_id: str,
    body: UpdateSavedSearchRequest,
    request: Request,
    tenant: Tenant = Depends(require_tenant),
):
    manager = get_saved_searches_manager()
    update_kwargs = {
        "name": body.name,
        "query_text": body.query_text,
        "filters": body.filters,
        "alert_enabled": body.alert_enabled,
        "alert_frequency": body.alert_frequency,
    }
    user_email = _resolve_user_email(request)
    if user_email:
        update_kwargs["user_email"] = user_email
    try:
        saved = manager.update(tenant_id=tenant.id, saved_search_id=saved_search_id, **update_kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not saved:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return saved.to_dict()


@router.delete("/{saved_search_id}")
async def delete_saved_search(
    saved_search_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    manager = get_saved_searches_manager()
    if not manager.delete(tenant.id, saved_search_id):
        raise HTTPException(status_code=404, detail="Saved search not found")
    return {"deleted": saved_search_id}


@router.post("/{saved_search_id}/run")
async def run_saved_search(
    saved_search_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Re-execute the saved search and return results.

    Delegates to the appropriate backend based on search_type:
      - meeting_search → api.search.get_search_engine().search(...)
      - vote_search    → api.tracker.get_policy_tracker().search_votes(...)
      - rag_ask        → api.query.ask(...)
      - rag_chat       → api.query.chat(...) seeded with the saved question
    """
    manager = get_saved_searches_manager()
    saved = manager.get(tenant.id, saved_search_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Saved search not found")

    try:
        result_type, results = execute_saved_search(saved)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Saved search run failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to run saved search")

    manager.record_run(tenant.id, saved_search_id)

    # Reload to get updated last_run_at
    refreshed = manager.get(tenant.id, saved_search_id)
    return {
        "saved_search": refreshed.to_dict() if refreshed else saved.to_dict(),
        "result_type": result_type,
        "results": results,
    }
