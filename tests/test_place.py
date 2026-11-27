"""Place pages: each area's current reading and its 30- and 90-day windows, drawn only from enough history."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import history as hi  # noqa: E402
import place as pl  # noqa: E402
from tic_doctrine import vocab  # noqa: E402  (place.py put the vendored kit on sys.path)

GULF = {"area": "gulf", "name": "Gulf", "center": [56.378, 27.218], "radius_km": 900, "countries": ["IR", "OM"]}
UKRAINE = {"area": "ukraine", "name": "Ukraine", "countries": ["UA"]}


def feature(item_id, program, countries, lonlat=None, tier="routine"):
    geom = {"type": "Point", "coordinates": lonlat} if lonlat else None
    return {"type": "Feature", "geometry": geom,
            "properties": {"id": item_id, "cop_program": program, "countries": countries, "tier": tier,
                           "title": item_id, "data_as_of": "2026-11-26T12:00:00Z"}}


def sourced(item_id, program, groups, countries=("UA",), **props):
    """An item in the given countries whose sources are the given independence groups."""
    f = feature(item_id, program, list(countries))
    f["properties"]["sources"] = [{"id": g, "publisher": g.upper(), "independence_group": g} for g in groups]
    f["properties"].update(props)
    return f


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

    def test_coverage_counts_independent_groups_read_on_time(self):
        """Kit format tic-coverage/1: groups, not outlets; stale and ended items do not count; claims are set aside."""
        snap = {"generated_at": "2026-11-26T12:00:00Z",
                "cop_programs": {p: {"name": p.title(), "state": "fresh"}
                                 for p in ("context", "cordon", "elements", "relief", "watchfloor")},
                "features": [
                    sourced("cordon:advisory:UA", "cordon", ["us-state-department"]),
                    sourced("watchfloor:zone:UA", "watchfloor", ["gdelt"]),
                    sourced("context:zone:UA", "context", ["gdelt"]),                 # GDELT again: one group
                    sourced("relief:appeal:UA", "relief", ["ifrc"], cop_stale=True),  # a stale copy
                    sourced("elements:usgs:1", "elements", ["usgs"], measures={"current": False}),   # ended
                    sourced("elements:gdacs:2", "elements", ["gdacs"], valid_until="2026-11-26T11:00:00Z"),  # expired
                    sourced("watchfloor:claim:UA", "watchfloor", ["example-ministry"],
                            corroboration={"state": "lead", "origins": 1, "party_claim": True}),
                ]}
        progs = sorted(snap["cop_programs"])
        row = pl.place_doc(snap, UKRAINE, progs)["coverage"]
        self.assertEqual((row["place"], row["name"], row["class"], row["independent"]), ("ukraine", "Ukraine", "few", 2))
        self.assertEqual([g["id"] for g in row["groups"]], ["gdelt", "us-state-department"])
        self.assertEqual(row["groups"][0]["programs"], ["context", "watchfloor"], "one group, both programs named")
        self.assertEqual([(g["id"], g["why"]) for g in row["set_aside"]], [("example-ministry", "party_claim")])
        self.assertNotIn("relief", row["programs"], "a stale copy is not counted")
        self.assertNotIn("elements", row["programs"], "ended or expired items are not counted")
        snap["features"].append(sourced("relief:appeal:UA2", "relief", ["ifrc"]))
        self.assertEqual(pl.place_doc(snap, UKRAINE, progs)["coverage"]["class"], "several", "3 groups")
        gulf = pl.place_doc(snap, GULF, progs)["coverage"]
        self.assertEqual((gulf["class"], gulf["independent"], gulf["groups"]), ("none", 0, []), "nothing held there")

    def test_the_coverage_table_is_written_through_the_gate(self):
        snap = {"generated_at": "2026-11-26T12:00:00Z", "cop_programs": {"cordon": {"state": "fresh"}},
                "features": [sourced("cordon:advisory:UA", "cordon", ["us-state-department"]),
                             sourced("cordon:advisory:IR", "cordon", ["us-state-department"], countries=["IR"])]}
        with tempfile.TemporaryDirectory() as d:
            pub = Path(d) / "site" / "data"
            pub.mkdir(parents=True)
            Path(d, "snap.json").write_text(json.dumps(snap), encoding="utf-8")
            pl.main(["--snapshot", str(Path(d, "snap.json")), "--history", str(Path(d, "none")), "--publish", str(pub)])
            cov = json.loads((pub / "places" / "coverage.json").read_text(encoding="utf-8"))
            ukraine = json.loads((pub / "places" / "ukraine.json").read_text(encoding="utf-8"))
            self.assertEqual(gate.check([pub]), [], "group ids are not read as item citations")
        self.assertEqual(cov["format"], "tic-coverage/1")
        self.assertEqual(cov["generated_at"], snap["generated_at"], "counted at the snapshot")
        self.assertEqual(cov["rule"], vocab.load("event_ladder")["coverage"]["rule"])
        self.assertIn("lower bound", cov["basis"])
        self.assertTrue(cov["gate"]["may_publish"])
        self.assertEqual([r["place"] for r in cov["places"]], [a["area"] for a in hi.load_areas()])
        self.assertEqual(next(r for r in cov["places"] if r["place"] == "ukraine"), ukraine["coverage"])
        self.assertEqual(ukraine["coverage"]["groups"][0]["label"], "US-STATE-DEPARTMENT")
        self.assertEqual(ukraine["coverage_method"]["url"], cov["url"])
        # Only a coverage row's source groups are skipped: an item id anywhere else is still a citation.
        doc = {"places": [ukraine["coverage"]], "top": [{"id": "gdelt:item"}]}
        self.assertEqual(gate.cited_ids(doc), {"gdelt:item"})
        self.assertFalse(gate.review(pl.JOIN, doc)["may_publish"], "gdelt is not a COP program")

    def test_the_page_shows_the_coverage_table(self):
        js = (ROOT / "site" / "place.js").read_text(encoding="utf-8")
        self.assertIn('import { coverageClass } from "./vendor/tic-doctrine.js";', js)
        self.assertIn('getJSON("data/places/coverage.json")', js)
        for col in ("Place", "Coverage", "Independent groups", "Programs", "Set aside"):
            self.assertIn(f'"{col}"', js)
        self.assertIn("each count is a lower bound", js)
        self.assertIn("coverageBox(doc.coverage", js, "each area shows its own row")

    def test_the_page_puts_text_in_as_text(self):
        js = (ROOT / "site" / "place.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertIn(f"{int(pl.TREND_SHARE * 100)}%", (ROOT / "site" / "scoring.html").read_text(encoding="utf-8"),
                      "the line rule is stated on the scoring page")


if __name__ == "__main__":
    unittest.main()
