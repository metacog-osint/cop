"""The Undertow election join: activity periods, never report dates; nothing inferred; published off.

    python tests/test_undertow_elections.py
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import undertow_elections as ue  # noqa: E402

TODAY = date(2026, 11, 18)


def ut(iid, publisher, targets, period, published, name=None, activity=None, title="a disclosure"):
    p = {"id": iid, "program": "undertow", "title": title, "data_as_of": f"{published}T00:00:00Z",
         "sources": [{"publisher": publisher}], "visual": {"url": f"https://undertow.theintelligence.company/#{iid}"},
         "operation": {"operation_name": name, "targets": targets, "period": period}}
    if activity is not None:
        p["activity"] = activity
    return {"type": "Feature", "geometry": None, "properties": p}


def vote(cc, day, vote_kind, rnd="single", occurred=True, verified=True):
    ev = {"kind": "election", "date": day, "occurred": occurred, "verified": verified,
          "ref": f"election:{cc}:{vote_kind}:{rnd}"}
    return {"type": "Feature", "geometry": None, "properties": {
        "id": f"verdict:calendar:{day}-{cc.lower()}-{vote_kind}", "program": "verdict", "title": f"{cc} vote",
        "countries": [cc], "event": ev, "sources": [{"publisher": "Electoral body", "url": "https://example.org"}]}}


# Armenia, 7 Jun 2026: DFRLab's report of 12 Oct falls outside by report date and inside by activity period.
DFRLAB_AM = ut("UT-WATCH-c12152a686", "DFRLab", ["AM"], "2026-10", "2026-10-12",
               name="Inauthentic networks amplifying fears of war before Armenia's elections",
               activity={"activity_period": {"from": "2026-01", "to": "2026-06"}, "first_report": "2026-10-12"})
CHECKFIRST_AM = ut("UT-WATCH-f93f56280a", "CheckFirst", ["AM"], "2026-06", "2026-06-10", name="Pro-Russian claims",
                   activity={"activity_period": {"from": "2026-05", "to": "2026-06"}})
# A reporter's item has report dates only: never used.
REPORTER = ut("UT-CORRECTIV-361d295870", "CORRECTIV", ["US"], "2026-11", "2026-11-11", name="Storm-1516",
              activity={"first_report": "2026-11-11", "last_report": "2026-11-11"})
TIKTOK_HU = ut("UT-TIKTOK-8399e2beb4", "TikTok", ["HU"], "2026-03", "2026-09-24",
               title="TikTok removed a network of 62 accounts that targeted a Hungarian audience")
META_US = ut("UT-META-4b0ee74e66", "Meta", ["US", "IL"], "H2 2026", "2026-08-27", title="Targeting United States")
OLD = ut("UT-EEAS-old", "European External Action Service", ["FR"], "2024", "2025-03-12", name="Old set")
OFF_SCOPE = ut("UT-TIKTOK-xx", "TikTok", ["KE"], "2026-03", "2026-09-24")
SNAP = {"type": "FeatureCollection", "features": [DFRLAB_AM, CHECKFIRST_AM, REPORTER, TIKTOK_HU, META_US, OLD, OFF_SCOPE]}
CAL = {"type": "FeatureCollection", "generated_at": "2026-11-18T12:00:00Z", "features": [
    vote("AM", "2026-06-07", "legislative"), vote("HU", "2026-04-12", "legislative"),
    vote("US", "2026-11-03", "legislative"), vote("IL", "2026-10-27", "legislative"),
    vote("FR", "2027-04-11", "presidential", "first", occurred=False, verified=False),
    {"type": "Feature", "geometry": None, "properties": {"id": "verdict:calendar:x", "countries": ["AM"],
                                                          "event": {"kind": "summit", "date": "2026-06-01"}}}]}


class Periods(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(ue.parse_period("2026-03"), (date(2026, 3, 1), date(2026, 3, 31)))
        self.assertEqual(ue.parse_period("2026-01 to 2026-06"), (date(2026, 1, 1), date(2026, 6, 30)))
        self.assertEqual(ue.parse_period("H2 2025"), (date(2025, 7, 1), date(2025, 12, 31)))
        self.assertEqual(ue.parse_period("H1 2026"), (date(2026, 1, 1), date(2026, 6, 30)))
        self.assertEqual(ue.parse_period("2025"), (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(ue.parse_period("Q3 2025"), (date(2025, 7, 1), date(2025, 9, 30)))
        self.assertIsNone(ue.parse_period("sometime"))
        self.assertIsNone(ue.parse_period(None))

    def test_activity_period_wins_and_report_dates_are_never_used(self):
        per, why = ue.item_period(DFRLAB_AM["properties"])
        self.assertEqual(per[:3], (date(2026, 1, 1), date(2026, 6, 30), "activity period"))
        self.assertEqual(ue.item_period(REPORTER["properties"]), (None, "report dates only"))

    def test_a_disclosure_period_is_cut_at_its_publication(self):
        per, _ = ue.item_period(META_US["properties"])
        self.assertEqual(per[:3], (date(2026, 7, 1), date(2026, 8, 27), "disclosure period"))


class Join(unittest.TestCase):
    def setUp(self):
        self.doc = ue.run(SNAP, CAL, today=TODAY)
        self.by = {(m["election"]["country"], m["election"]["date"]): m for m in self.doc["matches"]}

    def test_scope_file(self):
        s = ue.load_scope()
        self.assertEqual(len(s["countries"]), 40)
        self.assertEqual(set(s["names"]), set(s["countries"]))
        self.assertEqual((s["calendar_from"], s["calendar_to"]), ("2024-11-01", "2027-12-31"))

    def test_the_armenia_case_matches_by_activity_period(self):
        m = self.by[("AM", "2026-06-07")]
        self.assertEqual(m["window"], {"from": "2026-03-09", "to": "2026-06-07"})
        ids = [u["id"] for u in m["undertow"]]
        self.assertEqual(ids, ["UT-WATCH-c12152a686", "UT-WATCH-f93f56280a"])
        d = m["undertow"][0]
        self.assertEqual((d["fit"], d["period_days"]), ("overlaps", 181))
        self.assertEqual(d["sentence"],
                         "Armenia voted on 2026-06-07 in a parliamentary election; DFRLab reported Inauthentic networks "
                         "amplifying fears of war before Armenia's elections targeting Armenia from 157 days before to "
                         "23 days after (activity period 2026-01 to 2026-06).")

    def test_month_inside_the_window(self):
        d = self.by[("HU", "2026-04-12")]["undertow"][0]
        self.assertEqual((d["fit"], d["period_days"]), ("inside", 31))
        self.assertIn("from 42 to 12 days before (disclosure period 2026-03)", d["sentence"])
        self.assertIn('TikTok reported the operation in "TikTok removed a network', d["sentence"])

    def test_both_targets_and_a_cut_period(self):
        self.assertEqual([u["id"] for u in self.by[("US", "2026-11-03")]["undertow"]], ["UT-META-4b0ee74e66"])
        self.assertEqual([u["id"] for u in self.by[("IL", "2026-10-27")]["undertow"]], ["UT-META-4b0ee74e66"])
        self.assertNotIn(("FR", "2027-04-11"), self.by)

    def test_span_words(self):
        self.assertEqual(ue._span_words(18, 0), "from 18 days before to election day")
        self.assertEqual(ue._span_words(29, -1), "from 29 days before to 1 day after")
        self.assertEqual(ue._span_words(5, 5), "5 days before")
        self.assertEqual(ue._span_words(0, 0), "on election day")
        self.assertEqual(ue._span_words(42, 12), "from 42 to 12 days before")

    def test_us_house_wording_and_capital(self):
        el = {"country": "US", "date": "2026-11-03", "occurred": True, "verified": True, "vote": "legislative",
              "round": "single"}
        it = {"publisher": "TikTok, via the EU Code", "operation": "X", "period_from": date(2026, 9, 1),
              "period_to": date(2026, 9, 30), "period_source": "disclosure period", "period_as_written": "2026-09"}
        s = ue.sentence(el, it, {"US": "the United States"})
        self.assertTrue(s.startswith("The United States voted on 2026-11-03 in an election to the House of "
                                     "Representatives; TikTok, via the EU Code, reported X targeting the United States"))

    def test_unverified_and_future_votes_are_worded_as_such(self):
        el = {"country": "FR", "date": "2027-04-11", "occurred": False, "verified": False, "vote": "presidential",
              "round": "first"}
        it = {"publisher": "Meta", "operation": "X", "period_from": date(2027, 2, 1), "period_to": date(2027, 2, 28),
              "period_source": "disclosure period", "period_as_written": "2027-02"}
        s = ue.sentence(el, it, {"FR": "France"})
        self.assertTrue(s.startswith("France votes on 2027-04-11 in a presidential election (first round);"))
        self.assertTrue(s.endswith("The vote's date is not yet read on an official page."))

    def test_left_out_is_counted(self):
        lo = self.doc["left_out"]
        self.assertEqual(lo["report dates only"], 1)
        self.assertEqual(lo["period begins before the calendar"], 1)
        self.assertEqual(lo["target not on the calendar"], {"KE": 1})
        self.assertEqual(self.doc["items_considered"], 4)

    def test_only_elections_are_read(self):
        self.assertEqual(sum(len(v) for v in ue.elections(CAL).values()), 5)

    def test_base_rate(self):
        b = self.doc["base_rate"]
        self.assertEqual((b["pairs"], b["in_window"]), (5, 5))
        self.assertTrue(0 < b["share_of_days_in_window"] < b["share_if_placed_at_random"] <= 1)
        self.assertIn("A described measure, not a rule", b["sentence"])
        empty = ue.run({"features": []}, CAL, today=TODAY)["base_rate"]
        self.assertEqual(empty["pairs"], 0)

    def test_voice_rule(self):
        words = " ".join([ue.NOT_A_FINDING, self.doc["base_rate"]["sentence"], self.doc["base_rate"]["caveat"]]
                         + [u["sentence"] for m in self.doc["matches"] for u in m["undertow"]]).lower()
        for w in ("likely", "intent", "aimed at", "interfer", "influenced", "sponsor", "confirms", "proves",
                  "warning", "because"):
            if w == "aimed at":
                self.assertEqual(words.count(w), 1, w)     # only in "does not say the activity aimed at the vote"
                continue
            self.assertIsNone(re.search(rf"\b{w}", words), w)
        self.assertIn("does not say the activity aimed at the vote", ue.NOT_A_FINDING)

    def test_gate_open_but_not_published(self):
        self.assertFalse(ue.JOIN["published"])
        self.assertEqual(ue.JOIN["inputs"], ["undertow", "verdict:calendar"])
        v = gate.review(ue.JOIN, self.doc)
        self.assertEqual(v["review_class"], "open", v["reasons"])
        self.assertFalse(v["may_publish"])
        self.assertIsNotNone(gate.allowed(v, ROOT / "site" / "data"))
        tmp = Path(tempfile.mkdtemp(prefix="cop-ue-"))
        try:
            gate.write(ue.JOIN, self.doc, tmp, ["undertow-elections.json"])
            self.assertTrue((tmp / "undertow-elections.json").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_citing_an_undeclared_layer_is_refused(self):
        doc = json.loads(json.dumps(self.doc))
        doc["matches"][0]["undertow"].append({"id": "vigil:claim:x"})
        self.assertEqual(gate.review(ue.JOIN, doc)["review_class"], "refused")


if __name__ == "__main__":
    unittest.main()
