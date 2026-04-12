"""Tests for api/saved_searches.py - SavedSearchesManager CRUD, tenant isolation, alerts."""

from datetime import datetime, timedelta, timezone

import pytest

from api.saved_searches import (
    VALID_ALERT_FREQUENCIES,
    VALID_SEARCH_TYPES,
    SavedSearchesManager,
)


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
def sample_saved_search(manager):
    return manager.create(
        tenant_id="tenant-a",
        user_id="user-1",
        user_email=None,
        name="Zoning votes",
        search_type="vote_search",
        query_text="zoning",
        filters={"outcome": "passed"},
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreate:

    def test_create_basic(self, manager):
        saved = manager.create(
            tenant_id="tenant-a",
            user_id="user-1",
            user_email=None,
            name="My search",
            search_type="meeting_search",
            query_text="short term rentals",
        )
        assert saved.id
        assert saved.tenant_id == "tenant-a"
        assert saved.user_id == "user-1"
        assert saved.search_type == "meeting_search"
        assert saved.alert_enabled is False
        assert saved.alert_frequency == "off"
        assert saved.created_at
        assert saved.filters == {}

    def test_create_with_filters_and_alerts(self, manager):
        saved = manager.create(
            tenant_id="tenant-a",
            user_id="user-1",
            user_email="alerts@example.com",
            name="Weekly zoning digest",
            search_type="meeting_search",
            query_text="zoning",
            filters={"meeting_body": "Council", "date_after": "2026-01-01"},
            alert_enabled=True,
            alert_frequency="weekly",
        )
        assert saved.alert_enabled is True
        assert saved.alert_frequency == "weekly"
        assert saved.filters["meeting_body"] == "Council"
        assert saved.user_email == "alerts@example.com"

    def test_create_alert_enabled_off_frequency_defaults_daily(self, manager):
        """If alerts are enabled but frequency left 'off', default to daily."""
        saved = manager.create(
            tenant_id="tenant-a",
            user_id="user-1",
            user_email=None,
            name="Alert test",
            search_type="rag_ask",
            query_text="what is happening",
            alert_enabled=True,
            alert_frequency="off",
        )
        assert saved.alert_frequency == "daily"

    def test_create_rejects_unknown_search_type(self, manager):
        with pytest.raises(ValueError, match="Invalid search_type"):
            manager.create(
                tenant_id="tenant-a",
                user_id="user-1",
                user_email=None,
                name="Bad",
                search_type="not_a_type",
                query_text="hi",
            )

    def test_create_rejects_unknown_frequency(self, manager):
        with pytest.raises(ValueError, match="Invalid alert_frequency"):
            manager.create(
                tenant_id="tenant-a",
                user_id="user-1",
                user_email=None,
                name="Bad freq",
                search_type="rag_ask",
                query_text="hi",
                alert_frequency="hourly",
            )

    def test_create_rejects_empty_name(self, manager):
        with pytest.raises(ValueError, match="name must not be empty"):
            manager.create(
                tenant_id="tenant-a",
                user_id="user-1",
                user_email=None,
                name="   ",
                search_type="rag_ask",
                query_text="hi",
            )


class TestReadList:

    def test_get_roundtrip(self, manager, sample_saved_search):
        fetched = manager.get("tenant-a", sample_saved_search.id)
        assert fetched is not None
        assert fetched.name == "Zoning votes"
        assert fetched.filters == {"outcome": "passed"}

    def test_get_wrong_tenant_returns_none(self, manager, sample_saved_search):
        assert manager.get("other-tenant", sample_saved_search.id) is None

    def test_list_for_tenant_scopes_results(self, manager):
        manager.create("t1", "u1", None, "Search 1", "rag_ask", "q1")
        manager.create("t1", "u2", None, "Search 2", "rag_ask", "q2")
        manager.create("t2", "u1", None, "Search 3", "rag_ask", "q3")

        t1_items = manager.list_for_tenant("t1")
        t2_items = manager.list_for_tenant("t2")
        assert len(t1_items) == 2
        assert len(t2_items) == 1
        assert {s.name for s in t1_items} == {"Search 1", "Search 2"}

    def test_list_for_user_scopes_by_user(self, manager):
        manager.create("t1", "alice", None, "A1", "rag_ask", "q")
        manager.create("t1", "alice", None, "A2", "rag_ask", "q")
        manager.create("t1", "bob", None, "B1", "rag_ask", "q")

        alice = manager.list_for_user("t1", "alice")
        bob = manager.list_for_user("t1", "bob")
        assert len(alice) == 2
        assert len(bob) == 1


class TestUpdate:

    def test_update_name_and_query(self, manager, sample_saved_search):
        updated = manager.update(
            tenant_id="tenant-a",
            saved_search_id=sample_saved_search.id,
            name="Renamed",
            query_text="new query",
        )
        assert updated is not None
        assert updated.name == "Renamed"
        assert updated.query_text == "new query"
        # Unchanged fields preserved
        assert updated.search_type == "vote_search"

    def test_update_toggle_alert(self, manager, sample_saved_search):
        updated = manager.update(
            tenant_id="tenant-a",
            saved_search_id=sample_saved_search.id,
            alert_enabled=True,
            alert_frequency="daily",
        )
        assert updated.alert_enabled is True
        assert updated.alert_frequency == "daily"

    def test_update_wrong_tenant_returns_none(self, manager, sample_saved_search):
        assert manager.update(
            tenant_id="other-tenant",
            saved_search_id=sample_saved_search.id,
            name="hacked",
        ) is None


class TestDelete:

    def test_delete_success(self, manager, sample_saved_search):
        assert manager.delete("tenant-a", sample_saved_search.id) is True
        assert manager.get("tenant-a", sample_saved_search.id) is None

    def test_delete_wrong_tenant_fails(self, manager, sample_saved_search):
        assert manager.delete("other-tenant", sample_saved_search.id) is False
        # Still exists under the correct tenant
        assert manager.get("tenant-a", sample_saved_search.id) is not None


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------


class TestRecordRun:

    def test_record_run_updates_timestamp(self, manager, sample_saved_search):
        assert sample_saved_search.last_run_at is None
        assert manager.record_run("tenant-a", sample_saved_search.id) is True
        refreshed = manager.get("tenant-a", sample_saved_search.id)
        assert refreshed.last_run_at is not None
        # Parseable ISO timestamp
        datetime.fromisoformat(refreshed.last_run_at)

    def test_record_run_unknown_id(self, manager):
        assert manager.record_run("tenant-a", "nonexistent") is False


class TestAlertChecks:

    def test_record_alert_check_updates_status_fields(self, manager, sample_saved_search):
        checked_at = datetime(2026, 4, 11, tzinfo=timezone.utc)

        ok = manager.record_alert_check(
            "tenant-a",
            sample_saved_search.id,
            status="no_results",
            matched_count=0,
            checked_at=checked_at,
        )

        refreshed = manager.get("tenant-a", sample_saved_search.id)
        assert ok is True
        assert refreshed is not None
        assert refreshed.last_alert_checked_at == checked_at.isoformat()
        assert refreshed.last_alert_status == "no_results"
        assert refreshed.last_alert_match_count == 0
        assert refreshed.last_alert_sent_at is None

    def test_list_alert_statuses_includes_next_due_at(self, manager):
        checked_at = datetime(2026, 4, 11, tzinfo=timezone.utc)
        saved = manager.create(
            "t1", "u1", "alerts@example.com", "Digest", "meeting_search", "zoning",
            alert_enabled=True, alert_frequency="daily",
        )
        manager.record_alert_check(
            "t1",
            saved.id,
            status="sent",
            matched_count=2,
            checked_at=checked_at,
            sent=True,
        )

        statuses = manager.list_alert_statuses("t1", now=checked_at + timedelta(hours=1))
        assert len(statuses) == 1
        assert statuses[0]["deliverable"] is True
        assert statuses[0]["due_now"] is False
        assert statuses[0]["last_alert_status"] == "sent"
        assert statuses[0]["next_due_at"] == (checked_at + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# due_for_alert
# ---------------------------------------------------------------------------


class TestDueForAlert:

    def test_disabled_alerts_never_due(self, manager):
        manager.create("t1", "u1", None, "No alerts", "rag_ask", "q")
        assert manager.due_for_alert() == []

    def test_never_sent_is_due(self, manager):
        manager.create(
            "t1", "u1", None, "Needs sending", "rag_ask", "q",
            alert_enabled=True, alert_frequency="daily",
        )
        due = manager.due_for_alert()
        assert len(due) == 1
        assert due[0].name == "Needs sending"

    def test_recently_sent_is_not_due(self, manager):
        saved = manager.create(
            "t1", "u1", None, "Recent", "rag_ask", "q",
            alert_enabled=True, alert_frequency="daily",
        )
        manager.mark_alert_sent("t1", saved.id)
        # Immediately after sending, should not be due
        assert manager.due_for_alert() == []

    def test_stale_daily_is_due(self, manager):
        saved = manager.create(
            "t1", "u1", None, "Stale", "rag_ask", "q",
            alert_enabled=True, alert_frequency="daily",
        )
        manager.mark_alert_sent("t1", saved.id)
        # Ask "now" to be 2 days in the future
        future = datetime.now(timezone.utc) + timedelta(days=2)
        due = manager.due_for_alert(now=future)
        assert len(due) == 1
        assert due[0].id == saved.id

    def test_weekly_not_due_after_one_day(self, manager):
        saved = manager.create(
            "t1", "u1", None, "Weekly", "rag_ask", "q",
            alert_enabled=True, alert_frequency="weekly",
        )
        manager.mark_alert_sent("t1", saved.id)
        slightly_later = datetime.now(timezone.utc) + timedelta(days=1)
        assert manager.due_for_alert(now=slightly_later) == []

    def test_last_checked_at_suppresses_rechecks_until_cadence_elapses(self, manager):
        checked_at = datetime(2026, 4, 11, tzinfo=timezone.utc)
        saved = manager.create(
            "t1", "u1", "alerts@example.com", "Checked", "meeting_search", "zoning",
            alert_enabled=True, alert_frequency="daily",
        )
        manager.record_alert_check(
            "t1",
            saved.id,
            status="no_results",
            matched_count=0,
            checked_at=checked_at,
        )

        assert manager.due_for_alert(now=checked_at + timedelta(hours=12)) == []
        due = manager.due_for_alert(now=checked_at + timedelta(days=2))
        assert len(due) == 1
        assert due[0].id == saved.id


# ---------------------------------------------------------------------------
# Constants smoke tests
# ---------------------------------------------------------------------------


def test_valid_search_types():
    assert "meeting_search" in VALID_SEARCH_TYPES
    assert "vote_search" in VALID_SEARCH_TYPES
    assert "rag_ask" in VALID_SEARCH_TYPES
    assert "rag_chat" in VALID_SEARCH_TYPES


def test_valid_alert_frequencies():
    assert VALID_ALERT_FREQUENCIES == {"off", "daily", "weekly"}
