#!/usr/bin/env python3
"""Push-based news alerts for freshly ingested meeting clips.

Two signals, one email digest (the pull surfaces — search/ask — already
exist; this is the newsroom "tips channel", modeled on Connecticut Public's
Public Meeting Monitor and Hearst's Assembly keyword alerts):

  1. LEADS   — a Haiku pass over each new clip's timestamped transcript
               flags up to three potentially newsworthy items, intentionally
               brief, with [H:MM:SS] pointers. Discovery only: nothing here
               publishes anywhere.
  2. WATCHLIST — literal keyword/phrase hits (word-boundary, case-insensitive;
               `re:`-prefixed lines are raw regexes) from a newsroom-curated
               watchlist file, with first-hit timestamp + context.

State lives in alerts_state.json at the repo root (gitignored, NOT under
the output dir so the public S3 sync never ships it; same for
watchlist.txt). First run initializes state by marking every existing clip
seen and sends nothing — only clips ingested after install ever alert, and
only clips whose meeting date is within ALERT_RECENT_DAYS (backfilled
decades-old clips are marked seen silently).

Delivery is SES email via the aws CLI (no new Python deps; the box IAM
user has a scoped ses:SendEmail policy).

Usage:
  uv run python -m scripts.meeting_alerts --scan [--dry-run]
  uv run python -m scripts.meeting_alerts --clip 6791 --dry-run
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from config import get_config

CFG = get_config()
CLIPS_DIR = Path(CFG.output_dir) / "clips"

WATCHLIST_FILE = Path(os.getenv("WATCHLIST_FILE", REPO_ROOT / "watchlist.txt"))
STATE_FILE = Path(os.getenv("ALERTS_STATE_FILE", REPO_ROOT / "alerts_state.json"))
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "paulmoliva@gmail.com")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "paulmoliva@gmail.com")
ALERT_SES_REGION = os.getenv("ALERT_SES_REGION", "us-east-1")
# Clips whose meeting date is older than this never alert (backfills of
# historical archives are news *sources*, not news).
RECENT_DAYS = int(os.getenv("ALERT_RECENT_DAYS", "14"))
# Runaway guard: excess clips stay unseen and roll to the next cron run.
MAX_CLIPS_PER_RUN = int(os.getenv("ALERT_MAX_CLIPS_PER_RUN", "15"))
LEADS_MODEL = os.getenv("ALERT_LEADS_MODEL", "claude-haiku-4-5")
LEADS_MAX_TRANSCRIPT_CHARS = 120_000

LEADS_SYSTEM_PROMPT = """\
You are the news desk of The Lexington Times reviewing a transcript of a \
just-published local government meeting. Flag up to THREE potentially \
newsworthy items a reporter should consider investigating. Be selective — \
routine procedure (roll calls, minutes approvals, consent-agenda rubber \
stamps, proclamations) is not news. Newsworthy signals: unusual spending or \
contracts, policy changes, disputes or unusually heated exchanges, \
accountability issues, anything affecting many residents, named officials \
in conflict, public commenters raising substantive allegations.

Format each item as one bullet:
• [H:MM:SS] Short headline — one or two sentences on what happened and why \
it matters. Include names, dollar amounts, and vote outcomes when stated.

Use the [H:MM:SS] markers embedded in the transcript for timestamps. Do not \
speculate beyond what the transcript says. If nothing rises to newsworthy, \
reply with exactly: NONE"""


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def parse_watchlist(text: str) -> list[tuple[str, re.Pattern]]:
    """[(display_term, compiled_pattern)] from watchlist file content."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            pat = line[3:].strip()
            try:
                out.append((pat, re.compile(pat, re.IGNORECASE)))
            except re.error as e:
                print(f"[watchlist] bad regex {pat!r}: {e}")
        else:
            out.append((line, re.compile(rf"\b{re.escape(line)}\b", re.IGNORECASE)))
    return out


def load_watchlist() -> list[tuple[str, re.Pattern]]:
    if not WATCHLIST_FILE.exists():
        return []
    return parse_watchlist(WATCHLIST_FILE.read_text())


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def match_watchlist(watchlist, segments, flat_texts) -> list[dict]:
    """First hit + count per term. Segments give timestamps; flat_texts is
    [(label, text)] fallbacks (agenda files, segment-less transcripts)."""
    hits = []
    for term, pat in watchlist:
        first = None
        count = 0
        for seg in segments or []:
            if pat.search(seg.get("text") or ""):
                count += 1
                if first is None:
                    first = {"ts": seg.get("start"), "context": (seg.get("text") or "").strip()[:200]}
        if first is None:
            for label, text in flat_texts:
                m = pat.search(text or "")
                if m:
                    count += len(pat.findall(text))
                    ctx = text[max(0, m.start() - 80):m.end() + 80].replace("\n", " ").strip()
                    first = {"ts": None, "context": f"({label}) …{ctx}…"}
                    break
        if first is not None:
            hits.append({"term": term, "count": count, **first})
    return hits


# ---------------------------------------------------------------------------
# Leads (Haiku pass)
# ---------------------------------------------------------------------------

def build_leads_input(clip_dir: Path, meta: dict) -> str | None:
    files = meta.get("files") or {}
    seg_name = files.get("transcript_segments")
    if seg_name and (clip_dir / seg_name).exists():
        try:
            segments = json.loads((clip_dir / seg_name).read_text())
            from summary_v2 import build_timestamped_transcript
            return build_timestamped_transcript(segments)[:LEADS_MAX_TRANSCRIPT_CHARS]
        except Exception as e:
            print(f"[leads] segment load failed for {clip_dir.name}: {e}")
    t_name = files.get("transcript")
    if t_name and (clip_dir / t_name).exists():
        return (clip_dir / t_name).read_text(errors="ignore")[:LEADS_MAX_TRANSCRIPT_CHARS]
    return None


def generate_leads(clip_dir: Path, meta: dict) -> str | None:
    """Bullet text from Haiku, None on NONE/failure. Never raises."""
    transcript = build_leads_input(clip_dir, meta)
    if not transcript:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=LEADS_MODEL,
            max_tokens=1000,
            temperature=0.2,
            system=LEADS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       f"Meeting: {meta.get('title')} ({meta.get('date')})\n\n{transcript}"}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"[leads] generation failed for {clip_dir.name}: {e}")
        return None
    if not text or text.upper().startswith("NONE"):
        return None
    return text


# ---------------------------------------------------------------------------
# Digest + email
# ---------------------------------------------------------------------------

def granicus_url(clip_id: int, ts: float | None = None) -> str:
    url = f"https://{CFG.granicus_host}/player/clip/{clip_id}?view_id={CFG.default_view_id}&redirect=true"
    if ts is not None:
        url += f"&entrytime={int(ts)}"
    return url


def clip_section(clip_id: int, meta: dict, leads: str | None, hits: list[dict]) -> str:
    lines = [
        f"━━ Clip {clip_id} · {meta.get('title')} · {meta.get('date')}",
        f"Archive: {CFG.site_url}/meeting/{clip_id}",
        f"Video:   {granicus_url(clip_id)}",
    ]
    if leads:
        lines += ["", "Leads:", leads]
    if hits:
        lines += ["", "Watchlist:"]
        for h in hits:
            where = f"[{fmt_ts(h['ts'])}]" if h["ts"] is not None else "(no timestamp)"
            lines.append(f"• \"{h['term']}\" ×{h['count']} — first at {where}: \"{h['context']}\"")
            if h["ts"] is not None:
                lines.append(f"   ↳ {granicus_url(clip_id, h['ts'])}")
    return "\n".join(lines)


def send_email(subject: str, body: str) -> bool:
    payload = {
        "FromEmailAddress": ALERT_EMAIL_FROM,
        "Destination": {"ToAddresses": [a.strip() for a in ALERT_EMAIL_TO.split(",") if a.strip()]},
        "Content": {"Simple": {"Subject": {"Data": subject},
                               "Body": {"Text": {"Data": body}}}},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        tmp = f.name
    try:
        r = subprocess.run(
            ["aws", "sesv2", "send-email", "--region", ALERT_SES_REGION,
             "--cli-input-json", f"file://{tmp}"],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"[email] SES send failed: {r.stderr.strip()}")
            return False
        return True
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# State + scan
# ---------------------------------------------------------------------------

def all_clip_ids() -> list[int]:
    if not CLIPS_DIR.exists():
        return []
    return sorted(int(e.name) for e in CLIPS_DIR.iterdir()
                  if e.is_dir() and e.name.isdigit())


def load_state() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, STATE_FILE)


def is_recent(meta: dict, today: date | None = None) -> bool:
    d = meta.get("date")
    if not d:
        return True  # undated new clip: err toward alerting
    try:
        clip_date = date.fromisoformat(str(d)[:10])
    except ValueError:
        return True
    return clip_date >= (today or date.today()) - timedelta(days=RECENT_DAYS)


def process_clip(clip_id: int, watchlist, with_leads: bool = True):
    """(meta, leads_text, watchlist_hits) for one clip."""
    clip_dir = CLIPS_DIR / str(clip_id)
    meta = json.loads((clip_dir / "metadata.json").read_text())
    files = meta.get("files") or {}

    segments = None
    seg_name = files.get("transcript_segments")
    if seg_name and (clip_dir / seg_name).exists():
        try:
            segments = json.loads((clip_dir / seg_name).read_text())
        except Exception:
            segments = None

    flat_texts = []
    if segments is None:
        t_name = files.get("transcript")
        if t_name and (clip_dir / t_name).exists():
            flat_texts.append(("transcript", (clip_dir / t_name).read_text(errors="ignore")))
    for fn in sorted(clip_dir.glob("*agenda*.txt")):
        flat_texts.append(("agenda", fn.read_text(errors="ignore")))

    hits = match_watchlist(watchlist, segments, flat_texts)
    leads = generate_leads(clip_dir, meta) if with_leads else None
    return meta, leads, hits


def has_transcript(meta: dict, clip_dir: Path) -> bool:
    t = (meta.get("files") or {}).get("transcript")
    return bool(t and (clip_dir / t).exists())


def run_scan(dry_run: bool, with_leads: bool) -> int:
    state = load_state()
    if state is None:
        ids = all_clip_ids()
        state = {"seen": {str(i): "preexisting" for i in ids},
                 "initialized_at": datetime.now(timezone.utc).isoformat()}
        if dry_run:
            print(f"[init] dry-run: would mark {len(ids)} existing clips seen (no alerts)")
        else:
            save_state(state)
            print(f"[init] marked {len(ids)} existing clips seen — alerts start with the next new clip")
        return 0

    watchlist = load_watchlist()
    if not watchlist:
        print(f"[watchlist] {WATCHLIST_FILE} missing or empty — watchlist scan disabled")

    seen = state["seen"]
    unseen = [i for i in all_clip_ids() if str(i) not in seen]
    now_iso = datetime.now(timezone.utc).isoformat()

    to_alert = []
    for cid in unseen:
        clip_dir = CLIPS_DIR / str(cid)
        try:
            meta = json.loads((clip_dir / "metadata.json").read_text())
        except Exception:
            continue  # not fully written yet; retry next run
        if not is_recent(meta):
            seen[str(cid)] = f"skipped-old:{now_iso}"
            continue
        if not has_transcript(meta, clip_dir):
            seen[str(cid)] = f"skipped-no-transcript:{now_iso}"
            continue
        to_alert.append(cid)

    deferred = to_alert[MAX_CLIPS_PER_RUN:]
    to_alert = to_alert[:MAX_CLIPS_PER_RUN]
    if deferred:
        print(f"[scan] {len(deferred)} clips deferred to next run (max {MAX_CLIPS_PER_RUN}/run)")

    sections = []
    n_leads = n_hits = 0
    for cid in to_alert:
        meta, leads, hits = process_clip(cid, watchlist, with_leads=with_leads)
        if leads:
            n_leads += len(re.findall(r"^\s*•", leads, re.M)) or 1
        n_hits += len(hits)
        if leads or hits:
            sections.append(clip_section(cid, meta, leads, hits))
        seen[str(cid)] = now_iso
        print(f"[scan] {cid}: leads={'yes' if leads else 'no'} watchlist_hits={len(hits)}")

    if sections:
        subject = (f"LT Meeting Alerts ({CFG.publication_name}): "
                   f"{len(sections)} clip(s) — {n_leads} lead(s), {n_hits} watchlist hit(s)")
        body = (f"{CFG.publication_name} — new-meeting scan "
                f"({len(to_alert)} clip(s) reviewed)\n\n" + "\n\n\n".join(sections) +
                "\n\n—\nAI-flagged leads for reporter follow-up; verify against the "
                "linked video before publishing anything.")
        if dry_run:
            print("\n===== DRY RUN — email that would be sent =====")
            print(f"Subject: {subject}\n\n{body}")
        elif send_email(subject, body):
            print(f"[email] sent: {subject}")
        else:
            return 1  # leave state unwritten so the next run retries
    else:
        print(f"[scan] {len(to_alert)} clip(s) reviewed, nothing to report")

    if not dry_run:
        save_state(state)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="scan un-alerted clips and email a digest")
    ap.add_argument("--clip", type=int, help="process one clip and print its digest section")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending/saving")
    ap.add_argument("--no-leads", action="store_true", help="skip the Haiku leads pass")
    args = ap.parse_args(argv)

    if args.clip:
        meta, leads, hits = process_clip(args.clip, load_watchlist(), with_leads=not args.no_leads)
        print(clip_section(args.clip, meta, leads, hits))
        return 0
    if args.scan:
        return run_scan(dry_run=args.dry_run, with_leads=not args.no_leads)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
