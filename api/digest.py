"""Automated email digest system for the CivicLens multi-tenant platform.

Subscribers receive daily or weekly email summaries of meetings processed
for their tenant, filtered by meeting body and topic preferences.

Features:
- SQLite-backed subscriber management (via unified DatabaseManager)
- Configurable frequency: daily or weekly
- Meeting body and topic keyword filtering
- HTML email generation with inline CSS (email-safe)
- One-click unsubscribe tokens (CAN-SPAM compliant)
- Double opt-in with confirmation tokens
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from api.database import get_database
from api.notifications import NotificationManager, _render

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_FREQUENCIES = {"daily", "weekly"}
UNSUBSCRIBE_SECRET = os.environ.get("DIGEST_UNSUBSCRIBE_SECRET", "civiclens-digest-unsub-default")
BASE_URL = os.environ.get("CIVICLENS_BASE_URL", "http://localhost:5173")
API_BASE_URL = os.environ.get("CIVICLENS_API_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _generate_token() -> str:
    """Generate a cryptographic random token."""
    return secrets.token_urlsafe(32)


def _generate_unsubscribe_token(subscriber_id: str) -> str:
    """Generate a stable HMAC-based unsubscribe token for a subscriber."""
    return hmac.new(
        UNSUBSCRIBE_SECRET.encode(),
        subscriber_id.encode(),
        hashlib.sha256,
    ).hexdigest()


def _verify_unsubscribe_token(subscriber_id: str, token: str) -> bool:
    """Verify an unsubscribe token matches the subscriber."""
    expected = _generate_unsubscribe_token(subscriber_id)
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# DigestManager
# ---------------------------------------------------------------------------

class DigestManager:
    """Manages digest subscriptions, generation, and delivery."""

    def __init__(self, output_dir: str = "."):
        self._output_dir = output_dir
        self._notifier = NotificationManager()

    @property
    def _db(self):
        return get_database()

    # ------------------------------------------------------------------
    # Subscriber CRUD
    # ------------------------------------------------------------------

    def subscribe(
        self,
        tenant_id: str,
        email: str,
        frequency: str = "weekly",
        meeting_bodies: Optional[list[str]] = None,
        topics: Optional[list[str]] = None,
    ) -> dict:
        """Create a new digest subscription (pending confirmation).

        Returns subscriber info including a confirmation token for double opt-in.
        """
        email = email.strip().lower()
        if frequency not in VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of: {', '.join(sorted(VALID_FREQUENCIES))}")

        # Check for existing subscription
        existing = self._db.fetch_one(
            "SELECT id, active, confirmed FROM digest_subscribers WHERE tenant_id = ? AND email = ?",
            (tenant_id, email),
        )

        if existing:
            if existing["active"] and existing["confirmed"]:
                return {
                    "status": "already_subscribed",
                    "subscriber_id": existing["id"],
                    "message": "This email is already subscribed to the digest.",
                }
            # Reactivate if previously unsubscribed
            confirm_token = _generate_token()
            self._db.execute(
                """UPDATE digest_subscribers
                   SET frequency = ?, meeting_bodies = ?, topics = ?,
                       active = 1, confirmed = 0, confirm_token = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    frequency,
                    json.dumps(meeting_bodies or []),
                    json.dumps(topics or []),
                    confirm_token,
                    datetime.now(timezone.utc).isoformat(),
                    existing["id"],
                ),
            )
            self._send_confirmation_email(email, tenant_id, existing["id"], confirm_token)
            return {
                "status": "resubscribed_pending_confirmation",
                "subscriber_id": existing["id"],
                "message": "Check your email to confirm your subscription.",
            }

        subscriber_id = secrets.token_urlsafe(16)
        confirm_token = _generate_token()
        now = datetime.now(timezone.utc).isoformat()

        self._db.execute(
            """INSERT INTO digest_subscribers
               (id, tenant_id, email, frequency, meeting_bodies, topics,
                active, confirmed, confirm_token, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)""",
            (
                subscriber_id,
                tenant_id,
                email,
                frequency,
                json.dumps(meeting_bodies or []),
                json.dumps(topics or []),
                confirm_token,
                now,
                now,
            ),
        )

        self._send_confirmation_email(email, tenant_id, subscriber_id, confirm_token)

        return {
            "status": "pending_confirmation",
            "subscriber_id": subscriber_id,
            "message": "Check your email to confirm your subscription.",
        }

    def confirm(self, subscriber_id: str, token: str) -> dict:
        """Confirm a subscription via double opt-in token."""
        row = self._db.fetch_one(
            "SELECT id, confirm_token, confirmed FROM digest_subscribers WHERE id = ?",
            (subscriber_id,),
        )
        if not row:
            return {"status": "error", "message": "Subscription not found."}

        if row["confirmed"]:
            return {"status": "already_confirmed", "message": "Subscription already confirmed."}

        if not hmac.compare_digest(str(row["confirm_token"]), token):
            return {"status": "error", "message": "Invalid confirmation token."}

        self._db.execute(
            "UPDATE digest_subscribers SET confirmed = 1, confirm_token = NULL, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), subscriber_id),
        )
        return {"status": "confirmed", "message": "Your subscription is now active."}

    def unsubscribe_by_token(self, token: str) -> dict:
        """One-click unsubscribe via token (CAN-SPAM compliant)."""
        # Token encodes subscriber_id as first 22 chars, rest is HMAC
        # Actually, we look up by unsubscribe_token stored in the row
        row = self._db.fetch_one(
            "SELECT id, email FROM digest_subscribers WHERE unsubscribe_token = ? AND active = 1",
            (token,),
        )
        if not row:
            return {"status": "error", "message": "Invalid or expired unsubscribe link."}

        self._db.execute(
            "UPDATE digest_subscribers SET active = 0, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row["id"]),
        )
        return {
            "status": "unsubscribed",
            "message": f"{row['email']} has been unsubscribed from the digest.",
        }

    def get_preferences(self, tenant_id: str, email: str) -> Optional[dict]:
        """Get subscriber preferences."""
        email = email.strip().lower()
        row = self._db.fetch_one(
            "SELECT * FROM digest_subscribers WHERE tenant_id = ? AND email = ? AND active = 1",
            (tenant_id, email),
        )
        if not row:
            return None
        return {
            "subscriber_id": row["id"],
            "email": row["email"],
            "frequency": row["frequency"],
            "meeting_bodies": json.loads(row["meeting_bodies"]) if row["meeting_bodies"] else [],
            "topics": json.loads(row["topics"]) if row["topics"] else [],
            "confirmed": bool(row["confirmed"]),
            "created_at": row["created_at"],
        }

    def update_preferences(
        self,
        tenant_id: str,
        email: str,
        frequency: Optional[str] = None,
        meeting_bodies: Optional[list[str]] = None,
        topics: Optional[list[str]] = None,
    ) -> dict:
        """Update subscriber preferences."""
        email = email.strip().lower()
        row = self._db.fetch_one(
            "SELECT id FROM digest_subscribers WHERE tenant_id = ? AND email = ? AND active = 1",
            (tenant_id, email),
        )
        if not row:
            return {"status": "error", "message": "Subscriber not found."}

        updates = []
        params = []

        if frequency is not None:
            if frequency not in VALID_FREQUENCIES:
                raise ValueError(f"frequency must be one of: {', '.join(sorted(VALID_FREQUENCIES))}")
            updates.append("frequency = ?")
            params.append(frequency)

        if meeting_bodies is not None:
            updates.append("meeting_bodies = ?")
            params.append(json.dumps(meeting_bodies))

        if topics is not None:
            updates.append("topics = ?")
            params.append(json.dumps(topics))

        if not updates:
            return {"status": "no_changes", "message": "No changes specified."}

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(row["id"])

        self._db.execute(
            f"UPDATE digest_subscribers SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        return {"status": "updated", "message": "Preferences updated."}

    def list_subscribers(self, tenant_id: str, active_only: bool = True) -> list[dict]:
        """List all subscribers for a tenant."""
        query = "SELECT * FROM digest_subscribers WHERE tenant_id = ?"
        params = [tenant_id]
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY created_at DESC"

        rows = self._db.fetch_all(query, tuple(params))
        return [
            {
                "subscriber_id": r["id"],
                "email": r["email"],
                "frequency": r["frequency"],
                "meeting_bodies": json.loads(r["meeting_bodies"]) if r["meeting_bodies"] else [],
                "topics": json.loads(r["topics"]) if r["topics"] else [],
                "confirmed": bool(r["confirmed"]),
                "active": bool(r["active"]),
                "created_at": r["created_at"],
                "last_digest_at": r["last_digest_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Digest generation
    # ------------------------------------------------------------------

    def _get_meetings_since(self, since: str) -> list[dict]:
        """Load all meetings processed since the given ISO timestamp.

        Reads metadata.json and extracted_facts.json from the output directory.
        """
        clips_dir = os.path.join(self._output_dir, "clips")
        if not os.path.isdir(clips_dir):
            return []

        meetings = []
        for clip_name in os.listdir(clips_dir):
            clip_dir = os.path.join(clips_dir, clip_name)
            meta_path = os.path.join(clip_dir, "metadata.json")
            if not os.path.exists(meta_path):
                continue

            try:
                with open(meta_path) as f:
                    meta = json.load(f)

                processed_at = meta.get("processed_at", "")
                if processed_at and processed_at >= since:
                    facts_path = os.path.join(clip_dir, "extracted_facts.json")
                    facts = {}
                    if os.path.exists(facts_path):
                        with open(facts_path) as f:
                            facts = json.load(f)

                    meetings.append({
                        "clip_id": meta.get("clip_id"),
                        "title": meta.get("title", "Untitled"),
                        "date": meta.get("date", ""),
                        "meeting_body": meta.get("meeting_body", ""),
                        "topics": meta.get("topics", []),
                        "processed_at": processed_at,
                        "facts": facts,
                    })
            except (json.JSONDecodeError, KeyError, OSError):
                continue

        meetings.sort(key=lambda m: m["date"], reverse=True)
        return meetings

    def _filter_meetings_for_subscriber(
        self, meetings: list[dict], subscriber: dict
    ) -> list[dict]:
        """Filter meetings by subscriber's meeting body and topic preferences."""
        bodies = json.loads(subscriber["meeting_bodies"]) if subscriber["meeting_bodies"] else []
        topics = json.loads(subscriber["topics"]) if subscriber["topics"] else []

        filtered = meetings
        if bodies:
            filtered = [m for m in filtered if m["meeting_body"] in bodies]

        if topics:
            topic_set = {t.lower() for t in topics}
            filtered = [
                m for m in filtered
                if any(t.lower() in topic_set for t in m.get("topics", []))
                or any(
                    kw in (m.get("title", "") + " " + str(m.get("facts", {}))).lower()
                    for kw in topic_set
                )
            ]

        return filtered

    def generate_digest(
        self,
        tenant_id: str,
        subscriber: Optional[dict] = None,
        since: Optional[str] = None,
    ) -> Optional[dict]:
        """Generate a digest for a subscriber or for preview.

        Returns dict with subject, html_body, plain_text, meeting_count, or
        None if no meetings to report.
        """
        if since is None:
            if subscriber and subscriber.get("last_digest_at"):
                since = subscriber["last_digest_at"]
            else:
                # Default: last 7 days for weekly, last 1 day for daily
                freq = subscriber["frequency"] if subscriber else "weekly"
                days = 1 if freq == "daily" else 7
                since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        meetings = self._get_meetings_since(since)

        if subscriber:
            meetings = self._filter_meetings_for_subscriber(meetings, subscriber)

        if not meetings:
            return None

        # Extract highlights from facts
        all_votes = []
        all_financial = []
        all_public_comments = []
        highlights = []

        for m in meetings:
            facts = m.get("facts", {})

            votes = facts.get("motions_and_votes", [])
            for v in votes:
                all_votes.append({
                    **v,
                    "meeting_title": m["title"],
                    "meeting_date": m["date"],
                    "clip_id": m["clip_id"],
                })

            financial = facts.get("financial_items", [])
            for fi in financial:
                all_financial.append({
                    **fi,
                    "meeting_title": m["title"],
                    "meeting_date": m["date"],
                    "clip_id": m["clip_id"],
                })

            comments = facts.get("public_comments", [])
            for c in comments:
                all_public_comments.append({
                    **c,
                    "meeting_title": m["title"],
                    "clip_id": m["clip_id"],
                })

            contentious = facts.get("contentious_items", [])
            for item in contentious:
                highlights.append({
                    "text": item.get("description", item.get("title", "")),
                    "meeting_title": m["title"],
                    "clip_id": m["clip_id"],
                })

        # Unsubscribe token
        unsub_token = ""
        unsub_url = ""
        if subscriber:
            unsub_token = subscriber.get("unsubscribe_token", "")
            unsub_url = f"{API_BASE_URL}/api/v1/digest/unsubscribe?token={unsub_token}"

        # Build period string
        period_end = datetime.now(timezone.utc).strftime("%B %d, %Y")
        try:
            period_start = datetime.fromisoformat(since.replace("Z", "+00:00")).strftime("%B %d, %Y")
        except (ValueError, AttributeError):
            period_start = since[:10] if since else "Unknown"

        freq_label = "Daily" if (subscriber and subscriber.get("frequency") == "daily") else "Weekly"
        subject = f"CivicLens {freq_label} Digest ({period_start} - {period_end})"

        html_body = self._render_digest_email(
            meetings=meetings,
            votes=all_votes,
            financial_items=all_financial,
            public_comments=all_public_comments,
            highlights=highlights,
            period_start=period_start,
            period_end=period_end,
            unsub_url=unsub_url,
            freq_label=freq_label,
        )

        return {
            "subject": subject,
            "html_body": html_body,
            "meeting_count": len(meetings),
            "votes_count": len(all_votes),
            "financial_count": len(all_financial),
            "period_start": period_start,
            "period_end": period_end,
        }

    # ------------------------------------------------------------------
    # Send digests
    # ------------------------------------------------------------------

    def send_digests(self, tenant_id: str, frequency: str = "weekly") -> dict:
        """Send digests to all confirmed, active subscribers of the given frequency."""
        if frequency not in VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of: {', '.join(sorted(VALID_FREQUENCIES))}")

        subscribers = self._db.fetch_all(
            """SELECT * FROM digest_subscribers
               WHERE tenant_id = ? AND frequency = ? AND active = 1 AND confirmed = 1""",
            (tenant_id, frequency),
        )

        sent = 0
        failed = 0
        skipped = 0

        for sub in subscribers:
            digest = self.generate_digest(tenant_id, subscriber=sub)
            if digest is None:
                skipped += 1
                continue

            ok = self._notifier._send_raw(sub["email"], digest["subject"], digest["html_body"])
            if ok:
                sent += 1
                self._db.execute(
                    "UPDATE digest_subscribers SET last_digest_at = ?, updated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), sub["id"]),
                )
            else:
                failed += 1

        return {
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "total_subscribers": len(subscribers),
        }

    def send_immediate(self, tenant_id: str) -> dict:
        """Send an immediate digest to all active, confirmed subscribers (admin action)."""
        subscribers = self._db.fetch_all(
            """SELECT * FROM digest_subscribers
               WHERE tenant_id = ? AND active = 1 AND confirmed = 1""",
            (tenant_id,),
        )

        sent = 0
        failed = 0
        skipped = 0

        for sub in subscribers:
            digest = self.generate_digest(tenant_id, subscriber=sub)
            if digest is None:
                skipped += 1
                continue

            ok = self._notifier._send_raw(sub["email"], digest["subject"], digest["html_body"])
            if ok:
                sent += 1
                self._db.execute(
                    "UPDATE digest_subscribers SET last_digest_at = ?, updated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), sub["id"]),
                )
            else:
                failed += 1

        return {
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "total_subscribers": len(subscribers),
        }

    # ------------------------------------------------------------------
    # Email rendering
    # ------------------------------------------------------------------

    def _send_confirmation_email(
        self, email: str, tenant_id: str, subscriber_id: str, confirm_token: str
    ):
        """Send the double opt-in confirmation email."""
        confirm_url = f"{API_BASE_URL}/api/v1/digest/confirm?subscriber_id={subscriber_id}&token={confirm_token}"

        content = f"""
          <h2 style="margin:0 0 16px;color:#111827;font-size:22px;">Confirm Your Digest Subscription</h2>
          <p style="color:#374151;font-size:15px;line-height:1.6;">
            You requested a digest subscription for <strong>{email}</strong>.
            Click the button below to confirm and start receiving meeting digests.
          </p>
          <div style="text-align:center;margin:28px 0;">
            <a href="{confirm_url}"
               style="display:inline-block;background-color:#1a56db;color:#ffffff;
                      padding:14px 32px;border-radius:6px;font-size:15px;font-weight:600;
                      text-decoration:none;">
              Confirm Subscription
            </a>
          </div>
          <p style="color:#6b7280;font-size:13px;line-height:1.5;">
            If you did not request this, you can safely ignore this email.
          </p>
        """
        subject = "Confirm your CivicLens digest subscription"
        html = _render(content)
        self._notifier._send_raw(email, subject, html)

        # Store unsubscribe token now so it is ready when confirmed
        unsub_token = _generate_token()
        self._db.execute(
            "UPDATE digest_subscribers SET unsubscribe_token = ? WHERE id = ?",
            (unsub_token, subscriber_id),
        )

    def _render_digest_email(
        self,
        meetings: list[dict],
        votes: list[dict],
        financial_items: list[dict],
        public_comments: list[dict],
        highlights: list[dict],
        period_start: str,
        period_end: str,
        unsub_url: str,
        freq_label: str,
    ) -> str:
        """Render the HTML digest email with inline CSS."""

        # Meeting summary cards
        meeting_rows = ""
        for m in meetings[:10]:  # Cap at 10 meetings
            body_badge = (
                f'<span style="display:inline-block;background:#e0e7ff;color:#3730a3;'
                f'padding:2px 8px;border-radius:12px;font-size:11px;font-weight:500;">'
                f'{m["meeting_body"]}</span>'
            )
            topic_tags = "".join(
                f'<span style="display:inline-block;background:#f3f4f6;color:#374151;'
                f'padding:1px 6px;border-radius:8px;font-size:11px;margin:2px 2px 0 0;">{t}</span>'
                for t in m.get("topics", [])[:4]
            )
            archive_url = f"{BASE_URL}/meeting/{m['clip_id']}"
            meeting_rows += f"""
              <tr>
                <td style="padding:12px 0;border-bottom:1px solid #f3f4f6;">
                  <div style="font-size:14px;font-weight:600;color:#111827;">
                    <a href="{archive_url}" style="color:#1a56db;text-decoration:none;">{m['title']}</a>
                  </div>
                  <div style="margin-top:4px;">
                    <span style="color:#6b7280;font-size:13px;">{m['date']}</span>
                    {body_badge}
                  </div>
                  <div style="margin-top:4px;">{topic_tags}</div>
                </td>
              </tr>
            """

        # Votes section
        votes_html = ""
        if votes:
            vote_rows = ""
            for v in votes[:8]:
                outcome = v.get("outcome", "unknown")
                badge_bg = "#dcfce7" if outcome == "passed" else "#fef2f2"
                badge_color = "#166534" if outcome == "passed" else "#991b1b"
                badge_text = outcome.capitalize()
                desc = v.get("description", v.get("identifier", "Unknown item"))[:80]
                vote_detail = ""
                if v.get("ayes") is not None:
                    vote_detail = f' ({v.get("ayes", 0)}-{v.get("nays", 0)})'
                vote_rows += f"""
                  <tr>
                    <td style="padding:8px 0;border-bottom:1px solid #f9fafb;font-size:13px;color:#374151;">
                      {desc}{vote_detail}
                      <span style="display:inline-block;background:{badge_bg};color:{badge_color};
                                   padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;
                                   margin-left:6px;">{badge_text}</span>
                    </td>
                  </tr>
                """
            votes_html = f"""
              <h3 style="margin:24px 0 12px;color:#111827;font-size:16px;font-weight:600;">
                Key Votes ({len(votes)})
              </h3>
              <table style="width:100%;border-collapse:collapse;">{vote_rows}</table>
            """

        # Financial items section
        financial_html = ""
        if financial_items:
            fi_rows = ""
            for fi in financial_items[:6]:
                amount = fi.get("amount", "N/A")
                desc = fi.get("description", "")[:70]
                fi_type = fi.get("type", "")
                fi_rows += f"""
                  <tr>
                    <td style="padding:8px 0;border-bottom:1px solid #f9fafb;font-size:13px;color:#374151;">
                      {desc}
                    </td>
                    <td style="padding:8px 0;border-bottom:1px solid #f9fafb;font-size:13px;color:#111827;
                               font-weight:600;text-align:right;white-space:nowrap;">
                      {amount}
                    </td>
                  </tr>
                """
            financial_html = f"""
              <h3 style="margin:24px 0 12px;color:#111827;font-size:16px;font-weight:600;">
                Financial Items ({len(financial_items)})
              </h3>
              <table style="width:100%;border-collapse:collapse;">{fi_rows}</table>
            """

        # Public comments section
        comments_html = ""
        if public_comments:
            comment_items = ""
            for c in public_comments[:5]:
                speaker = c.get("speaker", "Unknown")
                topic = c.get("topic", "")
                summary = c.get("summary", "")[:100]
                comment_items += f"""
                  <li style="padding:6px 0;color:#374151;font-size:13px;">
                    <strong>{speaker}</strong>
                    {f' on {topic}' if topic else ''}:
                    {summary}
                  </li>
                """
            comments_html = f"""
              <h3 style="margin:24px 0 12px;color:#111827;font-size:16px;font-weight:600;">
                Public Comments ({len(public_comments)})
              </h3>
              <ul style="margin:0;padding-left:20px;">{comment_items}</ul>
            """

        # Highlights section
        highlights_html = ""
        if highlights:
            hl_items = ""
            for h in highlights[:5]:
                hl_items += f"""
                  <li style="padding:4px 0;color:#374151;font-size:13px;">
                    {h['text'][:120]}
                    <span style="color:#6b7280;font-size:12px;"> -- {h['meeting_title']}</span>
                  </li>
                """
            highlights_html = f"""
              <h3 style="margin:24px 0 12px;color:#111827;font-size:16px;font-weight:600;">
                Notable Items
              </h3>
              <ul style="margin:0;padding-left:20px;">{hl_items}</ul>
            """

        more_count = max(0, len(meetings) - 10)
        more_text = f'<p style="color:#6b7280;font-size:13px;margin-top:8px;">...and {more_count} more meetings</p>' if more_count > 0 else ""

        archive_link = f"{BASE_URL}/"

        content = f"""
          <h2 style="margin:0 0 4px;color:#111827;font-size:22px;">{freq_label} Digest</h2>
          <p style="color:#6b7280;font-size:14px;margin:0 0 20px;">{period_start} &mdash; {period_end}</p>

          <table style="width:100%;border-collapse:collapse;margin-bottom:4px;">
            <tr>
              <td style="padding:14px;text-align:center;background:#f0fdf4;border-radius:6px 0 0 6px;">
                <div style="font-size:26px;font-weight:700;color:#15803d;">{len(meetings)}</div>
                <div style="font-size:11px;color:#6b7280;margin-top:2px;">Meetings</div>
              </td>
              <td style="padding:14px;text-align:center;background:#eff6ff;">
                <div style="font-size:26px;font-weight:700;color:#1d4ed8;">{len(votes)}</div>
                <div style="font-size:11px;color:#6b7280;margin-top:2px;">Votes</div>
              </td>
              <td style="padding:14px;text-align:center;background:#fefce8;">
                <div style="font-size:26px;font-weight:700;color:#a16207;">{len(financial_items)}</div>
                <div style="font-size:11px;color:#6b7280;margin-top:2px;">Financial Items</div>
              </td>
              <td style="padding:14px;text-align:center;background:#faf5ff;border-radius:0 6px 6px 0;">
                <div style="font-size:26px;font-weight:700;color:#7c3aed;">{len(public_comments)}</div>
                <div style="font-size:11px;color:#6b7280;margin-top:2px;">Comments</div>
              </td>
            </tr>
          </table>

          <h3 style="margin:24px 0 12px;color:#111827;font-size:16px;font-weight:600;">
            Meetings Processed
          </h3>
          <table style="width:100%;border-collapse:collapse;">
            {meeting_rows}
          </table>
          {more_text}

          {highlights_html}
          {votes_html}
          {financial_html}
          {comments_html}

          <div style="text-align:center;margin:28px 0 8px;">
            <a href="{archive_link}"
               style="display:inline-block;background-color:#1a56db;color:#ffffff;
                      padding:12px 28px;border-radius:6px;font-size:14px;font-weight:600;
                      text-decoration:none;">
              Browse Full Archive
            </a>
          </div>
        """

        footer_extra = ""
        if unsub_url:
            footer_extra = (
                f'<a href="{unsub_url}" style="color:#6b7280;text-decoration:underline;">'
                f'Unsubscribe</a> from this digest.'
            )

        return _render(content, footer_extra=footer_extra)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_digest_manager: Optional[DigestManager] = None


def init_digest(output_dir: str) -> DigestManager:
    """Initialize the global DigestManager singleton."""
    global _digest_manager
    _digest_manager = DigestManager(output_dir=output_dir)
    return _digest_manager


def get_digest_manager() -> DigestManager:
    """Return the global DigestManager singleton."""
    if _digest_manager is None:
        raise RuntimeError("DigestManager not initialized. Call init_digest() at startup.")
    return _digest_manager
