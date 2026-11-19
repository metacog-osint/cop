"""Retention fixes C1-C3 (18 Nov 2026): python tests/test_dropped.py

C1  one snapshot per 6-hour slot is kept besides the day's last, under a name the daily readers never match.
C2  what sift() leaves out is written with its reason and its body (tools/dropped.py), and the published files do not change.
C3  the ledger step fails loudly on an upload failure, rolls per year from 2027, and runs after the deploy.
"""

import copy
import gzip
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import test_snapshot as ts  # noqa: E402
import dropped  # noqa: E402
import gate  # noqa: E402

snap = ts.snap
WF = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")


def run_main(tmp, fixtures, now="2026-10-31T20:30:00Z", extra=()):
    out = Path(tmp) / "out"
    with redirect_stdout(io.StringIO()):
        code = snap.main(["--fixtures", str(fixtures), "--out", str(out), "--ended-out", str(Path(tmp) / "ended"),
                          "--archive", str(Path(tmp) / "archive"), "--now", now, *extra])
    assert code == 0
    return out


def gz_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


class SixHourSlots(unittest.TestCase):
    def test_slot_names(self):
        at = lambda h, m=0: datetime(2026, 11, 18, h, m, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(snap.slot_name(at(0, 20)), "2026-11-18T00Z.snap.json.gz")
        self.assertEqual(snap.slot_name(at(5, 59)), "2026-11-18T00Z.snap.json.gz")
        self.assertEqual(snap.slot_name(at(6)), "2026-11-18T06Z.snap.json.gz")
        self.assertEqual(snap.slot_name(at(12, 20)), "2026-11-18T12Z.snap.json.gz")
        self.assertEqual(snap.slot_name(at(23, 59)), "2026-11-18T18Z.snap.json.gz")

    def test_the_daily_copy_is_unchanged_and_the_slot_copy_is_the_same_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_main(tmp, ts.FIX)
            arch = Path(tmp) / "archive"
            names = sorted(p.name for p in arch.glob("*.gz"))
            self.assertIn("2026-10-31.geojson.gz", names)
            self.assertIn("2026-10-31T18Z.snap.json.gz", names)       # 20:30 UTC is in the 18:00 slot
            # what the daily readers glob (*.geojson.gz, ????-??-??.geojson.gz, *.wake-ended.json.gz) sees only the day
            self.assertEqual([p.name for p in arch.glob("*.geojson.gz")], ["2026-10-31.geojson.gz"])
            self.assertEqual([p.name for p in arch.glob("????-??-??.geojson.gz")], ["2026-10-31.geojson.gz"])
            self.assertEqual(gz_json(arch / "2026-10-31.geojson.gz"), gz_json(arch / "2026-10-31T18Z.snap.json.gz"))


class DroppedItems(unittest.TestCase):
    def test_every_left_out_item_is_written_with_reason_and_body_and_the_picture_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = Path(tmp) / "fixtures"
            fixtures.mkdir()
            added = ts.spoiled_fixtures(fixtures)
            plain = run_main(Path(tmp) / "a", fixtures)
            (Path(tmp) / "a").mkdir(exist_ok=True)
            with_log = run_main(Path(tmp) / "b", fixtures, extra=("--dropped-out", str(Path(tmp) / "dropped.jsonl")))
            for name in ("today.geojson", "picks.json", "health.json", "countries-today.geojson"):   # live view: identical
                self.assertEqual((plain / name).read_bytes(), (with_log / name).read_bytes(), name)
            rows = [json.loads(s) for s in (Path(tmp) / "dropped.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), added)
            feed = json.loads((fixtures / "blackout.geojson").read_text(encoding="utf-8"))["features"]
            for r in rows:
                self.assertEqual((r["program"], r["source"], r["at"]), ("blackout", "feed", "2026-10-31T20:30:00Z"))
                self.assertTrue(r["reason"])
                self.assertEqual(len(r["sha256"]), 64)
                if "item" in r:
                    self.assertEqual(r["item"], feed[r["index"]])      # the item exactly as the program published it
                else:
                    self.assertIn("NaN", r["item_text"])               # JSON cannot hold NaN: kept as text
            self.assertEqual({r["reason"] for r in rows}, {r["reason"] for r in
                                                           json.loads((with_log / "today.geojson").read_text(encoding="utf-8"))["dropped"]})

    def test_a_clean_build_writes_no_rows_and_removes_a_stale_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "dropped.jsonl"
            stale.write_text("old\n", encoding="utf-8")
            run_main(tmp, ts.FIX, extra=("--dropped-out", str(stale)))
            self.assertFalse(stale.exists())

    def test_the_ended_lists_drops_are_kept_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = json.loads((ts.FIX / "elements.geojson").read_text(encoding="utf-8"))["features"][0]
            doc = {"type": "FeatureCollection", "generated_at": "2026-10-31T20:00:00Z",
                   "features": [good, "oops", copy.deepcopy(good) | {"geometry": "x"}]}
            fx = Path(tmp) / "fx"
            fx.mkdir()
            (fx / "elements-ended.geojson").write_text(json.dumps(doc), encoding="utf-8")
            left = []
            with redirect_stdout(io.StringIO()):
                snap.fetch_ended(datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc), Path(tmp) / "ended", fx, None, left)
            self.assertEqual([(d["index"], item) for d, item in left][0], (1, "oops"))
            rows = snap.dropped_rows({"dropped": []}, {}, datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc), left)
            self.assertEqual({r["source"] for r in rows}, {"ended"})
            self.assertEqual(rows[0]["item"], "oops")

    def test_the_day_file_is_append_only_and_a_repeated_item_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, rows = Path(tmp) / "ledger", Path(tmp) / "rows.jsonl"
            a = dropped.row_for({"program": "vigil", "id": "v:1", "index": 4, "reason": "geometry a string"},
                                {"id": "v:1", "geometry": "x"}, "2026-11-18T00:20:00Z")
            b = dropped.row_for({"program": "cordon", "id": None, "index": 0, "reason": "properties is not an object"},
                                {"properties": None}, "2026-11-18T00:20:00Z")
            rows.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")
            dest, n = dropped.merge(rows, folder, "2026-11-18")
            self.assertEqual((dest.name, n), ("2026-11-18.dropped.jsonl.gz", 2))
            first_lines = dropped.read_lines(dest)
            # two hours later: the same two items still malformed, one new one
            a2 = dict(a, at="2026-11-18T02:20:00Z")
            c = dropped.row_for({"program": "wake", "id": "w:9", "index": 2, "reason": "sources a string"},
                                {"id": "w:9", "sources": "x"}, "2026-11-18T02:20:00Z")
            rows.write_text(json.dumps(a2) + "\n" + json.dumps(c) + "\n", encoding="utf-8")
            dest, n = dropped.merge(rows, folder, "2026-11-18")
            self.assertEqual(n, 1)
            lines = dropped.read_lines(dest)
            self.assertEqual(lines[:2], first_lines)                  # earlier lines untouched, in order
            self.assertEqual([x["id"] for x in lines], ["v:1", None, "w:9"])
            self.assertEqual(dropped.merge(rows, folder, "2026-11-18")[1], 0)    # same rows again: nothing
            rows.unlink()                                             # a build with no rows file: nothing and no loss
            self.assertEqual(dropped.read_lines(dest), lines)

    def test_a_changed_body_is_a_new_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, rows = Path(tmp) / "ledger", Path(tmp) / "rows.jsonl"
            d = {"program": "vigil", "id": "v:1", "index": 4, "reason": "geometry a string"}
            rows.write_text(json.dumps(dropped.row_for(d, {"id": "v:1", "geometry": "x"}, "t1")) + "\n", encoding="utf-8")
            dropped.merge(rows, folder, "2026-11-18")
            rows.write_text(json.dumps(dropped.row_for(d, {"id": "v:1", "geometry": "y"}, "t2")) + "\n", encoding="utf-8")
            self.assertEqual(dropped.merge(rows, folder, "2026-11-18")[1], 1)

    def test_it_refuses_a_deployed_folder(self):
        with self.assertRaises(SystemExit):
            dropped.main(["--rows", "x", "--assets", str(ROOT / gate.DEPLOYED[0] / "d"), "--day", "2026-11-18"])


class WorkflowLedger(unittest.TestCase):
    def step(self, name):
        i = WF.index(f"- name: {name}")
        j = WF.find("\n      - name:", i + 1)
        return WF[i:j if j != -1 else len(WF)]

    def test_archive_steps_run_after_the_deploy_and_gate_on_it(self):
        i_deploy = WF.index("pages deploy site")
        for name in ("Keep the 6-hourly snapshots", "Suite ledger (kept as release assets, not published)",
                     "Dropped items (kept beside the ledger, not published)"):
            self.assertGreater(WF.index(f"- name: {name}"), i_deploy, name)
            self.assertIn("steps.deploy.outcome == 'success'", self.step(name), name)
        self.assertIn("id: deploy", self.step("Deploy site/ and functions/ (the MCP server at /mcp) to Cloudflare Pages"))

    def test_a_failed_ledger_upload_fails_the_step(self):
        s = self.step("Suite ledger (kept as release assets, not published)")
        self.assertNotIn("upload failed; the next build logs", s)
        self.assertNotIn("|| echo", s)
        self.assertIn('fail() { echo "::error::suite ledger: $1"; exit 1; }', s)
        self.assertIn('up ledger out/ledger/state.json.gz --clobber || fail', s)
        self.assertIn('up "$DAYTAG" "out/ledger/$DAY.jsonl.gz" --clobber || fail', s)
        self.assertLess(s.index('"out/ledger/$DAY.jsonl.gz" --clobber'), s.index("out/ledger/state.json.gz --clobber"))

    def test_the_release_rolls_per_year_and_the_state_stays_in_ledger(self):
        s = self.step("Suite ledger (kept as release assets, not published)")
        self.assertIn('if [ "${DAY%%-*}" -le 2026 ]; then DAYTAG=ledger; else DAYTAG="ledger-${DAY%%-*}"; fi', s)
        self.assertIn('gh release view "$DAYTAG" >/dev/null 2>&1 || gh release create "$DAYTAG"', s)
        self.assertIn("gh release download ledger --dir out/ledger --pattern state.json.gz", s)   # the state stays in ledger
        # nothing else in the repo reads the release (a reader of `ledger` keeps every 2026 day file and the state)
        for folder in ("functions", "mcp", "site"):
            for p in (ROOT / folder).rglob("*"):
                if p.is_file() and p.suffix in (".js", ".py", ".html", ".json") and "vendor" not in p.parts:
                    self.assertNotIn("releases/download/ledger", p.read_text(encoding="utf-8", errors="ignore"))

    def test_the_snapshot_step_writes_the_dropped_rows_and_this_file_runs_in_the_build(self):
        self.assertIn("python tools/snapshot.py --archive out/archive --dropped-out out/dropped.jsonl", WF)
        self.assertIn("python tests/test_dropped.py", WF)

    def test_slot_files_are_kept_without_clobber(self):
        s = self.step("Keep the 6-hourly snapshots (first build of each slot)")
        self.assertIn("*.snap.json.gz", s)
        self.assertNotIn("--clobber", s)
        self.assertIn("is already kept", s)


if __name__ == "__main__":
    unittest.main()
