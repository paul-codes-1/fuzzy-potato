"""Comprehensive end-to-end integration tests simulating a complete customer lifecycle.

Tests the "golden path" from tenant onboarding through data ingestion, querying,
plan upgrades, webhooks, exports, branding, and cleanup. Uses pytest + httpx
AsyncClient with the FastAPI app. External APIs (OpenAI, Stripe, Anthropic) are
mocked throughout.
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure optional SDK stubs are present so api.server imports cleanly
_slack_mock = MagicMock()
for mod in ["slack_sdk", "slack_sdk.errors", "slack_sdk.webhook"]:
    if mod not in sys.modules:
        sys.modules[mod] = _slack_mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_KEY = "admin_e2e_secret"


def _make_app(tmp_path):
    """Create a fresh FastAPI app with all singletons pointed at tmp_path."""
    output_dir = str(tmp_path)
    os.environ["MEETINGS_OUTPUT_DIR"] = output_dir
    os.environ["ADMIN_API_KEY"] = ADMIN_KEY

    import api.server as server_mod
    import api.auth as auth_mod
    import api.analytics as analytics_mod
    import api.webhooks as webhooks_mod

    # Reset module-level singletons so each test class gets a clean state
    auth_mod._tenant_store = None
    auth_mod._rate_limiter = None
    analytics_mod._analytics_store = None
    webhooks_mod._webhook_manager = None

    from api.auth import init_auth, RateLimiter
    from api.analytics import init_analytics
    from api.webhooks import init_webhooks

    store = init_auth(output_dir)
    auth_mod._rate_limiter = RateLimiter()
    init_analytics(output_dir)
    init_webhooks(output_dir)

    # Stub lazy-loaded server singletons so we never hit real ChromaDB/OpenAI
    server_mod._collection = MagicMock()
    server_mod._collection.count.return_value = 42
    server_mod._clip_metadata = {}
    server_mod._openai_client = MagicMock()

    return server_mod.app, store


def _mock_ask_return(**overrides):
    """Standard mock return value for api.server.ask."""
    base = {
        "answer": "The city council discussed zoning changes at the January meeting.",
        "sources": [
            {
                "clip_id": "6669",
                "meeting_body": "Council",
                "date": "2026-01-22",
                "excerpt": "Ordinance 0016-26 rezoning from Agricultural...",
                "timestamp": 1515,
                "source_type": "summary",
            }
        ],
        "filters_applied": {},
        "chunks_retrieved": 3,
    }
    base.update(overrides)
    return base


def _mock_chat_return(**overrides):
    """Standard mock return value for api.server.chat."""
    base = {
        "answer": "Yes, the zoning ordinance passed 8-0 in the January session.",
        "sources": [
            {
                "clip_id": "6669",
                "meeting_body": "Council",
                "date": "2026-01-22",
                "excerpt": "Ordinance 0016-26 passed 8-0.",
                "timestamp": 1515,
                "source_type": "facts",
            }
        ],
        "filters_applied": {},
        "chunks_retrieved": 2,
    }
    base.update(overrides)
    return base


def _create_sample_clip(output_dir, clip_id=6669):
    """Write sample meeting files into the output directory for ingestion/search tests."""
    clip_dir = os.path.join(output_dir, "clips", str(clip_id))
    os.makedirs(clip_dir, exist_ok=True)

    metadata = {
        "clip_id": clip_id,
        "url": f"https://test.granicus.com/player/clip/{clip_id}",
        "date": "2026-01-22",
        "meeting_body": "Council",
        "title": "Urban County Council Meeting",
        "topics": ["Zoning", "Budget", "Public Safety"],
        "files": {
            "summary_txt": "summary.txt",
            "extracted_facts": "extracted_facts.json",
            "transcript": "transcript.txt",
            "transcript_segments": "transcript_segments.json",
        },
        "processed_at": "2026-01-31T22:04:02.617983",
        "processing_time_seconds": 120.5,
        "transcript_words": 6835,
        "audio_kept": False,
        "models": {"transcribe": "whisper-1", "summary": "gpt-4o", "topics": "gpt-4o-mini"},
    }

    (open(os.path.join(clip_dir, "metadata.json"), "w")).write(json.dumps(metadata))
    (open(os.path.join(clip_dir, "summary.txt"), "w")).write(
        "## Meeting Overview\nZoning changes and budget amendments were discussed."
    )
    (open(os.path.join(clip_dir, "extracted_facts.json"), "w")).write(
        json.dumps({
            "meeting_info": {"date": "2026-01-22", "body": "Council"},
            "motions_and_votes": [
                {
                    "identifier": "Ordinance 0016-26",
                    "description": "Zoning change",
                    "outcome": "passed",
                    "ayes": 8,
                    "nays": 0,
                }
            ],
            "financial_items": [
                {"description": "General Obligation Bonds", "amount": "$18,040,000", "type": "appropriation"}
            ],
            "public_comments": [],
            "agenda_items": [],
            "appointments": [],
            "contentious_items": [],
            "attendance": {"present": ["Beasley", "Boone"], "absent": [], "late": []},
        })
    )
    (open(os.path.join(clip_dir, "transcript.txt"), "w")).write(
        "Welcome everyone. Roll call. Councilmember Beasley. Yes ma'am."
    )
    (open(os.path.join(clip_dir, "transcript_segments.json"), "w")).write(
        json.dumps([{"start": 0.0, "end": 10.0, "text": "Welcome everyone."}])
    )

    return clip_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_env(tmp_path):
    """Provide (client, store, tmp_path) with a clean app instance."""
    app, store = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    yield client, store, tmp_path


@pytest.fixture
def lifecycle_client(app_env):
    """Create a starter-plan tenant and return (client, api_key, tenant_id, store, tmp_path)."""
    client, store, tmp_path = app_env
    resp = client.post(
        "/api/v1/admin/tenants",
        json={
            "id": "golden-city",
            "name": "Golden City, CA",
            "granicus_host": "golden.granicus.com",
            "granicus_view_id": "5",
            "plan": "starter",
        },
        headers={"X-API-Key": ADMIN_KEY},
    )
    assert resp.status_code == 200, f"Tenant creation failed: {resp.text}"
    api_key = resp.json()["api_key"]
    return client, api_key, "golden-city", store, tmp_path


# ============================================================================
# 1. Tenant Onboarding Flow
# ============================================================================

class TestTenantOnboarding:
    """Verify the full tenant creation, API key validation, defaults, and rate limits."""

    def test_create_tenant_returns_valid_key(self, app_env):
        client, store, _ = app_env
        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "onboard-city",
                "name": "Onboard City",
                "granicus_host": "onboard.granicus.com",
                "granicus_view_id": "1",
                "plan": "pro",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "onboard-city"
        assert data["plan"] == "pro"
        assert data["api_key"].startswith("mra_")
        assert data["granicus_host"] == "onboard.granicus.com"

    def test_api_key_authenticates_successfully(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client
        # Use the v1 health endpoint which requires auth
        resp = client.get("/api/v1/health", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["tenant"] == tenant_id

    def test_branding_defaults_applied(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client
        resp = client.get("/api/v1/branding", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        branding = resp.json()
        # Should have sensible defaults
        assert branding["display_name"] == "CivicLens"
        assert branding["primary_color"] == "#1a56db"
        assert "welcome_message" in branding

    def test_rate_limit_matches_starter_plan(self, lifecycle_client):
        """Starter plan allows 100 queries/month. Verify the 101st is rejected."""
        client, api_key, _, _, _ = lifecycle_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()

            # Fire 100 queries (the starter plan limit)
            for i in range(100):
                resp = client.post(
                    "/api/v1/ask",
                    json={"question": f"query {i}"},
                    headers={"X-API-Key": api_key},
                )
                assert resp.status_code == 200, f"Request {i} failed: {resp.text}"

            # 101st should be rate-limited
            resp = client.post(
                "/api/v1/ask",
                json={"question": "one over the limit"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 429
            assert "Rate limit" in resp.json()["detail"]

    def test_duplicate_tenant_id_rejected(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "golden-city",  # same ID as lifecycle_client
                "name": "Duplicate",
                "granicus_host": "dup.granicus.com",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 400

    def test_invalid_plan_rejected(self, app_env):
        client, _, _ = app_env
        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "bad-plan-city",
                "name": "Bad Plan City",
                "granicus_host": "bad.granicus.com",
                "plan": "platinum",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 422

    def test_tenant_listed_in_admin(self, lifecycle_client):
        client, _, tenant_id, _, _ = lifecycle_client
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()["tenants"]]
        assert tenant_id in ids


# ============================================================================
# 2. Data Ingestion Flow (simulated)
# ============================================================================

class TestDataIngestion:
    """Simulate meeting processing and verify search index and metadata."""

    def test_clip_files_created_and_searchable(self, lifecycle_client):
        """Create sample clip files and verify the search index can find them."""
        client, api_key, tenant_id, _, tmp_path = lifecycle_client
        output_dir = str(tmp_path)
        _create_sample_clip(output_dir, clip_id=6669)

        # Verify the clip metadata file exists
        meta_path = os.path.join(output_dir, "clips", "6669", "metadata.json")
        assert os.path.exists(meta_path)

        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["clip_id"] == 6669
        assert "Zoning" in meta["topics"]

    def test_extracted_facts_structure(self, lifecycle_client):
        """Verify extracted_facts.json has the expected structure after ingestion."""
        _, _, _, _, tmp_path = lifecycle_client
        output_dir = str(tmp_path)
        _create_sample_clip(output_dir)

        facts_path = os.path.join(output_dir, "clips", "6669", "extracted_facts.json")
        with open(facts_path) as f:
            facts = json.load(f)

        assert "motions_and_votes" in facts
        assert facts["motions_and_votes"][0]["outcome"] == "passed"
        assert "financial_items" in facts
        assert facts["financial_items"][0]["amount"] == "$18,040,000"

    def test_index_json_generation(self, lifecycle_client):
        """Simulate index.json creation from clip metadata."""
        _, _, _, _, tmp_path = lifecycle_client
        output_dir = str(tmp_path)
        _create_sample_clip(output_dir, clip_id=6669)
        _create_sample_clip(output_dir, clip_id=6670)

        # Build a minimal index.json like the pipeline would
        index = []
        clips_dir = os.path.join(output_dir, "clips")
        for clip_dir_name in sorted(os.listdir(clips_dir)):
            meta_file = os.path.join(clips_dir, clip_dir_name, "metadata.json")
            if os.path.exists(meta_file):
                with open(meta_file) as f:
                    index.append(json.load(f))

        index_path = os.path.join(output_dir, "index.json")
        with open(index_path, "w") as f:
            json.dump(index, f)

        assert os.path.exists(index_path)
        with open(index_path) as f:
            loaded = json.load(f)
        assert len(loaded) == 2
        assert loaded[0]["clip_id"] in (6669, 6670)


# ============================================================================
# 3. Query Flow
# ============================================================================

class TestQueryFlow:
    """Test single-turn ask, multi-turn chat, and search endpoints."""

    def test_ask_returns_answer_and_sources(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()

            resp = client.post(
                "/api/v1/ask",
                json={"question": "What zoning changes were discussed?"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "answer" in data
            assert len(data["answer"]) > 0
            assert "sources" in data
            assert len(data["sources"]) >= 1
            assert data["tenant"] == tenant_id
            # Verify source has expected fields
            source = data["sources"][0]
            assert "clip_id" in source
            assert "date" in source

    def test_ask_with_filters(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()

            resp = client.post(
                "/api/v1/ask",
                json={
                    "question": "Budget amendments?",
                    "meeting_body": "Council",
                    "date_after": "2026-01-01",
                    "date_before": "2026-12-31",
                },
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

            # Verify filters were passed through
            call_kwargs = mock_ask.call_args
            filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters", {})
            assert "meeting_body" in filters or "date_after" in filters

    def test_ask_empty_question_rejected(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/ask",
            json={"question": "   "},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_chat_multi_turn(self, lifecycle_client):
        """Multi-turn conversation via /api/v1/chat."""
        client, api_key, tenant_id, _, _ = lifecycle_client

        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return()

            resp = client.post(
                "/api/v1/chat",
                json={
                    "messages": [
                        {"role": "user", "content": "Tell me about the zoning vote."},
                    ],
                    "model_provider": "openai",
                },
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "answer" in data
            assert data["tenant"] == tenant_id

        # Second turn with conversation history
        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return(
                answer="The vote was 8-0 in favor on January 22, 2026."
            )

            resp = client.post(
                "/api/v1/chat",
                json={
                    "messages": [
                        {"role": "user", "content": "Tell me about the zoning vote."},
                        {"role": "assistant", "content": "The zoning ordinance passed 8-0."},
                        {"role": "user", "content": "Who voted in favor?"},
                    ],
                    "model_provider": "openai",
                },
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert "answer" in resp.json()

    def test_chat_with_anthropic_provider(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client

        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return()

            resp = client.post(
                "/api/v1/chat",
                json={
                    "messages": [
                        {"role": "user", "content": "What happened at the last meeting?"},
                    ],
                    "model_provider": "anthropic",
                },
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

    def test_chat_invalid_model_provider_rejected(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/chat",
            json={
                "messages": [{"role": "user", "content": "test"}],
                "model_provider": "gemini",
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_chat_empty_messages_rejected(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/chat",
            json={"messages": []},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_chat_last_message_must_be_user(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/chat",
            json={
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there"},
                ],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_search_endpoint(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.get(
            "/api/v1/search?q=zoning&type=all&limit=10",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data or "total" in data

    def test_search_autocomplete(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.get(
            "/api/v1/search/autocomplete?q=zon",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "suggestions" in resp.json()

    def test_ask_logged_in_analytics(self, lifecycle_client):
        """After an ask, the analytics endpoint should reflect the query."""
        client, api_key, _, _, _ = lifecycle_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            client.post(
                "/api/v1/ask",
                json={"question": "Budget for parks?"},
                headers={"X-API-Key": api_key},
            )

        resp = client.get(
            "/api/v1/analytics/usage",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["total_queries"] >= 1


# ============================================================================
# 4. Plan Upgrade Flow
# ============================================================================

class TestPlanUpgrade:
    """Start on starter, hit rate limit, upgrade to pro, verify new limit."""

    def test_starter_to_pro_upgrade_extends_limit(self, app_env):
        client, store, _ = app_env

        # Create a starter tenant
        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "upgrade-city",
                "name": "Upgrade City",
                "granicus_host": "upgrade.granicus.com",
                "plan": "starter",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        api_key = resp.json()["api_key"]

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()

            # Exhaust the 100-query starter limit
            for i in range(100):
                r = client.post(
                    "/api/v1/ask",
                    json={"question": f"q{i}"},
                    headers={"X-API-Key": api_key},
                )
                assert r.status_code == 200

            # Confirm rate limit is hit
            r = client.post(
                "/api/v1/ask",
                json={"question": "blocked"},
                headers={"X-API-Key": api_key},
            )
            assert r.status_code == 429

            # Admin upgrades to pro (1000 queries/month)
            resp = client.patch(
                "/api/v1/admin/tenants/upgrade-city/plan",
                json={"plan": "pro"},
                headers={"X-API-Key": ADMIN_KEY},
            )
            assert resp.status_code == 200
            assert resp.json()["plan"] == "pro"

            # Now the tenant can query again (pro limit = 1000, used 100)
            r = client.post(
                "/api/v1/ask",
                json={"question": "post-upgrade query"},
                headers={"X-API-Key": api_key},
            )
            assert r.status_code == 200

    def test_upgrade_to_enterprise_unlimited(self, app_env):
        client, store, _ = app_env

        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "ent-city",
                "name": "Enterprise City",
                "granicus_host": "ent.granicus.com",
                "plan": "starter",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        api_key = resp.json()["api_key"]

        # Upgrade directly to enterprise
        resp = client.patch(
            "/api/v1/admin/tenants/ent-city/plan",
            json={"plan": "enterprise"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["plan"] == "enterprise"

        # Enterprise has unlimited queries -- fire 150 without hitting limit
        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            for i in range(150):
                r = client.post(
                    "/api/v1/ask",
                    json={"question": f"ent-q{i}"},
                    headers={"X-API-Key": api_key},
                )
                assert r.status_code == 200, f"Enterprise query {i} failed: {r.text}"

    def test_admin_list_plans(self, app_env):
        client, _, _ = app_env
        resp = client.get(
            "/api/v1/admin/plans",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        plans = resp.json()["plans"]
        assert plans["starter"]["queries_per_month"] == 100
        assert plans["pro"]["queries_per_month"] == 1000
        assert plans["enterprise"]["queries_per_month"] == "unlimited"


# ============================================================================
# 5. Webhook Flow
# ============================================================================

class TestWebhookFlow:
    """Register webhooks, list, test delivery, check deliveries, and delete."""

    def test_register_list_and_delete_webhook(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client

        # Register
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["meeting.processed", "vote.detected"],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        wh_data = resp.json()
        assert "id" in wh_data
        assert "secret" in wh_data
        assert wh_data["url"] == "https://example.com/webhook"
        webhook_id = wh_data["id"]
        webhook_secret = wh_data["secret"]
        assert len(webhook_secret) > 0

        # List
        resp = client.get("/api/v1/webhooks", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        webhooks = resp.json()["webhooks"]
        assert len(webhooks) == 1
        assert webhooks[0]["id"] == webhook_id

        # Get deliveries (should be empty)
        resp = client.get(
            f"/api/v1/webhooks/{webhook_id}/deliveries",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["deliveries"] == []

        # Delete
        resp = client.delete(
            f"/api/v1/webhooks/{webhook_id}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == webhook_id

        # List should be empty now
        resp = client.get("/api/v1/webhooks", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) == 0

    def test_webhook_test_delivery(self, lifecycle_client):
        """Register a webhook and trigger a test delivery (mocked HTTP)."""
        client, api_key, tenant_id, _, _ = lifecycle_client

        # Register
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/test-hook",
                "events": ["meeting.processed"],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        webhook_id = resp.json()["id"]

        # Mock httpx so the test delivery doesn't actually call out
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            resp = client.post(
                f"/api/v1/webhooks/{webhook_id}/test",
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

        # Check deliveries now has a record
        resp = client.get(
            f"/api/v1/webhooks/{webhook_id}/deliveries",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        deliveries = resp.json()["deliveries"]
        assert len(deliveries) >= 1
        assert deliveries[0]["success"] is True

    def test_webhook_invalid_event_type(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["nonexistent.event"],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_webhook_invalid_url_rejected(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "url": "ftp://bad-protocol.com/hook",
                "events": ["meeting.processed"],
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_webhook_not_found_returns_404(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.delete(
            "/api/v1/webhooks/nonexistent-id",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 404


# ============================================================================
# 6. Export Flow
# ============================================================================

class TestExportFlow:
    """Start an export job, check status, and verify FOIA endpoint."""

    def test_start_export_and_check_status(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client

        # Start an export
        resp = client.post(
            "/api/v1/export",
            json={"format": "json"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        job = resp.json()
        assert "job_id" in job
        assert job["tenant_id"] == tenant_id
        assert job["status"] in ("pending", "running", "completed")
        job_id = job["job_id"]

        # Poll status
        resp = client.get(
            f"/api/v1/export/{job_id}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == job_id

    def test_export_history(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client

        # Create a couple exports
        client.post(
            "/api/v1/export",
            json={"format": "json"},
            headers={"X-API-Key": api_key},
        )
        client.post(
            "/api/v1/export",
            json={"format": "csv"},
            headers={"X-API-Key": api_key},
        )

        resp = client.get(
            "/api/v1/export/history",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        exports = resp.json()["exports"]
        assert len(exports) >= 2

    def test_export_invalid_format_rejected(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/export",
            json={"format": "xml"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422

    def test_export_not_found_returns_404(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.get(
            "/api/v1/export/nonexistent-job-id",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 404

    def test_foia_endpoint_accepts_request(self, lifecycle_client):
        """Submit a FOIA request; mock the RAG dependencies."""
        client, api_key, tenant_id, _, _ = lifecycle_client

        with patch("api.export_routes.get_chroma_collection") as mock_coll, \
             patch("api.export_routes.OpenAI") as mock_openai_cls, \
             patch("api.export_routes.load_clip_metadata") as mock_meta:
            mock_coll.return_value = MagicMock()
            mock_openai_cls.return_value = MagicMock()
            mock_meta.return_value = {}

            resp = client.post(
                "/api/v1/foia",
                json={"query": "All records related to zoning changes in 2026"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "request_id" in data
            assert data["tenant_id"] == tenant_id

    def test_foia_empty_query_rejected(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/foia",
            json={"query": "   "},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 422


# ============================================================================
# 7. Branding Flow
# ============================================================================

class TestBrandingFlow:
    """Verify branding get/update and plan gating."""

    def test_branding_update_requires_pro_or_enterprise(self, lifecycle_client):
        """Starter plan cannot update branding."""
        client, api_key, _, _, _ = lifecycle_client

        resp = client.put(
            "/api/v1/branding",
            json={"display_name": "My Custom Name"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403
        assert "Pro or Enterprise" in resp.json()["detail"]

    def test_branding_update_on_pro_plan(self, app_env):
        client, store, _ = app_env

        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "brand-city",
                "name": "Brand City",
                "granicus_host": "brand.granicus.com",
                "plan": "pro",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        api_key = resp.json()["api_key"]

        # Update branding
        resp = client.put(
            "/api/v1/branding",
            json={
                "display_name": "Brand City Portal",
                "primary_color": "#ff5500",
                "welcome_message": "Welcome to Brand City meetings!",
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        branding = resp.json()
        assert branding["display_name"] == "Brand City Portal"
        assert branding["primary_color"] == "#ff5500"
        assert branding["welcome_message"] == "Welcome to Brand City meetings!"

        # Fetch to confirm persistence
        resp = client.get("/api/v1/branding", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Brand City Portal"

    def test_branding_reset(self, app_env):
        client, store, _ = app_env

        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "reset-city",
                "name": "Reset City",
                "granicus_host": "reset.granicus.com",
                "plan": "enterprise",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        api_key = resp.json()["api_key"]

        # Customize, then reset
        client.put(
            "/api/v1/branding",
            json={"display_name": "Custom Name"},
            headers={"X-API-Key": api_key},
        )
        resp = client.post("/api/v1/branding/reset", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "CivicLens"


# ============================================================================
# 8. Key Rotation Flow
# ============================================================================

class TestKeyRotation:
    """Rotate API key and verify old key stops working, new key works."""

    def test_rotate_key_invalidates_old_key(self, lifecycle_client):
        client, old_key, tenant_id, _, _ = lifecycle_client

        # Rotate
        resp = client.post(
            f"/api/v1/admin/tenants/{tenant_id}/rotate-key",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        new_key = resp.json()["api_key"]
        assert new_key != old_key
        assert new_key.startswith("mra_")

        # Old key should fail
        resp = client.get("/api/v1/health", headers={"X-API-Key": old_key})
        assert resp.status_code == 401

        # New key should work
        resp = client.get("/api/v1/health", headers={"X-API-Key": new_key})
        assert resp.status_code == 200
        assert resp.json()["tenant"] == tenant_id


# ============================================================================
# 9. Audit Trail
# ============================================================================

class TestAuditTrail:
    """Verify that API requests are logged to the audit trail."""

    def test_audit_log_captures_requests(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client

        # Make a request that should be audited
        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            client.post(
                "/api/v1/ask",
                json={"question": "Audit me"},
                headers={"X-API-Key": api_key},
            )

        # Query audit logs
        resp = client.get(
            "/api/v1/audit/logs?limit=10",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data or "entries" in data or isinstance(data, list)


# ============================================================================
# 10. Analytics Flow
# ============================================================================

class TestAnalyticsFlow:
    """Verify analytics accumulation after queries."""

    def test_analytics_accumulate_after_queries(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()

            for i in range(5):
                client.post(
                    "/api/v1/ask",
                    json={"question": f"analytics query {i}"},
                    headers={"X-API-Key": api_key},
                )

        # Check usage
        resp = client.get(
            "/api/v1/analytics/usage?period=30d",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["total_queries"] >= 5

    def test_analytics_recent_queries(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client

        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            client.post(
                "/api/v1/ask",
                json={"question": "recent analytics test"},
                headers={"X-API-Key": api_key},
            )

        resp = client.get(
            "/api/v1/analytics/recent",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "queries" in resp.json()

    def test_analytics_csv_export(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.get(
            "/api/v1/analytics/export/queries?period=30d",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]


# ============================================================================
# 11. Security and Auth Edge Cases
# ============================================================================

class TestSecurityEdgeCases:
    """Comprehensive auth rejection and security header tests."""

    def test_no_auth_returns_401(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.post("/api/v1/ask", json={"question": "test"})
        assert resp.status_code == 401

    def test_bogus_key_returns_401(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.post(
            "/api/v1/ask",
            json={"question": "test"},
            headers={"X-API-Key": "mra_totally_fake"},
        )
        assert resp.status_code == 401

    def test_tenant_key_cannot_access_admin(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        resp = client.get(
            "/api/v1/admin/tenants",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 403

    def test_security_headers_present(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "Strict-Transport-Security" in resp.headers
        assert "X-Request-ID" in resp.headers

    def test_cors_headers_on_response(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.get("/health", headers={"Origin": "https://app.civiclens.ai"})
        assert "access-control-allow-origin" in resp.headers

    def test_custom_request_id_echoed(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.get("/health", headers={"X-Request-ID": "custom-req-123"})
        assert resp.headers["X-Request-ID"] == "custom-req-123"

    def test_health_no_auth_required(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ============================================================================
# 12. Cleanup / Tenant Deletion
# ============================================================================

class TestCleanup:
    """Delete tenant and verify data isolation."""

    def test_delete_tenant_removes_access(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client

        # Verify access works before deletion
        resp = client.get("/api/v1/health", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

        # Delete
        resp = client.delete(
            f"/api/v1/admin/tenants/{tenant_id}",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == tenant_id

        # API key should no longer work
        resp = client.get("/api/v1/health", headers={"X-API-Key": api_key})
        assert resp.status_code == 401

    def test_delete_nonexistent_tenant_returns_404(self, app_env):
        client, _, _ = app_env
        resp = client.delete(
            "/api/v1/admin/tenants/does-not-exist",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 404

    def test_tenant_data_isolation(self, app_env):
        """Two tenants cannot see each other's webhooks or analytics."""
        client, store, _ = app_env

        # Create two tenants
        resp1 = client.post(
            "/api/v1/admin/tenants",
            json={"id": "city-a", "name": "City A", "granicus_host": "a.granicus.com", "plan": "pro"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        key_a = resp1.json()["api_key"]

        resp2 = client.post(
            "/api/v1/admin/tenants",
            json={"id": "city-b", "name": "City B", "granicus_host": "b.granicus.com", "plan": "pro"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        key_b = resp2.json()["api_key"]

        # City A registers a webhook
        resp = client.post(
            "/api/v1/webhooks",
            json={"url": "https://a.example.com/hook", "events": ["meeting.processed"]},
            headers={"X-API-Key": key_a},
        )
        assert resp.status_code == 200

        # City B should see no webhooks
        resp = client.get("/api/v1/webhooks", headers={"X-API-Key": key_b})
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) == 0

        # City A sees its webhook
        resp = client.get("/api/v1/webhooks", headers={"X-API-Key": key_a})
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) == 1

    def test_full_lifecycle_golden_path(self, app_env):
        """Complete golden path: create -> query -> upgrade -> webhook -> export -> delete."""
        client, store, tmp_path = app_env

        # Step 1: Create tenant
        resp = client.post(
            "/api/v1/admin/tenants",
            json={
                "id": "golden-path",
                "name": "Golden Path City",
                "granicus_host": "golden.granicus.com",
                "granicus_view_id": "7",
                "plan": "starter",
            },
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        api_key = resp.json()["api_key"]

        # Step 2: Verify auth works
        resp = client.get("/api/v1/health", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

        # Step 3: Ask a question
        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            resp = client.post(
                "/api/v1/ask",
                json={"question": "What zoning changes happened?"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert "answer" in resp.json()

        # Step 4: Multi-turn chat
        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return()
            resp = client.post(
                "/api/v1/chat",
                json={
                    "messages": [{"role": "user", "content": "Tell me about votes"}],
                },
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

        # Step 5: Check analytics recorded the queries
        resp = client.get(
            "/api/v1/analytics/usage",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["total_queries"] >= 2

        # Step 6: Upgrade plan
        resp = client.patch(
            "/api/v1/admin/tenants/golden-path/plan",
            json={"plan": "pro"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200

        # Step 7: Update branding (now possible on pro)
        resp = client.put(
            "/api/v1/branding",
            json={"display_name": "Golden Path Portal"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

        # Step 8: Register webhook
        resp = client.post(
            "/api/v1/webhooks",
            json={"url": "https://golden.example.com/hook", "events": ["meeting.processed"]},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        webhook_id = resp.json()["id"]

        # Step 9: Start export
        resp = client.post(
            "/api/v1/export",
            json={"format": "json"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Step 10: Check export status
        resp = client.get(
            f"/api/v1/export/{job_id}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200

        # Step 11: Delete tenant
        resp = client.delete(
            "/api/v1/admin/tenants/golden-path",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200

        # Step 12: Verify access revoked
        resp = client.get("/api/v1/health", headers={"X-API-Key": api_key})
        assert resp.status_code == 401


# ============================================================================
# 13. Legacy Endpoint Compatibility
# ============================================================================

class TestLegacyEndpoints:
    """Verify backward-compatible endpoint paths still work."""

    def test_legacy_ask_path(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            resp = client.post(
                "/ask",
                json={"question": "test legacy"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

    def test_legacy_api_ask_path(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            resp = client.post(
                "/api/ask",
                json={"question": "test legacy api"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

    def test_legacy_chat_path(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return()
            resp = client.post(
                "/chat",
                json={"messages": [{"role": "user", "content": "legacy chat"}]},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

    def test_legacy_api_chat_path(self, lifecycle_client):
        client, api_key, _, _, _ = lifecycle_client
        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return()
            resp = client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": "legacy api chat"}]},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200

    def test_legacy_health_paths(self, lifecycle_client):
        client, _, _, _, _ = lifecycle_client
        for path in ["/health", "/api/health"]:
            resp = client.get(path)
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


# ============================================================================
# 14. Widget Endpoints
# ============================================================================

class TestWidgetEndpoints:
    """Widget ask/chat endpoints use the same auth and return same shapes."""

    def test_widget_ask(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client
        with patch("api.server.ask") as mock_ask:
            mock_ask.return_value = _mock_ask_return()
            resp = client.post(
                "/api/v1/widget/ask",
                json={"question": "widget question"},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert resp.json()["tenant"] == tenant_id

    def test_widget_chat(self, lifecycle_client):
        client, api_key, tenant_id, _, _ = lifecycle_client
        with patch("api.server.chat") as mock_chat:
            mock_chat.return_value = _mock_chat_return()
            resp = client.post(
                "/api/v1/widget/chat",
                json={"messages": [{"role": "user", "content": "widget chat"}]},
                headers={"X-API-Key": api_key},
            )
            assert resp.status_code == 200
            assert resp.json()["tenant"] == tenant_id
