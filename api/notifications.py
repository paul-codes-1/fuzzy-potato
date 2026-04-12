"""Email notification system for the CivicLens multi-tenant platform."""

import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SMTP configuration from environment
# ---------------------------------------------------------------------------

_DEFAULT_FROM = "CivicLens <noreply@civiclens.io>"


def _smtp_config() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "localhost"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "username": os.environ.get("SMTP_USERNAME", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes"),
        "from_address": os.environ.get("SMTP_FROM", _DEFAULT_FROM),
    }


# ---------------------------------------------------------------------------
# Base HTML template
# ---------------------------------------------------------------------------

_BASE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
  <!-- Header -->
  <tr>
    <td style="background-color:#1a56db;padding:24px 32px;">
      <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:600;">CivicLens</h1>
    </td>
  </tr>
  <!-- Body -->
  <tr>
    <td style="padding:32px;">
      {content}
    </td>
  </tr>
  <!-- Footer -->
  <tr>
    <td style="padding:16px 32px;background-color:#f9fafb;border-top:1px solid #e5e7eb;">
      <p style="margin:0;color:#6b7280;font-size:12px;line-height:1.5;">
        CivicLens Meeting Intelligence Platform<br>
        {footer_extra}
      </p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _render(content: str, footer_extra: str = "") -> str:
    return _BASE_TEMPLATE.format(content=content, footer_extra=footer_extra)


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def _welcome_email(tenant_name: str, api_key: str) -> tuple[str, str]:
    """Returns (subject, html_body) for a welcome email."""
    subject = "Welcome to CivicLens"
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Welcome, {tenant_name}!</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Your CivicLens account is ready. You can start querying meeting intelligence data
        right away using the API.
      </p>
      <div style="background-color:#f0f4ff;border:1px solid #dbe4ff;border-radius:6px;padding:16px;margin:20px 0;">
        <p style="margin:0 0 8px;color:#374151;font-size:13px;font-weight:600;">Your API Key</p>
        <code style="display:block;background:#1e293b;color:#38bdf8;padding:12px;border-radius:4px;font-size:13px;word-break:break-all;">
          {api_key}
        </code>
        <p style="margin:8px 0 0;color:#6b7280;font-size:12px;">
          Keep this key secure. You can rotate it at any time from your dashboard.
        </p>
      </div>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Include the key in the <code style="background:#f3f4f6;padding:2px 6px;border-radius:3px;">X-API-Key</code>
        header with every request.
      </p>
    """
    return subject, _render(content)


def _meeting_processed_email(
    tenant_name: str,
    clip_id: str,
    meeting_title: str,
    meeting_date: str,
    topics: list[str],
) -> tuple[str, str]:
    """Returns (subject, html_body) for a new meeting processed notification."""
    subject = f"New meeting processed: {meeting_title}"
    topics_html = "".join(
        f'<span style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:2px 10px;border-radius:12px;font-size:12px;margin:2px 4px 2px 0;">{t}</span>'
        for t in topics
    )
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">New Meeting Processed</h2>
      <table style="width:100%;border-collapse:collapse;margin:16px 0;">
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;width:100px;">Title</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;font-weight:500;">{meeting_title}</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;">Date</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{meeting_date}</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;">Clip ID</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{clip_id}</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;vertical-align:top;">Topics</td>
          <td style="padding:8px 0;">{topics_html}</td>
        </tr>
      </table>
      <p style="color:#374151;font-size:14px;line-height:1.6;">
        The transcript, summary, and extracted facts are now available via the API.
      </p>
    """
    return subject, _render(content)


def _weekly_digest_email(
    tenant_name: str,
    period_start: str,
    period_end: str,
    meetings_count: int,
    votes_count: int,
    financial_items_count: int,
    queries_count: int,
    top_topics: list[str],
) -> tuple[str, str]:
    """Returns (subject, html_body) for a weekly digest email."""
    subject = f"CivicLens Weekly Digest ({period_start} - {period_end})"
    topics_html = "".join(
        f'<li style="color:#374151;font-size:14px;padding:2px 0;">{t}</li>'
        for t in top_topics[:10]
    ) or '<li style="color:#6b7280;font-size:14px;">No topics this week</li>'

    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Weekly Digest</h2>
      <p style="color:#6b7280;font-size:14px;margin:0 0 20px;">{period_start} &mdash; {period_end}</p>

      <table style="width:100%;border-collapse:collapse;">
        <tr>
          <td style="padding:16px;text-align:center;background:#f0fdf4;border-radius:6px 0 0 6px;">
            <div style="font-size:28px;font-weight:700;color:#15803d;">{meetings_count}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:4px;">Meetings</div>
          </td>
          <td style="padding:16px;text-align:center;background:#eff6ff;">
            <div style="font-size:28px;font-weight:700;color:#1d4ed8;">{votes_count}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:4px;">Votes</div>
          </td>
          <td style="padding:16px;text-align:center;background:#fefce8;">
            <div style="font-size:28px;font-weight:700;color:#a16207;">${financial_items_count}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:4px;">Financial Items</div>
          </td>
          <td style="padding:16px;text-align:center;background:#faf5ff;border-radius:0 6px 6px 0;">
            <div style="font-size:28px;font-weight:700;color:#7c3aed;">{queries_count}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:4px;">API Queries</div>
          </td>
        </tr>
      </table>

      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">Top Topics</h3>
      <ul style="margin:0;padding-left:20px;">
        {topics_html}
      </ul>
    """
    return subject, _render(content)


def _usage_alert_email(
    tenant_name: str,
    plan: str,
    usage_count: int,
    limit: int,
    percent: int,
) -> tuple[str, str]:
    """Returns (subject, html_body) for a usage alert (80% or 100%)."""
    at_limit = percent >= 100
    if at_limit:
        subject = f"CivicLens: Query limit reached ({plan} plan)"
        bar_color = "#dc2626"
        message = (
            "You have reached your monthly query limit. "
            "Upgrade your plan to continue making queries."
        )
    else:
        subject = f"CivicLens: {percent}% of query limit used ({plan} plan)"
        bar_color = "#f59e0b"
        message = (
            f"You have used {percent}% of your monthly query allowance. "
            "Consider upgrading to avoid interruptions."
        )

    bar_width = min(percent, 100)
    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Usage Alert</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">{message}</p>

      <div style="margin:20px 0;">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
          <span style="color:#374151;font-size:14px;font-weight:500;">{usage_count} / {limit} queries</span>
          <span style="color:#6b7280;font-size:14px;">{percent}%</span>
        </div>
        <div style="background:#e5e7eb;border-radius:4px;height:12px;overflow:hidden;">
          <div style="background:{bar_color};height:100%;width:{bar_width}%;border-radius:4px;"></div>
        </div>
      </div>

      <p style="color:#6b7280;font-size:13px;">
        Current plan: <strong>{plan}</strong>
      </p>
    """
    return subject, _render(content)


def _payment_failed_email(
    tenant_name: str,
    plan: str,
    failure_reason: str,
) -> tuple[str, str]:
    """Returns (subject, html_body) for a payment failure notification."""
    subject = "CivicLens: Payment failed - action required"
    content = f"""
      <h2 style="margin:0 0 16px;color:#dc2626;font-size:22px;">Payment Failed</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        We were unable to process your payment for the <strong>{plan}</strong> plan.
      </p>

      <div style="background-color:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:16px;margin:20px 0;">
        <p style="margin:0;color:#991b1b;font-size:14px;">
          <strong>Reason:</strong> {failure_reason}
        </p>
      </div>

      <p style="color:#374151;font-size:15px;line-height:1.6;">
        Please update your payment method to avoid service interruption.
        Your account will remain active for 7 days while we retry the charge.
      </p>
    """
    return subject, _render(content)


def _saved_search_alert_email(
    tenant_name: str,
    saved_search_name: str,
    search_type: str,
    query_text: str,
    summary_lines: list[str],
    result_count: Optional[int] = None,
    action_url: Optional[str] = None,
) -> tuple[str, str]:
    """Returns (subject, html_body) for a saved-search alert."""
    subject = f"CivicLens alert: {saved_search_name}"
    safe_name = html.escape(saved_search_name)
    safe_query = html.escape(query_text)
    safe_type = html.escape(search_type.replace("_", " "))
    bullets = "".join(
        f'<li style="color:#374151;font-size:14px;line-height:1.6;padding:2px 0;">{html.escape(line)}</li>'
        for line in summary_lines
    ) or '<li style="color:#6b7280;font-size:14px;">No new summary details were available.</li>'
    cta = ""
    if action_url:
        safe_url = html.escape(action_url, quote=True)
        cta = (
            f'<p style="margin:24px 0 0;">'
            f'<a href="{safe_url}" '
            f'style="display:inline-block;background:#1a56db;color:#ffffff;text-decoration:none;'
            f'padding:10px 16px;border-radius:6px;font-size:14px;font-weight:600;">'
            f'Open Saved Searches</a></p>'
        )

    content = f"""
      <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Saved Search Alert</h2>
      <p style="color:#374151;font-size:15px;line-height:1.6;">
        <strong>{safe_name}</strong> matched its scheduled alert cadence for <strong>{html.escape(tenant_name)}</strong>.
      </p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0;">
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;width:120px;">Search type</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{safe_type}</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;">Latest match count</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{result_count if result_count is not None else "Not run"}</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;vertical-align:top;">Query</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{safe_query}</td>
        </tr>
      </table>
      <h3 style="margin:24px 0 8px;color:#111827;font-size:16px;">What CivicLens found</h3>
      <ul style="margin:0;padding-left:20px;">
        {bullets}
      </ul>
      {cta}
    """
    return subject, _render(content)


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES = {
    "welcome": _welcome_email,
    "meeting_processed": _meeting_processed_email,
    "weekly_digest": _weekly_digest_email,
    "usage_alert": _usage_alert_email,
    "payment_failed": _payment_failed_email,
    "saved_search_alert": _saved_search_alert_email,
}


# ---------------------------------------------------------------------------
# NotificationManager
# ---------------------------------------------------------------------------

class NotificationManager:
    """Sends email notifications using SMTP with HTML templates."""

    def __init__(self, smtp_config: Optional[dict] = None):
        self._config = smtp_config or _smtp_config()

    def _send_raw(self, to: str, subject: str, html_body: str) -> bool:
        """Send an HTML email via SMTP. Returns True on success."""
        cfg = self._config
        msg = MIMEMultipart("alternative")
        msg["From"] = cfg["from_address"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            if cfg["use_tls"]:
                server = smtplib.SMTP(cfg["host"], cfg["port"])
                server.ehlo()
                server.starttls()
            else:
                server = smtplib.SMTP(cfg["host"], cfg["port"])
                server.ehlo()

            if cfg["username"]:
                server.login(cfg["username"], cfg["password"])

            server.sendmail(cfg["from_address"], [to], msg.as_string())
            server.quit()
            logger.info("Email sent: to=%s subject=%s", to, subject)
            return True

        except Exception:
            logger.exception("Failed to send email: to=%s subject=%s", to, subject)
            return False

    # -- Public convenience methods ------------------------------------------

    def send_welcome(self, to: str, tenant_name: str, api_key: str) -> bool:
        subject, html = _welcome_email(tenant_name, api_key)
        return self._send_raw(to, subject, html)

    def send_meeting_processed(
        self,
        to: str,
        tenant_name: str,
        clip_id: str,
        meeting_title: str,
        meeting_date: str,
        topics: Optional[list[str]] = None,
    ) -> bool:
        subject, html = _meeting_processed_email(
            tenant_name, clip_id, meeting_title, meeting_date, topics or []
        )
        return self._send_raw(to, subject, html)

    def send_weekly_digest(
        self,
        to: str,
        tenant_name: str,
        period_start: str,
        period_end: str,
        meetings_count: int = 0,
        votes_count: int = 0,
        financial_items_count: int = 0,
        queries_count: int = 0,
        top_topics: Optional[list[str]] = None,
    ) -> bool:
        subject, html = _weekly_digest_email(
            tenant_name, period_start, period_end,
            meetings_count, votes_count, financial_items_count,
            queries_count, top_topics or [],
        )
        return self._send_raw(to, subject, html)

    def send_usage_alert(
        self,
        to: str,
        tenant_name: str,
        plan: str,
        usage_count: int,
        limit: int,
        percent: int,
    ) -> bool:
        subject, html = _usage_alert_email(tenant_name, plan, usage_count, limit, percent)
        return self._send_raw(to, subject, html)

    def send_payment_failed(
        self,
        to: str,
        tenant_name: str,
        plan: str,
        failure_reason: str,
    ) -> bool:
        subject, html = _payment_failed_email(tenant_name, plan, failure_reason)
        return self._send_raw(to, subject, html)

    def send_saved_search_alert(
        self,
        to: str,
        tenant_name: str,
        saved_search_name: str,
        search_type: str,
        query_text: str,
        summary_lines: Optional[list[str]] = None,
        result_count: Optional[int] = None,
        action_url: Optional[str] = None,
    ) -> bool:
        subject, html = _saved_search_alert_email(
            tenant_name=tenant_name,
            saved_search_name=saved_search_name,
            search_type=search_type,
            query_text=query_text,
            summary_lines=summary_lines or [],
            result_count=result_count,
            action_url=action_url,
        )
        return self._send_raw(to, subject, html)

    def send_template(self, to: str, template_name: str, **kwargs) -> bool:
        """Send a notification using a named template.

        Example:
            manager.send_template("user@example.com", "welcome",
                                  tenant_name="Acme", api_key="mra_xxx")
        """
        template_fn = TEMPLATES.get(template_name)
        if not template_fn:
            raise ValueError(
                f"Unknown template '{template_name}'. "
                f"Available: {', '.join(sorted(TEMPLATES.keys()))}"
            )
        subject, html = template_fn(**kwargs)
        return self._send_raw(to, subject, html)
