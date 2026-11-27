"""The triage log (tools/triage_log.py): python tests/test_triage_log.py

The line format is the doctrine kit's (tic-history/1, tested in the kit and in tests/test_ledger.py). These tests
cover what is the triage log's own: one item per outage and place, a reading's history that grows only when the
reading or a layer's finding changes, ends only on a build that read Blackout, the gate's check on what it cites, the
voice rule (no probability, no cause, tier routine), the files, and that the log is never served from the site.
"""

import copy
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import triage as tri  # noqa: E402
import triage_log as tl  # noqa: E402

A, B, C = "2026-11-17T02:20:00Z", "2026-11-17T04:20:00Z", "2026-11-17T06:20:00Z"
OUTAGE = "blackout:radar-anomaly:c595977b"


def layer(found=False, counted=(), listed=(), key=None, listed_key="standing", state="fresh"):
    return {"name": "x", "showed": "Nothing found." if not found else "Found.", "state": state, "found": found,
            key: [{"id": i} for i in counted], listed_key: [{"id": i} for i in listed]}


def record(reading="open", cc="MY", outage=OUTAGE, elements=True, cordon=False, watchfloor=False, extra_id=None):
    rec = {
        "join": "outage-triage", "country": cc, "outage_kind": "internet", "us_state": None, "us_state_name": None,
        "outage": {"id": outage, "title": "Malaysia, AS10030", "source": "Cloudflare Radar (verified anomaly)",
                   "indicator_id": "BO-IND-10", "confirmed": True, "started_at": "2026-11-17T00:15:00Z",
                   "ended_at": None, "network": {"asn": 10030, "name": "CELCOMNET-AP"}, "stated_cause": None},
        "layers": {
            "elements": layer(elements, ["elements:gdacs:FL-1"] if elements else [], ["elements:firms:my"],
                              key="minor"),
            "cordon": layer(cordon, ["cordon:alert:MY:1"] if cordon else [], ["cordon:advisory:MY"], key="recent"),
            "watchfloor": layer(watchfloor, ["watchfloor:2026-11-16:country:MY:violence"] if watchfloor else [],
                                key="unusual_items", listed_key="elevated"),
        },
        "no_current_data": [], "reading": reading, "reading_text": tri.READINGS.get(reading, ""),
        "base_rate": {"share_of_country_days": 0.93, "share_of_this_countrys_days": 1.0, "counted_days": 10,
                      "text": "..."},
        "replay_rate": {"pool": "two_layer", "shutdown_share": 0.62, "ordinary_day_share": 0.84, "text": "..."},
        "question_draft": {"status": "draft, not registered", "verdict_id": "verdict:2026-11-21:cop-my-ooni"},
        "cause_question_draft": {"status": "draft, not registered", "verdict_id": "verdict:2026-11-24:cop-my-cause"},
        "registerable": True,
    }
    if extra_id:
        rec["layers"]["cordon"]["standing"].append({"id": extra_id})
    return rec


def doc(at, *records, blackout="fresh"):
    return {"join": copy.deepcopy(tri.JOIN), "snapshot_generated_at": at,
            "layer_states": {"elements": "fresh", "cordon": "fresh", "watchfloor": "fresh", "blackout": blackout},
            "records": list(records)}


def props(state, iid=None):
    iid = iid or f"cop:triage:{OUTAGE}:MY"
    return state["open"][iid]["item"]["properties"]


class Records(unittest.TestCase):

    def test_one_item_per_outage_and_place_in_the_kit_format(self):
        state, lines = tl.step({}, doc(A, record(), record(cc="SG")))
        self.assertEqual(sorted(state["open"]), [f"cop:triage:{OUTAGE}:MY", f"cop:triage:{OUTAGE}:SG"])
        self.assertEqual([x["change"] for x in lines], ["new", "new"])
        self.assertTrue(all(x["format"] == "tic-history/1" and x["lane"] == "cop/triage" for x in lines))
        p = props(state)
        self.assertEqual((p["program"], p["kind"], p["tier"], p["status_class"]),
                         ("cop", "observation", "routine", "reported"))
        self.assertEqual(p["title"], "Malaysia: outage triage reading open")
        self.assertEqual(p["triage"]["questions"], {"ooni": "verdict:2026-11-21:cop-my-ooni",
                                                    "cause": "verdict:2026-11-24:cop-my-cause", "registered": []})
        self.assertEqual(p["triage"]["labels"], [])
        self.assertEqual((p["last_change"], p["last_change_at"]), ("new", A))

    def test_what_each_layer_counted_is_kept_apart_from_what_it_listed(self):
        state, _ = tl.step({}, doc(A, record()))
        L = props(state)["triage"]["layers"]
        self.assertEqual(L["elements"]["counted"], ["elements:gdacs:FL-1"])
        self.assertEqual(L["elements"]["listed"], ["elements:firms:my"])
        self.assertEqual(L["cordon"]["counted"], [])
        self.assertEqual(L["cordon"]["listed"], ["cordon:advisory:MY"])
        ids = [s["id"] for s in props(state)["sources"]]
        self.assertEqual(ids[0], OUTAGE)
        self.assertIn("elements:gdacs:FL-1", ids)
        self.assertIn("cordon:advisory:MY", ids)

    def test_no_probability_and_no_cause_anywhere_in_the_item(self):
        state, lines = tl.step({}, doc(A, record()))
        text = json.dumps(lines)
        for word in ('"probability"', '"likelihood_term"', "shutdown likely", '"tier": "warning"'):
            self.assertNotIn(word, text)

    def test_the_place_of_a_state_power_outage_is_the_state(self):
        rec = record(cc=None)
        rec.update({"country": None, "outage_kind": "power", "us_state": "TX", "us_state_name": "Texas"})
        rec["outage"]["id"] = "blackout:power:tx"
        state, _ = tl.step({}, doc(A, rec))
        p = state["open"]["cop:triage:blackout:power:tx:TX"]["item"]["properties"]
        self.assertEqual(p["title"], "Texas: outage triage reading open")
        self.assertEqual(p["countries"], [])
        self.assertEqual(p["triage"]["outage"]["us_state"], "TX")


class History(unittest.TestCase):

    def test_a_build_that_changes_nothing_adds_no_history_and_no_update(self):
        s1, _ = tl.step({}, doc(A, record()))
        s2, lines = tl.step(s1, doc(B, record()))
        self.assertEqual(lines, [])
        p = props(s2)
        self.assertEqual(len(p["triage"]["history"]), 1)
        self.assertEqual(p["cop_last_read_at"], B)
        self.assertEqual(p["data_as_of"], A)
        self.assertEqual(s2["open"][f"cop:triage:{OUTAGE}:MY"]["updates"], 0)

    def test_a_reading_change_is_kept_in_the_items_history_not_as_a_kit_line(self):
        s1, _ = tl.step({}, doc(A, record()))
        s2, lines = tl.step(s1, doc(B, record("tension context", watchfloor=True)))
        if tl.kit_tracks_readings():              # kit 0.5.13 and later: the reading's move is its own kit line too
            self.assertEqual([x["change"] for x in lines], ["changed"])
            self.assertEqual(lines[0]["was"][tl.READING_KEY], "open")
        else:                                     # kit 0.5.x: the kit writes changed only for tier or stage
            self.assertEqual(lines, [])
        p = props(s2)
        self.assertEqual([h["reading"] for h in p["triage"]["history"]], ["open", "tension context"])
        self.assertEqual(p["triage"]["history"][-1]["found"]["watchfloor"], True)
        self.assertEqual((p["last_change"], p["last_change_at"], p["data_as_of"]), ("changed", B, B))
        self.assertEqual(p["title"], "Malaysia: outage triage reading tension context")
        s3, _ = tl.step(s2, doc(C, record("tension context", watchfloor=True)))
        self.assertEqual(len(props(s3)["triage"]["history"]), 2)
        self.assertEqual(props(s3)["last_change_at"], B)

    def test_a_layer_that_finds_something_new_is_history_even_when_the_reading_holds(self):
        s1, _ = tl.step({}, doc(A, record("natural")))
        s2, _ = tl.step(s1, doc(B, record("natural", cordon=True)))
        h = props(s2)["triage"]["history"]
        self.assertEqual([x["reading"] for x in h], ["natural", "natural"])
        self.assertEqual([x["found"]["cordon"] for x in h], [False, True])

    def test_the_first_entry_is_the_reading_as_first_published(self):
        s1, _ = tl.step({}, doc(A, record("incomplete")))
        s2, _ = tl.step(s1, doc(B, record("open")))
        self.assertEqual(props(s2)["triage"]["history"][0], {"at": A, "reading": "incomplete",
                                                             "found": {"cordon": False, "elements": True,
                                                                       "watchfloor": False}})


class Ends(unittest.TestCase):

    def test_an_outage_that_leaves_triage_ends_with_its_whole_history(self):
        s1, _ = tl.step({}, doc(A, record()))
        s2, _ = tl.step(s1, doc(B, record("tension context", watchfloor=True)))
        s3, lines = tl.step(s2, doc(C))
        self.assertEqual([x["change"] for x in lines], ["ended"])
        self.assertEqual(len(lines[0]["item"]["properties"]["triage"]["history"]), 2)
        self.assertEqual(s3["open"], {})

    def test_nothing_ends_on_a_build_that_did_not_read_blackout(self):
        s1, _ = tl.step({}, doc(A, record()))
        for state in ("stale", "missing"):
            s2, lines = tl.step(s1, doc(B, blackout=state))
            self.assertEqual(lines, [])
            self.assertIn(f"cop:triage:{OUTAGE}:MY", s2["open"])
            self.assertFalse(s2["blackout_read"])


class Gate(unittest.TestCase):

    def test_a_record_citing_a_layer_triage_never_declared_is_refused(self):
        with self.assertRaises(SystemExit):
            tl.step({}, doc(A, record(extra_id="vigil:kev:CVE-2026-1")))

    def test_the_log_is_never_written_where_the_build_publishes(self):
        for d in ("site", "site/data", "functions", "mcp"):
            with self.assertRaises(SystemExit):
                tl.ledger._folder(ROOT / d / "triage-log")


class Files(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_run_needs_the_last_state_or_first(self):
        with self.assertRaises(SystemExit):
            tl.run(doc(A, record()), self.folder)
        tl.run(doc(A, record()), self.folder, first=True)
        with self.assertRaises(SystemExit):                       # not older than this triage: nothing written
            tl.run(doc(A, record()), self.folder)
        tl.run(doc(B, record("tension context", watchfloor=True)), self.folder)
        with gzip.open(self.folder / "state.json.gz", "rt", encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(len(props(state)["triage"]["history"]), 2)
        with gzip.open(self.folder / "2026-11-17.jsonl.gz", "rt", encoding="utf-8") as fh:
            self.assertEqual([json.loads(s)["change"] for s in fh],
                             ["new", "changed"] if tl.kit_tracks_readings() else ["new"])

    def test_a_replay_marks_every_line_and_never_runs_over_a_log(self):
        docs = [doc(A, record("incomplete")), doc(B, record("open")), doc(C)]
        real = tl.replay_docs
        tl.replay_docs = lambda archive: iter(copy.deepcopy(docs))
        try:
            tl.replay(Path(self.tmp.name), self.folder)
            with gzip.open(self.folder / "2026-11-17.jsonl.gz", "rt", encoding="utf-8") as fh:
                lines = [json.loads(s) for s in fh]
            self.assertEqual([x["change"] for x in lines],
                             ["new", "changed", "ended"] if tl.kit_tracks_readings() else ["new", "ended"])
            self.assertTrue(all(x["backfill"] == "daily archive" for x in lines))
            self.assertEqual([h["reading"] for h in lines[-1]["item"]["properties"]["triage"]["history"]],
                             ["incomplete", "open"])
            with self.assertRaises(SystemExit):
                tl.replay(Path(self.tmp.name), self.folder)
        finally:
            tl.replay_docs = real

    def test_a_replay_with_no_archive_stops(self):
        with self.assertRaises(SystemExit):
            tl.replay(Path(self.tmp.name), self.folder)


if __name__ == "__main__":
    unittest.main()
