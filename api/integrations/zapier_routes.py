"""FastAPI routes for Zapier and Make (Integromat) integration.

Zapier-specific requirements:
- Authentication via X-API-Key header (standard Zapier pattern)
- All responses are flat JSON arrays or objects with `id` field
- Polling triggers return newest items first
- REST Hooks for instant triggers (subscribe/unsubscribe)
- GET for triggers (polling), POST for actions
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.integrations.zapier import (
    TRIGGER_TYPES,
    get_zapier_integration,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/zapier",
    tags=["zapier"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SubscribeRequest(BaseModel):
    """REST Hook subscribe request from Zapier."""

    trigger_type: str
    target_url: str
    config: dict = {}

    @field_validator("trigger_type")
    @classmethod
    def trigger_type_valid(cls, v: str) -> str:
        if v not in TRIGGER_TYPES:
            raise ValueError(
                f"Invalid trigger_type '{v}'. "
                f"Must be one of: {', '.join(sorted(TRIGGER_TYPES))}"
            )
        return v

    @field_validator("target_url")
    @classmethod
    def target_url_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_url must not be empty")
        if not v.startswith("https://"):
            raise ValueError("target_url must use HTTPS")
        return v


class AskActionRequest(BaseModel):
    """RAG query action request."""

    question: str
    meeting_body: str = ""
    date_after: str = ""
    date_before: str = ""

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        if len(v) > 2000:
            raise ValueError("question must be under 2000 characters")
        return v


class SearchVotesRequest(BaseModel):
    """Vote search action request."""

    member: str = ""
    date_after: str = ""
    date_before: str = ""
    outcome: str = ""
    keyword: str = ""
    limit: int = 20


class SearchFinancialRequest(BaseModel):
    """Financial search action request."""

    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    item_type: str = ""
    date_after: str = ""
    date_before: str = ""
    keyword: str = ""
    limit: int = 20


class ExportMeetingRequest(BaseModel):
    """Meeting export action request."""

    clip_id: str

    @field_validator("clip_id")
    @classmethod
    def clip_id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("clip_id must not be empty")
        return v


# ---------------------------------------------------------------------------
# Helper: get API/search dependencies lazily to avoid circular imports.
# ---------------------------------------------------------------------------


def _get_rag_dependencies():
    """Lazily import and return RAG singletons."""
    from api.server import _get_collection, _get_clip_metadata, _get_openai_client

    return _get_collection(), _get_clip_metadata(), _get_openai_client()


def _get_tracker():
    """Lazily get the PolicyTracker singleton."""
    from api.tracker import get_tracker

    return get_tracker()


# ---------------------------------------------------------------------------
# Polling triggers (GET endpoints -- Zapier polls these)
# ---------------------------------------------------------------------------


@router.get("/triggers/new_meeting")
def trigger_new_meeting(
    tenant: Tenant = Depends(require_tenant),
    limit: int = Query(50, ge=1, le=200),
):
    """Poll trigger: returns recently processed meetings, newest first.

    Zapier deduplicates by `id` field. Returns flat JSON array.
    """
    zapier = get_zapier_integration()
    items = zapier.poll_events(tenant.id, "new_meeting", limit=limit)

    # If no events logged yet, return sample data so Zapier can set up fields
    if not items:
        return [
            {
                "id": "sample_new_meeting",
                "clip_id": "0",
                "title": "Sample Meeting",
                "date": "2026-01-01",
                "meeting_body": "Council",
                "topics": "Budget, Zoning",
                "topic_count": 2,
                "vote_count": 3,
                "financial_item_count": 1,
                "public_comment_count": 2,
                "transcript_words": 5000,
                "url": "",
                "processed_at": "2026-01-01T12:00:00+00:00",
                "presiding_officer": "",
                "location": "",
                "members_present": 10,
                "members_absent": 2,
                "attendance_list": "",
            }
        ]

    return items


@router.get("/triggers/new_vote")
def trigger_new_vote(
    tenant: Tenant = Depends(require_tenant),
    limit: int = Query(50, ge=1, le=200),
):
    """Poll trigger: returns recently detected votes, newest first."""
    zapier = get_zapier_integration()
    items = zapier.poll_events(tenant.id, "new_vote", limit=limit)

    if not items:
        return [
            {
                "id": "sample_new_vote",
                "clip_id": "0",
                "meeting_date": "2026-01-01",
                "meeting_body": "Council",
                "meeting_title": "Sample Meeting",
                "identifier": "Ordinance 0001-26",
                "description": "Sample ordinance description",
                "motion_by": "Smith",
                "second_by": "Jones",
                "outcome": "passed",
                "vote_type": "roll_call",
                "ayes": 8,
                "nays": 2,
                "abstentions": 0,
                "votes_for": "Smith, Jones, Brown",
                "votes_against": "Davis, Wilson",
                "conditions": "",
                "transcript_time": "25:15",
            }
        ]

    return items


@router.get("/triggers/financial_alert")
def trigger_financial_alert(
    tenant: Tenant = Depends(require_tenant),
    threshold: Optional[float] = Query(
        None, description="Minimum dollar amount to trigger (e.g. 100000)"
    ),
    limit: int = Query(50, ge=1, le=200),
):
    """Poll trigger: returns financial items above threshold, newest first.

    If no threshold is set, returns all financial items.
    """
    zapier = get_zapier_integration()
    items = zapier.poll_events(tenant.id, "financial_alert", limit=limit)

    # Filter by threshold if provided
    if threshold is not None:
        threshold_cents = int(threshold * 100)
        items = [
            i for i in items
            if (i.get("amount_cents") or 0) >= threshold_cents
        ]

    if not items:
        return [
            {
                "id": "sample_financial_alert",
                "clip_id": "0",
                "meeting_date": "2026-01-01",
                "meeting_body": "Council",
                "meeting_title": "Sample Meeting",
                "description": "General Obligation Bonds",
                "amount": "$18,040,000",
                "amount_cents": 1804000000,
                "type": "appropriation",
                "identifier": "",
                "vendor_or_recipient": "",
            }
        ]

    return items


@router.get("/triggers/keyword_match")
def trigger_keyword_match(
    keyword: str = Query(..., description="Keyword to search for"),
    tenant: Tenant = Depends(require_tenant),
    limit: int = Query(50, ge=1, le=200),
):
    """Poll trigger: returns meetings where keyword appears in extracted facts.

    Searches agenda items, votes, and public comments.
    """
    if not keyword or not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword parameter is required")

    zapier = get_zapier_integration()
    items = zapier.search_keyword_in_facts(
        keyword=keyword.strip(), tenant_id=tenant.id, limit=limit,
    )

    if not items:
        return [
            {
                "id": "sample_keyword_match",
                "clip_id": "0",
                "keyword": keyword.strip(),
                "matched_section": "agenda_items",
                "matched_text": "Sample matched text containing the keyword",
                "meeting_date": "2026-01-01",
                "meeting_body": "Council",
                "meeting_title": "Sample Meeting",
            }
        ]

    return items


# ---------------------------------------------------------------------------
# Action endpoints (POST -- Zapier calls these to perform actions)
# ---------------------------------------------------------------------------


@router.post("/actions/ask")
def action_ask(
    request: AskActionRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Action: submit a RAG query about meetings.

    Returns a flat answer object with the question, answer, and source references.
    """
    zapier = get_zapier_integration()
    collection, clip_metadata, openai_client = _get_rag_dependencies()

    try:
        result = zapier.action_ask_question(
            question=request.question,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            tenant_id=tenant.id,
            meeting_body=request.meeting_body,
            date_after=request.date_after,
            date_before=request.date_before,
        )
        return result
    except Exception as e:
        logger.error("Zapier ask action failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to process question"
        )


@router.post("/actions/search_votes")
def action_search_votes(
    request: SearchVotesRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Action: search vote records with filters.

    Returns a flat array of vote objects.
    """
    zapier = get_zapier_integration()
    tracker = _get_tracker()

    try:
        items = zapier.action_search_votes(
            tracker=tracker,
            member=request.member,
            date_after=request.date_after,
            date_before=request.date_before,
            outcome=request.outcome,
            keyword=request.keyword,
            limit=request.limit,
        )
        return items
    except Exception as e:
        logger.error("Zapier search_votes action failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to search votes"
        )


@router.post("/actions/search_financial")
def action_search_financial(
    request: SearchFinancialRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Action: search financial items with filters.

    Returns a flat array of financial item objects.
    """
    zapier = get_zapier_integration()
    tracker = _get_tracker()

    try:
        items = zapier.action_search_financial(
            tracker=tracker,
            min_amount=request.min_amount,
            max_amount=request.max_amount,
            item_type=request.item_type,
            date_after=request.date_after,
            date_before=request.date_before,
            keyword=request.keyword,
            limit=request.limit,
        )
        return items
    except Exception as e:
        logger.error("Zapier search_financial action failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to search financial items"
        )


@router.post("/actions/export_meeting")
def action_export_meeting(
    request: ExportMeetingRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Action: export a specific meeting's full data.

    Returns a flat object with metadata, summary, transcript preview, and
    serialized JSON for votes, financial items, and public comments.
    """
    zapier = get_zapier_integration()

    try:
        result = zapier.action_export_meeting(clip_id=request.clip_id)
        if result.get("error"):
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Zapier export action failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to export meeting"
        )


# ---------------------------------------------------------------------------
# REST Hook endpoints (subscribe / unsubscribe for instant triggers)
# ---------------------------------------------------------------------------


@router.post("/hooks/subscribe")
def hook_subscribe(
    request: SubscribeRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Subscribe a REST Hook for instant trigger notifications.

    When Zapier sets up an instant trigger, it sends the target_url to
    receive POST payloads when the trigger fires. This endpoint registers
    that subscription.

    Returns the hook ID which Zapier uses to unsubscribe later.
    """
    zapier = get_zapier_integration()

    try:
        hook = zapier.subscribe(
            tenant_id=tenant.id,
            trigger_type=request.trigger_type,
            target_url=request.target_url,
            config=request.config,
        )
        return {"id": hook.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/hooks/{hook_id}")
def hook_unsubscribe(
    hook_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Unsubscribe a REST Hook when Zapier deactivates a Zap.

    Zapier calls this with the hook ID returned from subscribe.
    """
    zapier = get_zapier_integration()

    if not zapier.unsubscribe(hook_id, tenant.id):
        raise HTTPException(status_code=404, detail="Hook not found")

    return {"deleted": hook_id}


@router.get("/hooks")
def list_hooks(
    tenant: Tenant = Depends(require_tenant),
    trigger_type: Optional[str] = Query(None, description="Filter by trigger type"),
):
    """List active REST Hook subscriptions for the authenticated tenant."""
    zapier = get_zapier_integration()
    hooks = zapier.get_hooks(tenant.id, trigger_type=trigger_type)

    return {
        "hooks": [
            {
                "id": h.id,
                "trigger_type": h.trigger_type,
                "target_url": h.target_url,
                "config": h.config,
                "created_at": h.created_at,
            }
            for h in hooks
        ]
    }


# ---------------------------------------------------------------------------
# Auth test endpoint (Zapier calls this to validate the API key)
# ---------------------------------------------------------------------------


@router.get("/auth/test")
def auth_test(tenant: Tenant = Depends(require_tenant)):
    """Zapier calls this to verify the API key is valid during setup.

    Returns basic tenant info so the user sees their account in Zapier.
    """
    return {
        "id": tenant.id,
        "name": tenant.name,
        "plan": tenant.plan,
        "authenticated": True,
    }
