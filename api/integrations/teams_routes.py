"""FastAPI routes for Microsoft Teams integration (outgoing webhooks, config).

Teams outgoing webhooks:
- Send HMAC-SHA256 signed requests to a configured URL
- Expect a JSON response with an Adaptive Card attachment
- No OAuth flow needed (simpler than Slack)

Incoming webhooks:
- CivicLens POSTs Adaptive Cards to a Teams channel webhook URL
- Configured per-tenant via PUT /config
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.auth import Tenant, require_tenant
from api.integrations.teams import (
    TeamsBot,
    TeamsConfig,
    build_help_card,
    format_meeting_summary_card,
    format_qa_response,
    format_search_results,
    get_teams_config_store,
    parse_command,
    parse_teams_mention,
    verify_teams_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations/teams", tags=["teams"])


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _webhook_secret() -> str:
    return os.environ.get("TEAMS_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# Shared: HMAC verification dependency
# ---------------------------------------------------------------------------


async def _verify_teams_request(request: Request) -> bytes:
    """FastAPI dependency that verifies the Teams outgoing webhook HMAC.

    Returns the raw request body on success; raises 401 on failure.
    """
    secret = _webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Teams webhook secret not configured")

    body = await request.body()
    signature = request.headers.get("Authorization", "")

    if not verify_teams_signature(secret, body, signature):
        raise HTTPException(status_code=401, detail="Invalid Teams signature")

    return body


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------


def _get_rag_dependencies():
    """Lazily import and return RAG singletons to avoid circular imports."""
    from api.server import _get_collection, _get_clip_metadata, _get_openai_client

    return _get_collection(), _get_clip_metadata(), _get_openai_client()


def _process_ask_and_respond(
    webhook_url: str,
    question: str,
    tenant_id: str = "",
):
    """Background task: run RAG query and post result to Teams via incoming webhook."""
    try:
        from api.query import ask

        collection, clip_metadata, openai_client = _get_rag_dependencies()

        filters = {}
        if tenant_id and tenant_id != "dev":
            filters["tenant_id"] = tenant_id

        result = ask(
            question=question,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            filters=filters if filters else None,
        )

        bot = TeamsBot(webhook_url)
        bot.post_qa_response(
            question=question,
            answer=result.get("answer", "No answer available."),
            sources=result.get("sources", []),
            filters_applied=result.get("filters_applied"),
        )

    except Exception:
        logger.exception("Failed to process Teams ask command in background")


# ---------------------------------------------------------------------------
# Outgoing webhook endpoint (handles @CivicLens mentions)
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def handle_outgoing_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Handle Teams outgoing webhook requests.

    Teams sends a JSON payload when someone @mentions the bot.
    We verify HMAC, parse the command, and return an Adaptive Card response.

    For quick commands (help, status), we respond inline.
    For long-running queries (ask, search), we respond with an acknowledgement
    and optionally post a follow-up via incoming webhook.
    """
    body = await _verify_teams_request(request)
    payload = json.loads(body)

    # Extract text from the Teams message
    raw_text = payload.get("text", "")
    text = parse_teams_mention(raw_text)
    subcommand, argument = parse_command(text)

    # Try to identify the tenant from the Teams tenant ID in the payload
    from_tenant_id = payload.get("channelData", {}).get("tenant", {}).get("id", "")
    store = get_teams_config_store()
    config = None
    if from_tenant_id:
        config = store.get_by_teams_tenant_id(from_tenant_id)

    if subcommand == "help":
        card = build_help_card()
        return Response(
            content=json.dumps({
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }],
            }),
            media_type="application/json",
        )

    if subcommand == "status":
        status_text = "CivicLens is connected."
        if config:
            status_text += (
                f"\nChannel: {config.channel_name or 'Not configured'}"
                f"\nNew meeting alerts: {'On' if config.notify_new_meetings else 'Off'}"
                f"\nVote alerts: {'On' if config.notify_votes else 'Off'}"
            )
        else:
            status_text += "\nNo workspace configuration found."

        card = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": [{"type": "TextBlock", "text": status_text, "wrap": True}],
        }
        return Response(
            content=json.dumps({
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }],
            }),
            media_type="application/json",
        )

    if subcommand == "ask":
        if not argument:
            card = {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "body": [{
                    "type": "TextBlock",
                    "text": "Please provide a question. Example: @CivicLens ask What has the city done about parks?",
                    "wrap": True,
                }],
            }
            return Response(
                content=json.dumps({
                    "type": "message",
                    "attachments": [{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": card,
                    }],
                }),
                media_type="application/json",
            )

        # For ask: respond inline with a quick RAG query
        # Teams outgoing webhooks allow up to 10 seconds for a response
        try:
            from api.query import ask as rag_ask

            collection, clip_metadata, openai_client = _get_rag_dependencies()

            filters = {}
            if config and config.tenant_id != "dev":
                filters["tenant_id"] = config.tenant_id

            result = rag_ask(
                question=argument,
                collection=collection,
                openai_client=openai_client,
                clip_metadata=clip_metadata,
                filters=filters if filters else None,
            )

            card = format_qa_response(
                question=argument,
                answer=result.get("answer", "No answer available."),
                sources=result.get("sources", []),
                filters_applied=result.get("filters_applied"),
            )
        except Exception:
            logger.exception("Failed to process Teams ask inline")
            card = {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "body": [{
                    "type": "TextBlock",
                    "text": "Sorry, an error occurred processing your question. Please try again.",
                    "wrap": True,
                }],
            }

        return Response(
            content=json.dumps({
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }],
            }),
            media_type="application/json",
        )

    if subcommand == "search":
        if not argument:
            card = {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "body": [{
                    "type": "TextBlock",
                    "text": "Please provide a search term. Example: @CivicLens search zoning",
                    "wrap": True,
                }],
            }
            return Response(
                content=json.dumps({
                    "type": "message",
                    "attachments": [{
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": card,
                    }],
                }),
                media_type="application/json",
            )

        try:
            from api.server import _get_clip_metadata

            clip_metadata = _get_clip_metadata()
            query_lower = argument.lower()
            results = []
            for clip_id, meta in clip_metadata.items():
                title = meta.get("title", "").lower()
                topics = [t.lower() for t in meta.get("topics", [])]
                body_name = meta.get("meeting_body", "").lower()

                if (
                    query_lower in title
                    or query_lower in body_name
                    or any(query_lower in t for t in topics)
                ):
                    results.append({
                        "clip_id": clip_id,
                        "title": meta.get("title", "Unknown"),
                        "date": meta.get("date", ""),
                        "meeting_body": meta.get("meeting_body", ""),
                        "topics": meta.get("topics", []),
                        "url": meta.get("url", ""),
                    })

            results.sort(key=lambda r: r.get("date", ""), reverse=True)
            card = format_search_results(query=argument, results=results[:10])
        except Exception:
            logger.exception("Failed to process Teams search")
            card = {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "body": [{
                    "type": "TextBlock",
                    "text": "Sorry, an error occurred processing your search. Please try again.",
                    "wrap": True,
                }],
            }

        return Response(
            content=json.dumps({
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }],
            }),
            media_type="application/json",
        )

    # Fallback
    card = build_help_card()
    return Response(
        content=json.dumps({
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }],
        }),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tenant config endpoints
# ---------------------------------------------------------------------------


class TeamsConfigUpdate(BaseModel):
    webhook_url: Optional[str] = None
    teams_tenant_id: Optional[str] = None
    channel_name: Optional[str] = None
    notify_new_meetings: Optional[bool] = None
    notify_votes: Optional[bool] = None
    notify_financial: Optional[bool] = None


@router.put("/config")
async def update_teams_config(
    request: TeamsConfigUpdate,
    tenant: Tenant = Depends(require_tenant),
):
    """Save or update Teams configuration for the authenticated tenant.

    On first call, creates the Teams integration record.
    On subsequent calls, updates only the provided fields.
    """
    store = get_teams_config_store()
    existing = store.get(tenant.id)

    if existing:
        # Partial update
        updated = store.update_notifications(
            tenant_id=tenant.id,
            webhook_url=request.webhook_url,
            channel_name=request.channel_name,
            notify_new_meetings=request.notify_new_meetings,
            notify_votes=request.notify_votes,
            notify_financial=request.notify_financial,
        )
        if request.teams_tenant_id is not None and updated:
            updated.teams_tenant_id = request.teams_tenant_id
            store.save(updated)

        config = updated or existing
    else:
        # First-time setup -- require webhook_url
        if not request.webhook_url:
            raise HTTPException(
                status_code=400,
                detail="webhook_url is required for initial Teams setup.",
            )

        config = TeamsConfig(
            tenant_id=tenant.id,
            teams_tenant_id=request.teams_tenant_id or "",
            webhook_url=request.webhook_url,
            channel_name=request.channel_name or "",
            notify_new_meetings=request.notify_new_meetings if request.notify_new_meetings is not None else True,
            notify_votes=request.notify_votes if request.notify_votes is not None else True,
            notify_financial=request.notify_financial if request.notify_financial is not None else False,
            installed_at=datetime.now(timezone.utc).isoformat(),
            installed_by="",
        )
        store.save(config)

    return {
        "tenant_id": config.tenant_id,
        "teams_tenant_id": config.teams_tenant_id,
        "webhook_url": config.webhook_url,
        "channel_name": config.channel_name,
        "notify_new_meetings": config.notify_new_meetings,
        "notify_votes": config.notify_votes,
        "notify_financial": config.notify_financial,
    }


@router.get("/config")
async def get_teams_config(tenant: Tenant = Depends(require_tenant)):
    """Get current Teams configuration for the authenticated tenant."""
    store = get_teams_config_store()
    config = store.get(tenant.id)
    if not config:
        raise HTTPException(
            status_code=404,
            detail="No Teams integration found. Configure it via PUT /config first.",
        )

    return {
        "tenant_id": config.tenant_id,
        "teams_tenant_id": config.teams_tenant_id,
        "webhook_url": config.webhook_url,
        "channel_name": config.channel_name,
        "notify_new_meetings": config.notify_new_meetings,
        "notify_votes": config.notify_votes,
        "notify_financial": config.notify_financial,
        "installed_at": config.installed_at,
    }


@router.post("/test")
async def test_teams_webhook(tenant: Tenant = Depends(require_tenant)):
    """Send a test Adaptive Card to the configured Teams channel.

    Verifies that the incoming webhook URL works correctly.
    """
    store = get_teams_config_store()
    config = store.get(tenant.id)
    if not config:
        raise HTTPException(
            status_code=404,
            detail="No Teams integration found. Configure it via PUT /config first.",
        )

    if not config.webhook_url:
        raise HTTPException(
            status_code=400,
            detail="No webhook URL configured.",
        )

    card = format_meeting_summary_card(
        clip_id="test-001",
        title="Test Meeting - CivicLens Integration",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        meeting_body="Test Body",
        topics=["Integration Test", "Teams Setup"],
        summary_text=(
            "This is a test message from CivicLens. "
            "If you can see this Adaptive Card, your Teams integration is working correctly."
        ),
        granicus_url="",
        votes_count=3,
        financial_items_count=1,
    )

    bot = TeamsBot(config.webhook_url)
    success = bot.post_card(card, summary_text="CivicLens test message")

    if not success:
        raise HTTPException(
            status_code=502,
            detail="Failed to send test message to Teams. Check your webhook URL.",
        )

    return {
        "success": True,
        "message": "Test message sent to Teams channel successfully.",
    }
