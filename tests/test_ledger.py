"""The suite ledger (tools/ledger.py): python tests/test_ledger.py

The line format and its rules are the doctrine kit's (tic-history/1; tested in the kit and, on recorded data, in
Elements). These tests cover what is COP's own: a program is the lane (a program's source, where its feed
names its sources' health), a program that was not read ends nothing, COP's
own marks are no change, the files, the refusal to start over without a state, and that the ledger is never published.
"""

import gzip
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import ledger  # noqa: E402


def item(program, iid, tier="routine", **props):
    p = {"id": iid, "title": iid, "tier": tier, "program": program, "cop_program": program, "cop_stale": False,
         "cop_pick": None, "cop_strength": None, "cop_strength_text": None,
         "sources": [{"id": f"{program}-src", "accessed_at": "2026-11-14T00:00:00Z"}]} | props
    return {"type": "Feature", "geometry": None, "properties": p}


def snap(at, *items, states=None):
    programs = {f["properties"]["cop_program"] for f in items} | set(states or {})
    block = {p: {"name": p.title(), "state": (states or {}).get(p, "fresh")} for p in programs}
    return {"type": "FeatureCollection", "program": "cop", "generated_at": at, "cop_programs": block,
            "features": list(items)}


A, B, C = "2026-11-14T00:21:00Z", "2026-11-14T02:21:00Z", "2026-11-14T04:21:00Z"


def sourced(program, iid, source):
    return item(program, iid, sources=[{"id": source, "accessed_at": "2026-11-14T00:00:00Z"}])


def with_lanes(s, program, lanes, read):
    s["cop_programs"][program].update({"lanes": sorted(lanes), "lanes_read": sorted(read)})
    return s


class SourceLanes(unittest.TestCase):
    """A program whose feed says which sources were read ends an item only when that item's source was read."""

    def test_an_item_from_a_failed_source_stays_open_while_the_rest_of_its_program_ends(self):
        lanes = {"ooni-api", "ioda-gatech"}
        s1, lines = ledger.step({}, with_lanes(snap(A, sourced("blackout", "blackout:ooni:ir", "ooni-api"),
                                                    sourced("blackout", "blackout:outage:ly", "ioda-gatech")),
                                               "blackout", lanes, lanes))
        self.assertEqual(sorted(x["lane"] for x in lines), ["blackout/ioda-gatech", "blackout/ooni-api"])
        self.assertEqual({x["program"] for x in lines}, {"blackout"})
        # OONI failed this run, so Blackout dropped its item; IODA was read and its outage is over.
        s2, lines = ledger.step(s1, with_lanes(snap(B, states={"blackout": "fresh"}), "blackout", lanes, {"ioda-gatech"}))
        self.assertEqual([(x["change"], x["id"]) for x in lines], [("ended", "blackout:outage:ly")])
        self.assertIn("blackout:ooni:ir", s2["open"])
        _, lines = ledger.step(s2, with_lanes(snap(C, states={"blackout": "fresh"}), "blackout", lanes, lanes))
        self.assertEqual([x["id"] for x in lines], ["blackout:ooni:ir"])

    def test_the_lane_map_names_cordons_embassy_sources(self):
        s = with_lanes(snap(A, sourced("cordon", "cordon:alert:x", "usembassy-ly")), "cordon",
                       {"us-embassy-alerts"}, {"us-embassy-alerts"})
        _, lines = ledger.step({}, s)
        self.assertEqual(lines[0]["lane"], "cordon/us-embassy-alerts")

    def test_a_source_the_block_does_not_name_falls_back_to_the_program(self):
        s = with_lanes(snap(A, sourced("cordon", "cordon:new:1", "brand-new-source")), "cordon", {"easa-czib"}, set())
        _, lines = ledger.step({}, s)
        self.assertEqual(lines[0]["lane"], "cordon")

    def test_items_logged_under_their_program_move_to_their_source_lane(self):
        s1, _ = ledger.step({}, snap(A, sourced("blackout", "blackout:ooni:ir", "ooni-api")))
        self.assertEqual(s1["open"]["blackout:ooni:ir"]["lane"], "blackout")
        s2, lines = ledger.step(s1, with_lanes(snap(B, states={"blackout": "fresh"}), "blackout",
                                               {"ooni-api"}, set()))
        self.assertEqual(lines, [], "OONI was not read, so its old item does not end")
        self.assertEqual(s2["open"]["blackout:ooni:ir"]["lane"], "blackout/ooni-api")

    def test_a_program_not_read_ends_nothing_whatever_its_sources_say(self):
        lanes = {"ooni-api"}
        s1, _ = ledger.step({}, with_lanes(snap(A, sourced("blackout", "blackout:ooni:ir", "ooni-api")),
                                           "blackout", lanes, lanes))
        _, lines = ledger.step(s1, with_lanes(snap(B, states={"blackout": "stale"}), "blackout", lanes, lanes))
        self.assertEqual(lines, [])


class SnapshotLanes(unittest.TestCase):
    def test_the_snapshot_keeps_which_sources_a_feed_says_were_read(self):
        import snapshot
        t = "2026-11-14T21:00:00Z"
        got = snapshot.source_lanes({"health": {
            "a": {"state": "fresh", "last_ok": t, "checked_at": t},
            "b": {"state": "failed", "last_ok": "2026-11-14T17:00:00Z", "checked_at": t},
            "c": {"state": "stale", "last_ok": t, "checked_at": t}}})
        self.assertEqual(got, {"lanes": ["a", "b", "c"], "lanes_read": ["a"]})
        self.assertIsNone(snapshot.source_lanes({"features": []}))


class Step(unittest.TestCase):
    def test_a_program_is_the_lane_and_the_program_of_each_line(self):
        _, lines = ledger.step({}, snap(A, item("elements", "elements:usgs:1"), item("cordon", "cordon:alert:x")))
        self.assertEqual([(x["change"], x["program"], x["lane"]) for x in lines],
                         [("new", "elements", "elements"), ("new", "cordon", "cordon")])
        self.assertEqual(lines[0]["format"], "tic-history/1")
        self.assertEqual(lines[0]["item"]["properties"]["id"], "elements:usgs:1")

    def test_an_item_that_left_a_program_that_was_read_ends(self):
        s1, _ = ledger.step({}, snap(A, item("elements", "elements:usgs:1"), item("elements", "elements:usgs:2")))
        s2, lines = ledger.step(s1, snap(B, item("elements", "elements:usgs:2")))
        self.assertEqual([(x["change"], x["id"], x["ended_at"]) for x in lines], [("ended", "elements:usgs:1", B)])
        self.assertEqual(s2["programs_read"], ["elements"])

    def test_a_stale_program_ends_nothing_and_its_items_stay_open(self):
        s1, _ = ledger.step({}, snap(A, item("vigil", "vigil:kev:1"), item("elements", "elements:usgs:1")))
        s2, lines = ledger.step(s1, snap(B, states={"vigil": "stale", "elements": "fresh"}))
        self.assertEqual([x["id"] for x in lines], ["elements:usgs:1"])
        self.assertIn("vigil:kev:1", s2["open"])
        # Read again without the item: now it ends.
        _, later = ledger.step(s2, snap(C, states={"vigil": "late", "elements": "fresh"}))
        self.assertEqual([(x["change"], x["id"]) for x in later], [("ended", "vigil:kev:1")])

    def test_the_last_good_copies_of_a_program_that_was_not_read_are_not_sightings(self):
        # What the snapshot really does when a feed fails: it keeps the program's last good items, marked cop_stale.
        s1, _ = ledger.step({}, snap(A, item("vigil", "vigil:kev:1"), item("elements", "elements:usgs:1")))
        stale_copy = item("vigil", "vigil:kev:1", cop_stale=True)
        s2, lines = ledger.step(s1, snap(B, stale_copy, item("elements", "elements:usgs:1"), states={"vigil": "stale"}))
        self.assertEqual(lines, [])
        self.assertEqual(s2["open"]["vigil:kev:1"]["last_seen"], A, "a stale copy is not a sighting")
        self.assertFalse(s2["open"]["vigil:kev:1"]["item"]["properties"]["cop_stale"], "the item as last read is kept")
        self.assertEqual(s2["open"]["elements:usgs:1"]["last_seen"], B, "a program that was read is seen as before")
        # Read again without the item: the end is bracketed by the last real read, not by the last stale copy.
        _, later = ledger.step(s2, snap(C, item("elements", "elements:usgs:1"), states={"vigil": "late"}))
        self.assertEqual([(x["change"], x["id"], x["last_seen"], x["ended_at"]) for x in later],
                         [("ended", "vigil:kev:1", A, C)])

    def test_an_end_keeps_the_items_own_valid_until_across_a_failed_feed(self):
        # The kit dates an end by the item's own valid_until when that falls after its last sighting. A stale copy that
        # moved last_seen past valid_until lost that: the end was dated by the build that noticed it instead.
        until = "2026-11-14T01:00:00Z"                                     # between A and B
        s1, _ = ledger.step({}, snap(A, item("elements", "elements:nws:1", valid_until=until)))
        s2, _ = ledger.step(s1, snap(B, item("elements", "elements:nws:1", valid_until=until, cop_stale=True),
                                     states={"elements": "stale"}))
        _, later = ledger.step(s2, snap(C, states={"elements": "fresh"}))
        self.assertEqual([(x["change"], x["last_seen"], x["ended_at"]) for x in later], [("ended", A, until)])

    def test_a_stale_copy_is_not_logged_as_new(self):
        s1, lines = ledger.step({}, snap(A, item("vigil", "vigil:kev:1", cop_stale=True), states={"vigil": "stale"}))
        self.assertEqual((lines, s1["open"]), ([], {}), "nothing was read, so nothing is logged")
        _, lines = ledger.step(s1, snap(B, item("vigil", "vigil:kev:1")))
        self.assertEqual([(x["change"], x["first_seen"]) for x in lines], [("new", B)])

    def test_a_program_missing_from_the_snapshot_ends_nothing(self):
        s1, _ = ledger.step({}, snap(A, item("orbit", "orbit:launch:1")))
        s2, lines = ledger.step(s1, snap(B, item("elements", "elements:usgs:1")))
        self.assertEqual([x["change"] for x in lines], ["new"])
        self.assertIn("orbit:launch:1", s2["open"])

    def test_cops_own_marks_are_no_change(self):
        s1, _ = ledger.step({}, snap(A, item("elements", "elements:usgs:1", tier="anomaly")))
        marked = item("elements", "elements:usgs:1", tier="anomaly", cop_pick=3, cop_strength=6.1,
                      cop_strength_text="magnitude 6.1", cop_stale=True, published_at=B)
        s2, lines = ledger.step(s1, snap(B, marked))
        self.assertEqual(lines, [])
        self.assertEqual(s2["open"]["elements:usgs:1"]["updates"], 0)

    def test_a_tier_move_is_a_changed_line(self):
        s1, _ = ledger.step({}, snap(A, item("blackout", "blackout:outage:tn:x")))
        _, lines = ledger.step(s1, snap(B, item("blackout", "blackout:outage:tn:x", tier="warning")))
        self.assertEqual([(x["change"], x["was"]["tier"], x["peak_tier"]) for x in lines],
                         [("changed", "routine", "warning")])


class Dropped(unittest.TestCase):
    """An item the snapshot left out as malformed (its `dropped` list) has not left its program's feed."""

    def dropped(self, s, *rows):
        s["dropped"] = [{"id": iid, "program": program, "index": 0, "reason": "test"} for program, iid in rows]
        return s

    def test_an_item_dropped_as_malformed_stays_open(self):
        s1, _ = ledger.step({}, snap(A, item("elements", "elements:usgs:1"), item("elements", "elements:usgs:2")))
        s2, lines = ledger.step(s1, self.dropped(snap(B, states={"elements": "fresh"}), ("elements", "elements:usgs:1")))
        self.assertEqual([(x["change"], x["id"]) for x in lines], [("ended", "elements:usgs:2")])
        self.assertEqual(s2["open"]["elements:usgs:1"]["last_seen"], A)
        _, later = ledger.step(s2, snap(C, states={"elements": "fresh"}))       # gone from the feed for real
        self.assertEqual([(x["change"], x["id"]) for x in later], [("ended", "elements:usgs:1")])

    def test_a_malformed_item_with_no_id_ends_nothing_of_its_program(self):
        lanes = {"ooni-api"}
        s1, _ = ledger.step({}, with_lanes(snap(A, sourced("blackout", "blackout:ooni:ir", "ooni-api"),
                                                item("blackout", "blackout:outage:ly"), item("elements", "elements:usgs:1")),
                                           "blackout", lanes, lanes))
        s = with_lanes(snap(B, item("blackout", "blackout:outage:tn"), states={"elements": "fresh"}), "blackout", lanes, lanes)
        s2, lines = ledger.step(s1, self.dropped(s, ("blackout", None)))
        # Blackout's missing items may be the one that could not be read, on its program lane or a source lane.
        self.assertEqual(sorted((x["change"], x["id"]) for x in lines),
                         [("ended", "elements:usgs:1"), ("new", "blackout:outage:tn")])
        self.assertTrue({"blackout:ooni:ir", "blackout:outage:ly"} <= set(s2["open"]))
        self.assertEqual(s2["programs_read"], ["blackout", "elements"])
        self.assertEqual(s2["lanes_read"], ["elements"])


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "ledger"

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_a_state_the_run_stops_unless_it_is_the_first(self):
        with self.assertRaises(SystemExit) as stop:
            ledger.run(snap(A, item("elements", "elements:usgs:1")), self.folder)
        self.assertIn("--first", str(stop.exception))
        self.assertFalse((self.folder / "state.json.gz").exists())
        ledger.run(snap(A, item("elements", "elements:usgs:1")), self.folder, first=True)
        self.assertEqual(sorted(ledger.load_state(self.folder)["open"]), ["elements:usgs:1"])

    def test_a_days_lines_build_up_in_one_file_across_builds(self):
        ledger.run(snap(A, item("elements", "elements:usgs:1")), self.folder, first=True)
        ledger.run(snap(B, item("elements", "elements:usgs:2")), self.folder)
        ledger.run(snap("2026-11-15T00:21:00Z", item("elements", "elements:usgs:2")), self.folder)
        self.assertEqual([(x["change"], x["id"]) for x in ledger.read_day(self.folder, "2026-11-14")],
                         [("new", "elements:usgs:1"), ("new", "elements:usgs:2"), ("ended", "elements:usgs:1")])
        self.assertEqual(ledger.read_day(self.folder, "2026-11-15"), [])
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["2026-11-14.jsonl.gz", "state.json.gz"])

    def test_the_same_snapshot_twice_writes_nothing_the_second_time(self):
        ledger.run(snap(A, item("elements", "elements:usgs:1")), self.folder, first=True)
        before = (self.folder / "2026-11-14.jsonl.gz").read_bytes()
        with self.assertRaises(SystemExit):
            ledger.run(snap(A, item("elements", "elements:usgs:1")), self.folder)
        self.assertEqual((self.folder / "2026-11-14.jsonl.gz").read_bytes(), before)

    def test_the_files_carry_no_timestamp_so_the_same_lines_give_the_same_bytes(self):
        ledger.run(snap(A, item("elements", "elements:usgs:1")), self.folder, first=True)
        self.assertEqual((self.folder / "state.json.gz").read_bytes()[4:8], b"\x00\x00\x00\x00")

    def test_a_replay_of_archived_days_marks_every_line(self):
        archive = Path(self.tmp.name) / "archive"
        archive.mkdir()
        for day, items in (("2026-11-12", [item("elements", "elements:usgs:1")]),
                           ("2026-11-13", [item("elements", "elements:usgs:2")])):
            with gzip.open(archive / f"{day}.geojson.gz", "wt", encoding="utf-8") as fh:
                json.dump(snap(f"{day}T22:21:00Z", *items), fh)
        with gzip.open(archive / "2026-11-13.elements-ended.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"features": []}, fh)                        # an ended list beside the snapshots is not a day
        with redirect_stdout(io.StringIO()):
            ledger.replay(archive, self.folder)
        lines = ledger.read_day(self.folder, "2026-11-12") + ledger.read_day(self.folder, "2026-11-13")
        self.assertEqual([(x["change"], x["id"]) for x in lines],
                         [("new", "elements:usgs:1"), ("new", "elements:usgs:2"), ("ended", "elements:usgs:1")])
        self.assertTrue(all(x["backfill"] == "daily archive" for x in lines))
        with self.assertRaises(SystemExit):                        # a replay never runs over a ledger that exists
            ledger.replay(archive, self.folder)

    def test_the_day_command_prints_the_snapshots_utc_day(self):
        p = Path(self.tmp.name) / "today.geojson"
        p.write_text(json.dumps(snap("2026-11-14T23:59:58Z")), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ledger.main(["--day", str(p)]), 0)
        self.assertEqual(out.getvalue().strip(), "2026-11-14")


class NotPublished(unittest.TestCase):
    def test_the_ledger_refuses_every_deployed_folder(self):
        for folder in (ROOT / "site" / "data" / "ledger", ROOT / "functions", ROOT / "mcp" / "ledger", ROOT / "site"):
            with self.assertRaises(SystemExit, msg=str(folder)):
                ledger._folder(folder)

    def test_its_default_folder_is_under_out_and_ignored_by_git(self):
        self.assertEqual(ledger._folder(ROOT / "out" / "ledger"), (ROOT / "out" / "ledger").resolve())
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertTrue(any(line.strip().rstrip("/") == "out" for line in ignore.splitlines()), ".gitignore keeps out/")

    def test_the_page_and_the_mcp_server_do_not_read_it(self):
        # "ledger" alone is Verdict's forecast ledger, a public thing; these name the suite ledger's files.
        marks = ("out/ledger", "ledger.py", "ledger-state", "state.json.gz", ".jsonl.gz")
        for folder in ("site", "functions", "mcp"):
            for p in (ROOT / folder).rglob("*"):
                if p.is_file() and p.suffix in (".js", ".html", ".mjs") and "node_modules" not in p.parts:
                    text = p.read_text(encoding="utf-8", errors="replace").lower()
                    for mark in marks:
                        self.assertNotIn(mark, text, str(p))

    def test_the_deployed_folders_are_still_clean_for_the_gate(self):
        self.assertEqual(gate.check(), [])


if __name__ == "__main__":
    unittest.main()
