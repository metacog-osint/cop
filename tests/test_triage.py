"""Outage triage: python tests/test_triage.py"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import snapshot as snap  # noqa: E402
import triage as tri  # noqa: E402

AT = "2026-11-12T00:25:00Z"


def item(program, id_, countries=(), tier="routine", data_as_of=AT, geometry=None, **props):
    p = {"id": id_, "title": id_, "tier": tier, "data_as_of": data_as_of, "countries": list(countries),
         "cop_program": program, "cop_stale": False}
    p.update(props)
    return {"type": "Feature", "geometry": geometry, "properties": p}


def outage(cc="TN", indicator="BO-IND-1", at=AT):
    return item("blackout", f"blackout:outage:{cc.lower()}:{at[:13]}", [cc], "anomaly", at, indicator_id=indicator)


def snapshot(*features, states=None):
    states = {"elements": "fresh", "cordon": "fresh", "watchfloor": "fresh", "blackout": "fresh"} | (states or {})
    return {"generated_at": "2026-11-13T00:21:12Z", "features": list(features),
            "cop_programs": {k: {"state": v} for k, v in states.items()}}


def one(doc):
    assert len(doc["records"]) == 1, doc["records"]
    return doc["records"][0]


class Triage(unittest.TestCase):
    def test_nothing_under_an_outage_is_open_not_a_flag(self):
        r = one(tri.triage(snapshot(outage())))
        self.assertEqual(r["reading"], "open")
        self.assertFalse(any(r["layers"][k]["found"] for k in tri.LAYERS))
        self.assertEqual(r["no_current_data"], [])

    def test_a_significant_hazard_in_the_window_reads_natural(self):
        haz = item("elements", "elements:gdacs:TC-1", ["TN"], "anomaly", "2026-11-11T06:00:00Z",
                   measures={"from": "2026-11-10T00:00:00Z", "to": "2026-11-11T18:00:00Z"})
        r = one(tri.triage(snapshot(outage(), haz)))
        self.assertEqual(r["reading"], "natural")
        self.assertEqual(r["layers"]["elements"]["significant"][0]["id"], "elements:gdacs:TC-1")

    def test_a_hazard_outside_the_window_or_country_does_not_count(self):
        old = item("elements", "elements:gdacs:TC-old", ["TN"], "anomaly", "2026-11-03T06:00:00Z",
                   measures={"from": "2026-11-01T00:00:00Z", "to": "2026-11-03T06:00:00Z"})
        elsewhere = item("elements", "elements:gdacs:TC-dz", ["DZ"], "anomaly", AT)
        late = item("elements", "elements:usgs:late", ["TN"], "anomaly", "2026-11-12T12:00:00Z")
        r = one(tri.triage(snapshot(outage(), old, elsewhere, late)))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(r["layers"]["elements"]["significant"], [])

    def test_an_offshore_earthquake_counts_only_when_near(self):
        near = item("elements", "elements:usgs:near", [], "anomaly", "2026-11-11T20:00:00Z",
                    geometry={"type": "Point", "coordinates": [11.5, 37.6]})
        far = item("elements", "elements:usgs:far", [], "anomaly", "2026-11-11T20:00:00Z",
                   geometry={"type": "Point", "coordinates": [150.0, -3.0]})
        r = one(tri.triage(snapshot(outage(), near, far)))
        ids = [h["id"] for h in r["layers"]["elements"]["significant"]]
        self.assertEqual(ids, ["elements:usgs:near"])
        self.assertLessEqual(r["layers"]["elements"]["significant"][0]["km_from_country"], tri.HAZARD_NEAR_KM)

    def test_standing_background_never_makes_a_natural_reading(self):
        fires = item("elements", "elements:firms:TN", ["TN"], "anomaly", "2026-11-11T13:00:00Z")
        r = one(tri.triage(snapshot(outage(), fires)))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(len(r["layers"]["elements"]["background"]), 1)

    def test_recent_notice_or_raised_attention_without_a_hazard_reads_deliberate(self):
        alert = item("cordon", "cordon:alert:TN:abc", ["TN"], "routine", "2026-11-09T00:00:00Z")
        r = one(tri.triage(snapshot(outage(), alert)))
        self.assertEqual(r["reading"], "deliberate")
        wf = item("watchfloor", "watchfloor:2026-11-11:country:TS:attention", ["TN"], "anomaly",
                  "2026-11-11T23:59:59Z")
        r = one(tri.triage(snapshot(outage(), wf)))
        self.assertEqual(r["reading"], "deliberate")
        self.assertFalse(r["layers"]["watchfloor"]["data_reaches_outage"], "Watchfloor's day ended before the outage")

    def test_a_standing_advisory_is_listed_but_is_not_a_signal(self):
        adv = item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-05-01T00:00:00Z", measures={"level": 2})
        r = one(tri.triage(snapshot(outage(), adv)))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(r["layers"]["cordon"]["standing"][0]["level"], 2)

    def test_a_hazard_outranks_notices(self):
        haz = item("elements", "elements:gdacs:FL-1", ["TN"], "anomaly", "2026-11-11T06:00:00Z")
        alert = item("cordon", "cordon:alert:TN:abc", ["TN"], "routine", "2026-11-09T00:00:00Z")
        self.assertEqual(one(tri.triage(snapshot(outage(), haz, alert)))["reading"], "natural")

    def test_a_layer_without_current_data_is_incomplete_never_quiet(self):
        for state in ("stale", "missing"):
            r = one(tri.triage(snapshot(outage(), states={"elements": state})))
            self.assertEqual(r["reading"], "incomplete")
            self.assertEqual(r["no_current_data"], ["elements"])

    def test_a_single_source_dip_gets_context_but_no_reading(self):
        alert = item("cordon", "cordon:alert:SC:abc", ["SC"], "routine", "2026-11-09T00:00:00Z")
        r = one(tri.triage(snapshot(outage("SC", "BO-IND-4"), alert)))
        self.assertEqual(r["reading"], "unconfirmed")
        self.assertEqual(len(r["layers"]["cordon"]["recent"]), 1)

    def test_no_probability_and_nothing_registered(self):
        r = one(tri.triage(snapshot(outage())))
        v = r["verdict_draft"]
        self.assertIsNone(v["probability"])
        self.assertIsNone(v["approved_by"])
        self.assertFalse(v["registered"])
        self.assertEqual(v["window_ends"], "2026-11-15T00:25:00Z")
        self.assertIsNone(v["caution"])
        self.assertNotIn("probability", json.dumps({k: x for k, x in r.items() if k != "verdict_draft"}))

    def test_ooni_already_over_the_threshold_is_called_out(self):
        ooni = item("blackout", "blackout:ooni:ir:2026-11-12", ["IR"], "anomaly", indicator_id="BO-IND-3")
        r = one(tri.triage(snapshot(outage("IR"), ooni)))
        self.assertIn("already", r["verdict_draft"]["caution"])
        self.assertEqual(r["ooni_now"]["id"], "blackout:ooni:ir:2026-11-12")

    def test_stale_outages_and_other_blackout_items_are_not_triaged(self):
        stale = outage("TN")
        stale["properties"]["cop_stale"] = True
        ooni = item("blackout", "blackout:ooni:ru:2026-11-12", ["RU"], "anomaly", indicator_id="BO-IND-3")
        provider = item("blackout", "blackout:provider-status:aws:x", [], "anomaly")
        self.assertEqual(tri.triage(snapshot(stale, ooni, provider))["records"], [])

    def test_it_refuses_to_write_inside_site(self):
        with self.assertRaises(SystemExit):
            tri.safe_out(ROOT / "site" / "data" / "triage")
        with self.assertRaises(SystemExit):
            tri.safe_out(ROOT / "site")
        self.assertEqual(tri.safe_out(ROOT / "out" / "triage"), (ROOT / "out" / "triage").resolve())

    def test_the_build_and_the_page_do_not_know_about_it(self):
        for rel in ("tools/snapshot.py", "tools/agent_index.py", ".github/workflows/build.yml", "site/cop.js"):
            self.assertNotIn("triage", (ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_it_runs_on_a_snapshot_built_from_the_fixtures(self):
        ids = [p["id"] for p in snap.CONFIG["programs"]]
        feeds, reports = {}, {}
        for pid in ids:
            feeds[pid], reports[pid] = snap.read_local(ROOT / "tests" / "fixtures" / f"{pid}.geojson")
        today, *_ = snap.build(feeds, reports, None, datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc))
        doc = tri.triage(today)
        self.assertEqual({r["country"] for r in doc["records"]}, {"TN", "SC"})
        by = {r["country"]: r for r in doc["records"]}
        self.assertEqual(by["SC"]["reading"], "unconfirmed")
        self.assertIn(by["TN"]["reading"], tri.READINGS)
        for r in doc["records"]:
            self.assertEqual(set(r["layers"]), set(tri.LAYERS))


if __name__ == "__main__":
    unittest.main()
