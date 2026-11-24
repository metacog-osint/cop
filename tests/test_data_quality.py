"""Data-quality checks, report only: python tests/test_data_quality.py

Each rule has a failing item in tests/fixtures/data_quality/today.geojson (its "note" says which rule it trips) and a
near miss that must not trip it.
"""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import data_quality as dq  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "data_quality"
NOW = datetime(2026, 11, 24, 4, 30, tzinfo=timezone.utc)
SNAP = json.loads((FIX / "today.geojson").read_text(encoding="utf-8"))
HEALTH = json.loads((FIX / "health.json").read_text(encoding="utf-8"))
ZONES = json.loads(dq.ZONES.read_text(encoding="utf-8"))


def ids(report, rule):
    return sorted(e.split(" ", 1)[0].rstrip(":") for h in report[rule].values() for e in h["examples"])


class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.notes, cls.record = dq.check(SNAP, HEALTH, ZONES, NOW)

    def test_every_rule_reports(self):
        self.assertEqual(list(self.report), list(dq.RULES))

    def test_expired(self):
        self.assertEqual(ids(self.report, "expired"), ["wake:ais-listed:8821802:273396870"])

    def test_expired_is_judged_at_the_build_not_now(self):
        later = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(ids(dq.check(SNAP, HEALTH, ZONES, later)[0], "expired"),
                         ["wake:ais-listed:8821802:273396870"])

    def test_source_ended(self):
        self.assertEqual(ids(self.report, "source-ended"),
                         ["elements:gdacs:FL-1104290", "elements:gdacs:FL-1104294", "relief:ifrc:MDR001"])

    def test_status_with_no_baseline(self):
        self.assertEqual(ids(self.report, "status-no-base"),
                         ["blackout:ooni:xx:2026-11-24", "context:2026-11-24_0000Z:13_Sudan"])

    def test_z_on_a_zero_base(self):
        self.assertEqual(ids(self.report, "z-zero-base"),
                         ["wake:chokepoint:kerch", "watchfloor:2026-11-23:zone:15_Cuba"])

    def test_hours_covered(self):
        self.assertEqual(ids(self.report, "hours-covered"), ["noisefloor-milair-gulf-2026-11-22"])

    def test_zone_outside_the_kit(self):
        self.assertEqual(ids(self.report, "zone-unknown"),
                         ["context:2026-11-24_0000Z:17_Horn_of_Africa", "noisefloor-milair-nowhere-2026-11-22"])
        text = " ".join(e for h in self.report["zone-unknown"].values() for e in h["examples"])
        self.assertIn("retired zone 17_Horn_of_Africa", text)
        self.assertIn("not in the kit", text)

    def test_a_stale_copy_trips_nothing(self):
        for rule in dq.RULES:
            self.assertNotIn("wake:ais-gap:2", ids(self.report, rule))

    def test_report_only_leaves_the_snapshot_alone(self):
        snap = copy.deepcopy(SNAP)
        dq.check(snap, HEALTH, ZONES, NOW)
        self.assertEqual(snap, SNAP)

    def test_no_zone_list_is_a_note_not_a_crash(self):
        report, notes, _ = dq.check(SNAP, HEALTH, None, NOW)
        self.assertEqual(report["zone-unknown"], {})
        self.assertTrue(any("zone list could not be read" in n for n in notes))


class RowsLow(unittest.TestCase):
    def runs(self, n, vigil=300):
        return [{"at": f"2026-11-2{i}T00:00:00Z", "ok": ["vigil", "wake"],
                 "counts": {"vigil": vigil, "vigil/ransomlook": vigil, "wake": 40}} for i in range(n)]

    def snap(self, vigil):
        feats = [{"properties": {"id": f"vigil:{i}", "program": "vigil", "sources": [{"id": "ransomlook"}]}}
                 for i in range(vigil)]
        feats += [{"properties": {"id": f"wake:{i}", "program": "wake", "sources": [{"id": "imf-portwatch"}]}}
                  for i in range(40)]
        return {"generated_at": "2026-11-24T04:00:00Z", "features": feats}

    def test_a_collapse_against_the_median_is_a_hit(self):
        report, _, record = dq.check(self.snap(10), HEALTH, ZONES, NOW, self.runs(5))
        self.assertEqual(sorted(e.split(":")[0] for e in report["rows-low"]["vigil"]["examples"]),
                         ["vigil", "vigil/ransomlook"])
        self.assertNotIn("wake", report["rows-low"])
        self.assertEqual(record["counts"]["vigil"], 10)

    def test_an_ordinary_run_is_not(self):
        self.assertEqual(dq.check(self.snap(250), HEALTH, ZONES, NOW, self.runs(5))[0]["rows-low"], {})

    def test_too_few_runs_is_a_note(self):
        report, notes, _ = dq.check(self.snap(10), HEALTH, ZONES, NOW, self.runs(3))
        self.assertEqual(report["rows-low"], {})
        self.assertTrue(any("needs 4" in n for n in notes))

    def test_a_program_not_read_this_run_is_not_compared(self):
        health = {"programs": [{"id": "vigil", "state": "stale", "from_previous": True}, {"id": "wake", "state": "fresh"}]}
        self.assertEqual(dq.check(self.snap(10), health, ZONES, NOW, self.runs(5))[0]["rows-low"], {})


class Main(unittest.TestCase):
    def run_main(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dq.main(["--snapshot", str(FIX / "today.geojson"), "--now", "2026-11-24T04:30:00Z", *extra])
        return code, out.getvalue()

    def test_hits_never_fail_the_run(self):
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("::warning title=data quality expired::wake: 1", out)

    def test_an_unreadable_snapshot_never_fails_the_run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dq.main(["--snapshot", str(FIX / "missing.geojson")])
        self.assertEqual(code, 0)
        self.assertIn("snapshot could not be read", out.getvalue())

    def test_state_keeps_the_last_runs(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "dq-state.json"
            state.write_text("not json", encoding="utf-8")  # unreadable counts as no earlier run
            for _ in range(dq.ROWS_KEEP_RUNS + 3):
                self.assertEqual(self.run_main("--state", str(state))[0], 0)
            runs = json.loads(state.read_text(encoding="utf-8"))["runs"]
            self.assertEqual(len(runs), dq.ROWS_KEEP_RUNS)
            self.assertEqual(runs[-1]["counts"]["wake"], 3)

    def test_summary_and_json(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            summary, report = Path(d) / "summary.md", Path(d) / "dq.json"
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            try:
                self.run_main("--json", str(report))
            finally:
                del os.environ["GITHUB_STEP_SUMMARY"]
            self.assertIn("report only", summary.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["hits"]["expired"]["wake"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
