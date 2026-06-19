"""ElevenLabs Scribe speech-to-text adapter.

A drop-in alternative to the OpenAI Whisper path in ``main.py``. It returns
the SAME ``{"text", "segments": [{start, end, text}]}`` shape so
``LFUCGPipeline.process_clip`` can use either transcriber without branching
any downstream step (facts, summary, RAG ingest, search index).

Why Scribe for the backfill:
- 5 GB upload limit (vs Whisper's 25 MB) → no chunk-split / offset math.
- Word-level timestamps → we coalesce them into sentence-ish segments that
  mirror Whisper's segment granularity (clickable video deep-links).
- ``audio_duration_secs`` comes back in the response → exact cost accounting.

API contract (POST https://api.elevenlabs.io/v1/speech-to-text):
- header ``xi-api-key``
- multipart form: ``model_id`` (scribe_v1), ``file``, optional
  ``language_code`` / ``timestamps_granularity`` / ``diarize`` /
  ``tag_audio_events``.
- response: ``text``, ``words: [{text, start, end, type, speaker_id, ...}]``,
  ``audio_duration_secs``, ``language_code``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_MODEL = "scribe_v1"


class ScribeCreditsExhausted(Exception):
    """Raised when the ElevenLabs account is out of credits / over quota.

    Distinct from a transient/transport error so a batch runner can detect
    "the prepaid pool is drained" and switch transcribers, rather than
    burning download bandwidth retrying clip after clip.
    """

# Segment-coalescing knobs. A Scribe "word" is a single token; Whisper
# segments are roughly one sentence / clause. We flush a segment on sentence
# punctuation, a long pause, or a hard word cap so a runaway monologue still
# gets periodic timestamps for video seeking.
_MAX_GAP_SECS = 1.5
_MAX_WORDS = 22
_SENTENCE_END = (".", "!", "?")

# Collapse whitespace and pull stray spaces back off punctuation, so a
# segment reads cleanly whether Scribe attaches punctuation to the word token
# or emits it as its own token.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:])")
_MULTISPACE = re.compile(r"\s{2,}")


def _clean(text: str) -> str:
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def group_words_into_segments(
    words: List[Dict[str, Any]],
    *,
    max_gap_secs: float = _MAX_GAP_SECS,
    max_words: int = _MAX_WORDS,
) -> List[Dict[str, Any]]:
    """Coalesce Scribe word tokens into Whisper-shaped segments.

    Only ``type == "word"`` entries carry timing/content; ``spacing`` and
    ``audio_event`` entries are skipped. Pure function (no I/O) so it can be
    unit-tested without the network.
    """
    segments: List[Dict[str, Any]] = []
    buf: List[Dict[str, Any]] = []

    def flush() -> None:
        if not buf:
            return
        text = _clean(" ".join(str(w.get("text", "")) for w in buf))
        if text:
            segments.append(
                {
                    "start": round(float(buf[0].get("start", 0.0)), 3),
                    "end": round(float(buf[-1].get("end", 0.0)), 3),
                    "text": text,
                }
            )
        buf.clear()

    # Filter to word tokens up front so gap-to-next-word ignores spacing.
    word_tokens = [
        w for w in words
        if w.get("type", "word") == "word" and str(w.get("text", "")).strip()
    ]

    for i, w in enumerate(word_tokens):
        buf.append(w)
        tok = str(w.get("text", "")).strip()
        nxt = word_tokens[i + 1] if i + 1 < len(word_tokens) else None
        gap = None
        if nxt is not None:
            try:
                gap = float(nxt.get("start", 0.0)) - float(w.get("end", 0.0))
            except (TypeError, ValueError):
                gap = None

        if (
            tok.endswith(_SENTENCE_END)
            or len(buf) >= max_words
            or (gap is not None and gap > max_gap_secs)
        ):
            flush()

    flush()
    return segments


def transcribe_with_scribe(
    audio_path: Path,
    *,
    api_key: Optional[str] = None,
    model_id: str = DEFAULT_MODEL,
    language_code: Optional[str] = "en",
    diarize: bool = False,
    tag_audio_events: bool = False,
    timeout_seconds: int = 1200,
) -> Optional[Dict[str, Any]]:
    """Transcribe one audio file via ElevenLabs Scribe.

    Returns ``{"text", "segments", "audio_duration_secs", "language_code"}``
    or ``None`` on a too-short/empty result. Raises ``requests.HTTPError`` on
    API failure so the caller's retry/skip machinery can react (mirrors the
    Whisper path returning None on its own caught errors).
    """
    audio_path = Path(audio_path)
    api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    data: Dict[str, str] = {
        "model_id": model_id,
        "timestamps_granularity": "word",
        "tag_audio_events": "true" if tag_audio_events else "false",
        "diarize": "true" if diarize else "false",
    }
    if language_code:
        data["language_code"] = language_code

    with open(audio_path, "rb") as fh:
        files = {"file": (audio_path.name, fh, "audio/mpeg")}
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            data=data,
            files=files,
            timeout=timeout_seconds,
        )

    # Credit/quota exhaustion → distinct signal. ElevenLabs returns 401 with a
    # `quota_exceeded`/`max_character_limit_exceeded` status, or 402/429, when
    # the prepaid pool is drained. Detect by status + body so the runner can
    # stop the Scribe phase cleanly.
    if resp.status_code in (401, 402, 429):
        body = (resp.text or "").lower()
        if any(k in body for k in ("quota", "credit", "limit_exceeded", "exceeded", "insufficient")):
            raise ScribeCreditsExhausted(f"{resp.status_code}: {resp.text[:300]}")

    resp.raise_for_status()
    payload = resp.json()

    text = (payload.get("text") or "").strip()
    if not text or len(text) <= 50:
        return None

    segments = group_words_into_segments(payload.get("words") or [])
    return {
        "text": text,
        "segments": segments or None,
        "audio_duration_secs": payload.get("audio_duration_secs"),
        "language_code": payload.get("language_code"),
    }
