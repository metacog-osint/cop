"""The lane log and lane records (ideas#330): logged once before the outcome, chained, segmented by method version,
counts only, and never a rate before the minimum scored."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import lane_log as L  # noqa: E402
import triage_replay as replay_mod  # noqa: E402


def at(text):
    return datetime.fromisoformat(text + "+00:00").astimezone(timezone.utc)


def triage(version="0.9", readings=(("blackout:radar-anomaly:aaa", "open"), ("blackout:outage:bbb", "unconfirmed"))):
    recs = [{"country": "CM", "outage_kind": "internet", "reading": r,
             "outage": {"id": i, "title": "Cameroon outage", "data_as_of": "2026-11-25T01:00:00Z", "confirmed": True}}
            for i, r in readings]
    recs.append({"country": "US", "outage_kind": "power", "reading": "natural",
                 "outage": {"id": "blackout:power:tx", "title": "Texas"}})
    return {"join": {"id": "outage-triage", "version": version}, "records": recs}


def three_signal(reading="partial"):
    return {"join": {"id": "three-signal", "version": "0.4"},
            "records": [{"area": "hormuz", "area_name": "Hormuz", "reading": reading, "signals_present": ["notice"]}]}


class LaneLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "lane-log.jsonl"
        self.cfg = L.load_config()

    def tearDown(self):
        self.dir.cleanup()

    def lines(self):
        return [r for _, r in L.read_log(self.path)]

    def test_each_item_is_logged_once_with_its_horizon(self):
        n = L.update(self.path, self.cfg, {"triage": triage(), "three-signal": three_signal()}, at("2026-11-25T04:20:00"))
        self.assertEqual(n, 3, "two internet outages and one three-signal reading; the power outage is not this lane")
        again = L.update(self.path, self.cfg, {"triage": triage(), "three-signal": three_signal()}, at("2026-11-25T06:20:00"))
        self.assertEqual(again, 0)
        first = self.lines()[0]
        self.assertEqual(first["lane"], "blackout-outage-triage")
        self.assertEqual(first["method_version"], "outage-triage 0.9")
        self.assertEqual(first["horizon_ends"], "2026-12-09T04:20:00Z")
        self.assertEqual(first["logged_at"], "2026-11-25T04:20:00Z")

    def test_a_three_signal_reading_logs_once_a_day_and_none_is_never_a_flag(self):
        L.update(self.path, self.cfg, {"three-signal": three_signal()}, at("2026-11-25T04:20:00"))
        L.update(self.path, self.cfg, {"three-signal": three_signal()}, at("2026-11-26T04:20:00"))
        L.update(self.path, self.cfg, {"three-signal": three_signal("none")}, at("2026-11-27T04:20:00"))
        self.assertEqual([r["key"] for r in self.lines()], ["hormuz:partial:2026-11-25", "hormuz:partial:2026-11-26"])

    def test_a_missing_document_logs_nothing(self):
        self.assertEqual(L.update(self.path, self.cfg, {"triage": None, "three-signal": None}, at("2026-11-25T04:20:00")), 0)

    def test_a_changed_line_breaks_the_chain(self):
        L.update(self.path, self.cfg, {"triage": triage()}, at("2026-11-25T04:20:00"))
        text = self.path.read_text(encoding="utf-8")
        self.path.write_text(text.replace('"open"', '"natural"', 1), encoding="utf-8", newline="\n")
        with self.assertRaises(SystemExit):
            L.read_log(self.path)

    def test_a_method_change_starts_a_new_segment_never_pooled(self):
        L.update(self.path, self.cfg, {"triage": triage()}, at("2026-11-25T04:20:00"))
        L.update(self.path, self.cfg, {"triage": triage("1.0", (("blackout:radar-anomaly:aaa", "natural"),))},
                 at("2026-11-26T04:20:00"))
        doc = L.records(self.cfg, self.lines(), [], None, at("2026-11-26T04:30:00"))
        row = next(x for x in doc["lanes"] if x["id"] == "blackout-outage-triage")
        self.assertEqual(row["method_version"], "outage-triage 1.0")
        self.assertEqual(row["flagged"], 1)
        self.assertEqual(row["since"], "2026-11-26")
        self.assertEqual(row["older_segments"], ["outage-triage 0.9"])

    def test_the_record_is_counts_only_with_the_replay_labelled(self):
        L.update(self.path, self.cfg, {"triage": triage()}, at("2026-11-25T04:20:00"))
        doc = L.records(self.cfg, self.lines(), [], replay_mod.load(), at("2026-11-25T04:30:00"))
        self.assertNotIn('"join"', json.dumps(doc), "not a join: the gate check must not read it as one")
        row = next(x for x in doc["lanes"] if x["id"] == "blackout-outage-triage")
        # The split is triage's reading when logged, never a scored outcome: "unconfirmed" is a scored outcome's word
        # on the scoring page, so triage's single-source dip is called a dip here.
        self.assertEqual(row["text"], "This lane: 2 flagged since 2026-11-25; triage read 1 as open, 1 as a single-source "
                                      "dip (readings when logged, not outcomes); none scored yet.")
        self.assertNotIn("unconfirmed", row["text"])
        self.assertEqual(row["by_reading"], {"open": 1, "unconfirmed": 1}, "the data keeps triage's own reading names")
        self.assertTrue(row["replay"]["text"].startswith("Replay, not live"))
        self.assertIn("natural on 15.9% of shutdowns and 5.8% of ordinary days", row["replay"]["text"])
        self.assertEqual(row["replay"]["attribution"], "Access Now, #KeepItOn STOP")
        for x in doc["lanes"]:
            self.assertFalse(x["rate_shown"])
            self.assertEqual(x["scored"], 0)
            for word in ("likely", "warning sign", "probability"):
                self.assertNotIn(word, x["text"].lower())
        quiet = next(x for x in doc["lanes"] if x["id"] == "elements")
        self.assertIsNone(quiet["flagged"])
        self.assertEqual(quiet["text"], L.NO_CHECK)

    def test_the_public_record_carries_no_queue_references(self):
        """lanes.json is served on the site and through MCP: it describes the lanes for readers, with no issue numbers
        from the private work queue."""
        doc = L.records(L.load_config(), [], [], replay_mod.load(), at("2026-11-25T04:30:00"))
        text = json.dumps(doc, ensure_ascii=False)
        self.assertNotRegex(text, r"ideas#|#\d{2,}")
        self.assertNotRegex(L.CONFIG.read_text(encoding="utf-8"), r"ideas#")

    def test_the_direction_lane_reads_the_direction_log(self):
        calls = [{"type": "call", "rule": "1", "week": "2026-11-23", "word": w, "scored": s}
                 for w, s in (("steady", True), ("rising", True), ("steady", True), ("steady", False))]
        doc = L.records(self.cfg, [], calls, None, at("2026-11-25T04:30:00"))
        row = next(x for x in doc["lanes"] if x["id"] == "cop-direction")
        self.assertEqual(row["flagged"], 3, "an unscored place is not counted")
        self.assertEqual(row["text"], "This lane: 3 place calls locked over 1 week since 2026-11-23 (2 steady, 1 rising); "
                                      "none scored yet.")

    def test_every_program_has_a_lane_and_prefix_lanes_come_first(self):
        progs = {p["id"] for p in json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))["programs"]}
        for p in progs:
            self.assertIsNotNone(L.lane_for(self.cfg, p, f"{p}:x"), p)
        self.assertEqual(L.lane_for(self.cfg, "blackout", "blackout:outage:1")["id"], "blackout-outage-triage")
        self.assertEqual(L.lane_for(self.cfg, "blackout", "blackout:ooni:1")["id"], "blackout-other")
        self.assertEqual(len({x["id"] for x in self.cfg["lanes"]}), len(self.cfg["lanes"]))

    def test_vis_is_scored_outside_the_lane_records(self):
        """Verdict scores Vis's forecasts against METARs in its own record. Vis's lane links it, quotes none of its
        figures, and its counts stay those of a lane with nothing logged."""
        doc = L.records(self.cfg, [], [], None, at("2026-11-25T04:30:00"))
        vis = next(x for x in doc["lanes"] if x["id"] == "vis")
        self.assertTrue(vis["text"].startswith("Scored outside the lane records: Verdict scores this lane's stored "
                                               "forecasts against METAR observations"), vis["text"])
        self.assertNotIn("No outside check", vis["text"])
        self.assertNotRegex(vis["text"], r"\d", "no figure from Verdict's record is restated")
        self.assertEqual(vis["scored_elsewhere"]["record"], "https://verdict.theintelligence.company/#vis-h")
        self.assertEqual(vis["scored_elsewhere"]["data"], "https://verdict.theintelligence.company/data/vis.json")
        self.assertEqual((vis["flagged"], vis["scored"], vis["rate_shown"]), (None, 0, False))
        others = [x for x in doc["lanes"] if x["id"] != "vis"]
        self.assertTrue(all(x["scored_elsewhere"] is None for x in others), "only Vis's lane is scored elsewhere")
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        self.assertIn(vis["scored_elsewhere"]["record"], html)
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertEqual(js.count('h("a", { href: lane.scored_elsewhere.record }, lane.scored_elsewhere.label)'), 2,
                         "the card and the lane line both link the record")

    def test_the_scoring_page_states_the_config(self):
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        tri = next(x for x in self.cfg["lanes"] if x["id"] == "blackout-outage-triage")
        self.assertIn(f"{tri['horizon_days']} days for an outage", html)
        self.assertIn(f"No rate or skill figure before {self.cfg['min_scored_for_rate']} independently scored", html)

    def test_it_publishes_only_to_site_data(self):
        with self.assertRaises(SystemExit):
            L.publish_folder(ROOT / "site")
        self.assertEqual(L.publish_folder(ROOT / "site" / "data"), (ROOT / "site" / "data").resolve())


if __name__ == "__main__":
    unittest.main()
