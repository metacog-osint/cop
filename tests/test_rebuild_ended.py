"""Rebuilding Elements' ended list for archived days from its history log: python tests/test_rebuild_ended.py

Recorded data only (tests/fixtures/rebuild_ended, MANIFEST.json): whole lines of Elements' history log and the ended
list Elements served on 14 Nov 2026. Rebuilt on Tom's yes, 13 Nov 2026 ("Yes, rebuild them")."""

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rebuild_ended as rb  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "rebuild_ended"
TORNADO, STORM, SLIGHT = ("elements:nws:KOKX.TO.W.0006.2026", "elements:nws:KBGM.WS.W.0004.2026",
                          "elements:spc:day1:slgt")


def ids(doc):
    return {f["properties"]["id"] for f in doc["features"]}


class Rebuild(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for x in (json.loads(s) for s in (FX / "log.jsonl").read_text(encoding="utf-8").splitlines() if s.strip()):
            y, m, d = x["at"][:10].split("-")
            p = self.dir / "history" / "log" / y / m / f"{d}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        p = self.dir / "history" / "log" / "2026" / "11" / "13.jsonl"   # one closed day, gzipped as Elements does
        p.with_suffix(".jsonl.gz").write_bytes(gzip.compress(p.read_bytes(), mtime=0))
        p.unlink()
        self.lines = rb.read_log(self.dir / "history")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def at(self, s):
        return rb.ended_at(self.lines, rb.utc(s))

    def test_reads_plain_and_gzipped_days_in_order(self):
        self.assertEqual(len(self.lines), 13)
        self.assertEqual([x["at"] for x in self.lines], sorted(x["at"] for x in self.lines))

    def test_cop_13_nov_snapshot_time(self):
        doc = self.at("2026-11-13T20:21:16Z")
        self.assertEqual(ids(doc), {TORNADO, STORM, SLIGHT})
        f = next(f for f in doc["features"] if f["properties"]["id"] == STORM)
        self.assertEqual(f["history"]["ended_at"], "2026-11-13T18:00:00Z")
        self.assertEqual(doc["generated_at"], "2026-11-13T18:06:33Z", "the log's last line by then")
        self.assertIn("Rebuilt from the history log", doc["rebuilt"]["note"])
        self.assertNotIn("starts", doc["rebuilt"]["note"])

    def test_nothing_from_after_the_time_and_an_open_item_is_not_ended(self):
        self.assertEqual(ids(self.at("2026-11-12T17:00:00Z")), set(), "Slight risk is open again; no tornado yet")
        self.assertEqual(ids(self.at("2026-11-12T19:30:00Z")), {TORNADO})

    def test_seven_day_window(self):
        self.assertIn(TORNADO, ids(self.at("2026-11-19T18:59:00Z")))
        self.assertNotIn(TORNADO, ids(self.at("2026-11-19T19:01:00Z")))

    def test_a_log_younger_than_the_window_says_so(self):
        doc = self.at("2026-11-03T12:00:00Z")
        self.assertEqual(ids(doc), {SLIGHT})
        self.assertIn("The log starts at 2026-10-31T22:02:42Z", doc["rebuilt"]["note"])

    def test_same_items_as_elements_served_on_14_nov(self):
        served = json.loads((FX / "served-2026-11-14.json").read_text(encoding="utf-8"))
        doc = self.at("2026-11-14T03:44:03Z")
        got = {f["properties"]["id"]: f for f in doc["features"]}
        self.assertEqual(set(got), ids(served))
        for f in served["features"]:
            g = got[f["properties"]["id"]]
            self.assertEqual(g["properties"], f["properties"])
            self.assertEqual(g["history"], f["history"])
            self.assertEqual(g["geometry"], f["geometry"])

    def test_main_fills_only_days_without_a_list(self):
        arch = self.dir / "arch"
        arch.mkdir()
        snap = json.loads((ROOT / "tests" / "fixtures" / "us_power" / "cop-2026-11-13-us.json").read_text(encoding="utf-8"))
        with gzip.open(arch / "2026-11-13.geojson.gz", "wt", encoding="utf-8") as fh:
            json.dump(snap, fh)
        with gzip.open(arch / "2026-11-12.geojson.gz", "wt", encoding="utf-8") as fh:
            json.dump(snap | {"generated_at": "2026-11-12T19:30:00Z"}, fh)
        kept = arch / "2026-11-12.elements-ended.json.gz"
        kept.write_bytes(gzip.compress(b'{"features": []}'))
        rb.main(["--history", str(self.dir / "history"), "--archive", str(arch), "--source", "test"])
        with gzip.open(arch / "2026-11-13.elements-ended.json.gz", "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(ids(doc), {TORNADO, STORM, SLIGHT})
        self.assertEqual(doc["rebuilt"]["from"], "test")
        self.assertEqual(gzip.decompress(kept.read_bytes()), b'{"features": []}', "an existing list is left alone")

    def test_base_rates_say_which_lists_were_rebuilt_and_which_are_short(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import triage_rates as rates
        from test_triage_us_power import day_snapshot
        early = day_snapshot() | {"generated_at": "2026-11-02T20:22:33Z"}
        late = day_snapshot()
        for snap in (early, late):
            snap["cop_ended"] = rb.ended_at(self.lines, rb.utc(snap["generated_at"]))
        self.assertEqual(rates.ended_list_kind(early), "short")
        self.assertEqual(rates.ended_list_kind(late), "rebuilt")
        self.assertEqual(rates.ended_list_kind({"cop_ended": {"features": []}}), "served")
        self.assertEqual(rates.ended_list_kind({}), "none")
        s = rates.rates([early, late])["state_background"]
        self.assertTrue(any("For 2 of the 2 days" in c and "the log starts 2026-10-31T22:02:42Z" in c
                            for c in s["cautions"]))
        self.assertTrue(any(c.startswith("On 1 of them the log was younger") for c in s["cautions"]))


if __name__ == "__main__":
    unittest.main()
