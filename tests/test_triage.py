"""Outage triage: python tests/test_triage.py"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import snapshot as snap  # noqa: E402
import triage as tri  # noqa: E402
import triage_rates as rates  # noqa: E402

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
        alert = item("cordon", "cordon:alert:TN:abc", ["TN"], "routine", "2026-11-11T00:00:00Z")
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
        alert = item("cordon", "cordon:alert:SC:abc", ["SC"], "routine", "2026-11-11T00:00:00Z")
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

    def test_background_counts_each_signal_once_per_country_day(self):
        day1 = snapshot(
            item("elements", "elements:gdacs:TC-1", ["TN"], "anomaly", "2026-11-12T20:00:00Z"),
            item("cordon", "cordon:alert:TN:a", ["TN"], "routine", "2026-11-12T00:00:00Z"),   # hazard wins in TN
            item("cordon", "cordon:alert:DZ:a", ["DZ"], "routine", "2026-11-12T00:00:00Z"),
            item("cordon", "cordon:advisory:FR", ["FR"], "routine", "2026-01-01T00:00:00Z"),  # standing: no signal
            item("watchfloor", "watchfloor:2026-11-12:country:AG:attention", ["DZ"], "anomaly",
                 "2026-11-12T23:59:59Z"),
            item("watchfloor", "watchfloor:2026-11-12:zone:x", ["LY", "EG"], "anomaly", "2026-11-12T23:59:59Z"))
        doc = rates.rates([day1])
        n = len(rates.all_countries())
        d = doc["background"]["days"][0]
        self.assertEqual((d["hazard"], d["recent_notice"], d["raised_attention"]), (1, 2, 3))
        self.assertEqual(d["unusual_attention"], 0, "raised on the only archived day is this country's normal")
        self.assertEqual(d["would_read"], {"natural": 1, "deliberate": 1, "open": n - 2})
        self.assertEqual(doc["background"]["country_days"], n)
        self.assertEqual(doc["background"]["would_read_country_days"], n)
        self.assertEqual(d["recent_notice_kinds"], {"alert": 2})
        self.assertEqual(len(doc["cautions"]), 2, "one day and no outages: both cautions")
        by = doc["background"]["by_country"]
        self.assertEqual(by["TN"], {"deliberate": 0.0, "natural": 1.0, "open": 0.0})
        self.assertEqual(by["DZ"]["deliberate"], 1.0)
        self.assertNotIn("FR", by, "a country that only ever read open is not listed")
        self.assertNotIn("_natural", d)

    def test_a_day_with_a_layer_down_is_left_out_of_the_rates(self):
        good = snapshot()
        bad = snapshot(states={"cordon": "stale"})
        bad["generated_at"] = "2026-11-14T00:21:12Z"
        doc = rates.rates([good, bad])
        self.assertEqual(len(doc["background"]["days"]), 1)
        self.assertEqual(doc["archive"]["days_left_out_layer_down"], ["2026-11-14"])

    def test_replay_counts_an_outage_once_with_its_newest_reading(self):
        first = snapshot(outage())
        second = snapshot(outage(), item("cordon", "cordon:alert:TN:a", ["TN"], "routine", "2026-11-11T00:00:00Z"))
        second["generated_at"] = "2026-11-14T00:21:12Z"
        doc = rates.rates([second, first])
        self.assertEqual(doc["replay"]["outages"], 1)
        self.assertEqual(doc["replay"]["by_reading"], {"deliberate": 1})

    def test_every_reading_with_a_rate_carries_it_and_its_cautions(self):
        base = rates.rates([snapshot()])
        doc = tri.attach_rates(tri.triage(snapshot(outage(), outage("SC", "BO-IND-4"))), base)
        by = {r["country"]: r for r in doc["records"]}
        self.assertEqual(by["TN"]["base_rate"]["share_of_country_days"], 1.0)
        self.assertEqual(by["TN"]["base_rate"]["share_of_this_countrys_days"], 1.0)
        self.assertTrue(by["TN"]["base_rate"]["cautions"])
        self.assertIsNone(by["SC"]["base_rate"], "no reading was given, so there is no rate to quote")
        self.assertIsNone(tri.attach_rates(tri.triage(snapshot(outage())), None)["records"][0]["base_rate"])

    def test_a_hazard_that_began_before_the_window_is_standing_not_natural(self):
        volcano = item("elements", "elements:usgs-volcano:kilauea", ["TN"], "anomaly", "2026-11-11T12:00:00Z",
                       measures={"from": "2026-10-01T00:00:00Z", "to": "2026-11-20T00:00:00Z"})
        r = one(tri.triage(snapshot(outage(), volcano)))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(r["layers"]["elements"]["significant"], [])
        self.assertEqual(r["layers"]["elements"]["standing"][0]["id"], "elements:usgs-volcano:kilauea")
        self.assertTrue(any("standing" in c for c in r["caveats"]))

    def test_a_notice_older_than_two_days_or_dated_after_the_outage_does_not_count(self):
        old = item("cordon", "cordon:alert:TN:old", ["TN"], "routine", "2026-11-07T00:00:00Z")
        later = item("cordon", "cordon:alert:TN:later", ["TN"], "routine", "2026-11-12T09:00:00Z")
        r = one(tri.triage(snapshot(outage(), old, later)))
        self.assertEqual(r["reading"], "open")
        c = r["layers"]["cordon"]
        self.assertEqual(([x["id"] for x in c["standing"]], [x["id"] for x in c["after"]], c["recent"]),
                         (["cordon:alert:TN:old"], ["cordon:alert:TN:later"], []))

    def test_an_advisory_counts_when_raised_and_not_when_lowered_or_reissued(self):
        adv = item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-11-11T00:00:00Z", measures={"level": 3})
        for before, direction, reading, bucket in ((2, "raised", "deliberate", "recent"),
                                                   (4, "lowered", "open", "recent_not_raised"),
                                                   (3, "unchanged", "open", "recent_not_raised")):
            hist = {"advisory_levels": {"cordon:advisory:TN": [["2026-05-01T00:00:00Z", before],
                                                               ["2026-11-11T00:00:00Z", 3]]},
                    "attention_share": {}}
            r = one(tri.triage(snapshot(outage(), adv), hist))
            self.assertEqual(r["reading"], reading, direction)
            self.assertEqual(r["layers"]["cordon"][bucket][0]["direction"], direction)

    def test_an_advisory_with_no_earlier_level_counts_and_says_so(self):
        adv = item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-11-11T00:00:00Z", measures={"level": 3})
        hist = {"advisory_levels": {"cordon:advisory:TN": [["2026-11-11T00:00:00Z", 3]]}, "attention_share": {}}
        r = one(tri.triage(snapshot(outage(), adv), hist))
        self.assertEqual(r["reading"], "deliberate")
        self.assertEqual(r["layers"]["cordon"]["recent"][0]["direction"], "unknown")
        self.assertTrue(any("no earlier level" in c for c in r["caveats"]))

    def test_raised_attention_counts_only_where_it_is_unusual_for_the_country(self):
        wf = item("watchfloor", "watchfloor:2026-11-11:country:TS:attention", ["TN"], "anomaly",
                  "2026-11-11T23:59:59Z")
        usual = one(tri.triage(snapshot(outage(), wf), {"advisory_levels": {}, "attention_share": {"TN": 0.9}}))
        self.assertEqual(usual["reading"], "open")
        self.assertEqual((len(usual["layers"]["watchfloor"]["raised"]), usual["layers"]["watchfloor"]["unusual"]),
                         (1, False))
        rare = one(tri.triage(snapshot(outage(), wf), {"advisory_levels": {}, "attention_share": {"TN": 0.1}}))
        self.assertEqual(rare["reading"], "deliberate")
        never = one(tri.triage(snapshot(outage(), wf), {"advisory_levels": {}, "attention_share": {}}))
        self.assertEqual(never["reading"], "deliberate", "a country never raised in the archive: raised is unusual")

    def test_without_an_archive_baseline_the_record_says_so(self):
        r = one(tri.triage(snapshot(outage())))
        self.assertTrue(any("No archive baseline" in c for c in r["caveats"]))
        r = one(tri.triage(snapshot(outage()), {"advisory_levels": {}, "attention_share": {}}))
        self.assertFalse(any("No archive baseline" in c for c in r["caveats"]))

    def test_ooni_over_the_threshold_gets_a_rise_question(self):
        ooni = item("blackout", "blackout:ooni:ir:2026-11-12", ["IR"], "anomaly", indicator_id="BO-IND-3",
                    title="Iran: 37.0% OONI censorship anomaly rate")
        v = one(tri.triage(snapshot(outage("IR"), ooni)))["verdict_draft"]
        self.assertIn("47.0%", v["rise_form"]["question"])
        self.assertIn("37.0%", v["rise_form"]["rule_draft"])
        self.assertIsNone(v["probability"])
        self.assertIsNone(one(tri.triage(snapshot(outage())))["verdict_draft"]["rise_form"])

    def test_history_keeps_each_advisory_level_and_each_countrys_attention_share(self):
        a = snapshot(item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-05-01T00:00:00Z",
                          measures={"level": 2}),
                     item("watchfloor", "watchfloor:2026-11-12:zone:x", ["LY"], "anomaly", "2026-11-12T23:59:59Z"))
        b = snapshot(item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-11-13T00:00:00Z",
                          measures={"level": 3}),
                     item("watchfloor", "watchfloor:2026-11-13:summary", [], "routine", "2026-11-13T23:59:59Z"))
        b["generated_at"] = "2026-11-14T00:21:12Z"
        h = rates.history([a, b])
        self.assertEqual(h["advisory_levels"]["cordon:advisory:TN"],
                         [["2026-05-01T00:00:00Z", 2], ["2026-11-13T00:00:00Z", 3]])
        self.assertEqual((h["attention_days"], h["attention_share"]), (2, {"LY": 0.5}))

    def test_days_before_cordons_notices_changed_do_not_count_toward_readings(self):
        early = snapshot()
        early["generated_at"] = "2026-11-05T00:21:12Z"
        doc = rates.rates([early, snapshot()])
        n = len(rates.all_countries())
        self.assertEqual(doc["background"]["country_days"], 2 * n)
        self.assertEqual(doc["background"]["would_read_country_days"], n)
        self.assertEqual(doc["archive"]["days_counted_for_readings"], 1)

    def test_the_build_runs_it_after_the_snapshot_and_before_the_gate(self):
        build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        order = [build.index(s) for s in ("python tools/snapshot.py", "python tools/triage_rates.py",
                                          "python tools/triage.py", "python tools/gate.py --check",
                                          "pages deploy")]
        self.assertEqual(order, sorted(order))
        self.assertIn("--publish site/data", build)
        # The archive for base rates is downloaded apart from out/archive, which the build uploads from.
        self.assertIn("out/rates-archive", build)
        self.assertNotIn("--dir out/archive", build)
        # The snapshot and the agent index stay ignorant of the join: it reads them, they never read it.
        for rel in ("tools/snapshot.py", "tools/agent_index.py"):
            self.assertNotIn("triage", (ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_the_public_document_has_every_layers_answer_and_the_question_as_a_draft(self):
        haz = item("elements", "elements:gdacs:TC-1", ["TN"], "anomaly", "2026-11-11T06:00:00Z")
        doc = tri.attach_rates(tri.triage(snapshot(outage(), outage("SC", "BO-IND-4"), haz)), None)
        pub = tri.public(doc)
        self.assertEqual(len(pub["records"]), 2)
        text = json.dumps(pub)
        self.assertNotIn("verdict_draft", text)
        self.assertNotIn("approved_by", text)
        self.assertNotIn('"probability"', text, "no probability field, even an empty one")
        for r in pub["records"]:
            q = r["question_draft"]
            self.assertEqual(q["status"], "draft, not registered")
            self.assertIn("not a registered Verdict forecast", q["note"])
            self.assertEqual(set(q), {"status", "note", "question", "window_ends", "rule_draft", "caution",
                                      "rise_form"})
        self.assertIn("not a warning, not a forecast and not a finding of cause", pub["not_a_warning"])
        self.assertEqual(pub["readings"], tri.READINGS)
        for r in pub["records"]:
            self.assertEqual(set(r["layers"]), set(tri.LAYERS))
            for layer in r["layers"].values():
                self.assertTrue(layer["name"] and layer["showed"])
        by = {r["country"]: r for r in pub["records"]}
        self.assertIn("1 significant hazard began", by["TN"]["layers"]["elements"]["showed"])
        self.assertTrue(by["SC"]["layers"]["elements"]["showed"].startswith("Nothing found"))
        self.assertEqual(gate.review(tri.JOIN, pub)["may_publish"], True)

    def test_the_public_question_keeps_the_rise_form_and_its_caution(self):
        ooni = item("blackout", "blackout:ooni:ir:2026-11-12", ["IR"], "anomaly", indicator_id="BO-IND-3",
                    title="Iran: 37.0% OONI censorship anomaly rate")
        pub = tri.public(tri.attach_rates(tri.triage(snapshot(outage("IR"), ooni)), None))
        q = pub["records"][0]["question_draft"]
        self.assertIn("already", q["caution"])
        self.assertIn("47.0%", q["rise_form"]["question"])
        self.assertEqual(set(q["rise_form"]), {"question", "rule_draft", "notes"})

    def test_nothing_found_and_no_current_data_are_different_answers(self):
        quiet = one(tri.triage(snapshot(outage())))
        dead = one(tri.triage(snapshot(outage(), states={"cordon": "stale"})))
        for name in tri.LAYERS:
            self.assertTrue(tri.showed(name, quiet["layers"][name]).startswith("Nothing found"), name)
        self.assertEqual(tri.showed("cordon", dead["layers"]["cordon"]), "No current data.")
        self.assertEqual(tri.public(tri.attach_rates({"join": tri.JOIN, "snapshot_generated_at": None,
                                                      "layer_states": {}, "windows": {}, "baseline_given": False,
                                                      "records": [dead]}, None))["records"][0]["reading"],
                         "incomplete")

    def test_what_a_layer_showed_says_what_was_counted_and_what_was_not(self):
        wf = item("watchfloor", "watchfloor:2026-11-11:country:TS:attention", ["TN"], "anomaly",
                  "2026-11-11T23:59:59Z")
        adv = item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-05-01T00:00:00Z", measures={"level": 2})
        usual = one(tri.triage(snapshot(outage(), wf, adv), {"advisory_levels": {}, "attention_share": {"TN": 0.9}}))
        text = tri.showed("watchfloor", usual["layers"]["watchfloor"])
        self.assertIn("90% of archived days", text)
        self.assertIn("not counted", text)
        self.assertIn("ends before the outage", text)
        self.assertIn("1 older notice standing (not counted)", tri.showed("cordon", usual["layers"]["cordon"]))
        rare = one(tri.triage(snapshot(outage(), wf), {"advisory_levels": {}, "attention_share": {"TN": 0.1}}))
        self.assertIn("unusual here", tri.showed("watchfloor", rare["layers"]["watchfloor"]))
        blind = one(tri.triage(snapshot(outage(), wf)))
        self.assertIn("no archive baseline", tri.showed("watchfloor", blind["layers"]["watchfloor"]))

    def test_it_publishes_only_to_site_data_and_only_the_public_document(self):
        for folder in (ROOT / "site", ROOT / "site" / "data" / "joins", ROOT / "site" / "vendor"):
            with self.assertRaises(SystemExit, msg=str(folder)):
                tri.publish_folder(folder)
        self.assertEqual(tri.publish_folder(ROOT / "site" / "data"), (ROOT / "site" / "data").resolve())
        work = Path(tempfile.mkdtemp())
        snap_file = work / "s.geojson"
        snap_file.write_text(json.dumps(snapshot(outage())), encoding="utf-8")
        tri.main(["--snapshot", str(snap_file), "--out", str(work / "full"), "--rates", str(work / "none.json"),
                  "--publish", str(work / "pub")])
        self.assertEqual([p.name for p in (work / "pub").iterdir()], [tri.PUBLIC_NAME])
        pub = json.loads((work / "pub" / tri.PUBLIC_NAME).read_text(encoding="utf-8"))
        self.assertNotIn("verdict_draft", pub["records"][0])
        self.assertIn("Will OONI confirm blocking in TN", pub["records"][0]["question_draft"]["question"])
        self.assertEqual(pub["gate"]["may_publish"], True)
        full = json.loads((work / "full" / "latest.json").read_text(encoding="utf-8"))
        self.assertIn("approved_by", full["records"][0]["verdict_draft"], "the working draft stays in out/")

    def test_the_page_and_the_mcp_server_show_the_wording_from_the_document(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn("data/triage.json", js)
        self.assertIn("triage.not_a_warning", js)
        self.assertIn("Not a warning or a forecast", js)
        self.assertIn("a draft, not registered", js)
        self.assertNotIn("verdict_draft", js)
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="triage"', page)
        tools = (ROOT / "mcp" / "tools.js").read_text(encoding="utf-8")
        self.assertIn("triage.not_a_warning", tools)
        self.assertNotIn("verdict_draft", tools)
        server = (ROOT / "mcp" / "server.js").read_text(encoding="utf-8")
        self.assertIn("It is not a warning, not a forecast and not a finding of cause", server)

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
