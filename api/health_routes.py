"""FastAPI routes for customer health scoring and success dashboard."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_admin
from api.customer_health import get_health_scorer

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/v1/admin/health/scores - All tenant health scores
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/health/scores")
def health_scores_all(_: bool = Depends(require_admin)):
    """Return health scores for all tenants with trends."""
    scorer = get_health_scorer()
    scores = scorer.score_all_tenants()
    return {
        "scores": [s.to_dict() for s in scores],
        "total": len(scores),
        "healthy": sum(1 for s in scores if s.category == "healthy"),
        "at_risk": sum(1 for s in scores if s.category == "at_risk"),
        "churning": sum(1 for s in scores if s.category == "churning"),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/health/scores/{tenant_id} - Detailed score breakdown
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/health/scores/{tenant_id}")
def health_score_detail(tenant_id: str, _: bool = Depends(require_admin)):
    """Return detailed health score breakdown for a single tenant."""
    from api.auth import get_tenant_store

    store = get_tenant_store()
    tenant = store.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    scorer = get_health_scorer()
    hs = scorer.score_tenant(tenant_id)

    return {
        **hs.to_dict(),
        "tenant_name": tenant.name,
        "plan": tenant.plan,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/health/at-risk - Tenants below threshold
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/health/at-risk")
def health_at_risk(
    threshold: int = Query(50, ge=0, le=100),
    _: bool = Depends(require_admin),
):
    """Return tenants with health scores below the threshold."""
    scorer = get_health_scorer()
    at_risk = scorer.get_at_risk_tenants(threshold)
    return {
        "threshold": threshold,
        "tenants": [s.to_dict() for s in at_risk],
        "count": len(at_risk),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/health/expansion - Expansion opportunities
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/health/expansion")
def health_expansion(_: bool = Depends(require_admin)):
    """Return tenants showing expansion signals."""
    scorer = get_health_scorer()
    opportunities = scorer.get_expansion_opportunities()
    return {
        "tenants": [s.to_dict() for s in opportunities],
        "count": len(opportunities),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/health/trends - Health trends over time
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/health/trends")
def health_trends(
    tenant_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    _: bool = Depends(require_admin),
):
    """Return health score trend data for visualization."""
    scorer = get_health_scorer()
    trends = scorer.get_trends(tenant_id=tenant_id, days=days)
    return {
        "tenant_id": tenant_id,
        "days": days,
        "data": trends,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/health/refresh - Recalculate all scores
# ---------------------------------------------------------------------------

@router.post("/api/v1/admin/health/refresh")
def health_refresh(_: bool = Depends(require_admin)):
    """Recalculate and persist health scores for all tenants."""
    scorer = get_health_scorer()
    scores = scorer.score_all_tenants()
    return {
        "refreshed": len(scores),
        "scores": [s.to_dict() for s in scores],
        "summary": {
            "healthy": sum(1 for s in scores if s.category == "healthy"),
            "at_risk": sum(1 for s in scores if s.category == "at_risk"),
            "churning": sum(1 for s in scores if s.category == "churning"),
        },
    }
