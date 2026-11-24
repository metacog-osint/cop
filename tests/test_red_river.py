"""The Red River Valley view: right items in each state, and an empty layer never reads as an all-clear."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import red_river as rr  # noqa: E402


def item(id_, program, subs=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-96.8, 46.9]}, "properties": {
        "id": id_, "cop_program": program, "title": id_, "tier": "routine", "kind": "observation",
        "data_as_of": "2026-11-23T12:00:00Z", "subdivisions": subs or [],
        "sources": [{"publisher": "Publisher"}]}}


def snapshot(odin_read=True):
    lanes = {"cordon": ["faa-tfr"], "elements": list(rr.LAYERS[1]["lanes"]), "blackout": ["odin-ornl"] if odin_read else []}
    return {"generated_at": "2026-11-23T12:30:00Z",
            "cop_programs": {k: {"name": k.title(), "state": "fresh", "data_as_of": "2026-11-23T12:00:00Z", "lanes_read": v}
                             for k, v in lanes.items()},
            "features": [item("cordon:tfr:1", "cordon", ["US-ND"]), item("cordon:tfr:2", "cordon", ["US-MN", "US-WI"]),
                         item("cordon:tfr:3", "cordon", ["US-SD"]), item("elements:nws:1", "elements", ["US-MN"]),
                         item("elements:firms:US", "elements"), item("blackout:us-power:mn:2026-11-23", "blackout"),
                         item("blackout:us-power:ca:2026-11-23", "blackout"), item("vigil:x:1", "vigil", ["US-ND"])]}


def layer(doc, state, program):
    s = next(x for x in doc["states"] if x["code"] == state)
    return next(x for x in s["layers"] if x["program"] == program)


class RedRiver(unittest.TestCase):
    def test_items_by_state(self):
        doc = rr.build(snapshot())
        self.assertEqual([i["id"] for i in layer(doc, "ND", "cordon")["items"]], ["cordon:tfr:1"])
        self.assertEqual([i["id"] for i in layer(doc, "MN", "cordon")["items"]], ["cordon:tfr:2"])
        self.assertEqual([i["id"] for i in layer(doc, "MN", "blackout")["items"]], ["blackout:us-power:mn:2026-11-23"])
        self.assertEqual(layer(doc, "ND", "elements")["items"], [], "a country-wide item is not placed in a state")

    def test_empty_layer_says_what_was_checked(self):
        doc = rr.build(snapshot())
        text = layer(doc, "ND", "blackout")["empty_text"]
        self.assertIn("ODIN", text)
        self.assertIn(f"{rr.POWER_LINE:,} meters", text)
        for s in doc["states"]:
            for l in s["layers"]:
                self.assertNotIn("quiet", l["empty_text"].lower())

    def test_unread_source_is_named(self):
        l = layer(rr.build(snapshot(odin_read=False)), "ND", "blackout")
        self.assertEqual(l["not_read"], ["ODIN (Oak Ridge National Laboratory)"])
        self.assertIn("No current data", l["empty_text"])
        self.assertNotIn("Nothing placed", l["empty_text"])

    def test_stale_program_is_not_an_all_clear(self):
        snap = snapshot()
        snap["cop_programs"]["elements"]["state"] = "stale"
        self.assertIn("No current data", layer(rr.build(snap), "ND", "elements")["empty_text"])

    def test_missing_program_is_not_an_all_clear(self):
        snap = snapshot()
        del snap["cop_programs"]["cordon"]
        self.assertIn("No current data", layer(rr.build(snap), "ND", "cordon")["empty_text"])

    def test_gate_passes_and_writes(self):
        doc = rr.build(snapshot())
        self.assertTrue(gate.review(doc["join"], doc)["may_publish"])
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "today.geojson"
            src.write_text(json.dumps(snapshot()), encoding="utf-8")
            self.assertEqual(rr.main(["--snapshot", str(src), "--publish", tmp]), 0)
            self.assertTrue((Path(tmp) / rr.PUBLIC_NAME).exists())

    def test_page_stays_unlisted(self):
        page = (ROOT / "site" / "red-river.html").read_text(encoding="utf-8")
        self.assertIn('name="robots" content="noindex"', page)
        self.assertNotIn("red-river", (ROOT / "site" / "index.html").read_text(encoding="utf-8"),
                         "the page stays off the main menu until it is announced")


if __name__ == "__main__":
    unittest.main()
