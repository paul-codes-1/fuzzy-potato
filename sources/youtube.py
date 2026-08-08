"""YouTubeSource — a ``VideoSource`` adapter for jurisdictions that publish
their meetings to a YouTube channel instead of running Granicus.

This is the WS3 unlock (see ``deploy/lightsail/COUNTY2_ENGINEERING_SCOPE.md``
§WS3 and ``MULTI_COUNTY_EXPANSION_SPEC.md`` §0+§1): no Bluegrass neighbor of
Lexington runs Granicus, but most post meetings to YouTube, so a YouTube
ingest path is what makes county #2 possible.

THE CORE DESIGN — synthetic clip-id map (Option A from the scope)
================================================================
The whole pipeline assumes a **monotonic integer ``clip_id``**
(``state.json.last_processed_clip_id``, ``clips/<id>/`` dirs, the
``search.db`` ``clip_id`` column, ``--auto``/range CLI). YouTube has only
string ``videoId``s enumerated from a channel — there is no integer range to
probe. So ``YouTubeSource`` maintains a **persistent map** ``videoId → local
int clip_id`` at ``<output_dir>/source_ids.json`` (alongside ``state.json``).
New videos get the next sequential int (seeded at ``start_id`` — default 1,
configurable via ``[source.youtube] start_id`` — continuing from the current
max). Every ``VideoSource`` method that takes an
int ``clip_id`` reverse-maps int→videoId internally. This confines all
YouTube-ness to the adapter + the map; the integer-id machinery everywhere
else is unchanged.

``source_ids.json`` schema (chosen here)::

    {
      "version": 1,
      "videos": {
        "<videoId>": {"clip_id": <int>, "title": "<str|null>", "date": "<YYYY-MM-DD|null>"},
        ...
      }
    }

The map caches ``title``/``date`` so ``get_clip_title`` / ``canonical_url`` /
``download_audio`` can reverse-map int→videoId (and fetch a cached title)
without re-enumerating the channel. ``date``/``title`` are refreshed when
``list_meetings`` re-runs and learns a value it didn't have.

Everything DOWNSTREAM of the source stays portal-agnostic. In particular,
YouTube auto-caption WebVTT has no ``>> Speaker:`` turns, so
``granicus_captions.py`` degrades gracefully to a no-speaker transcript —
that's acceptable (see scope §"Speaker labels"); this module does NOT modify
``granicus_captions.py``.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .base import MeetingRef
from .granicus import (
    DOWNLOAD_STALL_TIMEOUT,
    POSTPROCESS_MARKERS,
    POSTPROCESS_STALL_TIMEOUT,
)


class YouTubeSource:
    """YouTube-channel adapter implementing the ``VideoSource`` protocol.

    Reads its channel URL from ``cfg.source_youtube_channel_url`` (populated
    from the TOML's ``[source.youtube]`` ``channel_url``). Mirrors
    ``GranicusSource``'s settable attributes (``view_id``,
    ``force_reprocess``, ``progress``) so ``main.py``'s ``make_source`` + the
    sync block work unchanged — ``view_id`` is meaningless for YouTube but is
    accepted and ignored so the pipeline's ``self.source.view_id = ...`` line
    doesn't break.
    """

    def __init__(self, cfg, log: Callable[..., None]):
        self.cfg = cfg
        self.log = log
        self.progress: Callable[[str], None] = lambda msg: None

        # Channel/playlist URL the pipeline enumerates. Empty string is a
        # mis-config; methods that need it log and degrade rather than crash.
        self.channel_url: str = getattr(cfg, "source_youtube_channel_url", "") or ""

        # Optional per-jurisdiction title→date regex escape hatch (scope risk
        # #3). Unused by the core path (we trust yt-dlp's upload_date) but
        # carried so a county with messy titles can override later.
        self.title_date_pattern: str = getattr(cfg, "source_youtube_title_date_pattern", "") or ""

        # Seed for the synthetic integer id space — the first id we hand out
        # when the map is empty. Defaults to 1 (NOT cfg.first_clip_id, which is
        # Granicus's portal-specific 6669 and meaningless for YouTube).
        # Configurable via [source.youtube] start_id.
        self.start_id: int = int(getattr(cfg, "source_youtube_start_id", 1))

        # Mirror GranicusSource's settable attributes (the pipeline syncs
        # these on). view_id is inert for YouTube but must exist so
        # main.py's `self.source.view_id = self.view_id` doesn't AttributeError.
        self.view_id = str(getattr(cfg, "default_view_id", ""))
        self.force_reprocess = False

        # Persistent videoId → int clip_id map (alongside state.json).
        output_dir = Path(getattr(cfg, "output_dir", "./lfucg_output"))
        self.id_map_path = output_dir / "source_ids.json"
        # Internal store: {videoId: {"clip_id": int, "title": str|None, "date": str|None}}
        self._videos: Dict[str, Dict[str, Any]] = {}
        self._load_id_map()

    # ------------------------------------------------------------------
    # Synthetic id map (videoId <-> int clip_id) persistence
    # ------------------------------------------------------------------
    def _load_id_map(self) -> None:
        """Load source_ids.json if present; tolerate absence/corruption."""
        if not self.id_map_path.exists():
            self._videos = {}
            return
        try:
            with self.id_map_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            videos = raw.get("videos", {}) if isinstance(raw, dict) else {}
            # Normalize: clip_id must be an int; title/date optional.
            self._videos = {}
            for vid, entry in videos.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    cid = int(entry["clip_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                self._videos[str(vid)] = {
                    "clip_id": cid,
                    "title": entry.get("title"),
                    "date": entry.get("date"),
                }
        except Exception as e:  # pragma: no cover - defensive
            self.log(f"Could not read {self.id_map_path.name}: {e}", "WARNING")
            self._videos = {}

    def _save_id_map(self) -> None:
        """Atomically persist the videoId → clip_id map."""
        self.id_map_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "videos": self._videos}
        tmp = self.id_map_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        tmp.replace(self.id_map_path)

    def _next_clip_id(self) -> int:
        """The next sequential int id to hand out.

        Seeded at ``self.start_id`` (default 1); thereafter ``max(existing) +
        1`` so ids are stable and monotonic across runs (never reused, never
        reassigned).
        """
        if not self._videos:
            return self.start_id
        current_max = max(v["clip_id"] for v in self._videos.values())
        return max(current_max + 1, self.start_id)

    def _video_id_for(self, clip_id: int) -> Optional[str]:
        """Reverse-map an int clip_id back to its YouTube videoId."""
        cid = int(clip_id)
        for vid, entry in self._videos.items():
            if entry["clip_id"] == cid:
                return vid
        return None

    def _watch_url(self, video_id: str) -> str:
        return f"https://www.youtube.com/watch?v={video_id}"

    @staticmethod
    def _iso_date_from_upload(upload_date: Optional[str]) -> Optional[str]:
        """yt-dlp ``upload_date`` is YYYYMMDD; convert to ISO YYYY-MM-DD."""
        if not upload_date:
            return None
        s = str(upload_date).strip()
        if re.fullmatch(r"\d{8}", s):
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        # Already ISO (some yt-dlp paths emit it) or unparseable.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s
        return None

    # ------------------------------------------------------------------
    # Filename helper — IDENTICAL to GranicusSource.sanitize_filename so the
    # audio file the pipeline's process_clip looks for matches byte-for-byte.
    # ------------------------------------------------------------------
    def sanitize_filename(self, title: str) -> str:
        """Sanitize title for use as filename (matches GranicusSource)."""
        sanitized = re.sub(r'\s*\(\s*\d+\s*\)\s*$', '', title)
        sanitized = re.sub(r'[\s\-]+', '_', sanitized)
        sanitized = re.sub(r'[^\w.]', '', sanitized)
        sanitized = sanitized.strip('_')
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized or "clip"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def list_meetings(self) -> List[MeetingRef]:
        """Enumerate the channel via ``yt-dlp --flat-playlist
        --dump-single-json`` and return ``MeetingRef``s with assigned int ids.

        New videos (not already in the map) get the next sequential int id;
        the map is persisted. Returned refs are sorted oldest-first by date so
        id assignment is chronological-ish.
        """
        if not self.channel_url:
            self.log("YouTubeSource: no channel_url configured", "WARNING")
            return []

        entries = self._enumerate_channel()
        if not entries:
            return []

        # Parse each entry into (videoId, title, iso_date).
        parsed: List[Dict[str, Any]] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            vid = e.get("id")
            if not vid:
                continue
            title = e.get("title")
            iso_date = self._iso_date_from_upload(e.get("upload_date"))
            parsed.append({"id": str(vid), "title": title, "date": iso_date})

        # Sort oldest-first so the first-seen ordering (and therefore the int
        # id assignment) is chronological-ish. Videos with no known date sort
        # last (empty string < any real date would put them first, so use a
        # high sentinel to push undated ones to the end).
        parsed.sort(key=lambda p: (p["date"] or "9999-99-99", p["id"]))

        changed = False
        for p in parsed:
            vid = p["id"]
            existing = self._videos.get(vid)
            if existing is None:
                # New video — assign the next int id.
                self._videos[vid] = {
                    "clip_id": self._next_clip_id(),
                    "title": p["title"],
                    "date": p["date"],
                }
                changed = True
            else:
                # Known video — refresh title/date if we learned a value, but
                # NEVER reassign clip_id (id stability is the whole point).
                if p["title"] and existing.get("title") != p["title"]:
                    existing["title"] = p["title"]
                    changed = True
                if p["date"] and existing.get("date") != p["date"]:
                    existing["date"] = p["date"]
                    changed = True

        if changed:
            self._save_id_map()

        refs: List[MeetingRef] = []
        for p in parsed:
            entry = self._videos[p["id"]]
            refs.append(
                MeetingRef(
                    clip_id=str(entry["clip_id"]),
                    title=entry.get("title"),
                    date=entry.get("date"),
                    body=None,
                )
            )
        return refs

    def _enumerate_channel(self) -> List[Dict[str, Any]]:
        """Run ``yt-dlp --flat-playlist --dump-single-json`` and return the
        ``entries`` list. Isolated for easy mocking in tests."""
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            self.channel_url,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            self.log("Timeout enumerating YouTube channel", "WARNING")
            return []
        except Exception as e:
            self.log(f"Error enumerating YouTube channel: {e}", "ERROR")
            return []

        if result.returncode != 0 or not result.stdout.strip():
            self.log(
                f"yt-dlp channel enumeration failed (rc={result.returncode})",
                "WARNING",
            )
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.log(f"Could not parse yt-dlp JSON: {e}", "WARNING")
            return []

        entries = data.get("entries") if isinstance(data, dict) else None
        return entries or []

    def scrape_available_clips(self) -> List[int]:
        """Return the int ids (sorted), via ``list_meetings``."""
        refs = self.list_meetings()
        return sorted(int(r.clip_id) for r in refs)

    def get_clip_title(self, clip_id: int) -> Optional[str]:
        """Reverse-map → videoId → cached title (or ``yt-dlp --print title``)."""
        video_id = self._video_id_for(clip_id)
        if video_id is None:
            self.log(f"No videoId mapped for clip {clip_id}", "WARNING")
            return None

        cached = self._videos[video_id].get("title")
        if cached:
            return cached

        # Fall back to a live yt-dlp title fetch on the watch URL.
        try:
            cmd = ["yt-dlp", "--print", "title", "--no-download", self._watch_url(video_id)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                title = result.stdout.strip()
                self._videos[video_id]["title"] = title
                self._save_id_map()
                self.progress(f"Got title: {title}")
                return title
            self.log(f"Could not get title for clip {clip_id}", "WARNING")
            return None
        except subprocess.TimeoutExpired:
            self.log(f"Timeout getting title for clip {clip_id}", "WARNING")
            return None
        except Exception as e:
            self.log(f"Error getting title: {e}", "WARNING")
            return None

    def get_metadata(self, ref: MeetingRef) -> Dict[str, Any]:
        """Per-clip metadata — the authoritative date (from yt-dlp
        ``upload_date``) + title. The body-taxonomy parse stays in main.py.

        Uses the cached map first; falls back to a single
        ``yt-dlp --print`` call for the video's upload_date/title when the
        map doesn't carry them.
        """
        video_id = self._video_id_for(int(ref.clip_id))
        if video_id is None:
            return {"date": None, "title": None}

        entry = self._videos[video_id]
        date = entry.get("date")
        title = entry.get("title")
        if date and title:
            return {"date": date, "title": title}

        # Targeted single-video metadata fetch for missing fields.
        try:
            cmd = [
                "yt-dlp",
                "--print", "%(upload_date)s\t%(title)s",
                "--no-download",
                self._watch_url(video_id),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            # A non-zero rc / blank stdout here is the deleted-/unavailable-
            # /members-only-video case (yt-dlp prints "This video is not
            # available"): we leave date/title as whatever the map already
            # has (often None) and degrade gracefully rather than crash.
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split("\t", 1)
                fetched_date = self._iso_date_from_upload(parts[0]) if parts else None
                fetched_title = parts[1] if len(parts) > 1 else None
                if fetched_date:
                    entry["date"] = date = fetched_date
                if fetched_title:
                    entry["title"] = title = fetched_title
                self._save_id_map()
        except Exception as e:  # pragma: no cover - network/env dependent
            self.log(f"Error fetching YouTube metadata for clip {ref.clip_id}: {e}", "WARNING")

        return {"date": date, "title": title}

    # ------------------------------------------------------------------
    # URL builders / citations
    # ------------------------------------------------------------------
    def canonical_url(self, clip_id: int) -> str:
        """Reverse-map → the public watch permalink used for citations.

        Timestamp deep-links (``&t=<sec>s``) are layered on by callers later;
        the base watch URL is the canonical permalink.
        """
        video_id = self._video_id_for(clip_id)
        if video_id is None:
            # Last-ditch: emit a search-shaped URL so citations don't crash.
            self.log(f"canonical_url: no videoId for clip {clip_id}", "WARNING")
            return "https://www.youtube.com/"
        return self._watch_url(video_id)

    # ------------------------------------------------------------------
    # Media + documents
    # ------------------------------------------------------------------
    def download_audio(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Optional[str]:
        """Download meeting audio via ``yt-dlp -x --audio-format mp3``.

        Mirrors GranicusSource's audio flags/quality + filename convention
        (same ``sanitize_filename`` + ``{date}_{title}_audio.mp3`` pattern) so
        downstream transcription/filename lookup in ``process_clip`` behaves
        identically.
        """
        video_id = self._video_id_for(clip_id)
        if video_id is None:
            self.log(f"download_audio: no videoId for clip {clip_id}", "ERROR")
            return None
        url = self._watch_url(video_id)

        # Filename: identical convention to GranicusSource.download_audio.
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

        # Reuse an existing download unless --force (same as GranicusSource).
        if output_path.exists() and output_path.stat().st_size > 0 and not self.force_reprocess:
            size_mb = output_path.stat().st_size / (1024 * 1024)
            self.progress(f"Audio already exists ({size_mb:.2f} MB) - skipping download")
            return audio_filename

        existing_mp3s = list(clip_dir.glob("*.mp3"))
        existing_mp3s = [f for f in existing_mp3s if not f.name.endswith("_compressed.mp3")]
        if existing_mp3s and not self.force_reprocess:
            existing = existing_mp3s[0]
            size_mb = existing.stat().st_size / (1024 * 1024)
            self.progress(f"Audio already exists as {existing.name} ({size_mb:.2f} MB) - skipping download")
            return existing.name

        self.log(f"Downloading clip {clip_id} from {url}")

        try:
            # Same audio flags/quality as GranicusSource so downstream
            # transcription behaves identically (48kbps, 22kHz mono mp3).
            cmd = [
                "yt-dlp",
                "--progress",
                "--newline",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "48k",
                "--postprocessor-args", "ffmpeg:-ar 22050 -ac 1",
                "-o", str(output_path),
                url,
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )

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
                    if '%' in line and ('[download]' in line or 'ETA' in line):
                        clean_line = line.replace('[download]', '').strip()
                        print(f"\r  {clean_line:<80}", end='', flush=True)
                    else:
                        print(f"\n  {line}", end='', flush=True)
                eof_reached.set()

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()

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
            print()

            if timed_out:
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

    def fetch_captions(self, clip_id: int, clip_dir: Path) -> Optional[Path]:
        """Fetch YouTube auto-captions as WebVTT into ``clip_dir/captions.vtt``.

        Cached: re-uses an existing file unless ``--force``. YouTube auto-CC
        VTT has no ``>> Speaker:`` turns; ``granicus_captions.py`` degrades to
        a no-speaker transcript, which is acceptable (scope §"Speaker labels").
        Returns the path on success or ``None`` when no captions exist.
        """
        vtt_path = clip_dir / "captions.vtt"
        if vtt_path.exists() and not self.force_reprocess:
            return vtt_path

        video_id = self._video_id_for(clip_id)
        if video_id is None:
            self.log(f"fetch_captions: no videoId for clip {clip_id}", "WARNING")
            return None

        self.progress(f"Fetching auto-captions for clip {clip_id}")
        # NOTE: granicus_captions.download_vtt only pulls --write-subs (manual
        # captions). YouTube meetings almost always carry only AUTO captions,
        # so we run our own yt-dlp here with both --write-auto-subs AND
        # --write-subs so an English VTT is produced whether the channel
        # uploaded manual captions or relies on auto-generated ones (per scope
        # §WS3). We do NOT modify granicus_captions.py.
        result = self._download_vtt_with_auto(self._watch_url(video_id), vtt_path)
        if result:
            self.progress(f"Saved captions to {vtt_path.name}")
        else:
            self.progress("No auto-captions track available")
        return result

    def _download_vtt_with_auto(self, url: str, out_path: Path) -> Optional[Path]:
        """Pull English VTT (auto OR manual) via yt-dlp into ``out_path``.

        yt-dlp writes ``<template>.en.vtt`` so we rename it into place. Returns
        ``out_path`` on success or ``None`` when no captions exist / yt-dlp
        fails. Isolated for mocking in tests.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        template = str(out_path.with_suffix(""))
        cmd = [
            "yt-dlp",
            "--write-auto-subs",
            "--write-subs",
            "--sub-langs", "en",
            "--sub-format", "vtt",
            "--skip-download",
            "--quiet",
            "-o", f"{template}.%(ext)s",
            url,
        ]
        try:
            subprocess.run(cmd, timeout=120, check=True, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None
        # yt-dlp may emit en.vtt / en-orig.vtt / en-US.vtt depending on track.
        # We just take the first alphabetically — note this is NOT a
        # manual-vs-auto preference (e.g. `en-orig` sorts before `en` in
        # practice); any English VTT is acceptable for the downstream parser.
        candidates = sorted(out_path.parent.glob(f"{out_path.stem}.en*.vtt"))
        if not candidates:
            return None
        candidates[0].rename(out_path)
        # Clean up any extra language variants left behind.
        for extra in candidates[1:]:
            try:
                extra.unlink()
            except OSError:  # pragma: no cover - defensive
                pass
        return out_path

    def download_agenda(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """YouTube has no in-band agenda (WS4 supplies it separately).

        Returns the same empty-shaped dict GranicusSource returns when absent.
        """
        return {"pdf_file": None, "txt_file": None, "text": None}

    def download_minutes(
        self,
        clip_id: int,
        clip_dir: Path,
        title: Optional[str] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """YouTube has no in-band minutes (WS4 supplies it separately).

        Returns the same empty-shaped dict GranicusSource returns when absent.
        """
        return {"pdf_file": None, "html_file": None, "txt_file": None, "text": None}
