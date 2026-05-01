#!/usr/bin/env python3
"""Backfill missing `date` fields in clip metadata.json files.

Sources, in priority order:
  1. Granicus ViewPublisher listing (authoritative unix timestamp; recent clips only)
  2. Granicus RSS feed (pubDate; last ~100 clips)
  3. extracted_facts.json meeting_info.date (LLM-extracted; ~20% coverage)
  4. Transcript first 3000 chars (regex for date; rare)
  5. Linear interpolation from the two nearest dated clip_ids (day-precision approximation)

Sources 1-4 are tagged "exact" in the log. Source 5 is "estimated" — prints so the
operator knows which dates are fuzzy.
"""

import bisect
import glob
import json
import os
import re
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GRANICUS_HOST = os.getenv("GRANICUS_HOST", "lfucg.granicus.com")
VIEW_ID = 14
CLIPS_DIR = Path("lfucg_output/clips")

MONTH_MAP = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4,
    'May': 5, 'June': 6, 'July': 7, 'August': 8,
    'September': 9, 'October': 10, 'November': 11, 'December': 12,
}
DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b"
    r"|\b(\d{1,2})/(\d{1,2})/(\d{4})\b"
)


def fetch_listing_map() -> dict:
    """clip_id -> ISO date from ViewPublisher page (hidden unix timestamp per row)."""
    url = f"https://{GRANICUS_HOST}/ViewPublisher.php?view_id={VIEW_ID}"
    try:
        html = requests.get(url, timeout=30).text
    except Exception as e:
        print(f"[listing] fetch failed: {e}")
        return {}
    out = {}
    ts_re = re.compile(r'<span\s+style="display:\s*none;\s*">\s*(\d{9,11})\s*</span>')
    clip_re = re.compile(r"clip_id=(\d+)")
    for row in html.split("</tr>"):
        clip_ids = clip_re.findall(row)
        if not clip_ids:
            continue
        m = ts_re.search(row)
        if not m:
            continue
        ts = int(m.group(1))
        iso = datetime.utcfromtimestamp(ts).date().isoformat()
        for cid in clip_ids:
            out[int(cid)] = iso
    print(f"[listing] {len(out)} clips from ViewPublisher")
    return out


def fetch_rss_map() -> dict:
    """clip_id -> ISO date from Granicus RSS agendas feed."""
    url = f"https://{GRANICUS_HOST}/ViewPublisherRSS.php?view_id={VIEW_ID}&mode=agendas"
    try:
        xml = requests.get(url, timeout=30).text
    except Exception as e:
        print(f"[rss] fetch failed: {e}")
        return {}
    out = {}
    items = xml.split("<item>")[1:]
    for item in items:
        clip_m = re.search(r"clip_id=(\d+)", item)
        pub_m = re.search(r"<pubDate>([^<]+)</pubDate>", item)
        if clip_m and pub_m:
            try:
                dt = parsedate_to_datetime(pub_m.group(1).strip())
                out[int(clip_m.group(1))] = dt.date().isoformat()
            except Exception:
                pass
    print(f"[rss] {len(out)} clips from RSS feed")
    return out


def date_from_text(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    g = m.groups()
    try:
        if g[0]:
            y, mo, d = int(g[2]), MONTH_MAP[g[0]], int(g[1])
        else:
            y, mo, d = int(g[5]), int(g[3]), int(g[4])
        from datetime import date as _d
        return _d(y, mo, d).isoformat()
    except Exception:
        return None


def date_from_clip_files(clip_dir: Path) -> str | None:
    # extracted_facts.meeting_info.date
    fp = clip_dir / "extracted_facts.json"
    if fp.exists():
        try:
            facts = json.loads(fp.read_text())
            d = (facts.get("meeting_info") or {}).get("date")
            if d:
                try:
                    datetime.fromisoformat(d)
                    return d
                except Exception:
                    pass
        except Exception:
            pass
    # Scan agenda, minutes, transcript first 3000 chars
    for pattern in ("*agenda*.txt", "*minutes*.txt", "transcript_*.txt"):
        for fn in sorted(clip_dir.glob(pattern)):
            try:
                text = fn.read_text(errors="ignore")[:3000]
            except Exception:
                continue
            iso = date_from_text(text)
            if iso:
                return iso
    return None


def interpolate_date(clip_id: int, sorted_ids: list, dated: dict) -> str | None:
    idx = bisect.bisect_left(sorted_ids, clip_id)
    lower = sorted_ids[idx - 1] if idx > 0 else None
    upper = sorted_ids[idx] if idx < len(sorted_ids) else None
    if lower is None and upper is None:
        return None
    if lower is None:
        return dated[upper]
    if upper is None:
        return dated[lower]
    ld = datetime.fromisoformat(dated[lower])
    ud = datetime.fromisoformat(dated[upper])
    frac = (clip_id - lower) / (upper - lower)
    est = ld + frac * (ud - ld)
    return est.date().isoformat()


def main():
    # Index already-dated clips for interpolation
    dated: dict[int, str] = {}
    null_clips: list[int] = []
    for entry in CLIPS_DIR.iterdir():
        if not entry.is_dir():
            continue
        mp = entry / "metadata.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text())
        except Exception:
            continue
        cid = int(entry.name)
        d = m.get("date")
        if d:
            try:
                datetime.fromisoformat(d)
                dated[cid] = d
                continue
            except Exception:
                pass
        null_clips.append(cid)

    print(f"Pre-scan: {len(dated)} clips already dated, {len(null_clips)} null-date clips")

    listing_map = fetch_listing_map()
    rss_map = fetch_rss_map()

    sorted_ids = sorted(dated.keys())
    updated = {"listing": 0, "rss": 0, "files": 0, "interpolated": 0, "no_source": 0}
    dry_run = "--dry-run" in sys.argv

    for cid in sorted(null_clips):
        clip_dir = CLIPS_DIR / str(cid)
        mp = clip_dir / "metadata.json"
        source = None
        iso = listing_map.get(cid)
        if iso:
            source = "listing"
        else:
            iso = rss_map.get(cid)
            if iso:
                source = "rss"
            else:
                iso = date_from_clip_files(clip_dir)
                if iso:
                    source = "files"
                else:
                    iso = interpolate_date(cid, sorted_ids, dated)
                    if iso:
                        source = "interpolated"
        if not iso:
            updated["no_source"] += 1
            print(f"  [{cid}] NO_SOURCE")
            continue
        updated[source] += 1
        if not dry_run:
            data = json.loads(mp.read_text())
            data["date"] = iso
            data.setdefault("date_source", source)
            mp.write_text(json.dumps(data, indent=2))
        flag = "~" if source == "interpolated" else ""
        print(f"  [{cid}] {iso}{flag}  (from {source})")

    print()
    print("Summary:")
    for k, v in updated.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
