#!/usr/bin/env python3
"""Local Whisper backfill for clips with NO transcript at all.

Runs on a Mac (Apple Silicon) with the ``mlx_whisper`` CLI installed
(``uv tool install mlx-whisper``). For each backlog clip it downloads the
audio from Granicus (same yt-dlp settings as the pipeline), transcribes it
locally with Whisper large-v3-turbo (free, ~60x realtime on an M4 Max),
QA-gates the result against Whisper's known repetition-loop failure, stages
pipeline-identical artifacts, and pushes them to the production box followed
by the standard finalize steps (RAG re-ingest, index/search rebuild, API
reload, S3 sync, CloudFront invalidation).

Scope: clips whose metadata has NO ``files.transcript`` and NO
``transcript_source`` — i.e. no transcript of any kind. VTT-placeholder
clips (``transcript_source == "granicus_vtt"``) are intentionally NOT
touched; upgrading those is a separate speaker-enrichment-aware pass.

Backfilled clips get ``transcript_source = "whisper-large-v3-local"``
(honest engine attribution — seo.py and MeetingDetail.jsx carry a matching
disclosure branch) and the real model recorded in ``models.transcribe``.

Heads-up on downstream spend: the box's nightly ``summaries_cron.sh`` runs
``--upgrade-summaries``, which picks up any clip that has a transcript but
no ``extracted_facts.json``. Every pushed clip therefore auto-generates
facts + summary that night at ~$0.10-0.15/clip (GPT-4o + Claude Haiku).
Size your ``push`` batches to the nightly spend you're comfortable with.

Usage (from the repo root; plain python3, stdlib only):
  python3 scripts/local_whisper_backfill.py census            # build backlog.json from the box
  python3 scripts/local_whisper_backfill.py run --max 10      # download + transcribe + stage
  python3 scripts/local_whisper_backfill.py run --max 0       # no limit (whole backlog)
  python3 scripts/local_whisper_backfill.py push              # rsync staged clips + finalize
  python3 scripts/local_whisper_backfill.py push --no-finalize
  python3 scripts/local_whisper_backfill.py status

State lives in ~/lt/.whisper-backfill/lfucg/ (backlog.json, state.json,
work/ scratch, staged/ ready-to-push artifacts). Everything is resumable:
re-running ``run`` skips staged/pushed/failed clips (``--retry-failed`` to
retry failures).
"""

from __future__ import annotations

import argparse
import fcntl
import itertools
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HOST = "lfucg-meetings"
REMOTE_REPO = "/opt/fuzzy-potato"
REMOTE_CLIPS = f"{REMOTE_REPO}/lfucg_output/clips"
WORKROOT = Path.home() / "lt" / ".whisper-backfill" / "lfucg"
MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
TRANSCRIPT_SOURCE = "whisper-large-v3-local"
MODELS_TRANSCRIBE = "whisper-large-v3-turbo (mlx, local)"

DOWNLOAD_TIMEOUT = 3600
WHISPER_TIMEOUT = 7200

# QA thresholds (the repetition-loop gate is the one that matters: a looped
# transcript looks valid and would sail into ChromaDB otherwise)
MAX_CONSECUTIVE_REPEATS = 8
DOMINANCE_FRACTION = 0.30
DOMINANCE_MIN_SEGMENTS = 40
MIN_WORDS = 50
TAIL_GAP_FAIL_FRACTION = 0.20
TAIL_GAP_FAIL_SECONDS = 600

# Remote census: emit the no-transcript backlog as JSON on stdout.
CENSUS_REMOTE_SCRIPT = r"""
import json, glob, os
out = []
for d in sorted(glob.glob("REMOTE_CLIPS/*/")):
    cid = os.path.basename(d.rstrip("/"))
    if not cid.isdigit():
        continue
    mp = os.path.join(d, "metadata.json")
    if not os.path.exists(mp):
        continue
    try:
        m = json.load(open(mp))
    except Exception:
        continue
    if m.get("transcript_source") or m.get("files", {}).get("transcript"):
        continue
    out.append({
        "clip_id": int(cid),
        "url": m.get("url"),
        "date": m.get("date"),
        "title": m.get("title"),
        "meeting_body": m.get("meeting_body"),
    })
print(json.dumps(out))
""".replace("REMOTE_CLIPS", REMOTE_CLIPS)


def sanitize_filename(title: str) -> str:
    """Verbatim copy of LFUCGPipeline.sanitize_filename (main.py) so the
    transcript filename stem matches what the pipeline would produce."""
    sanitized = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", title)
    sanitized = re.sub(r"[\s\-]+", "_", sanitized)
    sanitized = re.sub(r"[^\w.]", "", sanitized)
    sanitized = sanitized.strip("_")
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized or "clip"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def load_state() -> dict:
    return load_json(WORKROOT / "state.json", {"clips": {}})


def save_state(state: dict) -> None:
    save_json(WORKROOT / "state.json", state)


def set_clip_state(state: dict, clip_id: int, status: str, **extra) -> None:
    """Per-clip locked read-modify-write so concurrent `run` and `push`
    processes never clobber each other's state updates. The caller's
    in-memory copy is updated too (its skip logic reads it)."""
    entry = {"status": status, "at": now_iso(), **extra}
    state["clips"][str(clip_id)] = entry
    with open(WORKROOT / "state.lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        fresh = load_json(WORKROOT / "state.json", {"clips": {}})
        fresh["clips"][str(clip_id)] = entry
        save_json(WORKROOT / "state.json", fresh)


def ffprobe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def qa_check(segments: list[dict], text: str, duration: float | None) -> tuple[bool, str]:
    """Gate against the failure modes observed in real runs. Returns
    (ok, reason). A warning-grade tail gap is reported as ok with a note."""
    words = len(text.split())
    if words < MIN_WORDS:
        return False, f"too_short:{words}_words"
    if not segments:
        return False, "no_segments"

    norm = [re.sub(r"\s+", " ", s.get("text", "").strip().lower()) for s in segments]

    run, best_run, best_text = 1, 1, ""
    for a, b in zip(norm, norm[1:]):
        if b and a == b:
            run += 1
            if run > best_run:
                best_run, best_text = run, b
        else:
            run = 1
    if best_run >= MAX_CONSECUTIVE_REPEATS:
        return False, f"repetition_loop:{best_run}x_{best_text[:40]!r}"

    counts = Counter(t for t in norm if t)
    if len(segments) >= DOMINANCE_MIN_SEGMENTS:
        top_text, top_n = counts.most_common(1)[0]
        if top_n / len(segments) > DOMINANCE_FRACTION:
            return False, f"dominant_repeat:{top_n}/{len(segments)}_{top_text[:40]!r}"

    if duration:
        last_end = max((s.get("end", 0) for s in segments), default=0)
        gap = duration - last_end
        if gap > TAIL_GAP_FAIL_SECONDS and gap / duration > TAIL_GAP_FAIL_FRACTION:
            return False, f"tail_gap:{gap:.0f}s_of_{duration:.0f}s"
        if gap > 300:
            return True, f"warn_tail_gap:{gap:.0f}s"
    return True, ""


def ssh(host: str, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, *args],
                          capture_output=True, text=True, **kw)


def cmd_census(host: str) -> None:
    log(f"Querying {host} for clips with no transcript at all...")
    res = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", host, "python3", "-"],
        input=CENSUS_REMOTE_SCRIPT, capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        sys.exit(f"census failed: {res.stderr.strip()}")
    backlog = json.loads(res.stdout)
    # Newest first — recent meetings are the most-queried
    backlog.sort(key=lambda c: (c.get("date") or "0000", c["clip_id"]), reverse=True)
    save_json(WORKROOT / "backlog.json", backlog)
    dated = sum(1 for c in backlog if c.get("date"))
    log(f"Backlog: {len(backlog)} clips ({dated} with dates) -> {WORKROOT / 'backlog.json'}")


def download_audio(clip: dict, workdir: Path) -> Path | None:
    """Mirror sources/granicus.py download settings: 48kbps 22kHz mono mp3,
    audio-only/worst rendition, parallel HLS fragments."""
    clip_id = clip["clip_id"]
    url = clip.get("url") or f"https://lfucg.granicus.com/player/clip/{clip_id}?view_id=14&redirect=true"
    title, date = clip.get("title"), clip.get("date")
    stem = f"{date}_{sanitize_filename(title)}_audio" if title and date else (
        f"{sanitize_filename(title)}_audio" if title else f"clip_{clip_id}_audio")
    out = workdir / f"{stem}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out
    res = subprocess.run(
        ["yt-dlp", "--no-progress", "--newline",
         "-x", "--audio-format", "mp3", "--audio-quality", "48k",
         "--postprocessor-args", "ffmpeg:-ar 22050 -ac 1",
         "-f", "bestaudio/worst", "--concurrent-fragments", "5",
         "-o", str(out), url],
        capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT,
    )
    if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise RuntimeError("download_failed: " + " | ".join(tail))
    return out


# Whisper very long recordings in pieces: a single ~6h file balloons MLX's
# unified-memory allocation enough to take down the whole process group
# (observed twice on clip 2293, 348 min). mp3 splits cleanly on frame
# boundaries with -c copy; segments are offset-merged by measured chunk
# duration. With condition-on-previous-text off, each 30s window is
# independent anyway, so a hard cut costs at most a word at each seam.
CHUNK_SECONDS = 7200


def _run_mlx_whisper(audio: Path, out_dir: Path) -> dict:
    res = subprocess.run(
        ["mlx_whisper", str(audio),
         "--model", MLX_MODEL, "--language", "en",
         "--condition-on-previous-text", "False",
         "--hallucination-silence-threshold", "2",
         "--output-dir", str(out_dir), "--output-format", "json",
         "--verbose", "False"],
        capture_output=True, text=True, timeout=WHISPER_TIMEOUT,
    )
    result_path = out_dir / f"{audio.stem}.json"
    if res.returncode != 0 or not result_path.exists():
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise RuntimeError("whisper_failed: " + " | ".join(tail))
    return json.loads(result_path.read_text())


def _transcribe_chunked(audio: Path, workdir: Path, result_path: Path) -> dict:
    chunk_dir = workdir / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", str(audio),
         "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-c", "copy",
         str(chunk_dir / "chunk_%03d.mp3")],
        capture_output=True, text=True, timeout=1800,
    )
    chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
    if res.returncode != 0 or not chunks:
        raise RuntimeError("chunk_split_failed: " + (res.stderr or "").strip()[-200:])
    segments: list[dict] = []
    texts: list[str] = []
    offset = 0.0
    for ch in chunks:
        j = _run_mlx_whisper(ch, chunk_dir)
        texts.append((j.get("text") or "").strip())
        for s in j.get("segments", []):
            segments.append({
                "start": s["start"] + offset, "end": s["end"] + offset,
                "text": s.get("text", ""),
                "no_speech_prob": s.get("no_speech_prob", 0.0),
            })
        offset += ffprobe_duration(ch) or CHUNK_SECONDS
    result = {"text": " ".join(t for t in texts if t), "segments": segments}
    result_path.write_text(json.dumps(result))
    shutil.rmtree(chunk_dir, ignore_errors=True)
    return result


def transcribe(audio: Path, workdir: Path, duration: float | None) -> dict:
    result_path = workdir / f"{audio.stem}.json"
    if result_path.exists() and result_path.stat().st_size > 0:
        # Retry after a QA failure: reuse the existing Whisper output and
        # let the silence-hallucination repair re-run on it.
        return json.loads(result_path.read_text())
    if duration and duration > CHUNK_SECONDS * 1.25:
        return _transcribe_chunked(audio, workdir, result_path)
    return _run_mlx_whisper(audio, workdir)


def region_mean_volume(audio: Path, start: float, end: float) -> float | None:
    """Mean volume (dB) of an audio region via ffmpeg volumedetect."""
    dur = max(0.5, end - start)
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(max(0.0, start)), "-t", str(dur),
             "-i", str(audio), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.SubprocessError:
        return None
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", res.stderr)
    return float(m.group(1)) if m else None


SILENCE_REPAIR_MIN_RUN = 5
SILENCE_MEAN_DB = -38.0
NO_SPEECH_PROB_CUTOFF = 0.6
UNCONDITIONAL_RUN = 8
MAX_LOUD_RUN_SPAN_FRACTION = 0.15


def repair_silence_hallucinations(raw_segments: list[dict], audio: Path,
                                  duration: float | None) -> tuple[list[dict], list[str]]:
    """Whisper hallucinates filler ('thank you.', 'let's go.') over dead air
    and hold music — Granicus recordings are full of both (pre-roll, recess
    stings, executive sessions). Find runs of >=5 identical segments and drop
    them (keeping the first, which may border real speech) when:
      - the region is quiet (volumedetect) or Whisper's own no_speech_prob is
        high — the silence case; or
      - the run is >=8 segments AND spans <15% of the clip — the hold-music
        case (loud, nsp=0, but a short burst; observed 12s-6min in practice).
    A long loud run (like the content-eating loop that once replaced 30% of
    a clip) is deliberately NOT repaired — it falls through to the QA gate
    and fails the clip for human inspection."""
    notes: list[str] = []
    out: list[dict] = []
    norm = [re.sub(r"\s+", " ", (s.get("text") or "").strip().lower()) for s in raw_segments]
    i, n = 0, len(raw_segments)
    while i < n:
        j = i + 1
        while j < n and norm[i] and norm[j] == norm[i]:
            j += 1
        run = j - i
        if run >= SILENCE_REPAIR_MIN_RUN:
            probs = sorted(s.get("no_speech_prob", 0.0) for s in raw_segments[i:j])
            median_prob = probs[run // 2]
            span = raw_segments[j - 1]["end"] - raw_segments[i]["start"]
            vol = region_mean_volume(audio, raw_segments[i]["start"], raw_segments[j - 1]["end"])
            non_speech = (vol is not None and vol <= SILENCE_MEAN_DB) or median_prob >= NO_SPEECH_PROB_CUTOFF
            short_burst = (run >= UNCONDITIONAL_RUN and duration
                           and span / duration < MAX_LOUD_RUN_SPAN_FRACTION)
            if non_speech or short_burst:
                out.append(raw_segments[i])
                notes.append(
                    f"dropped {run - 1}x{norm[i][:30]!r}@{raw_segments[i]['start']:.0f}s"
                    f"(span={span:.0f}s,vol={vol if vol is not None else '?'}dB,nsp={median_prob:.2f})")
                i = j
                continue
        out.extend(raw_segments[i:j])
        i = j
    return out, notes


def _prepare_clip(host: str, clip: dict) -> dict:
    """Download-worker stage (runs in a thread pool): fetch fresh metadata
    from the box (guards against a cron race) + download the audio.
    Returns a ready-to-whisper dict, or {"skip": reason}. Raises on failure."""
    cid = clip["clip_id"]
    workdir = WORKROOT / "work" / str(cid)
    staged = WORKROOT / "staged" / str(cid)
    workdir.mkdir(parents=True, exist_ok=True)
    staged.mkdir(parents=True, exist_ok=True)

    meta_path = staged / "metadata.json"
    scp = subprocess.run(
        ["scp", "-q", f"{host}:{REMOTE_CLIPS}/{cid}/metadata.json", str(meta_path)],
        capture_output=True, text=True, timeout=60,
    )
    if scp.returncode != 0:
        raise RuntimeError(f"metadata_fetch_failed: {scp.stderr.strip()}")
    meta = json.loads(meta_path.read_text())
    if meta.get("transcript_source") or meta.get("files", {}).get("transcript"):
        return {"clip": clip, "skip": "box_already_has_transcript"}

    audio = download_audio(clip, workdir)
    return {
        "clip": clip, "meta": meta, "meta_path": meta_path, "audio": audio,
        "workdir": workdir, "staged": staged,
        "duration": ffprobe_duration(audio), "t0": datetime.now(),
    }


def cmd_run(host: str, max_clips: int, keep_audio: bool, retry_failed: bool,
            parallel: int) -> None:
    for tool in ("yt-dlp", "mlx_whisper", "ffprobe", "ssh", "scp", "rsync"):
        if not shutil.which(tool):
            sys.exit(f"required tool not on PATH: {tool}")
    backlog = load_json(WORKROOT / "backlog.json", None)
    if backlog is None:
        sys.exit("no backlog.json — run `census` first")
    state = load_state()
    skip_statuses = {"staged", "pushed", "skipped"} | (set() if retry_failed else {"failed"})

    todo = [c for c in backlog if state["clips"].get(str(c["clip_id"]), {}).get("status") not in skip_statuses]
    if max_clips > 0:
        todo = todo[:max_clips]
    parallel = max(1, min(parallel, 6))  # be polite to Granicus (each worker uses 5 HLS fragments)
    log(f"{len(todo)} clips to process (of {len(backlog)} backlog), "
        f"{parallel} parallel downloads feeding one Whisper")

    done = failed = i = 0
    it = iter(todo)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_prepare_clip, host, c): c
                   for c in itertools.islice(it, parallel)}
        while futures:
            fut = next(as_completed(futures))
            clip = futures.pop(fut)
            nxt = next(it, None)
            if nxt is not None:
                futures[pool.submit(_prepare_clip, host, nxt)] = nxt

            i += 1
            cid = clip["clip_id"]
            label = f"{cid} ({clip.get('date')} {clip.get('title')})"
            try:
                prep = fut.result()
                if prep.get("skip"):
                    set_clip_state(state, cid, "skipped", reason=prep["skip"])
                    shutil.rmtree(WORKROOT / "staged" / str(cid), ignore_errors=True)
                    shutil.rmtree(WORKROOT / "work" / str(cid), ignore_errors=True)
                    log(f"[{i}/{len(todo)}] SKIP {label} — {prep['skip']}")
                    continue
                meta, meta_path = prep["meta"], prep["meta_path"]
                audio, workdir, staged = prep["audio"], prep["workdir"], prep["staged"]
                duration, t0 = prep["duration"], prep["t0"]

                log(f"[{i}/{len(todo)}] whisper {label} ({(duration or 0)/60:.0f} min audio)"
                    + (" [chunked]" if duration and duration > CHUNK_SECONDS * 1.25 else ""))
                result = transcribe(audio, workdir, duration)
                raw_segments, repair_notes = repair_silence_hallucinations(
                    result.get("segments", []), audio, duration)
                segments = [
                    {"start": s["start"], "end": s["end"], "text": s.get("text", "").strip()}
                    for s in raw_segments
                ]
                text = " ".join(s["text"] for s in segments if s["text"]).strip()
                if repair_notes:
                    log(f"[{i}/{len(todo)}] repaired {len(repair_notes)} hallucinated "
                        f"run(s): {'; '.join(repair_notes[:3])}"
                        f"{' …' if len(repair_notes) > 3 else ''}")

                ok, note = qa_check(segments, text, duration)
                if not ok:
                    raise RuntimeError(f"qa_failed: {note}")
                if repair_notes:
                    note = (note + " " if note else "") + f"repaired:{len(repair_notes)}_runs"

                stem = audio.stem  # {date}_{title}_audio
                (staged / f"transcript_{stem}.txt").write_text(text, encoding="utf-8")
                (staged / f"transcript_{stem}_segments.json").write_text(
                    json.dumps(segments, indent=2), encoding="utf-8")

                files = meta.setdefault("files", {})
                files["transcript"] = f"transcript_{stem}.txt"
                files["transcript_segments"] = f"transcript_{stem}_segments.json"
                meta["transcript_source"] = TRANSCRIPT_SOURCE
                meta["transcript_words"] = len(text.split())
                if not isinstance(meta.get("models"), dict):
                    meta["models"] = {}
                meta["models"]["transcribe"] = MODELS_TRANSCRIBE
                if not meta.get("audio_kept"):
                    meta["audio_kept"] = False
                elapsed = (datetime.now() - t0).total_seconds()
                meta["local_backfill"] = {
                    "engine": "mlx-whisper", "model": MLX_MODEL,
                    "at": now_iso(), "elapsed_seconds": round(elapsed, 1),
                }
                save_json(meta_path, meta)

                rtf = (duration / elapsed) if duration and elapsed else None
                set_clip_state(state, cid, "staged", words=meta["transcript_words"],
                               duration_min=round((duration or 0) / 60, 1),
                               elapsed_s=round(elapsed, 1), note=note or None)
                if not keep_audio:
                    shutil.rmtree(workdir, ignore_errors=True)
                done += 1
                log(f"[{i}/{len(todo)}] STAGED {cid}: {meta['transcript_words']:,} words, "
                    f"{elapsed:.0f}s{f' ({rtf:.0f}x realtime)' if rtf else ''}"
                    f"{' ' + note if note else ''}")
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError,
                    KeyError, OSError) as e:
                failed += 1
                set_clip_state(state, cid, "failed", reason=str(e)[:300])
                shutil.rmtree(WORKROOT / "staged" / str(cid), ignore_errors=True)
                log(f"[{i}/{len(todo)}] FAILED {label}: {str(e)[:200]}")

    log(f"run complete: {done} staged, {failed} failed. Next: `push` to ship staged clips.")


FINALIZE_TEMPLATE = r"""
set -euo pipefail
export AWS_PAGER=""
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd __REPO__
exec 9>/tmp/lfucg-pipeline.lock
flock -w 7200 9
set -a; [ -f .env ] && source .env; set +a
for id in __IDS__; do
  echo "==> rag.ingest --clip $id"
  uv run python -m rag.ingest --clip "$id"
done
echo "==> generate-index"
uv run python main.py --generate-index
# Full restart, NOT /admin/reload: repeated reloads after big ingest waves
# leak the old Chroma allocation (glibc arenas never return to the OS) —
# uvicorn crept to 12.7GB and globally OOM-froze the box on 2026-07-16.
# A restart is sub-second on the co-located box and resets RSS.
echo "==> restarting ${RAG_SERVICE:-lfucg-rag} (fresh RSS)"
sudo systemctl restart "${RAG_SERVICE:-lfucg-rag}"
echo "==> syncing per-clip data to S3"
bash deploy/lightsail/sync_data_s3.sh
if [ -n "${CLOUDFRONT_DISTRIBUTION_ID:-}" ]; then
  echo "==> CloudFront invalidation /data/*"
  aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths '/data/*' --query 'Invalidation.Id' --output text
fi
echo "==> finalize done"
"""


def cmd_push(host: str, finalize: bool) -> None:
    state = load_state()
    staged_ids = sorted(
        int(cid) for cid, s in state["clips"].items() if s.get("status") == "staged")
    if not staged_ids:
        sys.exit("nothing staged to push")
    log(f"pushing {len(staged_ids)} staged clips to {host}: {staged_ids}")

    for cid in staged_ids:
        src = WORKROOT / "staged" / str(cid)
        res = subprocess.run(
            ["rsync", "-a", f"{src}/", f"{host}:{REMOTE_CLIPS}/{cid}/"],
            capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0:
            sys.exit(f"rsync failed for clip {cid}: {res.stderr.strip()}")
    log("rsync complete")

    if not finalize:
        log("--no-finalize: artifacts are on the box but NOT ingested/reloaded/synced")
        return

    script = (FINALIZE_TEMPLATE
              .replace("__REPO__", REMOTE_REPO)
              .replace("__IDS__", " ".join(str(i) for i in staged_ids)))
    log("running finalize on the box (flock-guarded; RAG ingest + index + reload + S3)...")
    proc = subprocess.Popen(["ssh", host, "bash", "-s"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    out, _ = proc.communicate(script, timeout=7200)
    print(out)
    if proc.returncode != 0:
        sys.exit(f"finalize FAILED (exit {proc.returncode}) — clips are on the box but "
                 "may not be ingested; re-run push after fixing")
    for cid in staged_ids:
        set_clip_state(state, cid, "pushed",
                       **{k: v for k, v in state["clips"][str(cid)].items()
                          if k not in ("status", "at")})
    log(f"pushed + finalized {len(staged_ids)} clips")


def cmd_status() -> None:
    backlog = load_json(WORKROOT / "backlog.json", [])
    state = load_state()
    counts = Counter(s.get("status") for s in state["clips"].values())
    touched = set(state["clips"].keys())
    remaining = sum(1 for c in backlog if str(c["clip_id"]) not in touched)
    print(f"backlog: {len(backlog)} clips")
    for k in ("staged", "pushed", "failed", "skipped"):
        if counts.get(k):
            print(f"  {k}: {counts[k]}")
    print(f"  untouched: {remaining}")
    mins = sum(s.get("duration_min", 0) for s in state["clips"].values()
               if s.get("status") in ("staged", "pushed"))
    if mins:
        print(f"  audio transcribed so far: {mins/60:.1f} h")
    failures = [(cid, s.get("reason", "")) for cid, s in state["clips"].items()
                if s.get("status") == "failed"]
    for cid, reason in failures[:10]:
        print(f"  failed {cid}: {reason[:120]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["census", "run", "push", "status"])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--max", type=int, default=5, help="max clips per run (0 = no limit)")
    ap.add_argument("--parallel-downloads", type=int, default=3,
                    help="download workers prefetching ahead of the (sequential) Whisper stage")
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--no-finalize", action="store_true")
    args = ap.parse_args()

    WORKROOT.mkdir(parents=True, exist_ok=True)
    if args.command == "census":
        cmd_census(args.host)
    elif args.command == "run":
        cmd_run(args.host, args.max, args.keep_audio, args.retry_failed,
                args.parallel_downloads)
    elif args.command == "push":
        cmd_push(args.host, finalize=not args.no_finalize)
    else:
        cmd_status()


if __name__ == "__main__":
    main()
