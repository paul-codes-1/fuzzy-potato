"""Granicus closed-caption (WebVTT) ingestion.

Granicus serves live-CC WebVTT alongside the m3u8 stream for ~60% of
clips. The captions are stenographer-quality (typos and broken sentences
are common) but they have one critical thing Whisper-1 doesn't: speaker
attributions, marked with `>> Speaker Name:` at each speaker change.

Two use cases this module supports:

1. **Speaker enrichment** — for clips that already have a Whisper
   transcript, parse VTT into speaker turns and align them onto Whisper
   segments by timestamp overlap. The Whisper text remains canonical;
   we only fold in the speaker attribution.

2. **VTT-as-placeholder transcript** — for clips that haven't been run
   through Whisper yet, render the VTT directly as a transcript so the
   meeting page is searchable / RAG-ingestible immediately. The page
   discloses that this is a placeholder; a Whisper pass replaces it
   later.

VTT URL discovery uses yt-dlp (already a project dependency); calling it
as a subprocess keeps the integration uniform with how main.py downloads
audio. Result is cached in `lfucg_output/clips/<id>/captions.vtt`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, List, Optional

# `>> Speaker Name:` at the start of a cue marks a speaker change. The
# stenographer convention sometimes uses a single `>` or omits the colon
# on continuation lines, so we accept either. Speaker name captures
# letters, spaces, hyphens, periods, and apostrophes.
_SPEAKER_RE = re.compile(r"^>>\s*([A-Za-z][A-Za-z .'\-]*?)\s*:\s*(.*)$")
# Stenographers occasionally type interjections before a colon
# (`>> thank you: this question is...`). These look like speaker names
# to the regex but aren't — drop them so a real speaker isn't replaced
# by an interjection a few cues later.
_NON_SPEAKER_PHRASES = frozenset({
    "thank you", "thanks", "yes", "no", "ok", "okay", "all right",
    "alright", "right", "welcome", "please", "well", "so", "hello",
    "good morning", "good afternoon", "good evening",
})
# Old Granicus clips occasionally serve VTT where the body is corrupted —
# usually long runs of \x7f (DEL) or other ASCII control bytes from a
# malformed live-CC encoder, sometimes with a handful of stray real
# letters mixed in. Strip control characters from every line so what
# survives is either real text or empty (then the empty-cue filter
# downstream drops it).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# After stripping control chars, lines like `oo ~`, `ww ww wwwwww w`,
# or `|` remain. Real speech contains at least one word with 3+ letters
# AND at least 2 distinct letters (e.g. "the", "and", "you" — not "www"
# or "ooo"). Reject lines that lack one — they're stenographer
# keystroke noise.
_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _has_real_word(line: str) -> bool:
    for w in _WORD_RE.findall(line):
        if len(set(w.lower())) >= 2:
            return True
    return False
_TIMESTAMP_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)
# yt-dlp doesn't expose a "subs only" mode that bypasses m3u8 negotiation,
# so we run it normally with --skip-download. Output template forces a
# predictable filename so we can read it back.
_YTDLP_VTT_TEMPLATE = "captions.%(ext)s"


@dataclass
class CaptionTurn:
    """A continuous block of caption text spoken by one identified speaker.

    `start_seconds` / `end_seconds` are floats in transcript-friendly
    seconds (matches Whisper segment shape). Multiple raw VTT cues may
    fold into a single CaptionTurn when they all belong to the same
    speaker without an intervening `>>` marker.
    """

    start_seconds: float
    end_seconds: float
    speaker: Optional[str]
    text: str

    def overlap_seconds(self, start: float, end: float) -> float:
        """How many seconds of `[start, end]` fall inside this turn."""
        if end <= self.start_seconds or start >= self.end_seconds:
            return 0.0
        return min(end, self.end_seconds) - max(start, self.start_seconds)


@dataclass
class CaptionSegment:
    """A single VTT cue, used when we need fine-grained text+timestamp
    pairs (vtt-as-transcript placeholder mode). Speaker is inherited
    from the most-recent `>>` marker.
    """

    start_seconds: float
    end_seconds: float
    speaker: Optional[str]
    text: str


def _vtt_timestamp_to_seconds(ts: str) -> float:
    """`HH:MM:SS.mmm` → float seconds."""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(vtt_text: str) -> tuple[List[CaptionSegment], List[CaptionTurn]]:
    """Parse a WebVTT body into per-cue segments and per-speaker turns.

    Returns (segments, turns). Turns coalesce consecutive cues that
    share a speaker — useful for alignment against Whisper segments,
    which may have different boundaries from the VTT cues.
    """
    segments: List[CaptionSegment] = []
    cur_speaker: Optional[str] = None
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    cur_text_lines: List[str] = []

    def _flush_cue() -> None:
        # Dedupe consecutive identical lines within a single cue. Granicus
        # "rolling captions" repeat the steno's buffer window and can stuff
        # a single 2-second cue with hundreds of duplicate lines (clip
        # 5211 had a 27K-char `we are adjourned.` cue). Keeping only the
        # first occurrence in each consecutive run preserves real speech
        # while collapsing buffer artifacts.
        deduped: List[str] = []
        prev_line: Optional[str] = None
        for ln in cur_text_lines:
            if ln == prev_line:
                continue
            deduped.append(ln)
            prev_line = ln
        segments.append(
            CaptionSegment(
                start_seconds=cur_start,
                end_seconds=cur_end or cur_start,
                speaker=cur_speaker,
                text=" ".join(deduped).strip(),
            )
        )

    for raw in vtt_text.splitlines():
        # Strip control bytes early so a corrupted body that has a
        # handful of stray letters doesn't slip past the alnum check.
        line = _CONTROL_CHARS.sub("", raw).rstrip()
        m = _TIMESTAMP_RE.match(line)
        if m:
            if cur_start is not None:
                _flush_cue()
            cur_start = _vtt_timestamp_to_seconds(m.group("start"))
            cur_end = _vtt_timestamp_to_seconds(m.group("end"))
            cur_text_lines = []
            continue
        if not line or line == "WEBVTT" or line.startswith("NOTE"):
            continue
        # Skip body lines with no real word — typically stenographer
        # keystroke noise that survived control-char stripping. Speaker
        # markers (`>>`) are exempt: a line like `>> Mayor:` is valid
        # even though "Mayor" is the only word.
        if not line.startswith(">>") and not _has_real_word(line):
            continue
        speaker_match = _SPEAKER_RE.match(line)
        if speaker_match:
            candidate = speaker_match.group(1).strip()
            tail = speaker_match.group(2).strip()
            if candidate.lower() in _NON_SPEAKER_PHRASES:
                # Treat the whole cue as continuation text — keep the
                # previous speaker, fold the matched bit back into the
                # text body so the words aren't lost.
                if cur_start is not None:
                    cur_text_lines.append(f"{candidate}: {tail}".strip())
            else:
                cur_speaker = _normalize_speaker(candidate)
                if tail:
                    cur_text_lines.append(tail)
        elif cur_start is not None:
            cur_text_lines.append(line)

    if cur_start is not None:
        _flush_cue()

    # Drop empty cues (VTT files often start with a 00:00:00 placeholder).
    segments = [s for s in segments if s.text or s.speaker]

    turns = _coalesce_turns(segments)
    return segments, turns


def _normalize_speaker(raw: str) -> str:
    """Title-case the speaker name and collapse whitespace.

    The stenographer convention is inconsistent: sometimes
    `Mayor Gorton`, sometimes `councilmember hale`, sometimes
    `COUNCILMEMBER HIGGINS-HORD`. Title-casing produces a stable
    representation downstream code can dedupe on.
    """
    return " ".join(part.capitalize() for part in raw.split())


def _coalesce_turns(segments: Iterable[CaptionSegment]) -> List[CaptionTurn]:
    """Merge adjacent same-speaker cues into one turn.

    Coalesced turns are what we align Whisper segments against —
    a single `Mayor Gorton` turn that spans 30 seconds is more useful
    than 8 separate cues, all of which would individually overlap
    multiple Whisper segments.
    """
    turns: List[CaptionTurn] = []
    for seg in segments:
        if turns and turns[-1].speaker == seg.speaker and seg.speaker is not None:
            prev = turns[-1]
            prev.end_seconds = max(prev.end_seconds, seg.end_seconds)
            prev.text = (prev.text + " " + seg.text).strip()
        else:
            turns.append(
                CaptionTurn(
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    speaker=seg.speaker,
                    text=seg.text,
                )
            )
    return turns


def align_speakers_to_segments(
    whisper_segments: List[dict],
    turns: List[CaptionTurn],
) -> List[dict]:
    """Attach a `speaker` field to each Whisper segment using max-overlap.

    For each Whisper segment we find the caption turn whose time range
    overlaps it most. Ties (two turns equally overlap) resolve to the
    earlier turn — matches what a reader skimming would assume. Whisper
    segments that fall entirely outside any turn (rare; usually the
    head/tail of a clip) get `speaker=None`.

    Returns a *new* list — does not mutate the input.
    """
    enriched: List[dict] = []
    for seg in whisper_segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        best_turn = None
        best_overlap = 0.0
        for turn in turns:
            if turn.speaker is None:
                continue
            ovl = turn.overlap_seconds(start, end)
            if ovl > best_overlap:
                best_overlap = ovl
                best_turn = turn
        new_seg = dict(seg)
        new_seg["speaker"] = best_turn.speaker if best_turn else None
        enriched.append(new_seg)
    return enriched


def vtt_to_transcript_segments(segments: List[CaptionSegment]) -> List[dict]:
    """Render parsed VTT cues into Whisper-compatible segment dicts.

    Used as a placeholder for un-Whispered clips so the page is still
    searchable. Schema mirrors what Whisper writes so downstream code
    (RAG ingest, frontend transcript tab) doesn't need to branch.
    """
    out: List[dict] = []
    for i, seg in enumerate(segments):
        if not seg.text.strip():
            continue
        out.append(
            {
                "id": i,
                "start": round(seg.start_seconds, 3),
                "end": round(seg.end_seconds, 3),
                "text": seg.text,
                "speaker": seg.speaker,
            }
        )
    return out


def vtt_to_transcript_text(segments: List[CaptionSegment]) -> str:
    """Plain-text rendering of VTT for the .txt transcript file.

    Format:
        Speaker Name: utterance one. utterance two.

        Other Speaker: utterance.

    A blank line separates speaker turns. When the speaker is unknown
    (continuation cue with no preceding `>>` marker), no prefix is
    written.
    """
    turns = _coalesce_turns(segments)
    lines: List[str] = []
    for turn in turns:
        if not turn.text.strip():
            continue
        if turn.speaker:
            lines.append(f"{turn.speaker}: {turn.text}")
        else:
            lines.append(turn.text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def download_vtt(
    granicus_url: str,
    out_path: Path,
    *,
    timeout_seconds: int = 60,
) -> Optional[Path]:
    """Pull the English VTT track from a Granicus player URL via yt-dlp.

    Returns `out_path` on success, or None when no captions exist /
    yt-dlp fails. Caller is responsible for deciding whether absence is
    a hard error.

    `out_path` is the final destination filename (e.g.
    `lfucg_output/clips/6757/captions.vtt`). yt-dlp writes a
    `<basename>.en.vtt` so we move it into place after.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # yt-dlp writes <output_template>.<lang>.vtt — strip the .vtt suffix
    # to get the template, since yt-dlp re-appends it.
    template = str(out_path.with_suffix(""))
    cmd = [
        "yt-dlp",
        "--write-subs",
        "--sub-langs",
        "en",
        "--skip-download",
        "--quiet",
        "-o",
        f"{template}.%(ext)s",
        granicus_url,
    ]
    try:
        subprocess.run(cmd, timeout=timeout_seconds, check=True, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    expected = Path(f"{template}.en.vtt")
    if not expected.exists():
        return None
    expected.rename(out_path)
    return out_path


def speakers_for_segments(enriched: List[dict]) -> List[str]:
    """Deduped, in-order list of distinct speakers across enriched segments.

    Useful for the Overview tab + extracted_facts attendance — answers
    "who spoke during this meeting" without round-tripping the full
    transcript.
    """
    seen = set()
    out: List[str] = []
    for seg in enriched:
        sp = seg.get("speaker")
        if sp and sp not in seen:
            seen.add(sp)
            out.append(sp)
    return out
