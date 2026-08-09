"""#6: the ~2s chunk overlap must appear ONCE, not twice, in the combined
transcript. Chunks after the first are downloaded CHUNK_OVERLAP_SECONDS early,
so their opening segments re-transcribe audio the prior chunk already covered.
Those segments (adjusted start < prev chunk's last end) are dropped, and the
text is rebuilt from the deduped segments (not " ".join(raw chunk texts))."""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from main import LFUCGPipeline


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class _ChunkResult:
    def __init__(self, text, segments):
        self.text = text
        self.segments = segments


def test_chunk_overlap_deduped(tmp_path, monkeypatch):
    pipe = LFUCGPipeline(output_dir=str(tmp_path), verbose=False)

    clip_dir = tmp_path / "clips" / "1"
    clip_dir.mkdir(parents=True)
    audio_path = clip_dir / "audio.mp3"
    # >24MB so transcribe_audio takes the chunking branch (2 chunks).
    with open(audio_path, "wb") as f:
        f.seek(25 * 1024 * 1024 - 1)
        f.write(b"\0")
    transcript_path = clip_dir / "transcript.txt"

    chunk0 = clip_dir / "audio_chunk00.mp3"
    chunk1 = clip_dir / "audio_chunk01.mp3"
    chunk0.write_bytes(b"x")
    chunk1.write_bytes(b"x")

    monkeypatch.setattr(pipe, "split_audio_into_chunks",
                        lambda p, num_chunks=3: [chunk0, chunk1])
    # 100s per chunk (and for the whole-file call). Offset after chunk0 =
    # 100 - CHUNK_OVERLAP_SECONDS = 98, so chunk1 segments are shifted +98.
    monkeypatch.setattr(pipe, "get_audio_duration", lambda p: 100.0)

    # chunk1's first segment (adjusted start 98) lands BEFORE chunk0's last
    # end (100) → it's the duplicated overlap window and must be dropped.
    results = [
        _ChunkResult("A B", [_seg(0, 50, "A"), _seg(50, 100, "B")]),
        _ChunkResult("B C", [_seg(0, 5, "B"), _seg(5, 60, "C")]),
    ]
    calls = {"i": 0}

    def fake_create(**kw):
        r = results[calls["i"]]
        calls["i"] += 1
        return r

    pipe.client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=lambda **kw: fake_create(**kw))
        )
    )

    out = pipe.transcribe_audio(audio_path, transcript_path)
    assert out is not None
    segs = out["segments"]
    texts = [s["text"] for s in segs]

    # The overlapping "B" from chunk1 (adjusted start 98 < 100) is dropped.
    assert texts == ["A", "B", "C"]
    # Text is rebuilt from the deduped segments — "B" appears exactly once.
    assert out["text"] == "A B C"
    assert transcript_path.read_text() == "A B C"
    # Persisted segments match.
    saved = json.loads((clip_dir / "transcript_segments.json").read_text())
    assert [s["text"] for s in saved] == ["A", "B", "C"]
