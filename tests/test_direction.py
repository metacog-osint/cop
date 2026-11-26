"""Direction of concern: each layer against its own last two weeks, counted and never added."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import direction as d  # noqa: E402
import gate  # noqa: E402


def l(state, layer="notices"):
    return {"layer": layer, "state": state, "name": layer}


class Direction(unittest.TestCase):
    def test_a_count_moves_only_on_a_real_change(self):
        self.assertEqual(d.count_move(12, 6, 7, 14), "up")
        self.assertEqual(d.count_move(1, 20, 7, 14), "down")
        self.assertEqual(d.count_move(7, 12, 7, 14), "steady", "7 against about 6 a week is not a rise")
        self.assertEqual(d.count_move(2, 0, 7, 14), "steady", "too few events to move")

    def test_a_level_must_leave_its_middle_half(self):
        self.assertEqual(d.level_move([200] * 7, [100, 110, 90, 105, 95, 100, 120, 80]), "up")
        self.assertEqual(d.level_move([100] * 7, [100, 110, 90, 105, 95, 100, 120, 80]), "steady")

    def test_the_word_counts_layers(self):
        self.assertEqual(d.word_for([l("up"), l("up"), l("steady")]), "rising")
        self.assertEqual(d.word_for([l("down"), l("down"), l("up")]), "easing")
        self.assertEqual(d.word_for([l("up"), l("down"), l("steady")]), "mixed")
        self.assertEqual(d.word_for([l("up"), l("steady")]), "edging_up")
        self.assertEqual(d.word_for([l("steady"), l("quiet")]), "steady")
        self.assertEqual(d.word_for([l("thin"), l("thin")]), "building")

    def test_a_measure_on_two_cards_says_what_each_covers(self):
        areas = [{"area": "gulf", "name": "Gulf"}, {"area": "shutdowns", "name": "Shutdowns", "worldwide": True}]
        names = []
        for a in areas:
            out = d.read_area({"subjects": {}, "days": {}}, {"features": []}, a, ["outages"], "2026-11-23", {}, {})
            names.append(out["layers"][0]["name"])
        self.assertEqual(len(set(names)), 2)
        self.assertIn("worldwide", names[1])

    def test_ship_transits_are_a_condition_and_never_count(self):
        self.assertEqual(d.word_for([l("up", "traffic"), l("steady"), l("steady")]), "steady")

    def test_a_transit_count_without_a_baseline_is_still_given(self):
        area = {"chokepoint": "kerch"}
        item = {"id": "wake:chokepoint:kerch:2026-11-15", "data_as_of": "2026-11-15T00:00:00Z",
                "measures": {"n_total_7d": 0.4, "change_90d_pct": None, "z_90d": None, "baseline_90d_median": 0}}
        snap = {"features": [{"properties": item}]}
        got = d.read_traffic(snap, area)
        self.assertEqual(got["state"], "thin")
        self.assertIn("0.4 a day", got["text"])
        self.assertIn("too thin to compare", got["text"])
        self.assertNotIn("No current transit count", got["text"])
        self.assertTrue(got["condition"])
        for gone in ({"features": []}, {"features": [{"properties": item | {"cop_stale": True}}]}):
            self.assertEqual(d.read_traffic(gone, area)["text"], "No current transit count.",
                             "a missing or stale item has no current count")

    def test_a_count_that_rounds_to_its_median_reads_level(self):
        def line(pct):
            item = {"id": "wake:chokepoint:x:2026-11-22", "data_as_of": "2026-11-22T00:00:00Z",
                    "measures": {"n_total_7d": 27, "change_90d_pct": pct, "z_90d": -0.1, "baseline_90d_median": 27.14}}
            return d.read_traffic({"features": [{"properties": item}]}, {"chokepoint": "x"})["text"]
        self.assertIn("level with its 90-day median", line(-0.5))
        self.assertNotIn("0% below", line(-0.5))
        self.assertIn("12% below", line(-12.0))
        self.assertIn("3% above", line(3.2))

    def test_one_alert_under_two_ids_counts_once(self):
        a = d._same("cordon:alert:UA:1", {"c": ["UA"], "t": "Security Alert: Kyiv"})
        b = d._same("cordon:alert:UA:2", {"c": ["UA"], "t": "Security  alert: kyiv"})
        self.assertEqual(a, b)

    def test_a_holiday_travel_alert_is_not_a_security_notice(self):
        r = {"t": "US embassy (UA): Security Alert: Holiday Travel, November 13, 2026"}
        self.assertFalse(d.in_family("cordon:alert:UA:1", r, "notices"))

    def test_press_does_not_compare_across_a_change_of_scope(self):
        days = [f"2026-11-{n:02d}" for n in range(2, 23)]
        v = {x: (100 if x < "2026-11-18" else 200) for x in days}
        vs = {x: ("original" if x < "2026-11-18" else "wide") for x in days}
        area = {"press_zone": "z"}
        mem = {"subjects": {"watchfloor:zone:z": {"id": "x", "v": v}}}
        self.assertEqual(d.read_press(mem, area, "2026-11-23", d._d("2026-11-22"))["state"], "up")
        mem["subjects"]["watchfloor:zone:z"]["vs"] = vs
        out = d.read_press(mem, area, "2026-11-23", d._d("2026-11-22"))
        self.assertEqual(out["state"], "thin")
        self.assertIn("changed on 18 Nov", out["text"])

    def test_the_voice_rule(self):
        text = (d.WHAT + d.NOT_A_WARNING).lower()
        self.assertIn("not a warning", text)
        for word in ("strike", "attack likely", "imminent", "intent"):
            self.assertNotIn(word, text)

    def test_the_join_is_declared(self):
        doc = {"join": d.JOIN, "x": [{"id": "wake:ukmto:ukmto-1"}, {"id": "verdict:2026-11-16:a"}]}
        self.assertTrue(gate.review(d.JOIN, doc)["may_publish"])

    def test_the_areas_are_well_formed(self):
        cfg = json.loads((ROOT / "config" / "areas.json").read_text(encoding="utf-8"))
        names = [a["area"] for a in cfg["problems"] + cfg["areas"]]
        self.assertEqual(len(names), len(set(names)))

    def test_a_pick_names_the_moving_places_it_falls_in(self):
        cfg = {"problems": [{"area": "gulf", "name": "Gulf", "center": [56.4, 27.2], "radius_km": 900,
                             "countries": ["IR"]},
                            {"area": "shutdowns", "name": "Shutdowns", "worldwide": True}],
               "areas": [{"area": "ukraine", "name": "Ukraine", "countries": ["UA"]},
                         {"area": "sudan", "name": "Sudan", "countries": ["SD"]}]}
        word = lambda a, w: {"area": a, "name": a, "direction": w, "word": d.WORDS[w], "up": ["x"], "down": []}
        doc = {"problems": [word("gulf", "rising"), word("shutdowns", "rising")],
               "areas": [word("ukraine", "edging_up"), word("sudan", "steady")]}
        pt = lambda lon, lat, cc: {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                                   "properties": {"id": f"noisefloor-event-{cc}", "countries": [cc]}}
        snap = {"features": [pt(55.0, 26.0, "IR"), pt(31.0, 49.0, "UA"), pt(30.0, 15.0, "SD")]}
        picks = {"picks": [{"id": "noisefloor-event-IR"}, {"id": "noisefloor-event-UA"}, {"id": "noisefloor-event-SD"},
                           {"id": "gone"}]}
        out = {x["pick"]: x for x in d.pick_places(doc, snap, picks, cfg)}
        self.assertEqual([p["area"] for p in out["noisefloor-event-IR"]["places"]], ["gulf"])  # never the worldwide one
        self.assertTrue(out["noisefloor-event-IR"]["rising"])
        self.assertFalse(out["noisefloor-event-UA"]["rising"])            # Edging up is named, never reordered
        self.assertNotIn("noisefloor-event-SD", out)                       # a steady place says nothing
        doc["picks"] = list(out.values())
        self.assertTrue(gate.review(d.JOIN, doc)["may_publish"], "pick ids are not citations of the join")


if __name__ == "__main__":
    unittest.main()
