"""The STOP replay beside triage's base rates (cop-purpose sequence step 4, second half): python tests/test_triage_replay.py"""

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import gate  # noqa: E402
import test_triage as tt  # noqa: E402
import triage as tri  # noqa: E402
import triage_rates as rates  # noqa: E402
import triage_replay as rp  # noqa: E402

BANNED = re.compile(r"\b(detect\w*|predict\w*|likely|unlikely|warns?|alert(?:s|ed)? (?:to|of) shutdowns?)\b", re.I)


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = rp.load()

    def test_it_reads_the_committed_manifests_and_keeps_counts_and_rates_only(self):
        r = self.rep
        self.assertEqual(r["runs"], {"two_layer": "stop-2025-two-layer-2026-11-16",
                                     "three_layer": "stop-2025-three-layer-2026-11-16"})
        w, t = r["two_layer"], r["three_layer"]
        self.assertEqual((w["shutdowns"], w["matched_shutdowns"], w["matched_left_out"]), (255, 172, ["MM"]))
        self.assertEqual(w["shutdown_shares"], {"natural": 0.1512, "tension context": 0.0814, "open": 0.6221,
                                                "incomplete": 0.1453})
        self.assertEqual(w["ordinary_day_shares"], {"natural": 0.058, "tension context": 0.0561, "open": 0.8352,
                                                    "incomplete": 0.0507})
        self.assertEqual((t["matched_shutdowns"], len(t["places"])), (157, 25))
        self.assertEqual(t["shutdown_shares"]["tension context"], 0.1338)
        self.assertEqual(t["ordinary_day_shares"]["tension context"], 0.0885)
        self.assertEqual(r["attribution"], "Access Now, #KeepItOn STOP")
        self.assertIn("counts and rates only", r["licence"])
        self.assertEqual(r["by_country"]["MM"]["ordinary_days"], 0)
        self.assertEqual(r["by_country"]["IN"]["shutdowns"], 63)
        self.assertEqual(r["by_country"]["IN"]["layers"], "three_layer", "Cordon's countries read the three-layer row")
        # No row, no date, no event: nothing from the sheet but counts and shares by country.
        for s in strings({k: v for k, v in r.items() if k not in ("built_at", "runs", "caveats", "what")}):
            self.assertIsNone(re.search(r"\d{4}-\d\d-\d\d", s), s)
        self.assertFalse(any(k in json.dumps(r) for k in ("sheet", "stop_sheet", "sha256", "D:/")))

    def test_the_caution_reads_its_directions_from_the_figures(self):
        c = self.rep["caution"]
        self.assertIn("natural and tension context showed more often on shutdown days than on ordinary days", c)
        self.assertIn("open less often", c)
        self.assertIn("39 countries", c)
        self.assertIn("does not show that a reading identifies a shutdown or its cause", c)
        self.assertTrue(c.endswith("most documented shutdowns still read open."))
        flipped = copy.deepcopy(self.rep)
        flipped["two_layer"]["shutdown_shares"] = {"natural": 0.05, "tension context": 0.0561, "open": 0.4,
                                                   "incomplete": 0.2}
        c2 = rp.caution(flipped)
        self.assertIn("natural showed less often and tension context about as often", c2)
        self.assertIn("open less often", c2)
        self.assertFalse(c2.endswith("still read open."), "open is not most when it is not")

    def test_a_missing_or_foreign_run_is_no_replay_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(rp.load(d))
            for kind in ("two", "three"):
                f = Path(d) / f"stop-2025-{kind}-layer-x" / "manifest.json"
                f.parent.mkdir()
                f.write_text(json.dumps({"contract": "something-else"}), encoding="utf-8")
            self.assertIsNone(rp.load(d), "a manifest of another contract is not read")
            for kind in ("two", "three"):
                (Path(d) / f"stop-2025-{kind}-layer-x" / "manifest.json").write_text("{", encoding="utf-8")
            self.assertIsNone(rp.load(d), "unreadable JSON is no replay")

    def test_a_record_gets_the_pooled_rates_and_its_own_countrys(self):
        x = rp.record_rate(self.rep, "IN", "tension context", "India")
        self.assertEqual((x["pool"], x["matched_shutdowns"]), ("three_layer", 157))
        self.assertEqual((x["shutdown_share"], x["ordinary_day_share"]), (0.1338, 0.0885))
        self.assertEqual(x["country"]["shutdowns"], 63)
        self.assertEqual(x["country"]["read"], 5)
        self.assertEqual(x["country"]["ordinary_days"], 67)
        self.assertIn("tension context was the reading on 13.4% of shutdowns and on 8.8% of those countries' ordinary days", x["text"])
        self.assertIn("In India: 5 of 63 documented shutdowns read tension context, against 7.5% of its 67 ordinary days", x["text"])
        self.assertEqual(x["caveats"], self.rep["three_layer"]["caveats"], "its own run's caveats, the three-layer one's")
        self.assertNotIn(rp.NO_CORDON, x["caveats"])
        self.assertEqual(x["attribution"], "Access Now, #KeepItOn STOP")

    def test_caveats_are_served_per_run_and_the_shared_list_does_not_contradict_itself(self):
        r = self.rep
        two = json.loads((rp.REPLAYS / r["runs"]["two_layer"] / "manifest.json").read_text(encoding="utf-8"))["caveats"]
        three = json.loads((rp.REPLAYS / r["runs"]["three_layer"] / "manifest.json").read_text(encoding="utf-8"))["caveats"]
        # Every caveat of each run reaches the reader exactly once, beside its own pool or in the shared list.
        self.assertEqual(sorted(r["caveats"] + r["two_layer"]["caveats"]), sorted(two))
        self.assertEqual(sorted(r["caveats"] + r["three_layer"]["caveats"]), sorted(three))
        self.assertTrue(all(c in two and c in three for c in r["caveats"]))
        self.assertFalse(any("not replayed yet" in c for c in r["caveats"]),
                         "the two-layer run's 'not replayed yet' stays with the two-layer pool")
        self.assertTrue(any("not replayed yet" in c for c in r["two_layer"]["caveats"]))
        self.assertTrue(any(c.startswith("Cordon's history") for c in r["three_layer"]["caveats"]))
        self.assertFalse(any(c.startswith("Cordon's history") for c in r["two_layer"]["caveats"]))
        x = rp.record_rate(r, "TN", "open")
        self.assertFalse(set(x["caveats"]) & set(r["three_layer"]["caveats"]), "a two-layer figure carries no three-layer caveat")

    def test_outside_cordons_countries_the_two_layer_figures_say_cordon_was_not_replayed(self):
        x = rp.record_rate(self.rep, "TN", "open", "Tunisia")
        self.assertEqual((x["pool"], x["matched_shutdowns"]), ("two_layer", 172))
        self.assertEqual((x["shutdown_share"], x["ordinary_day_share"]), (0.6221, 0.8352))
        self.assertIsNone(x["country"])
        self.assertIn("STOP documented no 2025 shutdown in Tunisia", x["text"])
        self.assertEqual(x["caveats"], [rp.NO_CORDON])

    def test_myanmar_has_shutdowns_and_no_ordinary_days_and_says_so(self):
        x = rp.record_rate(self.rep, "MM", "natural", "Myanmar")
        self.assertEqual(x["country"]["ordinary_days"], 0)
        self.assertIn("4 of 83 documented shutdowns read natural; it has no ordinary days to compare them with", x["text"])

    def test_unconfirmed_and_unknown_readings_have_no_replay_figure(self):
        self.assertIsNone(rp.record_rate(self.rep, "TN", "unconfirmed"))
        self.assertIsNone(rp.record_rate(None, "TN", "open"))
        for reading in rp.READINGS:
            self.assertIsNotNone(rp.record_rate(self.rep, "IN", reading))

    def test_no_text_says_triage_detects_or_predicts_a_shutdown(self):
        words = [self.rep["caution"], self.rep["what"].replace("It does not say that triage detects shutdowns.", "")]
        for cc in ("IN", "TN", "MM", "US", "IR"):
            for reading in rp.READINGS:
                x = rp.record_rate(self.rep, cc, reading)
                words += [x["text"], *x["caveats"]]
        for w in words:
            self.assertIsNone(BANNED.search(w), w)
        self.assertIn("It does not say that triage detects shutdowns", self.rep["what"])


class Attached(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = rp.load()

    def doc(self):
        snap = tt.snapshot(tt.outage("IN"), tt.outage("TN"))
        return tri.attach_rates(tri.triage(snap), rates.rates([tt.snapshot()]))

    def test_the_replay_sits_beside_each_internet_outage_and_replaces_the_no_hit_rate_caution(self):
        doc = tri.attach_replay(self.doc(), self.rep)
        by = {r["country"]: r for r in doc["records"]}
        self.assertEqual(by["IN"]["reading"], "open")
        self.assertEqual(by["IN"]["replay_rate"]["shutdown_share"], 0.586)
        self.assertEqual(by["TN"]["replay_rate"]["pool"], "two_layer")
        self.assertIn(self.rep["caution"], by["IN"]["base_rate"]["cautions"])
        self.assertNotIn(rates.HIT_RATE_CAUTION, by["IN"]["base_rate"]["cautions"])
        self.assertIn(self.rep["caution"], doc["base_rates"]["cautions"])
        self.assertNotIn(rates.HIT_RATE_CAUTION, doc["base_rates"]["cautions"])
        self.assertIs(doc["replay"], self.rep)

    def test_without_a_replay_the_old_caution_stays_and_no_record_carries_a_figure(self):
        doc = tri.attach_replay(self.doc(), None)
        self.assertIsNone(doc["replay"])
        self.assertTrue(all(r["replay_rate"] is None for r in doc["records"]))
        self.assertIn(rates.HIT_RATE_CAUTION, doc["base_rates"]["cautions"])
        self.assertIn(rates.HIT_RATE_CAUTION, doc["records"][0]["base_rate"]["cautions"])

    def test_power_and_unconfirmed_records_get_no_figure_and_the_state_rates_say_the_replay_is_not_theirs(self):
        h = rates.HIT_RATE_CAUTION
        doc = {"records": [
            {"outage_kind": "power", "country": "US", "reading": "natural", "base_rate": {"cautions": [h]}},
            {"outage_kind": "internet", "country": "TN", "reading": "unconfirmed", "base_rate": None}],
            "base_rates": {"cautions": [h], "states": {"cautions": [h]}}}
        tri.attach_replay(doc, self.rep)
        self.assertTrue(all(r["replay_rate"] is None for r in doc["records"]))
        self.assertEqual(doc["base_rates"]["states"]["cautions"], [rp.STATE_CAUTION],
                         "no power outage of known cause was replayed, and the caution now says STOP is not that")
        self.assertEqual(doc["records"][0]["base_rate"]["cautions"], [rp.STATE_CAUTION])
        self.assertIn("US state power outages", rp.STATE_CAUTION)
        self.assertEqual(doc["base_rates"]["cautions"], [self.rep["caution"]])

    def test_the_public_document_and_the_gate_carry_it(self):
        doc = tri.attach_replay(self.doc(), self.rep)
        pub = tri.public(doc)
        self.assertEqual(pub["replay"]["attribution"], "Access Now, #KeepItOn STOP")
        self.assertEqual({r["country"]: bool(r["replay_rate"]) for r in pub["records"]}, {"IN": True, "TN": True})
        with tempfile.TemporaryDirectory() as d:
            v = gate.write(tri.JOIN, pub, Path(d), (tri.PUBLIC_NAME,))
            self.assertNotEqual(v["review_class"], "refused")
            on_disk = json.loads((Path(d) / tri.PUBLIC_NAME).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["replay"]["three_layer"]["matched_shutdowns"], 157)
        self.assertNotIn("probability", json.dumps(on_disk["replay"]))

    def test_the_build_command_needs_no_new_flag_and_its_tests_run(self):
        build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("python tests/test_triage_replay.py", build)
        self.assertTrue((ROOT / "history" / "replays").is_dir(), "triage.py reads history/replays by default")


if __name__ == "__main__":
    unittest.main()
