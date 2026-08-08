"""Stall-watchdog phase awareness in GranicusSource.download_audio.

yt-dlp streams progress lines while downloading, so a short quiet window
there means a stalled HLS connection and the kill is correct. But its
ffmpeg post-processing steps ([ExtractAudio] / [Fixup*] / [Merger]) emit
NOTHING for minutes on GB-scale clips — applying the download timeout
there permanently failed clips 6804/6816/6832 (June-July 2026) on every
attempt, including the recovery reruns. Once a post-processor line has
been seen, the watchdog must allow POSTPROCESS_STALL_TIMEOUT instead.

These tests run the real download_audio against a fake ``yt-dlp``
installed at the front of PATH. The watchdog polls on a fixed 5s tick
(``eof_reached.wait(timeout=5)``), so timings below are chosen around
that: the first stall check happens ~5s in.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import sources.granicus as granicus_mod
from sources.granicus import GranicusSource


def make_source():
    cfg = SimpleNamespace(
        granicus_host="lfucg.granicus.com",
        default_view_id=14,
        listing_view_fallbacks=(),
    )
    return GranicusSource(cfg, log=lambda msg, level="INFO": None)


def install_fake_ytdlp(tmp_path, monkeypatch, body):
    """Drop an executable ``yt-dlp`` python script at the front of PATH.

    ``body`` runs with ``out`` bound to the ``-o`` output path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "yt-dlp"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "args = sys.argv[1:]\n"
        "out = args[args.index('-o') + 1]\n"
        + body
    )
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])


def test_silent_postprocessing_survives_download_stall_limit(tmp_path, monkeypatch):
    """7s of ffmpeg silence after [ExtractAudio] must NOT be killed even
    with a 1s download stall limit — the post-processing limit governs."""
    monkeypatch.setattr(granicus_mod, "DOWNLOAD_STALL_TIMEOUT", 1)
    monkeypatch.setattr(granicus_mod, "POSTPROCESS_STALL_TIMEOUT", 60)
    install_fake_ytdlp(
        tmp_path,
        monkeypatch,
        "print('[download] 100% of 10.00MiB', flush=True)\n"
        "print('[ExtractAudio] Destination: ' + out, flush=True)\n"
        "time.sleep(7)\n"
        "open(out, 'w').write('audio')\n",
    )
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()

    result = make_source().download_audio(6832, clip_dir, title="Stormwater", date="2026-07-17")

    assert result == "2026-07-17_Stormwater_audio.mp3"
    assert (clip_dir / result).read_text() == "audio"


def test_download_phase_stall_is_still_killed(tmp_path, monkeypatch):
    """Silence DURING the download phase (no post-processor line yet) must
    still trip the short watchdog and fail the clip."""
    monkeypatch.setattr(granicus_mod, "DOWNLOAD_STALL_TIMEOUT", 1)
    monkeypatch.setattr(granicus_mod, "POSTPROCESS_STALL_TIMEOUT", 60)
    install_fake_ytdlp(
        tmp_path,
        monkeypatch,
        "print('[download]   5.0% of 10.00MiB', flush=True)\n"
        "time.sleep(60)\n",
    )
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()

    result = make_source().download_audio(6832, clip_dir, title="Stormwater", date="2026-07-17")

    assert result is None
