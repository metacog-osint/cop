"""Registering a drafted triage question in Verdict: python tests/test_register_question.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import register_question as rq  # noqa: E402
import test_triage as tt  # noqa: E402
import triage as tri  # noqa: E402

WHEN = "2026-11-12T03:00:00Z"
NO_VERDICT = Path(tempfile.mkdtemp())   # no Verdict checkout here: only the kit's contract is checked


def published(*features):
    return tri.public(tri.attach_rates(tri.triage(tt.snapshot(*features)), None))


class Register(unittest.TestCase):
    def setUp(self):
        self.doc = published(tt.outage(), tt.outage("SC", "BO-IND-4"))
        self.tn = next(r for r in self.doc["records"] if r["country"] == "TN")

    def test_both_questions_build_into_forecasts_the_contract_accepts(self):
        for key in ("question_draft", "cause_question_draft"):
            r, q, what = rq.find(self.doc, self.tn[key]["verdict_id"])
            item = rq.build_item(self.doc, r, q, what, 0.2, "A. Analyst", WHEN)
            self.assertEqual(rq.problems(item, NO_VERDICT), [], key)
            self.assertEqual((item["probability"], item["likelihood_term"]), (0.2, "unlikely"))
            self.assertIn(item["confidence"], ("low", "moderate", "high"))
            self.assertEqual(item["resolution"]["check_after"], q["resolution"]["known_at"])
            self.assertIn("A. Analyst's", item["summary"])
            self.assertEqual(item["id"], q["verdict_id"])

    def test_the_probability_is_always_the_callers(self):
        r, q, what = rq.find(self.doc, self.tn["cause_question_draft"]["verdict_id"])
        for bad in (None, 0, 1, 1.5, -0.1):
            with self.assertRaises(SystemExit):
                rq.build_item(self.doc, r, q, what, bad, "A. Analyst", WHEN)
        for r in self.doc["records"]:
            for key in ("question_draft", "cause_question_draft"):
                self.assertNotIn("probability", r[key], "the public draft never carries a probability")

    def test_an_unconfirmed_outage_is_not_registered(self):
        sc = next(r for r in self.doc["records"] if r["country"] == "SC")
        r, q, what = rq.find(self.doc, sc["cause_question_draft"]["verdict_id"])
        with self.assertRaisesRegex(SystemExit, "not confirmed"):
            rq.build_item(self.doc, r, q, what, 0.2, "A. Analyst", WHEN)

    def test_a_closed_window_is_refused_and_ooni_over_the_line_still_registers(self):
        r, q, what = rq.find(self.doc, self.tn["question_draft"]["verdict_id"])
        with self.assertRaisesRegex(SystemExit, "window has closed"):
            rq.build_item(self.doc, r, q, what, 0.2, "A. Analyst", "2026-11-20T00:00:00Z")
        # The OONI rule is relative to the 120 hours before, so a country already over 15% is not settled early.
        ooni = tt.item("blackout", "blackout:ooni:ir:2026-11-12", ["IR"], "anomaly", indicator_id="BO-IND-3",
                       title="Iran: 37.0% OONI censorship anomaly rate")
        doc = published(tt.outage("IR"), ooni)
        r, q, what = rq.find(doc, doc["records"][0]["question_draft"]["verdict_id"])
        item = rq.build_item(doc, r, q, what, 0.2, "A. Analyst", WHEN)
        self.assertIn("probe_cc=IR", item["resolution"]["rule"])
        self.assertEqual(rq.problems(item, NO_VERDICT), [])

    def test_an_unknown_id_names_the_ones_there_are(self):
        with self.assertRaisesRegex(SystemExit, "cop-tn-outage-20261112t0025-cause"):
            rq.find(self.doc, "verdict:2026-11-19:nothing-like-it")

    def test_the_draft_goes_into_verdicts_own_drafts_file_once(self):
        root = Path(tempfile.mkdtemp())
        r, q, what = rq.find(self.doc, self.tn["cause_question_draft"]["verdict_id"])
        item = rq.build_item(self.doc, r, q, what, 0.2, "A. Analyst", WHEN)
        path = rq.add_draft(root, item, "A. Analyst")
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((doc["drafts"][0]["approved"], doc["drafts"][0]["item"]["id"]), ("A. Analyst", item["id"]))
        with self.assertRaisesRegex(SystemExit, "already"):
            rq.add_draft(root, item, "A. Analyst")

    def test_the_build_never_runs_it(self):
        build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertNotIn("tools/register_question.py", build)

    @unittest.skipUnless((rq.VERDICT / "tools" / "verdict.py").exists(), "no Verdict checkout on this machine")
    def test_verdicts_own_rules_accept_it(self):
        r, q, what = rq.find(self.doc, self.tn["cause_question_draft"]["verdict_id"])
        item = rq.build_item(self.doc, r, q, what, 0.2, "A. Analyst", WHEN)
        self.assertEqual(rq.problems(item, rq.VERDICT), [])


if __name__ == "__main__":
    unittest.main()
