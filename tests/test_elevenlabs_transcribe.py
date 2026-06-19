"""Unit tests for the ElevenLabs Scribe adapter's word→segment coalescing.

Pure-function tests — no network, no API key needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elevenlabs_transcribe import group_words_into_segments, _clean


def _w(text, start, end, type_="word"):
    return {"text": text, "start": start, "end": end, "type": type_}


def test_splits_on_sentence_punctuation():
    words = [
        _w("Hello", 0.0, 0.4),
        _w("there.", 0.4, 0.9),
        _w("Next", 1.0, 1.3),
        _w("one.", 1.3, 1.7),
    ]
    segs = group_words_into_segments(words)
    assert len(segs) == 2
    assert segs[0]["text"] == "Hello there."
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 0.9
    assert segs[1]["text"] == "Next one."


def test_splits_on_long_pause():
    words = [
        _w("Motion", 0.0, 0.5),
        _w("carries", 0.5, 1.0),
        # 3s gap → new segment even with no punctuation
        _w("Next", 4.0, 4.3),
        _w("item", 4.3, 4.6),
    ]
    segs = group_words_into_segments(words, max_gap_secs=1.5)
    assert len(segs) == 2
    assert segs[0]["text"] == "Motion carries"
    assert segs[1]["text"] == "Next item"


def test_hard_word_cap():
    words = [_w(f"w{i}", float(i), float(i) + 0.4) for i in range(50)]
    segs = group_words_into_segments(words, max_words=22, max_gap_secs=999)
    # 50 words capped at 22 → 22 + 22 + 6
    assert [len(s["text"].split()) for s in segs] == [22, 22, 6]


def test_skips_spacing_and_audio_events():
    words = [
        _w("Quiet", 0.0, 0.4),
        _w(" ", 0.4, 0.4, type_="spacing"),
        _w("(laughter)", 0.5, 1.0, type_="audio_event"),
        _w("room.", 1.0, 1.4),
    ]
    segs = group_words_into_segments(words)
    assert len(segs) == 1
    assert segs[0]["text"] == "Quiet room."


def test_clean_pulls_space_off_separate_punctuation_token():
    # When Scribe emits punctuation as its own token, join+clean must
    # reattach it rather than leave "word ." spacing.
    assert _clean("the meeting .") == "the meeting."
    assert _clean("yes , and no ?") == "yes, and no?"


def test_empty_input():
    assert group_words_into_segments([]) == []
