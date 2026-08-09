"""Tests for the failed-clip retry/sweep selection over the keyed ledger.

``failed_clips`` is a dict keyed by ``str(clip_id)`` →
``{first_ts, last_ts, attempts, reason}`` (migrated from the old
list-of-attempts form). ``_sweep_candidates`` windows on FIRST failure with no
attempt cap; ``_retry_candidates`` windows on LATEST failure and caps at
MAX_AUTO_RETRIES via the per-clip ``attempts`` counter (no more counting rows).

The selection helpers and cursor guard are tested unbound (plain functions on
the class, invoked with a stand-in ``self``) so no pipeline construction — and
none of its env/config requirements — is needed.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from main import LFUCGPipeline, _normalize_failed_clips

NOW = datetime(2026, 8, 8, 12, 0, 0)


def fake_pipeline(failed_clips, processed=(), window_days=60,
                  max_retries=3, recency_days=7):
    return SimpleNamespace(
        state={
            "failed_clips": failed_clips,
            "processed_clips": list(processed),
        },
        SWEEP_WINDOW_DAYS=window_days,
        MAX_AUTO_RETRIES=max_retries,
        RETRY_RECENCY_DAYS=recency_days,
    )


def rec(first_ts, last_ts=None, attempts=1, reason="download_failed"):
    return {
        "first_ts": first_ts,
        "last_ts": last_ts or first_ts,
        "attempts": attempts,
        "reason": reason,
    }


class TestSweepCandidates:
    def test_includes_exhausted_clips_regardless_of_attempt_count(self):
        """Unlike _retry_candidates, the sweep has no attempt cap — a clip
        with MAX_AUTO_RETRIES+ failures is still eligible."""
        fp = fake_pipeline({
            "6816": rec("2026-06-26T14:49:37", "2026-06-29T02:00:46", attempts=3),
        })
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6816]

    def test_windows_on_first_failure_not_latest(self):
        """A clip re-failing inside the sweep must age out anyway: the
        window keys on the FIRST failure timestamp, which is stable."""
        fp = fake_pipeline({
            # first failure >60d before NOW; re-failed yesterday
            "6700": rec("2026-05-01T00:00:00", "2026-08-07T00:00:00", attempts=5),
        })
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == []

    def test_recent_first_failure_is_eligible(self):
        fp = fake_pipeline({"6832": rec("2026-07-20T20:05:22")})
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6832]

    def test_excludes_processed_clips(self):
        """A clip that eventually succeeded keeps no entry normally, but even
        a lingering one must not be re-attempted once processed."""
        fp = fake_pipeline({"6825": rec("2026-07-13T14:25:57")})
        out = LFUCGPipeline._sweep_candidates(fp, {6825}, None, now=NOW)
        assert out == []

    def test_restricts_to_available_set_when_given(self):
        fp = fake_pipeline({
            "6804": rec("2026-07-19T00:00:00"),
            "9999": rec("2026-07-19T00:00:00"),  # not listed by the source
        })
        out = LFUCGPipeline._sweep_candidates(fp, set(), {6804}, now=NOW)
        assert out == [6804]

    def test_sorted_ascending(self):
        fp = fake_pipeline({
            "6832": rec("2026-07-20T20:05:22"),
            "6804": rec("2026-07-19T00:00:00"),
        })
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6804, 6832]

    def test_tolerates_malformed_entries(self):
        fp = fake_pipeline({
            "bad": "not-a-dict",
            "6800": {"attempts": 1},                     # no first_ts
            "6801": {"first_ts": "not-a-date"},          # unparseable
            "6832": rec("2026-07-20T20:05:22"),
        })
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6832]


class TestRetryCandidates:
    def _iso(self, **delta):
        return (datetime.now() - timedelta(**delta)).isoformat()

    def test_recent_under_cap_is_eligible(self):
        fp = fake_pipeline({"6900": rec(self._iso(hours=2), attempts=1)})
        out = LFUCGPipeline._retry_candidates(fp, set(), None)
        assert out == [6900]

    def test_at_or_over_cap_excluded(self):
        """attempts >= MAX_AUTO_RETRIES stops the inline retry (the whole
        point of counting a per-clip attempts field instead of rows)."""
        fp = fake_pipeline({"6901": rec(self._iso(hours=2), attempts=3)})
        out = LFUCGPipeline._retry_candidates(fp, set(), None)
        assert out == []

    def test_stale_latest_failure_excluded(self):
        """Windows on LATEST failure; an ancient last_ts ages out."""
        fp = fake_pipeline({"6902": rec(self._iso(days=30), attempts=1)})
        out = LFUCGPipeline._retry_candidates(fp, set(), None)
        assert out == []

    def test_excludes_processed(self):
        fp = fake_pipeline({"6903": rec(self._iso(hours=1), attempts=1)})
        out = LFUCGPipeline._retry_candidates(fp, {6903}, None)
        assert out == []

    def test_restricts_to_available(self):
        fp = fake_pipeline({
            "6904": rec(self._iso(hours=1)),
            "6905": rec(self._iso(hours=1)),
        })
        out = LFUCGPipeline._retry_candidates(fp, set(), {6904})
        assert out == [6904]


class TestNormalizeFailedClips:
    def test_folds_legacy_list_into_keyed_dict(self):
        legacy = [
            {"clip_id": 6816, "reason": "download_failed",
             "timestamp": "2026-06-26T14:49:37"},
            {"clip_id": 6816, "reason": "transcription_failed",
             "timestamp": "2026-06-29T02:00:46"},
            {"clip_id": 6804, "reason": "download_failed",
             "timestamp": "2026-07-19T00:00:00"},
        ]
        out = _normalize_failed_clips(legacy)
        assert set(out) == {"6816", "6804"}
        assert out["6816"]["attempts"] == 2
        assert out["6816"]["first_ts"] == "2026-06-26T14:49:37"
        assert out["6816"]["last_ts"] == "2026-06-29T02:00:46"
        # latest reason wins
        assert out["6816"]["reason"] == "transcription_failed"
        assert out["6804"]["attempts"] == 1

    def test_dict_passes_through(self):
        d = {"6816": rec("2026-06-26T14:49:37")}
        assert _normalize_failed_clips(d) is d

    def test_tolerates_malformed_and_missing_ids(self):
        out = _normalize_failed_clips([
            "not-a-dict",
            {"reason": "no clip_id"},
            {"clip_id": 6800, "timestamp": "2026-07-20T00:00:00"},
        ])
        assert set(out) == {"6800"}


class TestAdvanceCursor:
    def test_advances_forward(self):
        fp = SimpleNamespace(state={"last_processed_clip_id": 6800})
        LFUCGPipeline._advance_cursor(fp, 6845)
        assert fp.state["last_processed_clip_id"] == 6845

    def test_never_regresses(self):
        """Reprocessing an old clip (sweep / manual single-clip run) must
        not rewind the --auto cursor."""
        fp = SimpleNamespace(state={"last_processed_clip_id": 6845})
        LFUCGPipeline._advance_cursor(fp, 6816)
        assert fp.state["last_processed_clip_id"] == 6845

    def test_handles_missing_initial_state(self):
        fp = SimpleNamespace(state={})
        LFUCGPipeline._advance_cursor(fp, 6749)
        assert fp.state["last_processed_clip_id"] == 6749


class TestRecordAndClearFailure:
    def test_record_increments_attempts_and_updates_last_ts(self):
        fp = SimpleNamespace(state={"failed_clips": {}})
        LFUCGPipeline._record_failure(fp, 42, "download_failed")
        LFUCGPipeline._record_failure(fp, 42, "transcription_failed")
        entry = fp.state["failed_clips"]["42"]
        assert entry["attempts"] == 2
        assert entry["reason"] == "transcription_failed"
        assert entry["first_ts"] <= entry["last_ts"]

    def test_clear_removes_entry(self):
        fp = SimpleNamespace(state={"failed_clips": {"42": rec("2026-07-01T00:00:00")}})
        LFUCGPipeline._clear_failure(fp, 42)
        assert "42" not in fp.state["failed_clips"]

    def test_record_migrates_legacy_list_in_place(self):
        fp = SimpleNamespace(state={"failed_clips": [
            {"clip_id": 42, "reason": "download_failed",
             "timestamp": "2026-07-01T00:00:00"},
        ]})
        LFUCGPipeline._record_failure(fp, 42, "transcription_failed")
        assert isinstance(fp.state["failed_clips"], dict)
        assert fp.state["failed_clips"]["42"]["attempts"] == 2
