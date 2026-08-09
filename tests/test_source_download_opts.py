"""GranicusSource: unconditional parallel-fragment downloads (#3) and
process-lifetime memoization of the ViewPublisher listing (#8)."""

from pathlib import Path
from types import SimpleNamespace

import sources.granicus as granicus_mod
from sources.granicus import GranicusSource


def make_source(**cfg_over):
    cfg = SimpleNamespace(
        granicus_host="lfucg.granicus.com",
        default_view_id=14,
        listing_view_fallbacks=(),
        **cfg_over,
    )
    return GranicusSource(cfg, log=lambda msg, level="INFO": None)


class _FakeStdout:
    """One progress line then EOF so download_audio's reader thread exits."""
    def __init__(self):
        self._lines = [b"[download] 100% of 1.00MiB\n", b""]

    def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakePopen:
    """Captures the yt-dlp argv and writes the -o output file so the
    size>0 success check passes."""
    last_cmd = None

    def __init__(self, cmd, **kw):
        _FakePopen.last_cmd = cmd
        out = cmd[cmd.index("-o") + 1]
        Path(out).write_bytes(b"x" * 1024)
        self.stdout = _FakeStdout()
        self.returncode = 0

    def wait(self):
        return 0

    def kill(self):
        pass


class TestConcurrentFragments:
    def _run_download(self, tmp_path, monkeypatch, prefer_small):
        monkeypatch.setattr(granicus_mod.subprocess, "Popen", _FakePopen)
        src = make_source()
        src.prefer_small_audio_format = prefer_small
        clip_dir = tmp_path / "clips" / "42"
        clip_dir.mkdir(parents=True)
        out = src.download_audio(42, clip_dir, title="Council", date="2026-05-12")
        assert out is not None
        return _FakePopen.last_cmd

    def test_fragments_present_without_small_format_flag(self, tmp_path, monkeypatch):
        """The ~5x fragment-concurrency win is unconditional now — it must NOT
        be gated behind prefer_small_audio_format (the retired Scribe flag)."""
        cmd = self._run_download(tmp_path, monkeypatch, prefer_small=False)
        assert "--concurrent-fragments" in cmd
        assert cmd[cmd.index("--concurrent-fragments") + 1] == "5"
        # ...and the format flag is NOT set when prefer_small is off.
        assert "-f" not in cmd
        # --newline stays so the stall watchdog still sees output.
        assert "--newline" in cmd

    def test_fragments_present_with_small_format_flag(self, tmp_path, monkeypatch):
        cmd = self._run_download(tmp_path, monkeypatch, prefer_small=True)
        assert "--concurrent-fragments" in cmd
        assert "-f" in cmd  # small-format still selects the rendition

    def test_disabled_when_attr_zeroed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(granicus_mod.subprocess, "Popen", _FakePopen)
        src = make_source()
        src.hls_concurrent_fragments = 0
        clip_dir = tmp_path / "clips" / "43"
        clip_dir.mkdir(parents=True)
        src.download_audio(43, clip_dir, title="X", date="2026-05-12")
        assert "--concurrent-fragments" not in _FakePopen.last_cmd


class TestListingMemoization:
    def test_listing_fetched_once_per_view_across_clips(self, monkeypatch):
        html = (
            '<tr><td><a href="clip_id=100">Meeting</a></td>'
            '<td><span style="display: none;">1747152000</span></td></tr>'
        )
        calls = {"n": 0}

        def fake_get(url, timeout=30):
            calls["n"] += 1
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

        monkeypatch.setattr(granicus_mod.requests, "get", fake_get)
        src = make_source()

        d1 = src.fetch_date_from_listing(100)
        d2 = src.fetch_date_from_listing(100)  # same view, second clip lookup
        assert d1 == d2 is not None
        # Only ONE HTTP fetch despite two lookups (memoized for the lifetime).
        assert calls["n"] == 1

    def test_failed_fetch_is_cached_not_retried(self, monkeypatch):
        calls = {"n": 0}

        def boom(url, timeout=30):
            calls["n"] += 1
            raise RuntimeError("network down")

        monkeypatch.setattr(granicus_mod.requests, "get", boom)
        src = make_source()
        assert src.fetch_date_from_listing(100) is None
        assert src.fetch_date_from_listing(101) is None
        assert calls["n"] == 1  # not hammered
