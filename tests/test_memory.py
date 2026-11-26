"""COP's memory: a subject is kept after it leaves, and a new item is compared with what came before."""
import json
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


def ctx(day, entity="01_Ukraine_Conflict", stated=True, countries=("UA",)):
    """A Context zone item as its feed sent it before (dated id only) and after contract 2.1 (subject_id too)."""
    p = {"id": f"context:{day}_1200Z:{entity}", "program": "context", "cop_program": "context", "title": entity,
         "countries": list(countries), "data_as_of": f"{day}T12:00:00Z"}
    if stated:
        p["subject_id"] = f"context:zone:{entity}"
    return {"type": "Feature", "geometry": None, "properties": p}


class SubjectIdArrives(unittest.TestCase):
    """ideas#205: Context began stating subject_id on 24 Nov 2026; its zones keep their history."""

    def build(self, gap_day=None):
        mem = m.empty()
        for day in ("2026-11-20", "2026-11-21", "2026-11-22"):
            m.fold(mem, snap(day, [ctx(day, stated=False)], read=("context",)))
        if gap_day:
            m.fold(mem, snap(gap_day, [], read=("context",)))
        m.fold(mem, snap("2026-11-24", [ctx("2026-11-24")], read=("context",)))
        return mem

    def test_the_new_key_continues_the_old_record(self):
        mem = self.build()
        self.assertEqual(m.continues(mem, "context:zone:01_Ukraine_Conflict"), "context:01_Ukraine_Conflict")
        r = m.subject(mem, "context:zone:01_Ukraine_Conflict")
        self.assertEqual((r["first"], r["spans"]), ("2026-11-20", [["2026-11-20", "2026-11-24"]]))
        said = m.compare(mem, ctx("2026-11-24")["properties"], "2026-11-24")
        self.assertEqual((said["state"], said["days"]), ("running", 5))

    def test_a_day_read_without_the_subject_is_still_a_gap(self):
        mem = self.build(gap_day="2026-11-23")
        said = m.compare(mem, ctx("2026-11-24")["properties"], "2026-11-24")
        self.assertEqual((said["state"], said["gap_days"]), ("back", 2))

    def test_no_stored_record_is_changed_or_lost(self):
        mem = self.build()
        before = json.dumps(mem, sort_keys=True)
        m.compare(mem, ctx("2026-11-24")["properties"], "2026-11-24")
        m.similar_index(mem)
        self.assertEqual(json.dumps(mem, sort_keys=True), before)
        self.assertEqual(set(mem["subjects"]), {"context:01_Ukraine_Conflict", "context:zone:01_Ukraine_Conflict"})

    def test_the_subject_is_listed_once_and_is_not_its_own_last_like_it(self):
        mem = self.build()
        joined = m.joined_subjects(mem)
        self.assertNotIn("context:01_Ukraine_Conflict", joined)
        self.assertEqual(joined["context:zone:01_Ukraine_Conflict"]["was"], "context:01_Ukraine_Conflict")
        said = m.compare(mem, ctx("2026-11-24")["properties"], "2026-11-24")
        self.assertNotIn("last_similar", said)

    def test_another_program_or_a_stated_old_subject_is_not_joined(self):
        mem = self.build()
        mem["subjects"]["context:01_Ukraine_Conflict"]["p"] = "watchfloor"
        self.assertIsNone(m.continues(mem, "context:zone:01_Ukraine_Conflict"))
        mem = m.empty()
        m.fold(mem, snap("2026-11-01", [item("wake:ukmto:a", "2026-11-01")]))
        self.assertIsNone(m.continues(mem, "wake:ukmto:a"))


class SeriesScope(unittest.TestCase):
    SCOPES = {"watchfloor:zone:z": [{"from": "2026-11-18", "scope": "wide"}]}

    def mem(self):
        days = {"2026-11-16": 100, "2026-11-17": 110, "2026-11-18": 200, "2026-11-19": 210}
        return {"subjects": {"watchfloor:zone:z": {"v": dict(days)}, "watchfloor:zone:other": {"v": dict(days)}}}

    def test_a_scope_change_starts_a_new_series_and_keeps_the_old(self):
        mem = m.stamp_scopes(self.mem(), self.SCOPES)
        r = mem["subjects"]["watchfloor:zone:z"]
        self.assertEqual(len(r["v"]), 4)
        self.assertEqual(r["vs"]["2026-11-17"], m.FIRST_SCOPE)
        self.assertEqual(r["vs"]["2026-11-18"], "wide")
        self.assertEqual(m.current_series(r), {"2026-11-18": 200, "2026-11-19": 210})

    def test_a_series_with_no_scope_change_is_untouched(self):
        r = m.stamp_scopes(self.mem(), self.SCOPES)["subjects"]["watchfloor:zone:other"]
        self.assertNotIn("vs", r)
        self.assertEqual(len(m.current_series(r)), 4)

    def test_a_stated_scope_named_by_a_listed_entry_continues_it(self):
        mem = self.mem()
        r = mem["subjects"]["watchfloor:zone:z"]
        r["v"]["2026-11-24"] = 220
        r["fs"] = {"2026-11-24": "abc"}
        scopes = {"watchfloor:zone:z": [{"from": "2026-11-18", "scope": "wide", "zone_scope": "abc"}]}
        r = m.stamp_scopes(mem, scopes)["subjects"]["watchfloor:zone:z"]
        self.assertEqual(r["vs"]["2026-11-24"], "wide")
        self.assertEqual(m.current_series(r), {"2026-11-18": 200, "2026-11-19": 210, "2026-11-24": 220})

    def test_a_new_stated_scope_starts_a_new_series_with_no_listed_entry(self):
        mem = self.mem()
        r = mem["subjects"]["watchfloor:zone:other"]
        r["v"].update({"2026-11-24": 300, "2026-11-25": 310, "2026-11-26": 900, "2026-11-27": 910})
        r["fs"] = {"2026-11-24": "one", "2026-11-25": "one", "2026-11-26": "two", "2026-11-27": "two"}
        r = m.stamp_scopes(mem, {})["subjects"]["watchfloor:zone:other"]
        self.assertEqual(len(r["v"]), 8)
        self.assertEqual(r["vs"]["2026-11-16"], m.STATED + "one")     # before any stated day: the first stated scope
        self.assertEqual(m.current_series(r), {"2026-11-26": 900, "2026-11-27": 910})

    def test_a_day_the_feed_left_unstated_keeps_the_scope_before_it(self):
        labels = m.scope_labels([], {"2026-11-24": "one", "2026-11-26": "two"},
                                ["2026-11-24", "2026-11-25", "2026-11-26"])
        self.assertEqual(labels["2026-11-25"], m.STATED + "one")

    def test_fold_keeps_the_stated_scope_with_each_value(self):
        f = item("watchfloor:2026-11-24:zone:11_Iran_Red_Sea", "2026-11-24", program="watchfloor",
                 cop_program="watchfloor", subject_id="watchfloor:zone:11_Iran_Red_Sea",
                 measures={"violent_articles": 124, "zone_scope": "2bc2156e602d", "catalog_version": "2026-11-24.1"})
        mem = m.fold(m.empty(), snap("2026-11-25", [f], read=("watchfloor",)))
        r = mem["subjects"]["watchfloor:zone:11_Iran_Red_Sea"]
        self.assertEqual(r["fs"], {"2026-11-24": "2bc2156e602d"})
        self.assertEqual(r["vs"]["2026-11-24"], "nine-countries")

    def test_the_listed_scopes_are_well_formed(self):
        for changes in m.load_scopes().values():
            for change in changes:
                self.assertEqual(m.day_of(change["from"]), change["from"])
                self.assertTrue(change["scope"] and change["scope"] != m.FIRST_SCOPE and change["source"])


if __name__ == "__main__":
    unittest.main()
