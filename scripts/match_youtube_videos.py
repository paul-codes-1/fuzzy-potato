#!/usr/bin/env python3
"""Stamp each processed clip with the YouTube URL of its meeting video.

Document-driven jurisdictions (e.g. Paris on CivicClerk) have no in-band video,
but the city still posts meetings to a public YouTube channel. The pipeline box
can't DOWNLOAD/caption from YouTube (datacenter-IP bot-block), but it CAN still
LIST a channel — so this tool lists ``cfg.source_youtube_channel_url``, parses
the meeting date out of each video title, and writes a per-clip ``video_url``
into ``metadata.json``. The SPA then links the meeting page to the real video.

Matching is by DATE + meeting KIND (regular / workshop / special / budget /
joint): a date can carry several meetings (a regular + a workshop), and the
channel may only post one — so a "Regular Commission Meeting" video is NEVER
attached to a same-day workshop clip. Unmatched clips simply get no video_url
(the SPA hides the video section for those).

Idempotent: only rewrites metadata.json when video_url actually changes.

Usage:
    uv run python scripts/match_youtube_videos.py            # apply
    uv run python scripts/match_youtube_videos.py --dry-run  # report only
    uv run python scripts/match_youtube_videos.py --channel-url <url>
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
# Common abbreviations.
_MONTHS.update({"sept": 9})
for _full, _i in list(_MONTHS.items()):
    _MONTHS[_full[:3]] = _i

# "April 14, 2026" / "March 9th, 2021" / "February 24, 2026" — the format the
# City of Paris channel uses for its regular-meeting uploads.
_LONG_DATE_RE = re.compile(
    r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)
# Numeric fallbacks: MMDDYYYY (8 digits) — e.g. "12102024" -> 2024-12-10.
_MMDDYYYY_RE = re.compile(r"\b(0[1-9]|1[0-2])([0-2]\d|3[01])(20\d{2})\b")

_KIND_KEYWORDS = (
    ("workshop", "workshop"),
    ("work session", "workshop"),
    ("joint", "joint"),
    ("budget", "budget"),
    ("special", "special"),
    ("public hearing", "special"),
    ("called", "special"),
)


def parse_title_date(title: str) -> Optional[str]:
    """Best-effort ISO (YYYY-MM-DD) date parsed from a video title, or None."""
    if not title:
        return None
    m = _LONG_DATE_RE.search(title)
    if m:
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(2))).isoformat()
            except ValueError:
                pass
    m = _MMDDYYYY_RE.search(title)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            pass
    return None


def classify_kind(text: str) -> str:
    """Coarse meeting kind from a title/body string. Defaults to 'regular'."""
    t = (text or "").lower()
    for needle, kind in _KIND_KEYWORDS:
        if needle in t:
            return kind
    return "regular"


def match_clip_to_video(clip_meta: dict, videos_by_date: dict) -> Optional[str]:
    """Pick the best YouTube video id for a clip, or None.

    ``videos_by_date`` maps ISO date -> list of (video_id, title). A clip matches
    a video on the same date whose KIND agrees (so a regular-meeting upload is
    never attached to a same-day workshop clip). With a single same-day,
    same-kind candidate it wins; ties fall back to title token overlap.
    """
    d = clip_meta.get("date")
    if not d:
        return None
    candidates = videos_by_date.get(d) or []
    if not candidates:
        return None
    clip_kind = classify_kind(
        f"{clip_meta.get('title', '')} {clip_meta.get('meeting_body', '')}"
    )
    same_kind = [(vid, title) for (vid, title) in candidates if classify_kind(title) == clip_kind]
    if len(same_kind) == 1:
        return same_kind[0][0]
    pool = same_kind or candidates
    if len(pool) == 1 and same_kind:
        return pool[0][0]
    if not pool:
        return None
    # Tie-break by title-token overlap with the clip title.
    clip_tokens = set(re.findall(r"[a-z]{3,}", (clip_meta.get("title") or "").lower()))
    best, best_score = None, -1
    for vid, title in pool:
        score = len(clip_tokens & set(re.findall(r"[a-z]{3,}", title.lower())))
        if score > best_score:
            best, best_score = vid, score
    # Only accept a tie-break win when kinds agreed; otherwise it's too risky.
    return best if same_kind else None


def list_channel_videos(channel_url: str, timeout: int = 90) -> list[tuple[str, str]]:
    """List (video_id, title) for a channel's /videos and /streams tabs via yt-dlp."""
    base = channel_url.rstrip("/")
    seen: dict[str, str] = {}
    for tab in ("videos", "streams"):
        url = f"{base}/{tab}"
        try:
            out = subprocess.run(
                ["yt-dlp", "--flat-playlist", "--no-warnings",
                 "--print", "%(id)s\t%(title)s", url],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        for line in out.stdout.splitlines():
            if "\t" not in line:
                continue
            vid, title = line.split("\t", 1)
            if vid and vid not in seen:
                seen[vid] = title
    return list(seen.items())


def build_videos_by_date(videos: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    by_date: dict[str, list[tuple[str, str]]] = {}
    for vid, title in videos:
        d = parse_title_date(title)
        if d:
            by_date.setdefault(d, []).append((vid, title))
    return by_date


def run(output_dir: Path, channel_url: str, dry_run: bool, log=print) -> dict:
    videos = list_channel_videos(channel_url)
    by_date = build_videos_by_date(videos)
    log(f"Listed {len(videos)} channel videos, {len(by_date)} distinct dated.")

    clips_dir = output_dir / "clips"
    matched, cleared, unchanged, unmatched = 0, 0, 0, 0
    for meta_path in sorted(clips_dir.glob("*/metadata.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid = match_clip_to_video(meta, by_date)
        new_url = f"https://www.youtube.com/watch?v={vid}" if vid else None
        old_url = meta.get("video_url")
        cid = meta_path.parent.name
        if new_url == old_url:
            unchanged += 1
            continue
        if new_url:
            log(f"  clip {cid} ({meta.get('date')}) -> {new_url}")
            matched += 1
        else:
            log(f"  clip {cid} ({meta.get('date')}) -> (no match, clearing)")
            cleared += 1
        if not dry_run:
            if new_url:
                meta["video_url"] = new_url
            else:
                meta.pop("video_url", None)
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    unmatched = sum(
        1 for p in clips_dir.glob("*/metadata.json")
        if not match_clip_to_video(json.loads(p.read_text(encoding="utf-8")), by_date)
    )
    log(f"Done: {matched} matched, {cleared} cleared, {unchanged} unchanged, "
        f"{unmatched} clips without a video.")
    return {"matched": matched, "cleared": cleared, "unchanged": unchanged, "unmatched": unmatched}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", default=None, help="defaults to cfg.output_dir")
    ap.add_argument("--channel-url", default=None, help="override cfg.source_youtube_channel_url")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from config import get_config

    cfg = get_config()
    channel_url = args.channel_url or cfg.source_youtube_channel_url
    if not channel_url:
        print("No YouTube channel_url configured for this jurisdiction — nothing to do.")
        return 0
    output_dir = Path(args.output_dir or cfg.output_dir)
    run(output_dir, channel_url, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
