"""FastAPI routes for full-text search and autocomplete."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.auth import Tenant, require_tenant
from api.search import get_search_engine

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/search")
def search(
    q: str = Query("", description="Search query"),
    type: str = Query("all", description="Result type filter: all, meeting, vote, financial, speaker, topic, agenda_item"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    meeting_body: Optional[str] = Query(None, description="Filter by meeting body"),
    date_after: Optional[str] = Query(None, description="Filter: date >= value (YYYY-MM-DD)"),
    date_before: Optional[str] = Query(None, description="Filter: date <= value (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Full-text search across meetings, votes, financial items, speakers, and topics."""
    engine = get_search_engine()
    result = engine.search(
        query=q,
        type_filter=type,
        limit=limit,
        offset=offset,
        meeting_body=meeting_body,
        date_after=date_after,
        date_before=date_before,
        tenant_id=tenant.id if tenant.id != "dev" else None,
    )

    # Log the search for recent searches feature
    if q.strip():
        try:
            engine.log_search(q, tenant.id, result["total"])
        except Exception:
            logger.debug("Failed to log search", exc_info=True)

    return result


@router.get("/api/v1/search/autocomplete")
def autocomplete(
    q: str = Query("", description="Prefix to autocomplete"),
    limit: int = Query(10, ge=1, le=50, description="Max suggestions"),
    tenant: Tenant = Depends(require_tenant),
):
    """Fast autocomplete suggestions based on indexed terms."""
    engine = get_search_engine()
    suggestions = engine.autocomplete(prefix=q, limit=limit)
    return {"suggestions": suggestions, "query": q}


@router.get("/api/v1/search/recent")
def recent_searches(
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    tenant: Tenant = Depends(require_tenant),
):
    """Recent popular searches for this tenant."""
    engine = get_search_engine()
    return {"searches": engine.recent_searches(tenant_id=tenant.id, limit=limit)}


@router.get("/api/v1/search/stats")
def search_stats(
    tenant: Tenant = Depends(require_tenant),
):
    """Search index statistics."""
    engine = get_search_engine()
    return engine.stats()
