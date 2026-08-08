"""Tests for the weekly failed-clip retry sweep (--retry-failed-sweep).

The sweep exists because the inline --auto retry burns its whole
MAX_AUTO_RETRIES budget across ~3 consecutive 6-hourly crons (~12h),
while Granicus sometimes posts a meeting's video days after the clip
page appears — clips that exhausted the inline budget were silently
dropped forever (6804 / 6816 / 6832, June-July 2026).

The selection helper and cursor guard are tested unbound (plain
functions on the class, invoked with a stand-in ``self``) so no
pipeline construction — and none of its env/config requirements — is
needed.
"""

from datetime import datetime
from types import SimpleNamespace

from main import LFUCGPipeline

NOW = datetime(2026, 8, 8, 12, 0, 0)


def fake_pipeline(failed_clips, processed=(), window_days=60):
    return SimpleNamespace(
        state={
            "failed_clips": failed_clips,
            "processed_clips": list(processed),
        },
        SWEEP_WINDOW_DAYS=window_days,
    )


def failure(clip_id, ts):
    return {"clip_id": clip_id, "reason": "download_failed", "timestamp": ts}


class TestSweepCandidates:
    def test_includes_exhausted_clips_regardless_of_attempt_count(self):
        """Unlike _retry_candidates, the sweep has no attempt cap — a clip
        with MAX_AUTO_RETRIES+ failures is still eligible."""
        fp = fake_pipeline([
            failure(6816, "2026-06-26T14:49:37"),
            failure(6816, "2026-06-26T20:00:46"),
            failure(6816, "2026-06-29T02:00:46"),
        ])
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6816]

    def test_windows_on_first_failure_not_latest(self):
        """A clip re-failing inside the sweep must age out anyway: the
        window keys on the FIRST failure timestamp, which is stable."""
        fp = fake_pipeline([
            failure(6700, "2026-05-01T00:00:00"),  # first failure: >60d before NOW
            failure(6700, "2026-08-07T00:00:00"),  # re-failed yesterday (sweep attempt)
        ])
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == []

    def test_recent_first_failure_is_eligible(self):
        fp = fake_pipeline([failure(6832, "2026-07-20T20:05:22")])
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6832]

    def test_excludes_processed_clips(self):
        """A clip that eventually succeeded (e.g. via a later retry) keeps
        its old failure entries but must not be re-attempted."""
        fp = fake_pipeline([failure(6825, "2026-07-13T14:25:57")])
        out = LFUCGPipeline._sweep_candidates(fp, {6825}, None, now=NOW)
        assert out == []

    def test_restricts_to_available_set_when_given(self):
        fp = fake_pipeline([
            failure(6804, "2026-07-19T00:00:00"),
            failure(9999, "2026-07-19T00:00:00"),  # not listed by the source
        ])
        out = LFUCGPipeline._sweep_candidates(fp, set(), {6804}, now=NOW)
        assert out == [6804]

    def test_sorted_ascending_and_dedupes_entries(self):
        fp = fake_pipeline([
            failure(6832, "2026-07-20T20:05:22"),
            failure(6804, "2026-07-19T00:00:00"),
            failure(6832, "2026-07-21T02:00:46"),
        ])
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6804, 6832]

    def test_tolerates_malformed_entries(self):
        fp = fake_pipeline([
            "not-a-dict",
            {"reason": "no clip_id"},
            {"clip_id": 6800},  # no timestamp
            {"clip_id": 6801, "timestamp": "not-a-date"},
            failure(6832, "2026-07-20T20:05:22"),
        ])
        out = LFUCGPipeline._sweep_candidates(fp, set(), None, now=NOW)
        assert out == [6832]


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
