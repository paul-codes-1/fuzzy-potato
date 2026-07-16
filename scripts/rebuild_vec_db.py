#!/usr/bin/env python3
"""Rebuild the sqlite-vec vector DB (vec.db) from the clips/ tree.

Phase-1 companion to the ChromaDB→sqlite-vec port (RAG_CAPACITY_PLAN.md):
re-chunks every clip via the SAME chunkers the incremental ingest uses
(rag.ingest.build_clip_chunks), embeds at ``RAG_EMBED_DIMS`` (default
1536; the capacity-plan rebuild uses 512), and writes a fresh
``vec.db``. The build is atomic and resumable:

- chunks land in ``vec.db.tmp``; the live ``vec.db`` (if any) keeps
  serving until the FULL rebuild completes, then ``os.replace()`` swaps
  it in — the same tmp-then-replace pattern as search.db.
- progress is checkpointed per clip in ``vec.db.rebuild_state.json``;
  re-running skips already-embedded clips. A dims change invalidates the
  checkpoint (fresh start).
- ``--max N`` caps this run for testing; the tmp DB is kept and the
  next run resumes (vec.db is only replaced once every clip is done).

It can also drain the box's ``pending_rag_ingest.txt`` ledger (clip ids
queued while Chroma ingest was suspended) straight into the LIVE
``vec.db`` via ``--from-ledger`` — the post-cutover incremental path.

Usage:
    uv run python scripts/rebuild_vec_db.py                    # full rebuild
    uv run python scripts/rebuild_vec_db.py --max 25           # test slice (resumable)
    uv run python scripts/rebuild_vec_db.py --dims 512         # capacity-plan rebuild
    uv run python scripts/rebuild_vec_db.py --from-ledger      # drain pending_rag_ingest.txt
    uv run python scripts/rebuild_vec_db.py --from-ledger /path/to/ledger.txt

Requires OPENAI_API_KEY (embeddings). Does NOT read or write the
ChromaDB store, rag_state.json, or search.db.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Allow imports from repo root (for `python scripts/rebuild_vec_db.py`).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from config import get_config  # noqa: E402
from rag.ingest import build_clip_chunks, store_chunks  # noqa: E402
from rag.vecstore import SqliteVecStore, embedding_dims, vec_db_path  # noqa: E402

# text-embedding-3-small pricing, $ per 1M tokens (2026-07).
EMBED_COST_PER_MTOK = 0.02

STATE_FILENAME = "vec.db.rebuild_state.json"
LEDGER_FILENAME = "pending_rag_ingest.txt"

# How long a ledger drain waits for the box's shared pipeline flock before
# giving up (an ingest cron catch-up run can hold it for a while).
LOCK_WAIT_SECONDS = 600


def pipeline_lock_path() -> str:
    """The same per-jurisdiction flock the box crons take
    (deploy/lightsail/crontab.txt: /usr/bin/flock -n /tmp/<slug>-pipeline.lock ...)."""
    return f"/tmp/{get_config().slug}-pipeline.lock"


def acquire_pipeline_lock(lock_path: str, timeout: int = LOCK_WAIT_SECONDS):
    """flock-with-wait on the crons' shared lock file.

    The ledger drain writes into the LIVE vec.db, so it must not overlap
    an ingest cron (which also writes vec.db and calls /admin/reload).
    Returns the open file handle — hold it for the life of the run.
    """
    import fcntl

    fh = open(lock_path, "w")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                raise SystemExit(
                    f"Could not acquire {lock_path} within {timeout}s — "
                    "an ingest cron is likely running; retry later."
                )
            time.sleep(5)

# Per-clip text artifacts that feed the chunkers (metadata.json "files" keys).
_TEXT_FILE_KEYS = ("summary_txt", "extracted_facts", "minutes_txt",
                   "agenda_txt", "transcript_segments")


def list_clip_ids(output_dir: Path) -> list[int]:
    clips_dir = output_dir / "clips"
    if not clips_dir.is_dir():
        return []
    ids = []
    for name in sorted(os.listdir(clips_dir)):
        if (clips_dir / name / "metadata.json").exists():
            try:
                ids.append(int(name))
            except ValueError:
                continue
    return ids


def estimate_cost(output_dir: Path, clip_ids: list[int]) -> tuple[int, float]:
    """Rough embed-cost estimate from artifact file sizes (chars/4 ≈ tokens).

    File-size based, so segments JSON overhead and chunk overlap roughly
    cancel out — treat as an order-of-magnitude figure, not an invoice.
    """
    total_chars = 0
    for cid in clip_ids:
        clip_dir = output_dir / "clips" / str(cid)
        meta_path = clip_dir / "metadata.json"
        try:
            with open(meta_path) as f:
                files = json.load(f).get("files", {})
        except (OSError, json.JSONDecodeError):
            continue
        for key in _TEXT_FILE_KEYS:
            name = files.get(key)
            if not name:
                continue
            try:
                total_chars += (clip_dir / name).stat().st_size
            except OSError:
                continue
    tokens = total_chars // 4
    return tokens, tokens / 1_000_000 * EMBED_COST_PER_MTOK


def load_state(state_path: Path, dims: int) -> set[int]:
    """Done-clip checkpoint; discarded when dims changed."""
    if not state_path.exists():
        return set()
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    if state.get("dims") != dims:
        print(f"  Checkpoint was built at {state.get('dims')} dims, "
              f"current is {dims} — starting fresh.")
        return set()
    return set(state.get("done_clips", []))


def save_state(state_path: Path, dims: int, done: set[int]) -> None:
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump({"dims": dims, "done_clips": sorted(done)}, f)
    os.replace(tmp, state_path)


def ingest_into(store: SqliteVecStore, output_dir: Path, clip_ids: list[int],
                openai_client, done: set[int] | None = None,
                state_path: Path | None = None, dims: int = 0) -> tuple[int, list[int]]:
    """Embed clip_ids into store. Returns (ingested_count, failed_ids)."""
    done = done if done is not None else set()
    failed: list[int] = []
    ingested = 0
    started = time.monotonic()
    for i, cid in enumerate(clip_ids):
        if cid in done:
            continue
        try:
            chunks = build_clip_chunks(cid, output_dir)
            if chunks is None:
                print(f"[{i + 1}/{len(clip_ids)}] Clip {cid}: no metadata.json, skipping")
            elif not chunks:
                # A clip whose artifacts regressed to nothing chunkable must
                # not keep serving its OLD chunks (matters on the ledger
                # path, which writes into the live vec.db; a no-op on the
                # fresh-rebuild path).
                store.delete_clip(cid)
                print(f"[{i + 1}/{len(clip_ids)}] Clip {cid}: 0 chunks "
                      "(cleared any existing)")
            else:
                # Delete-before-reingest: resume-safety if a prior run died
                # mid-clip (same invariant as rag.ingest.ingest_clip).
                store.delete_clip(cid)
                store_chunks(chunks, store, openai_client)
                print(f"[{i + 1}/{len(clip_ids)}] Clip {cid}: {len(chunks)} chunks")
            done.add(cid)
            ingested += 1
        except Exception as e:
            print(f"[{i + 1}/{len(clip_ids)}] Clip {cid}: ERROR: {e}")
            failed.append(cid)
        if state_path is not None:
            save_state(state_path, dims, done)
    elapsed = time.monotonic() - started
    print(f"Processed {ingested} clip(s) in {elapsed:.0f}s"
          + (f", {len(failed)} failed" if failed else ""))
    return ingested, failed


def run_rebuild(output_dir: Path, dims: int, max_clips: int | None,
                openai_client) -> int:
    final_path = Path(vec_db_path(str(output_dir)))
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    state_path = output_dir / STATE_FILENAME

    all_ids = list_clip_ids(output_dir)
    if not all_ids:
        print(f"No clips found under {output_dir}/clips — nothing to do.")
        return 1

    done = load_state(state_path, dims)
    if done and not tmp_path.exists():
        print("  Checkpoint exists but vec.db.tmp is gone — starting fresh.")
        done = set()
    if not done and tmp_path.exists():
        tmp_path.unlink()

    pending = [cid for cid in all_ids if cid not in done]
    targets = pending[: max_clips] if max_clips else pending

    tokens, cost = estimate_cost(output_dir, targets)
    print(f"Rebuilding vec.db at {dims} dims: {len(all_ids)} clips total, "
          f"{len(done)} already done, {len(targets)} this run.")
    print(f"Estimated embedding cost for this run: ~{tokens:,} tokens "
          f"≈ ${cost:.2f} (chars/4, file-size based)")

    store = SqliteVecStore(str(tmp_path), dims=dims)
    try:
        _, failed = ingest_into(store, output_dir, targets, openai_client,
                                done=done, state_path=state_path, dims=dims)
        remaining = [cid for cid in all_ids if cid not in done]
        stats = store.stats()
    finally:
        store.close()

    print(f"Store: {stats['total_chunks']} chunks / {stats['unique_clips']} clips, "
          f"{stats['db_bytes'] / 1e6:.0f} MB on disk")

    if remaining or failed:
        print(f"Partial build: {len(remaining)} clip(s) remaining"
              + (f" ({len(failed)} failed this run)" if failed else "")
              + f" — {tmp_path.name} kept, re-run to resume. vec.db untouched.")
        return 1 if failed else 0

    os.replace(tmp_path, final_path)
    state_path.unlink(missing_ok=True)
    print(f"Done. {final_path} swapped in atomically "
          f"(reload the API: POST /admin/reload or systemctl restart).")
    return 0


def run_ledger_drain(output_dir: Path, ledger_path: Path, dims: int,
                     openai_client) -> int:
    if not ledger_path.exists():
        print(f"No ledger at {ledger_path} — nothing to drain.")
        return 0

    ids: list[int] = []
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            cid = int(line)
        except ValueError:
            print(f"  Skipping non-numeric ledger line: {line!r}")
            continue
        if cid not in ids:
            ids.append(cid)

    if not ids:
        print(f"Ledger {ledger_path} is empty — nothing to drain.")
        ledger_path.unlink()
        return 0

    tokens, cost = estimate_cost(output_dir, ids)
    print(f"Draining {len(ids)} clip(s) from {ledger_path} into "
          f"{vec_db_path(str(output_dir))} at {dims} dims.")
    print(f"Estimated embedding cost: ~{tokens:,} tokens ≈ ${cost:.2f} "
          f"(chars/4, file-size based)")

    store = SqliteVecStore(vec_db_path(str(output_dir)), dims=dims)
    try:
        _, failed = ingest_into(store, output_dir, ids, openai_client)
        stats = store.stats()
    finally:
        store.close()

    print(f"Store: {stats['total_chunks']} chunks / {stats['unique_clips']} clips")

    if failed:
        tmp = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
        tmp.write_text("\n".join(str(cid) for cid in failed) + "\n")
        os.replace(tmp, ledger_path)
        print(f"{len(failed)} clip(s) failed — ledger rewritten with just those.")
        return 1

    ledger_path.unlink()
    print("Ledger drained and removed. Reload the API to serve the new chunks.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild (or incrementally drain into) the sqlite-vec vec.db")
    parser.add_argument("--output-dir",
                        default=os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output"),
                        help="Pipeline output dir (default: $LFUCG_OUTPUT_DIR or ./lfucg_output)")
    parser.add_argument("--max", type=int, default=None,
                        help="Cap clips processed this run (testing; build stays resumable)")
    parser.add_argument("--dims", type=int, default=None,
                        help="Embedding dimensions (overrides RAG_EMBED_DIMS; default 1536)")
    parser.add_argument("--from-ledger", nargs="?", const="", default=None,
                        metavar="PATH",
                        help="Drain a newline-separated clip-id ledger into the LIVE vec.db "
                             f"(default ledger: <output-dir>/{LEDGER_FILENAME})")
    args = parser.parse_args()

    if args.dims:
        # Thread through to embedding_dims() / SqliteVecStore / the OpenAI
        # dimensions param — one knob for the whole run.
        os.environ["RAG_EMBED_DIMS"] = str(args.dims)
    dims = embedding_dims()

    output_dir = Path(args.output_dir)
    from clients import get_openai

    openai_client = get_openai()

    if args.from_ledger is not None:
        ledger_path = Path(args.from_ledger) if args.from_ledger else output_dir / LEDGER_FILENAME
        # The drain writes into the LIVE vec.db — take the same flock the
        # box crons use so it can't overlap an ingest run. (The full
        # rebuild path writes only vec.db.tmp, so it doesn't need this.)
        lock_fh = acquire_pipeline_lock(pipeline_lock_path())
        try:
            return run_ledger_drain(output_dir, ledger_path, dims, openai_client)
        finally:
            lock_fh.close()

    return run_rebuild(output_dir, dims, args.max, openai_client)


if __name__ == "__main__":
    sys.exit(main())
