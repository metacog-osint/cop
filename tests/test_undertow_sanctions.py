"""The Undertow sanctions join: exact names, organisations only, nothing inferred, published off."""
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
import undertow_sanctions as us  # noqa: E402

LISTED = [{"organisation": "Social Design Agency", "list": "eu-fsf", "listed": "2023-07-28"},
          {"organisation": "Social Design Agency", "list": "ofac-sdn", "listed": "2024-03-20"},
          {"organisation": "Social Design Agency", "list": "uk-sanctions", "listed": "2024-10-28"},
          {"organisation": "Social Design Agency", "list": "canada-sema", "listed": "2025-02-21"},
          {"organisation": "ANO Dialog", "list": "ofac-sdn", "listed": "2024-09-04"}]


def ut(iid, publisher, op, sponsor, listed_by=()):
    return {"type": "Feature", "geometry": None, "properties": {
        "id": iid, "program": "undertow", "title": f"{op} ({publisher})", "sources": [{"publisher": publisher}],
        "visual": {"url": f"https://undertow.theintelligence.company/#{iid}"},
        "operation": {"operation_name": op, "sponsor": list(sponsor), "listed_by": list(listed_by),
                      "disclosure": {"title": "a disclosure"}}}}


SNAP = {"type": "FeatureCollection", "features": [
    ut("UT-EEAS-71fc909be8", "European External Action Service", "Doppelganger", ["Social Design Agency", "Struktura"],
       LISTED[:4]),
    ut("UT-META-27d0708765", "Meta", "Doppelganger", ["Social Design Agency", "ANO Dialog"], LISTED),
    ut("UT-EEAS-aaaaaaaaaa", "European External Action Service", "Paperwall", ["Haimai"]),
    {"type": "Feature", "geometry": None, "properties": {"id": "headwind:sanctions:ofac-sdn:20261120T1400",
                                                         "program": "headwind"}}]}


def change(list_id, stamp, **named):
    return {"kind": "sanctions", "id": f"{list_id}:{stamp}", "list": list_id, "detected_at": "2026-11-20T14:00:00Z",
            "named_added": [], "named_removed": [], "named_changed": [], **named}


LC = {"generated_at": "2026-11-20T15:00:00Z", "changes": [
    change("ofac-sdn", "20261120T1400", named_added=[
        {"list_id": "45230", "name": "SOCIAL DESIGN AGENCY", "type": "entity", "programs": ["RUSSIA-EO14024"]},
        {"list_id": "99999", "name": "SOCIAL DESIGN AGENCY", "type": "individual", "programs": ["RUSSIA-EO14024"]},
        {"list_id": "99998", "name": "Social Design Agency", "type": "entity", "programs": ["RUSSIA-EO14024"]}]),
    change("uk-sanctions", "20261120T1400", named_changed=[
        {"list_id": "RUS2294", "name": "ANO DIALOG", "type": "entity", "programs": ["Russia", "Cyber"],
         "gained": ["Cyber"], "lost": []}]),
    change("eu-fsf", "20261120T1400", named_changed=[
        {"list_id": "155817", "name": "Social Design Agency", "type": "entity", "programs": ["UKR", "RUSDA"],
         "gained": ["RUSDA"], "lost": []}]),
    {"kind": "screening", "id": "trade-gov:x", "list": "ofac-sdn", "named_added": [
        {"list_id": "45230", "name": "SOCIAL DESIGN AGENCY", "type": "entity", "programs": []}]}]}


class Join(unittest.TestCase):
    def setUp(self):
        self.doc = us.run(SNAP, LC, today=date(2026, 11, 20))

    def test_the_table_passes_its_check(self):
        self.assertEqual(us.check_table(us.load_table()), [])
        t = us.load_table()
        self.assertEqual({r["organisation"] for r in t["rows"]}, {"Social Design Agency", "ANO Dialog", "Struktura"})
        self.assertEqual(len(t["rows"]), 15)

    def test_exact_entity_names_only(self):
        m = self.doc["matches"]
        self.assertEqual(sorted((x["list"], x["organisation"], x["action"]) for x in m),
                         [("ofac-sdn", "Social Design Agency", "added to"), ("uk-sanctions", "ANO Dialog", "changed on")])
        # the individual with the same name, the differently-cased entity and the screening record never match
        self.assertEqual(sum(1 for x in m if x["list"] == "ofac-sdn"), 1)

    def test_stale_row_is_reported_not_matched(self):
        self.assertEqual([(s["list"], s["entry_id"], s["headwind_name"]) for s in self.doc["stale_rows"]],
                         [("eu-fsf", "155817", "Social Design Agency")])

    def test_sentences_say_what_headwind_saw_and_what_the_publisher_names(self):
        sda = next(x for x in self.doc["matches"] if x["organisation"] == "Social Design Agency")
        self.assertEqual([u["id"] for u in sda["undertow"]], ["UT-EEAS-71fc909be8", "UT-META-27d0708765"])
        self.assertEqual(sda["sentences"][1],
                         "Headwind saw SOCIAL DESIGN AGENCY added to the OFAC SDN list (RUSSIA-EO14024) on 2026-11-20. "
                         "Meta names Social Design Agency in its disclosure of Doppelganger.")
        ano = next(x for x in self.doc["matches"] if x["organisation"] == "ANO Dialog")
        self.assertIn("(gained Cyber)", ano["sentences"][0])

    def test_voice_rule(self):
        words = " ".join([us.NOT_A_FINDING, self.doc["base_rate"]["sentence"], self.doc["base_rate"]["caveat"]]
                         + [s for x in self.doc["matches"] for s in x["sentences"]]).lower()
        for w in ("likely", "intent", "directed", "behind", "sponsor", "confirms", "proves", "warning"):
            self.assertIsNone(re.search(rf"\b{w}", words), w)
        self.assertIn("not a finding of origin", us.NOT_A_FINDING)

    def test_base_rate(self):
        b = self.doc["base_rate"]
        self.assertEqual((b["listing_actions"], b["months"], b["first"], b["last"]), (5, 41, "2023-07-28", "2025-02-21"))
        self.assertEqual(b["per_month"], 0.12)
        self.assertEqual(b["headwind_changes_naming_any"], 2)
        empty = us.run({"features": []}, {"changes": []}, today=date(2026, 11, 20))["base_rate"]
        self.assertIsNone(empty["per_month"])

    def test_gate_open_but_not_published(self):
        self.assertFalse(us.JOIN["published"])
        self.assertEqual(us.JOIN["inputs"], ["undertow", "headwind:sanctions"])
        v = gate.review(us.JOIN, self.doc)
        self.assertEqual(v["review_class"], "open", v["reasons"])
        self.assertFalse(v["may_publish"])
        self.assertIsNotNone(gate.allowed(v, ROOT / "site" / "data"))
        tmp = Path(tempfile.mkdtemp(prefix="cop-us-"))
        try:
            gate.write(us.JOIN, self.doc, tmp, ["undertow-sanctions.json"])
            self.assertTrue((tmp / "undertow-sanctions.json").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_join_citing_an_undeclared_layer_is_refused(self):
        doc = json.loads(json.dumps(self.doc))
        doc["matches"][0]["undertow"].append({"id": "vigil:claim:x"})
        self.assertEqual(gate.review(us.JOIN, doc)["review_class"], "refused")


if __name__ == "__main__":
    unittest.main()
