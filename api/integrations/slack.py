"""Slack integration for CivicLens meeting intelligence platform.

Provides:
- SlackBot class wrapping slack_sdk for posting messages and handling events
- Slash command handlers (/civiclens ask, /civiclens search)
- Event handler for @CivicLens mentions in channels
- Incoming webhook support for posting meeting summaries
- Rich Block Kit message formatting for meetings, votes, and Q&A
"""

import hashlib
import hmac
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.webhook import WebhookClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------


@dataclass
class SlackConfig:
    """Per-tenant Slack workspace configuration."""

    tenant_id: str
    team_id: str  # Slack workspace ID
    bot_token: str
    channel_id: str = ""  # Default channel for notifications
    notify_new_meetings: bool = True
    notify_votes: bool = True
    notify_financial: bool = False
    installed_at: str = ""
    installed_by: str = ""  # Slack user ID who installed


# ---------------------------------------------------------------------------
# Signing secret verification
# ---------------------------------------------------------------------------


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify a Slack request signature using the signing secret.

    Slack sends X-Slack-Request-Timestamp and X-Slack-Signature headers.
    We compute HMAC-SHA256 over "v0:{timestamp}:{body}" and compare.
    Rejects requests older than 5 minutes to prevent replay attacks.
    """
    # Reject stale requests (> 5 min)
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > 300:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Block Kit message formatters
# ---------------------------------------------------------------------------


def format_qa_response(
    question: str,
    answer: str,
    sources: list[dict],
    filters_applied: dict | None = None,
) -> list[dict]:
    """Format a RAG Q&A response as Slack Block Kit blocks.

    Returns a list of block dicts ready for the Slack API.
    """
    blocks: list[dict] = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": "CivicLens Q&A", "emoji": True},
    })

    # Question
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Question:* {question}"},
    })

    blocks.append({"type": "divider"})

    # Answer - truncate to Slack's 3000 char limit per text block
    answer_text = answer[:2900] + "..." if len(answer) > 2900 else answer
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": answer_text},
    })

    # Sources
    if sources:
        blocks.append({"type": "divider"})
        source_lines = []
        for s in sources[:5]:  # Limit to 5 sources in Slack
            title = s.get("title", "Unknown Meeting")
            date = s.get("date", "")
            url = s.get("granicus_url", "")
            timestamp = s.get("timestamp")
            ts_str = ""
            if timestamp is not None:
                m, sec = divmod(int(timestamp), 60)
                ts_str = f" [{m}:{sec:02d}]"
            if url:
                source_lines.append(f"<{url}|{title}> ({date}){ts_str}")
            else:
                source_lines.append(f"{title} ({date}){ts_str}")

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "*Sources:*\n" + "\n".join(source_lines),
                }
            ],
        })

    # Filters applied
    if filters_applied:
        filter_parts = []
        if filters_applied.get("meeting_body"):
            filter_parts.append(f"Body: {filters_applied['meeting_body']}")
        if filters_applied.get("date_after"):
            filter_parts.append(f"After: {filters_applied['date_after']}")
        if filters_applied.get("date_before"):
            filter_parts.append(f"Before: {filters_applied['date_before']}")
        if filter_parts:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Filters: {' | '.join(filter_parts)}"}
                ],
            })

    return blocks


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
) -> list[dict]:
    """Format a meeting summary as a rich Slack Block Kit card."""
    blocks: list[dict] = []

    # Header
    header_text = f"New Meeting Processed: {title}"
    if len(header_text) > 150:
        header_text = header_text[:147] + "..."
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": header_text, "emoji": True},
    })

    # Metadata fields
    fields = [
        {"type": "mrkdwn", "text": f"*Date:*\n{date}"},
        {"type": "mrkdwn", "text": f"*Body:*\n{meeting_body}"},
        {"type": "mrkdwn", "text": f"*Clip ID:*\n{clip_id}"},
    ]
    if votes_count:
        fields.append({"type": "mrkdwn", "text": f"*Votes:*\n{votes_count}"})
    if financial_items_count:
        fields.append(
            {"type": "mrkdwn", "text": f"*Financial Items:*\n{financial_items_count}"}
        )

    blocks.append({"type": "section", "fields": fields[:10]})  # Slack max 10 fields

    # Topics
    if topics:
        topic_str = ", ".join(topics[:8])
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*Topics:* {topic_str}"}],
        })

    blocks.append({"type": "divider"})

    # Summary excerpt
    excerpt = summary_text[:2900] + "..." if len(summary_text) > 2900 else summary_text
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": excerpt},
    })

    # Link to full meeting
    if granicus_url:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Watch Meeting"},
                    "url": granicus_url,
                    "style": "primary",
                }
            ],
        })

    return blocks


def format_vote_result_card(
    clip_id: str | int,
    meeting_title: str,
    date: str,
    vote: dict,
    granicus_url: str = "",
) -> list[dict]:
    """Format a vote result as a Slack Block Kit card.

    Args:
        vote: Dict with keys from extracted_facts motions_and_votes schema.
    """
    blocks: list[dict] = []

    outcome = vote.get("outcome", "unknown").upper()
    outcome_emoji = ":white_check_mark:" if outcome == "PASSED" else ":x:"

    identifier = vote.get("identifier", "")
    description = vote.get("description", "Vote")
    header_text = f"{outcome_emoji} {identifier}: {description}" if identifier else f"{outcome_emoji} {description}"
    if len(header_text) > 150:
        header_text = header_text[:147] + "..."

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*{header_text}*"},
    })

    # Vote details
    fields = [
        {"type": "mrkdwn", "text": f"*Meeting:*\n{meeting_title}"},
        {"type": "mrkdwn", "text": f"*Date:*\n{date}"},
        {"type": "mrkdwn", "text": f"*Outcome:*\n{outcome}"},
    ]

    motion_by = vote.get("motion_by")
    second_by = vote.get("second_by")
    if motion_by:
        fields.append({"type": "mrkdwn", "text": f"*Motion by:*\n{motion_by}"})
    if second_by:
        fields.append({"type": "mrkdwn", "text": f"*Second by:*\n{second_by}"})

    ayes = vote.get("ayes")
    nays = vote.get("nays")
    if ayes is not None or nays is not None:
        tally = f"Ayes: {ayes or 0} / Nays: {nays or 0}"
        abstentions = vote.get("abstentions")
        if abstentions:
            tally += f" / Abstentions: {abstentions}"
        fields.append({"type": "mrkdwn", "text": f"*Tally:*\n{tally}"})

    blocks.append({"type": "section", "fields": fields[:10]})

    # Roll call details
    votes_for = vote.get("votes_for", [])
    votes_against = vote.get("votes_against", [])
    if votes_for or votes_against:
        roll_parts = []
        if votes_for:
            roll_parts.append(f"*Ayes:* {', '.join(votes_for)}")
        if votes_against:
            roll_parts.append(f"*Nays:* {', '.join(votes_against)}")
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(roll_parts)}],
        })

    # Link
    if granicus_url:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"<{granicus_url}|Watch in meeting video>"}
            ],
        })

    return blocks


def format_search_results(
    query: str,
    results: list[dict],
) -> list[dict]:
    """Format meeting search results as Slack Block Kit blocks.

    Args:
        results: List of dicts with title, date, meeting_body, clip_id, topics, url.
    """
    blocks: list[dict] = []

    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"Search: {query[:100]}", "emoji": True},
    })

    if not results:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No meetings found matching your search."},
        })
        return blocks

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Found {len(results)} matching meeting(s)"}
        ],
    })

    blocks.append({"type": "divider"})

    for meeting in results[:10]:  # Limit to 10 results in Slack
        title = meeting.get("title", "Unknown")
        date = meeting.get("date", "")
        body = meeting.get("meeting_body", "")
        topics = meeting.get("topics", [])
        url = meeting.get("url", "")

        text = f"*{title}*\n{date} | {body}"
        if topics:
            text += f"\nTopics: {', '.join(topics[:5])}"

        section: dict[str, Any] = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
        if url:
            section["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "View"},
                "url": url,
            }
        blocks.append(section)

    return blocks


# ---------------------------------------------------------------------------
# Slash command parser
# ---------------------------------------------------------------------------


def parse_slash_command(text: str) -> tuple[str, str]:
    """Parse /civiclens slash command text into (subcommand, argument).

    Examples:
        "ask What has the city done about parks?" -> ("ask", "What has the city done about parks?")
        "search zoning"                          -> ("search", "zoning")
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


def build_help_blocks() -> list[dict]:
    """Build help text for the /civiclens slash command."""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "CivicLens Commands", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Available commands:*\n\n"
                    "`/civiclens ask <question>` - Ask a question about city meetings\n"
                    "`/civiclens search <term>` - Search meeting archive\n"
                    "`/civiclens status` - Check connection status\n"
                    "`/civiclens help` - Show this help message\n\n"
                    "You can also mention *@CivicLens* in any channel to ask a question."
                ),
            },
        },
    ]


# ---------------------------------------------------------------------------
# SQLite config store for per-tenant Slack settings
# ---------------------------------------------------------------------------

_CREATE_SLACK_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS slack_configs (
    tenant_id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    bot_token TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    notify_new_meetings INTEGER NOT NULL DEFAULT 1,
    notify_votes INTEGER NOT NULL DEFAULT 1,
    notify_financial INTEGER NOT NULL DEFAULT 0,
    installed_at TEXT NOT NULL,
    installed_by TEXT NOT NULL DEFAULT ''
);
"""


class SlackConfigStore:
    """SQLite-backed store for per-tenant Slack configuration."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_SLACK_CONFIG_TABLE)
        self._conn.commit()

    def _row_to_config(self, row: sqlite3.Row) -> SlackConfig:
        d = dict(row)
        d["notify_new_meetings"] = bool(d["notify_new_meetings"])
        d["notify_votes"] = bool(d["notify_votes"])
        d["notify_financial"] = bool(d["notify_financial"])
        return SlackConfig(**d)

    def get(self, tenant_id: str) -> Optional[SlackConfig]:
        row = self._conn.execute(
            "SELECT * FROM slack_configs WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        return self._row_to_config(row) if row else None

    def save(self, config: SlackConfig) -> SlackConfig:
        """Insert or update a Slack config."""
        self._conn.execute(
            """INSERT INTO slack_configs
               (tenant_id, team_id, bot_token, channel_id,
                notify_new_meetings, notify_votes, notify_financial,
                installed_at, installed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id) DO UPDATE SET
                 team_id = excluded.team_id,
                 bot_token = excluded.bot_token,
                 channel_id = excluded.channel_id,
                 notify_new_meetings = excluded.notify_new_meetings,
                 notify_votes = excluded.notify_votes,
                 notify_financial = excluded.notify_financial,
                 installed_at = excluded.installed_at,
                 installed_by = excluded.installed_by
            """,
            (
                config.tenant_id,
                config.team_id,
                config.bot_token,
                config.channel_id,
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
        channel_id: Optional[str] = None,
        notify_new_meetings: Optional[bool] = None,
        notify_votes: Optional[bool] = None,
        notify_financial: Optional[bool] = None,
    ) -> Optional[SlackConfig]:
        """Partially update notification settings. Returns updated config or None."""
        existing = self.get(tenant_id)
        if not existing:
            return None

        if channel_id is not None:
            existing.channel_id = channel_id
        if notify_new_meetings is not None:
            existing.notify_new_meetings = notify_new_meetings
        if notify_votes is not None:
            existing.notify_votes = notify_votes
        if notify_financial is not None:
            existing.notify_financial = notify_financial

        return self.save(existing)

    def delete(self, tenant_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM slack_configs WHERE tenant_id = ?", (tenant_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_by_team_id(self, team_id: str) -> Optional[SlackConfig]:
        """Look up config by Slack workspace team_id."""
        row = self._conn.execute(
            "SELECT * FROM slack_configs WHERE team_id = ?", (team_id,)
        ).fetchone()
        return self._row_to_config(row) if row else None

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_slack_config_store: Optional[SlackConfigStore] = None


def init_slack(output_dir: str) -> SlackConfigStore:
    """Initialize the Slack config store. Call once at startup."""
    global _slack_config_store
    db_path = os.path.join(output_dir, "slack_configs.db")
    _slack_config_store = SlackConfigStore(db_path)
    return _slack_config_store


def get_slack_config_store() -> SlackConfigStore:
    if _slack_config_store is None:
        raise RuntimeError("Slack not initialized -- call init_slack() first")
    return _slack_config_store


# ---------------------------------------------------------------------------
# SlackBot class
# ---------------------------------------------------------------------------


class SlackBot:
    """High-level Slack bot for CivicLens.

    Wraps slack_sdk WebClient and provides methods for posting formatted
    messages, handling slash commands, and processing app_mention events.
    """

    def __init__(self, bot_token: str):
        self._client = WebClient(token=bot_token)

    @property
    def client(self) -> WebClient:
        return self._client

    def post_blocks(
        self,
        channel: str,
        blocks: list[dict],
        text: str = "CivicLens notification",
        thread_ts: Optional[str] = None,
    ) -> Optional[dict]:
        """Post a Block Kit message to a channel.

        Args:
            channel: Slack channel ID.
            blocks: List of Block Kit block dicts.
            text: Fallback text for notifications.
            thread_ts: If set, post as a threaded reply.

        Returns:
            Slack API response dict, or None on failure.
        """
        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "blocks": blocks,
                "text": text,
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            response = self._client.chat_postMessage(**kwargs)
            return response.data
        except SlackApiError as e:
            logger.error("Slack API error posting message: %s", e.response["error"])
            return None

    def post_meeting_summary(
        self,
        channel: str,
        clip_id: str | int,
        title: str,
        date: str,
        meeting_body: str,
        topics: list[str],
        summary_text: str,
        granicus_url: str = "",
        votes_count: int = 0,
        financial_items_count: int = 0,
    ) -> Optional[dict]:
        """Post a formatted meeting summary card to a Slack channel."""
        blocks = format_meeting_summary_card(
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
        return self.post_blocks(
            channel=channel,
            blocks=blocks,
            text=f"New meeting processed: {title}",
        )

    def post_vote_result(
        self,
        channel: str,
        clip_id: str | int,
        meeting_title: str,
        date: str,
        vote: dict,
        granicus_url: str = "",
    ) -> Optional[dict]:
        """Post a formatted vote result card to a Slack channel."""
        blocks = format_vote_result_card(
            clip_id=clip_id,
            meeting_title=meeting_title,
            date=date,
            vote=vote,
            granicus_url=granicus_url,
        )
        identifier = vote.get("identifier", "Vote")
        outcome = vote.get("outcome", "unknown")
        return self.post_blocks(
            channel=channel,
            blocks=blocks,
            text=f"Vote result: {identifier} - {outcome}",
        )

    def post_qa_response(
        self,
        channel: str,
        question: str,
        answer: str,
        sources: list[dict],
        filters_applied: dict | None = None,
        thread_ts: Optional[str] = None,
    ) -> Optional[dict]:
        """Post a formatted Q&A response to a Slack channel or thread."""
        blocks = format_qa_response(
            question=question,
            answer=answer,
            sources=sources,
            filters_applied=filters_applied,
        )
        return self.post_blocks(
            channel=channel,
            blocks=blocks,
            text=f"Q&A: {question[:100]}",
            thread_ts=thread_ts,
        )

    def post_search_results(
        self,
        channel: str,
        query: str,
        results: list[dict],
        thread_ts: Optional[str] = None,
    ) -> Optional[dict]:
        """Post formatted search results to a Slack channel or thread."""
        blocks = format_search_results(query=query, results=results)
        return self.post_blocks(
            channel=channel,
            blocks=blocks,
            text=f"Search results: {query[:100]}",
            thread_ts=thread_ts,
        )

    def post_webhook_message(
        self,
        webhook_url: str,
        blocks: list[dict],
        text: str = "CivicLens notification",
    ) -> bool:
        """Post a message via an incoming webhook URL.

        Returns True on success, False on failure.
        """
        try:
            webhook = WebhookClient(webhook_url)
            response = webhook.send(blocks=blocks, text=text)
            return response.status_code == 200
        except Exception:
            logger.exception("Failed to send Slack webhook message")
            return False
