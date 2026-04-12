"""FastAPI routes for meeting-over-meeting diff and change tracking."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import Tenant, require_tenant
from api.diff import get_diff_engine, MeetingDiffEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/diff", tags=["diff"])


def _get_engine() -> MeetingDiffEngine:
    try:
        return get_diff_engine()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Diff engine not initialized.")


# ---------------------------------------------------------------------------
# GET /api/v1/diff/meeting/{clip_id}
# Changes from the previous meeting of the same body
# ---------------------------------------------------------------------------

@router.get("/meeting/{clip_id}")
def get_meeting_diff(
    clip_id: int,
    tenant: Tenant = Depends(require_tenant),
):
    """Get changes from the previous meeting of the same body."""
    engine = _get_engine()

    # Check if we already have a stored diff for this clip
    diff = engine.store.get_by_clip_after(clip_id)

    if diff is None:
        # Try to compute on the fly
        diff = engine.auto_diff_for_clip(clip_id)
        if diff is None:
            raise HTTPException(
                status_code=404,
                detail="No previous meeting found for comparison, or meeting not processed.",
            )

    return {
        "diff": diff.to_dict(),
        "tenant": tenant.id,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/diff/compare/{clip_id_a}/{clip_id_b}
# Compare any two meetings
# ---------------------------------------------------------------------------

@router.get("/compare/{clip_id_a}/{clip_id_b}")
def compare_meetings(
    clip_id_a: int,
    clip_id_b: int,
    tenant: Tenant = Depends(require_tenant),
):
    """Compare any two meetings side-by-side."""
    engine = _get_engine()

    # Check stored first
    diff = engine.store.get_by_pair(clip_id_a, clip_id_b)

    if diff is None:
        # Validate both clips exist
        meta_a = engine.load_metadata(clip_id_a)
        meta_b = engine.load_metadata(clip_id_b)

        if not meta_a:
            raise HTTPException(status_code=404, detail=f"Clip {clip_id_a} not found.")
        if not meta_b:
            raise HTTPException(status_code=404, detail=f"Clip {clip_id_b} not found.")

        diff = engine.compute_and_store(clip_id_a, clip_id_b)

    return {
        "diff": diff.to_dict(),
        "tenant": tenant.id,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/diff/whats-new
# Latest changes across all meeting bodies
# ---------------------------------------------------------------------------

@router.get("/whats-new")
def whats_new(
    limit: int = Query(default=10, ge=1, le=50),
    tenant: Tenant = Depends(require_tenant),
):
    """Get the latest diffs across all meeting bodies."""
    engine = _get_engine()
    diffs = engine.store.get_latest(limit=limit)

    return {
        "diffs": [d.to_dict() for d in diffs],
        "count": len(diffs),
        "tenant": tenant.id,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/diff/body/{meeting_body}/timeline
# Change timeline for a specific meeting body
# ---------------------------------------------------------------------------

@router.get("/body/{meeting_body}/timeline")
def body_timeline(
    meeting_body: str,
    limit: int = Query(default=20, ge=1, le=100),
    tenant: Tenant = Depends(require_tenant),
):
    """Get the change timeline for a specific meeting body."""
    engine = _get_engine()
    diffs = engine.store.get_body_timeline(meeting_body, limit=limit)

    if not diffs:
        raise HTTPException(
            status_code=404,
            detail=f"No diffs found for meeting body '{meeting_body}'.",
        )

    return {
        "meeting_body": meeting_body,
        "diffs": [d.to_dict() for d in diffs],
        "count": len(diffs),
        "tenant": tenant.id,
    }
