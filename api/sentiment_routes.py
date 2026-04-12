"""FastAPI routes for public comment sentiment analysis."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.auth import Tenant, require_tenant
from api.sentiment import get_analyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/topics")
def sentiment_topics(
    limit: int = Query(20, ge=1, le=100, description="Maximum topics to return"),
    meeting_body: Optional[str] = Query(None, description="Filter by meeting body"),
    date_after: Optional[str] = Query(None, description="Only comments on or after this date (YYYY-MM-DD)"),
    date_before: Optional[str] = Query(None, description="Only comments on or before this date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Top topics by comment volume with aggregated sentiment scores.

    Returns topics sorted by comment count, each with positive/negative/neutral
    breakdowns and an average sentiment score (-1 to +1).
    """
    analyzer = get_analyzer()
    topics = analyzer.get_topics(
        limit=limit,
        meeting_body=meeting_body or "",
        date_after=date_after or "",
        date_before=date_before or "",
    )
    return {"topics": topics, "tenant": tenant.id}


@router.get("/topics/{topic}")
def sentiment_topic_trend(
    topic: str,
    date_after: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_before: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Sentiment trend for a specific topic over time.

    Returns a time series of sentiment data points for the given topic,
    ordered chronologically. Each data point includes the average sentiment
    score and per-category comment counts for that date.
    """
    analyzer = get_analyzer()
    trend = analyzer.get_topic_trend(
        topic=topic,
        date_after=date_after or "",
        date_before=date_before or "",
    )
    return {"topic": topic, "trend": trend, "tenant": tenant.id}


@router.get("/comments")
def sentiment_comments(
    clip_id: Optional[str] = Query(None, description="Filter by clip ID"),
    topic: Optional[str] = Query(None, description="Filter by normalized topic"),
    speaker: Optional[str] = Query(None, description="Filter by speaker name (partial match)"),
    sentiment: Optional[str] = Query(None, description="Filter by sentiment value (positive/negative/neutral/mixed)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum comments to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    tenant: Tenant = Depends(require_tenant),
):
    """Analyzed public comments with optional filters.

    Returns individual comment sentiment classifications. Supports filtering
    by clip, topic, speaker, or sentiment category with pagination.
    """
    analyzer = get_analyzer()
    comments = analyzer.get_comments(
        clip_id=clip_id or "",
        topic=topic or "",
        speaker=speaker or "",
        sentiment=sentiment or "",
        limit=limit,
        offset=offset,
    )
    return {"comments": comments, "count": len(comments), "tenant": tenant.id}


@router.get("/controversial")
def sentiment_controversial(
    limit: int = Query(10, ge=1, le=50, description="Maximum topics to return"),
    min_comments: int = Query(2, ge=1, le=20, description="Minimum comments for a topic to qualify"),
    meeting_body: Optional[str] = Query(None, description="Filter by meeting body"),
    date_after: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_before: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Most divisive topics where public sentiment is split.

    Controversy is scored by how evenly split positive and negative comments
    are, weighted by total volume. High-controversy topics have many comments
    with roughly balanced for/against sentiment.
    """
    analyzer = get_analyzer()
    controversial = analyzer.get_controversial(
        limit=limit,
        min_comments=min_comments,
        meeting_body=meeting_body or "",
        date_after=date_after or "",
        date_before=date_before or "",
    )
    return {"controversial": controversial, "tenant": tenant.id}


@router.get("/speakers")
def sentiment_speakers(
    limit: int = Query(20, ge=1, le=100, description="Maximum speakers to return"),
    meeting_body: Optional[str] = Query(None, description="Filter by meeting body"),
    date_after: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_before: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Most active public commenters with their topics and sentiment profile.

    Returns speakers ranked by comment frequency, with the distinct topics
    they have commented on and their overall sentiment tendency.
    """
    analyzer = get_analyzer()
    speakers = analyzer.get_speakers(
        limit=limit,
        meeting_body=meeting_body or "",
        date_after=date_after or "",
        date_before=date_before or "",
    )
    return {"speakers": speakers, "tenant": tenant.id}


@router.get("/dashboard")
def sentiment_dashboard(
    meeting_body: Optional[str] = Query(None, description="Filter by meeting body"),
    date_after: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_before: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Overall sentiment dashboard summary.

    Returns aggregate statistics including total comments analyzed,
    sentiment distribution, trending topics, hot issues (topics with
    strongly negative sentiment), and meeting body breakdown.
    """
    analyzer = get_analyzer()
    dashboard = analyzer.get_dashboard(
        meeting_body=meeting_body or "",
        date_after=date_after or "",
        date_before=date_before or "",
    )
    dashboard["tenant"] = tenant.id
    return dashboard
