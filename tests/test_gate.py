"""The review gate: python tests/test_gate.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
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

    def test_open_and_military_layers(self):
        for program, kind in (("elements", "usgs"), ("blackout", "ooni"), ("watchfloor", "2026-11-11"),
                              ("cordon", "advisory"), ("cordon", "alert"), ("cordon", "posture"), ("vigil", "kev")):
            self.assertEqual(gate.layer_class(program, kind)[0], "open", (program, kind))
        for program, kind in (("noisefloor", ""), ("vis", "2026-11-13"), ("cordon", "navwarn"), ("cordon", "czib")):
            cls, why = gate.layer_class(program, kind)
            self.assertEqual(cls, "held", (program, kind))
            self.assertIn("military context", why)

    def test_cordon_travel_advice_and_health_notices_are_open_and_faa_kinds_are_not(self):
        # Tom, 14 Nov 2026: UK FCDO, Canada and CDC notices are of the same nature as State's.
        for kind in ("fcdo", "canada", "cdc", "safetravel"):
            self.assertEqual(gate.layer_class("cordon", kind)[0], "open", kind)
        for kind in ("tfr", "faa"):
            self.assertEqual(gate.layer_class("cordon", kind)[0], "held", kind)

    def test_a_layer_or_kind_nobody_reviewed_is_held(self):
        self.assertEqual(gate.layer_class("newprogram", "thing")[0], "held")
        self.assertEqual(gate.layer_class("cordon", "newkind")[0], "held")
        self.assertEqual(gate.layer_class("", "")[0], "held")

    def test_cordon_as_a_whole_is_held_until_its_kinds_are_named(self):
        cls, why = gate.layer_class("cordon")
        self.assertEqual(cls, "held")
        self.assertIn("name the kinds", why)

    def test_every_program_in_the_kit_is_on_one_list(self):
        programs = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
        names = programs if isinstance(programs, list) else programs.get("programs", programs)
        ids = [p["id"] if isinstance(p, dict) else p for p in names]
        for pid in ids:
            self.assertTrue(pid in gate.OPEN or pid in gate.MILITARY, f"{pid} is on neither list in tools/gate.py")


class Review(unittest.TestCase):
    def test_open_inputs_and_open_items_make_an_open_join(self):
        v = gate.review(OPEN_JOIN, doc("blackout:outage:tn:x", "elements:usgs:1", "cordon:alert:tn:1"))
        self.assertEqual((v["review_class"], v["reasons"]), ("open", []))
        self.assertFalse(v["may_publish"])     # open, but not switched on

    def test_open_and_switched_on_may_publish(self):
        v = gate.review(OPEN_JOIN | {"published": True}, doc("blackout:outage:tn:x"))
        self.assertTrue(v["may_publish"])

    def test_the_three_signal_join_is_held_whatever_its_switch_says(self):
        v = gate.review(IRAN_JOIN, doc("blackout:ooni:ir:2026-11-12", "noisefloor-event-2026-11-11-1",
                                       "vis:2026-11-13:hormuz", join=IRAN_JOIN))
        self.assertEqual(v["review_class"], "held")
        self.assertFalse(v["may_publish"])
        self.assertTrue(any("noisefloor" in r for r in v["reasons"]))
        self.assertTrue(any("vis" in r for r in v["reasons"]))

    def test_a_join_that_declares_open_but_cites_a_military_item_is_held(self):
        v = gate.review(OPEN_JOIN | {"published": True},
                        doc("blackout:outage:ae:x", "noisefloor-event-2026-11-11-1"))
        self.assertEqual(v["review_class"], "held")
        self.assertFalse(v["may_publish"])

    def test_citing_a_layer_the_join_never_declared_is_held(self):
        v = gate.review(OPEN_JOIN, doc("blackout:outage:tn:x", "vigil:kev:cve-1"))
        self.assertEqual(v["review_class"], "held")
        self.assertIn("declared inputs", v["reasons"][0])

    def test_a_join_with_no_inputs_is_held(self):
        self.assertEqual(gate.review({"id": "x", "published": True})["review_class"], "held")

    def test_an_old_self_declared_class_is_ignored(self):
        v = gate.review({"id": "x", "review_class": "open", "published": True, "inputs": ["noisefloor"]})
        self.assertEqual(v["review_class"], "held")


class Writing(unittest.TestCase):
    def test_a_held_join_is_refused_in_every_deployed_folder_and_in_plain_out(self):
        held = doc("blackout:ooni:ir:x", "noisefloor-event-1", join=IRAN_JOIN)
        for folder in (ROOT / "site" / "data" / "joins", ROOT / "functions", ROOT / "mcp", ROOT / "out" / "triage",
                       ROOT):
            with self.assertRaises(SystemExit, msg=str(folder)):
                gate.write(IRAN_JOIN, held, folder, ("x.json",))

    def test_a_held_join_may_be_written_under_out_held(self):
        v = gate.review(IRAN_JOIN)
        self.assertIsNone(gate.allowed(v, ROOT / "out" / "held" / "three-signal"))

    def test_an_open_join_not_switched_on_is_refused_in_site(self):
        with self.assertRaises(SystemExit):
            gate.write(OPEN_JOIN, doc("blackout:outage:tn:x"), ROOT / "site" / "data" / "joins", ("x.json",))

    def test_an_open_join_switched_on_is_allowed_in_site(self):
        v = gate.review(OPEN_JOIN | {"published": True}, doc("blackout:outage:tn:x"))
        self.assertIsNone(gate.allowed(v, ROOT / "site" / "data" / "joins"))

    def test_write_stamps_the_verdict_into_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate.write(OPEN_JOIN, doc("blackout:outage:tn:x"), tmp, ("a.json", "b.json"))
            for name in ("a.json", "b.json"):
                got = json.loads((Path(tmp) / name).read_text(encoding="utf-8"))
                self.assertEqual(got["gate"]["review_class"], "open")


class Check(unittest.TestCase):
    def test_check_finds_a_held_join_planted_in_a_deployed_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site" / "data"
            site.mkdir(parents=True)
            held = doc("blackout:ooni:ir:x", "noisefloor-event-1", join=IRAN_JOIN)
            held["gate"] = {"review_class": "open", "may_publish": True}     # a forged stamp
            (site / "three.json").write_text(json.dumps(held), encoding="utf-8")
            (site / "today.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                                encoding="utf-8")
            ok = doc("blackout:outage:tn:x", join=OPEN_JOIN | {"published": True})
            (site / "ok.json").write_text(json.dumps(ok), encoding="utf-8")
            offences = gate.check([Path(tmp) / "site"])
            self.assertEqual([Path(p).name for p, _, _ in offences], ["three.json"])

    def test_the_real_deployed_folders_are_clean(self):
        self.assertEqual(gate.check(), [])


class TriageThroughTheGate(unittest.TestCase):
    def test_the_outage_triage_join_is_open_and_switched_on(self):
        v = gate.review(tri.JOIN)
        self.assertEqual((v["review_class"], v["may_publish"]), ("open", True))
        # Switched off, the gate refuses it a deployed folder again.
        off = gate.review(tri.JOIN | {"published": False})
        self.assertIsNotNone(gate.allowed(off, ROOT / "site" / "data"))

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
