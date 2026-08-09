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

# A single unpublished range in the archive is routinely larger than the old
# 5-ID budget (Granicus interleaves clip ids across bodies/views, and whole
# blocks of ids are never used). Stopping after 5 consecutive misses — and
# then REWINDING the resume point to the last FOUND clip — made every rerun
# restart inside the same gap and wedge forever (real prod symptom). Keep a
# much larger budget AND persist a monotonic high-water mark (see run_scan).
MAX_CONSECUTIVE_ABSENT = 25

# Substrings that mark a transient network / tooling failure rather than a
# genuine "this clip does not exist" (404). Network errors must NOT count
# toward the end-of-archive gap budget, must NOT advance the resume point
# past the id, and must NOT be mistaken for the end of the archive.
_NETWORK_ERROR_SIGNS = (
    "temporary failure in name resolution",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "unable to connect",
    "getaddrinfo",
    "network is unreachable",
    "read timed out",
    "http error 5",  # any 5xx is a transient server-side error
    "ssl",
    "max retries",
    "remote end closed",
)


def _looks_like_network_error(text: str) -> bool:
    t = (text or "").lower()
    return any(sign in t for sign in _NETWORK_ERROR_SIGNS)


def probe_clip(clip_id: int) -> dict:
    """Check whether a clip exists without downloading it.

    Returns a dict with a ``status`` of:
      - ``"found"``  — clip exists (``title`` present)
      - ``"absent"`` — genuine 404 / no such clip (counts toward the gap budget)
      - ``"error"``  — transient network/tool failure (does NOT count as a gap)
    """
    url = f"https://{GRANICUS_HOST}/player/clip/{clip_id}?view_id={VIEW_ID}&redirect=true"

    try:
        result = subprocess.run(
            ["yt-dlp", "--no-download", "--print", "title", url],
            capture_output=True,
            text=True,
            timeout=30
        )
    except subprocess.TimeoutExpired:
        # A timeout is a network/tool stall, not proof the clip is missing.
        return {"status": "error", "clip_id": clip_id, "error": "timeout"}
    except FileNotFoundError as e:
        # yt-dlp not on PATH — a tooling error, definitely not a 404.
        return {"status": "error", "clip_id": clip_id, "error": f"tool_missing:{e}"}
    except Exception as e:  # pragma: no cover - defensive
        return {"status": "error", "clip_id": clip_id, "error": str(e)}

    if result.returncode == 0 and result.stdout.strip():
        title = result.stdout.strip()
        # Filter out error messages that might come through on stdout
        if "ERROR" not in title and "Unable" not in title:
            return {"status": "found", "clip_id": clip_id, "title": title}

    # Non-zero exit (or no usable title). Distinguish a real missing clip from
    # a transient network/server hiccup so the latter doesn't look like the
    # end of the archive.
    diag = f"{result.stderr or ''}\n{result.stdout or ''}"
    if _looks_like_network_error(diag):
        return {"status": "error", "clip_id": clip_id, "error": diag.strip()[:200]}
    return {"status": "absent", "clip_id": clip_id}


def run_scan(
    start: int,
    end: int,
    available: list,
    last_probed: int,
    output_file: Path,
    probe=probe_clip,
    save=None,
    save_every: int = 50,
) -> tuple[list, int]:
    """Walk clip ids ``start..end`` and classify each.

    ``last_probed`` is a MONOTONIC high-water mark of the furthest id that has
    been fully resolved (found or genuinely absent). It is what gets persisted
    as the resume point, so a later run never rewinds into an already-scanned
    gap. A transient ``"error"`` stops the scan WITHOUT advancing past the id
    (so the next run retries it) and WITHOUT counting toward the gap budget.

    Returns ``(available, last_probed)``.
    """
    if save is None:
        save = save_progress

    found_count = len(available)
    consecutive_absent = 0

    for clip_id in range(start, end + 1):
        res = probe(clip_id)
        status = res.get("status")

        if status == "error":
            # Do NOT advance last_probed and do NOT touch the gap budget — the
            # id is unresolved. Stop; the next run resumes here and retries it.
            print(
                f"[{clip_id}] network/tool error: {res.get('error')} — "
                f"stopping (resume will retry this id)"
            )
            break

        # found / absent => the id is resolved; the resume point may advance.
        last_probed = clip_id

        if status == "found":
            available.append({"clip_id": clip_id, "title": res.get("title")})
            found_count += 1
            consecutive_absent = 0
            print(f"[{clip_id}] FOUND: {res.get('title')}")
        else:  # absent
            consecutive_absent += 1
            if clip_id % 100 == 0:
                print(f"[{clip_id}] ... ({found_count} found so far)")
            if consecutive_absent >= MAX_CONSECUTIVE_ABSENT:
                print(
                    f"\nHit {MAX_CONSECUTIVE_ABSENT} consecutive absent clips at "
                    f"clip {clip_id}. Assuming end of archive."
                )
                break

        if save_every and clip_id % save_every == 0:
            save(output_file, available, last_probed)

    save(output_file, available, last_probed)
    return available, last_probed


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
    last_probed = 0
    if output_file.exists():
        data = json.loads(output_file.read_text())
        available = data.get("clips", [])
        # last_checked is the persisted high-water mark (resume point). Older
        # files rewound it to the last FOUND clip; fall back to the max known
        # id so a legacy file still resumes forward rather than re-scanning.
        last_probed = max(
            data.get("last_checked", 0),
            max((c["clip_id"] for c in available), default=0),
        )
        print(f"Resuming from clip {last_probed + 1}, found {len(available)} so far")

    # Parse args
    start = int(sys.argv[1]) if len(sys.argv) > 1 else max(1, last_probed + 1)
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 7000

    print(f"Probing clips {start} to {end}...")

    try:
        available, last_probed = run_scan(
            start, end, available, last_probed, output_file
        )
    except KeyboardInterrupt:
        print("\nInterrupted! Saving progress...")
        save_progress(output_file, available, last_probed)

    print(f"\nDone! Found {len(available)} available clips")
    print(f"Resume point (last_checked) saved as: {last_probed}")
    print(f"Saved to {output_file}")


def save_progress(output_file: Path, available: list, last_checked: int):
    """Save current progress to file.

    ``last_checked`` is the monotonic high-water mark (resume point), NOT the
    last found clip — persisting the last found id is exactly what wedged the
    scanner.
    """
    # Sort by clip_id and dedupe (a retried id could otherwise appear twice).
    by_id = {}
    for c in available:
        by_id[c["clip_id"]] = c
    available_sorted = sorted(by_id.values(), key=lambda x: x["clip_id"])

    data = {
        "last_checked": last_checked,
        "last_updated": datetime.now().isoformat(),
        "total_found": len(available_sorted),
        "clips": available_sorted
    }

    output_file.write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
