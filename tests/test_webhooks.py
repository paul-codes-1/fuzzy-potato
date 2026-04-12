"""Tests for api/webhooks.py - WebhookManager, HMAC signatures, delivery, pruning."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.webhooks import (
    MAX_DELIVERY_LOG,
    MAX_RETRIES,
    VALID_EVENT_TYPES,
    WebhookManager,
    sign_payload,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wh_manager(tmp_path):
    db_path = str(tmp_path / "webhooks.db")
    mgr = WebhookManager(db_path)
    yield mgr
    mgr.close()


@pytest.fixture
def sample_webhook(wh_manager):
    return wh_manager.register(
        tenant_id="tenant-1",
        url="https://example.com/hook",
        events=["meeting.processed", "vote.detected"],
    )


# ============================================================
# 1. Registration and unregistration
# ============================================================

class TestWebhookRegistration:

    def test_register_webhook(self, wh_manager):
        wh = wh_manager.register(
            tenant_id="t1",
            url="https://example.com/hook",
            events=["meeting.processed"],
        )
        assert wh.tenant_id == "t1"
        assert wh.url == "https://example.com/hook"
        assert wh.events == ["meeting.processed"]
        assert wh.active is True
        assert wh.secret.startswith("whsec_")

    def test_register_with_custom_secret(self, wh_manager):
        wh = wh_manager.register(
            tenant_id="t1",
            url="https://example.com/hook",
            events=["meeting.processed"],
            secret="my_custom_secret",
        )
        assert wh.secret == "my_custom_secret"

    def test_register_invalid_event_raises(self, wh_manager):
        with pytest.raises(ValueError, match="Invalid event types"):
            wh_manager.register("t1", "https://x.com/hook", ["bogus.event"])

    def test_register_empty_events_raises(self, wh_manager):
        with pytest.raises(ValueError, match="At least one event type"):
            wh_manager.register("t1", "https://x.com/hook", [])

    def test_unregister_webhook(self, wh_manager, sample_webhook):
        assert wh_manager.unregister("tenant-1", sample_webhook.id) is True
        assert wh_manager.get_webhook("tenant-1", sample_webhook.id) is None

    def test_unregister_wrong_tenant(self, wh_manager, sample_webhook):
        assert wh_manager.unregister("other-tenant", sample_webhook.id) is False

    def test_unregister_nonexistent(self, wh_manager):
        assert wh_manager.unregister("t1", "nonexistent-id") is False

    def test_list_webhooks(self, wh_manager):
        wh_manager.register("t1", "https://a.com/hook", ["meeting.processed"])
        wh_manager.register("t1", "https://b.com/hook", ["vote.detected"])
        wh_manager.register("t2", "https://c.com/hook", ["meeting.processed"])
        webhooks = wh_manager.list_webhooks("t1")
        assert len(webhooks) == 2

    def test_get_webhook(self, wh_manager, sample_webhook):
        found = wh_manager.get_webhook("tenant-1", sample_webhook.id)
        assert found is not None
        assert found.id == sample_webhook.id

    def test_get_webhook_wrong_tenant(self, wh_manager, sample_webhook):
        assert wh_manager.get_webhook("other-tenant", sample_webhook.id) is None


# ============================================================
# 2. HMAC signature generation and verification
# ============================================================

class TestHMACSignature:

    def test_sign_payload_deterministic(self):
        payload = b'{"event": "test"}'
        secret = "whsec_testsecret"
        sig1 = sign_payload(payload, secret)
        sig2 = sign_payload(payload, secret)
        assert sig1 == sig2

    def test_sign_payload_is_hex_string(self):
        sig = sign_payload(b"hello", "secret")
        assert all(c in "0123456789abcdef" for c in sig)
        assert len(sig) == 64  # SHA256 hex digest

    def test_verify_signature_valid(self):
        payload = b'{"key": "value"}'
        secret = "whsec_abc"
        sig = sign_payload(payload, secret)
        assert verify_signature(payload, secret, sig) is True

    def test_verify_signature_invalid(self):
        payload = b'{"key": "value"}'
        assert verify_signature(payload, "secret", "bad_signature") is False

    def test_different_secrets_produce_different_signatures(self):
        payload = b"same payload"
        sig1 = sign_payload(payload, "secret1")
        sig2 = sign_payload(payload, "secret2")
        assert sig1 != sig2

    def test_different_payloads_produce_different_signatures(self):
        secret = "same_secret"
        sig1 = sign_payload(b"payload1", secret)
        sig2 = sign_payload(b"payload2", secret)
        assert sig1 != sig2


# ============================================================
# 3. Delivery with mock httpx
# ============================================================

class TestWebhookDelivery:

    @pytest.mark.anyio
    async def test_delivery_success(self, wh_manager, sample_webhook):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        with patch("api.webhooks.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            records = await wh_manager.fire_event(
                tenant_id="tenant-1",
                event_type="meeting.processed",
                data={"clip_id": "123"},
            )
            assert len(records) == 1
            assert records[0].success is True
            assert records[0].status_code == 200
            assert records[0].attempts == 1

    @pytest.mark.anyio
    async def test_delivery_retry_on_failure(self, wh_manager, sample_webhook):
        """Server returns 500 on first two attempts, 200 on third."""
        responses = [
            MagicMock(status_code=500, text="Internal Server Error"),
            MagicMock(status_code=500, text="Internal Server Error"),
            MagicMock(status_code=200, text="OK"),
        ]
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("api.webhooks.httpx.AsyncClient") as MockClient, \
             patch("api.webhooks.asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post.side_effect = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            records = await wh_manager.fire_event(
                tenant_id="tenant-1",
                event_type="meeting.processed",
                data={"clip_id": "456"},
            )
            assert len(records) == 1
            assert records[0].success is True
            assert records[0].attempts == 3

    @pytest.mark.anyio
    async def test_delivery_max_retries_exhausted(self, wh_manager, sample_webhook):
        """All retries fail -> record as failure."""
        mock_response = MagicMock(status_code=500, text="Error")

        with patch("api.webhooks.httpx.AsyncClient") as MockClient, \
             patch("api.webhooks.asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            records = await wh_manager.fire_event(
                tenant_id="tenant-1",
                event_type="meeting.processed",
                data={"clip_id": "789"},
            )
            assert len(records) == 1
            assert records[0].success is False
            assert records[0].attempts == MAX_RETRIES

    @pytest.mark.anyio
    async def test_delivery_request_error(self, wh_manager, sample_webhook):
        """httpx.RequestError (network failure) triggers retries."""
        import httpx

        with patch("api.webhooks.httpx.AsyncClient") as MockClient, \
             patch("api.webhooks.asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            records = await wh_manager.fire_event(
                tenant_id="tenant-1",
                event_type="meeting.processed",
                data={},
            )
            assert len(records) == 1
            assert records[0].success is False
            assert records[0].attempts == MAX_RETRIES


# ============================================================
# 4. Delivery log pruning
# ============================================================

class TestDeliveryLogPruning:

    def test_prune_keeps_max_delivery_log(self, wh_manager, sample_webhook):
        """After MAX_DELIVERY_LOG+20 deliveries, only MAX_DELIVERY_LOG remain."""
        for i in range(MAX_DELIVERY_LOG + 20):
            wh_manager._record_delivery(
                webhook_id=sample_webhook.id,
                event_type="meeting.processed",
                payload={"i": i},
                status_code=200,
                response_body="OK",
                success=True,
                attempts=1,
                duration_ms=10.0,
            )
        deliveries = wh_manager.get_deliveries("tenant-1", sample_webhook.id, limit=200)
        assert len(deliveries) <= MAX_DELIVERY_LOG


# ============================================================
# 5. Event filtering
# ============================================================

class TestEventFiltering:

    def test_only_matching_events_fire(self, wh_manager):
        """Webhook subscribed to 'vote.detected' should not fire for 'meeting.processed'."""
        wh_manager.register("t1", "https://a.com/hook", ["vote.detected"])
        matching = wh_manager.get_webhooks_for_event("t1", "meeting.processed")
        assert len(matching) == 0

    def test_matching_event_returns_webhook(self, wh_manager):
        wh = wh_manager.register("t1", "https://a.com/hook", ["vote.detected"])
        matching = wh_manager.get_webhooks_for_event("t1", "vote.detected")
        assert len(matching) == 1
        assert matching[0].id == wh.id

    def test_multiple_webhooks_different_events(self, wh_manager):
        wh_manager.register("t1", "https://a.com/hook", ["meeting.processed"])
        wh_manager.register("t1", "https://b.com/hook", ["vote.detected"])
        wh_manager.register("t1", "https://c.com/hook", ["meeting.processed", "vote.detected"])

        meeting_hooks = wh_manager.get_webhooks_for_event("t1", "meeting.processed")
        assert len(meeting_hooks) == 2  # a and c

        vote_hooks = wh_manager.get_webhooks_for_event("t1", "vote.detected")
        assert len(vote_hooks) == 2  # b and c

    @pytest.mark.anyio
    async def test_fire_unknown_event_returns_empty(self, wh_manager, sample_webhook):
        """Firing an unknown event type returns empty list."""
        records = await wh_manager.fire_event("tenant-1", "unknown.event", {})
        assert records == []

    @pytest.mark.anyio
    async def test_fire_event_no_matching_webhooks(self, wh_manager):
        """Firing an event with no registered webhooks returns empty list."""
        records = await wh_manager.fire_event("t1", "meeting.processed", {})
        assert records == []

    def test_webhook_to_dict_excludes_secret(self, sample_webhook):
        d = sample_webhook.to_dict()
        assert "secret" not in d
        assert "id" in d
        assert "tenant_id" in d
        assert "url" in d
        assert "events" in d

    def test_valid_event_types_not_empty(self):
        assert len(VALID_EVENT_TYPES) > 0
        assert "meeting.processed" in VALID_EVENT_TYPES
