"""Tests for the 2026-09-12 transcript-source changes in main.py:

- ``requires_whisper``: Council / Work Session / Committee of the Whole /
  Planning Commission clips must not take the Granicus-VTT shortcut.
- ``find_placeholder_clips``: census for --retranscribe-placeholders.
- ``retranscribe_clip``: Whisper replaces the placeholder; a rejected
  Whisper pass restores the placeholder untouched.
- ``reset_failed_attempts``: --retry-failed re-opens the retry budget.

Pipeline methods are exercised unbound with a SimpleNamespace stand-in (same
pattern as tests/test_retry_sweep.py) so no pipeline construction is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from main import LFUCGPipeline, requires_whisper


class TestRequiresWhisper:
    @pytest.mark.parametrize("title", [
        "Urban County Council (1)",
        "Urban County Council Work Session (2)",
        "Council Work Session (1)",
        "Council Committee of the Whole (1)",
        "Planning Commission-Zoning Items (1)",
        "Planning Commission Subdivision Items (1)",
        "planning commission work session",
    ])
    def test_required_bodies(self, title, monkeypatch):
        monkeypatch.delenv("LFUCG_WHISPER_REQUIRED_BODIES", raising=False)
        assert requires_whisper(title)

    @pytest.mark.parametrize("title", [
        "Public Arts Commission (1)",
        "Board of Adjustment Meeting (1)",
        "Environmental Quality & Public Works Committee (1)",
        "January 8 2026 WQFB meeting",
        "",
        None,
    ])
    def test_other_bodies_keep_the_vtt_shortcut(self, title, monkeypatch):
        monkeypatch.delenv("LFUCG_WHISPER_REQUIRED_BODIES", raising=False)
        assert not requires_whisper(title)

    def test_meeting_body_alone_can_match(self):
        assert requires_whisper("Untitled clip", meeting_body="Planning Commission")

    def test_env_override_and_disable(self, monkeypatch):
        monkeypatch.setenv("LFUCG_WHISPER_REQUIRED_BODIES", "board of adjustment")
        assert requires_whisper("Board of Adjustment Meeting (1)")
        assert not requires_whisper("Urban County Council (1)")
        monkeypatch.setenv("LFUCG_WHISPER_REQUIRED_BODIES", "")
        assert not requires_whisper("Urban County Council (1)")

    def test_explicit_pattern_argument_wins(self):
        assert requires_whisper("Public Arts Commission (1)", pattern="arts")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_clip(root: Path, clip_id: int, *, title: str, date: str = "2026-08-27",
                source: str = "granicus_vtt", body: str = "", cues_end: float = 7200.0,
                whisper_pending: bool = False) -> Path:
    d = root / "clips" / str(clip_id)
    d.mkdir(parents=True)
    (d / "transcript.txt").write_text("placeholder caption text " * 40)
    (d / "transcript_segments.json").write_text(json.dumps(
        [{"start": 0.0, "end": cues_end / 2, "text": "a"},
         {"start": cues_end / 2, "end": cues_end, "text": "b"}]))
    meta = {
        "clip_id": clip_id, "title": title, "date": date, "meeting_body": body,
        "transcript_source": source, "speakers": [],
        "files": {"transcript": "transcript.txt", "transcript_segments": "transcript_segments.json",
                  "captions_vtt": "captions.vtt"},
        "models": {"transcribe": source},
    }
    if whisper_pending:
        meta["whisper_pending"] = True
    (d / "metadata.json").write_text(json.dumps(meta))
    return d


def _fake_pipeline(root: Path, **extra) -> SimpleNamespace:
    ns = SimpleNamespace(
        output_dir=root,
        state={"failed_clips": {}, "processed_clips": []},
        log=lambda *a, **k: None,
        save_state=lambda: None,
        keep_audio=False,
        transcriber="whisper",
        transcribe_model="whisper-1",
        force_reprocess=False,
        WHISPER_USD_PER_MINUTE=LFUCGPipeline.WHISPER_USD_PER_MINUTE,
    )
    ns._record_failure = lambda cid, reason: LFUCGPipeline._record_failure(ns, cid, reason)
    ns._clear_failure = lambda cid: LFUCGPipeline._clear_failure(ns, cid)
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


class TestFindPlaceholderClips:
    def test_selects_required_bodies_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LFUCG_WHISPER_REQUIRED_BODIES", raising=False)
        _write_clip(tmp_path, 1, title="Urban County Council (1)", date="2026-08-01")
        _write_clip(tmp_path, 2, title="Planning Commission-Zoning Items (1)", date="2026-09-03")
        _write_clip(tmp_path, 3, title="Public Arts Commission (1)", date="2026-09-05")   # not required
        _write_clip(tmp_path, 4, title="Council Work Session (1)", date="2026-08-25",
                    source="whisper-1+vtt-speakers")                                     # already Whispered
        _write_clip(tmp_path, 5, title="Council Work Session (2)", date="2026-07-01",
                    source="whisper-1", whisper_pending=True)                            # pending flag counts
        out = LFUCGPipeline.find_placeholder_clips(_fake_pipeline(tmp_path))
        assert [c["clip_id"] for c in out] == [2, 1, 5]
        assert out[0]["est_minutes"] == 120.0

    def test_since_and_limit_and_bodies(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LFUCG_WHISPER_REQUIRED_BODIES", raising=False)
        _write_clip(tmp_path, 1, title="Urban County Council (1)", date="2026-08-01")
        _write_clip(tmp_path, 2, title="Planning Commission-Zoning Items (1)", date="2026-09-03")
        _write_clip(tmp_path, 3, title="Public Arts Commission (1)", date="2026-09-05")
        fp = _fake_pipeline(tmp_path)
        assert [c["clip_id"] for c in LFUCGPipeline.find_placeholder_clips(fp, since="2026-09-01")] == [2]
        assert [c["clip_id"] for c in LFUCGPipeline.find_placeholder_clips(fp, limit=1)] == [2]
        # --bodies replaces the default set entirely.
        assert [c["clip_id"] for c in LFUCGPipeline.find_placeholder_clips(fp, bodies=["arts"])] == [3]


class TestRetranscribeClip:
    def _segments(self):
        return [{"start": 0.0, "end": 5.0, "text": "Welcome everyone."},
                {"start": 5.0, "end": 9.0, "text": "Roll call please."}]

    def test_success_replaces_placeholder_and_metadata(self, tmp_path):
        d = _write_clip(tmp_path, 6865, title="Urban County Council (1)")
        # Varied text so the repetition-loop QA gate accepts it (600 words).
        text = " ".join(f"word{i} council item{i % 7} vote" for i in range(150))

        def fake_transcribe(audio_path, transcript_path):
            transcript_path.write_text(text)
            (transcript_path.parent / f"{transcript_path.stem}_segments.json").write_text(
                json.dumps(self._segments()))
            return {"text": text, "segments": self._segments()}

        def fake_enrich(cid, clip_dir, segments, segments_path):
            return {"segments": segments, "speakers": ["Mayor Gorton"],
                    "source": "whisper-1+vtt-speakers", "vtt_filename": "captions.vtt"}

        (d / "audio.mp3").write_bytes(b"x")
        fp = _fake_pipeline(
            tmp_path,
            source=SimpleNamespace(download_audio=lambda cid, cd, title, date=None: "audio.mp3"),
            transcribe_audio=fake_transcribe,
            get_audio_duration=lambda p: 9.0,
            _enrich_segments_with_captions=fake_enrich,
        )
        assert LFUCGPipeline.retranscribe_clip(fp, 6865) is True
        meta = json.loads((d / "metadata.json").read_text())
        assert meta["transcript_source"] == "whisper-1+vtt-speakers"
        assert meta["speakers"] == ["Mayor Gorton"]
        assert meta["models"]["transcribe"] == "whisper-1"
        assert meta["transcript_words"] == 600
        assert "retranscribed_at" in meta
        assert "whisper_pending" not in meta
        assert "audio" not in meta["files"] and not (d / "audio.mp3").exists()  # keep_audio=False
        assert "word149 council" in (d / "transcript.txt").read_text()
        assert not list(d.glob("*.bak"))
        # transcribe was run with force_reprocess on, then restored.
        assert fp.force_reprocess is False

    def test_rejected_whisper_restores_placeholder(self, tmp_path):
        d = _write_clip(tmp_path, 6865, title="Urban County Council (1)")
        original_txt = (d / "transcript.txt").read_text()
        original_seg = (d / "transcript_segments.json").read_text()

        def bad_transcribe(audio_path, transcript_path):
            transcript_path.write_text("uh")  # far too short -> quality gate fails
            return {"text": "uh", "segments": [{"start": 0, "end": 1, "text": "uh"}]}

        (d / "audio.mp3").write_bytes(b"x")
        fp = _fake_pipeline(
            tmp_path,
            source=SimpleNamespace(download_audio=lambda cid, cd, title, date=None: "audio.mp3"),
            transcribe_audio=bad_transcribe,
            get_audio_duration=lambda p: 7200.0,
            _enrich_segments_with_captions=lambda *a, **k: None,
        )
        assert LFUCGPipeline.retranscribe_clip(fp, 6865) is False
        assert (d / "transcript.txt").read_text() == original_txt
        assert (d / "transcript_segments.json").read_text() == original_seg
        meta = json.loads((d / "metadata.json").read_text())
        assert meta["transcript_source"] == "granicus_vtt"
        assert not list(d.glob("*.bak"))
        assert fp.state["failed_clips"]["6865"]["reason"].startswith("retranscribe:transcript_quality")

    def test_download_failure_keeps_placeholder(self, tmp_path):
        d = _write_clip(tmp_path, 6865, title="Urban County Council (1)")
        fp = _fake_pipeline(
            tmp_path,
            source=SimpleNamespace(download_audio=lambda cid, cd, title, date=None: None),
            transcribe_audio=lambda *a: pytest.fail("must not transcribe without audio"),
            get_audio_duration=lambda p: None,
        )
        assert LFUCGPipeline.retranscribe_clip(fp, 6865) is False
        assert json.loads((d / "metadata.json").read_text())["transcript_source"] == "granicus_vtt"
        assert not list(d.glob("*.bak"))


class TestResetFailedAttempts:
    def test_resets_attempts_and_restamps(self, tmp_path):
        fp = _fake_pipeline(tmp_path)
        fp.state["failed_clips"] = {
            "6804": {"first_ts": "2026-06-01T00:00:00", "last_ts": "2026-06-02T00:00:00",
                     "attempts": 3, "reason": "download_failed"},
        }
        fp.state["processed_clips"] = [6825]
        reset = LFUCGPipeline.reset_failed_attempts(fp, [6804, 6816, 6825])
        assert reset == [6804, 6816]
        rec = fp.state["failed_clips"]["6804"]
        assert rec["attempts"] == 0
        assert rec["first_ts"] == rec["last_ts"] and rec["first_ts"] > "2026-09"
        assert rec["reason"].startswith("manual_retry (was: download_failed)")
        # Unknown clip gets a fresh zero-attempt row so the sweep can see it.
        assert fp.state["failed_clips"]["6816"]["attempts"] == 0
        # Already-processed clip is left alone.
        assert "6825" not in fp.state["failed_clips"]

    def test_reset_clips_are_eligible_for_retry_and_sweep(self, tmp_path):
        fp = _fake_pipeline(tmp_path)
        fp.state["failed_clips"] = {
            "6804": {"first_ts": "2026-01-01T00:00:00", "last_ts": "2026-01-02T00:00:00",
                     "attempts": 3, "reason": "download_failed"},
        }
        fp.SWEEP_WINDOW_DAYS = LFUCGPipeline.SWEEP_WINDOW_DAYS
        fp.MAX_AUTO_RETRIES = LFUCGPipeline.MAX_AUTO_RETRIES
        fp.RETRY_RECENCY_DAYS = LFUCGPipeline.RETRY_RECENCY_DAYS
        assert LFUCGPipeline._retry_candidates(fp, set()) == []
        assert LFUCGPipeline._sweep_candidates(fp, set()) == []
        LFUCGPipeline.reset_failed_attempts(fp, [6804])
        assert LFUCGPipeline._retry_candidates(fp, set()) == [6804]
        assert LFUCGPipeline._sweep_candidates(fp, set()) == [6804]
