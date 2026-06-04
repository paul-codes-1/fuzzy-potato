#!/usr/bin/env python3
"""Probe all clip IDs to find available clips without downloading."""

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

from config import get_config

load_dotenv()

_CFG = get_config()
GRANICUS_HOST = _CFG.granicus_host
# Read the listing view from config instead of hard-coding 14 — main.py
# already parameterized this, so a non-14 jurisdiction would otherwise be
# silently mis-probed here.
VIEW_ID = _CFG.default_view_id


def probe_clip(clip_id: int) -> dict | None:
    """Check if a clip exists and get its title without downloading."""
    url = f"https://{GRANICUS_HOST}/player/clip/{clip_id}?view_id={VIEW_ID}&redirect=true"

    try:
        result = subprocess.run(
            ["yt-dlp", "--no-download", "--print", "title", url],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0 and result.stdout.strip():
            title = result.stdout.strip()
            # Filter out error messages that might come through
            if "ERROR" not in title and "Unable" not in title:
                return {"clip_id": clip_id, "title": title}
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    return None


def main():
    # probe_clips.py is Granicus-only: it walks a sequential integer clip-id
    # range against the configured Granicus host. For non-Granicus sources
    # (e.g. YouTube), there is no int range to probe — enumeration happens in
    # the pipeline (auto_process -> source.list_meetings -> available_clips.json).
    # Bail out rather than probing the DEFAULTED lfucg.granicus.com host and
    # writing garbage over a YouTube county's available_clips.json.
    if _CFG.source_type != "granicus":
        print(
            f"probe_clips: source_type={_CFG.source_type} not granicus — "
            "enumeration happens in the pipeline (auto_process); skipping."
        )
        return

    output_file = Path(_CFG.output_dir) / "available_clips.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing progress if any
    available = []
    last_checked = 0
    if output_file.exists():
        data = json.loads(output_file.read_text())
        available = data.get("clips", [])
        last_checked = data.get("last_checked", 0)
        print(f"Resuming from clip {last_checked + 1}, found {len(available)} so far")

    # Parse args
    start = int(sys.argv[1]) if len(sys.argv) > 1 else max(1, last_checked + 1)
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 7000

    print(f"Probing clips {start} to {end}...")

    found_count = len(available)
    consecutive_failures = 0
    last_found = max((c["clip_id"] for c in available), default=0)
    MAX_CONSECUTIVE_FAILURES = 5
    clip_id = start - 1

    try:
        for clip_id in range(start, end + 1):
            result = probe_clip(clip_id)

            if result:
                available.append(result)
                found_count += 1
                consecutive_failures = 0
                last_found = clip_id
                print(f"[{clip_id}] FOUND: {result['title']}")
            else:
                consecutive_failures += 1
                if clip_id % 100 == 0:
                    print(f"[{clip_id}] ... ({found_count} found so far)")

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\nHit {MAX_CONSECUTIVE_FAILURES} consecutive 404s at clip {clip_id}. Stopping.")
                    break

            # Save progress every 50 clips
            if clip_id % 50 == 0:
                save_progress(output_file, available, last_found)

    except KeyboardInterrupt:
        print("\nInterrupted! Saving progress...")

    save_progress(output_file, available, last_found)
    print(f"\nDone! Found {len(available)} available clips")
    print(f"last_checked reset to last found clip: {last_found}")
    print(f"Saved to {output_file}")


def save_progress(output_file: Path, available: list, last_checked: int):
    """Save current progress to file."""
    # Sort by clip_id
    available_sorted = sorted(available, key=lambda x: x["clip_id"])

    data = {
        "last_checked": last_checked,
        "last_updated": datetime.now().isoformat(),
        "total_found": len(available_sorted),
        "clips": available_sorted
    }

    output_file.write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
