#!/usr/bin/env python3
"""Backfill missing `date` fields in clip metadata.json files.

Sources, in priority order:
  1. Granicus ViewPublisher listing (authoritative unix timestamp; recent clips only)
  2. Granicus RSS feed (pubDate; last ~100 clips)
  3. Official minutes PDF, page 1 (Granicus MinutesViewer redirect; exact)
  4. extracted_facts.json meeting_info.date (LLM-extracted — hallucination-prone)
     + agenda/minutes/transcript first 3000 chars (regex)
  5. Linear interpolation between the nearest anchor clip-ID neighbors (estimated)

Every candidate must pass a neighbor-window sanity check before being
written. On 2026-06-17 the LLM-extracted source stamped hallucinated
2024–2026 dates onto clips of 2008 council content and nothing validated
them against the archive's ID ordering (ISSUE-backfill-dates-sanity-check.md).

Anchors are the longest non-decreasing-by-date subsequence of the ID-sorted
dated clips: dates corroborated by the archive's global ordering. The
`date_source` field can't identify trustworthy clips (the corrupted clips
carried date_source=None, same as everything the pipeline dated at ingest),
and raw dated neighbors can't be trusted either — the 2026-06-17 batch was a
contiguous ID band whose bad dates mutually corroborate.

Modes:
  (default)     backfill null-date clips; --dry-run to preview
  --audit       read-only: flag every dated clip outside its anchor window;
                exit 1 if violations. Clips with `date_verified: true` in
                metadata.json (hand-checked out-of-order uploads) are skipped.
  --no-minutes  skip the minutes-PDF source (network + pdfplumber)
"""

import argparse
import bisect
import json
import os
import re
import time
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GRANICUS_HOST = os.getenv("GRANICUS_HOST", "lfucg.granicus.com")
VIEW_ID = 14
CLIPS_DIR = Path("lfucg_output/clips")

# Real gaps between adjacent clip IDs are days-to-weeks; 90 days absorbs the
# known genuinely-out-of-order uploads while rejecting year-scale hallucinations.
NEIGHBOR_WINDOW_DAYS = 90
# Clips at the head/tail of the ID range have only one neighbor to check against.
EDGE_WINDOW_DAYS = 365
# Politeness delay between Granicus minutes fetches.
MINUTES_FETCH_DELAY_S = 1.5
# Fancy UA strings get an HTML viewer shell from DocumentViewer.php, not the PDF.
MINUTES_UA = "Mozilla/5.0"

MONTH_MAP = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4,
    'May': 5, 'June': 6, 'July': 7, 'August': 8,
    'September': 9, 'October': 10, 'November': 11, 'December': 12,
}
DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b"
    r"|\b(\d{1,2})/(\d{1,2})/(\d{4})\b"
)


def parse_iso_date(s) -> date | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Anchor set + neighbor-window sanity check
# ---------------------------------------------------------------------------

def build_anchors(dated: dict[int, date]) -> dict[int, date]:
    """Longest non-decreasing-by-date subsequence over ID-sorted clips.

    Patience sorting, O(n log n). A hallucinated date can only join the
    subsequence by out-competing the real archive around it, which a
    contiguous band of bad dates (even mutually-consistent ones) cannot do.
    """
    ids = sorted(dated)
    seq = [dated[i] for i in ids]
    tails: list[date] = []       # smallest tail date of each pile
    tails_idx: list[int] = []    # index (into seq) of that tail element
    parent = [-1] * len(seq)
    for i, d in enumerate(seq):
        pos = bisect.bisect_right(tails, d)
        if pos == len(tails):
            tails.append(d)
            tails_idx.append(i)
        else:
            tails[pos] = d
            tails_idx[pos] = i
        parent[i] = tails_idx[pos - 1] if pos > 0 else -1
    anchors: dict[int, date] = {}
    i = tails_idx[-1] if tails_idx else -1
    while i != -1:
        anchors[ids[i]] = seq[i]
        i = parent[i]
    return anchors


def neighbor_window(clip_id: int, anchors: dict[int, date]):
    """(lo, hi, description) window for clip_id from its nearest anchors.

    The clip itself is excluded from the neighbor lookup so audit can check
    anchor clips too. Returns None when there are no anchors at all.
    """
    a_ids = sorted(anchors)
    pos = bisect.bisect_left(a_ids, clip_id)
    p = pos - 1
    if p >= 0 and a_ids[p] == clip_id:
        p -= 1
    n = pos
    if n < len(a_ids) and a_ids[n] == clip_id:
        n += 1
    prev = anchors[a_ids[p]] if p >= 0 else None
    nxt = anchors[a_ids[n]] if n < len(a_ids) else None
    w = timedelta(days=NEIGHBOR_WINDOW_DAYS)
    e = timedelta(days=EDGE_WINDOW_DAYS)
    if prev is not None and nxt is not None:
        lo, hi = min(prev, nxt) - w, max(prev, nxt) + w
        desc = f"{a_ids[p]}:{prev}/{a_ids[n]}:{nxt}"
    elif prev is not None:
        lo, hi = prev - e, prev + e
        desc = f"{a_ids[p]}:{prev}/-"
    elif nxt is not None:
        lo, hi = nxt - e, nxt + e
        desc = f"-/{a_ids[n]}:{nxt}"
    else:
        return None
    return lo, hi, desc


def check_candidate(clip_id: int, candidate: date, anchors: dict[int, date]):
    """(ok, detail). ok=True when the candidate is consistent with the archive."""
    win = neighbor_window(clip_id, anchors)
    if win is None:
        return True, "no anchors to check against"
    lo, hi, desc = win
    detail = f"window=[{lo}..{hi}] neighbors={desc}"
    return (lo <= candidate <= hi), detail


def find_violations(dated: dict[int, date], verified: set[int] | None = None):
    """Read-only check of every dated clip against its anchor window."""
    verified = verified or set()
    anchors = build_anchors(dated)
    violations = []
    for cid in sorted(dated):
        if cid in verified:
            continue
        ok, detail = check_candidate(cid, dated[cid], anchors)
        if not ok:
            violations.append({"clip_id": cid, "date": dated[cid].isoformat(), "detail": detail})
    return violations


# ---------------------------------------------------------------------------
# Date sources
# ---------------------------------------------------------------------------

def fetch_listing_map() -> dict:
    """clip_id -> ISO date from ViewPublisher page (hidden unix timestamp per row)."""
    from datetime import datetime
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
        return date(y, mo, d).isoformat()
    except Exception:
        return None


def resolve_minutes_pdf_name(clip_id: int) -> str | None:
    """Follow MinutesViewer.php's redirect to the underlying lfucg_<hash>.pdf name."""
    url = f"https://{GRANICUS_HOST}/MinutesViewer.php?view_id={VIEW_ID}&clip_id={clip_id}"
    try:
        r = requests.head(url, timeout=30, allow_redirects=False)
    except Exception:
        return None
    loc = r.headers.get("Location", "")
    m = re.search(r"file=(lfucg_[0-9a-f]+\.pdf)", loc)
    return m.group(1) if m else None


def minutes_first_page_text(pdf_bytes: bytes) -> str:
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text() or ""


def date_from_minutes(clip_id: int):
    """(iso_date, pdf_file_name) from the official minutes' first page, or None.

    The meeting date is printed in the first few lines of the official
    minutes — the authoritative source for old clips that Granicus's
    listing/RSS no longer cover.
    """
    pdf_name = resolve_minutes_pdf_name(clip_id)
    if not pdf_name:
        return None
    time.sleep(MINUTES_FETCH_DELAY_S)
    url = f"https://{GRANICUS_HOST}/DocumentViewer.php?file={pdf_name}&view=1"
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": MINUTES_UA})
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            return None
        text = minutes_first_page_text(r.content)
    except Exception:
        return None
    iso = date_from_text(text[:2000])
    return (iso, pdf_name) if iso else None


def date_from_clip_files(clip_dir: Path) -> str | None:
    # extracted_facts.meeting_info.date
    fp = clip_dir / "extracted_facts.json"
    if fp.exists():
        try:
            facts = json.loads(fp.read_text())
            d = (facts.get("meeting_info") or {}).get("date")
            if parse_iso_date(d):
                return d
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


def interpolate_date(clip_id: int, anchors: dict[int, date]) -> date | None:
    """Day-precision estimate between the nearest anchor neighbors by ID."""
    a_ids = sorted(anchors)
    if not a_ids:
        return None
    idx = bisect.bisect_left(a_ids, clip_id)
    lower = a_ids[idx - 1] if idx > 0 else None
    upper = a_ids[idx] if idx < len(a_ids) else None
    if lower is None and upper is None:
        return None
    if lower is None:
        return anchors[upper]
    if upper is None:
        return anchors[lower]
    frac = (clip_id - lower) / (upper - lower)
    return anchors[lower] + timedelta(days=round(frac * (anchors[upper] - anchors[lower]).days))


# ---------------------------------------------------------------------------
# Resolution cascade
# ---------------------------------------------------------------------------

def resolve_date(clip_id: int, clip_dir: Path, listing_map: dict, rss_map: dict,
                 anchors: dict[int, date], use_minutes: bool = True,
                 minutes_fetcher=date_from_minutes):
    """(iso_date, source, extra_metadata) for one null-date clip, or (None, 'no_source', {}).

    Candidates are tried in trust order; every one must pass the anchor
    neighbor-window check before it wins. Rejected candidates are logged
    loudly and NEVER written — we fall through to the next source, ending
    at interpolation (inside the window by construction).
    """
    def candidates():
        iso = listing_map.get(clip_id)
        if iso:
            yield "listing", iso, {}
        iso = rss_map.get(clip_id)
        if iso:
            yield "rss", iso, {}
        if use_minutes:
            got = minutes_fetcher(clip_id)
            if got:
                yield "minutes", got[0], {"minutes_pdf_file": got[1]}
        iso = date_from_clip_files(clip_dir)
        if iso:
            yield "files", iso, {}

    for source, iso, extra in candidates():
        d = parse_iso_date(iso)
        if not d:
            continue
        ok, detail = check_candidate(clip_id, d, anchors)
        if ok:
            return d.isoformat(), source, extra
        print(f"REJECTED source={source} clip={clip_id} candidate={d.isoformat()} {detail}")

    est = interpolate_date(clip_id, anchors)
    if est:
        ok, detail = check_candidate(clip_id, est, anchors)
        if ok:
            return est.isoformat(), "interpolated", {}
        print(f"REJECTED source=interpolated clip={clip_id} candidate={est.isoformat()} {detail}")
    return None, "no_source", {}


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def load_clip_dates(clips_dir: Path):
    """(dated, verified, null_clips) from every metadata.json under clips_dir."""
    dated: dict[int, date] = {}
    verified: set[int] = set()
    null_clips: list[int] = []
    for entry in clips_dir.iterdir():
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        mp = entry / "metadata.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text())
        except Exception:
            continue
        cid = int(entry.name)
        d = parse_iso_date(m.get("date"))
        if d:
            dated[cid] = d
            if m.get("date_verified"):
                verified.add(cid)
        else:
            null_clips.append(cid)
    return dated, verified, null_clips


def run_audit(clips_dir: Path) -> int:
    dated, verified, null_clips = load_clip_dates(clips_dir)
    violations = find_violations(dated, verified)
    print(f"Audit: {len(dated)} dated clips ({len(verified)} date_verified, skipped), "
          f"{len(null_clips)} null-date clips")
    for v in violations:
        print(f"AUDIT-VIOLATION clip={v['clip_id']} date={v['date']} {v['detail']}")
    print(f"{len(violations)} violation(s)")
    return 1 if violations else 0


def run_backfill(clips_dir: Path, dry_run: bool, use_minutes: bool) -> int:
    dated, verified, null_clips = load_clip_dates(clips_dir)
    print(f"Pre-scan: {len(dated)} clips already dated, {len(null_clips)} null-date clips")

    listing_map = fetch_listing_map()
    rss_map = fetch_rss_map()
    anchors = build_anchors(dated)
    print(f"Anchors: {len(anchors)} of {len(dated)} dated clips corroborated by ID order")

    updated = {"listing": 0, "rss": 0, "minutes": 0, "files": 0,
               "interpolated": 0, "no_source": 0}
    for cid in sorted(null_clips):
        clip_dir = clips_dir / str(cid)
        iso, source, extra = resolve_date(
            cid, clip_dir, listing_map, rss_map, anchors, use_minutes=use_minutes)
        updated[source] += 1
        if not iso:
            print(f"  [{cid}] NO_SOURCE")
            continue
        if not dry_run:
            mp = clip_dir / "metadata.json"
            data = json.loads(mp.read_text())
            data["date"] = iso
            data["date_source"] = source
            data.update(extra)
            mp.write_text(json.dumps(data, indent=2))
        flag = "~" if source == "interpolated" else ""
        print(f"  [{cid}] {iso}{flag}  (from {source})")

    print()
    print("Summary:")
    for k, v in updated.items():
        print(f"  {k}: {v}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="preview backfill without writing")
    ap.add_argument("--audit", action="store_true",
                    help="read-only date-consistency check of all dated clips; exit 1 on violations")
    ap.add_argument("--no-minutes", action="store_true",
                    help="skip the official-minutes PDF source (no network fetches per clip)")
    ap.add_argument("--clips-dir", default=str(CLIPS_DIR))
    args = ap.parse_args(argv)

    clips_dir = Path(args.clips_dir)
    if args.audit:
        return run_audit(clips_dir)
    return run_backfill(clips_dir, dry_run=args.dry_run, use_minutes=not args.no_minutes)


if __name__ == "__main__":
    raise SystemExit(main())
