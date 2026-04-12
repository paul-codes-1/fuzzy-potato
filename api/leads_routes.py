"""FastAPI routes for lead capture.

Public submission endpoint (no auth, honeypot + IP rate-limited) and
admin-only management endpoints behind ADMIN_API_KEY.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from api.auth import require_admin
from api.leads import VALID_INTENTS, VALID_STATUSES, get_lead_store


logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Simple per-IP rate limiter (in-memory, 1 hour sliding window)
# ---------------------------------------------------------------------------

_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 3600  # seconds


class _PublicIPRateLimiter:
    """Sliding-window per-IP rate limiter for the unauthenticated lead endpoint.

    Deliberately separate from the tenant rate limiter in ``api/auth.py`` since
    this endpoint is public and keyed by IP address, not tenant.
    """

    def __init__(self, max_per_window: int = _RATE_LIMIT_MAX, window: int = _RATE_LIMIT_WINDOW):
        self._max = max_per_window
        self._window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def check(self, ip: str, now: Optional[float] = None) -> tuple[bool, int]:
        """Return (allowed, remaining).

        Consumes one slot if allowed.
        """
        now = now if now is not None else time.time()
        with self._lock:
            bucket = self._hits[ip]
            cutoff = now - self._window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False, 0
            bucket.append(now)
            return True, self._max - len(bucket)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_rate_limiter = _PublicIPRateLimiter()


def _reset_rate_limiter_for_tests() -> None:
    """Test hook to clear per-IP state between cases."""
    _rate_limiter.reset()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LeadSubmitRequest(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=320)
    message: str = Field(..., max_length=5000)
    organization: Optional[str] = Field(None, max_length=200)
    role: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=40)
    intent: Optional[str] = Field(None, max_length=50)
    source_page: Optional[str] = Field(None, max_length=500)
    utm_source: Optional[str] = Field(None, max_length=100)
    utm_medium: Optional[str] = Field(None, max_length=100)
    utm_campaign: Optional[str] = Field(None, max_length=100)
    # Honeypot. Real users never fill this. If any value is present,
    # we silently return 200 and drop the submission.
    website: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("email")
    @classmethod
    def _email_looks_valid(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("email is not valid")
        return v

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("message must not be empty")
        return v


class StatusUpdateRequest(BaseModel):
    status: str
    assigned_to: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status_valid(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(
                f"status must be one of: {sorted(VALID_STATUSES)}"
            )
        return v


class NoteRequest(BaseModel):
    note: str = Field(..., max_length=5000)
    author: Optional[str] = Field(None, max_length=200)

    @field_validator("note")
    @classmethod
    def _note_not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("note must not be empty")
        return v


# ---------------------------------------------------------------------------
# Notification helper (graceful if SMTP not configured)
# ---------------------------------------------------------------------------

def _send_admin_notification(lead) -> None:
    """Best-effort email to ADMIN_LEAD_INBOX. Never raises."""
    try:
        admin_inbox = os.environ.get("ADMIN_LEAD_INBOX") or os.environ.get("SMTP_FROM")
        if not admin_inbox:
            logger.debug("lead_notification_skipped_no_inbox")
            return
        # Only attempt SMTP if a host is configured and not the default localhost,
        # to avoid noisy failures in dev / tests.
        smtp_host = os.environ.get("SMTP_HOST")
        if not smtp_host:
            logger.info(
                "lead_notification_skipped_no_smtp_host lead_id=%s email=%s",
                lead.public_id,
                lead.email,
            )
            return

        from api.notifications import NotificationManager

        subject = f"New lead: {lead.name} ({lead.intent})"
        html = _render_admin_email(lead)
        manager = NotificationManager()
        sent = manager._send_raw(admin_inbox, subject, html)
        if not sent:
            logger.warning("lead_notification_send_failed lead_id=%s", lead.public_id)
    except Exception:
        logger.exception("lead_notification_error lead_id=%s", getattr(lead, "public_id", None))


def _render_admin_email(lead) -> str:
    def _row(label: str, value) -> str:
        if value is None or value == "":
            return ""
        safe_value = html.escape(str(value))
        return (
            f'<tr><td style="padding:6px 12px;color:#6b7280;font-size:13px;">{label}</td>'
            f'<td style="padding:6px 12px;color:#111827;font-size:13px;">{safe_value}</td></tr>'
        )

    rows = "".join(
        [
            _row("Name", lead.name),
            _row("Email", lead.email),
            _row("Organization", lead.organization),
            _row("Role", lead.role),
            _row("Phone", lead.phone),
            _row("Intent", lead.intent),
            _row("Source page", lead.source_page),
            _row("utm_source", lead.utm_source),
            _row("utm_medium", lead.utm_medium),
            _row("utm_campaign", lead.utm_campaign),
        ]
    )
    safe_message = html.escape(lead.message or "").replace("\n", "<br>")
    return (
        '<div style="font-family:sans-serif;max-width:640px;margin:0 auto;">'
        '<h2 style="color:#111827;">New CivicLens lead</h2>'
        f'<table style="border-collapse:collapse;width:100%;">{rows}</table>'
        '<h3 style="color:#111827;margin-top:16px;">Message</h3>'
        f'<div style="background:#f9fafb;padding:12px;border-radius:6px;color:#111827;font-size:14px;">{safe_message}</div>'
        f'<p style="color:#9ca3af;font-size:12px;margin-top:24px;">Lead ID: {html.escape(str(lead.public_id))}</p>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"

    # Proxy headers are trivially spoofable unless the app is actually behind a
    # trusted reverse proxy that strips and rewrites them.
    if not _env_bool("TRUST_PROXY_HEADERS", default=False):
        return direct_ip

    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        forwarded_ip = fwd.split(",")[0].strip()
        if forwarded_ip:
            return forwarded_ip

    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip

    return direct_ip


@router.post("/api/v1/leads")
def submit_lead(request: LeadSubmitRequest, http_request: Request):
    """Public lead submission endpoint. No auth. Honeypot + IP rate-limited."""
    # Honeypot: any value means a bot filled our hidden field. Silently accept.
    if request.website is not None and request.website.strip() != "":
        logger.info("lead_honeypot_triggered ip=%s", _client_ip(http_request))
        return {"success": True, "lead_id": "accepted"}

    ip = _client_ip(http_request)
    allowed, remaining = _rate_limiter.check(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many submissions from your network. Please try again in an hour.",
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
        )

    store = get_lead_store()
    try:
        lead, created = store.create(
            name=request.name,
            email=request.email,
            message=request.message,
            organization=request.organization,
            role=request.role,
            phone=request.phone,
            intent=request.intent,
            source_page=request.source_page,
            utm_source=request.utm_source,
            utm_medium=request.utm_medium,
            utm_campaign=request.utm_campaign,
            ip_address=ip,
            user_agent=http_request.headers.get("user-agent"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("lead_create_failed")
        raise HTTPException(status_code=500, detail="Could not record your message.")

    # Best-effort email notification; never fail the request on email errors.
    _send_admin_notification(lead)

    return {
        "success": True,
        "lead_id": lead.public_id,
        "deduped": not created,
    }


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/leads")
def admin_list_leads(
    status: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: bool = Depends(require_admin),
):
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    if intent and intent not in VALID_INTENTS:
        raise HTTPException(status_code=400, detail=f"Invalid intent: {intent}")

    store = get_lead_store()
    leads = store.list(
        status=status, intent=intent, search=search, limit=limit, offset=offset
    )
    return {
        "leads": [lead.to_dict() for lead in leads],
        "count": len(leads),
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/v1/admin/leads/stats")
def admin_lead_stats(_: bool = Depends(require_admin)):
    store = get_lead_store()
    return store.stats()


@router.get("/api/v1/admin/leads/{lead_id}")
def admin_get_lead(lead_id: str, _: bool = Depends(require_admin)):
    store = get_lead_store()
    lead = store.get(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead.to_dict(include_private_hashes=True)


@router.patch("/api/v1/admin/leads/{lead_id}/status")
def admin_update_lead_status(
    lead_id: str,
    body: StatusUpdateRequest,
    _: bool = Depends(require_admin),
):
    store = get_lead_store()
    try:
        lead = store.update_status(lead_id, body.status, assigned_to=body.assigned_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead.to_dict()


@router.post("/api/v1/admin/leads/{lead_id}/notes")
def admin_add_lead_note(
    lead_id: str,
    body: NoteRequest,
    _: bool = Depends(require_admin),
):
    store = get_lead_store()
    try:
        lead = store.add_note(lead_id, body.note, author=body.author)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead.to_dict()
