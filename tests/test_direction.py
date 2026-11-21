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

    def test_ship_transits_are_a_condition_and_never_count(self):
        self.assertEqual(d.word_for([l("up", "traffic"), l("steady"), l("steady")]), "steady")

    def test_one_alert_under_two_ids_counts_once(self):
        a = d._same("cordon:alert:UA:1", {"c": ["UA"], "t": "Security Alert: Kyiv"})
        b = d._same("cordon:alert:UA:2", {"c": ["UA"], "t": "Security  alert: kyiv"})
        self.assertEqual(a, b)

    def test_a_holiday_travel_alert_is_not_a_security_notice(self):
        r = {"t": "US embassy (UA): Security Alert: Holiday Travel, November 13, 2026"}
        self.assertFalse(d.in_family("cordon:alert:UA:1", r, "notices"))

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


if __name__ == "__main__":
    unittest.main()
