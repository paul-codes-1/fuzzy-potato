"""FastAPI routes for usage analytics."""


from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from api.analytics import get_analytics_store
from api.auth import Tenant, require_admin, require_tenant

router = APIRouter()


# ---------------------------------------------------------------------------
# Tenant-facing analytics
# ---------------------------------------------------------------------------

@router.get("/api/v1/analytics/usage")
def analytics_usage(
    period: str = Query("30d", pattern=r"^\d+[dwm]$"),
    tenant: Tenant = Depends(require_tenant),
):
    """Current period usage summary for the authenticated tenant."""
    store = get_analytics_store()
    return store.usage_summary(tenant.id, period)


@router.get("/api/v1/analytics/queries")
def analytics_queries(
    period: str = Query("7d", pattern=r"^\d+[dwm]$"),
    tenant: Tenant = Depends(require_tenant),
):
    """Query volume over time for the authenticated tenant."""
    store = get_analytics_store()
    return {
        "period": period,
        "data": store.queries_over_time(tenant.id, period),
    }


@router.get("/api/v1/analytics/popular-topics")
def analytics_popular_topics(
    period: str = Query("30d", pattern=r"^\d+[dwm]$"),
    limit: int = Query(10, ge=1, le=50),
    tenant: Tenant = Depends(require_tenant),
):
    """Most queried meeting bodies / topics for the authenticated tenant."""
    store = get_analytics_store()
    return {
        "period": period,
        "meeting_bodies": store.popular_meeting_bodies(tenant.id, period, limit),
        "top_questions": store.top_questions(tenant.id, period, limit),
    }


@router.get("/api/v1/analytics/recent")
def analytics_recent(
    limit: int = Query(50, ge=1, le=200),
    tenant: Tenant = Depends(require_tenant),
):
    """Recent queries for the authenticated tenant."""
    store = get_analytics_store()
    return {"queries": store.recent_queries(tenant.id, limit)}


@router.get("/api/v1/analytics/peak-hours")
def analytics_peak_hours(
    period: str = Query("30d", pattern=r"^\d+[dwm]$"),
    tenant: Tenant = Depends(require_tenant),
):
    """Peak usage hours (UTC) for the authenticated tenant."""
    store = get_analytics_store()
    return {
        "period": period,
        "hours": store.peak_usage_hours(tenant.id, period),
    }


@router.get("/api/v1/analytics/export/queries")
def analytics_export_queries(
    period: str = Query("30d", pattern=r"^\d+[dwm]$"),
    tenant: Tenant = Depends(require_tenant),
):
    """Export query events as CSV."""
    store = get_analytics_store()
    csv_data = store.export_queries_csv(tenant.id, period)
    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=queries_{tenant.id}_{period}.csv"},
    )


# ---------------------------------------------------------------------------
# Admin analytics (cross-tenant)
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/analytics/overview")
def admin_analytics_overview(
    period: str = Query("30d", pattern=r"^\d+[dwm]$"),
    _: bool = Depends(require_admin),
):
    """Cross-tenant analytics overview (admin only)."""
    store = get_analytics_store()
    return store.admin_overview(period)


@router.get("/api/v1/admin/analytics/revenue")
def admin_analytics_revenue(
    _: bool = Depends(require_admin),
):
    """Revenue and growth metrics (admin only)."""
    store = get_analytics_store()
    return store.admin_revenue_metrics()
