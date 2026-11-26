"""Place pages: each area's current reading and its 30- and 90-day windows, drawn only from enough history."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import place as pl  # noqa: E402

GULF = {"area": "gulf", "name": "Gulf", "center": [56.378, 27.218], "radius_km": 900, "countries": ["IR", "OM"]}
UKRAINE = {"area": "ukraine", "name": "Ukraine", "countries": ["UA"]}


def feature(item_id, program, countries, lonlat=None, tier="routine"):
    geom = {"type": "Point", "coordinates": lonlat} if lonlat else None
    return {"type": "Feature", "geometry": geom,
            "properties": {"id": item_id, "cop_program": program, "countries": countries, "tier": tier,
                           "title": item_id, "data_as_of": "2026-11-26T12:00:00Z"}}


def history(n_days, read_every=1):
    days = [f"2026-11-{d:02d}" for d in range(1, n_days + 1)]
    read = [i % read_every == 0 for i in range(n_days)]
    return {"days": days, "layers": {"noisefloor": {
        "items": [5 if r else None for r in read], "anomalies": [1 if r else None for r in read], "read": read,
        "notes": ["a note" if i == 0 else None for i in range(n_days)], "method_versions": ["nf-1.0", "nf-1.1"]}}}


class Place(unittest.TestCase):
    def test_a_line_needs_four_days_in_five_read(self):
        full = pl.window(history(30)["layers"]["noisefloor"], history(30)["days"], 30)
        self.assertTrue(full["enough"])
        self.assertEqual(full["read_days"], 30)
        self.assertEqual(full["median"], 5)
        short = pl.window(history(23)["layers"]["noisefloor"], history(23)["days"], 30)
        self.assertFalse(short["enough"], "23 of 30 days is under 80%")
        self.assertEqual(short["items"][:7], [None] * 7, "days before the history starts have no value, never a zero")
        gappy = history(30, read_every=2)
        self.assertFalse(pl.window(gappy["layers"]["noisefloor"], gappy["days"], 30)["enough"])
        self.assertFalse(pl.window(history(30)["layers"]["noisefloor"], history(30)["days"], 90)["enough"])

    def test_an_unread_day_is_not_a_zero(self):
        h = history(30, read_every=2)
        w = pl.window(h["layers"]["noisefloor"], h["days"], 30)
        self.assertIn(None, w["items"])
        self.assertNotIn(0, w["items"])

    def test_items_are_placed_by_the_history_sets_own_rule(self):
        snap = {"generated_at": "2026-11-26T12:00:00Z",
                "cop_programs": {"noisefloor": {"name": "Noisefloor", "state": "fresh"}, "cordon": {"name": "Cordon", "state": "stale"}},
                "features": [feature("nf:1", "noisefloor", [], [56.3, 26.6], "anomaly"),   # in the circle
                             feature("nf:2", "noisefloor", [], [10.0, 50.0]),              # far away
                             feature("co:1", "cordon", ["UA"])]}
        g = pl.place_doc(snap, GULF, ["cordon", "noisefloor"])
        self.assertEqual((g["layers"]["noisefloor"]["items"], g["layers"]["noisefloor"]["anomalies"]), (1, 1))
        self.assertEqual([t["id"] for t in g["layers"]["noisefloor"]["top"]], ["nf:1"])
        self.assertEqual(g["definition"]["kind"], "circle")
        u = pl.place_doc(snap, UKRAINE, ["cordon", "noisefloor"])
        self.assertEqual(u["layers"]["cordon"]["items"], 1)
        self.assertEqual(u["layers"]["cordon"]["state"], "stale", "the program's own freshness is carried")
        self.assertFalse(u["history"]["available"])

    def test_the_join_is_declared_and_writes_through_the_gate(self):
        snap = {"generated_at": "2026-11-26T12:00:00Z", "cop_programs": {"noisefloor": {"state": "fresh"}},
                "features": [feature("nf:1", "noisefloor", ["IR"], [56.3, 26.6])]}
        with tempfile.TemporaryDirectory() as d:
            hist = Path(d) / "hist"
            hist.mkdir()
            (hist / "gulf.json").write_text(json.dumps(history(30)), encoding="utf-8")
            pub = Path(d) / "site" / "data"
            pub.mkdir(parents=True)
            Path(d, "snap.json").write_text(json.dumps(snap), encoding="utf-8")
            pl.main(["--snapshot", str(Path(d, "snap.json")), "--history", str(hist), "--publish", str(pub)])
            doc = json.loads((pub / "places" / "gulf.json").read_text(encoding="utf-8"))
            self.assertTrue(doc["gate"]["may_publish"])
            self.assertEqual(doc["join"]["inputs"], ["noisefloor"])
            self.assertTrue(doc["history"]["layers"]["noisefloor"]["windows"][0]["enough"])
            self.assertTrue((pub / "places" / "index.json").is_file())
        self.assertTrue(gate.review(pl.JOIN, doc)["may_publish"])

    def test_the_page_puts_text_in_as_text(self):
        js = (ROOT / "site" / "place.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertIn(f"{int(pl.TREND_SHARE * 100)}%", (ROOT / "site" / "scoring.html").read_text(encoding="utf-8"),
                      "the line rule is stated on the scoring page")


if __name__ == "__main__":
    unittest.main()
