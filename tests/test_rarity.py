"""Comparable rarity (tools/rarity.py, feed contract 2.1 F4): python tests/test_rarity.py"""
import gzip
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import memory as M  # noqa: E402
import rarity as R  # noqa: E402
import snapshot  # noqa: E402


def days_before(day, n):
    d = date.fromisoformat(day)
    return [(d - timedelta(days=i)).isoformat() for i in range(n, 0, -1)]


def record(values, day="2026-11-27"):
    return {"sv": dict(zip(days_before(day, len(values)), values))}


def item(value, tail="high", **sig):
    return {"id": "wake:chokepoint:x:2026-11-27", "subject_id": "wake:chokepoint:x", "cop_program": "wake",
            "data_as_of": "2026-11-27T00:00:00Z", "signal": {"name": "transits", "value": value, "tail": tail, **sig}}


class Rule(unittest.TestCase):
    def test_cops_own_history_from_sixty_days(self):
        hist = record(list(range(1, 100)))                      # 99 earlier days, values 1..99
        r = R.rarity(item(95), hist, "Wake")
        self.assertEqual(r["whose"], "cop")
        self.assertEqual(r["readings"], 99)
        self.assertAlmostEqual(r["exceedance"], round(6 / 100, 4))   # 95..99 at least as high, plus today: 6 of 100
        self.assertEqual(r["one_in"], 17)
        self.assertEqual(r["text"], "About 1 day in 17 has been as high or higher in COP's history of this subject "
                                    "(99 daily readings).")
        low = R.rarity(item(2, "low"), hist, "Wake")
        self.assertAlmostEqual(low["exceedance"], 0.03)            # 1 and 2, plus today: 3 of 100
        both = R.rarity(item(50, "both", expected=50), hist, "Wake")
        self.assertEqual(both["exceedance"], 1.0, "the usual value itself is never rare")
        far = R.rarity(item(140, "both", expected=50), hist, "Wake")
        self.assertAlmostEqual(far["exceedance"], 0.01, msg="further from usual than every earlier day: 1 in n + 1")
        mid = R.rarity(item(1, "both"), hist, "Wake")              # no expected value: the median, 50
        self.assertAlmostEqual(mid["exceedance"], round(3 / 100, 4))  # 1 and 99 are 49 away; with today, 3 of 100

    def test_todays_own_day_is_left_out(self):
        hist = record(list(range(1, 61)))
        hist["sv"]["2026-11-27"] = 1000
        r = R.rarity(item(60), hist, "Wake")
        self.assertEqual(r["readings"], 60)
        self.assertAlmostEqual(r["exceedance"], round(2 / 61, 4))

    def test_the_programs_figure_stands_in_below_sixty(self):
        r = R.rarity(item(23.7, exceedance=0.0109, baseline_n=91), record(list(range(59))), "Wake")
        self.assertEqual((r["whose"], r["exceedance"], r["one_in"], r["baseline_n"], r["readings"]),
                         ("program", 0.0109, 92, 91, 59))
        self.assertEqual(r["text"], "About 1 in 92 against Wake's own baseline of 91 readings (Wake's figure; "
                                    "COP holds 59 daily readings of this subject, fewer than 60).")
        none = R.rarity(item(23.7), record(list(range(59))), "Wake")
        self.assertIsNone(none["whose"])
        self.assertIsNone(none["exceedance"])
        self.assertEqual(none["text"], "No comparable rarity yet: COP holds 59 daily readings of this subject, fewer "
                                       "than 60, and Wake gives no figure of its own.")
        unread = R.rarity(item(23.7, exceedance=0.0109, baseline_n=91), None, "Wake", memory_read=False)
        self.assertEqual(unread["whose"], "program")
        self.assertIsNone(unread["readings"])
        self.assertIn("COP's history was not read on this build", unread["text"])

    def test_no_tail_no_figure_from_cop(self):
        level = item(2, tail=None)
        r = R.rarity(level, record(list(range(1, 100))), "Cordon")
        self.assertIsNone(r["whose"])
        self.assertIsNone(r["tail"])
        self.assertEqual(r["text"], "No comparable rarity yet: the feed names no direction (tail) for this signal, so "
                                    "COP computes no figure of its own, and Cordon gives no figure of its own.")
        own = R.rarity(item(2, tail=None, exceedance=0.05, baseline_n=19), record(list(range(1, 100))), "Cordon")
        self.assertEqual(own["whose"], "program", "the program's own figure still stands")

    def test_an_item_with_no_signal_gets_none(self):
        self.assertIsNone(R.rarity({"id": "x"}, None, "Vis"))
        self.assertIsNone(R.rarity({"id": "x", "signal": {"name": "n", "value": float("nan")}}, None, "Vis"))
        feats = [{"properties": {"id": "a", "cop_program": "vis"}}, {"properties": item(5)}]
        counts = R.add(feats, {"subjects": {}}, {"wake": "Wake"}, M.key)
        self.assertNotIn("cop_rarity", feats[0]["properties"])
        self.assertEqual(feats[1]["properties"]["cop_rarity"]["readings"], 0)
        self.assertEqual(counts, {"cop": 0, "program": 0, "none": 1})

    def test_never_added_across_layers(self):
        src = (ROOT / "tools" / "rarity.py").read_text(encoding="utf-8")
        self.assertNotIn("sum(r", src)
        for word in ("likely", "warning sign", "probability"):
            self.assertNotIn(word, R.rarity(item(95), record(list(range(1, 100))), "Wake")["text"].lower())


class Memory(unittest.TestCase):
    def test_memory_keeps_each_subjects_signal_apart_from_its_series(self):
        mem = M.empty()
        for i, day in enumerate(("2026-11-25", "2026-11-26")):
            snap = {"generated_at": f"{day}T12:00:00Z",
                    "cop_programs": {"wake": {"state": "fresh"}, "blackout": {"state": "fresh"}},
                    "features": [
                        {"type": "Feature", "geometry": None, "properties": {
                            "id": f"wake:chokepoint:x:{day}", "subject_id": "wake:chokepoint:x", "cop_program": "wake",
                            "data_as_of": f"{day}T00:00:00Z", "measures": {"n_total_7d": 70 + i},
                            "signal": {"name": "transits", "value": 10.5 + i, "tail": "low"}}},
                        {"type": "Feature", "geometry": None, "properties": {
                            "id": f"blackout:bgp-leak:as1:{day}", "subject_id": "blackout:bgp-leak:as1",
                            "cop_program": "blackout", "data_as_of": f"{day}T00:00:00Z",
                            "signal": {"name": "prefixes", "value": 100 + i, "tail": "high"}}}]}
            mem = M.fold(mem, snap)
        wake = mem["subjects"]["wake:chokepoint:x"]
        self.assertEqual(wake["sv"], {"2026-11-25": 10.5, "2026-11-26": 11.5})
        self.assertEqual(wake["v"], {"2026-11-25": 70, "2026-11-26": 71}, "the series the direction reads is unchanged")
        leak = mem["subjects"]["blackout:bgp-leak:as1"]
        self.assertEqual(leak["sv"], {"2026-11-25": 100, "2026-11-26": 101})
        self.assertNotIn("v", leak, "a signal does not make a subject a series")


class Backfill(unittest.TestCase):
    """tools/memory.py backfill_sv: earlier `sv` values from the archived daily snapshots, additive only."""

    @staticmethod
    def snap(day, value, data_day=None, state="fresh", sid=True, stale=False, gen="T22:00:00Z"):
        p = {"id": f"wake:chokepoint:x:{day}", "cop_program": "wake", "data_as_of": f"{data_day or day}T00:00:00Z",
             "measures": {"n_total_7d": 5}, "signal": {"name": "transits", "value": value, "tail": "low"}}
        if sid:
            p["subject_id"] = "wake:chokepoint:x"
        if stale:
            p["cop_stale"] = True
        return {"generated_at": f"{day}{gen}", "cop_programs": {"wake": {"state": state}},
                "features": [{"type": "Feature", "geometry": None, "properties": p}]}

    def archive(self, folder, snaps):
        for name, snap in snaps.items():
            with gzip.open(Path(folder) / f"{name}.geojson.gz", "wt", encoding="utf-8") as fh:
                json.dump(snap, fh)

    def test_adds_earlier_values_and_never_replaces_one(self):
        mem = M.empty()
        mem["subjects"]["wake:chokepoint:x"] = {"p": "wake", "first": "2026-11-18", "spans": [["2026-11-18", "2026-11-27"]],
                                               "at": [], "v": {"2026-11-20": 5}, "sv": {"2026-11-20": 99}}
        before = json.loads(json.dumps(mem))
        with tempfile.TemporaryDirectory() as tmp:
            self.archive(tmp, {
                "2026-11-19": self.snap("2026-11-19", 1),
                "2026-11-20": self.snap("2026-11-20", 2),                 # the memory holds this day: kept
                "2026-11-21": self.snap("2026-11-21", 3, data_day="2026-11-19"),   # a later build of 19 Nov wins
                "2026-11-22": self.snap("2026-11-22", 4, state="dead"),   # the program was not read
                "2026-11-23": self.snap("2026-11-23", 5, stale=True),     # a carried stale copy
                "2026-11-24": self.snap("2026-11-24", float("nan")),
            })
            counts = M.backfill_sv(mem, tmp)
        r = mem["subjects"]["wake:chokepoint:x"]
        self.assertEqual(r["sv"], {"2026-11-19": 3, "2026-11-20": 99})
        self.assertEqual(counts["wake"], {"subjects": 1, "values": 1, "left_out": 0, "enough": 0})
        for field in ("v", "spans", "first", "at"):
            self.assertEqual(r[field], before["subjects"]["wake:chokepoint:x"][field])
        self.assertEqual((mem["days"], mem["updated_at"]), (before["days"], before["updated_at"]))

    def test_an_older_key_goes_to_the_record_that_continues_it_and_an_unknown_subject_is_left_out(self):
        mem = M.empty()
        mem["subjects"]["wake:chokepoint:x"] = {"p": "wake", "first": "2026-11-18", "spans": [], "at": [],
                                               "id": "wake:chokepoint:x:2026-11-18"}          # keyed from its id
        mem["subjects"]["wake:zone:x"] = {"p": "wake", "first": "2026-11-24", "spans": [], "at": [],
                                         "id": "wake:chokepoint:x:2026-11-24"}               # continues it
        self.assertEqual(M.continues(mem, "wake:zone:x"), "wake:chokepoint:x")
        other = self.snap("2026-11-20", 7)
        other["features"][0]["properties"].update(id="wake:chokepoint:y:2026-11-20", subject_id="wake:chokepoint:y")
        with tempfile.TemporaryDirectory() as tmp:
            self.archive(tmp, {"2026-11-19": self.snap("2026-11-19", 1, sid=False), "2026-11-20": other})
            counts = M.backfill_sv(mem, tmp)
        self.assertEqual(mem["subjects"]["wake:zone:x"]["sv"], {"2026-11-19": 1})
        self.assertNotIn("sv", mem["subjects"]["wake:chokepoint:x"])
        self.assertNotIn("wake:chokepoint:y", mem["subjects"], "no record is made")
        self.assertEqual(counts["wake"]["left_out"], 1)

    def test_dry_run_writes_nothing(self):
        mem = M.empty()
        mem["subjects"]["wake:chokepoint:x"] = {"p": "wake", "first": "2026-11-18", "spans": [], "at": []}
        with tempfile.TemporaryDirectory() as tmp:
            arch, assets = Path(tmp) / "a", Path(tmp) / "m"
            arch.mkdir()
            self.archive(arch, {"2026-11-19": self.snap("2026-11-19", 1)})
            path = M.save(assets, mem)
            raw = path.read_bytes()
            self.assertEqual(M.main(["--backfill-sv", str(arch), "--assets", str(assets), "--dry-run"]), 0)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(M.main(["--backfill-sv", str(arch), "--assets", str(assets)]), 0)
            self.assertEqual(M.load(assets)["subjects"]["wake:chokepoint:x"]["sv"], {"2026-11-19": 1})


class Snapshot(unittest.TestCase):
    def test_the_snapshot_reads_the_last_memory_and_survives_without_it(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "memory.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                json.dump({"format": M.FORMAT, "subjects": {"s": record([1])}}, fh)
            self.assertEqual(snapshot.read_memory(str(path))["subjects"]["s"]["sv"], {"2026-11-26": 1})
            self.assertIsNone(snapshot.read_memory(str(Path(t) / "missing.json.gz")))
            self.assertIsNone(snapshot.read_memory(None))

    def test_the_build_gives_the_snapshot_the_last_memory(self):
        wf = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("--memory out/memory-last/memory.json.gz", wf)
        self.assertLess(wf.index("gh release download memory --dir out/memory-last"),
                        wf.index("python tools/snapshot.py --archive"))

    def test_the_card_and_the_scoring_page_show_it(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('if (p.cop_rarity && p.cop_rarity.text) fact("Rarity", p.cop_rarity.text);', js)
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        self.assertIn(f'<strong id="rarity-min">{R.MIN_READINGS}</strong>', html)


if __name__ == "__main__":
    unittest.main()
