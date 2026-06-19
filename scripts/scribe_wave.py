#!/usr/bin/env python3
"""Prioritized ElevenLabs Scribe transcription backfill wave.

Processes the meeting clips that are missing a transcript through the FULL
pipeline (Scribe transcript -> GPT-4o facts -> Claude summary -> RAG ingest),
newest-first, using the ElevenLabs Scribe transcriber instead of Whisper.

Why this exists:
- The 2009-2015 backlog has no Granicus VTT (live closed-captioning didn't
  exist yet), so a transcript can only come from speech-to-text.
- ElevenLabs Scribe is cheaper than Whisper ($0.22/hr vs ~$0.36/hr) and the
  account has prepaid Pro credits that expire when the plan cancels.
- Those credits are SHARED with paulBot's live TTS, so this runner enforces a
  hard Scribe-audio-hour budget cap and logs cumulative burn, letting an
  operator stop it (or watch the ElevenLabs dashboard) before the pool drains.

Resumable: process_clip() skips any clip that already has a transcript file,
so re-running continues where it left off. Serial by design — process_clip
writes state.json + ChromaDB, which are not concurrency-safe.

Run (key comes from paulBot/.env -> "Bold Bengal Tiger", STT-scoped):
    ELEVENLABS_API_KEY=sk_... uv run python scripts/scribe_wave.py \
        --ids-file /tmp/priority_local.txt --max-hours 150
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # OPENAI_API_KEY + ANTHROPIC_API_KEY from fuzzy-potato/.env

from main import LFUCGPipeline  # noqa: E402

PRIORITY_RX = re.compile(
    r"council|urban county|planning commission|work session|zoning|subdivision",
    re.I,
)


def has_transcript(clip_dir: Path) -> bool:
    if not clip_dir.is_dir():
        return False
    return any(
        f.name.startswith("transcript_") and f.name.endswith(".txt")
        for f in clip_dir.iterdir()
    )


def title_and_date(clip_id: int, clips_dir: Path, index_by_id: dict) -> tuple[str, str]:
    c = index_by_id.get(clip_id)
    if c and c.get("title"):
        return c["title"], (c.get("date") or "")
    mp = clips_dir / str(clip_id) / "metadata.json"
    if mp.exists():
        try:
            m = json.loads(mp.read_text())
            return (m.get("title") or ""), (m.get("date") or "")
        except (json.JSONDecodeError, OSError):
            pass
    return "", ""


def build_priority_list(output_dir: Path) -> list[tuple[int, str, str]]:
    clips_dir = output_dir / "clips"
    index_by_id: dict[int, dict] = {}
    idx_path = output_dir / "index.json"
    if idx_path.exists():
        try:
            index_by_id = {
                c["clip_id"]: c for c in json.loads(idx_path.read_text())["clips"]
            }
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    out: list[tuple[int, str, str]] = []
    for d in clips_dir.iterdir():
        if not (d.is_dir() and d.name.isdigit()):
            continue
        cid = int(d.name)
        if has_transcript(d):
            continue
        title, date = title_and_date(cid, clips_dir, index_by_id)
        tl = title.lower()
        if "agenda only" in tl or "no video" in tl:
            continue
        if not PRIORITY_RX.search(tl):
            continue
        out.append((cid, date, title))
    out.sort(key=lambda x: (x[1] or ""), reverse=True)  # newest first
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="./lfucg_output")
    ap.add_argument(
        "--ids-file",
        help="Optional file of clip IDs (one per line) to process in order. "
        "If omitted, the priority list is computed from disk (newest-first).",
    )
    ap.add_argument(
        "--transcriber",
        choices=["elevenlabs", "whisper"],
        default="elevenlabs",
        help="Transcription backend. elevenlabs=Scribe ($0.22/hr prepaid "
        "credits); whisper=OpenAI ($0.36/hr cash).",
    )
    ap.add_argument(
        "--max-hours",
        type=float,
        default=150.0,
        help="Hard cap on billed audio-hours this run (safety stop).",
    )
    ap.add_argument(
        "--max-cost",
        type=float,
        default=0.0,
        help="Hard cap on estimated USD spend this run (0 = disabled). "
        "Rate: $0.22/hr Scribe, $0.36/hr Whisper.",
    )
    ap.add_argument("--max-clips", type=int, default=0, help="0 = no clip-count limit")
    ap.add_argument("--no-rag", action="store_true", help="Skip per-clip RAG ingest")
    ap.add_argument(
        "--skip-search-rebuild",
        action="store_true",
        help="Don't rebuild search.db at the end",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--ledger",
        default="scribe_wave_ledger.jsonl",
        help="Per-clip progress ledger (JSONL, appended).",
    )
    args = ap.parse_args()

    output_dir = Path(args.output_dir)

    if args.ids_file:
        ids = [int(x) for x in Path(args.ids_file).read_text().split() if x.strip()]
        clips_dir = output_dir / "clips"
        idx_by_id: dict[int, dict] = {}
        idx_path = output_dir / "index.json"
        if idx_path.exists():
            try:
                idx_by_id = {
                    c["clip_id"]: c
                    for c in json.loads(idx_path.read_text())["clips"]
                }
            except Exception:
                pass
        # keep the file's order; only drop clips already transcribed
        queue = []
        for cid in ids:
            if has_transcript(clips_dir / str(cid)):
                continue
            title, date = title_and_date(cid, clips_dir, idx_by_id)
            queue.append((cid, date, title))
    else:
        queue = build_priority_list(output_dir)

    print(f"[wave] {len(queue)} clips queued (missing transcript)")
    caps = [f"{args.max_hours}h"]
    if args.max_cost:
        caps.append(f"${args.max_cost}")
    if args.max_clips:
        caps.append(f"{args.max_clips} clips")
    print(f"[wave] transcriber={args.transcriber}  caps: {' / '.join(caps)}")
    if args.dry_run:
        for cid, date, title in queue[:20]:
            print(f"   {cid}  {date}  {title}")
        print(f"   ... ({len(queue)} total)")
        return 0

    RATE = {"elevenlabs": 0.22, "whisper": 0.36}[args.transcriber]  # USD / audio-hour

    pipeline = LFUCGPipeline(
        output_dir=str(output_dir),
        transcriber=args.transcriber,
        verbose=False,
    )
    pipeline.rag_enabled = not args.no_rag
    pipeline.scribe_seconds = 0.0
    pipeline.scribe_credits_exhausted = False
    # Fast, small-audio downloads for BOTH transcribers (resolution is
    # discarded; only the 48kbps audio is transcribed).
    if hasattr(pipeline.source, "prefer_small_audio_format"):
        pipeline.source.prefer_small_audio_format = True

    def clip_audio_hours(clip_id: int, scribe_delta_secs: float) -> float:
        """Audio-hours billed for this clip. Scribe reports exact duration;
        for Whisper, measure the downloaded mp3."""
        if args.transcriber == "elevenlabs" and scribe_delta_secs > 0:
            return scribe_delta_secs / 3600.0
        cdir = output_dir / "clips" / str(clip_id)
        mp3s = [f for f in cdir.glob("*_audio.mp3")] if cdir.is_dir() else []
        if mp3s:
            dur = pipeline.get_audio_duration(mp3s[0])
            if dur:
                return dur / 3600.0
        return 0.0

    ledger = open(args.ledger, "a", encoding="utf-8")
    done = 0
    ok = 0
    failed = 0
    prev_secs = 0.0
    cum_hours = 0.0
    t_start = time.time()

    for cid, date, title in queue:
        cum_cost = cum_hours * RATE
        if cum_hours >= args.max_hours:
            print(f"[wave] STOP: hit {args.max_hours}h audio cap ({cum_hours:.2f}h)")
            break
        if args.max_cost and cum_cost >= args.max_cost:
            print(f"[wave] STOP: hit ${args.max_cost} cost cap (~${cum_cost:.2f})")
            break
        if args.max_clips and done >= args.max_clips:
            print(f"[wave] STOP: hit {args.max_clips}-clip cap")
            break
        if pipeline.scribe_credits_exhausted:
            print("[wave] STOP: ElevenLabs credits exhausted")
            break

        t0 = time.time()
        try:
            success = pipeline.process_clip(cid)
        except Exception as e:  # never let one bad clip kill the run
            success = False
            print(f"[wave] clip {cid} EXCEPTION: {type(e).__name__}: {e}")

        scribe_delta = pipeline.scribe_seconds - prev_secs
        prev_secs = pipeline.scribe_seconds
        clip_hours = clip_audio_hours(cid, scribe_delta) if success else 0.0
        cum_hours += clip_hours
        done += 1
        ok += 1 if success else 0
        failed += 0 if success else 1
        rec = {
            "clip_id": cid,
            "date": date,
            "title": title,
            "transcriber": args.transcriber,
            "ok": bool(success),
            "clip_audio_min": round(clip_hours * 60.0, 1),
            "cum_scribe_hours": round(cum_hours, 3),
            "est_cost_usd": round(cum_hours * RATE, 2),
            "wall_secs": round(time.time() - t0, 1),
        }
        ledger.write(json.dumps(rec) + "\n")
        ledger.flush()
        # Exhaustion shows up as a failed clip — surface it immediately.
        if pipeline.scribe_credits_exhausted:
            print("[wave] STOP: ElevenLabs credits exhausted")
            break
        print(f"[wave] {done}/{len(queue)} clip {cid} "
              f"{'OK ' if success else 'FAIL'} "
              f"{rec['clip_audio_min']:.0f}min  "
              f"cum={cum_hours:.2f}h (~${rec['est_cost_usd']:.2f})  {title[:50]}")

    ledger.close()
    elapsed = time.time() - t_start
    print(f"\n[wave] processed {done} clips ({ok} ok, {failed} failed) in "
          f"{elapsed/60:.1f} min; {cum_hours:.2f} audio-hours via {args.transcriber} "
          f"(~${cum_hours*RATE:.2f})"
          + ("  [EL CREDITS EXHAUSTED]" if pipeline.scribe_credits_exhausted else ""))

    if ok and not args.skip_search_rebuild:
        print("[wave] rebuilding search.db ...")
        try:
            from scripts import build_search_db
            build_search_db.main(["--output-dir", str(output_dir)])
            print("[wave] search.db rebuilt")
        except Exception as e:
            print(f"[wave] search.db rebuild failed: {type(e).__name__}: {e} "
                  "(run main.py --build-search-db manually)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
