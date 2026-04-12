"""Tests for the saved-search alert runner in api/scheduler.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from api.saved_searches import SavedSearchesManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path):
    db_path = str(tmp_path / "saved_searches.db")
    mgr = SavedSearchesManager(db_path)
    yield mgr
    mgr.close()


@pytest.fixture
def due_search(manager):
    """Create a saved search with alerts enabled that is immediately due."""
    return manager.create(
        tenant_id="tenant-a",
        user_id="user-1",
        user_email="alice@example.com",
        name="Zoning alerts",
        search_type="meeting_search",
        query_text="zoning",
        filters={"meeting_body": "Council"},
        alert_enabled=True,
        alert_frequency="daily",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunSavedSearchAlerts:

    def test_sends_email_and_marks_checked(self, manager, due_search, tmp_path):
        """The alert runner should execute the search, send email, and record the check."""
        mock_notifier = MagicMock()
        mock_notifier.send_saved_search_alert.return_value = True

        fake_search_results = {
            "results": [
                {"clip_id": 100, "title": "Council Zoning Hearing", "date": "2026-04-01"},
            ],
            "total": 1,
            "query": "zoning",
            "took_ms": 5.0,
        }

        # Patch both the singleton accessor and execute_saved_search
        with patch(
            "api.scheduler.get_saved_searches_manager", return_value=manager
        ), patch(
            "api.scheduler.execute_saved_search",
            return_value=("meeting_search", fake_search_results),
        ), patch(
            "api.scheduler.get_tenant_store",
        ) as mock_tenant_store:
            mock_tenant = MagicMock()
            mock_tenant.name = "Lexington, KY"
            mock_tenant_store.return_value.get_by_id.return_value = mock_tenant

            from api.scheduler import _run_saved_search_alerts

            result = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
            )

        # Should have processed 1 due alert and sent 1 email
        assert result["due"] == 1
        assert result["sent"] == 1
        assert result["skipped"] == 0
        assert result["failed"] == 0

        # Email was sent with correct args
        mock_notifier.send_saved_search_alert.assert_called_once()
        call_kwargs = mock_notifier.send_saved_search_alert.call_args
        assert call_kwargs.kwargs["to"] == "alice@example.com"
        assert call_kwargs.kwargs["saved_search_name"] == "Zoning alerts"

        # Alert was marked as checked in the database
        refreshed = manager.get("tenant-a", due_search.id)
        assert refreshed.last_alert_checked_at is not None
        assert refreshed.last_alert_status == "sent"
        assert refreshed.last_alert_sent_at is not None

    def test_no_due_alerts_is_noop(self, manager, tmp_path):
        """When no alerts are due, nothing happens."""
        with patch("api.scheduler.get_saved_searches_manager", return_value=manager):
            from api.scheduler import _run_saved_search_alerts

            result = _run_saved_search_alerts(output_dir=str(tmp_path))

        assert result == {"due": 0, "sent": 0, "skipped": 0, "failed": 0}

    def test_skips_search_without_email(self, manager, tmp_path):
        """Alerts without a user_email are skipped and recorded as such."""
        manager.create(
            tenant_id="tenant-b",
            user_id="user-2",
            user_email=None,
            name="No email search",
            search_type="meeting_search",
            query_text="budget",
            alert_enabled=True,
            alert_frequency="daily",
        )

        mock_notifier = MagicMock()

        with patch("api.scheduler.get_saved_searches_manager", return_value=manager):
            from api.scheduler import _run_saved_search_alerts

            result = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
            )

        assert result["due"] == 1
        assert result["skipped"] == 1
        assert result["sent"] == 0
        mock_notifier.send_saved_search_alert.assert_not_called()

    def test_skips_zero_result_searches(self, manager, due_search, tmp_path):
        """When execute_saved_search returns 0 results, skip sending and record no_results."""
        mock_notifier = MagicMock()

        with patch(
            "api.scheduler.get_saved_searches_manager", return_value=manager
        ), patch(
            "api.scheduler.execute_saved_search",
            return_value=("meeting_search", {"results": [], "total": 0}),
        ):
            from api.scheduler import _run_saved_search_alerts

            result = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
            )

        assert result["skipped"] == 1
        assert result["sent"] == 0
        mock_notifier.send_saved_search_alert.assert_not_called()

        refreshed = manager.get("tenant-a", due_search.id)
        assert refreshed.last_alert_status == "no_results"

    def test_records_failure_on_send_error(self, manager, due_search, tmp_path):
        """If the email send returns False, record as send_failed."""
        mock_notifier = MagicMock()
        mock_notifier.send_saved_search_alert.return_value = False

        with patch(
            "api.scheduler.get_saved_searches_manager", return_value=manager
        ), patch(
            "api.scheduler.execute_saved_search",
            return_value=("meeting_search", {"results": [{"clip_id": 1}], "total": 1}),
        ), patch("api.scheduler.get_tenant_store") as mock_ts:
            mock_ts.return_value.get_by_id.return_value = None

            from api.scheduler import _run_saved_search_alerts

            result = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
            )

        assert result["failed"] == 1
        assert result["sent"] == 0

        refreshed = manager.get("tenant-a", due_search.id)
        assert refreshed.last_alert_status == "send_failed"

    def test_records_failure_on_exception(self, manager, due_search, tmp_path):
        """If execute_saved_search raises, record as error."""
        mock_notifier = MagicMock()

        with patch(
            "api.scheduler.get_saved_searches_manager", return_value=manager
        ), patch(
            "api.scheduler.execute_saved_search",
            side_effect=RuntimeError("search engine down"),
        ):
            from api.scheduler import _run_saved_search_alerts

            result = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
            )

        assert result["failed"] == 1

        refreshed = manager.get("tenant-a", due_search.id)
        assert refreshed.last_alert_status == "error"
        assert "search engine down" in refreshed.last_alert_error

    def test_already_checked_not_due_again(self, manager, due_search, tmp_path):
        """After an alert is checked, it should not be due again until cadence elapses."""
        mock_notifier = MagicMock()
        mock_notifier.send_saved_search_alert.return_value = True

        now = datetime.now(timezone.utc)

        with patch(
            "api.scheduler.get_saved_searches_manager", return_value=manager
        ), patch(
            "api.scheduler.execute_saved_search",
            return_value=("meeting_search", {"results": [{"clip_id": 1}], "total": 1}),
        ), patch("api.scheduler.get_tenant_store") as mock_ts:
            mock_ts.return_value.get_by_id.return_value = None

            from api.scheduler import _run_saved_search_alerts

            # First run: should send
            result1 = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
                now=now,
            )
            assert result1["sent"] == 1

            # Second run 1 hour later: should not be due
            result2 = _run_saved_search_alerts(
                output_dir=str(tmp_path),
                notification_manager=mock_notifier,
                now=now + timedelta(hours=1),
            )
            assert result2["due"] == 0
