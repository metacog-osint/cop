"""Outage triage reads Elements' ended items (data/ended.geojson): python tests/test_triage_ended.py

Elements' feed carries active items only, so a warning that ran out before COP's snapshot was invisible to the
72-hour window (measured 1-14 Nov 2026: the live feed still held a power-line warning in 19% of the state-times when
one had begun in the window). Every test replays recorded items (tests/fixtures/us_power, MANIFEST.json): Elements'
own ended list of 02:33 UTC 14 Nov, COP's archived snapshot of 20:21 UTC 13 Nov and Blackout's recorded New York and
California readings of 14:40 UTC 13 Nov. Built on Tom's yes, 13 Nov 2026.
"""

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import snapshot  # noqa: E402
import triage as tri  # noqa: E402
import triage_rates as rates  # noqa: E402
from test_triage_us_power import by_state, day_snapshot, fixture  # noqa: E402

NY_TORNADO = "elements:nws:KOKX.TO.W.0006.2026"
NY_WINTER_STORM = "elements:nws:KBGM.WS.W.0004.2026"
STILL_LIVE = "elements:nws:PAFG.UP.W.2425.2026"


def ended():
    return fixture("elements-ended.json")


class WithEnded(unittest.TestCase):
    def setUp(self):
        self.snap = day_snapshot()
        self.live = [f for f in self.snap["features"] if isinstance(f.get("properties"), dict)]

    def test_ended_items_are_added_marked_and_never_twice(self):
        feats, info = tri.with_ended(self.live, ended(), tri.utc(self.snap["generated_at"]))
        added = feats[len(self.live):]
        self.assertEqual(info, {"state": "fresh", "added": 4, "generated_at": ended()["generated_at"]})
        self.assertEqual(len(added), 4)
        self.assertNotIn(STILL_LIVE, {f["properties"]["id"] for f in added}, "live in the snapshot: not added again")
        for f in added:
            self.assertEqual(f["properties"]["cop_program"], "elements")
            self.assertIn("ended_at", f["properties"]["cop_ended"])
        self.assertEqual(len({f["properties"]["id"] for f in feats}), len(feats))

    def test_missing_and_old_lists(self):
        self.assertEqual(tri.with_ended(self.live, None)[1]["state"], "missing")
        self.assertEqual(tri.with_ended(self.live, {"features": "x"})[1]["state"], "missing")
        late = tri.utc(ended()["generated_at"]) + timedelta(hours=tri.ENDED_STALE_H, minutes=1)
        self.assertEqual(tri.with_ended(self.live, ended(), late)[1]["state"], "old")
        self.assertIsNone(tri.ended_caveat({"state": "fresh"}))
        self.assertEqual(tri.ended_caveat({"state": "old"}), tri.ENDED_OLD)
        self.assertEqual(tri.ended_caveat(None), tri.ENDED_MISSING)


class NewYork(unittest.TestCase):
    """New York read open on the live snapshot alone; the warnings that had ended make it natural."""

    def test_live_snapshot_alone_reads_open_and_says_what_is_missing(self):
        doc = tri.triage(day_snapshot())
        ny = by_state(doc)["NY"]
        self.assertEqual(ny["reading"], "open")
        self.assertIn(tri.ENDED_MISSING, ny["caveats"])
        self.assertEqual(doc["ended_items"]["state"], "missing")

    def test_with_the_ended_list_new_york_reads_natural(self):
        doc = tri.triage(day_snapshot(), None, ended())
        ny = by_state(doc)["NY"]
        self.assertEqual(ny["reading"], "natural")
        sig = {r["id"]: r for r in ny["layers"]["elements"]["significant"]}
        self.assertEqual(set(sig), {NY_TORNADO, NY_WINTER_STORM})
        self.assertEqual(sig[NY_TORNADO]["power_hazard"], "Tornado")
        self.assertEqual(sig[NY_WINTER_STORM]["power_hazard"], "Winter Storm")
        self.assertEqual(sig[NY_WINTER_STORM]["ended_at"], "2026-11-13T18:00:00Z")
        self.assertTrue(all(r["ended_at"] < doc["snapshot_generated_at"] for r in sig.values()))
        self.assertNotIn(tri.ENDED_MISSING, ny["caveats"])
        text = tri.showed("elements", ny["layers"]["elements"])
        self.assertIn("All of them had ended before this snapshot.", text)

    def test_california_keeps_its_live_warnings_and_gains_the_ended_one(self):
        ca = by_state(tri.triage(day_snapshot(), None, ended()))["CA"]
        self.assertEqual(ca["reading"], "natural")
        rows = ca["layers"]["elements"]["significant"]
        self.assertIn("elements:nws:KEKA.HW.W.0003.2026", {r["id"] for r in rows})
        self.assertTrue(any(not r.get("ended_at") for r in rows), "the live warnings are still there")
        self.assertIn("of them had ended", tri.showed("elements", ca["layers"]["elements"]))

    def test_the_public_document_carries_the_list_state_and_each_end(self):
        pub = tri.public(tri.attach_rates(tri.triage(day_snapshot(), None, ended()), None))
        self.assertEqual(pub["ended_items"]["state"], "fresh")
        ny = next(r for r in pub["records"] if r.get("us_state") == "NY")
        self.assertTrue(all(r.get("ended_at") for r in ny["layers"]["elements"]["significant"]))


class Archive(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_snapshot_writes_the_list_and_a_daily_copy_and_drops_an_old_one_on_failure(self):
        fx = self.dir / "fx"
        fx.mkdir()
        (fx / "elements-ended.geojson").write_text(json.dumps(ended()), encoding="utf-8")
        now = tri.utc("2026-11-14T02:30:00Z")
        lines = snapshot.fetch_ended(now, self.dir / "out", fx, self.dir / "arch")
        self.assertIn("elements ended items: 5", lines[0])
        got = json.loads((self.dir / "out" / "elements.geojson").read_text(encoding="utf-8"))
        self.assertEqual(got["cop_fetched_at"], "2026-11-14T02:30:00Z")
        with gzip.open(self.dir / "arch" / "2026-11-14.elements-ended.json.gz", "rt", encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["features"]), 5)
        (fx / "elements-ended.geojson").unlink()
        lines = snapshot.fetch_ended(now, self.dir / "out", fx)
        self.assertIn("fetch failed", lines[0])
        self.assertFalse((self.dir / "out" / "elements.geojson").exists(), "an old list is not left to be read")

    def test_base_rates_read_the_day_list_and_skip_it_in_the_snapshot_glob(self):
        day = day_snapshot()
        with gzip.open(self.dir / "2026-11-13.geojson.gz", "wt", encoding="utf-8") as fh:
            json.dump(day, fh)
        with gzip.open(self.dir / "2026-11-13.elements-ended.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(ended(), fh)
        files = sorted(f for f in self.dir.glob("*.geojson.gz") if f.name.count(".") == 2)
        self.assertEqual([f.name for f in files], ["2026-11-13.geojson.gz"])
        snap = rates.attach_ended(tri.load(files[0]), files[0])
        self.assertIn("cop_ended", snap)
        without = rates.rates([day_snapshot()])["state_background"]
        withit = rates.rates([snap])["state_background"]
        self.assertTrue(withit["days"][0]["ended_list"])
        self.assertIn("NY", withit["days"][0]["natural_states"])
        self.assertNotIn("NY", without["days"][0]["natural_states"])
        self.assertEqual(withit["cautions"][1:], [], "every day has its list: no missing-list caution")
        self.assertTrue(any("no list of Elements' ended hazards" in c for c in without["cautions"]))


class Build(unittest.TestCase):
    def test_build_fetches_archives_and_passes_the_list(self):
        wf = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("python tests/test_triage_ended.py", wf)
        self.assertIn('gh release upload "$TAG" out/archive/*.geojson.gz $(ls out/archive/*-ended.json.gz 2>/dev/null)', wf)
        self.assertIn('--pattern "*-ended.json.gz"', wf)
        self.assertIn("cp out/archive/*-ended.json.gz out/rates-archive/ 2>/dev/null || true", wf)
        self.assertIn("--ended out/ended/elements.geojson", wf)
        cfg = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
        el = next(p for p in cfg["programs"] if p["id"] == "elements")
        self.assertEqual(el["ended"], "https://elements.theintelligence.company/data/ended.geojson")


if __name__ == "__main__":
    unittest.main()
