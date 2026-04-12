"""FastAPI routes for per-tenant cost attribution."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import Tenant, require_admin, require_tenant
from api.cost import MODEL_PRICING, get_cost_tracker

router = APIRouter()


def _parse_ts(value: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 date or unix timestamp into a float epoch seconds."""
    if value is None or value == "":
        return None
    # Allow raw unix timestamps (useful for tests and admin tools).
    try:
        return float(value)
    except ValueError:
        pass
    from datetime import datetime

    try:
        # Accept either YYYY-MM-DD or full ISO-8601
        if len(value) == 10:
            dt = datetime.strptime(value, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value} ({e})")


# ---------------------------------------------------------------------------
# Admin (cross-tenant) views
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/costs/summary")
def admin_costs_summary(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    _: bool = Depends(require_admin),
):
    """Total spend with breakdown by model and top 20 tenants."""
    tracker = get_cost_tracker()
    return tracker.get_admin_summary(_parse_ts(start), _parse_ts(end))


@router.get("/api/v1/admin/costs/tenant/{tenant_id}")
def admin_costs_tenant(
    tenant_id: str,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    group_by: str = Query("day", pattern=r"^(day|model|operation)$"),
    _: bool = Depends(require_admin),
):
    """Per-tenant cost view (admin)."""
    tracker = get_cost_tracker()
    return tracker.get_tenant_costs(
        tenant_id,
        _parse_ts(start),
        _parse_ts(end),
        group_by=group_by,
    )


@router.get("/api/v1/admin/costs/estimate/{tenant_id}")
def admin_costs_estimate(
    tenant_id: str,
    _: bool = Depends(require_admin),
):
    """30-day projection from the last 7 days of usage."""
    tracker = get_cost_tracker()
    return tracker.estimate_monthly_cost(tenant_id)


@router.get("/api/v1/admin/costs/top-tenants")
def admin_top_tenants(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    _: bool = Depends(require_admin),
):
    tracker = get_cost_tracker()
    return {
        "tenants": tracker.top_tenants_by_cost(
            _parse_ts(start),
            _parse_ts(end),
            limit=limit,
        )
    }


@router.get("/api/v1/admin/costs/pricing")
def admin_pricing_table(_: bool = Depends(require_admin)):
    """Return the active pricing table so Finance can verify rates."""
    return {"model_pricing": MODEL_PRICING}


# ---------------------------------------------------------------------------
# Tenant self-view
# ---------------------------------------------------------------------------

@router.get("/api/v1/costs/me")
def costs_me(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    group_by: str = Query("day", pattern=r"^(day|model|operation)$"),
    tenant: Tenant = Depends(require_tenant),
):
    """Authenticated tenant sees their own cost breakdown."""
    tracker = get_cost_tracker()
    return tracker.get_tenant_costs(
        tenant.id,
        _parse_ts(start),
        _parse_ts(end),
        group_by=group_by,
    )


@router.get("/api/v1/costs/me/estimate")
def costs_me_estimate(tenant: Tenant = Depends(require_tenant)):
    tracker = get_cost_tracker()
    return tracker.estimate_monthly_cost(tenant.id)
