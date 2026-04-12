"""Focused tests for saved-search alert scheduling."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api.saved_searches import SavedSearchesManager
from api.scheduler import _run_saved_search_alerts


class DummyNotifier:
    def __init__(self, should_send: bool = True):
        self.should_send = should_send
        self.calls = []

    def send_saved_search_alert(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return self.should_send


def test_run_saved_search_alerts_sends_and_marks_sent(tmp_path, monkeypatch):
    manager = SavedSearchesManager(str(tmp_path / "saved_searches.db"))
    saved = manager.create(
        tenant_id="tenant-a",
        user_id="user-1",
        user_email="alerts@example.com",
        name="Zoning digest",
        search_type="meeting_search",
        query_text="zoning",
        alert_enabled=True,
        alert_frequency="daily",
    )
    notifier = DummyNotifier()

    monkeypatch.setattr("api.scheduler.get_saved_searches_manager", lambda: manager)
    monkeypatch.setattr(
        "api.scheduler.execute_saved_search",
        lambda saved_search: (
            "meeting_search",
            {"total": 2, "results": [{"title": "Council Meeting", "date": "2026-01-08"}]},
        ),
    )
    monkeypatch.setattr(
        "api.scheduler.get_tenant_store",
        lambda: SimpleNamespace(get_by_id=lambda tenant_id: SimpleNamespace(name="Tenant A")),
    )
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")

    result = _run_saved_search_alerts(
        str(tmp_path),
        notification_manager=notifier,
        now=datetime.now(timezone.utc),
    )

    refreshed = manager.get("tenant-a", saved.id)
    assert result == {"due": 1, "sent": 1, "skipped": 0, "failed": 0}
    assert refreshed is not None
    assert refreshed.last_alert_checked_at is not None
    assert refreshed.last_alert_sent_at is not None
    assert refreshed.last_alert_status == "sent"
    assert refreshed.last_alert_match_count == 2
    assert notifier.calls[0]["action_url"] == "https://app.example.com/saved-searches"

    manager.close()


def test_run_saved_search_alerts_skips_when_smtp_not_configured(tmp_path, monkeypatch):
    manager = SavedSearchesManager(str(tmp_path / "saved_searches.db"))
    saved = manager.create(
        tenant_id="tenant-a",
        user_id="user-1",
        user_email="alerts@example.com",
        name="Budget digest",
        search_type="meeting_search",
        query_text="budget",
        alert_enabled=True,
        alert_frequency="daily",
    )

    monkeypatch.setattr("api.scheduler.get_saved_searches_manager", lambda: manager)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    result = _run_saved_search_alerts(str(tmp_path), now=datetime.now(timezone.utc))

    refreshed = manager.get("tenant-a", saved.id)
    assert result == {"due": 1, "sent": 0, "skipped": 1, "failed": 0}
    assert refreshed is not None
    assert refreshed.last_alert_checked_at is not None
    assert refreshed.last_alert_sent_at is None
    assert refreshed.last_alert_status == "smtp_not_configured"
    assert refreshed.last_alert_error == "SMTP_HOST not configured"

    manager.close()


def test_run_saved_search_alerts_suppresses_zero_result_email(tmp_path, monkeypatch):
    checked_at = datetime(2026, 4, 11, tzinfo=timezone.utc)
    manager = SavedSearchesManager(str(tmp_path / "saved_searches.db"))
    saved = manager.create(
        tenant_id="tenant-a",
        user_id="user-1",
        user_email="alerts@example.com",
        name="Empty digest",
        search_type="meeting_search",
        query_text="budget",
        alert_enabled=True,
        alert_frequency="daily",
    )
    notifier = DummyNotifier()

    monkeypatch.setattr("api.scheduler.get_saved_searches_manager", lambda: manager)
    monkeypatch.setattr(
        "api.scheduler.execute_saved_search",
        lambda saved_search: ("meeting_search", {"total": 0, "results": []}),
    )
    monkeypatch.setattr(
        "api.scheduler.get_tenant_store",
        lambda: SimpleNamespace(get_by_id=lambda tenant_id: SimpleNamespace(name="Tenant A")),
    )

    result = _run_saved_search_alerts(
        str(tmp_path),
        notification_manager=notifier,
        now=checked_at,
    )

    refreshed = manager.get("tenant-a", saved.id)
    assert result == {"due": 1, "sent": 0, "skipped": 1, "failed": 0}
    assert refreshed is not None
    assert refreshed.last_alert_status == "no_results"
    assert refreshed.last_alert_match_count == 0
    assert notifier.calls == []
    assert manager.due_for_alert(now=checked_at + timedelta(hours=12)) == []
    assert manager.due_for_alert(now=checked_at + timedelta(days=2))[0].id == saved.id

    manager.close()
