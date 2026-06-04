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
import sys
import json
import argparse
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import re

from dotenv import load_dotenv
import httpx

from config import get_config
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
            transcribe_timeout: int = 600
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

        # Optional, SEPARATE agenda-document portal (WS4). None for LFUCG (no
        # [agenda] config → Granicus supplies agendas in-band, untouched).
        # Only built for jurisdictions whose video source has no agendas
        # (e.g. YouTube counties on CivicClerk/CivicPlus). When None, the
        # agenda/minutes fallback in process_clip / backfill_docs NEVER runs,
        # so the Granicus path is byte-identical.
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
        """Load pipeline state from file"""
        if self.state_file.exists():
            with open(self.state_file) as f:
                self.state = json.load(f)
        else:
            self.state = {
                "last_processed_clip_id": 0,
                "processed_clips": [],
                "failed_clips": []
            }

    def save_state(self):
        """Save pipeline state to file"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

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

    def split_audio_into_chunks(self, audio_path: Path, num_chunks: int = 3) -> List[Path]:
        """Split audio file into a fixed number of chunks."""
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)

        # Get audio duration using ffprobe
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, text=True, check=True
            )
            duration = float(result.stdout.strip())
        except Exception as e:
            self.log(f"Could not get audio duration: {e}", "WARNING")
            # Estimate duration from file size (assume ~1MB per minute at low bitrate)
            duration = file_size_mb * 60

        chunk_duration = duration / num_chunks
        self.progress(f"Splitting {duration:.0f}s audio into {num_chunks} chunks of ~{chunk_duration:.0f}s each")

        chunk_paths = []
        for i in range(num_chunks):
            start_time = i * chunk_duration
            chunk_path = audio_path.parent / f"{audio_path.stem}_chunk{i:02d}.mp3"

            cmd = [
                "ffmpeg", "-y",
                "-i", str(audio_path),
                "-ss", str(start_time),
                "-t", str(chunk_duration),
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

        self.log(f"Transcribing audio with {self.transcribe_model}")

        try:
            file_size_mb = audio_path.stat().st_size / (1024 * 1024)

            # Whisper API has 25MB limit
            MAX_SIZE_MB = 24  # Leave some headroom

            transcribe_file = audio_path
            cleanup_files = []

            # Determine if we need to split — calculate chunks so each is under MAX_SIZE_MB
            import math
            if file_size_mb > MAX_SIZE_MB:
                num_chunks = math.ceil(file_size_mb / MAX_SIZE_MB)
                self.progress(f"Audio is {file_size_mb:.2f} MB (>{MAX_SIZE_MB} MB) - splitting into {num_chunks} chunks")
            else:
                num_chunks = 0

            if num_chunks > 0:
                chunk_paths = self.split_audio_into_chunks(transcribe_file, num_chunks=num_chunks)

                if not chunk_paths:
                    self.log("Failed to split audio into chunks", "ERROR")
                    for f in cleanup_files:
                        if f.exists():
                            f.unlink()
                    return None

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

                for i, chunk_path in enumerate(chunk_paths):
                    self.progress(f"Transcribing chunk {i+1}/{len(chunk_paths)}...")

                    # Get chunk duration for offset calculation
                    try:
                        result = subprocess.run(
                            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(chunk_path)],
                            capture_output=True, text=True, check=True
                        )
                        chunk_duration = float(result.stdout.strip())
                    except Exception:
                        chunk_duration = 0

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

                            # Add segments with adjusted timestamps
                            if hasattr(chunk_result, 'segments') and chunk_result.segments:
                                for seg in chunk_result.segments:
                                    # Handle both dict and object access patterns
                                    if isinstance(seg, dict):
                                        all_segments.append({
                                            "start": seg.get("start", 0) + chunk_offset,
                                            "end": seg.get("end", 0) + chunk_offset,
                                            "text": seg.get("text", "").strip()
                                        })
                                    else:
                                        all_segments.append({
                                            "start": getattr(seg, "start", 0) + chunk_offset,
                                            "end": getattr(seg, "end", 0) + chunk_offset,
                                            "text": getattr(seg, "text", "").strip()
                                        })

                        # Update offset for next chunk
                        chunk_offset += chunk_duration

                    except Exception as e:
                        self.log(f"Error transcribing chunk {i+1}: {e}", "WARNING")
                        chunk_offset += chunk_duration  # Still advance offset
                    finally:
                        # Clean up chunk file
                        if chunk_path.exists():
                            chunk_path.unlink()

                # Clean up compressed files
                for f in cleanup_files:
                    if f.exists():
                        f.unlink()

                if not transcripts:
                    self.log("All chunks failed to transcribe", "ERROR")
                    return None

                # Combine transcripts
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
                for f in cleanup_files:
                    if f.exists():
                        f.unlink()
                        self.progress("Removed temporary compressed file")
                return None

            except httpx.HTTPStatusError as e:
                upload_elapsed = (datetime.now() - upload_start).total_seconds()
                self.log(f"OpenAI API error after {upload_elapsed:.1f}s: {e}", "ERROR")
                for f in cleanup_files:
                    if f.exists():
                        f.unlink()
                return None

            # Clean up compressed files
            for f in cleanup_files:
                if f.exists():
                    f.unlink()
                    self.progress("Removed temporary compressed file")

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

        # Extract meeting body - common abbreviations and names (search title first)
        body_patterns = [
            rf'\b({"|".join(self.cfg.body_patterns)})\b',
            r'(Task Force)',
            r'(Work Session|Regular Session|Special Session|Budget Hearing)',
        ]

        search_text = title or ""
        for pattern in body_patterns:
            match = re.search(pattern, search_text, re.IGNORECASE)
            if match:
                # Normalize casing: keep acronyms uppercase, title-case regular words
                body = match.group(1)
                acronyms = set(self.cfg.body_acronyms)
                metadata["meeting_body"] = body.upper() if body.upper() in acronyms else body.title()
                break

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
            context_parts.append(f"MEETING AGENDA:\n{agenda_text}")

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


    def generate_search_index(self, build_search_db: bool = True) -> Optional[Path]:
        """Generate index.json with all processed clips for frontend search.

        ``build_search_db=True`` (the default) also rebuilds the FTS5
        search.db that powers /api/search. Pass ``False`` from per-clip
        loop callsites — the FTS rebuild is ~30s on the full archive
        and only the final batch state needs to be searchable.
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
                            full_text = f.read()
                            # First 500 chars for preview
                            transcript_preview = full_text[:500].replace('\n', ' ').strip()

                # Read agenda text for card preview
                agenda_preview = ""
                if "files" in metadata and "agenda_txt" in metadata["files"]:
                    agenda_path = clip_dir / metadata["files"]["agenda_txt"]
                    if agenda_path.exists():
                        with open(agenda_path, 'r', encoding='utf-8') as f:
                            agenda_text = f.read()
                            agenda_preview = agenda_text[:500].replace('\n', ' ').strip()

                # Summary preview extraction (temporarily disabled)
                summary_preview = ""

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
                    "summary_preview": summary_preview,
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

        with open(index_path, 'w') as f:
            json.dump(index_data, f, indent=2)

        self.log(f"Generated index with {len(index_entries)} clips at {index_path}")

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

            # Force regenerate summary (delete existing to bypass cache)
            summary_txt_path = clip_dir / "summary.txt"
            if summary_txt_path.exists():
                summary_txt_path.unlink()

            # Generate new summary with all context
            summary = self.generate_summary(
                clip_id,
                transcript,
                agenda_text,
                summary_txt_path,
                minutes_text=minutes_result.get("text")
            )

            if not summary:
                self.log(f"Failed to generate summary for clip {clip_id}", "ERROR")
                return False

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

        # Regenerate index if anything was updated
        if results["updated"]:
            self.generate_search_index()

        self.log(f"\nBackfill complete: {len(results['updated'])} updated, "
                 f"{len(results['skipped'])} skipped, {len(results['failed'])} failed")
        return results

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
                    meeting_date, body, clip_dir)
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
                    meeting_date, body, clip_dir)
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
                                    get_chroma_collection, load_rag_state,
                                    save_rag_state)
            from clients import get_openai
        except ImportError:
            self.log("RAG deps not installed — skipping re-ingest "
                     "(run: uv sync --extra rag)", "WARNING")
            return
        try:
            collection = get_chroma_collection(str(self.output_dir))
            openai_client = get_openai()
            state = load_rag_state(self.output_dir)
            # ingest_clip deletes the clip's existing chunks before storing
            # (skip_if_ingested defaults False), so this re-embeds cleanly.
            for clip_id in clip_ids:
                rag_ingest_clip(clip_id, self.output_dir, collection,
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

            if caption_info and caption_info.get("transcript_text"):
                # VTT path: Granicus stenographer captions are the
                # canonical transcript. Audio download + Whisper are
                # skipped entirely for this clip.
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
                self.log(
                    f"Using Granicus VTT transcript ({len(speakers)} speakers, "
                    f"{len(transcript_segments)} cues) — skipping Whisper"
                )
            else:
                # Whisper path: no captions track available, so we have
                # to transcribe the audio ourselves.
                audio_filename = self.source.download_audio(clip_id, clip_dir, title, date=meeting_date)
                if not audio_filename:
                    self.state["failed_clips"].append({
                        "clip_id": clip_id,
                        "reason": "download_failed",
                        "timestamp": datetime.now().isoformat()
                    })
                    self.state["last_processed_clip_id"] = clip_id
                    self.save_state()
                    return False
                files["audio"] = audio_filename
                audio_path = clip_dir / audio_filename

                transcript_result = self.transcribe_audio(audio_path, transcript_path)
                if isinstance(transcript_result, dict):
                    transcript = transcript_result["text"]
                    transcript_segments = transcript_result.get("segments")
                elif isinstance(transcript_result, str):
                    transcript = transcript_result

                if not transcript:
                    self.state["failed_clips"].append({
                        "clip_id": clip_id,
                        "reason": "transcription_failed",
                        "timestamp": datetime.now().isoformat()
                    })
                    self.state["last_processed_clip_id"] = clip_id
                    self.save_state()
                    return False

                # transcribe_audio writes both transcript_path and the
                # adjacent _segments.json file itself.
                files["transcript"] = transcript_filename
                if transcript_segments:
                    files["transcript_segments"] = segments_filename

            # Step 6: Download and extract agenda (optional - don't fail if
            # unavailable). Falls back to the separate AgendaSource (WS4) only
            # when the video source has no agenda AND one is configured — never
            # for LFUCG/Granicus (agenda_source is None).
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
            # no-op for LFUCG (agenda_source is None).
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

            # Step 10: Save enhanced metadata
            end_time = datetime.now()
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
                "models": {
                    # Reflect what actually produced the transcript: VTT
                    # path skips Whisper entirely, so claiming whisper-1
                    # would be misleading downstream.
                    "transcribe": (
                        "granicus_vtt"
                        if transcript_source == "granicus_vtt"
                        else self.transcribe_model
                    ),
                }
            }

            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            # Update state
            self.state["last_processed_clip_id"] = clip_id
            if clip_id not in self.state["processed_clips"]:
                self.state["processed_clips"].append(clip_id)
            self.save_state()

            self.log(f"Successfully processed clip {clip_id} in {metadata['processing_time_seconds']:.1f}s")

            # RAG ingestion (if enabled)
            if getattr(self, 'rag_enabled', False):
                try:
                    from rag.ingest import ingest_clip as rag_ingest_clip, get_chroma_collection
                    from clients import get_openai
                    collection = get_chroma_collection(str(self.output_dir))
                    rag_ingest_clip(clip_id, self.output_dir, collection, get_openai(), verbose=self.verbose)
                    self.log(f"RAG: Ingested clip {clip_id}")
                except ImportError:
                    self.log("RAG dependencies not installed, skipping ingestion", "WARNING")
                except Exception as e:
                    self.log(f"RAG ingestion failed for clip {clip_id}: {e}", "WARNING")

            # Regenerate the metadata search index after each successful
            # clip so the frontend list shows the new clip immediately.
            # Skip the FTS5 rebuild here — it'd run for every clip in a
            # batch (~30s × N). Batch-end callers do a final rebuild.
            self.generate_search_index(build_search_db=False)

            return True

        except Exception as e:
            self.log(f"Unexpected error processing clip {clip_id}: {e}", "ERROR")
            self.state["failed_clips"].append({
                "clip_id": clip_id,
                "reason": f"unexpected_error: {str(e)}",
                "timestamp": datetime.now().isoformat()
            })
            # Update last_processed_clip_id even on failure so auto mode moves forward
            self.state["last_processed_clip_id"] = clip_id
            self.save_state()
            return False

    def process_range(
            self,
            start_id: int,
            end_id: int,
            stop_on_failure: bool = True
    ) -> dict:
        """Process a range of clip IDs"""

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
        failed_counts = {}
        latest_ts = {}
        for entry in self.state.get("failed_clips", []):
            if not isinstance(entry, dict):
                continue
            cid = entry.get("clip_id")
            if cid is None:
                continue
            failed_counts[cid] = failed_counts.get(cid, 0) + 1
            ts_str = entry.get("timestamp")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if cid not in latest_ts or ts > latest_ts[cid]:
                    latest_ts[cid] = ts

        eligible = [
            cid for cid, count in failed_counts.items()
            if cid not in processed_set
            and count < self.MAX_AUTO_RETRIES
            and latest_ts.get(cid) is not None
            and latest_ts[cid] >= cutoff
        ]
        if available_set is not None:
            eligible = [c for c in eligible if c in available_set]
        return sorted(eligible)

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
        help="Run two-pass summary (GPT-4o extraction + Claude Sonnet narration), saving in-place. With no clip IDs: runs on all clips, skipping ones that already have extracted_facts.json. With a single clip ID or start/end range: runs on those specific clips (reprocessed even if already done)."
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
            transcribe_timeout=args.transcribe_timeout
        )
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Set RAG enabled flag
    pipeline.rag_enabled = args.rag

    # Handle rebuild-rag mode
    if args.rebuild_rag:
        try:
            from rag.ingest import ingest_clip as rag_ingest_clip, get_chroma_collection, save_rag_state
            from clients import get_openai

            openai_client = get_openai()
            collection = get_chroma_collection(str(pipeline.output_dir))

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
                rag_ingest_clip(clip_id, pipeline.output_dir, collection, openai_client,
                                rag_state=state, verbose=True)

            from rag.ingest import get_stats
            stats = get_stats(collection)
            print(f"\nRAG rebuild complete: {stats['total_chunks']} chunks from {stats['unique_clips']} clips")
        except ImportError:
            print("Error: RAG dependencies not installed. Run: uv sync --extra rag")
            sys.exit(1)
        sys.exit(0)

    # Handle test-summary mode
    if args.test_summary:
        try:
            from summary_v2 import generate_summary_v2
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

            # Load transcript
            transcript = ""
            for key in ("transcript", "transcript_segments"):
                fname = files.get(key)
                if fname and key == "transcript":
                    t_path = clip_dir / fname
                    if t_path.exists():
                        transcript = t_path.read_text()
                        break
                elif fname and key == "transcript_segments":
                    t_path = clip_dir / fname
                    if t_path.exists():
                        segments = json.load(open(t_path))
                        transcript = " ".join(s["text"] for s in segments)
                        break

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
            from summary_v2 import generate_summary_v2
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
            all_clip_ids = all_clip_ids[:args.max]
            explicit = False

        # Skip clips that already have extracted_facts.json, unless explicitly named or --force
        clip_ids = []
        skipped = 0
        for cid in all_clip_ids:
            facts_path = clips_dir / str(cid) / "extracted_facts.json"
            if facts_path.exists() and not explicit and not args.force:
                skipped += 1
            else:
                clip_ids.append(cid)

        print(f"\nUpgrading summaries: {len(clip_ids)} clips to process, {skipped} already done")
        print(f"Extraction model: {args.summary_model}")
        print(f"Narration model: Claude Sonnet\n")

        succeeded = 0
        failed_ids = []
        for i, clip_id in enumerate(clip_ids, 1):
            clip_dir = clips_dir / str(clip_id)
            meta_path = clip_dir / "metadata.json"

            with open(meta_path) as f:
                metadata = json.load(f)

            files = metadata.get("files", {})
            date = metadata.get("date", "Unknown")
            meeting_body = metadata.get("meeting_body", "Unknown")

            # Load transcript
            transcript = ""
            seg_file = files.get("transcript_segments")
            txt_file = files.get("transcript")
            if seg_file and (clip_dir / seg_file).exists():
                try:
                    with open(clip_dir / seg_file) as f:
                        segments = json.load(f)
                    transcript = " ".join(s["text"] for s in segments)
                except (json.JSONDecodeError, KeyError):
                    segments = None
            elif txt_file and (clip_dir / txt_file).exists():
                transcript = (clip_dir / txt_file).read_text()

            if not transcript:
                print(f"[{i}/{len(clip_ids)}] Clip {clip_id}: no transcript, skipping")
                continue

            # Load agenda and minutes
            agenda_text = None
            if files.get("agenda_txt") and (clip_dir / files["agenda_txt"]).exists():
                agenda_text = (clip_dir / files["agenda_txt"]).read_text()

            minutes_text = None
            if files.get("minutes_txt") and (clip_dir / files["minutes_txt"]).exists():
                minutes_text = (clip_dir / files["minutes_txt"]).read_text()

            print(f"[{i}/{len(clip_ids)}] Clip {clip_id} ({date} {meeting_body})")

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
                    (clip_dir / "extracted_facts.json").write_text(
                        json.dumps(facts, indent=2, ensure_ascii=False)
                    )
                    metadata["files"]["extracted_facts"] = "extracted_facts.json"

                if summary:
                    (clip_dir / "summary.txt").write_text(summary)
                    metadata["files"]["summary_txt"] = "summary.txt"
                    metadata.setdefault("models", {})["summary"] = f"{args.summary_model}+claude-sonnet"

                # Save updated metadata
                with open(meta_path, "w") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)

                succeeded += 1
            except Exception as e:
                print(f"  ERROR: {e}")
                failed_ids.append(clip_id)

        print(f"\nDone: {succeeded} upgraded, {len(failed_ids)} failed, {skipped} previously done")
        if failed_ids:
            print(f"Failed clips: {failed_ids}")
        print(f"\nNext step: uv run python main.py --rebuild-rag")
        sys.exit(0 if not failed_ids else 1)

    # Handle backfill-docs mode
    if args.backfill_docs:
        results = pipeline.backfill_documents(
            max_clips=args.max,
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

    # Final FTS5 rebuild after the batch is done. The per-clip
    # callsite skips it (~30s × N would be wasteful), so we do one
    # build here so /api/search reflects the new clips.
    if results.get("processed"):
        try:
            from scripts.build_search_db import build as build_search_db_fn
            build_search_db_fn(pipeline.output_dir, pipeline.output_dir / "search.db", verbose=False)
            pipeline.log("Rebuilt search.db after batch")
        except Exception as e:
            pipeline.log(f"search.db rebuild error: {e}", "WARNING")

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