"""The suite ledger (tools/ledger.py): python tests/test_ledger.py

The line format and its rules are the doctrine kit's (tic-history/1; tested in the kit and, on recorded data, in
Elements). These tests cover what is COP's own: a program is the lane, a program that was not read ends nothing, COP's
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
