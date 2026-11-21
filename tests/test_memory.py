"""COP's memory: a subject is kept after it leaves, and a new item is compared with what came before."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import memory as m  # noqa: E402


def item(i, day, **extra):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [56.0, 27.0]},
            "properties": {"id": i, "program": "wake", "cop_program": "wake", "title": i, "countries": [],
                           "data_as_of": f"{day}T06:00:00Z", **extra}}


def snap(day, items, read=("wake",)):
    return {"generated_at": f"{day}T12:00:00Z", "cop_programs": {p: {"state": "fresh"} for p in read}, "features": items}


class Memory(unittest.TestCase):
    def test_a_subject_that_leaves_is_kept_and_its_return_is_named(self):
        mem = m.empty()
        m.fold(mem, snap("2026-11-01", [item("wake:ukmto:a", "2026-11-01")]))
        m.fold(mem, snap("2026-11-02", []))
        m.fold(mem, snap("2026-11-05", [item("wake:ukmto:a", "2026-11-05")]))
        self.assertEqual(mem["subjects"]["wake:ukmto:a"]["spans"], [["2026-11-01", "2026-11-01"], ["2026-11-05", "2026-11-05"]])
        said = m.compare(mem, {"id": "wake:ukmto:a"}, "2026-11-05")
        self.assertEqual((said["state"], said["gap_days"]), ("back", 4))

    def test_a_day_the_program_was_not_read_is_not_a_gap(self):
        mem = m.empty()
        m.fold(mem, snap("2026-11-01", [item("wake:ukmto:a", "2026-11-01")]))
        m.fold(mem, snap("2026-11-02", [], read=()))
        m.fold(mem, snap("2026-11-03", [item("wake:ukmto:a", "2026-11-01")]))
        self.assertEqual(mem["subjects"]["wake:ukmto:a"]["spans"], [["2026-11-01", "2026-11-03"]])

    def test_a_new_item_is_compared_with_the_last_like_it_nearby(self):
        mem = m.empty()
        m.fold(mem, snap("2026-11-01", [item("wake:ukmto:a", "2026-11-01")]))
        m.fold(mem, snap("2026-11-04", [item("wake:ukmto:b", "2026-11-04")]))
        said = m.compare(mem, {"id": "wake:ukmto:b"}, "2026-11-04")
        self.assertEqual((said["state"], said["last_similar"]), ("new", "2026-11-01"))
        self.assertIn("3 days earlier", said["line"])

    def test_a_dated_id_is_one_subject(self):
        self.assertEqual(m.key({"id": "watchfloor:2026-11-20:zone:07_Russia_NATO"}), "watchfloor:zone:07_Russia_NATO")
        self.assertEqual(m.key({"id": "wake:chokepoint:strait-of-hormuz:2026-11-15"}), "wake:chokepoint:strait-of-hormuz")
        self.assertEqual(m.key({"id": "x", "subject_id": "s"}), "s")

    def test_only_major_and_notable_jamming_events_are_kept(self):
        rows = m.event_rows({"anomalies": [{"m": "jam", "tier": "major", "lat": 1, "lon": 2},
                                           {"m": "jam", "tier": "minor", "lat": 1, "lon": 2},
                                           {"m": "acc", "tier": "major", "lat": 1, "lon": 2}]})
        self.assertEqual(rows, [[1, 2, 2]])

    def test_the_memory_is_never_written_to_the_site(self):
        with self.assertRaises(SystemExit):
            m._folder(ROOT / "site" / "data")

    def test_it_survives_a_save(self):
        mem = m.empty()
        m.fold(mem, snap("2026-11-01", [item("wake:ukmto:a", "2026-11-01")]))
        with tempfile.TemporaryDirectory() as d:
            m.save(d, mem)
            self.assertEqual(m.load(d)["subjects"], mem["subjects"])


if __name__ == "__main__":
    unittest.main()
