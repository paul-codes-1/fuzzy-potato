#!/usr/bin/env python3
"""Local two-pass summaries backfill: Pass-1 facts extraction on a local
Qwen3-30B-A3B (llama-server, $0) + Pass-2 narration on Claude Haiku (cheap),
for clips that have a transcript but no extracted_facts.json / summary.txt.

Sibling of local_whisper_backfill.py — same census/run/push/status shape,
same staging + finalize chain, shared state-locking helpers. Validated
against production GPT-4o extractions on a 24-clip eval (2026-07-14):
normalized agreement 85% on stated roll-call tallies / 95% on outcome
classes, with the residual disagreements adjudicated in the LOCAL model's
favor (GPT-4o invents vote counts for voice votes; Qwen leaves them null —
the CONVENTION_ADDENDUM below pins that behavior explicitly).

Pass 1 runs against ``--llama-url`` (default http://127.0.0.1:8090/v1) via
fuzzy-potato's own summary_v2.generate_summary_v2 — the OpenAI client is
wrapped to append the convention addendum and raise the completion cap
(local extraction is more verbose than GPT-4o; 8000 tokens truncates big
council packets — that was the eval's only systematic failure).

Usage (repo root):
  python3 scripts/local_summaries_backfill.py census
  python3 scripts/local_summaries_backfill.py run --max 0 --workers 2
  python3 scripts/local_summaries_backfill.py push
  python3 scripts/local_summaries_backfill.py status

State in ~/lt/.whisper-backfill/summaries/. run/push overlap safely.
Requires: llama-server up with the Qwen GGUF; ANTHROPIC_API_KEY (repo .env).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import local_whisper_backfill as lwb  # state helpers, finalize template, ssh bits

WORKROOT = Path.home() / "lt" / ".whisper-backfill" / "summaries"
EXTRACTION_MODEL_STAMP = "qwen3-30b-a3b-instruct-2507-local"
MAX_COMPLETION_TOKENS = 16000
LLM_TIMEOUT = 2400

CONVENTION_ADDENDUM = """

ADDITIONAL EXTRACTION CONVENTIONS (follow exactly):
- Record ayes/nays ONLY when a numeric count or per-member roll call is
  explicitly stated. Voice votes ("all in favor", "without dissent",
  "motion carries") get ayes=null, nays=null. NEVER infer counts from
  attendance.
- outcome describes the fate of the underlying item: a motion to postpone/
  table/continue that succeeds -> outcome="postponed"; use "passed",
  "failed", "postponed", "withdrawn".
- Attendance names: full names as stated, without honorifics (no Mr./Ms./
  Councilmember prefixes).
- When one roll call covers multiple docket items, record it once per
  distinctly-voted motion, not per agenda line."""

CENSUS_REMOTE_SCRIPT = r"""
import json, glob, os
out = []
for d in sorted(glob.glob("REMOTE_CLIPS/*/")):
    cid = os.path.basename(d.rstrip("/"))
    if not cid.isdigit(): continue
    mp = os.path.join(d, "metadata.json")
    if not os.path.exists(mp): continue
    try: m = json.load(open(mp))
    except Exception: continue
    files = m.get("files", {})
    t = files.get("transcript")
    if not t or not os.path.exists(os.path.join(d, t)): continue
    has_facts = os.path.exists(os.path.join(d, "extracted_facts.json"))
    has_summary = os.path.exists(os.path.join(d, "summary.txt"))
    if has_facts and has_summary: continue
    out.append({
        "clip_id": int(cid), "date": m.get("date"), "title": m.get("title"),
        "meeting_body": m.get("meeting_body"),
        "words": m.get("transcript_words") or 0,
        "files": {k: files.get(k) for k in
                  ("transcript", "transcript_segments", "agenda_txt", "minutes_txt")},
    })
print(json.dumps(out))
""".replace("REMOTE_CLIPS", lwb.REMOTE_CLIPS)


def load_state() -> dict:
    return lwb.load_json(WORKROOT / "state.json", {"clips": {}})


def set_clip_state(state: dict, clip_id: int, status: str, **extra) -> None:
    entry = {"status": status, "at": lwb.now_iso(), **extra}
    state["clips"][str(clip_id)] = entry
    import fcntl
    with open(WORKROOT / "state.lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        fresh = lwb.load_json(WORKROOT / "state.json", {"clips": {}})
        fresh["clips"][str(clip_id)] = entry
        lwb.save_json(WORKROOT / "state.json", fresh)


def cmd_census(host: str) -> None:
    lwb.log(f"Querying {host} for clips with transcript but no facts/summary...")
    res = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, "python3", "-"],
                         input=CENSUS_REMOTE_SCRIPT, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        sys.exit(f"census failed: {res.stderr.strip()}")
    backlog = json.loads(res.stdout)
    backlog.sort(key=lambda c: (c.get("date") or "0000", c["clip_id"]), reverse=True)
    lwb.save_json(WORKROOT / "backlog.json", backlog)
    lwb.log(f"Backlog: {len(backlog)} clips -> {WORKROOT / 'backlog.json'}")


def fetch_inputs(host: str, clip: dict) -> Path:
    """Rsync the clip's input files from the box into work/<id>/."""
    cid = clip["clip_id"]
    workdir = WORKROOT / "work" / str(cid)
    workdir.mkdir(parents=True, exist_ok=True)
    inc = ["--include=/metadata.json"]
    for k in ("transcript", "transcript_segments", "agenda_txt", "minutes_txt"):
        if clip["files"].get(k):
            inc.append(f"--include=/{clip['files'][k]}")
    res = subprocess.run(
        ["rsync", "-a"] + inc + ["--exclude=*",
         f"{host}:{lwb.REMOTE_CLIPS}/{cid}/", f"{workdir}/"],
        capture_output=True, text=True, timeout=300)
    if res.returncode != 0 or not (workdir / "metadata.json").exists():
        raise RuntimeError(f"input_fetch_failed: {res.stderr.strip()[:200]}")
    return workdir


def process_clip(clip: dict, host: str, llama_url: str, anthropic_client) -> dict:
    import summary_v2
    from openai import OpenAI

    cid = clip["clip_id"]
    workdir = fetch_inputs(host, clip)
    staged = WORKROOT / "staged" / str(cid)
    staged.mkdir(parents=True, exist_ok=True)

    meta = json.loads((workdir / "metadata.json").read_text())
    files = meta.get("files", {})
    if (workdir / "extracted_facts.json").exists() and (workdir / "summary.txt").exists():
        return {"skip": "box_already_done"}

    # Timestamped transcript from segments when available (Pass 1 copies
    # real [H:MM:SS] markers instead of inventing transcript_approx_time).
    transcript = ""
    seg = clip["files"].get("transcript_segments")
    if seg and (workdir / seg).exists():
        try:
            segments = json.loads((workdir / seg).read_text())
            transcript = summary_v2.build_timestamped_transcript(segments)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    if not transcript:
        transcript = (workdir / clip["files"]["transcript"]).read_text(
            encoding="utf-8", errors="replace")

    agenda = minutes = None
    if clip["files"].get("agenda_txt") and (workdir / clip["files"]["agenda_txt"]).exists():
        agenda = (workdir / clip["files"]["agenda_txt"]).read_text(encoding="utf-8", errors="replace")
    if clip["files"].get("minutes_txt") and (workdir / clip["files"]["minutes_txt"]).exists():
        minutes = (workdir / clip["files"]["minutes_txt"]).read_text(encoding="utf-8", errors="replace")

    client = OpenAI(base_url=llama_url, api_key="local", timeout=LLM_TIMEOUT)
    _orig = client.chat.completions.create

    def _create(**kw):
        msgs = kw.get("messages", [])
        if msgs and msgs[0].get("role") == "system":
            msgs = [{**msgs[0], "content": msgs[0]["content"] + CONVENTION_ADDENDUM}] + msgs[1:]
        return _orig(**{**kw, "messages": msgs, "max_tokens": MAX_COMPLETION_TOKENS})
    client.chat.completions.create = _create

    summary, facts = summary_v2.generate_summary_v2(
        openai_client=client, anthropic_client=anthropic_client,
        transcript=transcript, agenda_text=agenda, minutes_text=minutes,
        meeting_body=meta.get("meeting_body") or "Unknown",
        date=meta.get("date") or "Unknown", extraction_model="local")

    if not facts:
        raise RuntimeError("pass1_no_facts")
    if not summary or len(summary) < 200:
        raise RuntimeError(f"pass2_summary_too_short:{len(summary or '')}")

    (staged / "extracted_facts.json").write_text(
        json.dumps(facts, indent=2, ensure_ascii=False))
    (staged / "summary.txt").write_text(summary)
    files["extracted_facts"] = "extracted_facts.json"
    files["summary_txt"] = "summary.txt"
    meta["files"] = files
    meta.setdefault("models", {})["summary"] = (
        f"{EXTRACTION_MODEL_STAMP}+{summary_v2.NARRATION_MODEL}")
    meta["summary_updated_at"] = datetime.now().isoformat()
    (staged / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return {"motions": len(facts.get("motions_and_votes", [])),
            "agenda_items": len(facts.get("agenda_items", [])),
            "summary_chars": len(summary)}


def cmd_run(host: str, llama_url: str, max_clips: int, workers: int, retry_failed: bool) -> None:
    sys.path.insert(0, str(Path.home() / "lt" / "fuzzy-potato"))
    from clients import get_anthropic
    anthropic_client = get_anthropic()

    backlog = lwb.load_json(WORKROOT / "backlog.json", None)
    if backlog is None:
        sys.exit("no backlog.json — run census first")
    state = load_state()
    skip = {"staged", "pushed", "skipped"} | (set() if retry_failed else {"failed"})
    todo = [c for c in backlog
            if state["clips"].get(str(c["clip_id"]), {}).get("status") not in skip]
    if max_clips > 0:
        todo = todo[:max_clips]
    lwb.log(f"{len(todo)} clips to process (of {len(backlog)} backlog), {workers} workers")

    done = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(process_clip, c, host, llama_url, anthropic_client): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            cid = c["clip_id"]
            label = f"{cid} ({c.get('date')} {str(c.get('title'))[:40]})"
            try:
                r = fut.result()
                if r.get("skip"):
                    set_clip_state(state, cid, "skipped", reason=r["skip"])
                    lwb.log(f"[{i}/{len(todo)}] SKIP {label} — {r['skip']}")
                else:
                    set_clip_state(state, cid, "staged", **r)
                    done += 1
                    lwb.log(f"[{i}/{len(todo)}] STAGED {label}: {r['motions']} motions, "
                            f"{r['agenda_items']} items, {r['summary_chars']} chars")
                shutil.rmtree(WORKROOT / "work" / str(cid), ignore_errors=True)
            except Exception as e:
                failed += 1
                set_clip_state(state, cid, "failed", reason=str(e)[:300])
                shutil.rmtree(WORKROOT / "staged" / str(cid), ignore_errors=True)
                lwb.log(f"[{i}/{len(todo)}] FAILED {label}: {str(e)[:160]}")
    lwb.log(f"run complete: {done} staged, {failed} failed. Next: push.")


def cmd_push(host: str, finalize: bool) -> None:
    state = load_state()
    staged_ids = sorted(int(cid) for cid, s in state["clips"].items()
                        if s.get("status") == "staged")
    if not staged_ids:
        sys.exit("nothing staged to push")
    lwb.log(f"pushing {len(staged_ids)} staged clips to {host}")
    for cid in staged_ids:
        src = WORKROOT / "staged" / str(cid)
        res = subprocess.run(["rsync", "-a", f"{src}/", f"{host}:{lwb.REMOTE_CLIPS}/{cid}/"],
                             capture_output=True, text=True, timeout=300)
        if res.returncode != 0:
            sys.exit(f"rsync failed for clip {cid}: {res.stderr.strip()}")
    lwb.log("rsync complete")
    if not finalize:
        lwb.log("--no-finalize: NOT ingested/reloaded/synced")
        return
    script = (lwb.FINALIZE_TEMPLATE
              .replace("__REPO__", lwb.REMOTE_REPO)
              .replace("__IDS__", " ".join(map(str, staged_ids))))
    proc = subprocess.Popen(["ssh", host, "bash", "-s"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out, _ = proc.communicate(script, timeout=14400)
    print(out)
    if proc.returncode != 0:
        sys.exit(f"finalize FAILED (exit {proc.returncode})")
    for cid in staged_ids:
        keep = {k: v for k, v in state["clips"][str(cid)].items() if k not in ("status", "at")}
        set_clip_state(state, cid, "pushed", **keep)
    lwb.log(f"pushed + finalized {len(staged_ids)} clips")


def cmd_status() -> None:
    from collections import Counter
    backlog = lwb.load_json(WORKROOT / "backlog.json", [])
    state = load_state()
    counts = Counter(s.get("status") for s in state["clips"].values())
    print(f"backlog: {len(backlog)} clips")
    for k in ("staged", "pushed", "failed", "skipped"):
        if counts.get(k): print(f"  {k}: {counts[k]}")
    print(f"  untouched: {sum(1 for c in backlog if str(c['clip_id']) not in state['clips'])}")
    for cid, s in list(state["clips"].items()):
        if s.get("status") == "failed":
            print(f"  failed {cid}: {s.get('reason','')[:110]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["census", "run", "push", "status"])
    ap.add_argument("--host", default=lwb.DEFAULT_HOST)
    ap.add_argument("--llama-url", default="http://127.0.0.1:8090/v1")
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--no-finalize", action="store_true")
    args = ap.parse_args()
    WORKROOT.mkdir(parents=True, exist_ok=True)
    if args.command == "census":
        cmd_census(args.host)
    elif args.command == "run":
        cmd_run(args.host, args.llama_url, args.max, args.workers, args.retry_failed)
    elif args.command == "push":
        cmd_push(args.host, finalize=not args.no_finalize)
    else:
        cmd_status()


if __name__ == "__main__":
    main()
