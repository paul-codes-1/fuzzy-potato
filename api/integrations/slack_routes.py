"""FastAPI routes for Slack integration (slash commands, events, OAuth, config).

Slack requires:
- 3-second response to slash commands; long-running queries use background tasks
- Signing secret verification on every inbound request
- Events API challenge/response for URL verification
- OAuth 2.0 flow for workspace installation
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.auth import Tenant, require_tenant
from api.integrations.slack import (
    SlackBot,
    SlackConfig,
    build_help_blocks,
    get_slack_config_store,
    parse_slash_command,
    verify_slack_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations/slack", tags=["slack"])


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "")


def _client_id() -> str:
    return os.environ.get("SLACK_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("SLACK_CLIENT_SECRET", "")


# ---------------------------------------------------------------------------
# Shared: signature verification dependency
# ---------------------------------------------------------------------------


async def _verify_slack_request(request: Request) -> bytes:
    """FastAPI dependency that verifies the Slack signing secret.

    Returns the raw request body on success; raises 401 on failure.
    """
    secret = _signing_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Slack signing secret not configured")

    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(secret, timestamp, body, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    return body


# ---------------------------------------------------------------------------
# Background task helpers (for async processing beyond 3s)
# ---------------------------------------------------------------------------


def _get_rag_dependencies():
    """Lazily import and return RAG singletons to avoid circular imports."""
    from api.server import _get_collection, _get_clip_metadata, _get_openai_client
    return _get_collection(), _get_clip_metadata(), _get_openai_client()


def _process_ask_command(
    bot_token: str,
    channel_id: str,
    question: str,
    response_url: str,
    thread_ts: Optional[str] = None,
    tenant_id: str = "",
):
    """Background task: run RAG query and post result back to Slack."""
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

        bot = SlackBot(bot_token)
        bot.post_qa_response(
            channel=channel_id,
            question=question,
            answer=result.get("answer", "No answer available."),
            sources=result.get("sources", []),
            filters_applied=result.get("filters_applied"),
            thread_ts=thread_ts,
        )

    except Exception:
        logger.exception("Failed to process Slack ask command")
        # Post error back via response_url as fallback
        try:
            import httpx
            httpx.post(response_url, json={
                "response_type": "ephemeral",
                "text": "Sorry, an error occurred processing your question. Please try again.",
            }, timeout=5.0)
        except Exception:
            logger.exception("Failed to send error response to Slack")


def _process_search_command(
    bot_token: str,
    channel_id: str,
    query: str,
    response_url: str,
    thread_ts: Optional[str] = None,
    tenant_id: str = "",
):
    """Background task: search meetings and post results back to Slack."""
    try:
        from api.server import _get_clip_metadata

        clip_metadata = _get_clip_metadata()

        # Simple keyword search over clip metadata
        query_lower = query.lower()
        results = []
        for clip_id, meta in clip_metadata.items():
            title = meta.get("title", "").lower()
            topics = [t.lower() for t in meta.get("topics", [])]
            body = meta.get("meeting_body", "").lower()

            if (
                query_lower in title
                or query_lower in body
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

        # Sort by date descending
        results.sort(key=lambda r: r.get("date", ""), reverse=True)

        bot = SlackBot(bot_token)
        bot.post_search_results(
            channel=channel_id,
            query=query,
            results=results[:10],
            thread_ts=thread_ts,
        )

    except Exception:
        logger.exception("Failed to process Slack search command")
        try:
            import httpx
            httpx.post(response_url, json={
                "response_type": "ephemeral",
                "text": "Sorry, an error occurred processing your search. Please try again.",
            }, timeout=5.0)
        except Exception:
            logger.exception("Failed to send error response to Slack")


# ---------------------------------------------------------------------------
# Slash command endpoint
# ---------------------------------------------------------------------------


@router.post("/commands")
async def handle_slash_command(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Handle /civiclens slash commands from Slack.

    Verifies signing secret, parses the command, and dispatches.
    Returns an immediate acknowledgement (< 3s) and processes async.
    """
    body = await _verify_slack_request(request)

    # Parse form-encoded body
    from urllib.parse import parse_qs
    params = parse_qs(body.decode("utf-8"))

    text = params.get("text", [""])[0]
    channel_id = params.get("channel_id", [""])[0]
    team_id = params.get("team_id", [""])[0]
    response_url = params.get("response_url", [""])[0]

    # Look up tenant config by team_id
    store = get_slack_config_store()
    config = store.get_by_team_id(team_id)
    if not config:
        return Response(
            content=json.dumps({
                "response_type": "ephemeral",
                "text": "This Slack workspace is not connected to a CivicLens account. "
                        "Please ask your admin to install the CivicLens integration.",
            }),
            media_type="application/json",
        )

    subcommand, argument = parse_slash_command(text)

    if subcommand == "help":
        return Response(
            content=json.dumps({
                "response_type": "ephemeral",
                "blocks": build_help_blocks(),
            }),
            media_type="application/json",
        )

    if subcommand == "status":
        return Response(
            content=json.dumps({
                "response_type": "ephemeral",
                "text": (
                    f"CivicLens is connected.\n"
                    f"Workspace: {config.team_id}\n"
                    f"Notifications channel: {config.channel_id or 'Not configured'}\n"
                    f"New meeting alerts: {'On' if config.notify_new_meetings else 'Off'}\n"
                    f"Vote alerts: {'On' if config.notify_votes else 'Off'}"
                ),
            }),
            media_type="application/json",
        )

    if subcommand == "ask":
        if not argument:
            return Response(
                content=json.dumps({
                    "response_type": "ephemeral",
                    "text": "Please provide a question. Usage: `/civiclens ask <question>`",
                }),
                media_type="application/json",
            )

        # Acknowledge immediately, process in background
        background_tasks.add_task(
            _process_ask_command,
            bot_token=config.bot_token,
            channel_id=channel_id,
            question=argument,
            response_url=response_url,
            tenant_id=config.tenant_id,
        )

        return Response(
            content=json.dumps({
                "response_type": "in_channel",
                "text": f"Looking up: _{argument}_\nPlease wait...",
            }),
            media_type="application/json",
        )

    if subcommand == "search":
        if not argument:
            return Response(
                content=json.dumps({
                    "response_type": "ephemeral",
                    "text": "Please provide a search term. Usage: `/civiclens search <term>`",
                }),
                media_type="application/json",
            )

        background_tasks.add_task(
            _process_search_command,
            bot_token=config.bot_token,
            channel_id=channel_id,
            query=argument,
            response_url=response_url,
            tenant_id=config.tenant_id,
        )

        return Response(
            content=json.dumps({
                "response_type": "in_channel",
                "text": f"Searching for: _{argument}_\nPlease wait...",
            }),
            media_type="application/json",
        )

    # Fallback
    return Response(
        content=json.dumps({
            "response_type": "ephemeral",
            "text": f"Unknown command: `{subcommand}`. Type `/civiclens help` for usage.",
        }),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Events API endpoint
# ---------------------------------------------------------------------------


@router.post("/events")
async def handle_events(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Handle Slack Events API requests.

    Supports:
    - URL verification challenge (no signature check needed for initial setup)
    - app_mention events (responds to @CivicLens in channels)
    """
    body_bytes = await request.body()
    payload = json.loads(body_bytes)

    # URL verification challenge -- Slack sends this during Event Subscription setup
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # For all other events, verify signature
    secret = _signing_secret()
    if secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if not verify_slack_signature(secret, timestamp, body_bytes, signature):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")

    # Handle event_callback
    if payload.get("type") != "event_callback":
        return {"ok": True}

    event = payload.get("event", {})
    event_type = event.get("type")
    team_id = payload.get("team_id", "")

    # Look up tenant config
    store = get_slack_config_store()
    config = store.get_by_team_id(team_id)
    if not config:
        logger.warning("Received Slack event for unknown team: %s", team_id)
        return {"ok": True}

    if event_type == "app_mention":
        # Extract the question from the mention text (strip the bot mention)
        text = event.get("text", "")
        # Remove <@BOT_ID> mention pattern
        import re
        question = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if question:
            channel = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts")

            background_tasks.add_task(
                _process_ask_command,
                bot_token=config.bot_token,
                channel_id=channel,
                question=question,
                response_url="",  # No response_url for events
                thread_ts=thread_ts,
                tenant_id=config.tenant_id,
            )

    return {"ok": True}


# ---------------------------------------------------------------------------
# OAuth install flow
# ---------------------------------------------------------------------------


@router.post("/install")
async def start_install(tenant: Tenant = Depends(require_tenant)):
    """Start the Slack OAuth install flow.

    Returns the Slack authorization URL for the tenant to visit.
    Requires tenant authentication so we can associate the install.
    """
    client_id = _client_id()
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Slack OAuth not configured. Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET.",
        )

    scopes = "chat:write,commands,app_mentions:read,channels:read"
    # Encode tenant_id in state parameter for the callback
    state = f"{tenant.id}"

    auth_url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&state={state}"
    )

    return {"authorization_url": auth_url, "tenant_id": tenant.id}


@router.get("/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Handle Slack OAuth callback after workspace installation.

    Exchanges the code for a bot token and saves the workspace config.
    """
    if error:
        raise HTTPException(status_code=400, detail=f"Slack OAuth error: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    client_id = _client_id()
    client_secret = _client_secret()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Slack OAuth not configured")

    # Exchange code for token
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            resp = await http_client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
            )
            data = resp.json()
    except Exception:
        logger.exception("Failed to exchange Slack OAuth code")
        raise HTTPException(status_code=502, detail="Failed to communicate with Slack")

    if not data.get("ok"):
        error_msg = data.get("error", "unknown_error")
        raise HTTPException(status_code=400, detail=f"Slack OAuth failed: {error_msg}")

    # Extract token and workspace info
    bot_token = data.get("access_token", "")
    team_id = data.get("team", {}).get("id", "")
    team_name = data.get("team", {}).get("name", "")
    authed_user = data.get("authed_user", {}).get("id", "")

    # state contains tenant_id
    tenant_id = state if state else "unknown"

    # Save config
    store = get_slack_config_store()
    config = SlackConfig(
        tenant_id=tenant_id,
        team_id=team_id,
        bot_token=bot_token,
        channel_id="",
        notify_new_meetings=True,
        notify_votes=True,
        notify_financial=False,
        installed_at=datetime.now(timezone.utc).isoformat(),
        installed_by=authed_user,
    )
    store.save(config)

    logger.info(
        "Slack workspace installed: team=%s tenant=%s",
        team_id, tenant_id,
    )

    return {
        "success": True,
        "team_id": team_id,
        "team_name": team_name,
        "tenant_id": tenant_id,
        "message": "Slack workspace connected successfully. Configure your notification channel in settings.",
    }


# ---------------------------------------------------------------------------
# Tenant config endpoints
# ---------------------------------------------------------------------------


class SlackConfigUpdate(BaseModel):
    channel_id: Optional[str] = None
    notify_new_meetings: Optional[bool] = None
    notify_votes: Optional[bool] = None
    notify_financial: Optional[bool] = None


@router.put("/config")
async def update_slack_config(
    request: SlackConfigUpdate,
    tenant: Tenant = Depends(require_tenant),
):
    """Save or update Slack workspace configuration for the authenticated tenant."""
    store = get_slack_config_store()
    config = store.update_notifications(
        tenant_id=tenant.id,
        channel_id=request.channel_id,
        notify_new_meetings=request.notify_new_meetings,
        notify_votes=request.notify_votes,
        notify_financial=request.notify_financial,
    )
    if not config:
        raise HTTPException(
            status_code=404,
            detail="No Slack integration found. Install the Slack app first.",
        )

    return {
        "tenant_id": config.tenant_id,
        "team_id": config.team_id,
        "channel_id": config.channel_id,
        "notify_new_meetings": config.notify_new_meetings,
        "notify_votes": config.notify_votes,
        "notify_financial": config.notify_financial,
    }


@router.get("/config")
async def get_slack_config(tenant: Tenant = Depends(require_tenant)):
    """Get current Slack configuration for the authenticated tenant."""
    store = get_slack_config_store()
    config = store.get(tenant.id)
    if not config:
        raise HTTPException(
            status_code=404,
            detail="No Slack integration found. Install the Slack app first.",
        )

    return {
        "tenant_id": config.tenant_id,
        "team_id": config.team_id,
        "channel_id": config.channel_id,
        "notify_new_meetings": config.notify_new_meetings,
        "notify_votes": config.notify_votes,
        "notify_financial": config.notify_financial,
        "installed_at": config.installed_at,
    }
