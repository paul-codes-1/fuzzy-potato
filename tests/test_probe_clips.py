"""Tests for probe_clips.py: the resume high-water mark, the raised gap
budget, and the 404-vs-network-error distinction.

The old scanner stopped after 5 consecutive 404s and saved the resume point
REWOUND to the last FOUND clip, so every rerun restarted inside the same gap
and wedged forever. And a network/tool error looked identical to a 404, so a
transient blip could both count toward the gap budget and terminate the scan.
"""

import json

import probe_clips


def _saver(records):
    """A save() stub that records (available_len, last_probed) per call."""
    def save(output_file, available, last_probed):
        records.append((len(available), last_probed))
    return save


class TestRunScan:
    def test_five_gap_no_longer_wedges_resume_point(self, tmp_path, monkeypatch):
        """A 5-id gap must NOT stop the scan or rewind the resume point.

        found at 100, absent 101-105, found 106 → scan continues and the
        persisted resume point advances past the gap (never rewound to 100).
        """
        monkeypatch.setattr(probe_clips, "MAX_CONSECUTIVE_ABSENT", 25)
        found = {100, 106}

        def probe(cid):
            if cid in found:
                return {"status": "found", "clip_id": cid, "title": f"clip {cid}"}
            return {"status": "absent", "clip_id": cid}

        records = []
        available, last_probed = probe_clips.run_scan(
            100, 110, [], 0, tmp_path / "out.json",
            probe=probe, save=_saver(records), save_every=0,
        )
        # Both found clips captured, and the resume point is the last id we
        # probed (110), NOT rewound to the last found clip (106) or to 100.
        assert sorted(c["clip_id"] for c in available) == [100, 106]
        assert last_probed == 110

    def test_gap_budget_terminates_only_after_25(self, tmp_path, monkeypatch):
        monkeypatch.setattr(probe_clips, "MAX_CONSECUTIVE_ABSENT", 25)

        def probe(cid):
            # Everything absent from 200 onward.
            return {"status": "absent", "clip_id": cid}

        _, last_probed = probe_clips.run_scan(
            200, 500, [], 199, tmp_path / "out.json",
            probe=probe, save=lambda *a: None, save_every=0,
        )
        # Stops after exactly 25 consecutive absents (200..224), advancing the
        # high-water mark through them.
        assert last_probed == 224

    def test_network_error_does_not_advance_or_terminate_like_404(self, tmp_path):
        """A network error must NOT count toward the gap budget and must NOT
        advance the resume point past the errored id (so it's retried)."""
        seq = {
            300: {"status": "found", "clip_id": 300, "title": "x"},
            301: {"status": "absent", "clip_id": 301},
            302: {"status": "error", "clip_id": 302, "error": "timeout"},
            303: {"status": "found", "clip_id": 303, "title": "y"},  # never reached
        }

        def probe(cid):
            return seq[cid]

        available, last_probed = probe_clips.run_scan(
            300, 303, [], 299, tmp_path / "out.json",
            probe=probe, save=lambda *a: None, save_every=0,
        )
        # Scan stopped AT the error: last resolved id was 301, so 302 is
        # retried next run and 303 was never probed.
        assert last_probed == 301
        assert sorted(c["clip_id"] for c in available) == [300]

    def test_errors_do_not_sum_toward_gap_budget(self, tmp_path, monkeypatch):
        """Even many errors interleaved with absents don't trip the budget —
        errors break the scan immediately without counting."""
        monkeypatch.setattr(probe_clips, "MAX_CONSECUTIVE_ABSENT", 3)

        calls = {"n": 0}

        def probe(cid):
            calls["n"] += 1
            if cid == 402:
                return {"status": "error", "clip_id": cid, "error": "SSL error"}
            return {"status": "absent", "clip_id": cid}

        _, last_probed = probe_clips.run_scan(
            400, 500, [], 399, tmp_path / "out.json",
            probe=probe, save=lambda *a: None, save_every=0,
        )
        # 400, 401 absent, 402 error → break. Budget (3) never reached.
        assert last_probed == 401


class TestProbeClipClassification:
    def _run(self, monkeypatch, returncode=0, stdout="", stderr="", exc=None):
        class R:
            pass
        r = R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr

        def fake_run(*a, **k):
            if exc is not None:
                raise exc
            return r
        monkeypatch.setattr(probe_clips.subprocess, "run", fake_run)
        return probe_clips.probe_clip(999)

    def test_found(self, monkeypatch):
        res = self._run(monkeypatch, returncode=0, stdout="Council Meeting")
        assert res["status"] == "found"
        assert res["title"] == "Council Meeting"

    def test_absent_on_clean_nonzero(self, monkeypatch):
        res = self._run(monkeypatch, returncode=1, stdout="",
                        stderr="ERROR: Unable to download webpage: HTTP Error 404: Not Found")
        assert res["status"] == "absent"

    def test_error_on_timeout(self, monkeypatch):
        import subprocess
        res = self._run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30))
        assert res["status"] == "error"

    def test_error_on_network_signature(self, monkeypatch):
        res = self._run(monkeypatch, returncode=1, stdout="",
                        stderr="ERROR: Unable to download: Temporary failure in name resolution")
        assert res["status"] == "error"

    def test_error_on_5xx(self, monkeypatch):
        res = self._run(monkeypatch, returncode=1, stdout="",
                        stderr="ERROR: HTTP Error 503: Service Unavailable")
        assert res["status"] == "error"

    def test_error_on_missing_tool(self, monkeypatch):
        res = self._run(monkeypatch, exc=FileNotFoundError("yt-dlp"))
        assert res["status"] == "error"


class TestSaveProgress:
    def test_persists_last_probed_and_dedupes(self, tmp_path):
        out = tmp_path / "available_clips.json"
        available = [
            {"clip_id": 5, "title": "e"},
            {"clip_id": 1, "title": "a"},
            {"clip_id": 5, "title": "e-dup"},
        ]
        probe_clips.save_progress(out, available, last_checked=42)
        data = json.loads(out.read_text())
        assert data["last_checked"] == 42
        assert [c["clip_id"] for c in data["clips"]] == [1, 5]  # sorted + deduped
