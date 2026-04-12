"""FastAPI admin routes for feature flag management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_admin, require_tenant
from api.feature_flags import (
    PLAN_HIERARCHY,
    get_feature_flag_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feature-flags"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class FlagUpdateRequest(BaseModel):
    description: Optional[str] = None
    default_enabled: Optional[bool] = None
    plan_minimum: Optional[str] = None
    rollout_percentage: Optional[int] = None

    @field_validator("plan_minimum")
    @classmethod
    def validate_plan(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in PLAN_HIERARCHY:
            raise ValueError(
                f"Invalid plan_minimum '{v}'. Choose from: {', '.join(PLAN_HIERARCHY)}"
            )
        return v

    @field_validator("rollout_percentage")
    @classmethod
    def validate_percentage(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("rollout_percentage must be between 0 and 100")
        return v


class OverrideRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@router.get("/api/v1/flags")
def list_flags(_admin: bool = Depends(require_admin)):
    """List all feature flags with their current configuration."""
    manager = get_feature_flag_manager()
    flags = manager.list_flags()
    result = []
    for flag in flags:
        d = flag.to_dict()
        d["overrides"] = manager.get_overrides(flag.name)
        result.append(d)
    return {"flags": result}


@router.get("/api/v1/flags/{tenant_id}")
def flags_for_tenant(
    tenant_id: str,
    _admin: bool = Depends(require_admin),
):
    """Get all flags evaluated for a specific tenant (shows which are enabled)."""
    from api.auth import get_tenant_store

    store = get_tenant_store()
    tenant = store.get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")

    manager = get_feature_flag_manager()
    evaluated = manager.flags_for_tenant(tenant_id, tenant.plan)

    # Return both the evaluated state and the flag details
    flags = manager.list_flags()
    flag_map = {f.name: f for f in flags}

    result = []
    for name, enabled in sorted(evaluated.items()):
        flag = flag_map.get(name)
        entry = {
            "name": name,
            "enabled": enabled,
            "plan_minimum": flag.plan_minimum if flag else "starter",
            "rollout_percentage": flag.rollout_percentage if flag else 100,
            "tenant_plan": tenant.plan,
        }
        # Note if there's a per-tenant override active
        overrides = manager.get_overrides(name)
        if tenant_id in overrides:
            entry["override"] = overrides[tenant_id]
        result.append(entry)

    return {"tenant_id": tenant_id, "plan": tenant.plan, "flags": result}


@router.put("/api/v1/flags/{flag_name}")
def update_flag(
    flag_name: str,
    request: FlagUpdateRequest,
    _admin: bool = Depends(require_admin),
):
    """Update a feature flag's configuration (rollout %, default, plan gate)."""
    manager = get_feature_flag_manager()

    try:
        updated = manager.update_flag(
            flag_name,
            description=request.description,
            default_enabled=request.default_enabled,
            plan_minimum=request.plan_minimum,
            rollout_percentage=request.rollout_percentage,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if updated is None:
        raise HTTPException(
            status_code=404, detail=f"Feature flag '{flag_name}' not found."
        )

    d = updated.to_dict()
    d["overrides"] = manager.get_overrides(flag_name)
    return d


@router.put("/api/v1/flags/{flag_name}/override/{tenant_id}")
def set_tenant_override(
    flag_name: str,
    tenant_id: str,
    request: OverrideRequest,
    _admin: bool = Depends(require_admin),
):
    """Set a per-tenant override for a feature flag (force enable/disable)."""
    from api.auth import get_tenant_store

    # Verify tenant exists
    store = get_tenant_store()
    tenant = store.get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")

    manager = get_feature_flag_manager()
    success = manager.set_override(flag_name, tenant_id, request.enabled)
    if not success:
        raise HTTPException(
            status_code=404, detail=f"Feature flag '{flag_name}' not found."
        )

    return {
        "flag_name": flag_name,
        "tenant_id": tenant_id,
        "enabled": request.enabled,
        "message": f"Override set: '{flag_name}' is now "
        f"{'enabled' if request.enabled else 'disabled'} for tenant '{tenant_id}'.",
    }


@router.delete("/api/v1/flags/{flag_name}/override/{tenant_id}")
def remove_tenant_override(
    flag_name: str,
    tenant_id: str,
    _admin: bool = Depends(require_admin),
):
    """Remove a per-tenant override (revert to default evaluation)."""
    manager = get_feature_flag_manager()
    removed = manager.remove_override(flag_name, tenant_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"No override found for flag '{flag_name}' on tenant '{tenant_id}'.",
        )
    return {
        "flag_name": flag_name,
        "tenant_id": tenant_id,
        "message": "Override removed. Flag will use default evaluation rules.",
    }


# ---------------------------------------------------------------------------
# Tenant-facing route (non-admin): see own flags
# ---------------------------------------------------------------------------

@router.get("/api/v1/my-flags")
def my_flags(tenant: Tenant = Depends(require_tenant)):
    """Get feature flags for the currently authenticated tenant."""
    manager = get_feature_flag_manager()
    evaluated = manager.flags_for_tenant(tenant.id, tenant.plan)
    return {
        "tenant_id": tenant.id,
        "plan": tenant.plan,
        "flags": evaluated,
    }
