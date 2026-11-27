"""The direction log: locked on Monday, append-only, chained, and never holding a word from before its first week."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import direction as d  # noqa: E402
import direction_log as log  # noqa: E402

CONFIG = {"problems": [], "areas": [{"area": "gulf", "name": "Gulf", "press_zone": "z", "countries": ["IR"]}]}


def at(text):
    return datetime.fromisoformat(text + "+00:00").astimezone(timezone.utc)


def reading(when):
    return {"join": {"version": "0.1"}, "generated_at": when, "problems": [], "areas": [{
        "area": "gulf", "name": "Gulf", "direction": "edging_up", "press_zone": "z",
        "layers": [
            {"layer": "press", "program": "watchfloor", "state": "up",
             "test": {"kind": "level", "recent_median": 160, "prior_median": 120, "prior_q1": 110, "prior_q3": 130,
                      "ra": 7, "rb": 14, "through": "2026-11-22"}},
            {"layer": "outages", "program": "blackout", "state": "thin"},
            {"layer": "traffic", "program": "wake", "state": "up", "condition": True}]}]}


def memory():
    days = {f"2026-11-{n}": ["watchfloor"] for n in range(23, 30)}
    return {"days": days, "events": {"noisefloor": {}},
            "subjects": {"watchfloor:zone:z": {"p": "watchfloor", "at": [], "v": {x: 150 for x in days}}}}


class DirectionLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "log.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def rows(self):
        return [r for _, r in log.read_log(self.path)]

    def test_nothing_is_logged_before_the_first_week(self):
        now = at("2026-11-16T00:30:00")
        self.assertEqual(log.update(self.path, reading("2026-11-16T00:21:00Z"), memory(), CONFIG, now), (0, 0, 0))
        self.assertFalse(self.path.exists())

    def test_the_first_monday_build_locks_and_a_later_one_changes_nothing(self):
        log.update(self.path, reading("2026-11-23T00:21:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"))
        first = self.path.read_bytes()
        later = reading("2026-11-23T02:21:00Z")
        later["areas"][0]["direction"] = "steady"
        log.update(self.path, later, memory(), CONFIG, at("2026-11-23T02:30:00"))
        self.assertEqual(self.path.read_bytes(), first)
        row = self.rows()[0]
        self.assertEqual((row["type"], row["word"], row["up"]), ("call", "edging_up", ["press"]))
        self.assertNotIn("traffic", row["up"], "a condition is never counted")

    def test_a_missed_monday_shows_as_a_gap_and_is_never_locked_late(self):
        log.update(self.path, reading("2026-11-24T00:21:00Z"), memory(), CONFIG, at("2026-11-24T00:30:00"))
        self.assertEqual([r["type"] for r in self.rows()], ["missed"])

    def test_a_stale_reading_is_not_locked_inside_the_window(self):
        log.update(self.path, reading("2026-11-22T20:00:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"))
        self.assertFalse(self.path.exists())

    def test_the_followup_waits_ten_days_and_reads_the_calendar_week(self):
        log.update(self.path, reading("2026-11-23T00:21:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"))
        log.update(self.path, reading("2026-12-02T00:21:00Z"), memory(), CONFIG, at("2026-12-02T00:30:00"))
        self.assertFalse([r for r in self.rows() if r["type"] == "followup"])
        log.update(self.path, reading("2026-12-03T00:21:00Z"), memory(), CONFIG, at("2026-12-03T00:30:00"))
        row = [r for r in self.rows() if r["type"] == "followup"][0]
        self.assertEqual(row["window"], ["2026-11-23", "2026-11-29"])
        self.assertEqual([(x["layer"], x["days_read"], x["median"]) for x in row["layers"]], [("press", 7, 150)])

    def test_a_changed_line_breaks_the_chain(self):
        log.update(self.path, reading("2026-11-23T00:21:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"))
        log.update(self.path, reading("2026-12-03T00:21:00Z"), memory(), CONFIG, at("2026-12-03T00:30:00"))
        text = self.path.read_text(encoding="utf-8")
        self.path.write_text(text.replace("edging_up", "rising", 1), encoding="utf-8", newline="\n")
        with self.assertRaises(SystemExit):
            log.read_log(self.path)

    def test_a_correction_is_appended_once_and_leaves_the_layer_out_of_the_followup(self):
        fix = [{"week": "2026-11-23", "area": "gulf", "layer": "press", "why": "The zone's scope changed."}]
        log.update(self.path, reading("2026-11-23T00:21:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"))
        call_line = self.path.read_text(encoding="utf-8").splitlines()[0]
        log.update(self.path, reading("2026-11-27T00:21:00Z"), memory(), CONFIG, at("2026-11-27T00:30:00"), fix)
        log.update(self.path, reading("2026-11-28T00:21:00Z"), memory(), CONFIG, at("2026-11-28T00:30:00"), fix)
        rows = self.rows()
        self.assertEqual([r["type"] for r in rows], ["call", "correction"], "appended once")
        self.assertEqual(self.path.read_text(encoding="utf-8").splitlines()[0], call_line, "the call is never changed")
        self.assertEqual((rows[1]["area"], rows[1]["layer"], rows[1]["action"]), ("gulf", "press", "left_out_of_followup"))
        log.update(self.path, reading("2026-12-03T00:21:00Z"), memory(), CONFIG, at("2026-12-03T00:30:00"), fix)
        fu = [r for r in self.rows() if r["type"] == "followup"][0]
        self.assertEqual(fu["layers"], [])
        self.assertEqual(fu["left_out"], [{"layer": "press", "why": "The zone's scope changed."}])

    def test_a_correction_for_a_place_or_layer_never_called_is_not_written(self):
        fix = [{"week": "2026-11-23", "area": "nowhere", "layer": "press", "why": "x"},
               {"week": "2026-11-23", "area": "gulf", "layer": "interference", "why": "x"},
               {"week": "2026-11-30", "area": "gulf", "layer": "press", "why": "x"}]
        log.update(self.path, reading("2026-11-23T00:21:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"), fix)
        log.update(self.path, reading("2026-11-27T00:21:00Z"), memory(), CONFIG, at("2026-11-27T00:30:00"), fix)
        self.assertEqual([r["type"] for r in self.rows()], ["call"])

    def test_the_log_holds_no_memory(self):
        log.update(self.path, reading("2026-11-23T00:21:00Z"), memory(), CONFIG, at("2026-11-23T00:30:00"))
        row = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn("subjects", row)
        self.assertEqual(row["thresholds"]["move_ratio"], d.MOVE_RATIO)


if __name__ == "__main__":
    unittest.main()
