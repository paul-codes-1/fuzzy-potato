"""Tests for probe_clips.py: the resume high-water mark, the raised gap
budget, the 404-vs-network-error distinction, and the late-publish recheck.

The old scanner stopped after 5 consecutive 404s and saved the resume point
REWOUND to the last FOUND clip, so every rerun restarted inside the same gap
and wedged forever. The monotonic high-water fix then wedged the other way:
Granicus publishes clips days after allocating their ids, so ids the scanner
had already walked past were absent-forever (prod 2026-08: clips 6846-6856).
run_recheck covers that window. Probing is plain HTTP now — yt-dlp's generic
extractor broke against the redesigned Granicus player and classified every
real clip as absent.
"""

import json

import pytest
import requests

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

        def probe(cid):
            if cid == 402:
                return {"status": "error", "clip_id": cid, "error": "SSL error"}
            return {"status": "absent", "clip_id": cid}

        _, last_probed = probe_clips.run_scan(
            400, 500, [], 399, tmp_path / "out.json",
            probe=probe, save=lambda *a: None, save_every=0,
        )
        # 400, 401 absent, 402 error → break. Budget (3) never reached.
        assert last_probed == 401


class TestRunRecheck:
    """Late-published clips: ids the scan already walked past (absent at the
    time) must be re-checked so a clip published days later is still found."""

    def test_late_published_clip_is_picked_up(self):
        available = [{"clip_id": 100, "title": "old"}]

        def probe(cid):
            if cid == 103:  # published after the scanner first passed it
                return {"status": "found", "clip_id": cid, "title": "late"}
            return {"status": "absent", "clip_id": cid}

        out = probe_clips.run_recheck(101, 110, available, probe=probe)
        assert sorted(c["clip_id"] for c in out) == [100, 103]

    def test_already_known_ids_are_not_reprobed(self):
        available = [{"clip_id": 100, "title": "a"}, {"clip_id": 102, "title": "b"}]
        probed = []

        def probe(cid):
            probed.append(cid)
            return {"status": "absent", "clip_id": cid}

        probe_clips.run_recheck(100, 103, available, probe=probe)
        assert probed == [101, 103]  # 100 and 102 skipped

    def test_error_stops_recheck_without_losing_found(self):
        def probe(cid):
            if cid == 201:
                return {"status": "found", "clip_id": cid, "title": "x"}
            if cid == 202:
                return {"status": "error", "clip_id": cid, "error": "timeout"}
            return {"status": "absent", "clip_id": cid}

        out = probe_clips.run_recheck(200, 210, [], probe=probe)
        assert [c["clip_id"] for c in out] == [201]

    def test_empty_range_is_noop(self):
        available = [{"clip_id": 5, "title": "a"}]
        out = probe_clips.run_recheck(6, 5, available, probe=lambda cid: pytest.fail("probed"))
        assert out is available


class TestProbeClipClassification:
    """HTTP classification: 200+<title> = found, 404/redirect = absent,
    anything transient (network error, 5xx, 403, titleless 200) = error."""

    def _run(self, monkeypatch, status_code=200, text="", exc=None):
        class R:
            pass
        r = R()
        r.status_code = status_code
        r.text = text

        def fake_get(*a, **k):
            if exc is not None:
                raise exc
            return r
        monkeypatch.setattr(probe_clips.requests, "get", fake_get)
        return probe_clips.probe_clip(999)

    def test_found(self, monkeypatch):
        res = self._run(monkeypatch, 200, "<html><title>Council Meeting </title></html>")
        assert res["status"] == "found"
        assert res["title"] == "Council Meeting"

    def test_found_unescapes_entities(self, monkeypatch):
        res = self._run(monkeypatch, 200, "<title>Social Services &amp; Public Safety</title>")
        assert res["status"] == "found"
        assert res["title"] == "Social Services & Public Safety"

    def test_absent_on_404(self, monkeypatch):
        res = self._run(monkeypatch, 404)
        assert res["status"] == "absent"

    def test_absent_on_redirect(self, monkeypatch):
        # A 302 (e.g. to the view listing) means no public player page.
        res = self._run(monkeypatch, 302)
        assert res["status"] == "absent"

    def test_error_on_200_without_title(self, monkeypatch):
        # A markup change must stop the scan, not eat the archive as absent.
        res = self._run(monkeypatch, 200, "<html><body>hi</body></html>")
        assert res["status"] == "error"

    def test_error_on_network_exception(self, monkeypatch):
        res = self._run(monkeypatch, exc=requests.ConnectionError("boom"))
        assert res["status"] == "error"

    def test_error_on_timeout(self, monkeypatch):
        res = self._run(monkeypatch, exc=requests.Timeout("timed out"))
        assert res["status"] == "error"

    def test_error_on_5xx(self, monkeypatch):
        res = self._run(monkeypatch, 503)
        assert res["status"] == "error"

    def test_error_on_403(self, monkeypatch):
        # A WAF challenge must not classify the whole archive as ended.
        res = self._run(monkeypatch, 403)
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
