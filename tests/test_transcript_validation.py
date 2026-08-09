"""Tests for the shared transcript quality gate (validate_transcript) and the
--repair-short-transcripts candidate detection (find_short_transcripts).

The gate exists because prod accepted ANY truthy VTT/Whisper transcript as
final and then skipped the clip forever — real casualties were Whisper
repetition loops and sub-1KB (half-captured) transcripts.
"""

import json
from types import SimpleNamespace

from main import LFUCGPipeline, validate_transcript


def _good_transcript(words=3000):
    # A varied, natural-looking transcript (no repetition loop).
    base = (
        "The council moved to approve the rezoning request after hearing "
        "public comment from several residents about traffic and drainage "
        "concerns near the proposed development on Richmond Road today. "
    )
    out = []
    while len(" ".join(out).split()) < words:
        out.append(base + f"Item number {len(out)} was discussed at length. ")
    return " ".join(out)


class TestValidateTranscript:
    def test_good_transcript_passes(self):
        # ~2h meeting, dense transcript
        text = _good_transcript(words=12000)
        ok, reason = validate_transcript(text, duration_seconds=7200)
        assert ok, reason

    def test_good_transcript_no_duration_passes(self):
        ok, reason = validate_transcript(_good_transcript(500), duration_seconds=None)
        assert ok, reason

    def test_welsh_style_repetition_loop_fails_joined(self):
        """A Whisper loop that emits one phrase thousands of times, joined
        into one line with no punctuation/newlines — caught by the n-gram
        dominance check."""
        text = " ".join(["thank you very much for that"] * 500)  # 3000 words
        ok, reason = validate_transcript(text, duration_seconds=7200)
        assert not ok
        assert "repetition_loop" in reason

    def test_repetition_loop_fails_linewise(self):
        """The same loop as separate lines — caught by the line-run check."""
        text = "\n".join(["thank you"] * 200)
        ok, reason = validate_transcript(text, duration_seconds=7200)
        assert not ok
        assert "repetition" in reason or "dominant" in reason

    def test_six_byte_transcript_fails(self):
        ok, reason = validate_transcript("hello!", duration_seconds=7200)
        assert not ok
        assert "too_short" in reason

    def test_tiny_transcript_no_duration_fails(self):
        ok, reason = validate_transcript("one two three", duration_seconds=None)
        assert not ok
        assert "too_short" in reason

    def test_half_coverage_fails(self):
        """A 2h clip that yields only a few thousand words implies most of the
        meeting was never captured (a dropped chunk)."""
        text = _good_transcript(words=1500)  # ~1500 words over 7200s ⇒ ~12 wpm
        ok, reason = validate_transcript(text, duration_seconds=7200)
        assert not ok
        assert "low_coverage" in reason

    def test_empty_fails(self):
        ok, reason = validate_transcript("", duration_seconds=100)
        assert not ok
        assert reason == "empty"

    def test_short_clip_lenient(self):
        """A genuinely short clip (2 min) with a modest transcript passes —
        the floor scales down and the coverage check is skipped under 5 min."""
        ok, reason = validate_transcript(_good_transcript(200), duration_seconds=120)
        assert ok, reason


class TestFindShortTranscripts:
    """--repair-short-transcripts keys on file SIZE + validate_transcript, not
    mere presence, and skips placeholder/document-driven transcripts."""

    @staticmethod
    def _mk_clip(clips_dir, cid, transcript_text, source="whisper-1", segments=None):
        d = clips_dir / str(cid)
        d.mkdir(parents=True)
        tname = f"transcript_{cid}.txt"
        (d / tname).write_text(transcript_text, encoding="utf-8")
        files = {"transcript": tname}
        if segments is not None:
            (d / "segs.json").write_text(json.dumps(segments))
            files["transcript_segments"] = "segs.json"
        (d / "metadata.json").write_text(json.dumps({
            "clip_id": cid,
            "transcript_source": source,
            "files": files,
        }))
        return d

    def _pipe(self, tmp_path):
        # Unbound-style stand-in: only output_dir + SHORT_TRANSCRIPT_BYTES_FLOOR
        # are needed by find_short_transcripts.
        return SimpleNamespace(
            output_dir=tmp_path,
            SHORT_TRANSCRIPT_BYTES_FLOOR=LFUCGPipeline.SHORT_TRANSCRIPT_BYTES_FLOOR,
        )

    def test_flags_tiny_file(self, tmp_path):
        clips = tmp_path / "clips"
        self._mk_clip(clips, 1, "too small")  # < 1KB
        out = LFUCGPipeline.find_short_transcripts(self._pipe(tmp_path))
        assert [c for c, _ in out] == [1]
        assert out[0][1].startswith("size:")

    def test_flags_repetition_loop(self, tmp_path):
        clips = tmp_path / "clips"
        loop = " ".join(["thank you very much"] * 600)  # big file, but a loop
        self._mk_clip(clips, 2, loop, segments=[{"start": 0, "end": 7200}])
        out = LFUCGPipeline.find_short_transcripts(self._pipe(tmp_path))
        assert [c for c, _ in out] == [2]
        assert "repetition_loop" in out[0][1]

    def test_ignores_good_transcript(self, tmp_path):
        clips = tmp_path / "clips"
        self._mk_clip(clips, 3, _good_transcript(12000),
                      segments=[{"start": 0, "end": 7200}])
        out = LFUCGPipeline.find_short_transcripts(self._pipe(tmp_path))
        assert out == []

    def test_skips_placeholder_sources(self, tmp_path):
        clips = tmp_path / "clips"
        # A tiny granicus_vtt placeholder must NOT be flagged (no better source).
        self._mk_clip(clips, 4, "short", source="granicus_vtt")
        self._mk_clip(clips, 5, "short", source="civicclerk_minutes")
        out = LFUCGPipeline.find_short_transcripts(self._pipe(tmp_path))
        assert out == []

    def test_respects_max_clips(self, tmp_path):
        clips = tmp_path / "clips"
        for cid in (10, 11, 12):
            self._mk_clip(clips, cid, "tiny")
        out = LFUCGPipeline.find_short_transcripts(self._pipe(tmp_path), max_clips=2)
        assert len(out) == 2
