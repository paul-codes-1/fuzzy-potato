"""Automated onboarding email drip campaign for new CivicLens tenants.

Manages a 5-email welcome sequence delivered over 14 days:

  Day  0: Welcome + quick start guide (API key, first query example)
  Day  1: "Process your first meeting" tutorial
  Day  3: "Set up Slack/Teams integration" guide
  Day  7: "Explore advanced features" (sentiment, vote tracking, reports)
  Day 14: "How's it going?" check-in + upgrade nudge for starter plans

Features:
- SQLite-backed tracking of which emails have been sent per tenant
- Open/click tracking via pixel + redirect URLs
- Pause/resume and skip support per tenant
- Respects existing digest unsubscribe preferences
- HTML templates with CivicLens branding (reuses notification base template)
"""

import hashlib
import hmac
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from api.notifications import NotificationManager, _render

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("CIVICLENS_BASE_URL", "http://localhost:5173")
API_BASE_URL = os.environ.get("CIVICLENS_API_URL", "http://localhost:8000")
TRACKING_SECRET = os.environ.get(
    "ONBOARDING_TRACKING_SECRET", "civiclens-onboarding-track-default"
)

# Email sequence definition: (email_number, delay_days, subject_template, template_key)
SEQUENCE = [
    (1, 0, "Welcome to CivicLens — let's get started", "welcome_quickstart"),
    (2, 1, "Process your first meeting with CivicLens", "first_meeting"),
    (3, 3, "Connect CivicLens to Slack or Teams", "integrations"),
    (4, 7, "Unlock advanced features in CivicLens", "advanced_features"),
    (5, 14, "How's it going with CivicLens?", "checkin"),
]

TOTAL_EMAILS = len(SEQUENCE)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SequenceStatus:
    """Status of the onboarding email sequence for a tenant."""

    tenant_id: str
    admin_email: str
    plan: str
    paused: bool
    created_at: str
    emails: list[dict] = field(default_factory=list)


@dataclass
class SendResult:
    """Result of a send_due_emails() run."""

    sent: int = 0
    failed: int = 0
    skipped: int = 0


# ---------------------------------------------------------------------------
# Tracking token helpers
# ---------------------------------------------------------------------------


def _generate_tracking_token(tenant_id: str, email_number: int, action: str) -> str:
    """Generate an HMAC-based tracking token for open/click events."""
    payload = f"{tenant_id}:{email_number}:{action}"
    return hmac.new(
        TRACKING_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_tracking_token(
    tenant_id: str, email_number: int, action: str, token: str
) -> bool:
    """Verify a tracking token."""
    expected = _generate_tracking_token(tenant_id, email_number, action)
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# Tracking URLs
# ---------------------------------------------------------------------------


def _open_pixel_url(tenant_id: str, email_number: int) -> str:
    """1x1 tracking pixel URL."""
    token = _generate_tracking_token(tenant_id, email_number, "open")
    return (
        f"{API_BASE_URL}/api/v1/onboarding/track/open"
        f"?tenant_id={tenant_id}&email={email_number}&token={token}"
    )


def _click_url(tenant_id: str, email_number: int, destination: str) -> str:
    """Click-tracking redirect URL."""
    token = _generate_tracking_token(tenant_id, email_number, "click")
    return (
        f"{API_BASE_URL}/api/v1/onboarding/track/click"
        f"?tenant_id={tenant_id}&email={email_number}&token={token}"
        f"&dest={destination}"
    )


def _unsubscribe_url(tenant_id: str) -> str:
    """Unsubscribe URL for onboarding emails."""
    token = _generate_tracking_token(tenant_id, 0, "unsubscribe")
    return (
        f"{API_BASE_URL}/api/v1/onboarding/unsubscribe"
        f"?tenant_id={tenant_id}&token={token}"
    )


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------


def _render_onboarding_email(
    content: str,
    tenant_id: str,
    email_number: int,
) -> str:
    """Wrap content in the CivicLens base template with tracking pixel and unsubscribe."""
    pixel = _open_pixel_url(tenant_id, email_number)
    unsub = _unsubscribe_url(tenant_id)

    footer_extra = (
        f'<a href="{unsub}" style="color:#6b7280;text-decoration:underline;">'
        f"Unsubscribe</a> from onboarding emails."
    )

    tracking_pixel = (
        f'<img src="{pixel}" width="1" height="1" alt="" '
        f'style="display:block;width:1px;height:1px;border:0;" />'
    )

    return _render(content + tracking_pixel, footer_extra=footer_extra)


def _cta_button(text: str, href: str, tenant_id: str, email_number: int) -> str:
    """Render a tracked CTA button."""
    tracked = _click_url(tenant_id, email_number, href)
    return (
        f'<div style="text-align:center;margin:28px 0;">'
        f'<a href="{tracked}" '
        f'style="display:inline-block;background-color:#1a56db;color:#ffffff;'
        f"padding:14px 32px;border-radius:6px;font-size:15px;font-weight:600;"
        f'text-decoration:none;">'
        f"{text}"
        f"</a></div>"
    )


def _template_welcome_quickstart(
    tenant_id: str, admin_email: str, plan: str, api_key: str
) -> tuple[str, str]:
    """Day 0: Welcome + quick start guide."""
    docs_url = f"{BASE_URL}/docs"
    subject = "Welcome to CivicLens -- let's get started"
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Welcome to CivicLens!</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Your account is ready. CivicLens turns government meeting archives into
        a searchable, queryable knowledge base powered by AI. Here is how to get
        started in under 5 minutes.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">1. Your API Key</h3>
      <div style="background-color:#f0f4ff;border:1px solid #dbe4ff;border-radius:6px;padding:16px;margin:12px 0;">
        <code style="display:block;background:#1e293b;color:#38bdf8;padding:12px;border-radius:4px;font-size:13px;word-break:break-all;">
          {api_key}
        </code>
        <p style="margin:8px 0 0;color:#6b7280;font-size:12px;">
          Include this in the <code style="background:#f3f4f6;padding:2px 6px;border-radius:3px;">X-API-Key</code>
          header with every API request.
        </p>
      </div>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">2. Try Your First Query</h3>
      <div style="background-color:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:16px;margin:12px 0;">
        <code style="display:block;background:#1e293b;color:#38bdf8;padding:12px;border-radius:4px;font-size:12px;white-space:pre;overflow-x:auto;">curl -X POST {API_BASE_URL}/api/v1/ask \\
  -H "X-API-Key: {api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"question": "What was discussed at the last council meeting?"}}'</code>
      </div>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">3. Explore the Dashboard</h3>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Browse meetings, search transcripts, and ask questions from the web interface.
      </p>

      {_cta_button("Open Dashboard", BASE_URL, tenant_id, 1)}

      <p style="color:#6b7280;font-size:13px;line-height:1.5;">
        Your current plan: <strong>{plan}</strong>. You can upgrade at any time
        from the Admin dashboard.
      </p>
    """
    return subject, _render_onboarding_email(content, tenant_id, 1)


def _template_first_meeting(
    tenant_id: str, admin_email: str, plan: str, api_key: str
) -> tuple[str, str]:
    """Day 1: Process your first meeting tutorial."""
    subject = "Process your first meeting with CivicLens"
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Process Your First Meeting</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        CivicLens automatically downloads, transcribes, and extracts structured
        data from Granicus meeting recordings. Here is how to process your first meeting.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Option A: Automatic Scraping</h3>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        The easiest way is to let CivicLens discover and process meetings automatically:
      </p>
      <div style="background-color:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:16px;margin:12px 0;">
        <code style="display:block;background:#1e293b;color:#38bdf8;padding:12px;border-radius:4px;font-size:12px;white-space:pre;overflow-x:auto;">uv run python main.py --tenant-id {tenant_id} --scrape --max 5 --rag</code>
      </div>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Option B: Schedule It</h3>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Set up a schedule so new meetings are processed automatically. Go to the
        Admin dashboard and configure a processing schedule, or use the API:
      </p>
      <div style="background-color:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:16px;margin:12px 0;">
        <code style="display:block;background:#1e293b;color:#38bdf8;padding:12px;border-radius:4px;font-size:12px;white-space:pre;overflow-x:auto;">curl -X POST {API_BASE_URL}/api/v1/scheduler/schedules \\
  -H "X-API-Key: {api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"cron": "0 20 * * 1-5", "max_clips": 10}}'</code>
      </div>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">What You Get</h3>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        For each meeting, CivicLens produces:
      </p>
      <ul style="color:#374151;font-size:14px;line-height:1.8;padding-left:20px;">
        <li>Full transcript with timestamps</li>
        <li>AI-generated narrative summary</li>
        <li>Structured data: votes, financial items, attendance, public comments</li>
        <li>Topic extraction and agenda/minutes parsing</li>
      </ul>

      {_cta_button("Go to Admin Dashboard", BASE_URL + "/admin", tenant_id, 2)}
    """
    return subject, _render_onboarding_email(content, tenant_id, 2)


def _template_integrations(
    tenant_id: str, admin_email: str, plan: str, api_key: str
) -> tuple[str, str]:
    """Day 3: Set up Slack/Teams integration guide."""
    subject = "Connect CivicLens to Slack or Teams"
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Stay in the Loop with Slack &amp; Teams</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Bring meeting intelligence right into the tools your team already uses.
        Get notified about new meetings, ask questions, and search -- all without
        leaving your chat app.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Slack Integration</h3>
      <div style="background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:16px;margin:12px 0;">
        <p style="color:#374151;font-size:14px;line-height:1.6;margin:0;">
          <strong>Slash commands:</strong><br>
          <code style="background:#f3f4f6;padding:2px 6px;border-radius:3px;">/civiclens ask What was the vote on the budget?</code><br>
          <code style="background:#f3f4f6;padding:2px 6px;border-radius:3px;">/civiclens search short-term rentals</code>
        </p>
      </div>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Microsoft Teams</h3>
      <div style="background-color:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:16px;margin:12px 0;">
        <p style="color:#374151;font-size:14px;line-height:1.6;margin:0;">
          <strong>@CivicLens mentions:</strong><br>
          Ask questions directly in any channel where the bot is installed.
          Responses include vote tallies, financial amounts, and source citations.
        </p>
      </div>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Zapier &amp; Webhooks</h3>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Connect CivicLens to 5,000+ apps via Zapier triggers, or set up custom
        webhooks to get notified when meetings are processed, votes are detected,
        or financial items appear.
      </p>

      {_cta_button("Set Up Integrations", BASE_URL + "/admin", tenant_id, 3)}
    """
    return subject, _render_onboarding_email(content, tenant_id, 3)


def _template_advanced_features(
    tenant_id: str, admin_email: str, plan: str, api_key: str
) -> tuple[str, str]:
    """Day 7: Explore advanced features."""
    subject = "Unlock advanced features in CivicLens"
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Go Deeper with Advanced Features</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        You have been using CivicLens for a week now. Here are some powerful features
        you might not have tried yet.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Vote Tracking &amp; Policy Alerts</h3>
      <p style="color:#374151;font-size:14px;line-height:1.6;">
        Track how council members vote on specific issues. Set up alerts to get
        notified when keywords appear in votes, financial items, or public comments.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Sentiment Analysis</h3>
      <p style="color:#374151;font-size:14px;line-height:1.6;">
        Understand public opinion at a glance. CivicLens analyzes public comments
        to identify sentiment trends, hot topics, and community concerns.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Data Export &amp; FOIA</h3>
      <p style="color:#374151;font-size:14px;line-height:1.6;">
        Export meeting data in bulk as JSON or CSV. Use the FOIA handler to
        quickly find and package records responsive to public records requests.
      </p>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Email Digests</h3>
      <p style="color:#374151;font-size:14px;line-height:1.6;">
        Subscribe your team to daily or weekly email digests with meeting summaries,
        key votes, and financial highlights -- filtered by topic or committee.
      </p>

      {_cta_button("Explore Features", BASE_URL + "/analytics", tenant_id, 4)}
    """
    return subject, _render_onboarding_email(content, tenant_id, 4)


def _template_checkin(
    tenant_id: str, admin_email: str, plan: str, api_key: str
) -> tuple[str, str]:
    """Day 14: Check-in + upgrade nudge for starter plans."""
    subject = "How's it going with CivicLens?"

    upgrade_section = ""
    if plan == "starter":
        upgrade_section = f"""
          <div style="background-color:#fefce8;border:1px solid #fde68a;border-radius:6px;padding:20px;margin:20px 0;">
            <h3 style="margin:0 0 8px;color:#92400e;font-size:16px;">Ready for More?</h3>
            <p style="color:#78350f;font-size:14px;line-height:1.6;margin:0 0 12px;">
              Your Starter plan includes 100 queries per month. Upgrade to
              <strong>Pro</strong> for 1,000 queries/month, priority support,
              and advanced analytics.
            </p>
            {_cta_button("View Plans", BASE_URL + "/admin", tenant_id, 5)}
          </div>
        """

    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Two Weeks In -- How's It Going?</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        You have had CivicLens for two weeks now. We hope it is saving you time
        and making meeting records more accessible. Here is a quick recap of what
        is available:
      </p>

      <table style="width:100%;border-collapse:collapse;margin:20px 0;">
        <tr>
          <td style="padding:12px 16px;background:#f0fdf4;border-radius:6px 0 0 0;font-size:14px;color:#374151;">
            RAG-powered Q&amp;A across all meetings
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;background:#eff6ff;font-size:14px;color:#374151;">
            Vote tracking and policy alerts
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;background:#faf5ff;font-size:14px;color:#374151;">
            Slack, Teams, and Zapier integrations
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;background:#fefce8;font-size:14px;color:#374151;">
            Sentiment analysis and email digests
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;background:#fef2f2;border-radius:0 0 6px 6px;font-size:14px;color:#374151;">
            Data export and FOIA request handler
          </td>
        </tr>
      </table>

      {upgrade_section}

      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Have questions or feedback? Reply to this email -- we read every message.
      </p>

      {_cta_button("Open Dashboard", BASE_URL, tenant_id, 5)}
    """
    return subject, _render_onboarding_email(content, tenant_id, 5)


# Template dispatch table
_TEMPLATES = {
    "welcome_quickstart": _template_welcome_quickstart,
    "first_meeting": _template_first_meeting,
    "integrations": _template_integrations,
    "advanced_features": _template_advanced_features,
    "checkin": _template_checkin,
}


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_SEQUENCES_TABLE = """
CREATE TABLE IF NOT EXISTS onboarding_sequences (
    tenant_id TEXT PRIMARY KEY,
    admin_email TEXT NOT NULL,
    plan TEXT NOT NULL,
    api_key TEXT NOT NULL DEFAULT '',
    paused INTEGER NOT NULL DEFAULT 0,
    unsubscribed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_CREATE_EMAILS_TABLE = """
CREATE TABLE IF NOT EXISTS onboarding_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    email_number INTEGER NOT NULL,
    template_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    sent_at TEXT,
    skipped INTEGER NOT NULL DEFAULT 0,
    opened_at TEXT,
    clicked_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES onboarding_sequences(tenant_id),
    UNIQUE(tenant_id, email_number)
);
"""


# ---------------------------------------------------------------------------
# OnboardingEmailManager
# ---------------------------------------------------------------------------


class OnboardingEmailManager:
    """Manages onboarding email drip campaigns for new tenants."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_SEQUENCES_TABLE)
        self._conn.execute(_CREATE_EMAILS_TABLE)
        self._conn.commit()
        self._notifier = NotificationManager()

    # ------------------------------------------------------------------
    # Sequence management
    # ------------------------------------------------------------------

    def schedule_sequence(
        self,
        tenant_id: str,
        admin_email: str,
        plan: str,
        api_key: str = "",
    ) -> SequenceStatus:
        """Schedule the full 5-email onboarding sequence for a new tenant.

        If a sequence already exists for this tenant, it is returned as-is
        without modification (idempotent).
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Check for existing sequence
        existing = self._conn.execute(
            "SELECT tenant_id FROM onboarding_sequences WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()

        if existing:
            logger.info(
                "Onboarding sequence already exists for tenant %s, returning status",
                tenant_id,
            )
            return self.get_sequence_status(tenant_id)

        # Create sequence record
        self._conn.execute(
            """INSERT INTO onboarding_sequences
               (tenant_id, admin_email, plan, api_key, paused, unsubscribed, created_at, updated_at)
               VALUES (?, ?, ?, ?, 0, 0, ?, ?)""",
            (tenant_id, admin_email.strip().lower(), plan, api_key, now_iso, now_iso),
        )

        # Schedule each email
        for email_number, delay_days, subject_template, template_key in SEQUENCE:
            scheduled_at = (now + timedelta(days=delay_days)).isoformat()
            self._conn.execute(
                """INSERT INTO onboarding_emails
                   (tenant_id, email_number, template_key, subject, scheduled_at, skipped)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (tenant_id, email_number, template_key, subject_template, scheduled_at),
            )

        self._conn.commit()
        logger.info(
            "Scheduled %d onboarding emails for tenant %s (%s)",
            TOTAL_EMAILS,
            tenant_id,
            admin_email,
        )
        return self.get_sequence_status(tenant_id)

    def get_sequence_status(self, tenant_id: str) -> SequenceStatus:
        """Return the full status of the onboarding sequence for a tenant."""
        seq = self._conn.execute(
            "SELECT * FROM onboarding_sequences WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()

        if not seq:
            return SequenceStatus(
                tenant_id=tenant_id,
                admin_email="",
                plan="",
                paused=False,
                created_at="",
                emails=[],
            )

        emails = self._conn.execute(
            """SELECT email_number, template_key, subject, scheduled_at,
                      sent_at, skipped, opened_at, clicked_at
               FROM onboarding_emails
               WHERE tenant_id = ?
               ORDER BY email_number""",
            (tenant_id,),
        ).fetchall()

        email_list = []
        for e in emails:
            status = "pending"
            if e["skipped"]:
                status = "skipped"
            elif e["sent_at"]:
                status = "sent"

            email_list.append(
                {
                    "email_number": e["email_number"],
                    "template_key": e["template_key"],
                    "subject": e["subject"],
                    "scheduled_at": e["scheduled_at"],
                    "sent_at": e["sent_at"],
                    "skipped": bool(e["skipped"]),
                    "opened_at": e["opened_at"],
                    "clicked_at": e["clicked_at"],
                    "status": status,
                }
            )

        return SequenceStatus(
            tenant_id=tenant_id,
            admin_email=seq["admin_email"],
            plan=seq["plan"],
            paused=bool(seq["paused"]),
            created_at=seq["created_at"],
            emails=email_list,
        )

    def skip_email(self, tenant_id: str, email_number: int) -> bool:
        """Skip a specific email in the sequence. Returns True if the email was found and updated."""
        if email_number < 1 or email_number > TOTAL_EMAILS:
            raise ValueError(
                f"email_number must be between 1 and {TOTAL_EMAILS}"
            )

        cur = self._conn.execute(
            """UPDATE onboarding_emails
               SET skipped = 1
               WHERE tenant_id = ? AND email_number = ? AND sent_at IS NULL""",
            (tenant_id, email_number),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.info(
                "Skipped onboarding email %d for tenant %s",
                email_number,
                tenant_id,
            )
        return cur.rowcount > 0

    def pause_sequence(self, tenant_id: str) -> bool:
        """Pause the onboarding sequence for a tenant. No further emails will be sent until resumed."""
        cur = self._conn.execute(
            "UPDATE onboarding_sequences SET paused = 1, updated_at = ? WHERE tenant_id = ?",
            (datetime.now(timezone.utc).isoformat(), tenant_id),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.info("Paused onboarding sequence for tenant %s", tenant_id)
        return cur.rowcount > 0

    def resume_sequence(self, tenant_id: str) -> bool:
        """Resume a paused onboarding sequence."""
        cur = self._conn.execute(
            "UPDATE onboarding_sequences SET paused = 0, updated_at = ? WHERE tenant_id = ?",
            (datetime.now(timezone.utc).isoformat(), tenant_id),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.info("Resumed onboarding sequence for tenant %s", tenant_id)
        return cur.rowcount > 0

    def unsubscribe(self, tenant_id: str) -> bool:
        """Unsubscribe a tenant from the onboarding sequence entirely."""
        cur = self._conn.execute(
            "UPDATE onboarding_sequences SET unsubscribed = 1, paused = 1, updated_at = ? WHERE tenant_id = ?",
            (datetime.now(timezone.utc).isoformat(), tenant_id),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.info("Unsubscribed tenant %s from onboarding emails", tenant_id)
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def record_open(self, tenant_id: str, email_number: int) -> bool:
        """Record an email open event (first open only)."""
        cur = self._conn.execute(
            """UPDATE onboarding_emails
               SET opened_at = ?
               WHERE tenant_id = ? AND email_number = ? AND opened_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), tenant_id, email_number),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_click(self, tenant_id: str, email_number: int) -> bool:
        """Record an email click event (first click only)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        # Also counts as an open if not yet recorded
        self._conn.execute(
            """UPDATE onboarding_emails
               SET opened_at = ?
               WHERE tenant_id = ? AND email_number = ? AND opened_at IS NULL""",
            (now_iso, tenant_id, email_number),
        )
        cur = self._conn.execute(
            """UPDATE onboarding_emails
               SET clicked_at = ?
               WHERE tenant_id = ? AND email_number = ? AND clicked_at IS NULL""",
            (now_iso, tenant_id, email_number),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Send due emails
    # ------------------------------------------------------------------

    def _is_digest_unsubscribed(self, tenant_id: str, email: str) -> bool:
        """Check if this email has unsubscribed from digest emails.

        Respects the existing digest unsubscribe preference as a signal
        that the user does not want automated emails.
        """
        try:
            from api.database import get_database

            db = get_database()
            row = db.fetch_one(
                "SELECT active FROM digest_subscribers WHERE tenant_id = ? AND email = ? AND active = 0",
                (tenant_id, email),
            )
            return row is not None
        except Exception:
            # If digest system is not initialized, do not block onboarding
            return False

    def send_due_emails(self) -> SendResult:
        """Send all onboarding emails that are due.

        Called periodically by the scheduler (e.g., every hour or every 15 minutes).
        Iterates over all active, non-paused sequences and sends any emails whose
        scheduled_at is in the past and have not been sent or skipped.
        """
        result = SendResult()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Find all active, non-paused, non-unsubscribed sequences
        sequences = self._conn.execute(
            """SELECT s.tenant_id, s.admin_email, s.plan, s.api_key
               FROM onboarding_sequences s
               WHERE s.paused = 0 AND s.unsubscribed = 0"""
        ).fetchall()

        for seq in sequences:
            tenant_id = seq["tenant_id"]
            admin_email = seq["admin_email"]
            plan = seq["plan"]
            api_key = seq["api_key"]

            # Check digest unsubscribe
            if self._is_digest_unsubscribed(tenant_id, admin_email):
                logger.debug(
                    "Skipping onboarding for tenant %s: admin email unsubscribed from digests",
                    tenant_id,
                )
                result.skipped += 1
                continue

            # Find due, unsent, unskipped emails
            due_emails = self._conn.execute(
                """SELECT email_number, template_key, subject
                   FROM onboarding_emails
                   WHERE tenant_id = ? AND sent_at IS NULL AND skipped = 0
                         AND scheduled_at <= ?
                   ORDER BY email_number""",
                (tenant_id, now_iso),
            ).fetchall()

            for email_row in due_emails:
                email_number = email_row["email_number"]
                template_key = email_row["template_key"]

                template_fn = _TEMPLATES.get(template_key)
                if not template_fn:
                    logger.error(
                        "Unknown onboarding template '%s' for tenant %s email %d",
                        template_key,
                        tenant_id,
                        email_number,
                    )
                    result.failed += 1
                    continue

                try:
                    subject, html_body = template_fn(
                        tenant_id, admin_email, plan, api_key
                    )
                except Exception:
                    logger.exception(
                        "Failed to render onboarding email %d for tenant %s",
                        email_number,
                        tenant_id,
                    )
                    result.failed += 1
                    continue

                ok = self._notifier._send_raw(admin_email, subject, html_body)
                if ok:
                    self._conn.execute(
                        "UPDATE onboarding_emails SET sent_at = ? WHERE tenant_id = ? AND email_number = ?",
                        (now_iso, tenant_id, email_number),
                    )
                    self._conn.commit()
                    result.sent += 1
                    logger.info(
                        "Sent onboarding email %d (%s) to %s for tenant %s",
                        email_number,
                        template_key,
                        admin_email,
                        tenant_id,
                    )
                else:
                    result.failed += 1
                    logger.warning(
                        "Failed to send onboarding email %d to %s for tenant %s",
                        email_number,
                        admin_email,
                        tenant_id,
                    )

        return result

    def close(self):
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[OnboardingEmailManager] = None


def init_onboarding_emails(output_dir: str) -> OnboardingEmailManager:
    """Initialize the global OnboardingEmailManager singleton."""
    global _manager
    db_path = os.path.join(output_dir, "onboarding.db")
    _manager = OnboardingEmailManager(db_path)
    logger.info("OnboardingEmailManager initialized: %s", db_path)
    return _manager


def get_onboarding_manager() -> OnboardingEmailManager:
    """Return the global OnboardingEmailManager singleton."""
    if _manager is None:
        raise RuntimeError(
            "OnboardingEmailManager not initialized. Call init_onboarding_emails() at startup."
        )
    return _manager
