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
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import requests
from bs4 import BeautifulSoup
import re

from dotenv import load_dotenv
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
import httpx

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
            view_id: str = "14",
            openai_api_key: Optional[str] = None,
            transcribe_model: str = "whisper-1",
            summary_model: str = "gpt-4o",
            topic_model: str = "gpt-4o-mini",
            keep_audio: bool = True,
            verbose: bool = True,
            force_reprocess: bool = False,
            transcribe_timeout: int = 600
    ):
        self.output_dir = Path(output_dir)
        self.view_id = view_id
        self.keep_audio = keep_audio
        self.verbose = verbose
        self.force_reprocess = force_reprocess
        self.transcribe_timeout = transcribe_timeout

        # Models
        self.transcribe_model = transcribe_model
        self.summary_model = summary_model
        self.topic_model = topic_model

        # First clip ID for auto-processing (from environment)
        self.first_clip_id = int(os.getenv("FIRST_CLIP_ID", "6669"))

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

    def clip_url(self, clip_id: int) -> str:
        """Generate Granicus clip URL"""
        return f"https://lfucg.granicus.com/player/clip/{clip_id}?view_id={self.view_id}&redirect=true"

    def agenda_url(self, clip_id: int) -> str:
        """Generate Granicus agenda PDF URL"""
        return f"https://lfucg.granicus.com/AgendaViewer.php?view_id={self.view_id}&clip_id={clip_id}"

    def minutes_url(self, clip_id: int) -> str:
        """Generate Granicus minutes URL"""
        return f"https://lfucg.granicus.com/MinutesViewer.php?view_id={self.view_id}&clip_id={clip_id}"

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

    def get_clip_title(self, clip_id: int) -> Optional[str]:
        """Get the original title for a clip using yt-dlp"""
        url = self.clip_url(clip_id)

        try:
            cmd = [
                "yt-dlp",
                "--print", "title",
                "--no-download",
                url
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0 and result.stdout.strip():
                title = result.stdout.strip()
                self.progress(f"Got title: {title}")
                return title
            else:
                self.log(f"Could not get title for clip {clip_id}", "WARNING")
                return None

        except subprocess.TimeoutExpired:
            self.log(f"Timeout getting title for clip {clip_id}", "WARNING")
            return None
        except Exception as e:
            self.log(f"Error getting title: {e}", "WARNING")
            return None

    def scrape_available_clips(self) -> List[int]:
        """Scrape all available clip IDs from Granicus viewer page"""
        url = f"https://lfucg.granicus.com/ViewPublisher.php?view_id={self.view_id}"

        self.log(f"Scraping clips from {url}")

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'lxml')
            clip_ids = set()

            # Find links with clip_id= parameter
            for link in soup.find_all('a', href=True):
                href = link['href']
                match = re.search(r'clip_id=(\d+)', href)
                if match:
                    clip_ids.add(int(match.group(1)))

            # Look for clip references in JavaScript/data
            for script in soup.find_all('script'):
                if script.string:
                    matches = re.findall(r'clip_id[=:](\d+)', script.string)
                    clip_ids.update(int(m) for m in matches)

            result = sorted(clip_ids)
            self.log(f"Found {len(result)} clips via scraping")
            if result:
                self.log(f"Range: {min(result)} to {max(result)}")

            return result

        except Exception as e:
            self.log(f"Error scraping: {e}", "ERROR")
            return []

    def download_audio(self, clip_id: int, clip_dir: Path, title: Optional[str] = None, date: Optional[str] = None) -> Optional[str]:
        """Download audio using yt-dlp with progress. Returns the audio filename or None on failure."""
        url = self.clip_url(clip_id)

        # Determine filename from title and date
        if title:
            sanitized_title = self.sanitize_filename(title)
            if date:
                audio_filename = f"{date}_{sanitized_title}_audio.mp3"
            else:
                audio_filename = f"{sanitized_title}_audio.mp3"
        else:
            if date:
                audio_filename = f"{date}_clip_{clip_id}_audio.mp3"
            else:
                audio_filename = f"clip_{clip_id}_audio.mp3"

        output_path = clip_dir / audio_filename

        # Check if file already exists
        if output_path.exists() and output_path.stat().st_size > 0 and not self.force_reprocess:
            size_mb = output_path.stat().st_size / (1024 * 1024)
            self.progress(f"Audio already exists ({size_mb:.2f} MB) - skipping download")
            return audio_filename

        # Also check for any existing mp3 file in directory (handles renamed files)
        existing_mp3s = list(clip_dir.glob("*.mp3"))
        existing_mp3s = [f for f in existing_mp3s if not f.name.endswith("_compressed.mp3")]
        if existing_mp3s and not self.force_reprocess:
            existing = existing_mp3s[0]
            size_mb = existing.stat().st_size / (1024 * 1024)
            self.progress(f"Audio already exists as {existing.name} ({size_mb:.2f} MB) - skipping download")
            return existing.name

        self.log(f"Downloading clip {clip_id} from {url}")

        try:
            # Use yt-dlp with progress display
            cmd = [
                "yt-dlp",
                "--progress",
                "--newline",  # Progress on new lines for better parsing
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "48k",  # Download at 48kbps - lower quality but smaller
                "--postprocessor-args", "ffmpeg:-ar 22050 -ac 1",  # 22kHz mono
                "-o", str(output_path),
                url
            ]

            # Run with real-time output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Display progress
            for line in process.stdout:
                line = line.strip()
                if line:
                    # Show download progress lines
                    if '[download]' in line or '[ExtractAudio]' in line:
                        # Clean up progress line for display
                        clean_line = line.replace('[download]', '').replace('[ExtractAudio]', '').strip()
                        if clean_line:
                            print(f"  {clean_line}", end='\r', flush=True)

            print()  # New line after progress
            process.wait()

            if process.returncode != 0:
                self.log("Download failed", "ERROR")
                return None

            if output_path.exists() and output_path.stat().st_size > 0:
                size_mb = output_path.stat().st_size / (1024 * 1024)
                self.progress(f"Downloaded {size_mb:.2f} MB as {audio_filename}")
                return audio_filename
            else:
                self.log("Download produced empty file", "ERROR")
                return None

        except Exception as e:
            self.log(f"Download error: {e}", "ERROR")
            return None

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

    def split_audio_into_chunks(self, audio_path: Path, max_size_mb: float = 24) -> List[Path]:
        """Split audio file into chunks that fit within size limit."""
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)

        # Calculate number of chunks needed (with some buffer)
        num_chunks = int(file_size_mb / max_size_mb) + 1

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
                "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k",
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

    def transcribe_audio(self, audio_path: Path, transcript_path: Path) -> Optional[str]:
        """Transcribe audio using OpenAI Whisper API"""

        # Check if transcript already exists
        if transcript_path.exists() and transcript_path.stat().st_size > 50 and not self.force_reprocess:
            self.progress("Transcript already exists - loading from file")
            with open(transcript_path, 'r', encoding='utf-8') as f:
                text = f.read().strip()
                if text:
                    word_count = len(text.split())
                    self.progress(f"Loaded transcript: {len(text)} chars, ~{word_count} words")
                    return text

        self.log(f"Transcribing audio with {self.transcribe_model}")

        try:
            file_size_mb = audio_path.stat().st_size / (1024 * 1024)

            # Whisper API has 25MB limit
            MAX_SIZE_MB = 24  # Leave some headroom
            ABSOLUTE_MAX_MB = 25  # Hard limit - must chunk if over this after compression

            transcribe_file = audio_path
            cleanup_files = []

            if file_size_mb > MAX_SIZE_MB:
                self.progress(f"Audio is {file_size_mb:.2f} MB (limit: 25 MB)")

                # Create compressed version
                compressed_path = audio_path.parent / f"{audio_path.stem}_compressed.mp3"

                if not self.compress_audio(audio_path, compressed_path):
                    self.log("Failed to compress audio", "ERROR")
                    return None

                transcribe_file = compressed_path
                cleanup_files.append(compressed_path)
                file_size_mb = compressed_path.stat().st_size / (1024 * 1024)

                # If still too large, try more aggressive compression
                if file_size_mb > MAX_SIZE_MB:
                    self.progress(f"Still {file_size_mb:.2f} MB, trying aggressive compression...")
                    aggressive_path = audio_path.parent / f"{audio_path.stem}_compressed_aggr.mp3"

                    cmd = [
                        "ffmpeg",
                        "-i", str(audio_path),
                        "-vn",
                        "-ar", "16000",
                        "-ac", "1",
                        "-b:a", "24k",  # Very low bitrate
                        "-y",
                        str(aggressive_path)
                    ]

                    subprocess.run(cmd, check=True, capture_output=True)

                    if aggressive_path.exists():
                        transcribe_file = aggressive_path
                        cleanup_files.append(aggressive_path)
                        file_size_mb = aggressive_path.stat().st_size / (1024 * 1024)
                        self.progress(f"Aggressively compressed to {file_size_mb:.2f} MB")

            # If STILL over the limit after compression, split into chunks
            if file_size_mb > ABSOLUTE_MAX_MB:
                self.progress(f"File still {file_size_mb:.2f} MB after compression - splitting into chunks")

                chunk_paths = self.split_audio_into_chunks(transcribe_file, max_size_mb=MAX_SIZE_MB)

                if not chunk_paths:
                    self.log("Failed to split audio into chunks", "ERROR")
                    for f in cleanup_files:
                        if f.exists():
                            f.unlink()
                    return None

                # Transcribe each chunk
                transcripts = []
                for i, chunk_path in enumerate(chunk_paths):
                    self.progress(f"Transcribing chunk {i+1}/{len(chunk_paths)}...")

                    try:
                        with open(chunk_path, "rb") as audio_file:
                            chunk_transcript = self.client.audio.transcriptions.create(
                                model=self.transcribe_model,
                                file=audio_file,
                                response_format="text"
                            )
                        if chunk_transcript:
                            transcripts.append(chunk_transcript.strip() if isinstance(chunk_transcript, str) else chunk_transcript)
                            self.progress(f"Chunk {i+1} transcribed: {len(transcripts[-1])} chars")
                    except Exception as e:
                        self.log(f"Error transcribing chunk {i+1}: {e}", "WARNING")
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

                return text

            # Normal single-file transcription
            self.progress(f"Uploading {file_size_mb:.2f} MB to OpenAI (timeout: {self.transcribe_timeout}s)...")
            self.progress(f"File: {transcribe_file.name}")
            upload_start = datetime.now()

            try:
                with open(transcribe_file, "rb") as audio_file:
                    transcript = self.client.audio.transcriptions.create(
                        model=self.transcribe_model,
                        file=audio_file,
                        response_format="text"
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

            text = transcript.strip() if isinstance(transcript, str) else transcript

            if text and len(text) > 50:
                word_count = len(text.split())
                self.progress(f"Transcribed {len(text)} chars, ~{word_count} words")

                # Save transcript immediately
                with open(transcript_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                self.progress(f"Saved transcript to {transcript_path.name}")

                return text
            else:
                self.log("Transcription too short or empty", "ERROR")
                return None

        except Exception as e:
            self.log(f"Transcription error: {type(e).__name__}: {e}", "ERROR")
            return None

    def download_agenda(self, clip_id: int, clip_dir: Path, title: Optional[str] = None, date: Optional[str] = None) -> Dict[str, Any]:
        """Download PDF agenda and extract text. Returns dict with pdf_file, txt_file, and text content."""
        result = {"pdf_file": None, "txt_file": None, "text": None}

        # Build filename with date prefix and title
        if title:
            sanitized_title = self.sanitize_filename(title)
            if date:
                pdf_filename = f"{date}_agenda_{sanitized_title}.pdf"
                txt_filename = f"{date}_agenda_{sanitized_title}.txt"
            else:
                pdf_filename = f"agenda_{sanitized_title}.pdf"
                txt_filename = f"agenda_{sanitized_title}.txt"
        else:
            if date:
                pdf_filename = f"{date}_agenda_{clip_id}.pdf"
                txt_filename = f"{date}_agenda_{clip_id}.txt"
            else:
                pdf_filename = f"agenda_{clip_id}.pdf"
                txt_filename = f"agenda_{clip_id}.txt"

        pdf_path = clip_dir / pdf_filename
        txt_path = clip_dir / txt_filename

        # Check if already downloaded (also check for old naming convention)
        existing_txt = list(clip_dir.glob("*agenda*.txt"))
        if existing_txt and not self.force_reprocess:
            txt_path = existing_txt[0]
            txt_filename = txt_path.name
            self.progress("Agenda text already exists - loading from file")
            with open(txt_path, 'r', encoding='utf-8') as f:
                result["text"] = f.read()
            # Find matching PDF
            existing_pdf = list(clip_dir.glob("*agenda*.pdf"))
            result["pdf_file"] = existing_pdf[0].name if existing_pdf else None
            result["txt_file"] = txt_filename
            return result

        url = self.agenda_url(clip_id)
        self.log(f"Downloading agenda from {url}")

        try:
            response = requests.get(url, timeout=30, allow_redirects=True)

            # Check if we got a PDF (content-type or magic bytes)
            content_type = response.headers.get('content-type', '')
            is_pdf = 'pdf' in content_type.lower() or response.content[:4] == b'%PDF'

            if not is_pdf:
                self.progress("No PDF agenda available for this clip")
                return result

            # Save PDF
            with open(pdf_path, 'wb') as f:
                f.write(response.content)
            result["pdf_file"] = pdf_filename
            self.progress(f"Downloaded agenda PDF ({len(response.content) / 1024:.1f} KB)")

            # Extract text with pdfplumber first
            try:
                text_parts = []
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)

                if text_parts:
                    agenda_text = "\n\n".join(text_parts)
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(agenda_text)
                    result["txt_file"] = txt_filename
                    result["text"] = agenda_text
                    self.progress(f"Extracted {len(agenda_text)} chars from agenda PDF")
                else:
                    # PDF has no extractable text - try OCR
                    self.progress("PDF appears to be scanned, attempting OCR...")
                    agenda_text = self.ocr_pdf(pdf_path)
                    if agenda_text:
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(agenda_text)
                        result["txt_file"] = txt_filename
                        result["text"] = agenda_text
                        self.progress(f"OCR extracted {len(agenda_text)} chars from agenda PDF")
                    else:
                        self.progress("OCR could not extract text from agenda PDF")

            except Exception as e:
                self.log(f"PDF text extraction error: {e}", "WARNING")

        except Exception as e:
            self.log(f"Agenda download error: {e}", "WARNING")

        return result

    def ocr_pdf(self, pdf_path: Path, max_pages: int = 5) -> Optional[str]:
        """Extract text from scanned PDF using OCR. Only processes first max_pages to save time."""
        try:
            # Convert PDF pages to images
            images = convert_from_path(pdf_path, first_page=1, last_page=max_pages)

            if not images:
                return None

            text_parts = []
            for i, image in enumerate(images):
                self.progress(f"OCR processing page {i + 1}/{len(images)}...")
                # Run OCR on the image
                page_text = pytesseract.image_to_string(image)
                if page_text and page_text.strip():
                    text_parts.append(page_text.strip())

            if text_parts:
                return "\n\n".join(text_parts)
            return None

        except Exception as e:
            self.log(f"OCR error: {e}", "WARNING")
            return None

    def download_minutes(self, clip_id: int, clip_dir: Path, title: Optional[str] = None, date: Optional[str] = None) -> Dict[str, Any]:
        """Download meeting minutes and extract text. Returns dict with file info and text content."""
        result = {"pdf_file": None, "html_file": None, "txt_file": None, "text": None}

        # Build filename with date prefix and title
        if title:
            sanitized_title = self.sanitize_filename(title)
            if date:
                base_filename = f"{date}_minutes_{sanitized_title}"
            else:
                base_filename = f"minutes_{sanitized_title}"
        else:
            if date:
                base_filename = f"{date}_minutes_{clip_id}"
            else:
                base_filename = f"minutes_{clip_id}"

        txt_filename = f"{base_filename}.txt"
        txt_path = clip_dir / txt_filename

        # Check if already downloaded (also check for old naming convention)
        existing_txt = list(clip_dir.glob("*minutes*.txt"))
        if existing_txt and not self.force_reprocess:
            txt_path = existing_txt[0]
            txt_filename = txt_path.name
            self.progress("Minutes text already exists - loading from file")
            with open(txt_path, 'r', encoding='utf-8') as f:
                result["text"] = f.read()
            result["txt_file"] = txt_filename
            # Check for original files
            existing_pdf = list(clip_dir.glob("*minutes*.pdf"))
            existing_html = list(clip_dir.glob("*minutes*.html"))
            if existing_pdf:
                result["pdf_file"] = existing_pdf[0].name
            if existing_html:
                result["html_file"] = existing_html[0].name
            return result

        url = self.minutes_url(clip_id)
        self.log(f"Checking for minutes at {url}")

        try:
            response = requests.get(url, timeout=30, allow_redirects=True)

            # Check content type
            content_type = response.headers.get('content-type', '').lower()

            # Check if we got actual content (not an error page)
            if response.status_code != 200:
                self.progress("No minutes available for this clip")
                return result

            # Handle PDF minutes
            if 'pdf' in content_type or response.content[:4] == b'%PDF':
                pdf_filename = f"{base_filename}.pdf"
                pdf_path = clip_dir / pdf_filename

                with open(pdf_path, 'wb') as f:
                    f.write(response.content)
                result["pdf_file"] = pdf_filename
                self.progress(f"Downloaded minutes PDF ({len(response.content) / 1024:.1f} KB)")

                # Extract text from PDF
                try:
                    text_parts = []
                    with pdfplumber.open(pdf_path) as pdf:
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                text_parts.append(page_text)

                    if text_parts:
                        minutes_text = "\n\n".join(text_parts)
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(minutes_text)
                        result["txt_file"] = txt_filename
                        result["text"] = minutes_text
                        self.progress(f"Extracted {len(minutes_text)} chars from minutes PDF")
                    else:
                        # Try OCR for scanned PDFs
                        self.progress("Minutes PDF appears scanned, attempting OCR...")
                        minutes_text = self.ocr_pdf(pdf_path)
                        if minutes_text:
                            with open(txt_path, 'w', encoding='utf-8') as f:
                                f.write(minutes_text)
                            result["txt_file"] = txt_filename
                            result["text"] = minutes_text
                            self.progress(f"OCR extracted {len(minutes_text)} chars from minutes PDF")

                except Exception as e:
                    self.log(f"Minutes PDF text extraction error: {e}", "WARNING")

            # Handle HTML minutes
            elif 'html' in content_type:
                html_content = response.text

                # Check if it's an error page or empty
                if 'no minutes' in html_content.lower() or len(html_content) < 500:
                    self.progress("No minutes available for this clip")
                    return result

                html_filename = f"{base_filename}.html"
                html_path = clip_dir / html_filename

                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                result["html_file"] = html_filename

                # Extract text from HTML
                try:
                    soup = BeautifulSoup(html_content, 'lxml')

                    # Remove script and style elements
                    for element in soup(['script', 'style', 'nav', 'header', 'footer']):
                        element.decompose()

                    # Get text content
                    minutes_text = soup.get_text(separator='\n', strip=True)

                    if minutes_text and len(minutes_text) > 100:
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(minutes_text)
                        result["txt_file"] = txt_filename
                        result["text"] = minutes_text
                        self.progress(f"Extracted {len(minutes_text)} chars from minutes HTML")

                except Exception as e:
                    self.log(f"Minutes HTML text extraction error: {e}", "WARNING")

            else:
                self.progress("No minutes available for this clip (unexpected content type)")

        except requests.exceptions.RequestException as e:
            self.progress(f"Minutes not available: {e}")
        except Exception as e:
            self.log(f"Minutes download error: {e}", "WARNING")

        return result

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
                    else:
                        # Numeric pattern
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

        # Extract meeting body - common abbreviations and names (search title first)
        body_patterns = [
            r'\b(WQFB|CAC|LFUCG|Council|Commission|Board|Committee)\b',
            r'(Work Session|Regular Session|Special Session|Budget Hearing)',
        ]

        search_text = title or ""
        for pattern in body_patterns:
            match = re.search(pattern, search_text, re.IGNORECASE)
            if match:
                metadata["meeting_body"] = match.group(1)
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

## Notable Quotes
Include 3-5 significant or memorable quotes that capture key moments (with speaker attribution).

## Implications for Residents
Brief analysis: How might decisions made in this meeting affect Lexington residents?

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

    def extract_topics(self, transcript: str) -> List[str]:
        """Extract 3-8 high-level topics from the transcript using AI."""
        self.log(f"Extracting topics with {self.topic_model}")

        # Use a sample of the transcript if it's very long
        sample = transcript[:15000] if len(transcript) > 15000 else transcript

        prompt = """Analyze this government meeting transcript and extract 3-8 high-level topics discussed.

Return ONLY a JSON array of topic strings. Topics should be:
- Concise (2-4 words each)
- Descriptive of the actual content discussed
- Capitalized properly

Example output: ["Budget Approval", "Zoning Amendment", "Public Safety", "Infrastructure Updates"]

Transcript:
"""

        try:
            response = self.client.chat.completions.create(
                model=self.topic_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You extract topics from meeting transcripts. Return only valid JSON arrays."
                    },
                    {
                        "role": "user",
                        "content": f"{prompt}\n{sample}"
                    }
                ],
                temperature=0.2,
                max_tokens=200
            )

            result = response.choices[0].message.content.strip()

            # Parse JSON array
            # Handle potential markdown code blocks
            if result.startswith("```"):
                result = re.sub(r'```\w*\n?', '', result).strip()

            topics = json.loads(result)

            if isinstance(topics, list) and all(isinstance(t, str) for t in topics):
                self.progress(f"Extracted topics: {topics}")
                return topics[:8]  # Limit to 8 topics
            else:
                self.log("Invalid topics format returned", "WARNING")
                return []

        except json.JSONDecodeError as e:
            self.log(f"Failed to parse topics JSON: {e}", "WARNING")
            return []
        except Exception as e:
            self.log(f"Topic extraction error: {e}", "WARNING")
            return []

    def _convert_inline_markdown(self, text: str) -> str:
        """Convert inline markdown (bold, italic) to HTML tags."""
        # Convert **bold** to <strong>bold</strong>
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # Convert *italic* to <em>italic</em> (but not if already processed as bold)
        text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', text)
        return text

    def summary_to_html(self, summary_text: str, title: str, summary_html_path: Path) -> bool:
        """Convert summary text to HTML with proper structure."""
        try:
            # Basic HTML template
            html_parts = [
                '<!DOCTYPE html>',
                '<html lang="en">',
                '<head>',
                '<meta charset="UTF-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
                f'<title>{title}</title>',
                '<style>',
                'body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }',
                'h1 { color: #1a365d; border-bottom: 2px solid #2c5282; padding-bottom: 10px; }',
                'h2 { color: #2c5282; margin-top: 30px; }',
                'ul { margin: 10px 0; }',
                'li { margin: 5px 0; }',
                'blockquote { border-left: 4px solid #cbd5e0; margin: 15px 0; padding-left: 15px; color: #4a5568; font-style: italic; }',
                '</style>',
                '</head>',
                '<body>',
                f'<h1>{title}</h1>',
            ]

            # Convert markdown-style summary to HTML
            lines = summary_text.split('\n')
            in_list = False

            for line in lines:
                line = line.strip()
                if not line:
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    continue

                # Handle headers
                if line.startswith('## '):
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    content = self._convert_inline_markdown(line[3:])
                    html_parts.append(f'<h2>{content}</h2>')
                elif line.startswith('# '):
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    content = self._convert_inline_markdown(line[2:])
                    html_parts.append(f'<h2>{content}</h2>')
                # Handle bullet points
                elif line.startswith('- ') or line.startswith('* '):
                    if not in_list:
                        html_parts.append('<ul>')
                        in_list = True
                    content = self._convert_inline_markdown(line[2:])
                    html_parts.append(f'<li>{content}</li>')
                # Handle quotes
                elif line.startswith('>'):
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    content = self._convert_inline_markdown(line[1:].strip())
                    html_parts.append(f'<blockquote>{content}</blockquote>')
                # Regular paragraph
                else:
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    content = self._convert_inline_markdown(line)
                    html_parts.append(f'<p>{content}</p>')

            if in_list:
                html_parts.append('</ul>')

            html_parts.extend(['</body>', '</html>'])

            html_content = '\n'.join(html_parts)

            with open(summary_html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            self.progress(f"Saved HTML summary to {summary_html_path.name}")
            return True

        except Exception as e:
            self.log(f"HTML conversion error: {e}", "ERROR")
            return False

    def generate_search_index(self) -> Optional[Path]:
        """Generate index.json with all processed clips for frontend search."""
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

                entry = {
                    "clip_id": metadata.get("clip_id"),
                    "date": metadata.get("date"),
                    "meeting_body": metadata.get("meeting_body"),
                    "title": metadata.get("title"),
                    "topics": metadata.get("topics", []),
                    "transcript_words": metadata.get("transcript_words", 0),
                    "transcript_preview": transcript_preview,
                    "processed_at": metadata.get("processed_at"),
                    "files": metadata.get("files", {})
                }

                index_entries.append(entry)
                self.progress(f"Indexed clip {metadata.get('clip_id')}")

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

            # Download minutes (force re-download to get latest)
            minutes_result = self.download_minutes(clip_id, clip_dir, title=title, date=meeting_date)
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

            # Regenerate HTML
            title = metadata.get("title", f"Clip {clip_id}")
            summary_html_path = clip_dir / "summary.html"
            if self.summary_to_html(summary, title, summary_html_path):
                metadata["files"]["summary_html"] = "summary.html"

            # Update metadata
            end_time = datetime.now()
            metadata["summary_updated_at"] = end_time.isoformat()
            metadata["models"]["summary"] = self.summary_model

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

    def process_clip(
            self,
            clip_id: int,
            skip_if_exists: bool = True
    ) -> bool:
        """Process a single clip through the entire pipeline"""

        clip_dir = self.output_dir / "clips" / str(clip_id)
        metadata_path = clip_dir / "metadata.json"

        # Check if fully processed
        if skip_if_exists and not self.force_reprocess and metadata_path.exists():
            # Check if summary exists (our new completion marker)
            summary_html = clip_dir / "summary.html"
            if summary_html.exists():
                self.log(f"Clip {clip_id} fully processed - skipping (use --force to reprocess)")
                return True

        clip_dir.mkdir(parents=True, exist_ok=True)
        start_time = datetime.now()

        # Track files created for metadata
        files = {}

        try:
            self.log(f"\n{'=' * 60}")
            self.log(f"Processing clip {clip_id}")
            self.log(f"{'=' * 60}")

            # Step 1: Get clip title
            title = self.get_clip_title(clip_id)
            if not title:
                title = f"Clip {clip_id}"

            # Step 2: Extract date from title for filename prefixes
            clip_metadata = self.scrape_clip_metadata(clip_id, title)
            meeting_date = clip_metadata.get("date")  # ISO format: YYYY-MM-DD
            if meeting_date:
                self.progress(f"Extracted date: {meeting_date}")

            # Step 3: Download audio with date prefix
            audio_filename = self.download_audio(clip_id, clip_dir, title, date=meeting_date)
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

            # Step 4: Determine transcript filename
            audio_stem = Path(audio_filename).stem
            transcript_filename = f"transcript_{audio_stem}.txt"
            transcript_path = clip_dir / transcript_filename

            # Step 5: Transcribe audio
            transcript = self.transcribe_audio(audio_path, transcript_path)
            if not transcript:
                self.state["failed_clips"].append({
                    "clip_id": clip_id,
                    "reason": "transcription_failed",
                    "timestamp": datetime.now().isoformat()
                })
                self.state["last_processed_clip_id"] = clip_id
                self.save_state()
                return False
            files["transcript"] = transcript_filename

            # Step 6: Download and extract agenda (optional - don't fail if unavailable)
            agenda_result = self.download_agenda(clip_id, clip_dir, title=title, date=meeting_date)
            if agenda_result["pdf_file"]:
                files["agenda_pdf"] = agenda_result["pdf_file"]
            if agenda_result["txt_file"]:
                files["agenda_txt"] = agenda_result["txt_file"]

            # Step 6b: Download and extract minutes (optional - don't fail if unavailable)
            minutes_result = self.download_minutes(clip_id, clip_dir, title=title, date=meeting_date)
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

            # Step 8: Extract topics from transcript
            topics = self.extract_topics(transcript)

            # Step 9: Generate summary (with agenda and minutes context)
            summary_txt_path = clip_dir / "summary.txt"
            summary = self.generate_summary(
                clip_id,
                transcript,
                agenda_result.get("text"),
                summary_txt_path,
                minutes_text=minutes_result.get("text")
            )
            if not summary:
                self.state["failed_clips"].append({
                    "clip_id": clip_id,
                    "reason": "summary_generation_failed",
                    "timestamp": datetime.now().isoformat()
                })
                self.state["last_processed_clip_id"] = clip_id
                self.save_state()
                return False
            files["summary_txt"] = "summary.txt"

            # Step 9: Convert summary to HTML
            summary_html_path = clip_dir / "summary.html"
            if self.summary_to_html(summary, title, summary_html_path):
                files["summary_html"] = "summary.html"

            # Remove audio if not keeping
            if not self.keep_audio and audio_path.exists():
                audio_path.unlink()
                del files["audio"]
                self.progress("Removed audio file (keep_audio=False)")

            # Step 10: Save enhanced metadata
            end_time = datetime.now()
            metadata = {
                "clip_id": clip_id,
                "url": self.clip_url(clip_id),
                "date": clip_metadata.get("date"),
                "meeting_body": clip_metadata.get("meeting_body"),
                "title": title,
                "topics": topics,
                "files": files,
                "processed_at": end_time.isoformat(),
                "processing_time_seconds": (end_time - start_time).total_seconds(),
                "transcript_words": len(transcript.split()),
                "audio_kept": self.keep_audio,
                "models": {
                    "transcribe": self.transcribe_model,
                    "summary": self.summary_model,
                    "topics": self.topic_model
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

            # Regenerate search index after each successful clip
            self.generate_search_index()

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

    def auto_process(self, max_clips: int = 10) -> dict:
        """Auto-process clips starting from last processed + 1 or FIRST_CLIP_ID.

        If available_clips.json exists, only processes clips from that list.
        """
        available_clips = self.load_available_clips()
        processed_set = set(self.state.get("processed_clips", []))
        last_id = self.state["last_processed_clip_id"]

        if available_clips:
            # Filter to unprocessed clips after last_processed_clip_id
            candidates = [c for c in available_clips if c > last_id and c not in processed_set]
            clips_to_process = candidates[:max_clips]

            if not clips_to_process:
                self.log("No more clips to process from available_clips.json")
                return {"processed": [], "failed": [], "skipped": []}

            self.log(f"Auto-processing {len(clips_to_process)} clips from available_clips.json")
            self.log(f"Clips: {clips_to_process[0]} to {clips_to_process[-1]}")

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
            # Fallback: sequential processing without available_clips.json
            if last_id == 0:
                start_id = self.first_clip_id
            else:
                start_id = last_id + 1

            end_id = start_id + max_clips - 1

            self.log(f"Auto-processing from clip {start_id} (max {max_clips} clips)")
            self.log("Tip: Run probe_clips.py to create available_clips.json for smarter processing")

            return self.process_range(start_id, end_id, stop_on_failure=False)


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
  %(prog)s --scrape                      # Scrape and process all new clips
  %(prog)s --generate-index              # Generate search index from all clips
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
        "--max",
        type=int,
        default=10,
        help="Maximum clips to process in auto/scrape mode (default: 10)"
    )

    parser.add_argument(
        "--output-dir",
        default="./lfucg_output",
        help="Output directory (default: ./lfucg_output)"
    )

    parser.add_argument(
        "--view-id",
        default="14",
        help="Granicus view ID (default: 14)"
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
        "--topic-model",
        default="gpt-4o-mini",
        help="OpenAI topic extraction model (default: gpt-4o-mini)"
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

    args = parser.parse_args()

    # Initialize pipeline
    try:
        pipeline = LFUCGPipeline(
            output_dir=args.output_dir,
            view_id=args.view_id,
            transcribe_model=args.transcribe_model,
            summary_model=args.summary_model,
            topic_model=args.topic_model,
            keep_audio=not args.no_audio,
            verbose=not args.quiet,
            force_reprocess=args.force,
            transcribe_timeout=args.transcribe_timeout
        )
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Handle generate-index mode
    if args.generate_index:
        index_path = pipeline.generate_search_index()
        if index_path:
            print(f"Generated search index: {index_path}")
        else:
            print("Failed to generate search index")
            sys.exit(1)
        sys.exit(0)

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
        available_clips = pipeline.scrape_available_clips()
        if not available_clips:
            print("No clips found via scraping")
            sys.exit(1)

        # Process new clips
        new_clips = [
            c for c in available_clips
            if c > pipeline.state["last_processed_clip_id"]
        ][:args.max]

        if not new_clips:
            print("No new clips to process")
            sys.exit(0)

        print(f"\nProcessing {len(new_clips)} new clips: {new_clips[0]} to {new_clips[-1]}")
        results = pipeline.process_range(new_clips[0], new_clips[-1])

    elif args.auto:
        # Auto mode
        results = pipeline.auto_process(args.max)

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