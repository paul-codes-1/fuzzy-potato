"""Tests for the CivicLens Python SDK client."""

from __future__ import annotations

import pytest
import httpx
import respx

from civiclens.client import (
    CivicLensClient,
    CivicLensError,
    AuthenticationError,
    RateLimitError,
    NotFoundError,
    RateLimitInfo,
    DEFAULT_BASE_URL,
)

API_KEY = "mra_test_key_abc123"
BASE = "https://test.civiclens.ai"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Return a client pointed at a test base URL (not yet entered as context manager)."""
    return CivicLensClient(api_key=API_KEY, base_url=BASE, max_retries=1)


@pytest.fixture
async def aclient():
    """Return a client that has been entered as an async context manager."""
    async with CivicLensClient(api_key=API_KEY, base_url=BASE, max_retries=1) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Client initialization
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_default_base_url(self):
        c = CivicLensClient(api_key=API_KEY)
        assert c.base_url == DEFAULT_BASE_URL

    def test_custom_base_url_strips_trailing_slash(self):
        c = CivicLensClient(api_key=API_KEY, base_url="https://example.com/")
        assert c.base_url == "https://example.com"

    def test_stores_api_key(self):
        c = CivicLensClient(api_key=API_KEY)
        assert c.api_key == API_KEY

    def test_custom_timeout_and_retries(self):
        c = CivicLensClient(api_key=API_KEY, timeout=30.0, max_retries=5, backoff_base=2.0)
        assert c.timeout == 30.0
        assert c.max_retries == 5
        assert c.backoff_base == 2.0

    def test_client_not_created_before_enter(self):
        c = CivicLensClient(api_key=API_KEY)
        assert c._client is None

    @pytest.mark.asyncio
    async def test_context_manager_creates_and_closes_client(self):
        c = CivicLensClient(api_key=API_KEY, base_url=BASE)
        async with c:
            assert c._client is not None
        assert c._client is None

    @pytest.mark.asyncio
    async def test_ensure_client_creates_lazily(self):
        c = CivicLensClient(api_key=API_KEY, base_url=BASE)
        assert c._client is None
        http = c._ensure_client()
        assert http is not None
        assert c._client is http
        await c.close()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        c = CivicLensClient(api_key=API_KEY, base_url=BASE)
        await c.close()  # no-op when client is None
        c._ensure_client()
        await c.close()
        await c.close()  # second close should not raise


# ---------------------------------------------------------------------------
# 7. API key sent in X-API-Key header
# ---------------------------------------------------------------------------

class TestHeaders:
    @pytest.mark.asyncio
    @respx.mock
    async def test_api_key_in_header(self, aclient):
        route = respx.get(f"{BASE}/api/v1/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        await aclient.health_authenticated()
        assert route.called
        request = route.calls[0].request
        assert request.headers["x-api-key"] == API_KEY

    @pytest.mark.asyncio
    @respx.mock
    async def test_user_agent_header(self, aclient):
        route = respx.get(f"{BASE}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        await aclient.health()
        request = route.calls[0].request
        assert "civiclens-python" in request.headers["user-agent"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_content_type_json(self, aclient):
        route = respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(200, json={"answer": "test"})
        )
        await aclient.ask("test question")
        request = route.calls[0].request
        assert "application/json" in request.headers["content-type"]


# ---------------------------------------------------------------------------
# 2. ask() method
# ---------------------------------------------------------------------------

class TestAsk:
    @pytest.mark.asyncio
    @respx.mock
    async def test_ask_basic(self, aclient):
        expected = {"answer": "The city approved...", "sources": [], "tenant": "test"}
        route = respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.ask("What about rentals?")
        assert result == expected
        body = route.calls[0].request.read()
        import json
        payload = json.loads(body)
        assert payload["question"] == "What about rentals?"

    @pytest.mark.asyncio
    @respx.mock
    async def test_ask_with_filters(self, aclient):
        route = respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(200, json={"answer": "filtered"})
        )
        await aclient.ask(
            "budget question",
            meeting_body="Council",
            date_after="2024-01-01",
            date_before="2024-12-31",
        )
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["meeting_body"] == "Council"
        assert payload["date_after"] == "2024-01-01"
        assert payload["date_before"] == "2024-12-31"

    @pytest.mark.asyncio
    @respx.mock
    async def test_ask_omits_none_filters(self, aclient):
        route = respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(200, json={"answer": "ok"})
        )
        await aclient.ask("simple question")
        import json
        payload = json.loads(route.calls[0].request.read())
        assert "meeting_body" not in payload
        assert "date_after" not in payload
        assert "date_before" not in payload


# ---------------------------------------------------------------------------
# 3. chat() method
# ---------------------------------------------------------------------------

class TestChat:
    @pytest.mark.asyncio
    @respx.mock
    async def test_chat_basic(self, aclient):
        messages = [{"role": "user", "content": "Hello"}]
        expected = {"answer": "Hi there!", "sources": []}
        route = respx.post(f"{BASE}/api/v1/chat").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.chat(messages)
        assert result == expected
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["messages"] == messages
        assert payload["model_provider"] == "openai"

    @pytest.mark.asyncio
    @respx.mock
    async def test_chat_anthropic_provider(self, aclient):
        messages = [{"role": "user", "content": "Hello"}]
        route = respx.post(f"{BASE}/api/v1/chat").mock(
            return_value=httpx.Response(200, json={"answer": "hi"})
        )
        await aclient.chat(messages, model_provider="anthropic")
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["model_provider"] == "anthropic"

    @pytest.mark.asyncio
    @respx.mock
    async def test_chat_with_filters(self, aclient):
        messages = [{"role": "user", "content": "budget?"}]
        route = respx.post(f"{BASE}/api/v1/chat").mock(
            return_value=httpx.Response(200, json={"answer": "budget info"})
        )
        await aclient.chat(
            messages,
            meeting_body="Finance",
            date_after="2024-06-01",
        )
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["meeting_body"] == "Finance"
        assert payload["date_after"] == "2024-06-01"

    @pytest.mark.asyncio
    @respx.mock
    async def test_chat_multi_turn(self, aclient):
        messages = [
            {"role": "user", "content": "What about parks?"},
            {"role": "assistant", "content": "The city allocated funds..."},
            {"role": "user", "content": "How much exactly?"},
        ]
        route = respx.post(f"{BASE}/api/v1/chat").mock(
            return_value=httpx.Response(200, json={"answer": "$2.5M"})
        )
        result = await aclient.chat(messages)
        import json
        payload = json.loads(route.calls[0].request.read())
        assert len(payload["messages"]) == 3
        assert result["answer"] == "$2.5M"


# ---------------------------------------------------------------------------
# 4. search-related methods (votes, financial)
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_search_votes(self, aclient):
        expected = {"votes": [], "total": 0}
        route = respx.get(f"{BASE}/api/v1/votes").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.search_votes(keyword="zoning")
        assert result == expected
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_votes_with_filters(self, aclient):
        route = respx.get(f"{BASE}/api/v1/votes").mock(
            return_value=httpx.Response(200, json={"votes": []})
        )
        await aclient.search_votes(
            member="Smith",
            outcome="passed",
            date_after="2024-01-01",
            limit=10,
            offset=5,
        )
        request = route.calls[0].request
        assert "member=Smith" in str(request.url)
        assert "outcome=passed" in str(request.url)
        assert "limit=10" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_financial(self, aclient):
        expected = {"items": [], "total": 0}
        route = respx.get(f"{BASE}/api/v1/financial").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.search_financial(min_amount=1000, keyword="bond")
        assert result == expected

    @pytest.mark.asyncio
    @respx.mock
    async def test_vote_stats(self, aclient):
        expected = {"total_votes": 100}
        respx.get(f"{BASE}/api/v1/votes/stats").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.get_vote_stats()
        assert result == expected

    @pytest.mark.asyncio
    @respx.mock
    async def test_member_voting_record(self, aclient):
        expected = {"member": "Jones", "votes": []}
        respx.get(f"{BASE}/api/v1/votes/member/Jones").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.get_member_voting_record("Jones")
        assert result["member"] == "Jones"


# ---------------------------------------------------------------------------
# 5. Meetings (health endpoints as proxies, since there are no list/get
#    meeting methods -- we test health + other GET endpoints)
# ---------------------------------------------------------------------------

class TestMeetingEndpoints:
    """The SDK doesn't expose list_meetings/get_meeting directly, so we test
    the health and analytics endpoints that serve similar discovery roles."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_unauthenticated(self, aclient):
        respx.get(f"{BASE}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok", "version": "1.0"})
        )
        result = await aclient.health()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_authenticated(self, aclient):
        respx.get(f"{BASE}/api/v1/health").mock(
            return_value=httpx.Response(200, json={"status": "ok", "index_size": 500})
        )
        result = await aclient.health_authenticated()
        assert result["index_size"] == 500

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_usage(self, aclient):
        expected = {"queries": 42, "period": "30d"}
        respx.get(f"{BASE}/api/v1/analytics/usage").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.get_usage(period="30d")
        assert result["queries"] == 42

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_schedule(self, aclient):
        expected = {"enabled": True, "cron_expression": "0 12 * * 1-5"}
        respx.get(f"{BASE}/api/v1/schedule").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.get_schedule()
        assert result["enabled"] is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_branding(self, aclient):
        expected = {"display_name": "Test City", "primary_color": "#003366"}
        respx.get(f"{BASE}/api/v1/branding").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.get_branding()
        assert result["display_name"] == "Test City"


# ---------------------------------------------------------------------------
# 6. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    @respx.mock
    async def test_401_raises_authentication_error(self, aclient):
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid API key"})
        )
        with pytest.raises(AuthenticationError) as exc_info:
            await aclient.ask("test")
        assert exc_info.value.status_code == 401
        assert "Invalid API key" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_raises_authentication_error(self, aclient):
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(403, json={"detail": "Forbidden"})
        )
        with pytest.raises(AuthenticationError) as exc_info:
            await aclient.ask("test")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_raises_not_found_error(self, aclient):
        respx.get(f"{BASE}/api/v1/votes/member/Nobody").mock(
            return_value=httpx.Response(404, json={"detail": "Member not found"})
        )
        with pytest.raises(NotFoundError) as exc_info:
            await aclient.get_member_voting_record("Nobody")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_raises_rate_limit_error(self, aclient):
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(
                429,
                json={"detail": "Rate limit exceeded"},
                headers={"Retry-After": "30", "X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "0"},
            )
        )
        with pytest.raises(RateLimitError) as exc_info:
            await aclient.ask("test")
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 30.0

    @pytest.mark.asyncio
    @respx.mock
    async def test_500_raises_civiclens_error(self, aclient):
        """With max_retries=1, a 500 is not retried and raises immediately."""
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(500, json={"detail": "Internal server error"})
        )
        with pytest.raises(CivicLensError) as exc_info:
            await aclient.ask("test")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_with_non_json_body(self, aclient):
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(502, text="Bad Gateway")
        )
        with pytest.raises(CivicLensError) as exc_info:
            await aclient.ask("test")
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_preserves_body(self, aclient):
        error_body = {"detail": "Quota exceeded", "code": "QUOTA_EXCEEDED"}
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(429, json=error_body)
        )
        with pytest.raises(RateLimitError) as exc_info:
            await aclient.ask("test")
        assert exc_info.value.body == error_body


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------

class TestRetry:
    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_500_then_succeeds(self):
        """With max_retries=2, first 500 is retried and second attempt succeeds."""
        async with CivicLensClient(
            api_key=API_KEY, base_url=BASE, max_retries=2, backoff_base=0.01
        ) as c:
            route = respx.post(f"{BASE}/api/v1/ask").mock(
                side_effect=[
                    httpx.Response(500, json={"detail": "error"}),
                    httpx.Response(200, json={"answer": "ok"}),
                ]
            )
            result = await c.ask("test")
            assert result["answer"] == "ok"
            assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_exhausted_raises(self):
        """When all retries fail with 500, the error is raised."""
        async with CivicLensClient(
            api_key=API_KEY, base_url=BASE, max_retries=2, backoff_base=0.01
        ) as c:
            respx.post(f"{BASE}/api/v1/ask").mock(
                return_value=httpx.Response(500, json={"detail": "down"})
            )
            with pytest.raises(CivicLensError):
                await c.ask("test")

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_retry_on_401(self):
        """Auth errors are not retried."""
        async with CivicLensClient(
            api_key=API_KEY, base_url=BASE, max_retries=3, backoff_base=0.01
        ) as c:
            route = respx.post(f"{BASE}/api/v1/ask").mock(
                return_value=httpx.Response(401, json={"detail": "bad key"})
            )
            with pytest.raises(AuthenticationError):
                await c.ask("test")
            assert route.call_count == 1


# ---------------------------------------------------------------------------
# RateLimitInfo
# ---------------------------------------------------------------------------

class TestRateLimitInfo:
    def test_parses_headers(self):
        headers = httpx.Headers({
            "X-RateLimit-Limit": "1000",
            "X-RateLimit-Remaining": "999",
            "Retry-After": "5.5",
        })
        info = RateLimitInfo.from_headers(headers)
        assert info.limit == 1000
        assert info.remaining == 999
        assert info.retry_after == 5.5

    def test_missing_headers(self):
        headers = httpx.Headers({})
        info = RateLimitInfo.from_headers(headers)
        assert info.limit is None
        assert info.remaining is None
        assert info.retry_after is None

    def test_invalid_header_values(self):
        headers = httpx.Headers({
            "X-RateLimit-Limit": "not_a_number",
            "X-RateLimit-Remaining": "",
            "Retry-After": "abc",
        })
        info = RateLimitInfo.from_headers(headers)
        assert info.limit is None
        assert info.remaining is None
        assert info.retry_after is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_last_rate_limit_updated(self):
        """Requests going through _request() update last_rate_limit."""
        async with CivicLensClient(api_key=API_KEY, base_url=BASE, max_retries=1) as c:
            respx.get(f"{BASE}/api/v1/health").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "ok"},
                    headers={"X-RateLimit-Remaining": "50"},
                )
            )
            await c.health_authenticated()
            assert c.last_rate_limit is not None
            assert c.last_rate_limit.remaining == 50


# ---------------------------------------------------------------------------
# 8. Tenant-scoped usage (testing that the API key is the tenant identifier)
# ---------------------------------------------------------------------------

class TestTenantHandling:
    """The SDK uses the API key as the tenant identifier -- each key maps to
    one tenant on the server side.  These tests verify that different keys
    produce different header values, which is how tenant isolation works."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_different_api_keys_produce_different_headers(self):
        key_a = "mra_tenant_a"
        key_b = "mra_tenant_b"

        route = respx.get(f"{BASE}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        async with CivicLensClient(api_key=key_a, base_url=BASE) as ca:
            await ca.health()
        async with CivicLensClient(api_key=key_b, base_url=BASE) as cb:
            await cb.health()

        assert route.call_count == 2
        assert route.calls[0].request.headers["x-api-key"] == key_a
        assert route.calls[1].request.headers["x-api-key"] == key_b

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_key_sent_on_every_request(self):
        """Verify the API key is included on POST, GET, PUT, DELETE."""
        respx.post(f"{BASE}/api/v1/ask").mock(
            return_value=httpx.Response(200, json={"answer": "a"})
        )
        respx.get(f"{BASE}/api/v1/analytics/usage").mock(
            return_value=httpx.Response(200, json={"queries": 0})
        )
        respx.put(f"{BASE}/api/v1/branding").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        respx.delete(f"{BASE}/api/v1/webhooks/wh_123").mock(
            return_value=httpx.Response(200, json={"deleted": True})
        )

        async with CivicLensClient(api_key=API_KEY, base_url=BASE, max_retries=1) as c:
            await c.ask("test")
            await c.get_usage()
            await c.update_branding(primary_color="#000")
            await c.delete_webhook("wh_123")

        for call in respx.calls:
            assert call.request.headers["x-api-key"] == API_KEY


# ---------------------------------------------------------------------------
# POST/PUT/PATCH/DELETE helpers
# ---------------------------------------------------------------------------

class TestWriteMethods:
    @pytest.mark.asyncio
    @respx.mock
    async def test_register_webhook(self, aclient):
        expected = {"id": "wh_1", "url": "https://example.com/hook"}
        route = respx.post(f"{BASE}/api/v1/webhooks").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.register_webhook(
            "https://example.com/hook",
            ["meeting.processed"],
            secret="s3cret",
        )
        assert result["id"] == "wh_1"
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["url"] == "https://example.com/hook"
        assert payload["events"] == ["meeting.processed"]
        assert payload["secret"] == "s3cret"

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_schedule(self, aclient):
        route = respx.put(f"{BASE}/api/v1/schedule").mock(
            return_value=httpx.Response(200, json={"enabled": False})
        )
        result = await aclient.update_schedule(enabled=False, max_clips=10)
        assert result["enabled"] is False
        import json
        payload = json.loads(route.calls[0].request.read())
        assert payload["enabled"] is False
        assert payload["max_clips"] == 10

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_alert(self, aclient):
        expected = {"id": "alert_1", "name": "Budget Watch"}
        respx.post(f"{BASE}/api/v1/alerts").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.create_alert(
            "Budget Watch", "financial_threshold", {"min_amount": 100000}
        )
        assert result["name"] == "Budget Watch"

    @pytest.mark.asyncio
    @respx.mock
    async def test_start_export(self, aclient):
        expected = {"job_id": "exp_1", "status": "pending"}
        respx.post(f"{BASE}/api/v1/export").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.start_export(format="csv")
        assert result["job_id"] == "exp_1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_foia_search(self, aclient):
        expected = {"request_id": "foia_1", "results": []}
        respx.post(f"{BASE}/api/v1/foia").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await aclient.foia_search("All records about water quality 2024")
        assert result["request_id"] == "foia_1"
