"""Microsoft Teams integration for CivicLens meeting intelligence platform.

Provides:
- TeamsBot class using simple webhook approach (no Bot Framework dependency)
- Incoming webhook support for posting meeting summaries to Teams channels
- Outgoing webhook handler for @CivicLens mentions with RAG Q&A
- Adaptive Card formatters for: meeting summary, vote result, Q&A response, search results
- TeamsConfigStore (SQLite) for per-tenant Teams workspace configuration
"""

import hashlib
import hmac
import base64
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------


@dataclass
class TeamsConfig:
    """Per-tenant Teams workspace configuration."""

    tenant_id: str
    teams_tenant_id: str  # Microsoft 365 tenant ID
    webhook_url: str  # Incoming webhook URL for posting to Teams
    channel_name: str = ""  # Display name of the target channel
    notify_new_meetings: bool = True
    notify_votes: bool = True
    notify_financial: bool = False
    installed_at: str = ""
    installed_by: str = ""  # User who configured the integration


# ---------------------------------------------------------------------------
# HMAC verification for outgoing webhooks
# ---------------------------------------------------------------------------


def verify_teams_signature(
    webhook_secret: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify a Teams outgoing webhook request using HMAC-SHA256.

    Teams outgoing webhooks send an Authorization header with
    "HMAC <base64-encoded-hmac>" computed over the request body
    using the shared secret (base64-decoded).

    Returns True if the signature is valid, False otherwise.
    """
    if not webhook_secret or not signature:
        return False

    # Teams sends "HMAC <value>"
    prefix = "HMAC "
    if signature.startswith(prefix):
        signature = signature[len(prefix):]

    try:
        secret_bytes = base64.b64decode(webhook_secret)
    except Exception:
        logger.error("Failed to base64-decode Teams webhook secret")
        return False

    computed = base64.b64encode(
        hmac.new(secret_bytes, body, hashlib.sha256).digest()
    ).decode("utf-8")

    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Adaptive Card formatters
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int = 2000) -> str:
    """Truncate text to max_len, appending '...' if truncated."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_qa_response(
    question: str,
    answer: str,
    sources: list[dict],
    filters_applied: dict | None = None,
) -> dict:
    """Format a RAG Q&A response as a Teams Adaptive Card.

    Returns an Adaptive Card JSON dict.
    """
    body: list[dict] = []

    # Header
    body.append({
        "type": "TextBlock",
        "text": "CivicLens Q&A",
        "size": "Large",
        "weight": "Bolder",
        "color": "Accent",
    })

    # Question
    body.append({
        "type": "TextBlock",
        "text": f"**Question:** {question}",
        "wrap": True,
    })

    body.append({"type": "TextBlock", "text": "---", "spacing": "Small"})

    # Answer
    body.append({
        "type": "TextBlock",
        "text": _truncate(answer),
        "wrap": True,
    })

    # Sources
    if sources:
        body.append({"type": "TextBlock", "text": "---", "spacing": "Small"})
        source_lines = []
        for s in sources[:5]:
            title = s.get("title", "Unknown Meeting")
            date = s.get("date", "")
            url = s.get("granicus_url", "")
            timestamp = s.get("timestamp")
            ts_str = ""
            if timestamp is not None:
                m, sec = divmod(int(timestamp), 60)
                ts_str = f" [{m}:{sec:02d}]"
            if url:
                source_lines.append(f"- [{title}]({url}) ({date}){ts_str}")
            else:
                source_lines.append(f"- {title} ({date}){ts_str}")

        body.append({
            "type": "TextBlock",
            "text": "**Sources:**\n" + "\n".join(source_lines),
            "wrap": True,
            "size": "Small",
        })

    # Filters
    if filters_applied:
        filter_parts = []
        if filters_applied.get("meeting_body"):
            filter_parts.append(f"Body: {filters_applied['meeting_body']}")
        if filters_applied.get("date_after"):
            filter_parts.append(f"After: {filters_applied['date_after']}")
        if filters_applied.get("date_before"):
            filter_parts.append(f"Before: {filters_applied['date_before']}")
        if filter_parts:
            body.append({
                "type": "TextBlock",
                "text": f"Filters: {' | '.join(filter_parts)}",
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
            })

    return _wrap_adaptive_card(body)


def format_meeting_summary_card(
    clip_id: str | int,
    title: str,
    date: str,
    meeting_body: str,
    topics: list[str],
    summary_text: str,
    granicus_url: str = "",
    votes_count: int = 0,
    financial_items_count: int = 0,
) -> dict:
    """Format a meeting summary as a Teams Adaptive Card."""
    body: list[dict] = []

    # Header
    header_text = f"New Meeting Processed: {title}"
    if len(header_text) > 200:
        header_text = header_text[:197] + "..."
    body.append({
        "type": "TextBlock",
        "text": header_text,
        "size": "Large",
        "weight": "Bolder",
        "color": "Accent",
        "wrap": True,
    })

    # Metadata as FactSet (Teams equivalent of Slack fields)
    facts = [
        {"title": "Date", "value": date},
        {"title": "Body", "value": meeting_body},
        {"title": "Clip ID", "value": str(clip_id)},
    ]
    if votes_count:
        facts.append({"title": "Votes", "value": str(votes_count)})
    if financial_items_count:
        facts.append({"title": "Financial Items", "value": str(financial_items_count)})

    body.append({"type": "FactSet", "facts": facts})

    # Topics
    if topics:
        topic_str = ", ".join(topics[:8])
        body.append({
            "type": "TextBlock",
            "text": f"**Topics:** {topic_str}",
            "wrap": True,
            "size": "Small",
            "isSubtle": True,
        })

    body.append({"type": "TextBlock", "text": "---", "spacing": "Small"})

    # Summary excerpt
    body.append({
        "type": "TextBlock",
        "text": _truncate(summary_text),
        "wrap": True,
    })

    # Action button
    actions = []
    if granicus_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "Watch Meeting",
            "url": granicus_url,
            "style": "positive",
        })

    return _wrap_adaptive_card(body, actions=actions)


def format_vote_result_card(
    clip_id: str | int,
    meeting_title: str,
    date: str,
    vote: dict,
    granicus_url: str = "",
) -> dict:
    """Format a vote result as a Teams Adaptive Card.

    Args:
        vote: Dict with keys from extracted_facts motions_and_votes schema.
    """
    body: list[dict] = []

    outcome = vote.get("outcome", "unknown").upper()
    outcome_icon = "✅" if outcome == "PASSED" else "❌"

    identifier = vote.get("identifier", "")
    description = vote.get("description", "Vote")
    header = f"{outcome_icon} {identifier}: {description}" if identifier else f"{outcome_icon} {description}"
    if len(header) > 200:
        header = header[:197] + "..."

    body.append({
        "type": "TextBlock",
        "text": header,
        "size": "Medium",
        "weight": "Bolder",
        "wrap": True,
    })

    # Vote details as FactSet
    facts = [
        {"title": "Meeting", "value": meeting_title},
        {"title": "Date", "value": date},
        {"title": "Outcome", "value": outcome},
    ]

    motion_by = vote.get("motion_by")
    second_by = vote.get("second_by")
    if motion_by:
        facts.append({"title": "Motion by", "value": motion_by})
    if second_by:
        facts.append({"title": "Second by", "value": second_by})

    ayes = vote.get("ayes")
    nays = vote.get("nays")
    if ayes is not None or nays is not None:
        tally = f"Ayes: {ayes or 0} / Nays: {nays or 0}"
        abstentions = vote.get("abstentions")
        if abstentions:
            tally += f" / Abstentions: {abstentions}"
        facts.append({"title": "Tally", "value": tally})

    body.append({"type": "FactSet", "facts": facts})

    # Roll call details
    votes_for = vote.get("votes_for", [])
    votes_against = vote.get("votes_against", [])
    if votes_for or votes_against:
        roll_parts = []
        if votes_for:
            roll_parts.append(f"**Ayes:** {', '.join(votes_for)}")
        if votes_against:
            roll_parts.append(f"**Nays:** {', '.join(votes_against)}")
        body.append({
            "type": "TextBlock",
            "text": " | ".join(roll_parts),
            "wrap": True,
            "size": "Small",
        })

    # Link
    actions = []
    if granicus_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "Watch in Meeting Video",
            "url": granicus_url,
        })

    return _wrap_adaptive_card(body, actions=actions)


def format_search_results(
    query: str,
    results: list[dict],
) -> dict:
    """Format meeting search results as a Teams Adaptive Card.

    Args:
        results: List of dicts with title, date, meeting_body, clip_id, topics, url.
    """
    body: list[dict] = []

    body.append({
        "type": "TextBlock",
        "text": f"Search: {query[:100]}",
        "size": "Large",
        "weight": "Bolder",
        "color": "Accent",
    })

    if not results:
        body.append({
            "type": "TextBlock",
            "text": "No meetings found matching your search.",
            "wrap": True,
        })
        return _wrap_adaptive_card(body)

    body.append({
        "type": "TextBlock",
        "text": f"Found {len(results)} matching meeting(s)",
        "size": "Small",
        "isSubtle": True,
    })

    body.append({"type": "TextBlock", "text": "---", "spacing": "Small"})

    for meeting in results[:10]:
        title = meeting.get("title", "Unknown")
        date = meeting.get("date", "")
        mb = meeting.get("meeting_body", "")
        topics = meeting.get("topics", [])
        url = meeting.get("url", "")

        text = f"**{title}**\n{date} | {mb}"
        if topics:
            text += f"\nTopics: {', '.join(topics[:5])}"

        container_items: list[dict] = [{
            "type": "TextBlock",
            "text": text,
            "wrap": True,
        }]

        if url:
            container_items.append({
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.OpenUrl",
                    "title": "View",
                    "url": url,
                }],
            })

        body.append({
            "type": "Container",
            "items": container_items,
            "separator": True,
        })

    return _wrap_adaptive_card(body)


def _wrap_adaptive_card(
    body: list[dict],
    actions: list[dict] | None = None,
) -> dict:
    """Wrap body elements in a standard Adaptive Card envelope."""
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return card


def adaptive_card_to_message(card: dict, summary_text: str = "CivicLens notification") -> dict:
    """Wrap an Adaptive Card in a Teams message payload for incoming webhooks.

    Returns the JSON payload to POST to a Teams incoming webhook URL.
    """
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
        "summary": summary_text,
    }


# ---------------------------------------------------------------------------
# Outgoing webhook message parser
# ---------------------------------------------------------------------------


def parse_teams_mention(text: str) -> str:
    """Extract the user's question from a Teams outgoing webhook message.

    Teams outgoing webhooks include the bot name in an <at> tag, e.g.:
    "<at>CivicLens</at> What has the city done about parks?"

    Returns the text with the <at>...</at> tag removed and stripped.
    """
    cleaned = re.sub(r"<at>[^<]*</at>", "", text).strip()
    return cleaned


def parse_command(text: str) -> tuple[str, str]:
    """Parse a Teams mention message into (subcommand, argument).

    Examples:
        "ask What has the city done about parks?" -> ("ask", "What has the city done about parks?")
        "search zoning"                          -> ("search", "zoning")
        "help"                                   -> ("help", "")
        ""                                       -> ("help", "")
    """
    text = text.strip()
    if not text:
        return "help", ""

    parts = text.split(None, 1)
    subcommand = parts[0].lower()
    argument = parts[1] if len(parts) > 1 else ""

    if subcommand in ("ask", "search", "help", "status"):
        return subcommand, argument

    # If not a recognized subcommand, treat the whole thing as an ask
    return "ask", text


def build_help_card() -> dict:
    """Build help text as a Teams Adaptive Card."""
    body = [
        {
            "type": "TextBlock",
            "text": "CivicLens Commands",
            "size": "Large",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": (
                "**Available commands:**\n\n"
                "- **ask \\<question\\>** - Ask a question about city meetings\n"
                "- **search \\<term\\>** - Search meeting archive\n"
                "- **status** - Check connection status\n"
                "- **help** - Show this help message\n\n"
                "Mention **@CivicLens** in any channel followed by a command or question."
            ),
            "wrap": True,
        },
    ]
    return _wrap_adaptive_card(body)


# ---------------------------------------------------------------------------
# SQLite config store for per-tenant Teams settings
# ---------------------------------------------------------------------------

_CREATE_TEAMS_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS teams_configs (
    tenant_id TEXT PRIMARY KEY,
    teams_tenant_id TEXT NOT NULL,
    webhook_url TEXT NOT NULL,
    channel_name TEXT NOT NULL DEFAULT '',
    notify_new_meetings INTEGER NOT NULL DEFAULT 1,
    notify_votes INTEGER NOT NULL DEFAULT 1,
    notify_financial INTEGER NOT NULL DEFAULT 0,
    installed_at TEXT NOT NULL,
    installed_by TEXT NOT NULL DEFAULT ''
);
"""


class TeamsConfigStore:
    """SQLite-backed store for per-tenant Teams configuration."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TEAMS_CONFIG_TABLE)
        self._conn.commit()

    def _row_to_config(self, row: sqlite3.Row) -> TeamsConfig:
        d = dict(row)
        d["notify_new_meetings"] = bool(d["notify_new_meetings"])
        d["notify_votes"] = bool(d["notify_votes"])
        d["notify_financial"] = bool(d["notify_financial"])
        return TeamsConfig(**d)

    def get(self, tenant_id: str) -> Optional[TeamsConfig]:
        row = self._conn.execute(
            "SELECT * FROM teams_configs WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        return self._row_to_config(row) if row else None

    def save(self, config: TeamsConfig) -> TeamsConfig:
        """Insert or update a Teams config."""
        self._conn.execute(
            """INSERT INTO teams_configs
               (tenant_id, teams_tenant_id, webhook_url, channel_name,
                notify_new_meetings, notify_votes, notify_financial,
                installed_at, installed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id) DO UPDATE SET
                 teams_tenant_id = excluded.teams_tenant_id,
                 webhook_url = excluded.webhook_url,
                 channel_name = excluded.channel_name,
                 notify_new_meetings = excluded.notify_new_meetings,
                 notify_votes = excluded.notify_votes,
                 notify_financial = excluded.notify_financial,
                 installed_at = excluded.installed_at,
                 installed_by = excluded.installed_by
            """,
            (
                config.tenant_id,
                config.teams_tenant_id,
                config.webhook_url,
                config.channel_name,
                int(config.notify_new_meetings),
                int(config.notify_votes),
                int(config.notify_financial),
                config.installed_at,
                config.installed_by,
            ),
        )
        self._conn.commit()
        return config

    def update_notifications(
        self,
        tenant_id: str,
        webhook_url: Optional[str] = None,
        channel_name: Optional[str] = None,
        notify_new_meetings: Optional[bool] = None,
        notify_votes: Optional[bool] = None,
        notify_financial: Optional[bool] = None,
    ) -> Optional[TeamsConfig]:
        """Partially update notification settings. Returns updated config or None."""
        existing = self.get(tenant_id)
        if not existing:
            return None

        if webhook_url is not None:
            existing.webhook_url = webhook_url
        if channel_name is not None:
            existing.channel_name = channel_name
        if notify_new_meetings is not None:
            existing.notify_new_meetings = notify_new_meetings
        if notify_votes is not None:
            existing.notify_votes = notify_votes
        if notify_financial is not None:
            existing.notify_financial = notify_financial

        return self.save(existing)

    def delete(self, tenant_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM teams_configs WHERE tenant_id = ?", (tenant_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_by_teams_tenant_id(self, teams_tenant_id: str) -> Optional[TeamsConfig]:
        """Look up config by Microsoft 365 tenant ID."""
        row = self._conn.execute(
            "SELECT * FROM teams_configs WHERE teams_tenant_id = ?",
            (teams_tenant_id,),
        ).fetchone()
        return self._row_to_config(row) if row else None

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_teams_config_store: Optional[TeamsConfigStore] = None


def init_teams(output_dir: str) -> TeamsConfigStore:
    """Initialize the Teams config store. Call once at startup."""
    global _teams_config_store
    db_path = os.path.join(output_dir, "teams_configs.db")
    _teams_config_store = TeamsConfigStore(db_path)
    return _teams_config_store


def get_teams_config_store() -> TeamsConfigStore:
    if _teams_config_store is None:
        raise RuntimeError("Teams not initialized -- call init_teams() first")
    return _teams_config_store


# ---------------------------------------------------------------------------
# TeamsBot class
# ---------------------------------------------------------------------------


class TeamsBot:
    """High-level Teams bot for CivicLens.

    Uses simple webhook-based approach (incoming webhooks for posting,
    outgoing webhooks for receiving). No Bot Framework SDK dependency.
    """

    def __init__(self, webhook_url: str):
        """Initialize with the Teams incoming webhook URL for a channel."""
        self._webhook_url = webhook_url

    def post_card(
        self,
        card: dict,
        summary_text: str = "CivicLens notification",
    ) -> bool:
        """Post an Adaptive Card to the configured Teams channel.

        Args:
            card: Adaptive Card dict (from format_* functions).
            summary_text: Fallback text shown in notifications.

        Returns:
            True on success, False on failure.
        """
        payload = adaptive_card_to_message(card, summary_text)
        try:
            response = httpx.post(
                self._webhook_url,
                json=payload,
                timeout=10.0,
            )
            if response.status_code == 200:
                return True
            logger.error(
                "Teams webhook returned status %d: %s",
                response.status_code,
                response.text[:200],
            )
            return False
        except Exception:
            logger.exception("Failed to send Teams webhook message")
            return False

    def post_meeting_summary(
        self,
        clip_id: str | int,
        title: str,
        date: str,
        meeting_body: str,
        topics: list[str],
        summary_text: str,
        granicus_url: str = "",
        votes_count: int = 0,
        financial_items_count: int = 0,
    ) -> bool:
        """Post a formatted meeting summary card to Teams."""
        card = format_meeting_summary_card(
            clip_id=clip_id,
            title=title,
            date=date,
            meeting_body=meeting_body,
            topics=topics,
            summary_text=summary_text,
            granicus_url=granicus_url,
            votes_count=votes_count,
            financial_items_count=financial_items_count,
        )
        return self.post_card(card, summary_text=f"New meeting processed: {title}")

    def post_vote_result(
        self,
        clip_id: str | int,
        meeting_title: str,
        date: str,
        vote: dict,
        granicus_url: str = "",
    ) -> bool:
        """Post a formatted vote result card to Teams."""
        card = format_vote_result_card(
            clip_id=clip_id,
            meeting_title=meeting_title,
            date=date,
            vote=vote,
            granicus_url=granicus_url,
        )
        identifier = vote.get("identifier", "Vote")
        outcome = vote.get("outcome", "unknown")
        return self.post_card(card, summary_text=f"Vote result: {identifier} - {outcome}")

    def post_qa_response(
        self,
        question: str,
        answer: str,
        sources: list[dict],
        filters_applied: dict | None = None,
    ) -> bool:
        """Post a formatted Q&A response to Teams."""
        card = format_qa_response(
            question=question,
            answer=answer,
            sources=sources,
            filters_applied=filters_applied,
        )
        return self.post_card(card, summary_text=f"Q&A: {question[:100]}")

    def post_search_results(
        self,
        query: str,
        results: list[dict],
    ) -> bool:
        """Post formatted search results to Teams."""
        card = format_search_results(query=query, results=results)
        return self.post_card(card, summary_text=f"Search results: {query[:100]}")
