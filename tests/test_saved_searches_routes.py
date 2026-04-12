"""Focused tests for saved-search alert visibility routes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.auth import Tenant
from api.saved_searches import SavedSearchesManager


def test_saved_search_alert_status_endpoint_returns_summary(tmp_path):
    manager = SavedSearchesManager(str(tmp_path / "saved_searches.db"))
    sent_search = manager.create(
        tenant_id="dev",
        user_id="user-1",
        user_email="alerts@example.com",
        name="Sent digest",
        search_type="meeting_search",
        query_text="zoning",
        alert_enabled=True,
        alert_frequency="daily",
    )
    pending_search = manager.create(
        tenant_id="dev",
        user_id="user-2",
        user_email=None,
        name="Pending digest",
        search_type="vote_search",
        query_text="budget",
        alert_enabled=True,
        alert_frequency="weekly",
    )
    manager.record_alert_check(
        "dev",
        sent_search.id,
        status="sent",
        matched_count=3,
        checked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        sent=True,
    )

    async def fake_resolve_tenant(request=None, api_key=None, allow_legacy_dev_fallback=False):
        return Tenant(
            id="dev",
            name="Development",
            granicus_host="dev.local",
            granicus_view_id="14",
            api_key="dev-key",
            plan="enterprise",
            created_at="2026-01-01T00:00:00+00:00",
        )

    with patch("api.saved_searches_routes.get_saved_searches_manager", return_value=manager), \
         patch("api.auth._resolve_tenant", side_effect=fake_resolve_tenant):
        from api.server import app

        client = TestClient(app)
        response = client.get("/api/v1/saved-searches/alerts/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == "dev"
    assert payload["summary"]["total_alerts"] == 2
    assert payload["summary"]["deliverable"] == 1
    assert payload["summary"]["due_now"] == 1
    assert payload["summary"]["status_counts"]["sent"] == 1
    assert payload["summary"]["status_counts"]["never_checked"] == 1

    items = {item["name"]: item for item in payload["saved_searches"]}
    assert items["Sent digest"]["last_alert_status"] == "sent"
    assert items["Sent digest"]["last_alert_match_count"] == 3
    assert items["Pending digest"]["deliverable"] is False
    assert items["Pending digest"]["due_now"] is True

    manager.close()
