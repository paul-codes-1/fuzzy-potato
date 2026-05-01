#!/usr/bin/env python3
"""One-shot backfill: pull Granicus VTT for already-processed clips.

Two modes per clip, decided automatically:

1. **Speaker-enrich** — clip has Whisper transcript_segments.json. We
   download VTT, align speakers onto the existing Whisper segments, and
   rewrite the segments file in place. transcript_source becomes
   "whisper-1+vtt-speakers".

2. **Placeholder** — clip has no Whisper transcript at all. We download
   VTT, render it as transcript text + segments, and write the standard
   transcript files. transcript_source becomes "granicus_vtt"; a future
   Whisper pass replaces it.

Idempotent: skips clips whose transcript_source already matches the
target state. Safe to re-run after partial failures.

Usage:
    uv run python scripts/backfill_captions.py --dry-run
    uv run python scripts/backfill_captions.py
    uv run python scripts/backfill_captions.py --clip 6757
    uv run python scripts/backfill_captions.py --max 50

After a successful backfill, re-embed affected clips:
    uv run python -m rag.ingest --clip <id>     # per-clip
    uv run python main.py --rebuild-rag         # all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

# Allow imports from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from granicus_captions import (  # noqa: E402
    align_speakers_to_segments,
    download_vtt,
    parse_vtt,
    speakers_for_segments,
    vtt_to_transcript_segments,
    vtt_to_transcript_text,
)


def clip_url(clip_id: int, host: str, view_id: int) -> str:
    return f"https://{host}/player/clip/{clip_id}?view_id={view_id}&redirect=true"


def load_metadata(clip_dir: Path) -> Optional[dict]:
    metadata_path = clip_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_metadata(clip_dir: Path, metadata: dict) -> None:
    (clip_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


def discover_clips(output_dir: Path, only: Optional[int]) -> List[int]:
    clips_dir = output_dir / "clips"
    if not clips_dir.exists():
        return []
    if only is not None:
        return [only]
    ids: List[int] = []
    for p in clips_dir.iterdir():
        if p.is_dir() and p.name.isdigit():
            ids.append(int(p.name))
    ids.sort(reverse=True)  # newest first
    return ids


def get_or_fetch_vtt(clip_dir: Path, granicus_url: str, force: bool) -> Optional[Path]:
    vtt_path = clip_dir / "captions.vtt"
    if vtt_path.exists() and not force:
        return vtt_path
    return download_vtt(granicus_url, vtt_path)


def backfill_one(
    clip_id: int,
    output_dir: Path,
    host: str,
    view_id: int,
    *,
    dry_run: bool,
    force: bool,
) -> str:
    """Backfill a single clip. Returns a one-word status:
    'enriched', 'placeholder', 'skipped', 'no-vtt', 'no-meta', 'error'.
    """
    clip_dir = output_dir / "clips" / str(clip_id)
    metadata = load_metadata(clip_dir)
    if metadata is None:
        return "no-meta"

    files = metadata.get("files", {}) or {}
    segments_file = files.get("transcript_segments")
    transcript_file = files.get("transcript")
    audio_file = files.get("audio")
    current_source = metadata.get("transcript_source")

    # A segments file produced by a previous placeholder run is NOT the
    # same as a Whisper segments file — re-enriching it would just align
    # VTT speakers against VTT cues. The reliable signal that Whisper
    # actually ran is `metadata.models` being populated (set only by
    # transcribe_audio). Without it, treat any segments/transcript file
    # as VTT-derived and regenerate cleanly under --force.
    has_real_whisper_run = bool(metadata.get("models"))
    has_whisper_segments = (
        bool(segments_file and (clip_dir / segments_file).exists())
        and has_real_whisper_run
    )
    has_any_transcript = (
        bool(transcript_file and (clip_dir / transcript_file).exists())
        and has_real_whisper_run
    )

    # Idempotence guards.
    if has_whisper_segments and current_source == "whisper-1+vtt-speakers" and not force:
        return "skipped"
    if not has_whisper_segments and current_source == "granicus_vtt" and not force:
        return "skipped"

    granicus_url = metadata.get("url") or clip_url(clip_id, host, view_id)
    if dry_run:
        # Don't actually network-request in dry-run; just predict the action.
        if has_whisper_segments:
            return "would-enrich"
        if not has_any_transcript:
            return "would-placeholder"
        # transcript exists but no segments — skip (likely an old clip
        # without timestamped segments; needs --update-transcripts first).
        return "skipped"

    vtt_path = get_or_fetch_vtt(clip_dir, granicus_url, force)
    if not vtt_path:
        return "no-vtt"

    try:
        vtt_text = vtt_path.read_text(encoding="utf-8")
    except OSError:
        return "error"
    cue_segments, turns = parse_vtt(vtt_text)
    if not cue_segments:
        return "no-vtt"

    if has_whisper_segments:
        segments_path = clip_dir / segments_file
        try:
            whisper_segments = json.loads(segments_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "error"
        enriched = align_speakers_to_segments(whisper_segments, turns)
        speakers = speakers_for_segments(enriched)
        segments_path.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
        files["captions_vtt"] = vtt_path.name
        metadata["files"] = files
        metadata["transcript_source"] = "whisper-1+vtt-speakers"
        metadata["speakers"] = speakers
        save_metadata(clip_dir, metadata)
        return "enriched"

    if has_any_transcript:
        # Has transcript text but no segments — not safe to backfill
        # speaker labels (no timestamps to align onto). Skip; user
        # should run --update-transcripts to get Whisper segments first.
        return "skipped"

    # Pure placeholder mode.
    placeholder_segments = vtt_to_transcript_segments(cue_segments)
    placeholder_text = vtt_to_transcript_text(cue_segments)
    speakers = speakers_for_segments(placeholder_segments)

    # Need an audio_stem to derive consistent filenames. Fall back to
    # clip-id-based names if no audio file is recorded.
    if audio_file:
        audio_stem = Path(audio_file).stem
    else:
        audio_stem = f"clip_{clip_id}"
    transcript_filename = f"transcript_{audio_stem}.txt"
    segments_filename = f"transcript_{audio_stem}_segments.json"

    (clip_dir / transcript_filename).write_text(placeholder_text, encoding="utf-8")
    (clip_dir / segments_filename).write_text(
        json.dumps(placeholder_segments, indent=2), encoding="utf-8"
    )

    files["transcript"] = transcript_filename
    files["transcript_segments"] = segments_filename
    files["captions_vtt"] = vtt_path.name
    metadata["files"] = files
    metadata["transcript_source"] = "granicus_vtt"
    metadata["transcript_words"] = len(placeholder_text.split())
    metadata["speakers"] = speakers
    save_metadata(clip_dir, metadata)
    return "placeholder"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="./lfucg_output")
    parser.add_argument("--clip", type=int, help="Backfill only this clip ID")
    parser.add_argument("--max", type=int, default=0, help="Cap on clips processed")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report planned actions without downloading or writing")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch VTT and rewrite even if already present")
    parser.add_argument("--host", default=os.getenv("GRANICUS_HOST", "lfucg.granicus.com"))
    parser.add_argument("--view-id", type=int, default=int(os.getenv("GRANICUS_VIEW_ID", "1")))
    parser.add_argument("--workers", type=int, default=1,
                        help="Concurrent yt-dlp downloads (default 1; safe up to ~8)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    clips = discover_clips(output_dir, args.clip)
    if not clips:
        print("No clips found.")
        return 1

    if args.max:
        clips = clips[:args.max]

    counts: dict = {}
    print_lock = threading.Lock()

    def run_one(cid: int) -> str:
        return backfill_one(
            cid, output_dir, args.host, args.view_id,
            dry_run=args.dry_run, force=args.force,
        )

    if args.workers <= 1:
        for cid in clips:
            status = run_one(cid)
            counts[status] = counts.get(status, 0) + 1
            print(f"  clip {cid}: {status}", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(run_one, cid): cid for cid in clips}
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    status = fut.result()
                except Exception as e:
                    status = f"error:{type(e).__name__}"
                counts[status] = counts.get(status, 0) + 1
                with print_lock:
                    print(f"  clip {cid}: {status}", flush=True)

    print("\nSummary:")
    for status, n in sorted(counts.items()):
        print(f"  {status}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
