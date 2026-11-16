"""The STOP replay and the history datasets it reads (tools/stop_replay.py, history_elements.py,
history_watchfloor.py). Synthetic fixtures only: STOP's rows have no data licence."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import history_elements as hel  # noqa: E402
import history_watchfloor as hwf  # noqa: E402
import stop_replay as sr  # noqa: E402
import triage as tri  # noqa: E402

HEADER = "start_date_type,start_date,country,geo_scope,area_name,shutdown_type,end_date,actual_cause\n"


def stop_csv(*rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    f.write(HEADER + "".join(rows))
    f.close()
    return f.name


def hazard_row(cc, at, code="EQ", frm=None, to=None, item="elements:usgs:x1"):
    return {"place": cc, "at": at, "precision": "minute", "source_id": "usgs", "source_ref": "test",
            "retrieved_at": at, "quality": [], "item_id": item, "tier": "anomaly", "hazard_code": code,
            "alert_level": "red", "from": frm, "to": to, "countries": [cc], "geometry": None, "name": "test"}


def wf_index(rows, days):
    wf = {(r["place"], r["at"]): r for r in rows}
    wf["_days"] = set(days)
    return wf


def wf_row(cc, day, unusual, quality=()):
    return {"place": cc, "at": day, "quality": list(quality), "unusual": unusual}


class LoadStop(unittest.TestCase):
    def test_aliases_years_and_several_countries_in_one_row(self):
        p = stop_csv("Actual,3/4/2025,Iran (Islamic Republic of),x,Nationwide,Shutdown,3/5/2025,Protests\n",
                     "Estimated,5/1/2025,Cameroon; Central African Republic,x,Regional,Shutdown,,Conflict\n",
                     "Actual,5/1/2024,India,x,Regional,Shutdown,,Conflict\n")
        s = sr.load_stop(p, 2025)
        self.assertEqual([(x["country"], str(x["start"])) for x in s],
                         [("IR", "2025-03-04"), ("CM", "2025-05-01"), ("CF", "2025-05-01")])
        self.assertTrue(s[1]["estimated"])
        self.assertIsNone(s[1]["end"])
        self.assertEqual(s[0]["cause"], "Protests")

    def test_an_unknown_country_name_stops_the_run(self):
        with self.assertRaises(SystemExit):
            sr.load_stop(stop_csv("Actual,3/4/2025,Atlantis,x,Nationwide,Shutdown,,Other\n"), 2025)


class ReadDay(unittest.TestCase):
    def setUp(self):
        self.items = hel.to_items([hazard_row("MM", "2025-03-28T06:20:00Z")])
        self.days = ["2025-03-27", "2025-03-28", "2025-03-29"]

    def test_a_hazard_that_began_in_the_window_reads_natural(self):
        wf = wf_index([wf_row("MM", "2025-03-28", True)], self.days)
        self.assertEqual(sr.read_day("MM", date(2025, 3, 29), self.items, wf), "natural")

    def test_attention_the_day_before_reads_tension_context(self):
        wf = wf_index([wf_row("IR", "2025-03-28", True)], self.days)
        self.assertEqual(sr.read_day("IR", date(2025, 3, 29), [], wf), tri.TENSION)

    def test_a_country_absent_from_a_complete_day_reads_open(self):
        self.assertEqual(sr.read_day("IR", date(2025, 3, 29), [], wf_index([], self.days)), "open")

    def test_a_missing_or_partial_day_is_incomplete(self):
        self.assertEqual(sr.read_day("IR", date(2025, 3, 29), [], wf_index([], [])), "incomplete")
        wf = wf_index([wf_row("IR", "2025-03-28", True, ["partial-day"])], [])
        self.assertEqual(sr.read_day("IR", date(2025, 3, 29), [], wf), "incomplete")

    def test_a_day_with_no_scored_country_is_incomplete_not_open(self):
        doc = {"day": "2025-07-05", "partial": False, "lists": {},
               "countries": {"IR": {"urls": 90, "violent": 4}, "MM": {"urls": 40, "violent": 2}}}
        rows = hwf.day_rows(doc, {"IR": "IR", "BM": "MM", "MM": "MM"}, "2026-11-16T00:00:00Z")
        self.assertTrue(hwf.mark_unscored(rows))
        self.assertTrue(all("unscored-day" in r["quality"] for r in rows))
        wf = wf_index(rows, [])   # main() leaves unscored days out of _days
        self.assertEqual(sr.read_day("IR", date(2025, 7, 6), [], wf), "incomplete")
        self.assertEqual(sr.read_day("KE", date(2025, 7, 6), [], wf), "incomplete", "absent from an unscored day")
        scored = hwf.day_rows({**doc, "countries": {"IR": {"urls": 90, "urls_score": {"z": 0.4}}}}, {"IR": "IR"}, "x")
        self.assertFalse(hwf.mark_unscored(scored))

    def test_same_day_attention_counts_only_in_the_sensitivity_variant(self):
        wf = wf_index([wf_row("IR", "2025-03-29", True)], self.days)
        self.assertEqual(sr.read_day("IR", date(2025, 3, 29), [], wf), "open")
        self.assertEqual(sr.read_day("IR", date(2025, 3, 29), [], wf, same_day=True), tri.TENSION)

    def test_an_episodic_flood_counts_while_active(self):
        items = hel.to_items([hazard_row("PK", "2025-06-20T00:00:00Z", "FL", "2025-06-20T00:00:00Z",
                                         "2025-07-10T00:00:00Z", "elements:gdacs:FL-9")])
        wf = wf_index([], ["2025-07-04"])
        self.assertEqual(sr.read_day("PK", date(2025, 7, 5), items, wf), "natural")


class ThreeLayers(unittest.TestCase):
    def dataset(self, rows):
        import hashlib
        d = Path(tempfile.mkdtemp()) / "cordon-test"
        d.mkdir()
        body = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
        (d / "rows.jsonl").write_bytes(body)
        (d / "manifest.json").write_text(json.dumps({"dataset_id": "cordon-test", "files": [
            {"path_or_url": "history/cordon/cordon-test/rows.jsonl", "sha256": hashlib.sha256(body).hexdigest()}]}),
            encoding="utf-8")
        return str(d)

    def alert(self, at, title, cc="IN", link="https://in.usembassy.gov/x/"):
        return {"kind": "alert", "item_id": "cordon:alert:%s:%s" % (cc, abs(hash(link)) % 10**12), "place": cc,
                "countries": [cc], "at": at, "first_seen": at, "title": title, "alert_type_raw": "alert"}

    def test_a_security_alert_counts_and_a_weather_alert_does_not(self):
        c = sr.Cordon([self.dataset([self.alert("2025-05-07T03:30:00Z", "Security Alert: protests", link="a"),
                                     self.alert("2025-05-20T03:30:00Z", "Weather Alert: cyclone", link="b")])])
        wf = wf_index([], ["2025-05-06", "2025-05-07", "2025-05-20", "2025-05-21"])
        self.assertEqual(c.places, {"IN"})
        self.assertEqual(sr.read_day("IN", date(2025, 5, 8), [], wf, cordon=c), tri.TENSION)
        self.assertEqual(sr.read_day("IN", date(2025, 5, 21), [], wf, cordon=c), "open")
        self.assertEqual(sr.read_day("IN", date(2025, 5, 8), [], wf), "open", "two layers: the alert is not read")

    def test_fcdo_statements_become_levels_in_triage_order(self):
        row = {"kind": "fcdo", "item_id": "cordon:fcdo:myanmar", "place": "MM", "countries": ["MM"],
               "at": "2025-04-08T10:00:00Z", "alert_status": [tri.FCDO_STATEMENTS[0]], "alert_status_changed": True,
               "from_alert_status": [], "note": "x", "name": "Myanmar"}
        c = sr.Cordon([self.dataset([row])])
        self.assertEqual(c.levels["cordon:fcdo:myanmar"][0][1][0], 1)
        self.assertTrue(c.counted("MM", tri.utc("2025-04-09T06:00:00Z")))


class Replay(unittest.TestCase):
    def test_controls_leave_out_days_near_a_shutdown_and_match_by_country(self):
        days = [str(date(2025, 1, 1).fromordinal(date(2025, 1, 1).toordinal() + i)) for i in range(365)]
        wf = wf_index([wf_row("IR", "2025-03-03", True), wf_row("IR", "2025-08-01", True)], days)
        s = sr.load_stop(stop_csv("Actual,3/4/2025,Iran (Islamic Republic of),x,Nationwide,Shutdown,3/5/2025,"
                                  "Protests\n"), 2025)
        res = sr.replay(s, [], wf, 2025)
        self.assertEqual(res["shutdowns_read"], {tri.TENSION: 1})
        ir = res["by_country"][0]
        self.assertEqual(ir["control_days"], 365 - 16, "4 Mar to 5 Mar plus 7 days each side")
        self.assertEqual(ir["control_read"][tri.TENSION], 1, "2 Aug reads 1 Aug's attention")
        self.assertAlmostEqual(res["matched"]["control_shares"][tri.TENSION], round(1 / 349, 4))
        self.assertEqual(ir["name"], "Iran")
        self.assertEqual(res["by_scope"]["nationwide"]["shutdowns"], 1)
        self.assertAlmostEqual(res["by_scope"]["nationwide"]["matched"]["control_shares"][tri.TENSION],
                               round(1 / 349, 4))

    def test_a_country_with_no_control_day_is_left_out_of_the_matched_comparison(self):
        days = [str(date(2025, 1, 1).fromordinal(date(2025, 1, 1).toordinal() + i)) for i in range(365)]
        rows = "".join(f"Actual,{m}/1/2025,Myanmar,x,Regional,Shutdown,,Conflict\n" for m in range(1, 13))
        rows += "Actual,3/4/2025,Iran (Islamic Republic of),x,Nationwide,Shutdown,3/5/2025,Protests\n"
        res = sr.replay(sr.load_stop(stop_csv(rows), 2025), [], wf_index([], days), 2025)
        self.assertEqual((res["shutdowns"], res["matched"]["shutdowns"], res["matched"]["left_out"]), (13, 1, ["MM"]))
        self.assertAlmostEqual(sum(res["matched"]["control_shares"].values()), 1.0, places=3)


class HistoryDatasets(unittest.TestCase):
    def test_watchfloor_rows_merge_fips_codes_that_share_an_iso_code(self):
        doc = {"day": "2025-05-01", "partial": False,
               "lists": {"urls": {"rising": ["GZ"]}, "violent": {"rising": []}},
               "countries": {"WE": {"urls": 50, "violent": 3, "urls_score": {"z": 1.0, "ratio": 1.1}},
                             "GZ": {"urls": 70, "violent": 9, "urls_score": {"z": 3.0, "ratio": 2.0}},
                             "OS": {"urls": 5, "violent": 0}}}
        rows = hwf.day_rows(doc, {"WE": "PS", "GZ": "PS"}, "2026-11-16T00:00:00Z")
        self.assertEqual(len(rows), 1, "OS has no ISO code and is dropped")
        r = rows[0]
        self.assertEqual((r["place"], r["urls"], r["urls_z"], r["unusual"]), ("PS", 120, 3.0, True))
        self.assertEqual(r["fips"], ["GZ", "WE"])
        self.assertIn("merged-fips", r["quality"])
        for k in ("place", "at", "precision", "source_id", "source_ref", "retrieved_at", "quality"):
            self.assertIn(k, r, "every row carries the contract's fields")

    def test_a_quake_is_placed_inside_its_country_or_near_one(self):
        self.assertEqual(hel.places_for_point(96.0, 22.0), [("MM", 0)])
        near = hel.places_for_point(141.5, 37.0)   # off Japan's Pacific coast
        self.assertTrue(near and near[0][0] == "JP" and near[0][1] > 0)

    def test_manifests_carry_the_contract(self):
        m = hel.manifest([], date(2025, 1, 1), date(2025, 1, 2), "2026-11-16T00:00:00Z", "x")
        w = hwf.manifest([], date(2025, 1, 1), date(2025, 1, 2), [], ["2025-01-02"], "2026-11-16T00:00:00Z", "x", "r")
        for man in (m, w):
            self.assertEqual(man["contract"], "cop-layer-history/1")
            for k in ("dataset_id", "sources", "method", "coverage", "gaps", "biases", "row_schema",
                      "redistribution", "uses", "not_for"):
                self.assertIn(k, man)
            self.assertIn("differences", man["method"])
        self.assertTrue(any("partial" in g["reason"] for g in w["gaps"]))

    def test_the_replay_publishes_counts_and_never_names_cause(self):
        self.assertTrue(any("not a test of whether triage finds shutdowns" in c for c in sr.CAVEATS))
        self.assertTrue(any("Cordon" in c for c in sr.CAVEATS))


if __name__ == "__main__":
    unittest.main()
