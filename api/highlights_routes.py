"""FastAPI routes for meeting highlights - key moments from council meetings."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import Tenant, require_tenant
from api.highlights import HIGHLIGHT_TYPES, get_highlights_store

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/v1/highlights/meeting/{clip_id}
# ---------------------------------------------------------------------------

@router.get("/api/v1/highlights/meeting/{clip_id}")
def get_meeting_highlights(
    clip_id: int,
    top: Optional[int] = Query(None, ge=1, le=50, description="Return only top N highlights"),
    tenant: Tenant = Depends(require_tenant),
):
    """Get highlights for a specific meeting clip.

    If ?top=5 is passed, returns only the top 5 moments.
    Otherwise returns all highlights for the meeting.
    """
    store = get_highlights_store()

    if top:
        highlights = store.get_top_for_clip(clip_id, top_n=top)
    else:
        highlights = store.get_for_clip(clip_id)

    return {
        "clip_id": clip_id,
        "count": len(highlights),
        "highlights": [h.to_dict() for h in highlights],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/highlights/recent
# ---------------------------------------------------------------------------

@router.get("/api/v1/highlights/recent")
def get_recent_highlights(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(require_tenant),
):
    """Top highlights across recent meetings, sorted by date then importance."""
    store = get_highlights_store()
    highlights = store.get_recent(limit=limit, offset=offset)

    return {
        "count": len(highlights),
        "limit": limit,
        "offset": offset,
        "highlights": [h.to_dict() for h in highlights],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/highlights/trending
# ---------------------------------------------------------------------------

@router.get("/api/v1/highlights/trending")
def get_trending_highlights(
    days: int = Query(30, ge=1, le=365, description="Look back N days"),
    limit: int = Query(20, ge=1, le=100),
    tenant: Tenant = Depends(require_tenant),
):
    """Most important moments from the last N days."""
    store = get_highlights_store()
    highlights = store.get_trending(days=days, limit=limit)

    return {
        "days": days,
        "count": len(highlights),
        "highlights": [h.to_dict() for h in highlights],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/highlights/type/{type}
# ---------------------------------------------------------------------------

@router.get("/api/v1/highlights/type/{highlight_type}")
def get_highlights_by_type(
    highlight_type: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(require_tenant),
):
    """Filter highlights by type (contentious_vote, financial, public_comment, etc.)."""
    if highlight_type not in HIGHLIGHT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type. Choose from: {', '.join(sorted(HIGHLIGHT_TYPES))}",
        )

    store = get_highlights_store()
    highlights = store.get_by_type(highlight_type, limit=limit, offset=offset)

    return {
        "type": highlight_type,
        "count": len(highlights),
        "highlights": [h.to_dict() for h in highlights],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/highlights/types
# ---------------------------------------------------------------------------

@router.get("/api/v1/highlights/types")
def get_highlight_types(tenant: Tenant = Depends(require_tenant)):
    """List available highlight types with counts."""
    store = get_highlights_store()
    counts = store.count_by_type()

    return {
        "types": sorted(HIGHLIGHT_TYPES),
        "counts": counts,
    }
