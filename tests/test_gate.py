"""The write path for joins (the mosaic gate's lists retired 15 Nov 2026): python tests/test_gate.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import three_signal as ts  # noqa: E402
import triage as tri  # noqa: E402

OPEN_JOIN = {"id": "t-open", "published": False, "inputs": ["blackout", "elements", "cordon:alert"]}
# The Iran three-signal case in miniature: an outage, GPS interference and flying weather over one place.
IRAN_JOIN = {"id": "t-three-signal", "published": True, "inputs": ["blackout", "noisefloor", "vis"]}


def doc(*ids, join=OPEN_JOIN):
    return {"join": join, "records": [{"outage": {"id": ids[0]}, "found": [{"id": i} for i in ids[1:]]}]}


class Layers(unittest.TestCase):
    def test_item_ids_are_read_as_program_and_kind(self):
        self.assertEqual(gate.layer_of("blackout:outage:tn:2026-11-12T00"), ("blackout", "outage"))
        self.assertEqual(gate.layer_of("cordon:navwarn:iv-500-2024"), ("cordon", "navwarn"))
        self.assertEqual(gate.layer_of("noisefloor-event-2026-11-11-1"), ("noisefloor", ""))
        self.assertEqual(gate.layer_of("UT-GOOGLE-fe9985db2a"), ("undertow", "google"))
        self.assertEqual(gate.layer_of("UT-EEAS-e7ebbf0d02"), ("undertow", "eeas"))
        self.assertEqual(gate.layer_of("XX-GOOGLE-fe9985db2a"), ("", ""))
        self.assertEqual(gate.layer_of("wake:chokepoint:strait-of-hormuz:2026-11-08"), ("wake", "chokepoint"))
        self.assertEqual(gate.layer_of("RL-IFRC-d855019585"), ("relief", "ifrc"))
        self.assertEqual(gate.layer_of("RL-WHO-0b2662db99"), ("relief", "who"))

    def test_the_military_lists_are_gone(self):
        # Tom, 15 Nov 2026: no held classes. Nothing in the module may hold a layer back again.
        for name in ("OPEN", "MILITARY", "HELD_DIR", "layer_class"):
            self.assertFalse(hasattr(gate, name), name)

    def test_every_program_cop_draws_is_usable_in_a_join(self):
        cfg = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
        ids = [p["id"] for p in cfg["programs"]]
        self.assertEqual(gate.programs(), set(ids) | {"verdict"})
        join = {"id": "t-all", "published": True, "inputs": ids}
        self.assertEqual(gate.review(join)["review_class"], "open")

    def test_verdict_is_read_not_drawn(self):
        # Verdict is no layer in config/programs.json, but its calendar is a declared input of the election join.
        self.assertEqual(gate.READ_NOT_DRAWN, {"verdict"})
        self.assertEqual(gate.layer_of("verdict:calendar:2026-06-07-am-legislative"), ("verdict", "calendar"))
        join = {"id": "t-vd", "published": False, "inputs": ["undertow", "verdict:calendar"]}
        ok = {"join": join, "m": [{"id": "UT-WATCH-c12152a686"}, {"id": "verdict:calendar:2026-06-07-am-legislative"}]}
        self.assertEqual(gate.review(join, ok)["review_class"], "open")
        bad = {"join": join, "m": [{"id": "verdict:2026-11-06:fao-ffpi-october-up"}]}
        self.assertEqual(gate.review(join, bad)["review_class"], "refused")

    def test_layers_that_used_to_be_held_are_open(self):
        for item_id, inp in (("noisefloor-event-2026-11-11-1", "noisefloor"), ("vis:2026-11-13:hormuz", "vis"),
                             ("cordon:navwarn:4-1096-2026", "cordon:navwarn"), ("cordon:czib:2026-09-r1", "cordon"),
                             ("cordon:tfr:6-1234", "cordon:tfr"), ("cordon:faa:dxb", "cordon"),
                             ("launch:tfr:1-2345", "launch"), ("orbit:maneuver:41744:2026-10-12", "orbit:maneuver")):
            join = {"id": "t", "published": True, "inputs": ["blackout", inp]}
            v = gate.review(join, doc("blackout:outage:ir:x", item_id, join=join))
            self.assertEqual((v["review_class"], v["may_publish"]), ("open", True), (item_id, v["reasons"]))


class Review(unittest.TestCase):
    def test_declared_inputs_and_matching_citations_make_an_open_join(self):
        v = gate.review(OPEN_JOIN, doc("blackout:outage:tn:x", "elements:usgs:1", "cordon:alert:tn:1"))
        self.assertEqual((v["review_class"], v["reasons"]), ("open", []))
        self.assertFalse(v["may_publish"])     # open, but not switched on

    def test_open_and_switched_on_may_publish(self):
        v = gate.review(OPEN_JOIN | {"published": True}, doc("blackout:outage:tn:x"))
        self.assertTrue(v["may_publish"])

    def test_the_iran_case_publishes_when_switched_on(self):
        v = gate.review(IRAN_JOIN, doc("blackout:ooni:ir:2026-11-12", "noisefloor-event-2026-11-11-1",
                                       "vis:2026-11-13:hormuz", join=IRAN_JOIN))
        self.assertEqual((v["review_class"], v["may_publish"]), ("open", True))

    def test_citing_a_layer_the_join_never_declared_is_refused(self):
        v = gate.review(OPEN_JOIN, doc("blackout:outage:tn:x", "vigil:kev:cve-1"))
        self.assertEqual(v["review_class"], "refused")
        self.assertIn("declared inputs", v["reasons"][0])
        v = gate.review(OPEN_JOIN, doc("blackout:outage:tn:x", "cordon:navwarn:1"))   # cordon:alert only
        self.assertEqual(v["review_class"], "refused")

    def test_an_id_or_input_that_names_no_program_is_refused(self):
        self.assertEqual(gate.review(OPEN_JOIN, doc("blackout:outage:tn:x", "XX-GOOGLE-1"))["review_class"], "refused")
        self.assertEqual(gate.review({"id": "x", "published": True, "inputs": ["newprogram"]})["review_class"],
                         "refused")

    def test_a_join_with_no_inputs_is_refused(self):
        self.assertEqual(gate.review({"id": "x", "published": True})["review_class"], "refused")


class Writing(unittest.TestCase):
    def test_a_refused_join_is_written_nowhere(self):
        bad = doc("blackout:outage:tn:x", "vigil:kev:cve-1")
        for folder in (ROOT / "site" / "data", ROOT / "out" / "triage", Path(tempfile.mkdtemp())):
            with self.assertRaises(SystemExit, msg=str(folder)):
                gate.write(OPEN_JOIN, bad, folder, ("x.json",))

    def test_an_open_join_not_switched_on_is_refused_in_deployed_folders(self):
        for folder in (ROOT / "site" / "data", ROOT / "functions", ROOT / "mcp"):
            with self.assertRaises(SystemExit, msg=str(folder)):
                gate.write(OPEN_JOIN, doc("blackout:outage:tn:x"), folder, ("x.json",))

    def test_an_open_join_switched_on_is_allowed_in_site(self):
        v = gate.review(OPEN_JOIN | {"published": True}, doc("blackout:outage:tn:x"))
        self.assertIsNone(gate.allowed(v, ROOT / "site" / "data"))

    def test_write_stamps_the_verdict_into_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate.write(OPEN_JOIN, doc("blackout:outage:tn:x"), tmp, ("a.json", "b.json"))
            for name in ("a.json", "b.json"):
                got = json.loads((Path(tmp) / name).read_text(encoding="utf-8"))
                self.assertEqual(got["gate"]["review_class"], "open")


class Check(unittest.TestCase):
    def test_check_finds_an_unpublished_or_refused_join_planted_in_a_deployed_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site" / "data"
            site.mkdir(parents=True)
            off = doc("blackout:outage:tn:x", join=OPEN_JOIN)
            off["gate"] = {"review_class": "open", "may_publish": True}     # a forged stamp
            (site / "off.json").write_text(json.dumps(off), encoding="utf-8")
            bad = doc("blackout:outage:tn:x", "vigil:kev:1", join=OPEN_JOIN | {"published": True})
            (site / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
            (site / "today.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                                encoding="utf-8")
            ok = doc("blackout:outage:tn:x", join=OPEN_JOIN | {"published": True})
            (site / "ok.json").write_text(json.dumps(ok), encoding="utf-8")
            offences = gate.check([Path(tmp) / "site"])
            self.assertEqual(sorted(Path(p).name for p, _, _ in offences), ["bad.json", "off.json"])

    def test_the_real_deployed_folders_are_clean(self):
        self.assertEqual(gate.check(), [])


class JoinsThroughTheGate(unittest.TestCase):
    def test_outage_triage_is_open_and_switched_on(self):
        v = gate.review(tri.JOIN)
        self.assertEqual((v["review_class"], v["may_publish"]), ("open", True))
        off = gate.review(tri.JOIN | {"published": False})
        self.assertIsNotNone(gate.allowed(off, ROOT / "site" / "data"))

    def test_three_signal_is_open_and_switched_on(self):
        v = gate.review(ts.JOIN)
        self.assertEqual((v["review_class"], v["may_publish"]), ("open", True))

    def test_triage_does_not_read_navigation_warnings_or_airspace_bulletins(self):
        at = "2026-11-12T00:25:00Z"

        def item(program, id_, countries, tier="routine", **props):
            p = {"id": id_, "title": id_, "tier": tier, "data_as_of": at, "countries": countries,
                 "cop_program": program, "cop_stale": False} | props
            return {"type": "Feature", "geometry": None, "properties": p}

        snap = {"generated_at": "2026-11-13T00:21:12Z",
                "cop_programs": {k: {"state": "fresh"} for k in ("elements", "cordon", "watchfloor", "blackout")},
                "features": [item("blackout", "blackout:outage:sa:2026-11-12T00", ["SA"], "anomaly",
                                  indicator_id="BO-IND-1"),
                             item("cordon", "cordon:czib:2026-09-r1", ["SA"]),
                             item("cordon", "cordon:navwarn:ix-1", ["SA"]),
                             item("watchfloor", "watchfloor:2026-11-12:summary", [])]}
        out = tri.triage(snap)
        rec = out["records"][0]
        self.assertEqual(rec["layers"]["cordon"]["recent"] + rec["layers"]["cordon"]["standing"], [])
        self.assertEqual(rec["reading"], "open")
        self.assertEqual(gate.review(tri.JOIN, out)["review_class"], "open")


if __name__ == "__main__":
    unittest.main()
