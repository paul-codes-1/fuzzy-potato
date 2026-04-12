"""Push notification manager for CivicLens PWA.

Stores Web Push subscriptions per tenant user in SQLite and sends
push notifications via the Web Push protocol (pywebpush + VAPID).
"""

import base64
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VAPID key management
# ---------------------------------------------------------------------------

def _vapid_key_path(output_dir: str) -> str:
    return os.path.join(output_dir, "vapid_private.pem")


def _load_or_generate_vapid(output_dir: str) -> Vapid:
    """Load existing VAPID keys or generate a new pair."""
    pem_path = _vapid_key_path(output_dir)
    vapid = Vapid()

    if os.path.exists(pem_path):
        vapid.load_key(pem_path)
        logger.info("Loaded existing VAPID keys from %s", pem_path)
    else:
        vapid.generate_keys()
        vapid.save_key(pem_path)
        logger.info("Generated new VAPID key pair at %s", pem_path)

    return vapid


def get_vapid_public_key(output_dir: str) -> str:
    """Return the URL-safe base64-encoded public key for client subscription."""
    vapid = _load_or_generate_vapid(output_dir)
    raw = vapid.public_key.public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Subscription SQLite store
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL DEFAULT '',
    endpoint    TEXT NOT NULL UNIQUE,
    keys_json   TEXT NOT NULL,
    created_at  REAL NOT NULL,
    last_used   REAL
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_push_tenant ON push_subscriptions(tenant_id);
"""


class SubscriptionStore:
    """Thread-safe SQLite store for Web Push subscriptions."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_INDEX)
            conn.commit()
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, tenant_id: str, endpoint: str, keys: dict, user_id: str = "") -> bool:
        """Register a push subscription. Returns True if new, False if already exists."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO push_subscriptions (tenant_id, user_id, endpoint, keys_json, created_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(endpoint) DO UPDATE SET
                         keys_json = excluded.keys_json,
                         tenant_id = excluded.tenant_id,
                         user_id = excluded.user_id
                    """,
                    (tenant_id, user_id, endpoint, json.dumps(keys), time.time()),
                )
                conn.commit()
                return True
            except Exception:
                logger.exception("Failed to add push subscription")
                return False
            finally:
                conn.close()

    def remove(self, endpoint: str) -> bool:
        """Unregister a push subscription by endpoint."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM push_subscriptions WHERE endpoint = ?",
                    (endpoint,),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def remove_by_id(self, sub_id: int) -> None:
        """Remove a subscription by its row ID (used for expired cleanup)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub_id,))
                conn.commit()
            finally:
                conn.close()

    def get_by_tenant(self, tenant_id: str) -> list[dict]:
        """Get all subscriptions for a tenant."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, tenant_id, user_id, endpoint, keys_json FROM push_subscriptions WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "tenant_id": r["tenant_id"],
                    "user_id": r["user_id"],
                    "endpoint": r["endpoint"],
                    "keys": json.loads(r["keys_json"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_all(self) -> list[dict]:
        """Get all subscriptions across all tenants."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, tenant_id, user_id, endpoint, keys_json FROM push_subscriptions"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "tenant_id": r["tenant_id"],
                    "user_id": r["user_id"],
                    "endpoint": r["endpoint"],
                    "keys": json.loads(r["keys_json"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def count(self, tenant_id: Optional[str] = None) -> int:
        conn = self._connect()
        try:
            if tenant_id:
                row = conn.execute(
                    "SELECT COUNT(*) FROM push_subscriptions WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()
            return row[0]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# PushManager -- sends push notifications
# ---------------------------------------------------------------------------

class PushManager:
    """Send Web Push notifications to subscribed clients."""

    def __init__(self, output_dir: str):
        self._output_dir = output_dir
        db_path = os.path.join(output_dir, "push.db")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.store = SubscriptionStore(db_path)
        self._vapid = _load_or_generate_vapid(output_dir)
        self._vapid_claims = {
            "sub": os.environ.get("VAPID_CONTACT", "mailto:admin@civiclens.io"),
        }

    def _send_one(self, subscription: dict, payload: dict) -> bool:
        """Send a single push message. Returns True on success."""
        from pywebpush import webpush, WebPushException

        sub_info = {
            "endpoint": subscription["endpoint"],
            "keys": subscription["keys"],
        }

        try:
            webpush(
                subscription_info=sub_info,
                data=json.dumps(payload),
                vapid_private_key=_vapid_key_path(self._output_dir),
                vapid_claims=self._vapid_claims,
                timeout=10,
            )
            return True
        except WebPushException as e:
            status_code = getattr(e, "response", None)
            if status_code is not None:
                status_code = getattr(status_code, "status_code", None)

            if status_code in (404, 410):
                # Subscription expired or unsubscribed -- clean up
                logger.info(
                    "Removing expired push subscription id=%s endpoint=%s",
                    subscription.get("id"),
                    subscription["endpoint"][:60],
                )
                if subscription.get("id"):
                    self.store.remove_by_id(subscription["id"])
                else:
                    self.store.remove(subscription["endpoint"])
                return False

            logger.warning(
                "Push send failed for endpoint=%s: %s",
                subscription["endpoint"][:60],
                str(e)[:200],
            )
            return False
        except Exception:
            logger.exception("Unexpected error sending push notification")
            return False

    def send_to_tenant(self, tenant_id: str, payload: dict) -> dict:
        """Send a push notification to all subscriptions for a tenant.

        Returns {"sent": N, "failed": N, "removed": N}.
        """
        subs = self.store.get_by_tenant(tenant_id)
        return self._batch_send(subs, payload)

    def send_to_all(self, payload: dict) -> dict:
        """Send a push notification to every subscription (broadcast)."""
        subs = self.store.get_all()
        return self._batch_send(subs, payload)

    def _batch_send(self, subscriptions: list[dict], payload: dict) -> dict:
        sent = 0
        failed = 0
        removed = 0

        for sub in subscriptions:
            ok = self._send_one(sub, payload)
            if ok:
                sent += 1
            else:
                # Check if it was removed (expired)
                if sub.get("id") and not self.store.get_by_tenant(sub["tenant_id"]):
                    removed += 1
                failed += 1

        logger.info(
            "Push batch complete: sent=%d failed=%d total=%d",
            sent, failed, len(subscriptions),
        )
        return {"sent": sent, "failed": failed, "total": len(subscriptions)}

    # -- Convenience methods for specific notification types ----------------

    def notify_new_meeting(self, tenant_id: str, clip_id: str, title: str, date: str) -> dict:
        """Notify a tenant that a new meeting has been processed."""
        return self.send_to_tenant(tenant_id, {
            "title": "New Meeting Processed",
            "body": f"{title} ({date})",
            "tag": f"meeting-{clip_id}",
            "url": f"/meeting/{clip_id}",
            "clip_id": clip_id,
        })

    def notify_vote_alert(self, tenant_id: str, clip_id: str, description: str, outcome: str) -> dict:
        """Notify a tenant about a vote matching their tracked criteria."""
        return self.send_to_tenant(tenant_id, {
            "title": f"Vote Alert: {outcome.upper()}",
            "body": description[:200],
            "tag": f"vote-{clip_id}",
            "url": f"/meeting/{clip_id}",
            "clip_id": clip_id,
        })

    def notify_export_ready(self, tenant_id: str, export_id: str) -> dict:
        """Notify a tenant that a FOIA export is ready for download."""
        return self.send_to_tenant(tenant_id, {
            "title": "Export Ready",
            "body": "Your FOIA export package is ready for download.",
            "tag": f"export-{export_id}",
            "url": "/admin",
        })

    def notify_usage_alert(self, tenant_id: str, percent: int, plan: str) -> dict:
        """Notify a tenant about their API usage approaching the limit."""
        return self.send_to_tenant(tenant_id, {
            "title": "Usage Alert",
            "body": f"You have used {percent}% of your {plan} plan query limit.",
            "tag": "usage-alert",
            "url": "/analytics",
        })


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_push_manager: Optional[PushManager] = None


def init_push(output_dir: str) -> PushManager:
    global _push_manager
    _push_manager = PushManager(output_dir)
    logger.info("Push notification system initialized (db=%s/push.db)", output_dir)
    return _push_manager


def get_push_manager() -> PushManager:
    if _push_manager is None:
        raise RuntimeError("PushManager not initialized. Call init_push() first.")
    return _push_manager
