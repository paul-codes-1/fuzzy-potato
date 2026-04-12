"""Webhook subscription management and async delivery for multi-tenant meeting platform."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EVENT_TYPES = {
    "meeting.processed",
    "meeting.summary_ready",
    "vote.detected",
    "financial_item.detected",
    "query.answered",
}

MAX_DELIVERY_LOG = 100  # per webhook
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds; exponential backoff: 1s, 2s, 4s

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Webhook:
    id: str
    tenant_id: str
    url: str
    events: list[str]
    secret: str
    active: bool
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "url": self.url,
            "events": self.events,
            "active": self.active,
            "created_at": self.created_at,
        }


@dataclass
class DeliveryRecord:
    id: str
    webhook_id: str
    event_type: str
    payload: dict
    status_code: Optional[int]
    response_body: Optional[str]
    success: bool
    attempts: int
    delivered_at: str
    duration_ms: Optional[float]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "webhook_id": self.webhook_id,
            "event_type": self.event_type,
            "status_code": self.status_code,
            "success": self.success,
            "attempts": self.attempts,
            "delivered_at": self.delivered_at,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_WEBHOOKS_TABLE = """
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    url TEXT NOT NULL,
    events TEXT NOT NULL,
    secret TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""

_CREATE_DELIVERIES_TABLE = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    webhook_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status_code INTEGER,
    response_body TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT NOT NULL,
    duration_ms REAL,
    FOREIGN KEY (webhook_id) REFERENCES webhooks(id) ON DELETE CASCADE
);
"""

_CREATE_DELIVERIES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_deliveries_webhook_id
    ON webhook_deliveries(webhook_id, delivered_at DESC);
"""


# ---------------------------------------------------------------------------
# Payload signing
# ---------------------------------------------------------------------------

def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for a webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(payload_bytes: bytes, secret: str, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature."""
    expected = sign_payload(payload_bytes, secret)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# WebhookManager
# ---------------------------------------------------------------------------

class WebhookManager:
    """Manages webhook subscriptions and delivery for all tenants.

    Uses SQLite for persistent storage of subscriptions and delivery logs.
    Fires webhooks asynchronously via httpx with retries and exponential backoff.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(_CREATE_WEBHOOKS_TABLE)
        self._conn.execute(_CREATE_DELIVERIES_TABLE)
        self._conn.execute(_CREATE_DELIVERIES_INDEX)
        self._conn.commit()

    # -- helpers -------------------------------------------------------------

    def _row_to_webhook(self, row: sqlite3.Row) -> Webhook:
        d = dict(row)
        d["events"] = json.loads(d["events"])
        d["active"] = bool(d["active"])
        return Webhook(**d)

    def _row_to_delivery(self, row: sqlite3.Row) -> DeliveryRecord:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        d["success"] = bool(d["success"])
        return DeliveryRecord(**d)

    # -- CRUD ----------------------------------------------------------------

    def register(
        self,
        tenant_id: str,
        url: str,
        events: list[str],
        secret: Optional[str] = None,
    ) -> Webhook:
        """Register a new webhook subscription for a tenant."""
        invalid = set(events) - VALID_EVENT_TYPES
        if invalid:
            raise ValueError(
                f"Invalid event types: {', '.join(sorted(invalid))}. "
                f"Valid types: {', '.join(sorted(VALID_EVENT_TYPES))}"
            )
        if not events:
            raise ValueError("At least one event type is required.")

        webhook_id = str(uuid.uuid4())
        if not secret:
            secret = f"whsec_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO webhooks (id, tenant_id, url, events, secret, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (webhook_id, tenant_id, url, json.dumps(events), secret, now),
        )
        self._conn.commit()

        return Webhook(
            id=webhook_id,
            tenant_id=tenant_id,
            url=url,
            events=events,
            secret=secret,
            active=True,
            created_at=now,
        )

    def unregister(self, tenant_id: str, webhook_id: str) -> bool:
        """Remove a webhook subscription. Returns True if deleted."""
        cur = self._conn.execute(
            "DELETE FROM webhooks WHERE id = ? AND tenant_id = ?",
            (webhook_id, tenant_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_webhooks(self, tenant_id: str) -> list[Webhook]:
        """List all webhooks for a tenant."""
        rows = self._conn.execute(
            "SELECT * FROM webhooks WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        return [self._row_to_webhook(r) for r in rows]

    def get_webhook(self, tenant_id: str, webhook_id: str) -> Optional[Webhook]:
        """Get a single webhook by ID, scoped to tenant."""
        row = self._conn.execute(
            "SELECT * FROM webhooks WHERE id = ? AND tenant_id = ?",
            (webhook_id, tenant_id),
        ).fetchone()
        return self._row_to_webhook(row) if row else None

    def get_webhooks_for_event(self, tenant_id: str, event_type: str) -> list[Webhook]:
        """Get all active webhooks for a tenant that subscribe to an event type."""
        rows = self._conn.execute(
            "SELECT * FROM webhooks WHERE tenant_id = ? AND active = 1",
            (tenant_id,),
        ).fetchall()
        webhooks = [self._row_to_webhook(r) for r in rows]
        return [w for w in webhooks if event_type in w.events]

    # -- Delivery log --------------------------------------------------------

    def _record_delivery(
        self,
        webhook_id: str,
        event_type: str,
        payload: dict,
        status_code: Optional[int],
        response_body: Optional[str],
        success: bool,
        attempts: int,
        duration_ms: Optional[float],
    ) -> DeliveryRecord:
        delivery_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO webhook_deliveries "
            "(id, webhook_id, event_type, payload, status_code, response_body, success, attempts, delivered_at, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                delivery_id,
                webhook_id,
                event_type,
                json.dumps(payload),
                status_code,
                response_body[:2000] if response_body else None,
                int(success),
                attempts,
                now,
                duration_ms,
            ),
        )

        # Prune old deliveries beyond MAX_DELIVERY_LOG per webhook
        self._conn.execute(
            """
            DELETE FROM webhook_deliveries
            WHERE id IN (
                SELECT id FROM webhook_deliveries
                WHERE webhook_id = ?
                ORDER BY delivered_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (webhook_id, MAX_DELIVERY_LOG),
        )
        self._conn.commit()

        return DeliveryRecord(
            id=delivery_id,
            webhook_id=webhook_id,
            event_type=event_type,
            payload=payload,
            status_code=status_code,
            response_body=response_body,
            success=success,
            attempts=attempts,
            delivered_at=now,
            duration_ms=duration_ms,
        )

    def get_deliveries(self, tenant_id: str, webhook_id: str, limit: int = 50) -> list[DeliveryRecord]:
        """Get recent deliveries for a webhook, scoped to tenant."""
        # Verify webhook belongs to tenant
        wh = self.get_webhook(tenant_id, webhook_id)
        if not wh:
            return []

        rows = self._conn.execute(
            "SELECT * FROM webhook_deliveries WHERE webhook_id = ? ORDER BY delivered_at DESC LIMIT ?",
            (webhook_id, min(limit, MAX_DELIVERY_LOG)),
        ).fetchall()
        return [self._row_to_delivery(r) for r in rows]

    # -- Async delivery ------------------------------------------------------

    async def _deliver_single(
        self,
        webhook: Webhook,
        event_type: str,
        payload: dict,
    ) -> DeliveryRecord:
        """Deliver a webhook with retries and exponential backoff."""
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        signature = sign_payload(payload_bytes, webhook.secret)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Webhook-Event": event_type,
            "X-Webhook-ID": str(uuid.uuid4()),
            "User-Agent": "CivicLens-Webhooks/1.0",
        }

        status_code = None
        response_body = None
        success = False
        attempts = 0
        duration_ms = None

        async with httpx.AsyncClient(timeout=10.0) as client:
            for attempt in range(MAX_RETRIES):
                attempts = attempt + 1
                try:
                    start = time.perf_counter()
                    resp = await client.post(
                        webhook.url,
                        content=payload_bytes,
                        headers=headers,
                    )
                    duration_ms = round((time.perf_counter() - start) * 1000, 1)
                    status_code = resp.status_code
                    response_body = resp.text[:2000]

                    if 200 <= resp.status_code < 300:
                        success = True
                        break

                    logger.warning(
                        "Webhook delivery attempt %d/%d failed: status=%d url=%s",
                        attempts, MAX_RETRIES, resp.status_code, webhook.url,
                    )

                except httpx.RequestError as exc:
                    duration_ms = round((time.perf_counter() - start) * 1000, 1) if 'start' in dir() else None
                    response_body = str(exc)
                    logger.warning(
                        "Webhook delivery attempt %d/%d error: %s url=%s",
                        attempts, MAX_RETRIES, exc, webhook.url,
                    )

                # Exponential backoff before retry
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)

        record = self._record_delivery(
            webhook_id=webhook.id,
            event_type=event_type,
            payload=payload,
            status_code=status_code,
            response_body=response_body,
            success=success,
            attempts=attempts,
            duration_ms=duration_ms,
        )

        if success:
            logger.info("Webhook delivered: event=%s webhook=%s", event_type, webhook.id)
        else:
            logger.error(
                "Webhook delivery failed after %d attempts: event=%s webhook=%s",
                attempts, event_type, webhook.id,
            )

        return record

    async def fire_event(
        self,
        tenant_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> list[DeliveryRecord]:
        """Fire an event to all matching webhooks for a tenant.

        Delivers to all active webhooks subscribed to the event type.
        Each delivery runs concurrently via asyncio.gather.
        """
        if event_type not in VALID_EVENT_TYPES:
            logger.warning("Ignoring unknown event type: %s", event_type)
            return []

        webhooks = self.get_webhooks_for_event(tenant_id, event_type)
        if not webhooks:
            return []

        payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "data": data,
        }

        tasks = [
            self._deliver_single(wh, event_type, payload)
            for wh in webhooks
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        deliveries = []
        for r in results:
            if isinstance(r, DeliveryRecord):
                deliveries.append(r)
            else:
                logger.error("Webhook delivery raised exception: %s", r)
        return deliveries

    async def send_test_event(self, tenant_id: str, webhook_id: str) -> Optional[DeliveryRecord]:
        """Send a test event to a specific webhook."""
        webhook = self.get_webhook(tenant_id, webhook_id)
        if not webhook:
            return None

        payload = {
            "event": "webhook.test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "data": {
                "message": "This is a test webhook delivery from CivicLens.",
                "webhook_id": webhook_id,
            },
        }

        return await self._deliver_single(webhook, "webhook.test", payload)

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_webhook_manager: Optional[WebhookManager] = None


def init_webhooks(output_dir: str) -> WebhookManager:
    """Initialize the webhook manager. Call once at startup."""
    global _webhook_manager
    db_path = os.path.join(output_dir, "webhooks.db")
    _webhook_manager = WebhookManager(db_path)
    return _webhook_manager


def get_webhook_manager() -> WebhookManager:
    if _webhook_manager is None:
        raise RuntimeError("Webhooks not initialized -- call init_webhooks() first")
    return _webhook_manager
