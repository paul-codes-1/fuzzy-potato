#!/usr/bin/env python3
"""Probe clip IDs to find available clips without downloading.

Probing is plain HTTP against the Granicus player page (status code +
``<title>``), NOT yt-dlp extraction. yt-dlp's generic extractor broke against
the redesigned Granicus player (2026-08: "[html5] No video formats found"
for clips that exist and play fine), and when extraction breaks every real
clip classifies as "absent" — which is indistinguishable from the end of the
archive. An HTTP 200/404 can't lie about existence.
"""

import html as html_mod
import json
import re
import sys
from pathlib import Path
from datetime import datetime

import requests
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

# Granicus publishes clips DAYS after allocating their ids, so the monotonic
# high-water mark alone re-creates the wedge in the other direction: the
# scanner walks past a not-yet-published id, marks it absent forever, and the
# clip is never picked up when it goes live (real prod symptom 2026-08: clips
# 6846-6856 were published after the scanner had already crawled to the old
# hard-coded 7000 ceiling, wedging the archive for 13 days). Every run
# therefore RE-CHECKS the absent ids between the newest found clip and the
# high-water mark (bounded by RECHECK_WINDOW), and the high-water mark is
# never allowed to run more than MAX_LOOKAHEAD past the newest found clip.
RECHECK_WINDOW = 500
MAX_LOOKAHEAD = 400

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def probe_clip(clip_id: int) -> dict:
    """Check whether a clip exists without downloading it.

    Returns a dict with a ``status`` of:
      - ``"found"``  — clip exists (``title`` present)
      - ``"absent"`` — genuine 404 / not-public (counts toward the gap budget)
      - ``"error"``  — transient network/server failure (does NOT count as a gap)
    """
    url = f"https://{GRANICUS_HOST}/player/clip/{clip_id}?view_id={VIEW_ID}&redirect=true"

    try:
        resp = requests.get(
            url,
            timeout=30,
            allow_redirects=False,  # a redirect means "no public player page"
            headers={"User-Agent": _UA},
        )
    except requests.RequestException as e:
        return {"status": "error", "clip_id": clip_id, "error": str(e)[:200]}

    if resp.status_code == 200:
        m = _TITLE_RE.search(resp.text)
        title = html_mod.unescape(m.group(1)).strip() if m else ""
        if title:
            return {"status": "found", "clip_id": clip_id, "title": title}
        # A 200 with no parseable title is suspicious (markup change?) — treat
        # as transient so the scan stops here instead of eating the archive.
        return {"status": "error", "clip_id": clip_id, "error": "200 without <title>"}

    if resp.status_code == 404 or 300 <= resp.status_code < 400:
        # 404 = no such clip; a redirect (e.g. to the view listing) = clip id
        # exists but has no public player page. Both are "not available".
        return {"status": "absent", "clip_id": clip_id}

    # 403 / 5xx / anything else — a server-side or WAF hiccup must NOT look
    # like the end of the archive.
    return {
        "status": "error",
        "clip_id": clip_id,
        "error": f"HTTP {resp.status_code}",
    }


def run_recheck(
    start: int,
    end: int,
    available: list,
    probe=probe_clip,
) -> list:
    """Re-probe previously-absent ids ``start..end`` for late-published clips.

    No gap budget — the range is bounded by the caller (it sits between the
    newest found clip and the high-water mark, capped at RECHECK_WINDOW).
    A transient error stops the pass; the ids simply get re-checked next run.
    """
    if end < start:
        return available

    known = {c["clip_id"] for c in available}

    for clip_id in range(start, end + 1):
        if clip_id in known:
            continue
        res = probe(clip_id)
        status = res.get("status")

        if status == "error":
            print(
                f"[recheck {clip_id}] network error: {res.get('error')} — "
                f"stopping recheck (retried next run)"
            )
            break
        if status == "found":
            available.append({"clip_id": clip_id, "title": res.get("title")})
            print(f"[recheck {clip_id}] LATE-PUBLISHED: {res.get('title')}")

    return available


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
    Ids the budget skipped are covered by run_recheck on later runs.

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

    max_found = max((c["clip_id"] for c in available), default=0)

    # Pass 1 — re-check the absent ids behind the high-water mark for clips
    # that were published AFTER the scanner first walked past their id.
    recheck_start = max(max_found + 1, last_probed - RECHECK_WINDOW + 1)
    if recheck_start <= last_probed:
        print(f"Re-checking previously-absent ids {recheck_start}..{last_probed}")
        before = len(available)
        available = run_recheck(recheck_start, last_probed, available)
        if len(available) != before:
            save_progress(output_file, available, last_probed)
            max_found = max((c["clip_id"] for c in available), default=0)

    # Pass 2 — fresh ground past the high-water mark, capped so the frontier
    # can never run away from the newest real clip (the old hard-coded 7000
    # ceiling both let it run 150+ ids ahead AND froze the scan once reached).
    start = int(sys.argv[1]) if len(sys.argv) > 1 else max(1, last_probed + 1)
    end = int(sys.argv[2]) if len(sys.argv) > 2 else max_found + MAX_LOOKAHEAD

    if end < start:
        print(
            f"Fresh scan skipped: high-water mark {last_probed} is already "
            f"{last_probed - max_found} ids past newest found clip {max_found} "
            f"(cap {MAX_LOOKAHEAD})."
        )
        save_progress(output_file, available, last_probed)
        print(f"\nDone! Found {len(available)} available clips")
        print(f"Resume point (last_checked) saved as: {last_probed}")
        print(f"Saved to {output_file}")
        return

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
