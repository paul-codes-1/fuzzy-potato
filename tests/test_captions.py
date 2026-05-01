"""Tests for granicus_captions.py — VTT parsing, speaker alignment,
and the VTT-as-transcript placeholder rendering."""

import pytest

from granicus_captions import (
    CaptionSegment,
    CaptionTurn,
    _coalesce_turns,
    _normalize_speaker,
    _vtt_timestamp_to_seconds,
    align_speakers_to_segments,
    parse_vtt,
    speakers_for_segments,
    vtt_to_transcript_segments,
    vtt_to_transcript_text,
)


SAMPLE_VTT = """WEBVTT

00:00:00.000 --> 00:00:00.000


00:01:23.820 --> 00:01:25.820
>> Mayor Gorton: welcome

00:01:26.000 --> 00:01:28.000
everyone, it's 6:00 I will call
to order

00:01:30.000 --> 00:01:32.000
>> councilmember hale: present.

00:01:33.000 --> 00:01:35.000
>> COUNCILMEMBER HIGGINS-HORD:
>> yes, ma'am.

00:01:40.000 --> 00:01:42.000
>> Mayor Gorton: thank you.
"""


class TestVttTimestampToSeconds:
    def test_basic(self):
        assert _vtt_timestamp_to_seconds("00:00:00.000") == 0.0
        assert _vtt_timestamp_to_seconds("00:01:23.820") == 83.82
        assert _vtt_timestamp_to_seconds("01:00:00.000") == 3600.0


class TestNormalizeSpeaker:
    def test_lowercase_to_titlecase(self):
        assert _normalize_speaker("councilmember hale") == "Councilmember Hale"

    def test_uppercase_to_titlecase(self):
        assert _normalize_speaker("COUNCILMEMBER HIGGINS-HORD") == "Councilmember Higgins-hord"

    def test_already_titlecase(self):
        assert _normalize_speaker("Mayor Gorton") == "Mayor Gorton"


class TestParseVtt:
    def test_extracts_segments_with_speaker(self):
        segments, turns = parse_vtt(SAMPLE_VTT)
        # Empty 0:00 placeholder should be dropped; 5 real cues remain
        # (one of which is a continuation of the previous Mayor Gorton turn).
        assert len(segments) == 5
        assert segments[0].speaker == "Mayor Gorton"
        assert segments[0].text == "welcome"
        # Continuation cue inherits previous speaker
        assert segments[1].speaker == "Mayor Gorton"
        assert "everyone" in segments[1].text

    def test_coalesces_consecutive_same_speaker_turns(self):
        _, turns = parse_vtt(SAMPLE_VTT)
        speakers = [t.speaker for t in turns]
        # Mayor Gorton speaks first, then hale, higgins-hord, then Mayor again
        assert speakers == [
            "Mayor Gorton",
            "Councilmember Hale",
            "Councilmember Higgins-hord",
            "Mayor Gorton",
        ]

    def test_first_mayor_turn_spans_initial_two_cues(self):
        _, turns = parse_vtt(SAMPLE_VTT)
        # First turn coalesces the two Mayor Gorton cues at 1:23 and 1:26
        first = turns[0]
        assert first.start_seconds == pytest.approx(83.82)
        assert first.end_seconds == pytest.approx(88.0)
        assert "welcome" in first.text and "everyone" in first.text


class TestParseVttFiltersControlCharGarbage:
    def test_drops_lines_with_no_alphanumerics(self):
        # Real-world Granicus clip 756: VTT body was thousands of lines
        # of \x7f (DEL) bytes. Without filtering, these get concatenated
        # into a single 9000-word segment of garbage.
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:10.000 --> 00:00:12.000\n"
            "\x7f\x7f \x7f\x7f \x7f\x7f\n"
            "real speech here\n"
            "\x7f\x7f\x7f\x7f\n"
        )
        segments, _ = parse_vtt(vtt)
        assert len(segments) == 1
        assert segments[0].text == "real speech here"

    def test_drops_cue_with_only_garbage(self):
        # When the entire cue body is non-alphanumeric, the segment
        # should still emit (timestamps preserved) but with empty text,
        # which downstream filtering then drops.
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:10.000 --> 00:00:12.000\n"
            "\x7f\x7f \x7f\x7f \x7f\x7f\n"
        )
        segments, _ = parse_vtt(vtt)
        # Empty cue is dropped by the existing `text or speaker` filter
        assert segments == []

    def test_strips_control_chars_with_stray_letters(self):
        # Real-world clip 756 had ~30 letters in a 30K-char line of DEL
        # chars. Stripping control bytes leaves only the letters joined
        # by the few interleaved spaces — usually just garbage like "Bo".
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:10.000 --> 00:00:12.000\n"
            "\x7f\x7fB\x7f\x7f \x7f\x7fo\x7f\x7f hello\x7f\n"
        )
        segments, _ = parse_vtt(vtt)
        assert len(segments) == 1
        # Control chars stripped, real word retained
        assert "hello" in segments[0].text
        # No DEL chars in output
        assert "\x7f" not in segments[0].text

    def test_drops_keystroke_noise_lines(self):
        # Clip 615 had lines like `ww ww wwwwww w w ww` — stenographer
        # noise. Lines with no 3+ letter run should be dropped.
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:10.000 --> 00:00:12.000\n"
            "ww ww wwwwww w\n"
            "the meeting is open\n"
            "oo ~\n"
        )
        segments, _ = parse_vtt(vtt)
        assert len(segments) == 1
        assert segments[0].text == "the meeting is open"


class TestParseVttDedupesRollingCaptions:
    def test_collapses_consecutive_duplicate_lines_in_cue(self):
        # Granicus "rolling captions" repeat the steno buffer window —
        # a single cue can carry the same line dozens of times. Real-world
        # case (clip 5211): a 2-second cue had ~5,000 copies of "we are
        # adjourned." Dedup must collapse to one occurrence per run.
        vtt = """WEBVTT

00:09:14.365 --> 00:09:16.699
we are adjourned.
we are adjourned.
we are adjourned.
we are adjourned.

00:09:17.000 --> 00:09:19.000
to go ahead and read the
to go ahead and read the
new motion now.
"""
        segments, _ = parse_vtt(vtt)
        assert len(segments) == 2
        assert segments[0].text == "we are adjourned."
        # Second cue: dedupe the rolling line, keep the trailing new content
        assert segments[1].text == "to go ahead and read the new motion now."


class TestParseVttRejectsInterjections:
    def test_thank_you_does_not_become_a_speaker(self):
        # Real stenographer pattern observed in clip 6742: `>> thank you:`
        # appears in the middle of a Mayor-then-Chair conversation. It
        # should be folded back into the previous speaker's continuation,
        # not treated as a "Thank You" speaker change.
        vtt = """WEBVTT

00:00:10.000 --> 00:00:12.000
>> Mayor Gorton: thank you, council.

00:00:13.000 --> 00:00:15.000
>> thank you: this question is for staff.

00:00:16.000 --> 00:00:18.000
>> Chair: please respond.
"""
        _, turns = parse_vtt(vtt)
        speakers = [t.speaker for t in turns]
        assert "Thank You" not in speakers
        # Mayor's turn should absorb the misattributed cue
        assert speakers[0] == "Mayor Gorton"
        assert "this question is for staff" in turns[0].text


class TestCaptionTurnOverlap:
    def test_full_overlap(self):
        t = CaptionTurn(10.0, 20.0, "X", "")
        assert t.overlap_seconds(12.0, 18.0) == 6.0

    def test_partial_overlap(self):
        t = CaptionTurn(10.0, 20.0, "X", "")
        assert t.overlap_seconds(15.0, 25.0) == 5.0

    def test_no_overlap(self):
        t = CaptionTurn(10.0, 20.0, "X", "")
        assert t.overlap_seconds(20.0, 25.0) == 0.0
        assert t.overlap_seconds(0.0, 10.0) == 0.0


class TestAlignSpeakersToSegments:
    def _turns(self):
        return [
            CaptionTurn(0.0, 10.0, "Alice", "intro"),
            CaptionTurn(10.0, 20.0, "Bob", "reply"),
            CaptionTurn(20.0, 30.0, "Alice", "rebuttal"),
        ]

    def test_segment_inside_one_turn(self):
        whisper = [{"start": 2.0, "end": 8.0, "text": "..."}]
        result = align_speakers_to_segments(whisper, self._turns())
        assert result[0]["speaker"] == "Alice"

    def test_segment_spans_two_turns_picks_max_overlap(self):
        # 8s of Alice (2..10) vs 2s of Bob (10..12) → Alice wins
        whisper = [{"start": 2.0, "end": 12.0, "text": "..."}]
        result = align_speakers_to_segments(whisper, self._turns())
        assert result[0]["speaker"] == "Alice"

    def test_segment_outside_all_turns_gets_none(self):
        whisper = [{"start": 100.0, "end": 110.0, "text": "..."}]
        result = align_speakers_to_segments(whisper, self._turns())
        assert result[0]["speaker"] is None

    def test_does_not_mutate_input(self):
        whisper = [{"start": 2.0, "end": 8.0, "text": "..."}]
        align_speakers_to_segments(whisper, self._turns())
        assert "speaker" not in whisper[0]


class TestVttToTranscriptSegments:
    def test_renders_whisper_compatible_dicts(self):
        segments, _ = parse_vtt(SAMPLE_VTT)
        out = vtt_to_transcript_segments(segments)
        assert all("start" in s and "end" in s and "text" in s for s in out)
        assert all("speaker" in s for s in out)

    def test_skips_empty_text(self):
        segments = [
            CaptionSegment(0.0, 1.0, "Alice", "hello"),
            CaptionSegment(1.0, 2.0, "Alice", ""),
            CaptionSegment(2.0, 3.0, "Alice", "world"),
        ]
        out = vtt_to_transcript_segments(segments)
        assert len(out) == 2
        assert [s["text"] for s in out] == ["hello", "world"]


class TestVttToTranscriptText:
    def test_speaker_prefixes(self):
        segments = [
            CaptionSegment(0.0, 1.0, "Alice", "hello"),
            CaptionSegment(1.0, 2.0, "Bob", "hi back"),
        ]
        text = vtt_to_transcript_text(segments)
        assert "Alice: hello" in text
        assert "Bob: hi back" in text

    def test_coalesces_same_speaker(self):
        segments = [
            CaptionSegment(0.0, 1.0, "Alice", "hello"),
            CaptionSegment(1.0, 2.0, "Alice", "world"),
        ]
        text = vtt_to_transcript_text(segments)
        # Single Alice line, not two
        assert text.count("Alice:") == 1
        assert "hello world" in text


class TestSpeakersForSegments:
    def test_dedupe_and_preserve_order(self):
        enriched = [
            {"speaker": "Alice"},
            {"speaker": "Bob"},
            {"speaker": "Alice"},  # duplicate
            {"speaker": None},
            {"speaker": "Carol"},
        ]
        assert speakers_for_segments(enriched) == ["Alice", "Bob", "Carol"]

    def test_empty(self):
        assert speakers_for_segments([]) == []

    def test_only_nones(self):
        assert speakers_for_segments([{"speaker": None}]) == []
