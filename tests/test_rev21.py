"""COP reads feed contract revision 2.1 (kit 0.6.0) and the old shape alike: python tests/test_rev21.py

Step 3 of the feed-contract migration (the private planning notes section 5): the new field
first, the old reading as fallback. Fixtures: tests/fixtures/rev21/old/blackout.geojson is three items of Blackout's
live feed of 18 Nov 2026 as Blackout sent them; rev21/new/ is the same items with the 2.1 fields Blackout would state,
set to what COP reads from the old shape, and a 2.1 header (tests/make_rev21_fixtures.py writes both). So a build
reads both alike, and where a stated field differs from the old reading the stated field wins.
"""

import copy
import gzip
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REV = ROOT / "tests" / "fixtures" / "rev21"
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)
import agent_index  # noqa: E402
import ledger  # noqa: E402
import three_signal  # noqa: E402
import triage as tri  # noqa: E402
import undertow_elections  # noqa: E402
from tic_doctrine import contract  # noqa: E402

NOW = "2026-11-18T18:58:00Z"
NEW_FIELDS = set(contract.REVISION_FIELDS)


def build(shape):
    out = Path(tempfile.mkdtemp())
    with redirect_stdout(io.StringIO()):
        snap.main(["--fixtures", str(REV / shape), "--out", str(out), "--ended-out", str(out / "ended"), "--now", NOW])
    today = json.loads((out / "today.geojson").read_text(encoding="utf-8"))
    health = json.loads((out / "health.json").read_text(encoding="utf-8"))
    picks = json.loads((out / "picks.json").read_text(encoding="utf-8"))
    return today, health, picks


def blackout_props(shape):
    feed = json.loads((REV / shape / "blackout.geojson").read_text(encoding="utf-8"))
    return {f["properties"]["id"]: f["properties"] for f in feed["features"]}


def without_new(p):
    q = {k: v for k, v in p.items() if k not in NEW_FIELDS}
    q["sources"] = [{k: v for k, v in s.items() if k != "lane"} for s in p.get("sources") or []]
    return q


class BothShapesBuildAlike(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old, cls.new = build("old"), build("new")

    def test_the_picture_and_picks_are_the_same_apart_from_the_new_fields(self):
        (t0, _, p0), (t1, _, p1) = self.old, self.new
        self.assertEqual([without_new(f["properties"]) for f in t0["features"]],
                         [without_new(f["properties"]) for f in t1["features"]])
        self.assertEqual(p0["picks"], p1["picks"])

    def test_the_new_fields_pass_through_untouched(self):
        t1 = self.new[0]
        stated = blackout_props("new")
        for f in t1["features"]:
            p = f["properties"]
            for k in NEW_FIELDS:
                self.assertEqual(p.get(k), stated[p["id"]].get(k), (p["id"], k))

    def test_health_counts_coverage_and_the_header_and_keeps_lint_as_before(self):
        h0 = next(h for h in self.old[1]["programs"] if h["id"] == "blackout")
        h1 = next(h for h in self.new[1]["programs"] if h["id"] == "blackout")
        self.assertEqual(set(h0["coverage"]), NEW_FIELDS)
        self.assertEqual(sum(h0["coverage"].values()), 0)
        self.assertEqual(h1["coverage"], {"last_change": 3, "last_change_at": 2, "event_start": 3, "event_end": 1,
                                          "event_time_precision": 1, "subject_id": 3, "signal": 1, "sea_areas": 3,
                                          "place_basis": 3, "corroboration": 0})   # corroboration: kit 0.6.2 (D11)
        self.assertEqual(h0["revision"]["contract_rev"], None)
        self.assertEqual(h1["revision"], {"contract_rev": "2.1", "kit": "0.6.0", "notes": 1})
        self.assertGreater(h0["revision"]["notes"], 0)
        # The 2.1 notices are counted under revision, never as lint warnings, so warnings read as with kit 0.5.12.
        self.assertEqual(h0["lint"], h1["lint"])
        self.assertEqual(h0["lint"]["errors"], 0)
        # A program whose feed did not come has no header to read.
        dead = next(h for h in self.old[1]["programs"] if h["id"] == "watchfloor")
        self.assertIsNone(dead["revision"])


class StatedFieldsWin(unittest.TestCase):
    def setUp(self):
        self.old, self.new = blackout_props("old"), blackout_props("new")

    def test_blackout_start_times_read_alike_from_prose_and_from_event_start(self):
        for iid in self.old:
            a, b = tri.outage_input(self.old[iid]), tri.outage_input(self.new[iid])
            self.assertEqual(a, b, iid)
        radar = "blackout:radar-anomaly:c595977b-aa94-4659-bb59-45d1884015f0"
        self.assertEqual(tri.outage_input(self.new[radar])["at"], tri.utc("2026-11-17T00:15:00Z"))

    def test_event_start_and_end_win_over_the_prose(self):
        radar = copy.deepcopy(self.new["blackout:radar-anomaly:c595977b-aa94-4659-bb59-45d1884015f0"])
        radar.update(event_start="2026-11-16T09:00:00Z", event_end="2026-11-17T03:00:00Z")
        inp = tri.outage_input(radar)
        self.assertEqual((inp["at"], inp["ended"], inp["timed_from"]),
                         (tri.utc("2026-11-16T09:00:00Z"), "2026-11-17T03:00:00Z", "start"))
        outage = copy.deepcopy(self.new["blackout:outage:nl:2026-11-18T02"])
        outage.update(event_start="2026-11-18T02:12:00Z", event_end="2026-11-18T02:28:00Z")
        inp = tri.outage_input(outage)
        self.assertEqual((inp["at"], inp["ended"]), (tri.utc("2026-11-18T02:12:00Z"), "2026-11-18T02:28:00Z"))

    def test_ooni_rate_from_signal_else_title(self):
        ooni_old, ooni_new = self.old["blackout:ooni:ir:2026-11-18"], self.new["blackout:ooni:ir:2026-11-18"]
        self.assertEqual(tri.item_rate(ooni_old), 36.0)
        self.assertEqual(tri.item_rate(ooni_new), 36.0)
        moved = dict(ooni_new, signal={"name": "ooni_anomaly_rate", "value": 0.5, "unit": "share"})
        self.assertEqual(tri.item_rate(moved), 50.0)
        self.assertEqual(tri.item_rate(dict(ooni_new, signal={"name": "x", "value": 41.2, "unit": "percent"})), 41.2)
        self.assertEqual(tri.item_rate(dict(ooni_new, signal={"name": "x", "value": 7, "unit": "measurements"})), 36.0)

    def test_hazard_interval_reads_event_fields_with_precision(self):
        p = {"id": "elements:nws:X", "data_as_of": "2026-11-18T10:00:00Z", "valid_until": "2026-11-19T00:00:00Z",
             "measures": {"onset": "2026-11-18T06:00:00Z"}}
        self.assertEqual(tri._interval(p), (tri.utc("2026-11-18T06:00:00Z"), tri.utc("2026-11-19T00:00:00Z")))
        p2 = dict(p, event_start="2026-11-17T00:00:00Z", event_end="2026-11-17T00:00:00Z", event_time_precision="day")
        self.assertEqual(tri._interval(p2), (tri.utc("2026-11-17T00:00:00Z"), tri.utc("2026-11-18T00:00:00Z")))
        p3 = dict(p, event_start="2026-11-18T04:00:00Z")          # no end stated: valid_until still closes it
        self.assertEqual(tri._interval(p3), (tri.utc("2026-11-18T04:00:00Z"), tri.utc("2026-11-19T00:00:00Z")))


def cordon(iid, cc="IR", dated="2026-11-18T12:00:00Z", **props):
    p = {"id": iid, "title": iid, "tier": "anomaly", "cop_program": "cordon", "countries": [cc], "data_as_of": dated,
         "measures": {"level": 3}} | props
    return {"type": "Feature", "geometry": None, "properties": p}


class NoticesReadLastChange(unittest.TestCase):
    AT = tri.utc("2026-11-18T18:00:00Z")

    def bucket(self, f):
        out = tri.notices([f], "IR", self.AT)
        return next(k for k, v in out.items() if v)

    def test_an_advisory_with_no_stated_change_reads_as_before(self):
        self.assertEqual(self.bucket(cordon("cordon:advisory:IR")), "recent")      # level unknown: counts

    def test_standing_and_edits_do_not_count_and_a_raise_counts_from_its_own_time(self):
        self.assertEqual(self.bucket(cordon("cordon:advisory:IR", last_change="none")), "recent_not_raised")
        self.assertEqual(self.bucket(cordon("cordon:advisory:IR", last_change="changed")), "recent_not_raised")
        self.assertEqual(self.bucket(cordon("cordon:advisory:IR", last_change="raised",
                                            last_change_at="2026-11-18T09:00:00Z")), "recent")
        # Raised long before the outage: the data_as_of in the window is an edit date, the raise is standing.
        self.assertEqual(self.bucket(cordon("cordon:advisory:IR", last_change="raised",
                                            last_change_at="2026-11-01T09:00:00Z")), "standing")

    def test_a_country_only_named_is_skipped_by_the_join(self):
        out = tri.notices([cordon("cordon:advisory:IR", place_basis="named")], "IR", self.AT)
        self.assertFalse(any(out.values()))
        self.assertEqual(tri.place_countries({"countries": ["IR"], "place_basis": "subject"}), ["IR"])

    def test_three_signal_change_time(self):
        dated = tri.utc("2026-11-18T12:00:00Z")
        self.assertEqual(three_signal.changed_at({}, dated), dated)
        self.assertIsNone(three_signal.changed_at({"last_change": "none"}, dated))
        self.assertEqual(three_signal.changed_at({"last_change": "new", "last_change_at": "2026-11-17T01:00:00Z"},
                                                 dated), tri.utc("2026-11-17T01:00:00Z"))
        self.assertEqual(three_signal.changed_at({"last_change": "raised"}, dated), dated)


class EventsAndPeriods(unittest.TestCase):
    BASE = {"id": "orbit:upcoming:x", "title": "Falcon 9 | Starlink, 2026-11-20 05:10 UTC",
            "summary": "(status Go for Launch; NET precision Minute)", "data_as_of": "2026-11-18T00:00:00Z"}

    def test_orbit_launch_time_from_event_start_else_title(self):
        ev, _ = agent_index.orbit_event(self.BASE)
        self.assertEqual((ev["date"], ev["at"]), ("2026-11-20", "2026-11-20T05:10:00Z"))
        ev, _ = agent_index.orbit_event(dict(self.BASE, event_start="2026-11-21T06:00:00Z"))
        self.assertEqual((ev["date"], ev["at"]), ("2026-11-21", "2026-11-21T06:00:00Z"))
        ev, _ = agent_index.orbit_event(dict(self.BASE, event_start="2026-11-22T00:00:00Z", event_time_precision="day"))
        self.assertEqual((ev["date"], ev["at"]), ("2026-11-22", None))
        ev, why = agent_index.orbit_event(dict(self.BASE, event_start="2026-12-01T00:00:00Z",
                                               event_time_precision="month"))
        self.assertIsNone(ev)
        self.assertEqual(why, "event_time_precision month")

    def test_undertow_period_from_event_fields_first(self):
        p = {"id": "UT-X-1", "data_as_of": "2026-06-30T00:00:00Z", "operation": {"period": "2026-01 to 2026-06"}}
        (a, b, src, _), _ = undertow_elections.item_period(p)
        self.assertEqual((str(a), str(b), src), ("2026-01-01", "2026-06-30", "disclosure period"))
        q = dict(p, event_start="2026-03-01T00:00:00Z", event_end="2026-04-01T00:00:00Z", event_time_precision="month")
        (a, b, src, _), _ = undertow_elections.item_period(q)
        self.assertEqual((str(a), str(b), src), ("2026-03-01", "2026-04-30", "event time"))


class ShapesThatWouldRaise(unittest.TestCase):
    def feature(self, **props):
        p = {"id": "x:1", "title": "t", "sources": [{"id": "s"}]} | props
        return {"type": "Feature", "geometry": None, "properties": p}

    def test_bad_new_fields_are_left_out_not_fatal(self):
        self.assertIsNone(snap.unsound(self.feature(subject_id="x", sea_areas=["persian-gulf"])))
        self.assertEqual(snap.unsound(self.feature(subject_id=["x"])), "subject_id is not a string")
        self.assertEqual(snap.unsound(self.feature(subject_id=" ")), "subject_id is not a string")
        self.assertEqual(snap.unsound(self.feature(sea_areas="persian-gulf")), "sea_areas is not a list of strings")
        self.assertEqual(snap.unsound(self.feature(sources=[{"id": "s", "lane": ["a"]}])),
                         "a source's lane is not a string")

    def test_a_21_feed_is_checked_strictly(self):
        f = self.feature(event_start="yesterday")
        _, problems, _ = snap.sift([f], "blackout", rev="2.1")
        self.assertTrue(any(sev == "error" for _, (std, sev, msg) in problems), problems)


def item(iid, subject=None, tier="routine"):
    p = {"id": iid, "title": iid, "tier": tier, "program": "blackout", "cop_program": "blackout", "cop_stale": False,
         "sources": [{"id": "ooni-api"}]}
    if subject:
        p["subject_id"] = subject
    return {"type": "Feature", "geometry": None, "properties": p}


def snapshot(at, *items):
    return {"type": "FeatureCollection", "program": "cop", "generated_at": at,
            "cop_programs": {"blackout": {"name": "Blackout", "state": "fresh"}}, "features": list(items)}


class LedgerKeyedOnSubject(unittest.TestCase):
    A, B, C = "2026-11-18T00:20:00Z", "2026-11-18T02:20:00Z", "2026-11-18T04:20:00Z"

    def test_a_dated_reading_of_one_subject_is_an_update_not_new_and_ended(self):
        s1, lines = ledger.step({}, snapshot(self.A, item("blackout:ooni:ir:2026-11-17", "blackout:ooni:ir")))
        self.assertEqual([x["change"] for x in lines], ["new"])
        self.assertEqual(s1["keyed_on"], ledger.KEYED_ON)
        s2, lines = ledger.step(s1, snapshot(self.B, item("blackout:ooni:ir:2026-11-18", "blackout:ooni:ir")))
        self.assertEqual(lines, [])
        self.assertEqual(list(s2["open"]), ["blackout:ooni:ir"])
        # Without subject_id, the same two readings are a new item and an end, as before.
        s1, _ = ledger.step({}, snapshot(self.A, item("blackout:ooni:ir:2026-11-17")))
        _, lines = ledger.step(s1, snapshot(self.B, item("blackout:ooni:ir:2026-11-18")))
        self.assertEqual(sorted(x["change"] for x in lines), ["ended", "new"])

    def test_the_first_build_with_subject_ids_moves_open_entries_and_logs_nothing(self):
        s1, _ = ledger.step({}, snapshot(self.A, item("blackout:outage:nl")))
        s2, lines = ledger.step(s1, snapshot(self.B, item("blackout:outage:nl", "blackout:outage:nl-subject")))
        self.assertEqual(lines, [])
        self.assertEqual(list(s2["open"]), ["blackout:outage:nl-subject"])

    def test_the_old_state_is_copied_once_and_never_overwritten(self):
        folder = Path(tempfile.mkdtemp())
        old_state = {"format": "tic-history/1", "updated_at": self.A, "recently_ended": {},
                     "open": {}, "programs_read": ["blackout"], "lanes_read": ["blackout"]}
        with open(folder / "state.json.gz", "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(json.dumps(old_state).encode("utf-8"))
        before = (folder / "state.json.gz").read_bytes()
        ledger.run(snapshot(self.B, item("blackout:ooni:ir:2026-11-18", "blackout:ooni:ir")), folder)
        copy_path = folder / ledger.BEFORE_KEYING
        self.assertEqual(copy_path.read_bytes(), before)
        self.assertEqual(ledger.load_state(folder)["keyed_on"], ledger.KEYED_ON)
        self.assertNotEqual((folder / "state.json.gz").read_bytes(), before)
        ledger.run(snapshot(self.C, item("blackout:ooni:ir:2026-11-18", "blackout:ooni:ir")), folder)
        self.assertEqual(copy_path.read_bytes(), before)       # the copy is never written again
        # A first ledger has nothing to copy.
        fresh = Path(tempfile.mkdtemp())
        ledger.run(snapshot(self.A, item("a:1")), fresh, first=True)
        self.assertFalse((fresh / ledger.BEFORE_KEYING).exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
