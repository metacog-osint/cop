"""History across programs: keys, churn, comparability marks, places, closed days only, and sources left untouched."""
import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import history as h  # noqa: E402


def feat(id_, program, countries, tier="routine", subject=None, mv=None, measures=None, lonlat=(51.4, 35.7)):
    p = {"id": id_, "program": program, "cop_program": program, "title": f"{id_} title", "tier": tier,
         "kind": "observation", "countries": countries, "method_version": mv or f"{program}-1.0 (details)",
         "data_as_of": "2026-11-20T00:00:00Z", "cop_strength": 2.5, "cop_strength_text": "2.5 times usual"}
    if subject:
        p["subject_id"] = subject
    if measures is not None:
        p["measures"] = measures
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": list(lonlat)}, "properties": p}


def snap(day, features, read=("cordon", "context", "watchfloor")):
    return {"generated_at": f"{day}T23:00:00Z", "features": features,
            "cop_programs": {k: {"state": "fresh"} for k in read}}


def line(at, change, id_, program, subject=None, backfill=None, countries=("IR",)):
    ln = {"format": "tic-history/1", "at": at, "change": change, "id": id_, "program": program, "lane": program,
          "item": {"type": "Feature", "geometry": None,
                   "properties": {"id": id_, "program": program, "tier": "routine", "title": id_,
                                  "countries": list(countries), "method_version": f"{program}-1.0"}}}
    if subject:
        ln["subject_id"] = subject
    if backfill:
        ln["backfill"] = backfill
    return ln


def write_gz(path, obj=None, lines=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        if lines is not None:
            fh.write("".join(json.dumps(x) + "\n" for x in lines))
        else:
            json.dump(obj, fh)


def digest(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.archive, self.ledger, self.logs = t / "snap", t / "ledger", t / "logs"
        self.out = t / "out"
        for day in ("2026-11-16", "2026-11-17", "2026-11-18"):
            feats = [feat("cordon:alert:ir-1", "cordon", ["IR"], subject="cordon:alert:ir"),
                     feat(f"context:{day}_0000Z:Z11", "context", [], measures={} if day == "2026-11-16" else
                          {"volume_12h": 5}, subject="context:zone:11_Iran_Red_Sea"),
                     feat(f"watchfloor:{day}:11_Iran_Red_Sea", "watchfloor", ["IR", "IQ"],
                          measures={"zone_scope": "a" if day < "2026-11-18" else "b"})]
            write_gz(self.archive / f"{day}.geojson.gz", snap(day, feats))
        write_gz(self.archive / "2026-11-18T06Z.snap.json.gz", snap("2026-11-18", []))    # a slot copy: not read
        write_gz(self.ledger / "2026-11-17.jsonl.gz", lines=[
            line("2026-11-17T02:20:00Z", "ended", "vigil:claim:2026-11-16:x", "vigil", backfill="daily archive"),
            line("2026-11-17T02:20:00Z", "new", "vigil:claim:2026-11-17:x", "vigil", backfill="daily archive"),
            line("2026-11-17T04:20:00Z", "new", "cordon:alert:ir-1", "cordon", subject="cordon:alert:ir")])
        write_gz(self.logs / "elements" / "log" / "2026" / "11" / "17.jsonl.gz",
                 lines=[line("2026-11-17T03:00:00Z", "new", "elements:usgs:1", "elements")])
        self.before = {**digest(self.archive), **digest(self.ledger), **digest(self.logs)}
        self.doc = h.build(self.archive, self.out, self.ledger, self.logs, now="2026-11-19T00:30:00Z")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sources_are_untouched(self):
        self.assertEqual({**digest(self.archive), **digest(self.ledger), **digest(self.logs)}, self.before)

    def test_keys_and_counts(self):
        self.assertEqual(self.doc["days"], 3)
        self.assertEqual(self.doc["keys"], ["date", "country", "zones", "subject_id"])
        # cordon 1 row a day, context 1 (no country), watchfloor 2 (IR, IQ): 4 a day
        self.assertEqual(self.doc["rows"]["readings"], 12)
        self.assertEqual(self.doc["programs"]["cordon"]["days_read"], 3)

    def test_subject_basis_derived_when_missing(self):
        self.assertEqual(h.subject_of({}, "vigil:claim:2026-11-16:x"), ("vigil:claim:x", "derived"))
        self.assertEqual(h.subject_of({"subject_id": "s"}, "x"), ("s", "feed"))
        self.assertEqual(self.doc["programs"]["watchfloor"]["subject_id_from_feed"], 0.0)

    def test_churn_is_marked_not_dropped(self):
        self.assertEqual(self.doc["rows"]["churn_events"], 2)
        self.assertEqual(self.doc["programs"]["vigil"]["churn_events"], 2)
        self.assertEqual(self.doc["programs"]["cordon"]["churn_events"], 0)

    def test_breaks_and_scope_marked(self):
        notes = self.doc["programs"]
        self.assertTrue(any("Iran zone" in n for n in notes["watchfloor"]["notes"]))
        self.assertTrue(any("window fix" in n for n in notes["context"]["notes"]))
        self.assertTrue(any("one snapshot a day" in n for n in notes["vigil"]["notes"]))
        self.assertEqual(notes["cordon"]["notes"], [])

    def test_method_versions_listed(self):
        mv = self.doc["programs"]["cordon"]["method_versions"]
        self.assertEqual(mv, [{"version": "cordon-1.0", "from": "2026-11-16", "to": "2026-11-18"}])

    def test_place_files_and_zones(self):
        ir = json.loads((self.out / "place" / "IR" / "2026-11-16.json").read_text(encoding="utf-8"))
        runs = [dict(zip(ir["runs"]["cols"], r)) for r in ir["runs"]["rows"]]
        cordon = [r for r in runs if r["program"] == "cordon"]
        self.assertEqual(len(cordon), 1)                       # three days, one unchanged reading: one run
        self.assertEqual((cordon[0]["from"], cordon[0]["to"], cordon[0]["days"]), ("2026-11-16", "2026-11-18", 3))
        evs = [dict(zip(ir["events"]["cols"], r)) for r in ir["events"]["rows"]]
        self.assertEqual({e["source"] for e in evs}, {"cop-ledger", "program-log:elements"})
        # the context item names no country, but its subject names the Iran zone's press zone
        iran = json.loads((self.out / "place" / "iran-red-sea" / "2026-11-16.json").read_text(encoding="utf-8"))
        self.assertIn("context", {r[0] for r in iran["runs"]["rows"]})
        gulf = json.loads((self.out / "areas" / "gulf.json").read_text(encoding="utf-8"))
        self.assertEqual(gulf["days"], ["2026-11-16", "2026-11-17", "2026-11-18"])

    def test_before_keeps_closed_days_only(self):
        doc = h.build(self.archive, Path(self.tmp.name) / "o2", self.ledger, self.logs, before="2026-11-18")
        self.assertEqual(doc["last_day"], "2026-11-17")

    def test_published_through_the_gate(self):
        self.assertTrue(self.doc["gate"]["may_publish"])
        self.assertEqual(gate.check([self.out]), [])
        self.assertEqual(set(self.doc["join"]["inputs"]), gate.programs() - gate.READ_NOT_DRAWN)

    def test_only_feed_logs_are_read(self):
        self.assertTrue(all(k.startswith("metacog-osint/") for k in h.json.loads(h.CONFIG.read_text())
                            ["program_logs"].values()))
        (self.logs / "elements" / "meteoalarm").mkdir(parents=True)
        files = [str(f) for _, f in h.log_files(self.logs / "elements" / "log")]
        self.assertTrue(all("meteoalarm" not in f for f in files))


if __name__ == "__main__":
    unittest.main()
