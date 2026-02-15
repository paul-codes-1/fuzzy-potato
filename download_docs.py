#!/usr/bin/env python3
"""
Download agendas and minutes for all available clips that don't have them yet.
Reads clip IDs from available_clips.json. Does NOT download audio, transcribe, or summarize.

Usage:
    python download_docs.py                  # All clips missing agenda or minutes
    python download_docs.py --max 20         # Limit to 20 clips
    python download_docs.py --force          # Re-download even if files exist
    python download_docs.py --agenda-only    # Only download agendas
    python download_docs.py --minutes-only   # Only download minutes
"""

import json
import argparse
from pathlib import Path
from datetime import datetime

from main import LFUCGPipeline


def load_available_clips(output_dir: Path):
    """Load clip list from available_clips.json."""
    path = output_dir / "available_clips.json"
    if not path.exists():
        print(f"Error: {path} not found. Run probe_clips.py first.")
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("clips", [])


def find_clips_missing_docs(output_dir: Path, agenda_only: bool = False, minutes_only: bool = False):
    """Find available clips that are missing agenda or minutes."""
    clips_dir = output_dir / "clips"
    available = load_available_clips(output_dir)
    if not available:
        return []

    results = []
    for clip_info in available:
        clip_id = clip_info["clip_id"]
        title = clip_info.get("title")
        clip_dir = clips_dir / str(clip_id)

        # Load existing metadata if present
        meta_path = clip_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
        else:
            meta = None

        # Check what files already exist (metadata + filesystem)
        files = meta.get("files", {}) if meta else {}
        has_agenda_meta = bool(files.get("agenda_txt") or files.get("agenda_pdf"))
        has_minutes_meta = bool(files.get("minutes_txt") or files.get("minutes_pdf") or files.get("minutes_html"))

        # Also check filesystem directly (covers clips without metadata)
        has_agenda_fs = bool(list(clip_dir.glob("*agenda*.txt"))) if clip_dir.exists() else False
        has_minutes_fs = bool(list(clip_dir.glob("*minutes*.txt"))) if clip_dir.exists() else False

        has_agenda = has_agenda_meta or has_agenda_fs
        has_minutes = has_minutes_meta or has_minutes_fs

        need_agenda = not has_agenda and not minutes_only
        need_minutes = not has_minutes and not agenda_only

        if need_agenda or need_minutes:
            results.append({
                "clip_id": clip_id,
                "clip_dir": clip_dir,
                "title": (meta.get("title") if meta else None) or title,
                "date": meta.get("date") if meta else None,
                "need_agenda": need_agenda,
                "need_minutes": need_minutes,
                "metadata": meta,
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Download agendas/minutes for all available clips")
    parser.add_argument("--max", type=int, default=0, help="Max clips to process (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-download even if files exist")
    parser.add_argument("--agenda-only", action="store_true", help="Only download agendas")
    parser.add_argument("--minutes-only", action="store_true", help="Only download minutes")
    parser.add_argument("--output-dir", default="./lfucg_output", help="Output directory")
    parser.add_argument("--quiet", action="store_true", help="Reduce output")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    # Find clips needing docs
    clips = find_clips_missing_docs(output_dir, args.agenda_only, args.minutes_only)

    if not clips:
        print("All available clips already have agenda and minutes.")
        return

    if args.max:
        clips = clips[:args.max]

    print(f"Found {len(clips)} clips needing documents (out of {len(load_available_clips(output_dir))} available)")

    # Initialize pipeline (only needs requests/pdfplumber/etc, but constructor requires OpenAI key)
    pipeline = LFUCGPipeline(
        output_dir=args.output_dir,
        verbose=not args.quiet,
        force_reprocess=args.force,
    )

    agenda_downloaded = 0
    minutes_downloaded = 0
    errors = 0

    for i, clip in enumerate(clips, 1):
        clip_id = clip["clip_id"]
        clip_dir = clip["clip_dir"]
        title = clip["title"]
        date = clip["date"]
        need_agenda = clip["need_agenda"]
        need_minutes = clip["need_minutes"]

        # Ensure clip directory exists
        clip_dir.mkdir(parents=True, exist_ok=True)

        parts = []
        if need_agenda:
            parts.append("agenda")
        if need_minutes:
            parts.append("minutes")

        print(f"\n[{i}/{len(clips)}] Clip {clip_id}: {title} — downloading {', '.join(parts)}")

        # Load or create metadata
        meta = clip["metadata"]
        if meta is None:
            meta = {
                "clip_id": clip_id,
                "url": pipeline.clip_url(clip_id),
                "title": title,
                "files": {},
            }
        files = meta.get("files", {})
        updated = False

        # Download agenda
        if need_agenda:
            try:
                result = pipeline.download_agenda(clip_id, clip_dir, title=title, date=date)
                if result["pdf_file"]:
                    files["agenda_pdf"] = result["pdf_file"]
                    updated = True
                    agenda_downloaded += 1
                if result["txt_file"]:
                    files["agenda_txt"] = result["txt_file"]
                    updated = True
            except Exception as e:
                print(f"  ERROR downloading agenda: {e}")
                errors += 1

        # Download minutes
        if need_minutes:
            try:
                result = pipeline.download_minutes(clip_id, clip_dir, title=title, date=date)
                if result["pdf_file"]:
                    files["minutes_pdf"] = result["pdf_file"]
                    updated = True
                    minutes_downloaded += 1
                if result["html_file"]:
                    files["minutes_html"] = result["html_file"]
                    updated = True
                if result["txt_file"]:
                    files["minutes_txt"] = result["txt_file"]
                    updated = True
            except Exception as e:
                print(f"  ERROR downloading minutes: {e}")
                errors += 1

        # Update metadata.json
        if updated:
            meta["files"] = files
            meta["docs_updated_at"] = datetime.now().isoformat()
            with open(clip_dir / "metadata.json", "w") as f:
                json.dump(meta, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Done! Agendas downloaded: {agenda_downloaded}, Minutes downloaded: {minutes_downloaded}")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    main()
