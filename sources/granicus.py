"""GranicusSource — the Granicus-portal implementation of ``VideoSource``.

This is a behavior-preserving lift of the Granicus-specific methods that
used to live on ``LFUCGPipeline`` (``clip_url`` / ``agenda_url`` /
``minutes_url``, ``get_clip_title``, ``scrape_available_clips``,
``fetch_date_from_listing``, ``download_audio`` / ``download_agenda`` /
``download_minutes``, and the VTT ``fetch_captions``). The pipeline now
delegates to ``self.source`` so a future portal (YouTube, etc.) is a new
class, not a fork.

The class reads its jurisdiction config (``config.get_config()`` — passed in
as ``cfg``) for the Granicus host, default view, and listing-view fallbacks.
``view_id``, ``force_reprocess``, and the ``progress`` printer are settable
attributes so the pipeline can keep them in sync with its own CLI flags
(``--force``, ``--view-id``, ``--quiet``) without changing observable
behavior. The defaults reproduce the historical values exactly.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from documents import extract_html_text, extract_pdf_text, sniff_office_kind, extract_office_text
from granicus_captions import download_vtt

from .base import MeetingRef

# Stall watchdog tuning for the yt-dlp subprocess (see download_audio).
# During the download phase yt-dlp streams progress lines, so a short quiet
# window means a genuinely stalled HLS connection. Its ffmpeg post-processing
# phases ([ExtractAudio] / [Fixup*] / [Merger]) are SILENT for minutes on
# GB-scale clips — killing on the download timeout there is what permanently
# failed clips 6804/6816/6832 (June-July 2026) even after their videos
# appeared. Once a post-processor line is seen, allow a much longer quiet
# window. Module-level so tests can shrink them.
DOWNLOAD_STALL_TIMEOUT = 30
POSTPROCESS_STALL_TIMEOUT = 1800
POSTPROCESS_MARKERS = ("[ExtractAudio]", "[Fixup", "[Merger]")


class GranicusSource:
    """Granicus portal adapter implementing the ``VideoSource`` protocol."""

    def __init__(self, cfg, log: Callable[..., None]):
        self.cfg = cfg
        # log(msg, level="INFO"); progress(msg). Both default to no-ops if a
        # caller doesn't wire them, but the pipeline always passes its own.
        self.log = log
        self.progress: Callable[[str], None] = lambda msg: None

        # Granicus host (per-jurisdiction; env GRANICUS_HOST already folded
        # into cfg by config.get_config()).
        self.granicus_host = cfg.granicus_host

        # Active listing view. Defaults to the jurisdiction's default view;
        # the pipeline may override (it accepts a --view-id). Stored as a
        # string to match the historical URL formatting exactly.
        self.view_id = str(cfg.default_view_id)

        # Views scanned (in order) when the default view doesn't contain a
        # clip — non-council bodies live on their own views. Most-trafficked
        # first. Mirrors LFUCGPipeline.LISTING_VIEW_FALLBACKS.
        self.LISTING_VIEW_FALLBACKS: tuple[int, ...] = tuple(cfg.listing_view_fallbacks)

        # Whether to re-download / re-fetch even when a cached file exists.
        # The pipeline keeps this in sync with its --force flag.
        self.force_reprocess = False

        # When True, download the SMALLEST rendition that still carries audio
        # (`bestaudio/worst`) instead of yt-dlp's default "best". The audio is
        # always downsampled to 48kbps mono mp3 downstream, so a clip's video
        # resolution never affects the transcript — for HD clips the default
        # otherwise pulls a ~1GB video just to throw the pixels away. Left off
        # by default to preserve the established Whisper/Lambda download path;
        # the pipeline flips it on for the ElevenLabs Scribe backfill wave.
        self.prefer_small_audio_format = False

        # Parallel HLS fragment count for downloads. Granicus throttles a
        # single HLS connection to ~1 MB/s, so pulling fragments in parallel
        # is a ~5x throughput win on every clip. This is INDEPENDENT of
        # prefer_small_audio_format (which only picks the rendition) — it used
        # to be gated behind that flag, so only the retired ElevenLabs path
        # ever got the speedup. 0/None disables it.
        self.hls_concurrent_fragments = 5

        # Process-lifetime cache of ViewPublisher listing pages, keyed by
        # view_id (str). fetch_date_from_listing is called once per clip in a
        # batch and each call otherwise refetched the same 1-3 large listing
        # pages, so a 20-clip batch hit Granicus 20-40x for identical HTML.
        self._listing_cache: Dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------
    # Filename helper (used by the download_* methods). Identical to the
    # pipeline's sanitize_filename so produced filenames are byte-stable.
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # URL builders
    # ------------------------------------------------------------------
    def clip_url(self, clip_id: int) -> str:
        """Generate Granicus clip URL"""
        return f"https://{self.granicus_host}/player/clip/{clip_id}?view_id={self.view_id}&redirect=true"

    def agenda_url(self, clip_id: int) -> str:
        """Generate Granicus agenda PDF URL"""
        return f"https://{self.granicus_host}/AgendaViewer.php?view_id={self.view_id}&clip_id={clip_id}"

    def minutes_url(self, clip_id: int) -> str:
        """Generate Granicus minutes URL"""
        return f"https://{self.granicus_host}/MinutesViewer.php?view_id={self.view_id}&clip_id={clip_id}"

    def canonical_url(self, clip_id: int) -> str:
        """Public source-video permalink used for citations (== clip_url)."""
        return self.clip_url(clip_id)

    # ------------------------------------------------------------------
    # Title / metadata
    # ------------------------------------------------------------------
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

    def get_metadata(self, ref: MeetingRef) -> Dict[str, Any]:
        """Per-clip portal metadata — the authoritative listing date.

        The body-taxonomy parse stays in the pipeline (config-driven). This
        returns ``{"date": <iso or None>}`` from the Granicus ViewPublisher
        listing lookup.
        """
        return {"date": self.fetch_date_from_listing(int(ref.clip_id))}

    # ------------------------------------------------------------------
    # Listing scrapes
    # ------------------------------------------------------------------
    def scrape_available_clips(self) -> List[int]:
        """Scrape all available clip IDs from Granicus viewer page"""
        url = f"https://{self.granicus_host}/ViewPublisher.php?view_id={self.view_id}"

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

    def list_meetings(self) -> List[MeetingRef]:
        """Enumerate available meetings as MeetingRefs (clip ids only)."""
        return [MeetingRef(clip_id=str(cid)) for cid in self.scrape_available_clips()]

    def _get_listing_html(self, view_id) -> Optional[str]:
        """Fetch a ViewPublisher listing page, memoized for the process lifetime.

        The listing HTML for a given view is stable across a batch, so we cache
        it (including a ``None`` for a failed fetch, to avoid hammering a broken
        view repeatedly). fetch_date_from_listing is called once per clip, so
        without this a 20-clip batch refetched the same 1-3 pages 20-40x.
        """
        key = str(view_id)
        if key in self._listing_cache:
            return self._listing_cache[key]
        url = f"https://{self.granicus_host}/ViewPublisher.php?view_id={key}"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            self.log(f"fetch_date_from_listing view_id={key} failed: {e}", "WARNING")
            html = None
        self._listing_cache[key] = html
        return html

    def fetch_date_from_listing(self, clip_id: int) -> Optional[str]:
        """Fetch Granicus ViewPublisher listings and extract the authoritative meeting date for clip_id.

        Tries `self.view_id` first, then LISTING_VIEW_FALLBACKS, since
        non-council bodies live on their own views (e.g. clip 6770 —
        Mayor's Task Force to End Homelessness — is on view_id=9, not 14).

        Two date formats are recognized per row:
        - Hidden `<span style="display:none">unix_timestamp</span>` (view 14 archive layout).
        - Displayed text like "May&nbsp;13,&nbsp;2026" (view 9 / committee layouts).
        """
        month_map = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
        }

        # Normalize to str — self.view_id is a string ("14") while the
        # fallbacks are ints, so the dedup check never matched and the
        # default view was fetched twice.
        views_to_try: List[str] = [str(self.view_id)]
        for v in self.LISTING_VIEW_FALLBACKS:
            if str(v) not in views_to_try:
                views_to_try.append(str(v))

        for view_id in views_to_try:
            listing_html = self._get_listing_html(view_id)
            if listing_html is None:
                continue

            for row in listing_html.split("</tr>"):
                if f"clip_id={clip_id}" not in row:
                    continue
                # Preferred: hidden unix-timestamp span.
                m = re.search(r'<span\s+style="display:\s*none;\s*">\s*(\d{9,11})\s*</span>', row)
                if m:
                    try:
                        ts = int(m.group(1))
                        # Meeting-local timezone, NOT UTC — evening meetings
                        # cross midnight UTC and would get next-day dates.
                        return datetime.fromtimestamp(
                            ts, ZoneInfo("America/New_York")).date().isoformat()
                    except Exception:
                        pass
                # Fallback: displayed text. Granicus renders dates as
                # "May&nbsp;13,&nbsp;2026" so collapse whitespace and
                # &nbsp; before matching.
                row_text = re.sub(r"&nbsp;|\s+", " ", row)
                m = re.search(
                    r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b',
                    row_text,
                    re.IGNORECASE,
                )
                if m:
                    try:
                        return _date(
                            int(m.group(3)),
                            month_map[m.group(1).lower()],
                            int(m.group(2)),
                        ).isoformat()
                    except (ValueError, KeyError):
                        pass
                # Found the row but couldn't parse the date — no point
                # checking other views for this clip.
                return None
        return None

    # ------------------------------------------------------------------
    # Media + documents
    # ------------------------------------------------------------------
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

        # Also check for any existing mp3 file in directory (handles renamed
        # files). Exclude compression/chunking intermediates — a crashed
        # transcription run can leave *_chunk*.mp3 leftovers behind, and
        # treating one as the full audio would transcribe a fragment.
        existing_mp3s = list(clip_dir.glob("*.mp3"))
        existing_mp3s = [
            f for f in existing_mp3s
            if not f.name.endswith("_compressed.mp3") and "_chunk" not in f.name
        ]
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
            ]
            if self.prefer_small_audio_format:
                # Grab a standalone audio track if Granicus exposes one, else
                # the lowest-bitrate muxed rendition — avoids pulling a full
                # HD video just to extract 48kbps audio.
                cmd += ["-f", "bestaudio/worst"]
            # Parallel HLS fragments — a ~5x throughput win on Granicus's
            # single-connection ~1 MB/s throttle. Applied UNCONDITIONALLY (not
            # gated behind prefer_small_audio_format) so every clip benefits,
            # not just the retired Scribe path.
            if self.hls_concurrent_fragments:
                cmd += ["--concurrent-fragments", str(self.hls_concurrent_fragments)]
            cmd += ["-o", str(output_path), url]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0  # unbuffered bytes
            )

            # Read lines in a thread so the main thread can check for stalls
            last_output_time = [time.time()]
            postprocessing = [False]
            eof_reached = threading.Event()

            def read_output():
                while True:
                    raw_line = process.stdout.readline()
                    if not raw_line:
                        break
                    last_output_time[0] = time.time()
                    line = raw_line.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue
                    if line.startswith(POSTPROCESS_MARKERS):
                        postprocessing[0] = True
                    # Percentage progress: overwrite in place
                    if '%' in line and ('[download]' in line or 'ETA' in line):
                        clean_line = line.replace('[download]', '').strip()
                        # Pad to overwrite previous longer lines
                        print(f"\r  {clean_line:<80}", end='', flush=True)
                    else:
                        # Everything else: print on its own line
                        print(f"\n  {line}", end='', flush=True)
                eof_reached.set()

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()

            # Poll for stall; the allowed quiet window depends on phase
            timed_out = False
            while not eof_reached.is_set():
                eof_reached.wait(timeout=5)
                stall_limit = POSTPROCESS_STALL_TIMEOUT if postprocessing[0] else DOWNLOAD_STALL_TIMEOUT
                if not eof_reached.is_set() and time.time() - last_output_time[0] > stall_limit:
                    self.log(f"Download stalled (no output for {stall_limit}s) - skipping clip", "WARNING")
                    process.kill()
                    timed_out = True
                    break

            process.wait()
            reader.join(timeout=5)
            print()  # New line after progress

            if timed_out:
                # Clean up partial download
                if output_path.exists():
                    output_path.unlink()
                return None

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
                kind = sniff_office_kind(response.content)
                if kind:
                    doc_path = clip_dir / f"{txt_path.stem}.{kind}"
                    with open(doc_path, 'wb') as f:
                        f.write(response.content)
                    agenda_text = extract_office_text(doc_path, kind, log_fn=self.log)
                    if agenda_text:
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(agenda_text)
                        result["txt_file"] = txt_filename
                        result["text"] = agenda_text
                        self.progress(f"Extracted {len(agenda_text)} chars from agenda .{kind}")
                        return result
                self.progress("No PDF agenda available for this clip")
                return result

            # Save PDF
            with open(pdf_path, 'wb') as f:
                f.write(response.content)
            result["pdf_file"] = pdf_filename
            self.progress(f"Downloaded agenda PDF ({len(response.content) / 1024:.1f} KB)")

            agenda_text = extract_pdf_text(pdf_path, log_fn=self.log, progress_fn=self.progress)
            if agenda_text:
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(agenda_text)
                result["txt_file"] = txt_filename
                result["text"] = agenda_text
                self.progress(f"Extracted {len(agenda_text)} chars from agenda PDF")
            else:
                self.progress("Could not extract text from agenda PDF")

        except Exception as e:
            self.log(f"Agenda download error: {e}", "WARNING")

        return result

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

                minutes_text = extract_pdf_text(pdf_path, log_fn=self.log, progress_fn=self.progress)
                if minutes_text:
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(minutes_text)
                    result["txt_file"] = txt_filename
                    result["text"] = minutes_text
                    self.progress(f"Extracted {len(minutes_text)} chars from minutes PDF")

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

                try:
                    minutes_text = extract_html_text(html_content)
                    if minutes_text:
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(minutes_text)
                        result["txt_file"] = txt_filename
                        result["text"] = minutes_text
                        self.progress(f"Extracted {len(minutes_text)} chars from minutes HTML")
                except Exception as e:
                    self.log(f"Minutes HTML text extraction error: {e}", "WARNING")

            # Handle Word minutes (.docx 2024 PC-subdivision, legacy .doc 2007-2015)
            elif sniff_office_kind(response.content):
                kind = sniff_office_kind(response.content)
                doc_filename = f"{base_filename}.{kind}"
                doc_path = clip_dir / doc_filename
                with open(doc_path, 'wb') as f:
                    f.write(response.content)
                self.progress(f"Downloaded minutes .{kind} ({len(response.content) / 1024:.1f} KB)")
                minutes_text = extract_office_text(doc_path, kind, log_fn=self.log)
                if minutes_text:
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(minutes_text)
                    result["txt_file"] = txt_filename
                    result["text"] = minutes_text
                    self.progress(f"Extracted {len(minutes_text)} chars from minutes .{kind}")
                else:
                    self.progress(f"Could not extract text from minutes .{kind}")

            else:
                self.progress(f"No minutes available for this clip (unexpected content type: {content_type[:40]})")

        except requests.exceptions.RequestException as e:
            self.progress(f"Minutes not available: {e}")
        except Exception as e:
            self.log(f"Minutes download error: {e}", "WARNING")

        return result

    # ------------------------------------------------------------------
    # Captions (FETCH only — VTT parsing stays in granicus_captions.py /
    # the pipeline's apply_captions).
    # ------------------------------------------------------------------
    def fetch_captions(self, clip_id: int, clip_dir: Path) -> Optional[Path]:
        """Download Granicus VTT captions to clip_dir/captions.vtt.

        Cached: re-uses an existing file unless --force. Returns the path
        on success or None when no captions track exists.
        """
        vtt_path = clip_dir / "captions.vtt"
        if vtt_path.exists() and not self.force_reprocess:
            return vtt_path
        self.progress(f"Fetching closed-captions for clip {clip_id}")
        result = download_vtt(self.clip_url(clip_id), vtt_path)
        if result:
            self.progress(f"Saved captions to {vtt_path.name}")
        else:
            self.progress("No closed-captions track available")
        return result
