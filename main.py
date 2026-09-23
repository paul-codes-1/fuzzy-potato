#!/usr/bin/env python3
"""
LFUCG Meeting Pipeline - Standalone Version
Downloads, transcribes, and generates articles for LFUCG meeting clips.

Usage:
    python lfucg_pipeline.py 6650                    # Process single clip
    python lfucg_pipeline.py 6650 6660               # Process range
    python lfucg_pipeline.py --auto                  # Auto-increment from last
    python lfucg_pipeline.py --scrape                # Scrape and process all new

Requirements:
    pip install yt-dlp openai requests beautifulsoup4 lxml

    System: ffmpeg must be installed
"""

import os
import shutil
import sys
import json
import argparse
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import re
from collections import Counter

from dotenv import load_dotenv
import httpx

from config import classify_meeting_body, get_config
from sources import MeetingRef, make_agenda_source, make_source
from granicus_captions import (
    align_speakers_to_segments,
    parse_vtt,
    speakers_for_segments,
    vtt_to_transcript_segments,
    vtt_to_transcript_text,
)

# Load environment variables from .env file
load_dotenv()

# Check for OpenAI library
try:
    from openai import OpenAI
except ImportError:
    print("Error: openai library not installed. Run: pip install openai")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Transcript quality gate
# ---------------------------------------------------------------------------
# A truthy VTT/Whisper transcript is NOT automatically usable. Real prod
# casualties: (a) Whisper repetition loops that emit thousands of words of one
# repeated phrase, and (b) sub-1KB transcripts where an audio chunk silently
# dropped. Accepting either wrote metadata.json with files.transcript, marking
# the clip "done" forever. This shared gate rejects garbage so the pipeline can
# fall through to the other transcription path instead of caching it. The
# repetition-loop idea is ported from scripts/local_whisper_backfill.py's
# qa_check (which works on segments); here we detect it in raw text via a
# word-n-gram dominance check plus a line-run check.

# Assumed word density of a FULLY transcribed meeting (words/min). A 2-3h
# council meeting averages ~100-150 wpm of actual captured speech.
TRANSCRIPT_FULL_WPM = 110
# Coverage floor: below ~half the full density we assume ≥50% of the meeting
# is missing (a dropped chunk / truncated upload).
TRANSCRIPT_COVERAGE_MIN_WPM = 55
# Absolute word floor for a multi-hour clip regardless of the coverage math.
TRANSCRIPT_ABS_MIN_WORDS = 200
# Below this we don't bother with the coverage check (tiny clips are legit).
TRANSCRIPT_MIN_WORDS_SHORT = 20
TRANSCRIPT_COVERAGE_MIN_DURATION = 300  # seconds
# Repetition-loop detection.
TRANSCRIPT_REPEAT_MAX_RUN = 8           # consecutive identical lines
TRANSCRIPT_DOMINANCE_FRACTION = 0.30    # one line/phrase > this share of all
TRANSCRIPT_DOMINANCE_MIN_LINES = 40
TRANSCRIPT_NGRAM_SIZE = 6               # word-window for the joined-text loop check
TRANSCRIPT_NGRAM_FRACTION = 0.25        # one 6-gram > this share ⇒ short-period loop
# Distinct-6-gram ratio floor. A period-P loop yields ~P distinct 6-grams over
# N total, so the ratio collapses toward 0 no matter the phrase length (the
# dominance check alone misses long-period loops); natural speech sits ≥0.8.
TRANSCRIPT_NGRAM_MIN_UNIQUE_RATIO = 0.10
TRANSCRIPT_NGRAM_MIN_WORDS = 60


# Meeting bodies whose Granicus live-caption (VTT) track must NOT become the
# final transcript. The stenographer feed on Council / Work Session /
# Committee of the Whole / Planning Commission clips is what people ask the
# RAG about most, and it ships with 0 speaker labels and dropped sentences —
# votes were being extracted from it. For these bodies the VTT is only an
# immediate placeholder (page goes live now); Whisper runs in the same
# process_clip pass (or later via --retranscribe-placeholders) and the VTT
# is folded back in for speaker labels. Override the regex per deployment
# with LFUCG_WHISPER_REQUIRED_BODIES (empty string disables).
DEFAULT_WHISPER_REQUIRED_BODIES = (
    r"urban county council|council work session|committee of the whole|planning commission"
)


def _whisper_required_re() -> Optional["re.Pattern[str]"]:
    pattern = os.getenv("LFUCG_WHISPER_REQUIRED_BODIES", DEFAULT_WHISPER_REQUIRED_BODIES)
    if not pattern.strip():
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(DEFAULT_WHISPER_REQUIRED_BODIES, re.IGNORECASE)


def requires_whisper(title: Optional[str], meeting_body: Optional[str] = None,
                     pattern: Optional[str] = None) -> bool:
    """True when a clip's title/body names a body that must be Whispered
    (see DEFAULT_WHISPER_REQUIRED_BODIES). ``pattern`` overrides the env."""
    rx = re.compile(pattern, re.IGNORECASE) if pattern is not None else _whisper_required_re()
    if rx is None:
        return False
    hay = f"{title or ''} {meeting_body or ''}"
    return bool(rx.search(hay))


def validate_transcript(text: Optional[str],
                        duration_seconds: Optional[float] = None) -> tuple[bool, str]:
    """Gate a transcript before it's accepted as final. Returns ``(ok, reason)``.

    Rejects, in order: (a) too-short transcripts (scaled to duration, absolute
    floor for multi-hour clips), (b) repetition loops where a few distinct
    lines or word-n-grams dominate, and (c) low coverage (implied words/min far
    below a plausible meeting density ⇒ a dropped chunk). ``duration_seconds``
    may be None (VTT/Whisper paths pass the audio duration or the last segment
    end when known); the coverage check is skipped when it's unavailable.
    """
    if not text or not text.strip():
        return False, "empty"

    words = text.split()
    n_words = len(words)

    # (a) Too short. Scale the floor to duration, capped at the absolute floor
    # so a multi-hour clip always needs at least TRANSCRIPT_ABS_MIN_WORDS.
    if duration_seconds and duration_seconds > 0:
        scaled = int((duration_seconds / 60.0) * TRANSCRIPT_COVERAGE_MIN_WPM)
        min_words = min(TRANSCRIPT_ABS_MIN_WORDS, max(TRANSCRIPT_MIN_WORDS_SHORT, scaled))
    else:
        min_words = TRANSCRIPT_MIN_WORDS_SHORT
    if n_words < min_words:
        return False, f"too_short:{n_words}w<{min_words}"

    # (b) Repetition loop — checked BEFORE coverage because a loop is also
    # (trivially) low-coverage, and "repetition_loop" is the more actionable
    # diagnosis.
    # Line-run / line-dominance: catches VTT-placeholder and segment-joined
    # transcripts that carry newlines.
    lines = [re.sub(r"\s+", " ", ln.strip().lower())
             for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        run = best_run = 1
        for a, b in zip(lines, lines[1:]):
            if b and a == b:
                run += 1
                best_run = max(best_run, run)
            else:
                run = 1
        if best_run >= TRANSCRIPT_REPEAT_MAX_RUN:
            return False, f"repetition_loop:{best_run}x_line"
        if len(lines) >= TRANSCRIPT_DOMINANCE_MIN_LINES:
            top_line, top_n = Counter(lines).most_common(1)[0]
            if top_n / len(lines) > TRANSCRIPT_DOMINANCE_FRACTION:
                return False, f"dominant_line:{top_n}/{len(lines)}"

    # Word-n-gram dominance: catches loops joined into one long line with no
    # newlines/punctuation (e.g. "thank you thank you thank you ..."), which
    # the line check can't see.
    if n_words >= TRANSCRIPT_NGRAM_MIN_WORDS:
        toks = [w.lower() for w in words]
        n = TRANSCRIPT_NGRAM_SIZE
        grams = [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]
        if grams:
            top_gram, top_n = Counter(grams).most_common(1)[0]
            if top_n / len(grams) > TRANSCRIPT_NGRAM_FRACTION:
                return False, f"repetition_loop:{top_n}/{len(grams)}_ngram"
            unique_ratio = len(set(grams)) / len(grams)
            if unique_ratio < TRANSCRIPT_NGRAM_MIN_UNIQUE_RATIO:
                return False, f"repetition_loop:{unique_ratio:.3f}_uniq_ngram"

    # (c) Low coverage — implied words/min far below a plausible meeting
    # density means roughly half (or more) of the meeting never made it in.
    if duration_seconds and duration_seconds > TRANSCRIPT_COVERAGE_MIN_DURATION:
        implied_wpm = n_words / (duration_seconds / 60.0)
        if implied_wpm < TRANSCRIPT_COVERAGE_MIN_WPM:
            return False, (
                f"low_coverage:{implied_wpm:.0f}wpm_over_{duration_seconds:.0f}s"
            )

    return True, ""


def _normalize_failed_clips(failed) -> dict:
    """Fold the failed-clips ledger into the keyed-dict form.

    The ledger used to be a LIST with one row appended per attempt, never
    deduped and never removed on success — so the retry budgets (which count
    rows) burned lifetime attempts on stale failures. The canonical form is a
    dict keyed by ``str(clip_id)`` with ``{first_ts, last_ts, attempts,
    reason}``. A legacy list is folded once (on load); a dict passes through.
    """
    if isinstance(failed, dict):
        return failed
    out: Dict[str, dict] = {}
    for entry in failed or []:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("clip_id")
        if cid is None:
            continue
        key = str(cid)
        ts = entry.get("timestamp")
        reason = entry.get("reason")
        rec = out.get(key)
        if rec is None:
            out[key] = {
                "first_ts": ts,
                "last_ts": ts,
                "attempts": 1,
                "reason": reason,
            }
        else:
            rec["attempts"] += 1
            # ISO-8601 sorts lexically = chronologically; be robust to
            # out-of-order rows rather than trusting append order.
            if ts:
                if not rec.get("first_ts") or ts < rec["first_ts"]:
                    rec["first_ts"] = ts
                if not rec.get("last_ts") or ts > rec["last_ts"]:
                    rec["last_ts"] = ts
            if reason:
                rec["reason"] = reason
    return out


class LFUCGPipeline:
    def __init__(
            self,
            output_dir: str = "./lfucg_output",
            view_id: Optional[str] = None,
            openai_api_key: Optional[str] = None,
            transcribe_model: str = "whisper-1",
            summary_model: str = "gpt-4o",
            keep_audio: bool = True,
            verbose: bool = True,
            force_reprocess: bool = False,
            transcribe_timeout: int = 600,
            transcriber: str = "whisper"
    ):
        # Active jurisdiction config (Granicus host/views, clip range,
        # body taxonomy). Defaults to LFUCG; see config.py / jurisdictions/.
        self.cfg = get_config()

        self.output_dir = Path(output_dir)
        self.view_id = str(view_id) if view_id is not None else str(self.cfg.default_view_id)
        self.keep_audio = keep_audio
        self.verbose = verbose
        self._force_reprocess = force_reprocess
        self.transcribe_timeout = transcribe_timeout

        # Models
        self.transcribe_model = transcribe_model
        self.summary_model = summary_model

        # Transcriber backend: "whisper" (OpenAI, default) or "elevenlabs"
        # (Scribe). Env TRANSCRIBER lets the wave runner / Lambda flip it
        # without threading the flag through every call site.
        self.transcriber = (transcriber or os.getenv("TRANSCRIBER") or "whisper").lower()
        if self.transcriber == "elevenlabs" and not os.getenv("ELEVENLABS_API_KEY"):
            raise ValueError(
                "transcriber='elevenlabs' requires ELEVENLABS_API_KEY in the environment"
            )

        # First clip ID for auto-processing (per-jurisdiction; env
        # FIRST_CLIP_ID still wins via config resolution)
        self.first_clip_id = self.cfg.first_clip_id

        # Granicus host (per-jurisdiction; env GRANICUS_HOST still wins)
        self.granicus_host = self.cfg.granicus_host

        # Listing views to scan for a clip's authoritative date (per-jurisdiction)
        self.LISTING_VIEW_FALLBACKS = tuple(self.cfg.listing_view_fallbacks)

        # Set up OpenAI client with timeout for large file uploads
        api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY environment variable "
                "or pass openai_api_key parameter"
            )
        # Configure timeout: 60s connect, transcribe_timeout for read (upload can be slow for large files)
        self.client = OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(60.0, read=transcribe_timeout, write=transcribe_timeout)
        )

        # Create output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "clips").mkdir(exist_ok=True)

        # State file
        self.state_file = self.output_dir / "state.json"
        self.load_state()

        # Swappable video source (Granicus today; YouTube etc. later). All
        # portal-specific logic — URL builders, listing scrapes, yt-dlp
        # downloads, agenda/minutes fetches, the VTT fetch — lives behind
        # this. Keyed on cfg.source_type (default "granicus"). See sources/.
        self.source = make_source(self.cfg, self.log)
        # Keep the source's view, force flag, and progress printer in sync
        # with the pipeline's so produced URLs/filenames/output are
        # byte-identical to the pre-refactor behavior.
        self.source.view_id = self.view_id
        self.source.force_reprocess = self._force_reprocess
        self.source.progress = self.progress
        # Scribe backfill: download the smallest audio-bearing rendition
        # instead of full HD video (resolution is discarded downstream anyway).
        if self.transcriber == "elevenlabs" and hasattr(self.source, "prefer_small_audio_format"):
            self.source.prefer_small_audio_format = True

        # Optional, SEPARATE agenda-document portal (WS4). None for LFUCG (no
        # [agenda] config → Granicus supplies agendas in-band, untouched).
        # Only built for jurisdictions whose video source has no agendas
        # (e.g. YouTube counties on CivicClerk/CivicPlus). When None, the
        # agenda/minutes fallback in process_clip / backfill_docs NEVER runs,
        # so the Granicus path is byte-identical.
        #
        # PR-6: a document-driven primary source (CivicClerkSource) ALREADY
        # owns agenda+minutes via download_agenda/download_minutes, so the
        # parallel WS4 fallback would be redundant (and double-fetch). Skip it
        # entirely for the document-driven path — the primary source is the
        # single document fetcher.
        if self._is_document_driven():
            self.agenda_source = None
        else:
            self.agenda_source = make_agenda_source(self.cfg, self.log)
        if self.agenda_source is not None:
            self.agenda_source.force_reprocess = self._force_reprocess
            self.agenda_source.progress = self.progress

    @property
    def force_reprocess(self) -> bool:
        return self._force_reprocess

    @force_reprocess.setter
    def force_reprocess(self, value: bool) -> None:
        # Write through to the source so its cached-file checks honor the
        # same flag (e.g. --update-transcripts toggles this temporarily).
        self._force_reprocess = value
        source = getattr(self, "source", None)
        if source is not None:
            source.force_reprocess = value
        agenda_source = getattr(self, "agenda_source", None)
        if agenda_source is not None:
            agenda_source.force_reprocess = value

    def log(self, msg: str, level: str = "INFO"):
        """Log message with timestamp"""
        if self.verbose:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] {level}: {msg}", flush=True)

    def progress(self, msg: str):
        """Print progress without timestamp (for inline updates)"""
        if self.verbose:
            print(f"  → {msg}", flush=True)

    def load_state(self):
        """Load pipeline state from file (falls back to .bak when corrupt)"""
        bak_file = self.state_file.with_name(self.state_file.name + ".bak")
        self.state = None
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    self.state = json.load(f)
            except json.JSONDecodeError as e:
                # A crash mid-write (pre-atomic-write era) leaves a torn
                # state.json that would otherwise crash every run forever.
                self.log(f"state.json is corrupt ({e}) — falling back to {bak_file.name}", "ERROR")
                if bak_file.exists():
                    with open(bak_file) as f:
                        self.state = json.load(f)
                else:
                    self.log("No state.json.bak found — starting from empty state", "ERROR")
        if self.state is None:
            self.state = {
                "last_processed_clip_id": 0,
                "processed_clips": [],
                "failed_clips": {},
            }
        # Migrate the legacy list-form failed_clips ledger to the keyed dict
        # (one-time fold on load); ensure the other keys exist.
        self.state.setdefault("last_processed_clip_id", 0)
        self.state.setdefault("processed_clips", [])
        self.state["failed_clips"] = _normalize_failed_clips(
            self.state.get("failed_clips"))

    def save_state(self):
        """Save pipeline state to file (atomic, keeping a .bak of the last good state)"""
        # Keep a copy of the last good state so a corrupt state.json is
        # recoverable (see load_state), then write via tmp + os.replace so
        # a crash mid-write never leaves a torn file (same pattern as
        # scripts/build_search_db.py).
        if self.state_file.exists():
            bak_file = self.state_file.with_name(self.state_file.name + ".bak")
            try:
                with open(self.state_file) as f:
                    json.load(f)  # only back up a parseable state
                shutil.copyfile(self.state_file, bak_file)
            except json.JSONDecodeError:
                pass  # never clobber a good .bak with a corrupt state.json
            except OSError as e:
                self.log(f"Could not write {bak_file.name}: {e}", "WARNING")
        tmp_file = self.state_file.with_name(self.state_file.name + ".tmp")
        with open(tmp_file, 'w') as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp_file, self.state_file)

    def sanitize_filename(self, title: str) -> str:
        """Sanitize title for use as filename"""
        # Strip trailing number in parentheses like "(1)" or "( 2 )" - these are Granicus duplicates
        sanitized = re.sub(r'\s*\(\s*\d+\s*\)\s*$', '', title)
        # Replace spaces and common separators with underscores
        sanitized = re.sub(r'[\s\-]+', '_', sanitized)
        # Remove any characters that aren't alphanumeric, underscore, or period
        sanitized = re.sub(r'[^\w.]', '', sanitized)
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        # Limit length
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized or "clip"

    def compress_audio(self, input_path: Path, output_path: Path) -> bool:
        """Compress audio to reduce file size while maintaining quality"""
        self.progress("Compressing audio to meet API size limits...")

        try:
            cmd = [
                "ffmpeg",
                "-i", str(input_path),
                "-vn",  # No video
                "-ar", "16000",  # 16kHz sample rate (Whisper optimized)
                "-ac", "1",  # Mono
                "-b:a", "32k",  # 32kbps bitrate
                "-y",  # Overwrite
                str(output_path)
            ]

            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True
            )

            if output_path.exists():
                old_size = input_path.stat().st_size / (1024 * 1024)
                new_size = output_path.stat().st_size / (1024 * 1024)
                self.progress(f"Compressed {old_size:.2f} MB → {new_size:.2f} MB")
                return True

            return False

        except subprocess.CalledProcessError as e:
            self.log(f"Compression error: {e.stderr}", "ERROR")
            return False
        except Exception as e:
            self.log(f"Compression error: {e}", "ERROR")
            return False

    # Seconds of audio repeated at the start of every chunk after the first,
    # so a word straddling an exact chunk boundary isn't bisected (Whisper
    # would garble or drop it in both halves).
    CHUNK_OVERLAP_SECONDS = 2.0

    # ffprobe-failure fallback: estimate audio duration from file size. The
    # pipeline downloads 48 kbps mono mp3 (~6 KB/s), so 1 MB ≈ 167 s of audio.
    # The old `file_size_mb * 60` (~1 MB/min ≈ 133 kbps) under-estimated
    # duration ~2.8x, which collapsed chunk offsets onto each other.
    AUDIO_SECONDS_PER_MB = 167

    def get_audio_duration(self, audio_path: Path) -> Optional[float]:
        """Get audio duration in seconds via ffprobe. Returns None on failure."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, text=True, check=True
            )
            return float(result.stdout.strip())
        except Exception as e:
            self.log(f"Could not get audio duration: {e}", "WARNING")
            return None

    def split_audio_into_chunks(self, audio_path: Path, num_chunks: int = 3) -> List[Path]:
        """Split audio file into a fixed number of chunks (with overlapping starts)."""
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)

        duration = self.get_audio_duration(audio_path)
        if duration is None:
            # Estimate duration from file size at the download bitrate (48kbps
            # mono ≈ 167 s/MB), NOT the old ~1MB/min guess.
            duration = file_size_mb * self.AUDIO_SECONDS_PER_MB

        chunk_duration = duration / num_chunks
        self.progress(f"Splitting {duration:.0f}s audio into {num_chunks} chunks of ~{chunk_duration:.0f}s each")

        chunk_paths = []
        for i in range(num_chunks):
            # Every chunk after the first starts CHUNK_OVERLAP_SECONDS early
            # but still ends at its nominal boundary, so boundary words appear
            # whole in at least one chunk.
            overlap = self.CHUNK_OVERLAP_SECONDS if i > 0 else 0.0
            start_time = i * chunk_duration - overlap
            chunk_path = audio_path.parent / f"{audio_path.stem}_chunk{i:02d}.mp3"

            cmd = [
                "ffmpeg", "-y",
                "-i", str(audio_path),
                "-ss", str(start_time),
                "-t", str(chunk_duration + overlap),
                "-vn", "-c:a", "copy",
                str(chunk_path)
            ]

            try:
                subprocess.run(cmd, check=True, capture_output=True)
                if chunk_path.exists() and chunk_path.stat().st_size > 0:
                    chunk_paths.append(chunk_path)
                    self.progress(f"Created chunk {i+1}/{num_chunks}: {chunk_path.stat().st_size / (1024*1024):.2f} MB")
            except subprocess.CalledProcessError as e:
                self.log(f"Failed to create chunk {i}: {e}", "WARNING")

        return chunk_paths

    def _transcribe_with_scribe(
        self, audio_path: Path, transcript_path: Path, segments_path: Path
    ) -> Optional[Dict[str, Any]]:
        """Transcribe one clip via ElevenLabs Scribe (see elevenlabs_transcribe).

        Returns the same ``{"text", "segments"}`` dict as the Whisper path and
        writes both transcript artifacts. ``self.scribe_seconds`` accumulates
        billed audio-duration across the run so the wave runner can track
        credit burn. Returns None on empty/failed transcription so the
        caller's skip/retry machinery behaves exactly as for Whisper.
        """
        from elevenlabs_transcribe import transcribe_with_scribe, ScribeCreditsExhausted

        size_mb = audio_path.stat().st_size / (1024 * 1024)
        self.log(f"Transcribing audio with ElevenLabs Scribe ({size_mb:.1f} MB)")
        try:
            result = transcribe_with_scribe(
                audio_path, timeout_seconds=max(self.transcribe_timeout, 1200)
            )
        except ScribeCreditsExhausted as e:
            # Prepaid pool drained. Flag it so a batch runner can stop the
            # Scribe phase and switch transcribers instead of retrying.
            self.scribe_credits_exhausted = True
            self.log(f"ElevenLabs credits exhausted: {e}", "ERROR")
            return None
        except Exception as e:  # requests.HTTPError, timeouts, etc.
            self.log(f"Scribe transcription error: {type(e).__name__}: {e}", "ERROR")
            return None

        if not result or not result.get("text"):
            self.log("Scribe returned empty/too-short transcript", "ERROR")
            return None

        text = result["text"]
        segments = result.get("segments")
        secs = result.get("audio_duration_secs")
        if secs is not None:
            # Tally billed seconds for cost accounting (attr defaulted lazily
            # so older call sites that never set it still work).
            self.scribe_seconds = getattr(self, "scribe_seconds", 0.0) + float(secs)

        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(text)
        word_count = len(text.split())
        mins = (secs or 0) / 60.0
        self.progress(
            f"Scribe transcript: {len(text)} chars, ~{word_count} words, "
            f"{mins:.1f} min audio"
        )
        if segments:
            with open(segments_path, "w", encoding="utf-8") as f:
                json.dump(segments, f, indent=2)
            self.progress(f"Saved {len(segments)} segments to {segments_path.name}")

        return {"text": text, "segments": segments}

    def transcribe_audio(self, audio_path: Path, transcript_path: Path) -> Optional[Dict[str, Any]]:
        """Transcribe audio using OpenAI Whisper API with timestamps.

        Returns dict with 'text' (full transcript) and 'segments' (timestamped segments).
        For backward compatibility, the plain text is also saved to transcript_path.
        Segments are saved to a separate JSON file.
        """
        segments_path = transcript_path.parent / f"{transcript_path.stem}_segments.json"

        # Check if transcript already exists
        if transcript_path.exists() and transcript_path.stat().st_size > 50 and not self.force_reprocess:
            self.progress("Transcript already exists - loading from file")
            with open(transcript_path, 'r', encoding='utf-8') as f:
                text = f.read().strip()
                if text:
                    word_count = len(text.split())
                    self.progress(f"Loaded transcript: {len(text)} chars, ~{word_count} words")
                    # Load segments if available
                    segments = None
                    if segments_path.exists():
                        try:
                            with open(segments_path, 'r', encoding='utf-8') as sf:
                                segments = json.load(sf)
                            self.progress(f"Loaded {len(segments)} transcript segments")
                        except Exception:
                            pass
                    return {"text": text, "segments": segments}

        # ElevenLabs Scribe path: single upload (5 GB limit, no chunking),
        # word timestamps coalesced into Whisper-shaped segments. Writes the
        # same transcript_path + _segments.json artifacts so process_clip and
        # all downstream steps stay identical.
        if self.transcriber == "elevenlabs":
            return self._transcribe_with_scribe(audio_path, transcript_path, segments_path)

        self.log(f"Transcribing audio with {self.transcribe_model}")

        try:
            file_size_mb = audio_path.stat().st_size / (1024 * 1024)

            # Whisper API has 25MB limit
            MAX_SIZE_MB = 24  # Leave some headroom

            transcribe_file = audio_path

            # Determine if we need to split — calculate chunks so each is under MAX_SIZE_MB
            import math
            if file_size_mb > MAX_SIZE_MB:
                num_chunks = math.ceil(file_size_mb / MAX_SIZE_MB)
                self.progress(f"Audio is {file_size_mb:.2f} MB (>{MAX_SIZE_MB} MB) - splitting into {num_chunks} chunks")
            else:
                num_chunks = 0

            if num_chunks > 0:
                chunk_paths = self.split_audio_into_chunks(transcribe_file, num_chunks=num_chunks)

                # Every chunk must exist — a missing chunk means a missing
                # third of the meeting, and a partial transcript would be
                # cached as if it were complete.
                if len(chunk_paths) != num_chunks:
                    self.log(
                        f"Failed to split audio into chunks "
                        f"({len(chunk_paths)}/{num_chunks} created)", "ERROR")
                    for f in chunk_paths:
                        if f.exists():
                            f.unlink()
                    return None

                # Nominal chunk length — fallback for per-chunk ffprobe
                # failures below (chunk_duration=0 would collapse every
                # subsequent segment offset onto the same spot).
                total_duration = self.get_audio_duration(transcribe_file)
                if total_duration is None:
                    total_duration = file_size_mb * self.AUDIO_SECONDS_PER_MB  # same estimate as the splitter
                nominal_chunk_duration = total_duration / num_chunks

                # Compress any chunks that exceed the Whisper API size limit
                for i, chunk_path in enumerate(chunk_paths):
                    chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
                    if chunk_size_mb > MAX_SIZE_MB:
                        self.progress(f"Chunk {i+1} is {chunk_size_mb:.2f} MB - compressing...")
                        compressed_chunk = chunk_path.parent / f"{chunk_path.stem}_compressed.mp3"
                        if self.compress_audio(chunk_path, compressed_chunk):
                            chunk_path.unlink()
                            chunk_paths[i] = compressed_chunk
                        else:
                            self.log(f"Failed to compress chunk {i+1}", "WARNING")

                # Transcribe each chunk with timestamps
                transcripts = []
                all_segments = []
                chunk_offset = 0.0  # Track time offset for each chunk
                chunk_failed = False
                # End (in global adjusted time) of the previous chunk's last
                # segment. Chunk i>0 is downloaded CHUNK_OVERLAP_SECONDS early,
                # so its first ~2s of segments re-transcribe audio the prior
                # chunk already covered. Dropping segments whose adjusted start
                # precedes this boundary removes the duplicated overlap window
                # from BOTH the segment list and the rebuilt text (the old
                # " ".join(chunk_texts) double-counted it).
                prev_chunk_last_end = 0.0

                for i, chunk_path in enumerate(chunk_paths):
                    self.progress(f"Transcribing chunk {i+1}/{len(chunk_paths)}...")

                    # Get chunk duration for offset calculation; fall back to
                    # the nominal length so one ffprobe hiccup doesn't zero
                    # out every subsequent offset.
                    chunk_duration = self.get_audio_duration(chunk_path)
                    if chunk_duration is None:
                        chunk_duration = nominal_chunk_duration + (
                            self.CHUNK_OVERLAP_SECONDS if i > 0 else 0.0)

                    try:
                        with open(chunk_path, "rb") as audio_file:
                            chunk_result = self.client.audio.transcriptions.create(
                                model=self.transcribe_model,
                                file=audio_file,
                                response_format="verbose_json",
                                timestamp_granularities=["segment"]
                            )

                        if chunk_result and chunk_result.text:
                            transcripts.append(chunk_result.text.strip())
                            self.progress(f"Chunk {i+1} transcribed: {len(transcripts[-1])} chars")

                            # Add segments with adjusted timestamps, dropping the
                            # overlap window from every chunk after the first.
                            boundary = prev_chunk_last_end
                            this_chunk_max_end = boundary
                            if hasattr(chunk_result, 'segments') and chunk_result.segments:
                                for seg in chunk_result.segments:
                                    # Handle both dict and object access patterns
                                    if isinstance(seg, dict):
                                        adj_start = seg.get("start", 0) + chunk_offset
                                        adj_end = seg.get("end", 0) + chunk_offset
                                        seg_text = seg.get("text", "").strip()
                                    else:
                                        adj_start = getattr(seg, "start", 0) + chunk_offset
                                        adj_end = getattr(seg, "end", 0) + chunk_offset
                                        seg_text = getattr(seg, "text", "").strip()
                                    # Skip segments that fall inside the region
                                    # the previous chunk already transcribed.
                                    if i > 0 and adj_start < boundary:
                                        continue
                                    all_segments.append({
                                        "start": adj_start,
                                        "end": adj_end,
                                        "text": seg_text,
                                    })
                                    if adj_end > this_chunk_max_end:
                                        this_chunk_max_end = adj_end
                            prev_chunk_last_end = this_chunk_max_end
                        else:
                            self.log(f"Chunk {i+1} transcribed empty", "WARNING")
                            chunk_failed = True

                        # Update offset for next chunk. The next chunk starts
                        # CHUNK_OVERLAP_SECONDS before this one's end, so its
                        # segment times overlap-correct back by that much.
                        chunk_offset += chunk_duration - self.CHUNK_OVERLAP_SECONDS

                    except Exception as e:
                        self.log(f"Error transcribing chunk {i+1}: {e}", "WARNING")
                        chunk_failed = True
                    finally:
                        # Clean up chunk file
                        if chunk_path.exists():
                            chunk_path.unlink()

                    if chunk_failed:
                        break

                # Clean up any chunks left after an early break.
                for f in chunk_paths:
                    if f.exists():
                        f.unlink()

                # Any chunk failure fails the WHOLE attempt. Saving the
                # partial join used to cache it as the complete transcript
                # (the transcript_path.exists() check never refills it),
                # permanently losing a third of the meeting. Returning None
                # lets the retry machinery re-run transcription.
                if chunk_failed:
                    self.log("Chunk transcription failed - aborting so a retry can re-run it", "ERROR")
                    return None

                if not transcripts:
                    self.log("All chunks failed to transcribe", "ERROR")
                    return None

                # Combine transcripts. Prefer the overlap-deduped segments so
                # the ~2s overlap window isn't double-counted; fall back to the
                # raw chunk-text join only when no segments came back.
                if all_segments:
                    text = " ".join(s["text"] for s in all_segments if s["text"])
                else:
                    text = " ".join(transcripts)
                word_count = len(text.split())
                self.progress(f"Combined {len(chunk_paths)} chunks: {len(text)} chars, ~{word_count} words")

                # Save transcript
                with open(transcript_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                self.progress(f"Saved transcript to {transcript_path.name}")

                # Save segments
                if all_segments:
                    with open(segments_path, 'w', encoding='utf-8') as f:
                        json.dump(all_segments, f, indent=2)
                    self.progress(f"Saved {len(all_segments)} segments to {segments_path.name}")

                return {"text": text, "segments": all_segments if all_segments else None}

            # Normal single-file transcription with timestamps
            self.progress(f"Uploading {file_size_mb:.2f} MB to OpenAI (timeout: {self.transcribe_timeout}s)...")
            self.progress(f"File: {transcribe_file.name}")
            upload_start = datetime.now()

            try:
                with open(transcribe_file, "rb") as audio_file:
                    transcript_result = self.client.audio.transcriptions.create(
                        model=self.transcribe_model,
                        file=audio_file,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"]
                    )
                upload_elapsed = (datetime.now() - upload_start).total_seconds()
                self.progress(f"Upload + transcription completed in {upload_elapsed:.1f}s")

            except httpx.TimeoutException as e:
                upload_elapsed = (datetime.now() - upload_start).total_seconds()
                self.log(f"Transcription timed out after {upload_elapsed:.1f}s: {e}", "ERROR")
                self.progress("Skipping this clip due to timeout - will retry on next run")
                return None

            except httpx.HTTPStatusError as e:
                upload_elapsed = (datetime.now() - upload_start).total_seconds()
                self.log(f"OpenAI API error after {upload_elapsed:.1f}s: {e}", "ERROR")
                return None

            text = transcript_result.text.strip() if transcript_result and transcript_result.text else ""

            if text and len(text) > 50:
                word_count = len(text.split())
                self.progress(f"Transcribed {len(text)} chars, ~{word_count} words")

                # Save transcript immediately
                with open(transcript_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                self.progress(f"Saved transcript to {transcript_path.name}")

                # Extract and save segments
                segments = None
                if hasattr(transcript_result, 'segments') and transcript_result.segments:
                    segments = []
                    for seg in transcript_result.segments:
                        # Handle both dict and object access patterns
                        if isinstance(seg, dict):
                            segments.append({
                                "start": seg.get("start", 0),
                                "end": seg.get("end", 0),
                                "text": seg.get("text", "").strip()
                            })
                        else:
                            segments.append({
                                "start": getattr(seg, "start", 0),
                                "end": getattr(seg, "end", 0),
                                "text": getattr(seg, "text", "").strip()
                            })
                    with open(segments_path, 'w', encoding='utf-8') as f:
                        json.dump(segments, f, indent=2)
                    self.progress(f"Saved {len(segments)} segments to {segments_path.name}")

                return {"text": text, "segments": segments}
            else:
                self.log("Transcription too short or empty", "ERROR")
                return None

        except Exception as e:
            self.log(f"Transcription error: {type(e).__name__}: {e}", "ERROR")
            return None

    def scrape_clip_metadata(self, clip_id: int, title: Optional[str] = None, agenda_text: Optional[str] = None) -> Dict[str, Any]:
        """Extract metadata from clip title and agenda. Returns dict with date, meeting_body, title."""
        metadata = {
            "date": None,
            "meeting_body": None,
            "title": title or f"Clip {clip_id}"
        }

        # Text sources to search for date (title first, then agenda)
        text_sources = [title] if title else []
        if agenda_text:
            # Only use first 500 chars of agenda for date extraction
            text_sources.append(agenda_text[:500])

        # Common patterns: "January 8 2026 WQFB meeting" or "Work Session - January 8, 2026"
        # Try to extract date
        date_patterns = [
            # "January 8 2026" or "January 8, 2026"
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
            # ISO "2026-05-12" — newer Granicus titles use this (e.g. "Committee on 2026-05-12 1:00 PM")
            r'(\d{4})-(\d{1,2})-(\d{1,2})',
            # "1/8/2026" or "01/08/2026"
            r'(\d{1,2})/(\d{1,2})/(\d{4})',
        ]

        month_map = {
            'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }

        # Try each text source until we find a date
        for text in text_sources:
            if metadata["date"]:
                break
            for pattern in date_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    groups = match.groups()
                    if groups[0] in month_map:
                        # Named month pattern
                        month = month_map[groups[0]]
                        day = int(groups[1])
                        year = int(groups[2])
                    elif len(groups[0]) == 4:
                        # ISO year-first pattern (YYYY-MM-DD)
                        year = int(groups[0])
                        month = int(groups[1])
                        day = int(groups[2])
                    else:
                        # Numeric MM/DD/YYYY pattern
                        month = int(groups[0])
                        day = int(groups[1])
                        year = int(groups[2])

                    try:
                        from datetime import date
                        d = date(year, month, day)
                        metadata["date"] = d.isoformat()
                        break
                    except ValueError:
                        pass

        # Final fallback: look up the clip in the source's listing for the
        # authoritative date (Granicus ViewPublisher timestamp). The
        # body-taxonomy parse below stays here — it's config-driven and
        # portal-agnostic.
        if not metadata["date"]:
            listing_date = self.source.get_metadata(MeetingRef(clip_id=str(clip_id))).get("date")
            if listing_date:
                metadata["date"] = listing_date

        # Extract meeting body from the title via the jurisdiction taxonomy
        # (shared with seo.py's llms.txt body counts — see config.classify_meeting_body).
        metadata["meeting_body"] = classify_meeting_body(title, self.cfg)

        return metadata

    def generate_summary(
            self,
            clip_id: int,
            transcript: str,
            agenda_text: Optional[str],
            summary_txt_path: Path,
            minutes_text: Optional[str] = None
    ) -> Optional[str]:
        """Generate comprehensive meeting summary using transcript, agenda, and minutes context."""

        # Check if summary already exists
        if summary_txt_path.exists() and summary_txt_path.stat().st_size > 100 and not self.force_reprocess:
            self.progress("Summary already exists - loading from file")
            with open(summary_txt_path, 'r', encoding='utf-8') as f:
                summary = f.read().strip()
                if summary:
                    self.progress(f"Loaded summary: {len(summary)} chars")
                    return summary

        self.log(f"Generating summary with {self.summary_model}")
        self.progress(f"Sending {len(transcript.split())} words to OpenAI...")

        # Build context with all available sources
        context_parts = []

        if agenda_text:
            # Truncate agendas too — LFUCG agenda packets can run hundreds of
            # pages and an unbounded paste blows the model's context window
            # (API 400 → summary fails).
            truncated_agenda = agenda_text[:30000] if len(agenda_text) > 30000 else agenda_text
            context_parts.append(f"MEETING AGENDA:\n{truncated_agenda}")

        if minutes_text:
            # Truncate minutes if very long (they can be quite detailed)
            truncated_minutes = minutes_text[:20000] if len(minutes_text) > 20000 else minutes_text
            context_parts.append(f"OFFICIAL MEETING MINUTES:\n{truncated_minutes}")

        context_parts.append(f"MEETING TRANSCRIPT:\n{transcript}")

        context = "\n\n---\n\n".join(context_parts)

        # Enhanced prompt for more detailed summaries
        prompt = """You are an expert government meeting analyst. Create a detailed, comprehensive summary of this government meeting that would be useful for citizens, journalists, and researchers.

Structure your summary with these sections:

## Meeting Overview
- Date, time, and type of meeting (if available)
- Presiding officer and notable attendees
- 3-4 sentence high-level summary of what was accomplished

## Roll Call & Attendance
List who was present, absent, or arrived late (if mentioned).

## Key Decisions & Votes
For EACH vote or decision:
- What was being voted on (resolution number, ordinance, motion)
- The outcome (passed/failed, vote count if available)
- Who voted for/against (if roll call vote)
- Brief context on why this matters

## Detailed Agenda Item Discussion
For EACH major agenda item discussed:
- Item number/name
- What was presented or discussed
- Key points raised by council members or staff
- Any concerns, objections, or amendments proposed
- Outcome or next steps

## Budget & Financial Items
Summarize any budget amendments, appropriations, contracts awarded, or financial decisions with specific dollar amounts when mentioned.

## Public Comments & Citizen Input
- How many people spoke
- Summary of topics addressed
- Notable concerns or requests from citizens
- Any responses from council members

## Presentations & Reports
Summarize any formal presentations, staff reports, or updates given.

## Appointments & Recognitions
List any board appointments, proclamations, or recognitions.

## Controversies & Disagreements
Note any contentious issues, split votes, heated discussions, or areas where council members disagreed.

## Action Items & Follow-ups
List specific next steps, items deferred, or tasks assigned to staff.

---

Guidelines:
- Be thorough and detailed - aim for a comprehensive record
- Include specific names, dates, amounts, and resolution numbers when mentioned
- Use bullet points and sub-bullets for clarity
- Cross-reference information from the agenda, minutes, and transcript
- If official minutes are provided, use them to verify vote counts and outcomes
- Maintain objectivity - report what was said without editorializing
- If a section has no relevant content, write "None discussed" rather than omitting it"""

        try:
            response = self.client.chat.completions.create(
                model=self.summary_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert government meeting analyst creating comprehensive, detailed summaries for public records, journalists, and researchers. Be thorough, accurate, objective, and include specific details like names, vote counts, and dollar amounts."
                    },
                    {
                        "role": "user",
                        "content": f"{prompt}\n\n{context}"
                    }
                ],
                temperature=0.3,
                max_tokens=8000  # Increased for more detailed summaries
            )

            summary = response.choices[0].message.content.strip()

            if summary and len(summary) > 100:
                self.progress(f"Generated summary: {len(summary)} chars")

                # Save summary immediately
                with open(summary_txt_path, 'w', encoding='utf-8') as f:
                    f.write(summary)
                self.progress(f"Saved summary to {summary_txt_path.name}")

                return summary
            else:
                self.log("Summary generation produced short/empty result", "ERROR")
                return None

        except Exception as e:
            self.log(f"Summary generation error: {e}", "ERROR")
            return None


    def generate_search_index(self, build_search_db: bool = True,
                              seo: bool = True) -> Optional[Path]:
        """Generate index.json with all processed clips for frontend search.

        ``build_search_db=True`` (the default) also rebuilds the FTS5
        search.db that powers /api/search. Pass ``False`` from per-clip
        loop callsites — the FTS rebuild is ~30s on the full archive
        and only the final batch state needs to be searchable.

        ``seo=True`` (the default) also runs generate_seo_artifacts, which
        re-reads every clip's transcript and rewrites clip.md for ALL clips
        (~300MB read / ~134MB write across the archive). Pass ``False`` from
        per-clip loop callsites so a 100-clip batch doesn't redo that 100x;
        the batch-end hook does one full SEO pass.
        """
        self.log("Generating search index...")

        clips_dir = self.output_dir / "clips"
        if not clips_dir.exists():
            self.log("No clips directory found", "ERROR")
            return None

        index_entries = []

        # Scan all clip directories
        for clip_dir in sorted(clips_dir.iterdir()):
            if not clip_dir.is_dir():
                continue

            metadata_path = clip_dir / "metadata.json"
            if not metadata_path.exists():
                continue

            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)

                # Read transcript for searchable text preview
                transcript_preview = ""
                if "files" in metadata and "transcript" in metadata["files"]:
                    transcript_path = clip_dir / metadata["files"]["transcript"]
                    if transcript_path.exists():
                        with open(transcript_path, 'r', encoding='utf-8') as f:
                            # Only need the first 500 chars — read a small
                            # bounded prefix instead of the whole (up to
                            # multi-MB) transcript for every clip.
                            full_text = f.read(2048)
                            transcript_preview = full_text[:500].replace('\n', ' ').strip()

                # Read agenda text for card preview
                agenda_preview = ""
                if "files" in metadata and "agenda_txt" in metadata["files"]:
                    agenda_path = clip_dir / metadata["files"]["agenda_txt"]
                    if agenda_path.exists():
                        with open(agenda_path, 'r', encoding='utf-8') as f:
                            # Bounded read — only the first 500 chars are used.
                            agenda_text = f.read(2048)
                            agenda_preview = agenda_text[:500].replace('\n', ' ').strip()

                # Normalize meeting body casing
                body = metadata.get("meeting_body")
                if body:
                    acronyms = set(self.cfg.body_acronyms)
                    body = body.upper() if body.upper() in acronyms else body.title()

                entry = {
                    "clip_id": metadata.get("clip_id"),
                    "date": metadata.get("date"),
                    "meeting_body": body,
                    "title": metadata.get("title"),
                    "transcript_words": metadata.get("transcript_words", 0),
                    "transcript_preview": transcript_preview,
                    "agenda_preview": agenda_preview,
                    "processed_at": metadata.get("processed_at"),
                    "speakers": metadata.get("speakers") or [],
                    "files": metadata.get("files", {})
                }

                index_entries.append(entry)
                pass

            except Exception as e:
                self.log(f"Error indexing {clip_dir.name}: {e}", "WARNING")
                continue

        # Sort by date descending (most recent first)
        index_entries.sort(
            key=lambda x: x.get("date") or "0000-00-00",
            reverse=True
        )

        # Write index
        index_path = self.output_dir / "index.json"
        index_data = {
            "generated_at": datetime.now().isoformat(),
            "total_clips": len(index_entries),
            "clips": index_entries
        }

        # No indent — pretty-printing the full archive roughly doubles the
        # multi-MB payload the frontend downloads.
        with open(index_path, 'w') as f:
            json.dump(index_data, f)

        self.log(f"Generated index with {len(index_entries)} clips at {index_path}")

        if seo:
            try:
                from seo import generate_seo_artifacts
                generate_seo_artifacts(index_entries, self.output_dir, log=self.log)
            except Exception as e:
                self.log(f"SEO artifact generation error: {e}", "WARNING")

        # Rebuild the SQLite FTS5 search index alongside index.json so the
        # frontend's server-backed search reflects the latest clips. The
        # builder is destructive (drops + recreates the FTS table) and
        # ~30s on the full archive — skipped on per-clip loop callsites
        # so a 100-clip batch doesn't rebuild it 100 times.
        if build_search_db:
            try:
                from scripts.build_search_db import build as build_search_db_fn
                stats = build_search_db_fn(self.output_dir, self.output_dir / "search.db", verbose=False)
                self.log(
                    f"Built search.db: {stats['clips_indexed']} clips, "
                    f"{stats['db_size_bytes'] / 1024 / 1024:.1f} MB"
                )
            except Exception as e:
                self.log(f"search.db build error: {e}", "WARNING")

        return index_path

    def update_clip_summary(self, clip_id: int) -> bool:
        """Update only minutes and summary for an already-processed clip."""
        clip_dir = self.output_dir / "clips" / str(clip_id)
        metadata_path = clip_dir / "metadata.json"

        # Check if clip was previously processed
        if not metadata_path.exists():
            self.log(f"Clip {clip_id} not found - run full processing first", "ERROR")
            return False

        # Load existing metadata
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)

        # Check for existing transcript
        transcript_file = metadata.get("files", {}).get("transcript")
        if not transcript_file:
            self.log(f"Clip {clip_id} has no transcript - run full processing first", "ERROR")
            return False

        transcript_path = clip_dir / transcript_file
        if not transcript_path.exists():
            self.log(f"Transcript file not found for clip {clip_id}", "ERROR")
            return False

        self.log(f"\n{'=' * 60}")
        self.log(f"Updating summary for clip {clip_id}")
        self.log(f"{'=' * 60}")

        start_time = datetime.now()

        try:
            # Load transcript
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript = f.read()
            self.progress(f"Loaded transcript: {len(transcript.split())} words")

            # Load existing agenda text if available
            agenda_text = None
            agenda_txt_file = metadata.get("files", {}).get("agenda_txt")
            if agenda_txt_file:
                agenda_path = clip_dir / agenda_txt_file
                if agenda_path.exists():
                    with open(agenda_path, 'r', encoding='utf-8') as f:
                        agenda_text = f.read()
                    self.progress(f"Loaded agenda: {len(agenda_text)} chars")

            # Get title and date from existing metadata for filename consistency
            title = metadata.get("title")
            meeting_date = metadata.get("date")

            # Download minutes (force re-download to get latest). AgendaSource
            # fallback (WS4) only when empty AND configured — no-op for LFUCG.
            minutes_result = self._minutes_with_fallback(
                clip_id, clip_dir, title=title, meeting_date=meeting_date,
                body=metadata.get("meeting_body"))
            if minutes_result["pdf_file"]:
                metadata["files"]["minutes_pdf"] = minutes_result["pdf_file"]
            if minutes_result["html_file"]:
                metadata["files"]["minutes_html"] = minutes_result["html_file"]
            if minutes_result["txt_file"]:
                metadata["files"]["minutes_txt"] = minutes_result["txt_file"]

            # Regenerate into a temp file, then swap in on success. Deleting
            # the old summary up front (the old bypass-the-cache trick) left
            # the clip with NO summary whenever generation failed.
            summary_txt_path = clip_dir / "summary.txt"
            tmp_summary_path = clip_dir / "summary.txt.tmp"
            if tmp_summary_path.exists():
                tmp_summary_path.unlink()

            # Generate new summary with all context
            summary = self.generate_summary(
                clip_id,
                transcript,
                agenda_text,
                tmp_summary_path,
                minutes_text=minutes_result.get("text")
            )

            if not summary:
                self.log(f"Failed to generate summary for clip {clip_id}", "ERROR")
                if tmp_summary_path.exists():
                    tmp_summary_path.unlink()
                return False

            os.replace(tmp_summary_path, summary_txt_path)

            metadata["files"]["summary_txt"] = "summary.txt"

            # Update metadata
            end_time = datetime.now()
            metadata["summary_updated_at"] = end_time.isoformat()
            # Placeholder clips (VTT-as-transcript) won't have a models
            # dict from the original processing — setdefault avoids a
            # KeyError when summarizing them.
            metadata.setdefault("models", {})["summary"] = self.summary_model

            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            self.log(f"Updated summary for clip {clip_id} in {(end_time - start_time).total_seconds():.1f}s")
            return True

        except Exception as e:
            self.log(f"Error updating clip {clip_id}: {e}", "ERROR")
            return False

    def update_range_summaries(self, start_id: int, end_id: int) -> dict:
        """Update summaries for a range of already-processed clips only."""
        results = {"updated": [], "failed": [], "skipped": []}

        # Find all processed clips in range (only existing folders)
        clips_dir = self.output_dir / "clips"
        clip_ids = []
        for clip_dir in clips_dir.iterdir():
            if clip_dir.is_dir():
                try:
                    cid = int(clip_dir.name)
                    if start_id <= cid <= end_id:
                        clip_ids.append(cid)
                except ValueError:
                    continue

        clip_ids.sort()

        if not clip_ids:
            self.log(f"No previously-processed clips found in range {start_id}-{end_id}")
            return results

        self.log(f"Found {len(clip_ids)} existing clips to update in range {start_id}-{end_id}")
        self.log(f"Clips: {clip_ids}")

        for idx, clip_id in enumerate(clip_ids, 1):
            self.log(f"\n[{idx}/{len(clip_ids)}] Clip {clip_id}")

            success = self.update_clip_summary(clip_id)
            if success:
                results["updated"].append(clip_id)
            else:
                results["failed"].append(clip_id)

        # Regenerate search index
        self.generate_search_index()

        return results

    def backfill_documents(self, max_clips: int = 0, regenerate_summary: bool = False) -> dict:
        """Check processed clips (highest first) for missing minutes/agenda and download them.

        Args:
            max_clips: Maximum clips to check (0 = all)
            regenerate_summary: If True, regenerate summary when new docs are found

        Returns:
            Dict with 'updated', 'skipped', 'failed' lists
        """
        results = {"updated": [], "skipped": [], "failed": []}

        # Find all processed clips with metadata, sorted highest first
        clips_dir = self.output_dir / "clips"
        clip_ids = []
        for name in sorted(os.listdir(clips_dir), key=lambda x: int(x) if x.isdigit() else 0, reverse=True):
            meta_path = clips_dir / name / "metadata.json"
            if meta_path.exists():
                try:
                    clip_ids.append(int(name))
                except ValueError:
                    continue

        if max_clips > 0:
            clip_ids = clip_ids[:max_clips]

        if not clip_ids:
            self.log("No processed clips found")
            return results

        self.log(f"Checking {len(clip_ids)} clips for missing documents (highest first)")

        for idx, clip_id in enumerate(clip_ids, 1):
            clip_dir = clips_dir / str(clip_id)
            metadata_path = clip_dir / "metadata.json"

            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self.log(f"Clip {clip_id}: bad metadata - {e}", "WARNING")
                results["failed"].append(clip_id)
                continue

            title = metadata.get("title")
            meeting_date = metadata.get("date")
            files = metadata.get("files", {})
            found_new = False

            # Check for missing agenda
            has_agenda = files.get("agenda_txt") or files.get("agenda_pdf")
            if not has_agenda:
                self.progress(f"[{idx}/{len(clip_ids)}] Clip {clip_id}: checking for agenda...")
                # Force download by ensuring no existing files match. Falls
                # back to the AgendaSource (WS4) only when empty AND configured
                # — never for LFUCG/Granicus (agenda_source is None).
                agenda_result = self._agenda_with_fallback(
                    clip_id, clip_dir, title=title, meeting_date=meeting_date,
                    body=metadata.get("meeting_body"))
                if agenda_result.get("pdf_file") or agenda_result.get("txt_file"):
                    if agenda_result["pdf_file"]:
                        metadata.setdefault("files", {})["agenda_pdf"] = agenda_result["pdf_file"]
                    if agenda_result["txt_file"]:
                        metadata.setdefault("files", {})["agenda_txt"] = agenda_result["txt_file"]
                    found_new = True
                    self.log(f"Clip {clip_id}: downloaded agenda")

            # Check for missing minutes
            has_minutes = files.get("minutes_txt") or files.get("minutes_pdf") or files.get("minutes_html")
            if not has_minutes:
                self.progress(f"[{idx}/{len(clip_ids)}] Clip {clip_id}: checking for minutes...")
                minutes_result = self._minutes_with_fallback(
                    clip_id, clip_dir, title=title, meeting_date=meeting_date,
                    body=metadata.get("meeting_body"))
                if minutes_result.get("pdf_file") or minutes_result.get("html_file") or minutes_result.get("txt_file"):
                    if minutes_result["pdf_file"]:
                        metadata.setdefault("files", {})["minutes_pdf"] = minutes_result["pdf_file"]
                    if minutes_result["html_file"]:
                        metadata.setdefault("files", {})["minutes_html"] = minutes_result["html_file"]
                    if minutes_result["txt_file"]:
                        metadata.setdefault("files", {})["minutes_txt"] = minutes_result["txt_file"]
                    found_new = True
                    self.log(f"Clip {clip_id}: downloaded minutes")

            if found_new:
                # Save updated metadata
                metadata["docs_backfilled_at"] = datetime.now().isoformat()
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                results["updated"].append(clip_id)

                # Optionally regenerate summary with new context
                if regenerate_summary:
                    self.log(f"Clip {clip_id}: regenerating summary with new documents...")
                    self.update_clip_summary(clip_id)
            else:
                if not has_agenda and not has_minutes:
                    self.progress(f"[{idx}/{len(clip_ids)}] Clip {clip_id}: no docs available yet")
                else:
                    self.progress(f"[{idx}/{len(clip_ids)}] Clip {clip_id}: already has all docs")
                results["skipped"].append(clip_id)

        # Regenerate index if anything was updated, and re-embed the touched
        # clips so the NEW minutes/agenda text reaches the vector store too —
        # generate_search_index only refreshes the keyword side (search.db);
        # without the re-ingest, late-arriving minutes were invisible to
        # semantic retrieval. Same pattern as --backfill-tables-of-motions.
        if results["updated"]:
            self.generate_search_index()
            self._reingest_clips(sorted(results["updated"]))

        self.log(f"\nBackfill complete: {len(results['updated'])} updated, "
                 f"{len(results['skipped'])} skipped, {len(results['failed'])} failed")
        return results

    def reocr_scanned_documents(self, max_clips: int = 0, min_pages: int = 6) -> dict:
        """Re-extract text for scanned minutes/agenda PDFs that the old 5-page
        OCR cap truncated, then rebuild search.db + re-embed the touched clips.

        A PDF counts as scanned when pdfplumber gets no native text off its
        first page; it counts as truncated when it has ``min_pages`` or more
        pages (the old cap OCR'd only 5). Clips already re-OCR'd carry
        ``docs_reocr_at`` in metadata and are skipped, so re-runs are cheap.
        """
        import pdfplumber
        from documents import extract_pdf_text

        results = {"updated": [], "skipped": [], "failed": []}
        clips_dir = self.output_dir / "clips"
        clip_ids = sorted(
            (int(n) for n in os.listdir(clips_dir)
             if n.isdigit() and (clips_dir / n / "metadata.json").exists()),
            reverse=True)
        if max_clips > 0:
            clip_ids = clip_ids[:max_clips]
        self.log(f"Checking {len(clip_ids)} clips for truncated scanned documents")

        for idx, clip_id in enumerate(clip_ids, 1):
            clip_dir = clips_dir / str(clip_id)
            meta_path = clip_dir / "metadata.json"
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self.log(f"Clip {clip_id}: bad metadata - {e}", "WARNING")
                results["failed"].append(clip_id)
                continue
            if metadata.get("docs_reocr_at"):
                results["skipped"].append(clip_id)
                continue
            files = metadata.get("files", {})
            touched = False
            for kind in ("minutes", "agenda"):
                pdf_name = files.get(f"{kind}_pdf")
                if not pdf_name or not (clip_dir / pdf_name).exists():
                    continue
                pdf_path = clip_dir / pdf_name
                try:
                    with pdfplumber.open(pdf_path) as pdf:
                        n_pages = len(pdf.pages)
                        native = (pdf.pages[0].extract_text() or "").strip() if n_pages else ""
                except Exception as e:
                    self.log(f"Clip {clip_id}: cannot open {pdf_name} - {e}", "WARNING")
                    continue
                if len(native) >= 50 or n_pages < min_pages:
                    continue
                self.progress(f"[{idx}/{len(clip_ids)}] Clip {clip_id}: re-OCR {kind} ({n_pages} pages)...")
                text = extract_pdf_text(pdf_path, log_fn=self.log, progress_fn=self.progress)
                if not text:
                    self.log(f"Clip {clip_id}: re-OCR of {pdf_name} produced no text", "WARNING")
                    continue
                txt_name = files.get(f"{kind}_txt") or (Path(pdf_name).stem + ".txt")
                with open(clip_dir / txt_name, "w", encoding="utf-8") as f:
                    f.write(text)
                files[f"{kind}_txt"] = txt_name
                touched = True
                self.log(f"Clip {clip_id}: re-OCR'd {kind} — {n_pages} pages, {len(text)} chars")
            if touched:
                metadata["files"] = files
                metadata["docs_reocr_at"] = datetime.now().isoformat()
                with open(meta_path, "w") as f:
                    json.dump(metadata, f, indent=2)
                results["updated"].append(clip_id)
            else:
                results["skipped"].append(clip_id)

        if results["updated"]:
            self.generate_search_index()
            self._reingest_clips(sorted(results["updated"]))
        self.log(f"\nRe-OCR complete: {len(results['updated'])} updated, "
                 f"{len(results['skipped'])} skipped, {len(results['failed'])} failed")
        return results

    def _is_document_driven(self) -> bool:
        """True when the active source is a DOCUMENT-DRIVEN source — i.e. it
        has no audio / video / captions and the meeting's *content* is its
        official agenda + minutes documents (PR-6: CivicClerk / Paris).

        This is the single gate that turns on the minutes-as-content path in
        ``process_clip``. It keys on ``cfg.source_type == "civicclerk"`` so
        Granicus (LFUCG) and YouTube clips — which carry real transcripts —
        are completely unaffected and stay byte-identical.
        """
        return getattr(self.cfg, "source_type", "granicus") == "civicclerk"

    @staticmethod
    def _doc_result_empty(result: dict) -> bool:
        """True when an agenda/minutes download yielded nothing usable.

        A result is "empty" when it has no PDF/HTML file AND no extracted text
        — i.e. the GranicusSource/YouTubeSource miss-shape. Used to decide
        whether the optional AgendaSource fallback should run.
        """
        if not result:
            return True
        return not (
            result.get("pdf_file")
            or result.get("html_file")
            or result.get("txt_file")
            or result.get("text")
        )

    def _agenda_with_fallback(self, clip_id: int, clip_dir: Path,
                              title: Optional[str], meeting_date: Optional[str],
                              body: Optional[str]) -> dict:
        """Video-source agenda, falling back to the AgendaSource when empty.

        The video source (Granicus/YouTube) is asked first — Granicus returns
        a real agenda, YouTube returns the empty miss-shape. ONLY when that's
        empty AND a separate ``agenda_source`` is configured do we fetch from
        the structured-agenda portal keyed by meeting date (+ best-effort
        body). For LFUCG ``self.agenda_source is None``, so the fallback NEVER
        runs and the Granicus result passes through byte-identically.
        """
        result = self.source.download_agenda(
            clip_id, clip_dir, title=title, date=meeting_date)
        if (self.agenda_source is not None and meeting_date
                and self._doc_result_empty(result)):
            try:
                fallback = self.agenda_source.fetch_for_date(
                    meeting_date, body, clip_dir, title=title)
                if not self._doc_result_empty(fallback):
                    return fallback
            except Exception as e:
                self.log(f"Agenda-source fallback failed for clip {clip_id}: {e}", "WARNING")
        return result

    def _minutes_with_fallback(self, clip_id: int, clip_dir: Path,
                               title: Optional[str], meeting_date: Optional[str],
                               body: Optional[str]) -> dict:
        """Video-source minutes, falling back to the AgendaSource when empty.

        Same guard as ``_agenda_with_fallback``: the fallback runs only when
        the video source returned nothing AND an ``agenda_source`` is
        configured. None for LFUCG → Granicus minutes pass through unchanged.
        """
        result = self.source.download_minutes(
            clip_id, clip_dir, title=title, date=meeting_date)
        if (self.agenda_source is not None and meeting_date
                and self._doc_result_empty(result)):
            try:
                fallback = self.agenda_source.fetch_minutes_for_date(
                    meeting_date, body, clip_dir, title=title)
                if not self._doc_result_empty(fallback):
                    return fallback
            except Exception as e:
                self.log(f"Minutes-source fallback failed for clip {clip_id}: {e}", "WARNING")
        return result

    def _apply_one_table(self, table, source_clip_id: int, clips: list,
                         force: bool = False) -> dict:
        """Resolve a single Table of Motions to its clip and merge it in.

        Returns a status dict: ``{"status": ..., "target": clip_id|None,
        ...}``. ``status`` is one of matched / no_date / no_match / ambiguous
        / skipped. The official motions *replace* the target clip's
        Whisper-derived ``motions_and_votes`` (video timestamps carried over
        where descriptions align).
        """
        from table_of_motions import resolve_target_clip, merge_table_into_facts

        res = resolve_target_clip(table, clips)
        if res.status != "matched":
            return {"status": res.status, "target": None,
                    "candidates": res.candidates, "date": table.date}

        target = res.clip_id
        clip_dir = self.output_dir / "clips" / str(target)
        facts_path = clip_dir / "extracted_facts.json"

        existing = None
        if facts_path.exists():
            try:
                with open(facts_path) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = None

        # Idempotent: skip a clip already carrying an official table unless
        # forced (e.g. after a parser fix).
        if existing and existing.get("motions_source") == "table_of_motions" \
                and not force:
            return {"status": "skipped", "target": target, "date": table.date}

        new_facts, stats = merge_table_into_facts(
            existing, table, source_clip_id=source_clip_id)

        clip_dir.mkdir(parents=True, exist_ok=True)
        with open(facts_path, "w") as f:
            json.dump(new_facts, f, indent=2)

        # Make sure the clip's metadata references the facts file so RAG
        # ingest + the frontend pick it up (matters for clips that had no
        # prior extraction).
        meta_path = clip_dir / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                files = meta.setdefault("files", {})
                if files.get("extracted_facts") != "extracted_facts.json":
                    files["extracted_facts"] = "extracted_facts.json"
                    with open(meta_path, "w") as f:
                        json.dump(meta, f, indent=2)
            except (json.JSONDecodeError, OSError):
                pass

        return {"status": "matched", "target": target, "date": table.date,
                **stats}

    def apply_tables_from_agenda(self, agenda_text: str,
                                 source_clip_id: int) -> list:
        """Apply any Tables of Motions embedded in one agenda to prior clips.

        Used by the per-clip pipeline hook. Returns the list of target clip
        ids that were updated; re-ingests each into RAG when ingestion is
        enabled for this run (the batch-end pass rebuilds search.db).
        """
        from table_of_motions import extract_tables

        tables = [t for t in extract_tables(agenda_text) if t.motions]
        if not tables:
            return []
        clips = self.load_index_clips()
        if not clips:
            return []

        touched = []
        for table in tables:
            result = self._apply_one_table(table, source_clip_id, clips)
            if result["status"] == "matched":
                touched.append(result["target"])
                self.log(
                    f"Table of Motions: applied {result.get('official_motions', 0)} "
                    f"official motions to clip {result['target']} "
                    f"({table.raw_date})")
            elif result["status"] == "ambiguous":
                self.log(
                    f"Table of Motions for {table.raw_date} ambiguous "
                    f"(candidates {result['candidates']}) — skipped", "WARNING")

        if touched and getattr(self, "rag_enabled", False):
            self._reingest_clips(touched)
        return touched

    def backfill_tables_of_motions(self, max_clips: int = 0,
                                   force: bool = False,
                                   reingest: bool = True) -> dict:
        """Scan agendas for embedded Tables of Motions and merge the official
        motions onto the prior-meeting clips they record.

        LFUCG work sessions have no official minutes — the Table of Motions
        printed in the *next* session's agenda packet is the authoritative
        record. This sweeps every agenda, extracts those tables, and replaces
        the referenced clip's transcript-derived motions with the official
        ones. Touched clips are re-ingested into RAG and the FTS5 search.db is
        rebuilt once at the end (skip via ``reingest=False``).
        """
        from table_of_motions import extract_tables

        clips = self.load_index_clips()
        if not clips:
            self.log("No index.json clips found — run --generate-index first",
                     "WARNING")
            return {}

        clips_dir = self.output_dir / "clips"
        # Newest agendas first (most useful for incremental runs).
        names = sorted(
            (n for n in os.listdir(clips_dir) if (clips_dir / n).is_dir()),
            key=lambda n: int(n) if n.isdigit() else -1,
            reverse=True,
        )
        if max_clips:
            names = names[:max_clips]

        from collections import Counter
        stats = Counter()
        touched: set[int] = set()
        ambiguous: list = []

        for name in names:
            if not name.isdigit():
                continue
            source_clip_id = int(name)
            agenda_files = list((clips_dir / name).glob("*agenda*.txt"))
            if not agenda_files:
                continue
            try:
                agenda_text = agenda_files[0].read_text(errors="replace")
            except OSError:
                continue
            tables = extract_tables(agenda_text)
            for table in tables:
                if not table.motions:
                    continue
                stats["tables_seen"] += 1
                result = self._apply_one_table(table, source_clip_id, clips,
                                                force=force)
                stats[result["status"]] += 1
                if result["status"] == "matched":
                    touched.add(result["target"])
                    stats["motions_written"] += result.get("official_motions", 0)
                    stats["timestamps_carried"] += result.get(
                        "timestamps_carried", 0)
                    self.log(
                        f"Table of Motions: clip {source_clip_id} agenda -> "
                        f"clip {result['target']} ({table.raw_date}), "
                        f"{result.get('official_motions', 0)} motions")
                elif result["status"] == "ambiguous":
                    ambiguous.append((table.raw_date, result["candidates"]))

        self.log(
            f"\nTables of Motions: {stats['tables_seen']} seen, "
            f"{stats['matched']} merged, {stats['skipped']} already done, "
            f"{stats['no_match']} no-match, {stats['ambiguous']} ambiguous, "
            f"{stats['no_date']} no-date")
        if ambiguous:
            self.log("Ambiguous (duplicate uploads, skipped) — resolve "
                     "manually if needed:", "WARNING")
            for raw_date, cands in ambiguous:
                self.log(f"  {raw_date}: candidates {cands}", "WARNING")

        if touched and reingest:
            self._reingest_clips(sorted(touched))
            self.generate_search_index()  # rebuilds search.db too

        return {"stats": dict(stats), "touched": sorted(touched),
                "ambiguous": ambiguous}

    def _reingest_clips(self, clip_ids: list) -> None:
        """Re-embed a set of clips into the RAG vector store (best-effort)."""
        try:
            from rag.ingest import (ingest_clip as rag_ingest_clip,
                                    get_vecstore, load_rag_state,
                                    save_rag_state)
            from clients import get_openai
        except ImportError:
            self.log("RAG deps not installed — skipping re-ingest "
                     "(run: uv sync --extra rag)", "WARNING")
            return
        try:
            store = get_vecstore(str(self.output_dir))
            openai_client = get_openai()
            state = load_rag_state(self.output_dir)
            # ingest_clip deletes the clip's existing chunks before storing
            # (skip_if_ingested defaults False), so this re-embeds cleanly.
            for clip_id in clip_ids:
                rag_ingest_clip(clip_id, self.output_dir, store,
                                openai_client, rag_state=state,
                                verbose=self.verbose)
            save_rag_state(state, self.output_dir)
            self.log(f"RAG: re-ingested {len(clip_ids)} clip(s)")
        except Exception as e:
            self.log(f"RAG re-ingest failed: {e}", "WARNING")

    def load_index_clips(self) -> list:
        """Load the ``clips`` list from index.json (empty list if missing)."""
        index_path = self.output_dir / "index.json"
        if not index_path.exists():
            return []
        try:
            with open(index_path) as f:
                data = json.load(f)
            return data.get("clips", []) if isinstance(data, dict) else data
        except (json.JSONDecodeError, OSError):
            return []

    def _enrich_segments_with_captions(
        self,
        clip_id: int,
        clip_dir: Path,
        whisper_segments: List[dict],
        segments_path: Path,
    ) -> Optional[Dict[str, Any]]:
        """Align VTT speaker labels onto fresh Whisper segments and rewrite the
        segments JSON with them. Best-effort: returns None (leaving the plain
        Whisper artifacts in place) on any failure."""
        try:
            enriched = self.apply_captions(clip_id, clip_dir, whisper_segments=whisper_segments)
        except Exception as e:
            self.log(f"Caption speaker enrichment failed for clip {clip_id}: {e}", "WARNING")
            return None
        if not enriched or not enriched.get("segments"):
            return None
        try:
            with open(segments_path, "w", encoding="utf-8") as f:
                json.dump(enriched["segments"], f, indent=2)
        except OSError as e:
            self.log(f"Could not write enriched segments for clip {clip_id}: {e}", "WARNING")
            return None
        self.log(
            f"Folded Granicus captions into Whisper transcript: "
            f"{len(enriched.get('speakers') or [])} speakers")
        return enriched

    def find_placeholder_clips(
        self,
        bodies: Optional[List[str]] = None,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Clips still carrying a Granicus-VTT placeholder transcript for a
        Whisper-required body (or the explicit ``bodies`` substrings), newest
        first. Each entry: ``{clip_id, title, date, meeting_body, est_minutes}``
        where est_minutes comes from the last caption cue (0 if unknown)."""
        clips_dir = self.output_dir / "clips"
        found: List[Dict[str, Any]] = []
        if not clips_dir.exists():
            return found
        needles = [b.lower() for b in (bodies or []) if b and b.strip()]
        for clip_dir in clips_dir.iterdir():
            if not clip_dir.is_dir() or not clip_dir.name.isdigit():
                continue
            meta_path = clip_dir / "metadata.json"
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            source = metadata.get("transcript_source") or ""
            if source != "granicus_vtt" and not metadata.get("whisper_pending"):
                continue
            title = metadata.get("title") or ""
            body = metadata.get("meeting_body") or ""
            if needles:
                hay = f"{title} {body}".lower()
                if not any(n in hay for n in needles):
                    continue
            elif not requires_whisper(title, body):
                continue
            date = metadata.get("date") or ""
            if since and (not date or date < since):
                continue
            est_minutes = 0.0
            segf = (metadata.get("files") or {}).get("transcript_segments")
            if segf and (clip_dir / segf).exists():
                try:
                    segs = json.loads((clip_dir / segf).read_text())
                    est_minutes = max((float(s.get("end", 0) or 0) for s in segs), default=0.0) / 60.0
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
            found.append({
                "clip_id": int(clip_dir.name), "title": title, "date": date,
                "meeting_body": body, "est_minutes": round(est_minutes, 1),
            })
        found.sort(key=lambda c: (c["date"] or "", c["clip_id"]), reverse=True)
        if limit:
            found = found[:limit]
        return found

    # OpenAI whisper-1 list price, used only for the --dry-run cost estimate.
    WHISPER_USD_PER_MINUTE = 0.006

    def retranscribe_clip(self, clip_id: int) -> bool:
        """Replace one clip's VTT placeholder transcript with Whisper (+ VTT
        speaker labels). Keeps the same transcript filenames so every
        downstream reference (index, search.db, clip.md) stays valid.

        The placeholder files are backed up first and restored if Whisper
        fails or its output fails the quality gate — a failed re-run must
        never leave the page worse than the placeholder it had.
        """
        clip_dir = self.output_dir / "clips" / str(clip_id)
        meta_path = clip_dir / "metadata.json"
        try:
            with open(meta_path) as f:
                metadata = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.log(f"Clip {clip_id}: unreadable metadata ({e})", "ERROR")
            return False
        files = metadata.get("files") or {}
        tfile = files.get("transcript")
        if not tfile:
            self.log(f"Clip {clip_id}: no transcript file recorded — skipping", "WARNING")
            return False
        transcript_path = clip_dir / tfile
        segments_path = clip_dir / f"{transcript_path.stem}_segments.json"
        title = metadata.get("title") or f"Clip {clip_id}"
        meeting_date = metadata.get("date")

        # Back up the placeholder so a failed Whisper pass can be rolled back.
        backups = []
        for src in (transcript_path, segments_path):
            if src.exists():
                bak = src.with_name(src.name + ".vtt-placeholder.bak")
                shutil.copy2(src, bak)
                backups.append((src, bak))

        def _restore() -> None:
            for src, bak in backups:
                try:
                    shutil.copy2(bak, src)
                    bak.unlink()
                except OSError:
                    pass

        def _discard_backups() -> None:
            for _src, bak in backups:
                try:
                    bak.unlink()
                except OSError:
                    pass

        audio_filename = self.source.download_audio(clip_id, clip_dir, title, date=meeting_date)
        if not audio_filename:
            _discard_backups()
            self.log(f"Clip {clip_id}: audio download failed — placeholder kept", "ERROR")
            self._record_failure(clip_id, "retranscribe:download_failed")
            self.save_state()
            return False
        audio_path = clip_dir / audio_filename

        old_force = self.force_reprocess
        self.force_reprocess = True  # ignore the cached placeholder transcript
        try:
            result = self.transcribe_audio(audio_path, transcript_path)
        finally:
            self.force_reprocess = old_force

        transcript = result.get("text") if isinstance(result, dict) else (result or "")
        segments = result.get("segments") if isinstance(result, dict) else None
        duration = self.get_audio_duration(audio_path)
        if duration is None and segments:
            duration = max((s.get("end", 0) for s in segments), default=0) or None
        ok, reason = validate_transcript(transcript, duration) if transcript else (False, "empty")
        if not ok:
            _restore()
            self.log(
                f"Clip {clip_id}: Whisper output rejected ({reason}) — placeholder restored", "ERROR")
            self._record_failure(clip_id, f"retranscribe:transcript_quality:{reason}")
            self.save_state()
            if not self.keep_audio and audio_path.exists():
                audio_path.unlink()
            return False
        _discard_backups()

        transcript_source = "elevenlabs_scribe" if self.transcriber == "elevenlabs" else "whisper-1"
        speakers: List[str] = []
        if segments:
            files["transcript_segments"] = segments_path.name
            enriched = self._enrich_segments_with_captions(clip_id, clip_dir, segments, segments_path)
            if enriched:
                speakers = enriched["speakers"]
                transcript_source = enriched["source"]
                files["captions_vtt"] = enriched["vtt_filename"]

        if self.keep_audio:
            files["audio"] = audio_filename
        elif audio_path.exists():
            audio_path.unlink()
            files.pop("audio", None)

        metadata["files"] = files
        metadata["transcript_source"] = transcript_source
        metadata["speakers"] = speakers
        metadata["transcript_words"] = len(transcript.split())
        metadata["audio_kept"] = self.keep_audio
        metadata.setdefault("models", {})["transcribe"] = (
            "elevenlabs_scribe" if self.transcriber == "elevenlabs" else self.transcribe_model)
        metadata["retranscribed_at"] = datetime.now().isoformat()
        metadata.pop("whisper_pending", None)
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
        self._clear_failure(clip_id)
        self.save_state()
        self.log(
            f"Clip {clip_id}: placeholder replaced with {transcript_source} "
            f"({metadata['transcript_words']:,} words, {len(speakers)} speakers)")
        return True

    def reset_failed_attempts(self, clip_ids: List[int]) -> List[int]:
        """Re-open the retry budget for specific failed clips.

        Resets ``attempts`` to 0 and re-stamps first_ts/last_ts to now so BOTH
        the inline --auto retry (recent last_ts, attempts < cap) and the weekly
        --retry-failed-sweep (first_ts inside the 60-day window) pick the clip
        up again. Clips that are already processed or not in the ledger are
        reported back untouched. Persists state.
        """
        failed = _normalize_failed_clips(self.state.get("failed_clips"))
        self.state["failed_clips"] = failed
        processed = set(self.state.get("processed_clips", []))
        ts = datetime.now().isoformat()
        reset: List[int] = []
        for cid in clip_ids:
            key = str(cid)
            if cid in processed:
                self.log(f"Clip {cid}: already processed — nothing to reset", "WARNING")
                continue
            rec = failed.get(key)
            if not isinstance(rec, dict):
                # Not in the ledger (e.g. dropped by a prune): add a fresh
                # zero-attempt row so the sweep can pick it up.
                failed[key] = {"first_ts": ts, "last_ts": ts, "attempts": 0,
                               "reason": "manual_retry"}
            else:
                rec["attempts"] = 0
                rec["first_ts"] = ts
                rec["last_ts"] = ts
                rec["reason"] = f"manual_retry (was: {rec.get('reason', '?')})"
            reset.append(cid)
        self.save_state()
        return reset

    def apply_captions(
        self,
        clip_id: int,
        clip_dir: Path,
        whisper_segments: Optional[List[dict]],
    ) -> Optional[Dict[str, Any]]:
        """Resolve Granicus VTT into either a standalone transcript or
        speaker-enrichment for an existing Whisper transcript.

        Pass `whisper_segments=None` (the default for new clips in
        `process_clip`) to get a Granicus-VTT-only transcript: the
        returned dict includes `transcript_text` and `segments`, and
        `source="granicus_vtt"`. Pass `whisper_segments=[...]` to align
        speaker labels onto pre-existing Whisper segments — returned
        dict has `segments` (with speakers attached) and
        `source="whisper-1+vtt-speakers"`, no `transcript_text`.

        Returns None when no captions track is available.
        """
        vtt_path = self.source.fetch_captions(clip_id, clip_dir)
        if not vtt_path:
            return None
        try:
            vtt_text = vtt_path.read_text(encoding="utf-8")
        except OSError:
            return None
        cue_segments, turns = parse_vtt(vtt_text)
        if not cue_segments:
            return None

        if whisper_segments:
            enriched = align_speakers_to_segments(whisper_segments, turns)
            return {
                "vtt_filename": vtt_path.name,
                "segments": enriched,
                "speakers": speakers_for_segments(enriched),
                "source": "whisper-1+vtt-speakers",
            }

        placeholder_segments = vtt_to_transcript_segments(cue_segments)
        return {
            "vtt_filename": vtt_path.name,
            "segments": placeholder_segments,
            "speakers": speakers_for_segments(placeholder_segments),
            "transcript_text": vtt_to_transcript_text(cue_segments),
            "source": "granicus_vtt",
        }

    def process_clip(
            self,
            clip_id: int,
            skip_if_exists: bool = True
    ) -> bool:
        """Process a single clip through the entire pipeline"""

        clip_dir = self.output_dir / "clips" / str(clip_id)
        metadata_path = clip_dir / "metadata.json"

        # Check if fully processed (metadata exists with a transcript file)
        if skip_if_exists and not self.force_reprocess and metadata_path.exists():
            try:
                with open(metadata_path) as f:
                    existing_meta = json.load(f)
                if existing_meta.get("files", {}).get("transcript"):
                    self.log(f"Clip {clip_id} fully processed - skipping (use --force to reprocess)")
                    return True
            except (json.JSONDecodeError, OSError):
                pass  # Corrupted metadata, reprocess

        clip_dir.mkdir(parents=True, exist_ok=True)
        start_time = datetime.now()

        # Track files created for metadata
        files = {}

        try:
            self.log(f"\n{'=' * 60}")
            self.log(f"Processing clip {clip_id}")
            self.log(f"{'=' * 60}")

            # Step 1: Get clip title
            title = self.source.get_clip_title(clip_id)
            if not title:
                title = f"Clip {clip_id}"

            # Step 2: Extract date from title for filename prefixes
            clip_metadata = self.scrape_clip_metadata(clip_id, title)
            meeting_date = clip_metadata.get("date")  # ISO format: YYYY-MM-DD

            # Fall back to date from existing metadata.json on disk
            if not meeting_date:
                existing_meta_path = clip_dir / "metadata.json"
                if existing_meta_path.exists():
                    try:
                        existing_meta = json.loads(existing_meta_path.read_text())
                        meeting_date = existing_meta.get("date")
                        if meeting_date:
                            clip_metadata["date"] = meeting_date
                    except (json.JSONDecodeError, OSError):
                        pass

            if meeting_date:
                self.log(f"Meeting date: {meeting_date}")
                self.progress(f"Extracted date: {meeting_date}")

            # Step 3-5: Get transcript. Prefer Granicus VTT (cheap, ~10s,
            # already speaker-attributed) when available; fall back to
            # audio + Whisper (~5min, ~$1.20/clip) only when there's no
            # captions track. Either path produces the same transcript +
            # timestamped segments shape so downstream steps (facts,
            # summary, RAG ingest) don't branch.
            caption_info = self.apply_captions(clip_id, clip_dir, whisper_segments=None)

            # Filename stem mirrors what download_audio would produce so
            # the transcript filename is stable regardless of source.
            sanitized_title = self.sanitize_filename(title) if title else f"clip_{clip_id}"
            if meeting_date:
                audio_stem = f"{meeting_date}_{sanitized_title}_audio"
            else:
                audio_stem = f"{sanitized_title}_audio"
            transcript_filename = f"transcript_{audio_stem}.txt"
            transcript_path = clip_dir / transcript_filename
            segments_filename = f"transcript_{audio_stem}_segments.json"

            transcript: Optional[str] = None
            transcript_segments: Optional[List[dict]] = None
            transcript_source = "whisper-1"
            speakers: List[str] = []
            audio_path: Optional[Path] = None
            # Document-driven path may fetch agenda/minutes up front (they ARE
            # the content). Steps 6/6b below reuse these instead of re-fetching
            # (re-fetching with --force bypasses the disk cache → double the
            # network calls). None means "not yet fetched" (the Granicus/
            # YouTube/Whisper paths leave them None → steps 6/6b fetch as before).
            agenda_doc: Optional[Dict[str, Any]] = None
            minutes_doc: Optional[Dict[str, Any]] = None

            # Quality-gate a VTT transcript BEFORE accepting it as final. A
            # truthy-but-garbage VTT (steno buffer looped, or only a few
            # minutes captured before the CC feed dropped) would otherwise be
            # written as the canonical transcript and the clip marked done
            # forever. On failure we log loudly and fall through to the Whisper
            # path (which re-derives the transcript from audio).
            vtt_ok = False
            if caption_info and caption_info.get("transcript_text"):
                vtt_segments = caption_info.get("segments") or []
                vtt_duration = max(
                    (s.get("end", 0) for s in vtt_segments), default=0) or None
                vtt_ok, vtt_reason = validate_transcript(
                    caption_info["transcript_text"], vtt_duration)
                if not vtt_ok:
                    self.log(
                        f"REJECTED Granicus VTT transcript for clip {clip_id} "
                        f"({vtt_reason}) — falling through to Whisper instead of "
                        f"accepting garbage", "ERROR")

            # Council / Work Session / Committee of the Whole / Planning
            # Commission clips must be Whispered even when a VTT validates:
            # the caption track is only a placeholder for them (see
            # DEFAULT_WHISPER_REQUIRED_BODIES). If Whisper fails this run the
            # placeholder is still written so the page goes live, and the clip
            # is stamped whisper_pending for --retranscribe-placeholders.
            whisper_required = vtt_ok and requires_whisper(
                title, clip_metadata.get("meeting_body"))
            whisper_pending = False

            def _write_vtt_placeholder(why: str) -> None:
                nonlocal transcript, transcript_segments, transcript_source, speakers, whisper_pending
                transcript = caption_info["transcript_text"]
                transcript_segments = caption_info["segments"]
                transcript_source = caption_info["source"]  # "granicus_vtt"
                speakers = caption_info["speakers"]
                files["captions_vtt"] = caption_info["vtt_filename"]
                with open(transcript_path, "w", encoding="utf-8") as f:
                    f.write(transcript)
                with open(clip_dir / segments_filename, "w", encoding="utf-8") as f:
                    json.dump(transcript_segments, f, indent=2)
                files["transcript"] = transcript_filename
                files["transcript_segments"] = segments_filename
                if why:
                    whisper_pending = True
                    self.log(
                        f"Whisper {why} for clip {clip_id} — keeping the Granicus VTT "
                        f"placeholder ({len(speakers)} speakers, "
                        f"{len(transcript_segments)} cues) and marking whisper_pending",
                        "WARNING")

            if vtt_ok and not whisper_required:
                # VTT path: Granicus stenographer captions are the
                # canonical transcript. Audio download + Whisper are
                # skipped entirely for this clip.
                _write_vtt_placeholder("")
                self.log(
                    f"Using Granicus VTT transcript ({len(speakers)} speakers, "
                    f"{len(transcript_segments)} cues) — skipping Whisper"
                )
            elif self._is_document_driven():
                # Document-driven path (PR-6: CivicClerk / Paris). There is NO
                # audio and NO caption track (YouTube blocks both from the
                # server), so the meeting's official MINUTES (votes, motions,
                # appropriations — the authoritative record) ARE the content
                # the facts/summary/RAG flow runs on. We fetch agenda+minutes
                # here, then write the minutes text (falling back to agenda
                # when minutes aren't published yet) as the clip's transcript
                # artifact so the existing downstream flow runs UNCHANGED.
                #
                # The fetched minutes_doc/agenda_doc are REUSED by steps 6/6b
                # below (they populate files["agenda_*"]/files["minutes_*"]) —
                # we don't re-fetch, which under --force would bypass the disk
                # cache and double the network calls.
                doc_body = clip_metadata.get("meeting_body")
                minutes_doc = self._minutes_with_fallback(
                    clip_id, clip_dir, title=title, meeting_date=meeting_date,
                    body=doc_body)
                agenda_doc = self._agenda_with_fallback(
                    clip_id, clip_dir, title=title, meeting_date=meeting_date,
                    body=doc_body)

                # Prefer minutes (better-structured: explicit votes/motions);
                # fall back to agenda text. Either becomes the transcript.
                if minutes_doc.get("text"):
                    transcript = minutes_doc["text"]
                    transcript_source = "civicclerk_minutes"
                    self.log(
                        f"Document-driven: using official minutes as content "
                        f"({len(transcript.split())} words)"
                    )
                elif agenda_doc.get("text"):
                    transcript = agenda_doc["text"]
                    transcript_source = "civicclerk_agenda"
                    self.log(
                        f"Document-driven: no minutes; using agenda as content "
                        f"({len(transcript.split())} words)"
                    )
                else:
                    # No documents available for this meeting → nothing to
                    # build a record from. Record the miss and move on.
                    self._record_failure(clip_id, "no_documents")
                    self._advance_cursor(clip_id)
                    self.save_state()
                    self.log(
                        f"Clip {clip_id}: no minutes or agenda from CivicClerk "
                        f"— skipping", "WARNING")
                    return False

                # Single synthetic segment so the segments-based readers
                # (RAG transcript chunker, --upgrade-summaries) see content.
                # start/end are 0 — there are no video timestamps for a
                # document-driven record (no clickable deep-links).
                transcript_segments = [{"text": transcript, "start": 0.0, "end": 0.0}]
                with open(transcript_path, "w", encoding="utf-8") as f:
                    f.write(transcript)
                with open(clip_dir / segments_filename, "w", encoding="utf-8") as f:
                    json.dump(transcript_segments, f, indent=2)
                files["transcript"] = transcript_filename
                files["transcript_segments"] = segments_filename
            else:
                # Whisper path: no usable captions track, OR a whisper-required
                # body whose captions are only a placeholder. Every failure
                # below falls back to the VTT placeholder when one validated
                # (whisper_pending=True, retried later) instead of failing the
                # clip outright.
                if whisper_required:
                    self.log(
                        f"Clip {clip_id} is a Whisper-required body — captions kept "
                        f"as placeholder only; transcribing audio")
                audio_filename = self.source.download_audio(clip_id, clip_dir, title, date=meeting_date)
                whisper_ok = bool(audio_filename)
                if not whisper_ok and not vtt_ok:
                    self._record_failure(clip_id, "download_failed")
                    self._advance_cursor(clip_id)
                    self.save_state()
                    return False
                if not whisper_ok:
                    _write_vtt_placeholder("audio download failed")
                else:
                    files["audio"] = audio_filename
                    audio_path = clip_dir / audio_filename

                    transcript_result = self.transcribe_audio(audio_path, transcript_path)
                    if isinstance(transcript_result, dict):
                        transcript = transcript_result["text"]
                        transcript_segments = transcript_result.get("segments")
                    elif isinstance(transcript_result, str):
                        transcript = transcript_result

                    if not transcript:
                        if vtt_ok:
                            _write_vtt_placeholder("transcription failed")
                            whisper_ok = False
                        else:
                            self._record_failure(clip_id, "transcription_failed")
                            self._advance_cursor(clip_id)
                            self.save_state()
                            return False

                if whisper_ok:
                    # Quality-gate the Whisper output before accepting it. A
                    # repetition loop or a half-captured transcript (dropped
                    # chunk) must NOT be written as final — that marks the
                    # clip done forever. Without a VTT there's no other
                    # transcription path here, so we record a quality failure
                    # and let the retry/repair machinery re-attempt it (the
                    # garbage file stays on disk so --repair-short-transcripts
                    # can find it).
                    whisper_duration = self.get_audio_duration(audio_path)
                    if whisper_duration is None and transcript_segments:
                        whisper_duration = max(
                            (s.get("end", 0) for s in transcript_segments), default=0
                        ) or None
                    ok, reason = validate_transcript(transcript, whisper_duration)
                    if not ok:
                        self.log(
                            f"REJECTED Whisper transcript for clip {clip_id} "
                            f"({reason})", "ERROR")
                        if vtt_ok:
                            _write_vtt_placeholder(f"rejected ({reason})")
                            whisper_ok = False
                        else:
                            self.log("  — not marking done; will retry", "ERROR")
                            self._record_failure(clip_id, f"transcript_quality:{reason}")
                            self._advance_cursor(clip_id)
                            self.save_state()
                            return False

                if whisper_ok:
                    # transcribe_audio writes both transcript_path and the
                    # adjacent _segments.json file itself.
                    files["transcript"] = transcript_filename
                    if transcript_segments:
                        files["transcript_segments"] = segments_filename

                    # Record which engine produced this transcript (default above
                    # is "whisper-1"). Scribe transcripts are flagged so the
                    # frontend disclosure + RAG metadata can distinguish them.
                    if self.transcriber == "elevenlabs":
                        transcript_source = "elevenlabs_scribe"

                    # Fold the (validated) caption track back in for speaker
                    # labels — Whisper text stays canonical, VTT adds "who".
                    if vtt_ok and transcript_segments:
                        enriched = self._enrich_segments_with_captions(
                            clip_id, clip_dir, transcript_segments,
                            clip_dir / segments_filename)
                        if enriched:
                            transcript_segments = enriched["segments"]
                            speakers = enriched["speakers"]
                            transcript_source = enriched["source"]
                            files["captions_vtt"] = enriched["vtt_filename"]

            # Step 6: Download and extract agenda (optional - don't fail if
            # unavailable). Falls back to the separate AgendaSource (WS4) only
            # when the video source has no agenda AND one is configured — never
            # for LFUCG/Granicus (agenda_source is None). The document-driven
            # path already fetched this above (agenda_doc is set) — reuse it
            # rather than re-fetching (a --force re-fetch would skip the cache).
            if agenda_doc is not None:
                agenda_result = agenda_doc
            else:
                agenda_result = self._agenda_with_fallback(
                    clip_id, clip_dir, title=title, meeting_date=meeting_date,
                    body=clip_metadata.get("meeting_body"))
            if agenda_result["pdf_file"]:
                files["agenda_pdf"] = agenda_result["pdf_file"]
            if agenda_result["txt_file"]:
                files["agenda_txt"] = agenda_result["txt_file"]

            # Step 6a-ii: This agenda packet may embed the official "Table of
            # Motions" for a *prior* meeting (work sessions have no minutes).
            # Apply it to the clip it records so that clip's motions become
            # authoritative. Best-effort — never fail the clip over it.
            if agenda_result.get("text"):
                try:
                    self.apply_tables_from_agenda(
                        agenda_result["text"], clip_id)
                except Exception as e:
                    self.log(f"Table-of-Motions hook failed: {e}", "WARNING")

            # Step 6b: Download and extract minutes (optional - don't fail if
            # unavailable). Same AgendaSource fallback as the agenda step;
            # no-op for LFUCG (agenda_source is None). Reuse the document-driven
            # path's already-fetched minutes_doc when present (see step 6).
            if minutes_doc is not None:
                minutes_result = minutes_doc
            else:
                minutes_result = self._minutes_with_fallback(
                    clip_id, clip_dir, title=title, meeting_date=meeting_date,
                    body=clip_metadata.get("meeting_body"))
            if minutes_result["pdf_file"]:
                files["minutes_pdf"] = minutes_result["pdf_file"]
            if minutes_result["html_file"]:
                files["minutes_html"] = minutes_result["html_file"]
            if minutes_result["txt_file"]:
                files["minutes_txt"] = minutes_result["txt_file"]

            # Step 7: Update metadata with agenda text if we got one
            if agenda_result.get("text") and not clip_metadata.get("date"):
                # Re-extract metadata now that we have agenda text
                clip_metadata = self.scrape_clip_metadata(clip_id, title, agenda_result.get("text"))

            # Remove audio if not keeping (only applies when we
            # downloaded one — VTT path skips audio entirely).
            if not self.keep_audio and audio_path is not None and audio_path.exists():
                audio_path.unlink()
                files.pop("audio", None)
                self.progress("Removed audio file (keep_audio=False)")

            # Step 10: Save enhanced metadata. When the clip was processed
            # before, MERGE with the existing metadata.json instead of
            # rebuilding from scratch — a reprocess must not wipe the
            # upgraded-summary artifacts (files.summary_txt /
            # files.extracted_facts, summary_updated_at, topics) that
            # --upgrade-summaries stamped in. The daily summaries cron skips
            # clips by file-existence, so it would never restore the refs.
            end_time = datetime.now()
            old_metadata = {}
            if metadata_path.exists():
                try:
                    with open(metadata_path) as f:
                        old_metadata = json.load(f)
                except (OSError, json.JSONDecodeError):
                    old_metadata = {}

            # Preserve file refs this run didn't produce, but only when the
            # target file is still on disk (new values win on collision).
            for key, fname in (old_metadata.get("files") or {}).items():
                if key not in files and isinstance(fname, str) and fname \
                        and (clip_dir / fname).exists():
                    files[key] = fname

            models = {
                # Reflect what actually produced the transcript: the VTT
                # and document-driven paths skip Whisper entirely, so
                # claiming whisper-1 would be misleading downstream.
                "transcribe": (
                    transcript_source
                    if transcript_source in (
                        "granicus_vtt", "civicclerk_minutes", "civicclerk_agenda",
                        "elevenlabs_scribe")
                    else self.transcribe_model
                ),
            }
            # Keep model attributions from earlier passes (summary, topics).
            for key, value in (old_metadata.get("models") or {}).items():
                models.setdefault(key, value)

            metadata = {
                "clip_id": clip_id,
                "url": self.source.canonical_url(clip_id),
                "date": clip_metadata.get("date"),
                "meeting_body": clip_metadata.get("meeting_body"),
                "title": title,
                "files": files,
                "processed_at": end_time.isoformat(),
                "processing_time_seconds": (end_time - start_time).total_seconds(),
                "transcript_words": len(transcript.split()),
                "audio_kept": self.keep_audio,
                "transcript_source": transcript_source,
                "speakers": speakers,
                "models": models,
            }

            # Carry over bookkeeping fields this run doesn't compute
            # (summary_updated_at, topics, motions_source-style stamps, …) —
            # keys the fresh run produced always win.
            for key, value in old_metadata.items():
                if key not in metadata:
                    metadata[key] = value

            # whisper_pending: VTT placeholder on a Whisper-required body whose
            # Whisper pass failed this run. Set only when true (and cleared
            # explicitly so a stale carry-over can't resurrect it).
            if whisper_pending:
                metadata["whisper_pending"] = True
            else:
                metadata.pop("whisper_pending", None)

            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            # Update state
            self._advance_cursor(clip_id)
            if clip_id not in self.state["processed_clips"]:
                self.state["processed_clips"].append(clip_id)
            # Success clears any prior failure ledger entry so old failures
            # don't linger and burn future retry budgets.
            self._clear_failure(clip_id)
            self.save_state()

            self.log(f"Successfully processed clip {clip_id} in {metadata['processing_time_seconds']:.1f}s")

            # RAG ingestion (if enabled)
            if getattr(self, 'rag_enabled', False):
                try:
                    from rag.ingest import ingest_clip as rag_ingest_clip, get_vecstore
                    from clients import get_openai
                    store = get_vecstore(str(self.output_dir))
                    rag_ingest_clip(clip_id, self.output_dir, store, get_openai(), verbose=self.verbose)
                    self.log(f"RAG: Ingested clip {clip_id}")
                except ImportError:
                    self.log("RAG dependencies not installed, skipping ingestion", "WARNING")
                except Exception as e:
                    self.log(f"RAG ingestion failed for clip {clip_id}: {e}", "WARNING")

            # Regenerate the metadata search index after each successful
            # clip so the frontend list shows the new clip immediately.
            # Skip the FTS5 rebuild AND the full-transcript SEO pass here —
            # both run for every clip in a batch otherwise (the SEO pass alone
            # re-reads ~300MB and rewrites clip.md for all ~4700 clips PER
            # processed clip). Batch-end callers do one final index+SEO+FTS
            # pass. See generate_search_index(seo=...) and the main() hook.
            self.generate_search_index(build_search_db=False, seo=False)

            return True

        except Exception as e:
            self.log(f"Unexpected error processing clip {clip_id}: {e}", "ERROR")
            self._record_failure(clip_id, f"unexpected_error: {str(e)}")
            # Update last_processed_clip_id even on failure so auto mode moves forward
            self._advance_cursor(clip_id)
            self.save_state()
            return False

    def process_range(
            self,
            start_id: int,
            end_id: int,
            stop_on_failure: bool = False
    ) -> dict:
        """Process a range of clip IDs.

        ``stop_on_failure`` defaults to False (safer batch default): a single
        failed/absent clip in a range shouldn't abort the whole batch. Callers
        that want fail-fast pass ``stop_on_failure=True`` explicitly.
        """

        results = {
            "processed": [],
            "failed": [],
            "skipped": []
        }

        total = end_id - start_id + 1
        for idx, clip_id in enumerate(range(start_id, end_id + 1), 1):
            self.log(f"\n{'=' * 70}")
            self.log(f"Clip {clip_id} - [{idx}/{total}]")
            self.log(f"{'=' * 70}")

            success = self.process_clip(clip_id)

            if success:
                results["processed"].append(clip_id)
            else:
                results["failed"].append(clip_id)
                if stop_on_failure:
                    self.log(f"Stopping due to failure on clip {clip_id}")
                    break

        return results

    def load_available_clips(self) -> list[int]:
        """Load available clip IDs from available_clips.json if it exists"""
        available_path = self.output_dir / "available_clips.json"
        if available_path.exists():
            try:
                data = json.loads(available_path.read_text())
                return sorted([c["clip_id"] for c in data.get("clips", [])])
            except Exception as e:
                self.log(f"Error loading available_clips.json: {e}", "WARNING")
        return []

    def _refresh_available_clips_from_source(self) -> None:
        """Enumerate the active source and (re)write available_clips.json.

        For non-Granicus sources (e.g. YouTube) there is no integer clip-id
        range to probe with probe_clips.py, so the channel is enumerated by
        the source adapter (synthetic-id map) and written into the SAME
        available_clips.json format load_available_clips() reads:

            {"clips": [{"clip_id": <int>, "title": <str|None>}, ...],
             "total_found": N}

        sorted by clip_id ascending. Best-effort: any failure logs a WARNING
        and returns so a cron `--auto` run degrades gracefully instead of
        crashing. The Granicus path never calls this (auto_process guards on
        source_type), so probe_clips.py remains its sole producer.
        """
        try:
            refs = self.source.list_meetings()
            clips = sorted(
                ({"clip_id": int(r.clip_id), "title": r.title} for r in refs),
                key=lambda c: c["clip_id"],
            )
            data = {"clips": clips, "total_found": len(clips)}
            available_path = self.output_dir / "available_clips.json"
            available_path.write_text(json.dumps(data, indent=2))
            self.log(f"Refreshed available_clips.json from source: {len(clips)} clips")
        except Exception as e:
            self.log(f"Could not refresh available clips from source: {e}", "WARNING")
            return

    def _record_failure(self, clip_id: int, reason: str) -> None:
        """Record (or update) a clip's entry in the keyed failed-clips ledger.

        One entry per clip: first_ts is stamped once, last_ts + attempts move
        on each new failure, reason reflects the latest. Counting a per-clip
        ``attempts`` counter (instead of appending a row per attempt) is what
        keeps stale failures from burning the lifetime retry budget.
        """
        failed = self.state.setdefault("failed_clips", {})
        if not isinstance(failed, dict):
            failed = _normalize_failed_clips(failed)
            self.state["failed_clips"] = failed
        key = str(clip_id)
        ts = datetime.now().isoformat()
        rec = failed.get(key)
        if not isinstance(rec, dict):
            failed[key] = {
                "first_ts": ts, "last_ts": ts, "attempts": 1, "reason": reason,
            }
        else:
            rec["attempts"] = rec.get("attempts", 0) + 1
            rec["last_ts"] = ts
            rec["reason"] = reason
            rec.setdefault("first_ts", ts)

    def _clear_failure(self, clip_id: int) -> None:
        """Drop a clip's failed-clips entry (called when it enters processed)."""
        failed = self.state.get("failed_clips")
        if isinstance(failed, dict):
            failed.pop(str(clip_id), None)

    def _advance_cursor(self, clip_id: int) -> None:
        """Advance last_processed_clip_id, never regressing it.

        Explicit reprocessing of an old clip (single-clip CLI runs, the
        weekly --retry-failed-sweep) must not rewind the --auto cursor —
        that would make the next cron re-walk every clip after it.
        """
        current = self.state.get("last_processed_clip_id") or 0
        self.state["last_processed_clip_id"] = max(current, clip_id)

    MAX_AUTO_RETRIES = 3
    RETRY_RECENCY_DAYS = 7

    def _retry_candidates(self, processed_set: set, available_set: set = None) -> list:
        """Clips that failed recently, are under the retry cap, and aren't yet processed.

        Only considers clips whose most-recent failure timestamp is within
        RETRY_RECENCY_DAYS — this avoids re-attempting ancient failures every
        run while still catching transient failures (e.g. download blips).
        Returns clip IDs sorted ascending. If available_set is given, restricts
        to clips known to exist on Granicus.
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=self.RETRY_RECENCY_DAYS)
        failed = _normalize_failed_clips(self.state.get("failed_clips"))
        eligible = []
        for key, rec in failed.items():
            if not isinstance(rec, dict):
                continue
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            if cid in processed_set:
                continue
            if rec.get("attempts", 0) >= self.MAX_AUTO_RETRIES:
                continue
            last_ts = rec.get("last_ts")
            if not last_ts:
                continue
            try:
                ts = datetime.fromisoformat(last_ts)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            eligible.append(cid)
        if available_set is not None:
            eligible = [c for c in eligible if c in available_set]
        return sorted(eligible)

    # The weekly second-chance sweep (--retry-failed-sweep) exists because
    # the inline --auto retry above burns its whole MAX_AUTO_RETRIES budget
    # across ~3 consecutive 6-hourly crons (~12h), while Granicus sometimes
    # posts a meeting's video days after the clip page appears. Clips that
    # exhausted the inline budget were silently dropped for good — 6804 /
    # 6816 / 6832 (June–July 2026) were real meetings lost this way. The
    # sweep re-attempts any still-available unprocessed failure for
    # SWEEP_WINDOW_DAYS after its FIRST failure (weekly cadence ≈ 8 spaced
    # attempts); keying on first-failure means re-failing inside the sweep
    # can't keep a dead clip eligible forever.
    SWEEP_WINDOW_DAYS = 60

    def _sweep_candidates(self, processed_set: set, available_set: set = None,
                          now: datetime = None) -> list:
        """Failed, unprocessed, still-available clips whose FIRST failure is
        within SWEEP_WINDOW_DAYS. No attempt cap — the weekly cadence plus
        the first-failure window bounds total attempts (~8)."""
        from datetime import timedelta

        now = now or datetime.now()
        cutoff = now - timedelta(days=self.SWEEP_WINDOW_DAYS)
        failed = _normalize_failed_clips(self.state.get("failed_clips"))
        eligible = []
        for key, rec in failed.items():
            if not isinstance(rec, dict):
                continue
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            if cid in processed_set:
                continue
            first_ts = rec.get("first_ts")
            if not first_ts:
                continue
            try:
                ts = datetime.fromisoformat(first_ts)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            eligible.append(cid)
        if available_set is not None:
            eligible = [c for c in eligible if c in available_set]
        return sorted(eligible)

    def retry_failed_sweep(self, max_clips: int = 10) -> dict:
        """Weekly second-chance pass over dropped failed clips.

        See the SWEEP_WINDOW_DAYS comment above for why this exists.
        """
        available_clips = self.load_available_clips()
        processed_set = set(self.state.get("processed_clips", []))
        candidates = self._sweep_candidates(
            processed_set,
            set(available_clips) if available_clips else None,
        )[:max_clips]

        if not candidates:
            self.log("Retry sweep: no eligible failed clips")
            return {"processed": [], "failed": [], "skipped": []}

        self.log(f"Retry sweep: re-attempting {len(candidates)} dropped clip(s): {candidates}")
        results = {"processed": [], "failed": [], "skipped": []}
        for idx, clip_id in enumerate(candidates, 1):
            self.log(f"\n{'=' * 70}")
            self.log(f"Retry sweep clip {clip_id} - [{idx}/{len(candidates)}]")
            self.log(f"{'=' * 70}")
            if self.process_clip(clip_id):
                results["processed"].append(clip_id)
            else:
                results["failed"].append(clip_id)
        return results

    # Transcripts smaller than this are almost certainly truncated (a dropped
    # chunk / interrupted upload), independent of the word-count gate.
    SHORT_TRANSCRIPT_BYTES_FLOOR = 1024

    def find_short_transcripts(self, max_clips: int = 0) -> list:
        """Return ``[(clip_id, reason), ...]`` for clips whose stored Whisper
        transcript is too small OR fails ``validate_transcript``.

        Keyed on transcript file SIZE + the quality gate, NOT mere presence —
        placeholder / document-driven transcripts (legitimately short, no
        better source) are skipped so they aren't churned.
        """
        clips_dir = self.output_dir / "clips"
        candidates: list = []
        if not clips_dir.exists():
            return candidates
        for clip_dir in sorted(
                clips_dir.iterdir(),
                key=lambda x: int(x.name) if x.name.isdigit() else 0):
            if not clip_dir.is_dir() or not clip_dir.name.isdigit():
                continue
            meta_path = clip_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            files = metadata.get("files", {})
            tfile = files.get("transcript")
            if not tfile:
                continue
            tpath = clip_dir / tfile
            if not tpath.exists():
                continue
            # Leave placeholder / document-driven transcripts alone — they're
            # legitimately short and have no better source to re-derive from.
            source = metadata.get("transcript_source") or ""
            if source in ("granicus_vtt", "civicclerk_minutes", "civicclerk_agenda"):
                continue
            size = tpath.stat().st_size
            try:
                text = tpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Duration from the segments' last end (best available offline).
            duration = None
            segf = files.get("transcript_segments")
            if segf and (clip_dir / segf).exists():
                try:
                    segs = json.loads((clip_dir / segf).read_text())
                    duration = max((s.get("end", 0) for s in segs), default=0) or None
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
            if size < self.SHORT_TRANSCRIPT_BYTES_FLOOR:
                candidates.append((int(clip_dir.name), f"size:{size}b"))
                continue
            ok, reason = validate_transcript(text, duration)
            if not ok:
                candidates.append((int(clip_dir.name), reason))
        if max_clips and max_clips > 0:
            candidates = candidates[:max_clips]
        return candidates

    def repair_short_transcripts(self, max_clips: int = 10) -> dict:
        """Re-attempt clips flagged by find_short_transcripts.

        Forces a full reprocess (re-download → re-transcribe) of each flagged
        clip so a truncated / looped transcript is replaced with a fresh one.
        """
        candidates = self.find_short_transcripts(max_clips=max_clips)
        if not candidates:
            self.log("Repair: no short/garbage transcripts found")
            return {"processed": [], "failed": [], "skipped": []}

        self.log(
            f"Repair: re-attempting {len(candidates)} clip(s): "
            f"{[c for c, _ in candidates]}")
        results = {"processed": [], "failed": [], "skipped": []}
        old_force = self.force_reprocess
        self.force_reprocess = True  # ignore the cached (bad) transcript
        try:
            for idx, (clip_id, why) in enumerate(candidates, 1):
                self.log(f"\n{'=' * 70}")
                self.log(f"Repair clip {clip_id} ({why}) - [{idx}/{len(candidates)}]")
                self.log(f"{'=' * 70}")
                if self.process_clip(clip_id, skip_if_exists=False):
                    results["processed"].append(clip_id)
                else:
                    results["failed"].append(clip_id)
        finally:
            self.force_reprocess = old_force
        return results

    def auto_process(self, max_clips: int = 10, reverse: bool = False, start: int = None,
                     retry_failed: bool = True) -> dict:
        """Auto-process clips starting from last processed + 1 or FIRST_CLIP_ID.

        If available_clips.json exists, only processes clips from that list.

        Args:
            max_clips: Maximum number of clips to process.
            reverse: If True, process all unprocessed clips from most recent
                     backwards (useful for filling gaps in the middle).
            start: If set, override last_processed_clip_id and begin from this clip ID.
            retry_failed: If True (default) and not in --reverse/--start mode,
                          retry clips in failed_clips (up to MAX_AUTO_RETRIES times)
                          before advancing past last_processed_clip_id.
        """
        # Non-Granicus sources have no int clip-id range for probe_clips.py to
        # walk, so self-enumerate the channel into available_clips.json before
        # loading it. The Granicus path skips this entirely (probe_clips.py is
        # its producer), keeping LFUCG/Granicus --auto byte-identical.
        if getattr(self.cfg, "source_type", "granicus") != "granicus":
            self._refresh_available_clips_from_source()

        available_clips = self.load_available_clips()
        processed_set = set(self.state.get("processed_clips", []))
        last_id = start - 1 if start else self.state["last_processed_clip_id"]

        # Only auto-retry in the default forward path (not reverse, not explicit --start)
        do_retry = retry_failed and not reverse and start is None
        retries = self._retry_candidates(
            processed_set,
            set(available_clips) if available_clips else None
        ) if do_retry else []

        if available_clips:
            if reverse:
                # Unprocessed clips, newest-first; if --start given, only clips <= start
                candidates = [c for c in available_clips if c not in processed_set]
                if start:
                    candidates = [c for c in candidates if c <= start]
                candidates = list(reversed(candidates))
            else:
                # Default: retries first (oldest first), then unprocessed clips after last_id
                retry_set = set(retries)
                new_candidates = [
                    c for c in available_clips
                    if c > last_id and c not in processed_set and c not in retry_set
                ]
                candidates = retries + new_candidates

            clips_to_process = candidates[:max_clips]

            if not clips_to_process:
                self.log("No more clips to process from available_clips.json")
                return {"processed": [], "failed": [], "skipped": []}

            order = "newest first" if reverse else "oldest first"
            self.log(f"Auto-processing {len(clips_to_process)} clips from available_clips.json ({order})")
            self.log(f"Clips: {clips_to_process}")
            if not reverse and retries:
                retried = [c for c in clips_to_process if c in set(retries)]
                if retried:
                    self.log(f"Retrying {len(retried)} previously failed clip(s): {retried}")

            results = {"processed": [], "failed": [], "skipped": []}
            for idx, clip_id in enumerate(clips_to_process, 1):
                self.log(f"\n{'=' * 70}")
                self.log(f"Clip {clip_id} - [{idx}/{len(clips_to_process)}]")
                self.log(f"{'=' * 70}")

                success = self.process_clip(clip_id)
                if success:
                    results["processed"].append(clip_id)
                else:
                    results["failed"].append(clip_id)

            return results
        else:
            if reverse:
                self.log("Warning: --reverse requires available_clips.json. "
                         "Run probe_clips.py first.", "WARNING")
                return {"processed": [], "failed": [], "skipped": []}

            # Fallback: sequential processing without available_clips.json
            if start:
                start_id = start
            elif last_id == 0:
                start_id = self.first_clip_id
            else:
                start_id = last_id + 1

            self.log(f"Auto-processing from clip {start_id} (max {max_clips} clips)")
            self.log("Tip: Run probe_clips.py to create available_clips.json for smarter processing")

            results = {"processed": [], "failed": [], "skipped": []}
            remaining = max_clips

            if retries:
                retry_slice = retries[:remaining]
                self.log(f"Retrying {len(retry_slice)} previously failed clip(s) first: {retry_slice}")
                for clip_id in retry_slice:
                    success = self.process_clip(clip_id)
                    if success:
                        results["processed"].append(clip_id)
                    else:
                        results["failed"].append(clip_id)
                remaining -= len(retry_slice)

            if remaining > 0:
                end_id = start_id + remaining - 1
                range_results = self.process_range(start_id, end_id, stop_on_failure=False)
                for key in ("processed", "failed", "skipped"):
                    results.setdefault(key, []).extend(range_results.get(key, []))

            return results

    def update_transcript_timestamps(self, max_clips: int = 0) -> dict:
        """Re-transcribe clips that have audio but no timestamp segments.

        This allows adding clickable timestamps to older transcripts.
        Only processes clips that have audio files and are missing transcript_segments.

        Args:
            max_clips: Maximum clips to process (0 = unlimited)

        Returns:
            Dict with 'updated', 'skipped', and 'failed' lists
        """
        results = {"updated": [], "skipped": [], "failed": []}
        clips_dir = self.output_dir / "clips"

        if not clips_dir.exists():
            self.log("No clips directory found", "ERROR")
            return results

        # Find clips needing transcript updates
        clips_to_update = []
        for clip_dir in sorted(clips_dir.iterdir(), key=lambda x: int(x.name) if x.name.isdigit() else 0):
            if not clip_dir.is_dir() or not clip_dir.name.isdigit():
                continue

            metadata_path = clip_dir / "metadata.json"
            if not metadata_path.exists():
                continue

            try:
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)

                files = metadata.get("files", {})

                # Check if has audio and transcript but no segments
                has_audio = files.get("audio") and (clip_dir / files["audio"]).exists()
                has_transcript = files.get("transcript")
                has_segments = files.get("transcript_segments") and (clip_dir / files["transcript_segments"]).exists()

                if has_audio and has_transcript and not has_segments:
                    clips_to_update.append({
                        "clip_id": int(clip_dir.name),
                        "clip_dir": clip_dir,
                        "metadata": metadata,
                        "audio_file": files["audio"]
                    })
            except Exception as e:
                self.log(f"Error checking clip {clip_dir.name}: {e}", "WARNING")

        if not clips_to_update:
            self.log("No clips need transcript timestamp updates")
            return results

        # Apply max limit
        if max_clips > 0:
            clips_to_update = clips_to_update[:max_clips]

        self.log(f"Found {len(clips_to_update)} clips to update with timestamps")

        for idx, clip_info in enumerate(clips_to_update, 1):
            clip_id = clip_info["clip_id"]
            clip_dir = clip_info["clip_dir"]
            metadata = clip_info["metadata"]
            audio_file = clip_info["audio_file"]

            self.log(f"\n{'=' * 70}")
            self.log(f"Updating transcript {clip_id} - [{idx}/{len(clips_to_update)}]")
            self.log(f"{'=' * 70}")

            try:
                audio_path = clip_dir / audio_file
                transcript_file = metadata["files"]["transcript"]
                transcript_path = clip_dir / transcript_file

                # Temporarily enable force_reprocess for this transcription
                old_force = self.force_reprocess
                self.force_reprocess = True

                # Re-transcribe to get timestamps
                result = self.transcribe_audio(audio_path, transcript_path)

                self.force_reprocess = old_force

                if result and result.get("segments"):
                    # Update metadata with new segments file
                    segments_filename = f"{transcript_path.stem}_segments.json"
                    metadata["files"]["transcript_segments"] = segments_filename

                    # Save updated metadata
                    with open(clip_dir / "metadata.json", 'w') as f:
                        json.dump(metadata, f, indent=2)

                    self.log(f"Updated clip {clip_id} with {len(result['segments'])} segments")
                    results["updated"].append(clip_id)
                else:
                    self.log(f"Failed to get segments for clip {clip_id}", "WARNING")
                    results["failed"].append(clip_id)

            except Exception as e:
                self.log(f"Error updating clip {clip_id}: {e}", "ERROR")
                results["failed"].append(clip_id)

        # Regenerate search index
        if results["updated"]:
            self.generate_search_index()

        self.log(f"\nTranscript update complete: {len(results['updated'])} updated, {len(results['failed'])} failed")
        return results


# Max nightly Pass-1 (GPT-4o fact extraction) attempts before a clip that has
# a transcript but keeps failing extraction is parked. Without this cap the
# summaries cron retried it every night forever, re-billing ~$0.05/attempt
# (its failure was only printed to stdout and discarded).
MAX_PASS1_ATTEMPTS = 3


def _pass1_attempts(clips_dir: Path, cid: int) -> int:
    """Read the persisted Pass-1 attempt counter from a clip's metadata.json."""
    meta_path = clips_dir / str(cid) / "metadata.json"
    try:
        with open(meta_path) as f:
            return int(json.load(f).get("pass1_attempts", 0) or 0)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return 0


def upgrade_clip_summary_v2(pipeline, clip_id: int, anthropic_client,
                            extraction_model: str, progress: str = "",
                            log=print) -> Optional[bool]:
    """Run the two-pass v2 summary (GPT-4o facts + Claude narrative) for one
    clip and save extracted_facts.json + summary.txt + metadata in place.

    Returns True on success, False on failure (with the persisted Pass-1
    attempt counter bumped — see MAX_PASS1_ATTEMPTS), None when the clip has
    no transcript to work from. Shared by --upgrade-summaries and
    --retranscribe-placeholders.
    """
    from summary_v2 import NARRATION_MODEL, build_timestamped_transcript, generate_summary_v2

    clip_dir = pipeline.output_dir / "clips" / str(clip_id)
    meta_path = clip_dir / "metadata.json"
    with open(meta_path) as f:
        metadata = json.load(f)

    files = metadata.get("files", {})
    date = metadata.get("date", "Unknown")
    meeting_body = metadata.get("meeting_body", "Unknown")

    # Load transcript. Built from the segments JSON when available so
    # real [H:MM:SS] markers are interleaved — Pass 1 must COPY its
    # transcript_approx_time values from those markers instead of
    # inventing them. Plain text has no markers → the prompt's
    # "no markers → null" rule applies.
    transcript = ""
    seg_file = files.get("transcript_segments")
    txt_file = files.get("transcript")
    if seg_file and (clip_dir / seg_file).exists():
        try:
            with open(clip_dir / seg_file) as f:
                segments = json.load(f)
            transcript = build_timestamped_transcript(segments)
        except (json.JSONDecodeError, KeyError, TypeError):
            segments = None
    if not transcript and txt_file and (clip_dir / txt_file).exists():
        transcript = (clip_dir / txt_file).read_text()

    if not transcript:
        log(f"{progress} Clip {clip_id}: no transcript, skipping")
        return None

    # Load agenda and minutes
    agenda_text = None
    if files.get("agenda_txt") and (clip_dir / files["agenda_txt"]).exists():
        agenda_text = (clip_dir / files["agenda_txt"]).read_text()

    minutes_text = None
    if files.get("minutes_txt") and (clip_dir / files["minutes_txt"]).exists():
        minutes_text = (clip_dir / files["minutes_txt"]).read_text()

    log(f"{progress} Clip {clip_id} ({date} {meeting_body})")

    try:
        summary, facts = generate_summary_v2(
            openai_client=pipeline.client,
            anthropic_client=anthropic_client,
            transcript=transcript,
            agenda_text=agenda_text,
            minutes_text=minutes_text,
            meeting_body=meeting_body,
            date=date,
            extraction_model=extraction_model,
            log_fn=lambda msg: log(f"  {msg}"),
        )

        if facts:
            (clip_dir / "extracted_facts.json").write_text(
                json.dumps(facts, indent=2, ensure_ascii=False)
            )
            metadata["files"]["extracted_facts"] = "extracted_facts.json"

        if summary:
            (clip_dir / "summary.txt").write_text(summary)
            metadata["files"]["summary_txt"] = "summary.txt"
            metadata.setdefault("models", {})["summary"] = f"{extraction_model}+{NARRATION_MODEL}"
            metadata["summary_updated_at"] = datetime.now().isoformat()

        # A successful extraction clears any prior Pass-1 failure count.
        if facts:
            metadata.pop("pass1_attempts", None)

        # Save updated metadata
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"  ERROR: {e}")
        # Persist a Pass-1 attempt counter so a clip that keeps failing
        # extraction is parked after MAX_PASS1_ATTEMPTS instead of
        # re-billing GPT-4o every nightly summaries_cron run.
        attempts = int(metadata.get("pass1_attempts", 0) or 0) + 1
        metadata["pass1_attempts"] = attempts
        metadata["pass1_last_error"] = str(e)[:300]
        metadata["pass1_last_attempt_at"] = datetime.now().isoformat()
        try:
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
        except OSError as werr:
            log(f"  (could not persist pass1_attempts: {werr})")
        if attempts >= MAX_PASS1_ATTEMPTS:
            log(
                f"  Clip {clip_id} has now failed Pass-1 {attempts}x — "
                f"will be parked on future runs"
            )
        return False


def filter_upgrade_candidates(clips_dir: Path, all_clip_ids: List[int],
                              max_clips: int, explicit: bool,
                              force: bool) -> tuple[List[int], int]:
    """Drop already-upgraded clips, THEN apply the --max cap.

    Slicing before filtering took the N OLDEST (already-upgraded) clips, so
    `--upgrade-summaries --max N` no-oped once the oldest N were done.
    Explicitly-named clips are never filtered or capped.

    Also parks clips that have exhausted MAX_PASS1_ATTEMPTS Pass-1 extraction
    attempts (unless explicit/force), so a chronically-failing clip stops
    re-billing GPT-4o every night. Both kinds of skip fold into skipped_count.

    Returns (clip_ids_to_process, skipped_count).
    """
    clip_ids = []
    skipped = 0
    for cid in all_clip_ids:
        facts_path = clips_dir / str(cid) / "extracted_facts.json"
        if facts_path.exists() and not explicit and not force:
            skipped += 1
        elif not explicit and not force and \
                _pass1_attempts(clips_dir, cid) >= MAX_PASS1_ATTEMPTS:
            print(
                f"Skipping clip {cid}: {MAX_PASS1_ATTEMPTS}+ failed Pass-1 "
                f"extraction attempts (parked to stop re-billing)"
            )
            skipped += 1
        else:
            clip_ids.append(cid)
    if not explicit and max_clips:
        clip_ids = clip_ids[:max_clips]
    return clip_ids, skipped


def main():
    parser = argparse.ArgumentParser(
        description="LFUCG Meeting Pipeline - Download, transcribe, and generate meeting summaries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 6669                          # Process single clip
  %(prog)s 6669 6675                     # Process range (inclusive)
  %(prog)s --auto                        # Auto-process from FIRST_CLIP_ID or last + 1
  %(prog)s --auto --max 5                # Auto-process up to 5 clips
  %(prog)s --auto --start 6480            # Start auto-processing from clip 6480
  %(prog)s --auto --reverse              # Process most recent clips first
  %(prog)s --scrape                      # Scrape and process all new clips
  %(prog)s --scrape --reverse --max 5    # Scrape, process 5 most recent first
  %(prog)s --generate-index              # Generate search index from all clips
  %(prog)s --backfill-docs               # Check all clips for missing minutes/agenda
  %(prog)s --backfill-docs --max 50      # Check 50 most recent clips
  %(prog)s --backfill-docs --regenerate-summary  # Also regenerate summaries
  %(prog)s --update-transcripts          # Add timestamps to existing transcripts
  %(prog)s --update-transcripts --max 5  # Update up to 5 transcripts
  %(prog)s 6669 --no-audio               # Don't keep audio files
  %(prog)s 6669 --force                  # Reprocess even if files exist
        """
    )

    parser.add_argument(
        "clip_ids",
        nargs="*",
        type=int,
        help="Clip ID(s) to process. Single ID or range (start end)"
    )

    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-process from last processed clip + 1 (or FIRST_CLIP_ID)"
    )

    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Scrape Granicus site for available clips and process new ones"
    )

    parser.add_argument(
        "--generate-index",
        action="store_true",
        help="Generate search index (index.json) from all processed clips"
    )

    parser.add_argument(
        "--build-search-db",
        action="store_true",
        help="Build the SQLite FTS5 search.db (server-side full-text search)"
    )

    parser.add_argument(
        "--prerender",
        action="store_true",
        help="Regenerate the pre-rendered per-clip HTML pages (lfucg_output/prerender/"
             "meeting/<id>) from index.json + the live SPA shell. Incremental by "
             "default; add --full to re-render every page (e.g. after a bundle deploy). "
             "Also runs automatically at the end of --generate-index / each batch."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="With --prerender: re-render every clip page, ignoring the fingerprint state"
    )

    parser.add_argument(
        "--max",
        type=int,
        default=10,
        help="Maximum clips to process in auto/scrape mode (default: 10)"
    )

    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Process clips in reverse chronological order (most recent first)"
    )

    parser.add_argument(
        "--start",
        type=int,
        help="Start auto-processing from this clip ID (overrides last_processed_clip_id)"
    )

    parser.add_argument(
        "--no-retry-failed",
        action="store_true",
        help="In --auto mode, skip retrying clips in failed_clips (default: retry up to 3 times)"
    )

    parser.add_argument(
        "--retry-failed-sweep",
        action="store_true",
        help="Second-chance pass: re-attempt failed clips that are still listed "
             "by the source and unprocessed, for 60 days after their first "
             "failure. Complements the inline --auto retry (3 attempts), which "
             "burns out across ~12h of cron runs — too fast for videos Granicus "
             "posts late. Meant for the weekly backfill cron. --max caps it (default 10)."
    )

    parser.add_argument(
        "--repair-short-transcripts",
        action="store_true",
        help="Re-attempt clips whose stored Whisper transcript is too small "
             "(< 1KB) or fails the quality gate (repetition loop / low "
             "coverage). Forces a full re-download + re-transcribe of each. "
             "--max caps it (default 10). Skips VTT/document-driven placeholders."
    )

    parser.add_argument(
        "--output-dir",
        default="./lfucg_output",
        help="Output directory (default: ./lfucg_output)"
    )

    parser.add_argument(
        "--view-id",
        default=None,
        help="Granicus view ID (default: from jurisdiction config, 14 for LFUCG)"
    )

    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Don't keep audio files after processing"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reprocessing even if files already exist"
    )

    parser.add_argument(
        "--transcribe-model",
        default="whisper-1",
        help="OpenAI transcription model (default: whisper-1)"
    )

    parser.add_argument(
        "--transcriber",
        choices=["whisper", "elevenlabs"],
        default="whisper",
        help="Transcription backend: 'whisper' (OpenAI, default) or "
             "'elevenlabs' (Scribe, requires ELEVENLABS_API_KEY)"
    )

    parser.add_argument(
        "--transcribe-timeout",
        type=int,
        default=600,
        help="Timeout in seconds for transcription upload (default: 600)"
    )

    parser.add_argument(
        "--summary-model",
        default="gpt-4o",
        help="OpenAI summary generation model (default: gpt-4o)"
    )


    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity"
    )

    parser.add_argument(
        "--update-summary",
        action="store_true",
        help="Only update minutes and regenerate summaries (skip audio/transcription)"
    )

    parser.add_argument(
        "--update-transcripts",
        action="store_true",
        help="Re-transcribe clips to add timestamp segments (for clickable timestamps in UI)"
    )

    parser.add_argument(
        "--rag",
        action="store_true",
        help="Enable RAG ingestion after processing each clip"
    )

    parser.add_argument(
        "--rebuild-rag",
        action="store_true",
        help="Re-embed all clips into the RAG vector store"
    )

    parser.add_argument(
        "--test-summary",
        action="store_true",
        help="Test new two-pass summary on a few clips, writing to a test output directory for comparison"
    )

    parser.add_argument(
        "--test-summary-dir",
        default="./lfucg_test_output",
        help="Output directory for --test-summary (default: ./lfucg_test_output)"
    )

    parser.add_argument(
        "--upgrade-summaries",
        action="store_true",
        help="Run two-pass summary (GPT-4o extraction + Claude narration), saving in-place. "
             "With no clip IDs: runs on all clips that have a transcript, skipping ones that "
             "already have extracted_facts.json. Granicus-VTT placeholder transcripts are NOT "
             "skipped — facts/votes get extracted from the noisy caption track; run "
             "--retranscribe-placeholders on Council/PC clips first if you want Whisper-grade "
             "facts. With a single clip ID or start/end range: runs on those specific clips "
             "(reprocessed even if already done)."
    )

    parser.add_argument(
        "--retranscribe-placeholders",
        action="store_true",
        help="Find clips whose transcript is still the Granicus-VTT placeholder for a "
             "Whisper-required body (Urban County Council, Council Work Session, Committee "
             "of the Whole, Planning Commission — see LFUCG_WHISPER_REQUIRED_BODIES), run "
             "Whisper (+ VTT speaker labels), regenerate facts + summary, re-ingest into RAG "
             "and rebuild the index. Newest first. Combine with --bodies / --since / --limit / "
             "--dry-run. Honors --no-audio."
    )
    parser.add_argument(
        "--bodies",
        nargs="+",
        default=None,
        metavar="SUBSTR",
        help="With --retranscribe-placeholders: case-insensitive substrings matched against "
             "the clip title/body, replacing the default Whisper-required set "
             "(e.g. --bodies 'planning commission' 'work session')"
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="With --retranscribe-placeholders: only clips dated on/after this date"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="With --retranscribe-placeholders: cap the number of clips (newest first)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --retranscribe-placeholders: list candidates + estimated Whisper cost, change nothing"
    )

    parser.add_argument(
        "--retry-failed",
        nargs="+",
        type=int,
        default=None,
        metavar="CLIP_ID",
        help="Reset the retry counter for these failed clip IDs (attempts=0, first/last "
             "failure re-stamped to now) so the next --auto run and the weekly sweep re-attempt "
             "them. Does not process anything itself. Example: --retry-failed 6804 6816 6825"
    )

    parser.add_argument(
        "--clean-v1-summaries",
        action="store_true",
        help="Remove all summary.html files and summary.txt files from clips that have NOT been upgraded (no extracted_facts.json)"
    )

    parser.add_argument(
        "--backfill-docs",
        action="store_true",
        help="Check processed clips (highest first) for missing minutes/agenda and download them"
    )

    parser.add_argument(
        "--reocr-scanned-docs",
        action="store_true",
        help="Re-OCR scanned minutes/agenda PDFs truncated by the old 5-page OCR cap "
             "(6+ pages, no native text), then rebuild search.db + re-embed. "
             "--max caps clips checked (newest first); idempotent via docs_reocr_at."
    )
    parser.add_argument(
        "--regenerate-summary",
        action="store_true",
        help="With --backfill-docs, regenerate summary when new docs are found"
    )

    parser.add_argument(
        "--backfill-tables-of-motions",
        action="store_true",
        help="Scan agendas for embedded 'Table of Motions' blocks and merge "
             "the official motions onto the prior-meeting clips they record "
             "(work sessions have no official minutes). Replaces those clips' "
             "transcript-derived motions, re-ingests RAG + rebuilds search.db. "
             "Use --max to limit, --force to re-apply, --no-reingest to defer."
    )

    parser.add_argument(
        "--no-reingest",
        action="store_true",
        help="With --backfill-tables-of-motions, skip RAG re-ingest + "
             "search.db rebuild (defer to the next batch run)"
    )

    args = parser.parse_args()

    # Initialize pipeline
    try:
        pipeline = LFUCGPipeline(
            output_dir=args.output_dir,
            view_id=args.view_id,
            transcribe_model=args.transcribe_model,
            summary_model=args.summary_model,
            keep_audio=not args.no_audio,
            verbose=not args.quiet,
            force_reprocess=args.force,
            transcribe_timeout=args.transcribe_timeout,
            transcriber=args.transcriber
        )
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Set RAG enabled flag
    pipeline.rag_enabled = args.rag

    # Handle rebuild-rag mode
    if args.rebuild_rag:
        try:
            from rag.ingest import ingest_clip as rag_ingest_clip, get_vecstore, save_rag_state
            from clients import get_openai

            openai_client = get_openai()
            store = get_vecstore(str(pipeline.output_dir))

            # Clear existing state
            state = {"ingested_clips": []}
            save_rag_state(state, pipeline.output_dir)

            # Find all clips with metadata
            clips_dir = pipeline.output_dir / "clips"
            clip_ids = []
            for name in sorted(os.listdir(clips_dir)):
                meta_path = clips_dir / name / "metadata.json"
                if meta_path.exists():
                    try:
                        clip_ids.append(int(name))
                    except ValueError:
                        continue

            print(f"Rebuilding RAG index for {len(clip_ids)} clips...")
            for i, clip_id in enumerate(clip_ids):
                print(f"[{i + 1}/{len(clip_ids)}] Clip {clip_id}")
                rag_ingest_clip(clip_id, pipeline.output_dir, store, openai_client,
                                rag_state=state, verbose=True)

            from rag.ingest import get_stats
            stats = get_stats(store)
            print(f"\nRAG rebuild complete: {stats['total_chunks']} chunks from {stats['unique_clips']} clips")
        except ImportError:
            print("Error: RAG dependencies not installed. Run: uv sync --extra rag")
            sys.exit(1)
        sys.exit(0)

    # Handle test-summary mode
    if args.test_summary:
        try:
            from summary_v2 import build_timestamped_transcript, generate_summary_v2
            from clients import get_anthropic, MissingAPIKey
        except ImportError:
            print("Error: RAG dependencies not installed. Run: uv sync --extra rag")
            sys.exit(1)

        try:
            anthropic_client = get_anthropic()
        except MissingAPIKey as e:
            print(f"Error (--test-summary): {e}")
            sys.exit(1)
        test_dir = Path(args.test_summary_dir)
        test_dir.mkdir(parents=True, exist_ok=True)

        # Find clips to test: use explicit clip IDs, or pick the most recent N
        if args.clip_ids:
            test_clip_ids = args.clip_ids
        else:
            # Pick the most recent processed clips
            clips_dir = pipeline.output_dir / "clips"
            test_clip_ids = []
            for name in sorted(os.listdir(clips_dir), reverse=True):
                meta_path = clips_dir / name / "metadata.json"
                if meta_path.exists():
                    try:
                        test_clip_ids.append(int(name))
                    except ValueError:
                        continue
                if len(test_clip_ids) >= args.max:
                    break

        print(f"\nTesting two-pass summary on {len(test_clip_ids)} clips")
        print(f"Output directory: {test_dir}")
        print(f"Extraction model: {args.summary_model}")
        print(f"Narration model: Claude Sonnet")
        print()

        for i, clip_id in enumerate(test_clip_ids, 1):
            clip_dir = pipeline.output_dir / "clips" / str(clip_id)
            meta_path = clip_dir / "metadata.json"
            if not meta_path.exists():
                print(f"[{i}/{len(test_clip_ids)}] Clip {clip_id}: no metadata, skipping")
                continue

            with open(meta_path) as f:
                metadata = json.load(f)

            files = metadata.get("files", {})
            date = metadata.get("date", "Unknown")
            meeting_body = metadata.get("meeting_body", "Unknown")

            # Load transcript — prefer the segments JSON so real [H:MM:SS]
            # markers are interleaved for Pass 1, same as --upgrade-summaries.
            transcript = ""
            seg_file = files.get("transcript_segments")
            txt_file = files.get("transcript")
            if seg_file and (clip_dir / seg_file).exists():
                try:
                    with open(clip_dir / seg_file) as f:
                        segments = json.load(f)
                    transcript = build_timestamped_transcript(segments)
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
            if not transcript and txt_file and (clip_dir / txt_file).exists():
                transcript = (clip_dir / txt_file).read_text()

            if not transcript:
                print(f"[{i}/{len(test_clip_ids)}] Clip {clip_id}: no transcript, skipping")
                continue

            # Load agenda and minutes
            agenda_text = None
            agenda_file = files.get("agenda_txt")
            if agenda_file:
                a_path = clip_dir / agenda_file
                if a_path.exists():
                    agenda_text = a_path.read_text()

            minutes_text = None
            minutes_file = files.get("minutes_txt")
            if minutes_file:
                m_path = clip_dir / minutes_file
                if m_path.exists():
                    minutes_text = m_path.read_text()

            print(f"[{i}/{len(test_clip_ids)}] Clip {clip_id} ({date} {meeting_body})")

            # Copy old summary for comparison
            out_dir = test_dir / str(clip_id)
            out_dir.mkdir(parents=True, exist_ok=True)

            old_summary_file = files.get("summary_txt")
            if old_summary_file:
                old_path = clip_dir / old_summary_file
                if old_path.exists():
                    (out_dir / "summary_v1.txt").write_text(old_path.read_text())

            # Generate new summary
            try:
                summary, facts = generate_summary_v2(
                    openai_client=pipeline.client,
                    anthropic_client=anthropic_client,
                    transcript=transcript,
                    agenda_text=agenda_text,
                    minutes_text=minutes_text,
                    meeting_body=meeting_body,
                    date=date,
                    extraction_model=args.summary_model,
                    log_fn=lambda msg: print(f"  {msg}"),
                )

                if facts:
                    (out_dir / "extracted_facts.json").write_text(
                        json.dumps(facts, indent=2, ensure_ascii=False)
                    )

                if summary:
                    (out_dir / "summary_v2.txt").write_text(summary)

                    old_len = len((out_dir / "summary_v1.txt").read_text()) if (out_dir / "summary_v1.txt").exists() else 0
                    print(f"  Done: v1={old_len} chars, v2={len(summary)} chars, "
                          f"facts={len(json.dumps(facts))} chars")
                else:
                    print(f"  FAILED: no summary generated")

            except Exception as e:
                print(f"  ERROR: {e}")

        print(f"\nResults written to {test_dir}/")
        print(f"Compare with: diff {test_dir}/{{clip_id}}/summary_v1.txt {test_dir}/{{clip_id}}/summary_v2.txt")
        sys.exit(0)

    # Handle clean-v1-summaries mode
    if args.clean_v1_summaries:
        clips_dir = pipeline.output_dir / "clips"
        html_removed = 0
        txt_removed = 0
        metadata_updated = 0

        for name in sorted(os.listdir(clips_dir)):
            clip_dir = clips_dir / name
            if not clip_dir.is_dir():
                continue

            has_facts = (clip_dir / "extracted_facts.json").exists()

            # Always remove summary.html (dead code)
            html_path = clip_dir / "summary.html"
            if html_path.exists():
                html_path.unlink()
                html_removed += 1

            # Remove summary.txt only if clip has NOT been upgraded
            if not has_facts:
                txt_path = clip_dir / "summary.txt"
                if txt_path.exists():
                    txt_path.unlink()
                    txt_removed += 1

            # Clean metadata references
            meta_path = clip_dir / "metadata.json"
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        metadata = json.load(f)
                    changed = False
                    if "summary_html" in metadata.get("files", {}):
                        del metadata["files"]["summary_html"]
                        changed = True
                    if not has_facts and "summary_txt" in metadata.get("files", {}):
                        del metadata["files"]["summary_txt"]
                        changed = True
                    if changed:
                        with open(meta_path, "w") as f:
                            json.dump(metadata, f, indent=2, ensure_ascii=False)
                        metadata_updated += 1
                except (json.JSONDecodeError, KeyError):
                    pass

        print(f"Cleaned v1 summaries:")
        print(f"  summary.html removed: {html_removed}")
        print(f"  summary.txt removed (non-upgraded clips): {txt_removed}")
        print(f"  metadata.json updated: {metadata_updated}")
        sys.exit(0)

    # Handle upgrade-summaries mode
    if args.upgrade_summaries:
        try:
            from summary_v2 import NARRATION_MODEL, build_timestamped_transcript, generate_summary_v2
            from clients import get_anthropic, MissingAPIKey
        except ImportError:
            print("Error: RAG dependencies not installed. Run: uv sync --extra rag")
            sys.exit(1)

        try:
            anthropic_client = get_anthropic()
        except MissingAPIKey as e:
            print(f"Error (--upgrade-summaries): {e}")
            sys.exit(1)

        clips_dir = pipeline.output_dir / "clips"

        # Determine candidate clip IDs: explicit args take precedence over scanning all clips
        if args.clip_ids:
            if len(args.clip_ids) == 1:
                candidate_ids = [args.clip_ids[0]]
            elif len(args.clip_ids) == 2:
                start, end = args.clip_ids
                candidate_ids = list(range(start, end + 1))
            else:
                print("Error: --upgrade-summaries accepts at most 2 clip IDs (single ID or start end range)")
                sys.exit(1)

            # Keep only those with metadata on disk
            all_clip_ids = []
            for cid in candidate_ids:
                if (clips_dir / str(cid) / "metadata.json").exists():
                    all_clip_ids.append(cid)
                else:
                    print(f"Skipping clip {cid}: no metadata.json found")
            explicit = True
        else:
            all_clip_ids = []
            for name in sorted(os.listdir(clips_dir), key=lambda x: int(x) if x.isdigit() else 99999):
                meta_path = clips_dir / name / "metadata.json"
                if meta_path.exists():
                    try:
                        all_clip_ids.append(int(name))
                    except ValueError:
                        continue
            explicit = False

        # Skip clips that already have extracted_facts.json (unless explicitly
        # named or --force), THEN apply --max — slicing first took the N
        # oldest, already-done clips and no-oped.
        clip_ids, skipped = filter_upgrade_candidates(
            clips_dir, all_clip_ids, args.max, explicit, args.force)

        print(f"\nUpgrading summaries: {len(clip_ids)} clips to process, {skipped} already done")
        print(f"Extraction model: {args.summary_model}")
        print(f"Narration model: Claude Sonnet\n")

        succeeded = 0
        failed_ids = []
        for i, clip_id in enumerate(clip_ids, 1):
            outcome = upgrade_clip_summary_v2(
                pipeline, clip_id, anthropic_client, args.summary_model,
                progress=f"[{i}/{len(clip_ids)}]")
            if outcome is True:
                succeeded += 1
            elif outcome is False:
                failed_ids.append(clip_id)
            # None = skipped (no transcript)

        print(f"\nDone: {succeeded} upgraded, {len(failed_ids)} failed, {skipped} previously done")
        if failed_ids:
            print(f"Failed clips: {failed_ids}")
        print(f"\nNext step: uv run python main.py --rebuild-rag")
        sys.exit(0 if not failed_ids else 1)

    # Handle re-OCR of truncated scanned documents
    if args.reocr_scanned_docs:
        max_explicit = any(a == "--max" or a.startswith("--max=") for a in sys.argv)
        results = pipeline.reocr_scanned_documents(max_clips=args.max if max_explicit else 0)
        print(f"\nRe-OCR results: {len(results['updated'])} updated, "
              f"{len(results['skipped'])} skipped, {len(results['failed'])} failed")
        if results['updated']:
            print(f"    {results['updated']}")
        sys.exit(0 if not results['failed'] else 1)

    # Handle backfill-docs mode
    if args.backfill_docs:
        # Same convention as --backfill-tables-of-motions: a bare
        # --backfill-docs sweeps ALL clips; the --max default of 10 only
        # applies when the user explicitly passes --max. (The default used to
        # silently cap the sweep at the 10 newest clips, so minutes that
        # Granicus publishes ~2 months late were never picked up.)
        max_explicit = any(a == "--max" or a.startswith("--max=")
                           for a in sys.argv)
        results = pipeline.backfill_documents(
            max_clips=args.max if max_explicit else 0,
            regenerate_summary=args.regenerate_summary
        )
        print(f"\nBackfill results:")
        print(f"  Updated (new docs found): {len(results['updated'])} clips")
        if results['updated']:
            print(f"    {results['updated']}")
        print(f"  Skipped (no new docs): {len(results['skipped'])} clips")
        print(f"  Failed: {len(results['failed'])} clips")
        if results['failed']:
            print(f"    {results['failed']}")
        sys.exit(0 if not results['failed'] else 1)

    # Handle backfill-tables-of-motions mode
    if args.backfill_tables_of_motions:
        # A backfill sweeps the whole archive by default; only cap when the
        # user explicitly passes --max (which otherwise defaults to 10 for
        # the auto/scrape paths).
        max_explicit = any(a == "--max" or a.startswith("--max=")
                           for a in sys.argv)
        result = pipeline.backfill_tables_of_motions(
            max_clips=args.max if max_explicit else 0,
            force=args.force,
            reingest=not args.no_reingest,
        )
        if not result:
            sys.exit(1)
        stats = result["stats"]
        print(f"\nTables of Motions backfill:")
        print(f"  Merged onto clips: {stats.get('matched', 0)}")
        print(f"  Motions written: {stats.get('motions_written', 0)} "
              f"(timestamps carried: {stats.get('timestamps_carried', 0)})")
        print(f"  Already done (skipped): {stats.get('skipped', 0)}")
        print(f"  No matching clip: {stats.get('no_match', 0)}")
        print(f"  Ambiguous (duplicate uploads): {stats.get('ambiguous', 0)}")
        sys.exit(0)

    # Handle --retry-failed: re-open the retry budget for specific clips
    if args.retry_failed:
        reset = pipeline.reset_failed_attempts(args.retry_failed)
        print(f"Reset retry counters for {len(reset)} clip(s): {reset}")
        untouched = [c for c in args.retry_failed if c not in reset]
        if untouched:
            print(f"Not reset (already processed): {untouched}")
        print("Next --auto run (and the weekly --retry-failed-sweep) will re-attempt them.")
        sys.exit(0)

    # Handle --retranscribe-placeholders: Whisper the Council / PC clips that
    # are still serving the Granicus caption track as their transcript.
    if args.retranscribe_placeholders:
        candidates = pipeline.find_placeholder_clips(
            bodies=args.bodies, since=args.since, limit=args.limit)
        total_min = sum(c["est_minutes"] for c in candidates)
        est_cost = total_min * pipeline.WHISPER_USD_PER_MINUTE
        print(f"\nPlaceholder clips to re-transcribe: {len(candidates)} "
              f"(~{total_min:,.0f} audio-min, est. Whisper ~${est_cost:,.2f} "
              f"+ facts/summary ~${0.12 * len(candidates):,.2f})")
        for c in candidates:
            print(f"  {c['clip_id']}  {c['date'] or '????-??-??'}  {c['est_minutes']:6.1f} min  {c['title']}")
        if args.dry_run or not candidates:
            sys.exit(0)

        try:
            from clients import get_anthropic, MissingAPIKey
            anthropic_client = get_anthropic()
        except ImportError:
            print("Error: RAG dependencies not installed. Run: uv sync --extra rag")
            sys.exit(1)
        except MissingAPIKey as e:
            print(f"Error (--retranscribe-placeholders): {e}")
            sys.exit(1)

        done, failed_ids = [], []
        for i, c in enumerate(candidates, 1):
            cid = c["clip_id"]
            pipeline.log(f"\n{'=' * 70}")
            pipeline.log(f"Re-transcribing clip {cid} ({c['title']}) - [{i}/{len(candidates)}]")
            pipeline.log(f"{'=' * 70}")
            if not pipeline.retranscribe_clip(cid):
                failed_ids.append(cid)
                continue
            outcome = upgrade_clip_summary_v2(
                pipeline, cid, anthropic_client, args.summary_model,
                progress=f"[{i}/{len(candidates)}]")
            if outcome is False:
                pipeline.log(f"Clip {cid}: transcript replaced but facts/summary failed "
                             f"(summaries_cron will retry)", "WARNING")
            done.append(cid)

        if done:
            pipeline.log(f"Re-ingesting {len(done)} clip(s) into the vector store + rebuilding index")
            pipeline._reingest_clips(done)
            try:
                pipeline.generate_search_index(build_search_db=True, seo=True)
            except Exception as e:
                pipeline.log(f"index/SEO/search.db rebuild error: {e}", "WARNING")
        print(f"\nRe-transcribed: {len(done)} {done}")
        if failed_ids:
            print(f"Failed (placeholder kept): {len(failed_ids)} {failed_ids}")
        print("Reminder: POST /admin/reload (or restart the RAG service) and run "
              "deploy/lightsail/sync_data_s3.sh to publish.")
        sys.exit(0 if not failed_ids else 1)

    # Handle prerender mode (per-clip HTML pages for crawlers — see prerender.py)
    if args.prerender:
        from prerender import generate_prerendered_pages
        entries = pipeline.load_index_clips()
        if not entries:
            print("No index.json (run --generate-index first)")
            sys.exit(1)
        stats = generate_prerendered_pages(
            entries, pipeline.output_dir, full=args.full, log=pipeline.log)
        print(f"Pre-render: {stats}")
        sys.exit(0)

    # Handle generate-index mode
    if args.generate_index:
        index_path = pipeline.generate_search_index()
        if index_path:
            print(f"Generated search index: {index_path}")
        else:
            print("Failed to generate search index")
            sys.exit(1)
        sys.exit(0)

    # Handle build-search-db mode (standalone, without regenerating
    # index.json). Useful for one-off rebuilds when only the FTS schema
    # changes and the metadata index is already up to date.
    if args.build_search_db:
        from scripts.build_search_db import build as build_search_db
        build_search_db(pipeline.output_dir, pipeline.output_dir / "search.db")
        sys.exit(0)

    # Handle update-transcripts mode
    if args.update_transcripts:
        results = pipeline.update_transcript_timestamps(max_clips=args.max)
        print(f"\nTranscript update results:")
        print(f"  Updated: {len(results['updated'])} clips")
        print(f"  Failed: {len(results['failed'])} clips")
        if results['updated']:
            print(f"  Updated clip IDs: {results['updated']}")
        if results['failed']:
            print(f"  Failed clip IDs: {results['failed']}")
        sys.exit(0 if not results['failed'] else 1)

    # Execute based on mode
    if args.update_summary:
        # Update summaries only mode
        if not args.clip_ids:
            print("Error: --update-summary requires clip ID(s)")
            print("Usage: python main.py 6669 --update-summary")
            print("       python main.py 6669 6680 --update-summary")
            sys.exit(1)

        if len(args.clip_ids) == 1:
            success = pipeline.update_clip_summary(args.clip_ids[0])
            results = {
                "processed": [args.clip_ids[0]] if success else [],
                "failed": [] if success else [args.clip_ids[0]]
            }
        else:
            results = pipeline.update_range_summaries(args.clip_ids[0], args.clip_ids[1])
            # Rename 'updated' to 'processed' for consistent output
            results["processed"] = results.pop("updated", [])

    elif args.scrape:
        # Scrape mode
        available_clips = pipeline.source.scrape_available_clips()
        if not available_clips:
            print("No clips found via scraping")
            sys.exit(1)

        processed_set = set(pipeline.state.get("processed_clips", []))

        if args.reverse:
            # All unprocessed clips, newest first (fills gaps)
            new_clips = [c for c in reversed(available_clips) if c not in processed_set]
        else:
            # Unprocessed clips after last_processed_clip_id, oldest first
            new_clips = [
                c for c in available_clips
                if c > pipeline.state["last_processed_clip_id"]
            ]

        new_clips = new_clips[:args.max]

        if not new_clips:
            print("No new clips to process")
            sys.exit(0)

        order = " (newest first)" if args.reverse else ""
        print(f"\nProcessing {len(new_clips)} clips{order}: {new_clips}")
        results = {"processed": [], "failed": [], "skipped": []}
        for idx, clip_id in enumerate(new_clips, 1):
            pipeline.log(f"\n{'=' * 70}")
            pipeline.log(f"Clip {clip_id} - [{idx}/{len(new_clips)}]")
            pipeline.log(f"{'=' * 70}")
            success = pipeline.process_clip(clip_id)
            if success:
                results["processed"].append(clip_id)
            else:
                results["failed"].append(clip_id)

    elif args.auto:
        # Auto mode
        results = pipeline.auto_process(
            args.max,
            reverse=args.reverse,
            start=args.start,
            retry_failed=not args.no_retry_failed,
        )

    elif args.retry_failed_sweep:
        # Weekly second-chance pass over dropped failed clips
        results = pipeline.retry_failed_sweep(max_clips=args.max)

    elif args.repair_short_transcripts:
        # Re-attempt clips with truncated / looped transcripts
        results = pipeline.repair_short_transcripts(max_clips=args.max)

    elif len(args.clip_ids) == 1:
        # Single clip
        success = pipeline.process_clip(args.clip_ids[0], skip_if_exists=False)
        results = {
            "processed": [args.clip_ids[0]] if success else [],
            "failed": [] if success else [args.clip_ids[0]]
        }

    elif len(args.clip_ids) == 2:
        # Range
        results = pipeline.process_range(
            args.clip_ids[0],
            args.clip_ids[1],
            stop_on_failure=False
        )

    else:
        parser.print_help()
        sys.exit(1)

    # Final index + SEO + FTS5 pass after the batch is done. The per-clip
    # callsite skips BOTH the ~30s FTS rebuild and the full-archive SEO pass
    # (~300MB read / clip.md rewrite for all clips) — doing them once here is
    # the whole point of the per-clip build_search_db=False/seo=False skips.
    if results.get("processed"):
        try:
            pipeline.generate_search_index(build_search_db=True, seo=True)
            pipeline.log("Rebuilt index.json + SEO artifacts + search.db after batch")
        except Exception as e:
            pipeline.log(f"batch-end index/SEO/search.db rebuild error: {e}", "WARNING")

    # Print summary
    print(f"\n{'=' * 60}")
    print("PROCESSING SUMMARY")
    print(f"{'=' * 60}")
    print(f"Processed: {len(results['processed'])} clips")
    if results['processed']:
        print(f"  {results['processed']}")
    print(f"Failed: {len(results.get('failed', []))} clips")
    if results.get('failed'):
        print(f"  {results['failed']}")
    print(f"\nOutput directory: {pipeline.output_dir}")
    print(f"Last processed: {pipeline.state['last_processed_clip_id']}")


if __name__ == "__main__":
    main()