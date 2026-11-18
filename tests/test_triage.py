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


def alert(cc, suffix, when, subject="Security Alert: U.S. Embassy (November 11, 2026)"):
    """An embassy alert as Cordon titles it."""
    return item("cordon", f"cordon:alert:{cc}:{suffix}", [cc], "routine", when, title=f"US embassy ({cc}): {subject}")


def attention_item(cc, day="2026-11-11", baseline="wf-28d", z=3.1, kind="attention"):
    """A Watchfloor country item raised on its 28-day z-score (wf-28d) or its 90-day median (wf-90d-median)."""
    return item("watchfloor", f"watchfloor:{day}:country:{cc}:{kind}", [cc], "anomaly", f"{day}T23:59:59Z",
                baseline_ref={"id": baseline}, measures={"score": {"z": z}})


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

    def test_recent_notice_or_unusual_attention_without_a_hazard_reads_tension_context(self):
        r = one(tri.triage(snapshot(outage(), alert("TN", "abc", "2026-11-11T00:00:00Z"))))
        self.assertEqual(r["reading"], tri.TENSION)
        r = one(tri.triage(snapshot(outage(), attention_item("TN"))))
        self.assertEqual(r["reading"], tri.TENSION)
        self.assertFalse(r["layers"]["watchfloor"]["data_reaches_outage"], "Watchfloor's day ended before the outage")

    def test_the_reading_names_what_was_seen_and_never_a_cause(self):
        self.assertEqual(tri.TENSION, "tension context")
        self.assertNotIn("deliberate", tri.READINGS)
        for text in list(tri.READINGS.values()) + list(tri.POWER_READINGS.values()) + list(tri.POWER_CAVEATS):
            self.assertNotIn("deliberate", text.lower())
            self.assertNotIn("has support", text)

    def test_a_standing_advisory_is_listed_but_is_not_a_signal(self):
        adv = item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-05-01T00:00:00Z", measures={"level": 2})
        r = one(tri.triage(snapshot(outage(), adv)))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(r["layers"]["cordon"]["standing"][0]["level"], 2)

    def test_a_hazard_outranks_notices(self):
        haz = item("elements", "elements:gdacs:FL-1", ["TN"], "anomaly", "2026-11-11T06:00:00Z")
        notice = alert("TN", "abc", "2026-11-11T09:00:00Z")
        self.assertEqual(one(tri.triage(snapshot(outage(), haz, notice)))["reading"], "natural")

    def test_a_layer_without_current_data_is_incomplete_never_quiet(self):
        for state in ("stale", "missing"):
            r = one(tri.triage(snapshot(outage(), states={"elements": state})))
            self.assertEqual(r["reading"], "incomplete")
            self.assertEqual(r["no_current_data"], ["elements"])

    def test_a_single_source_dip_gets_context_but_no_reading(self):
        r = one(tri.triage(snapshot(outage("SC", "BO-IND-4"), alert("SC", "abc", "2026-11-11T00:00:00Z"))))
        self.assertEqual(r["reading"], "unconfirmed")
        self.assertEqual(len(r["layers"]["cordon"]["recent"]), 1)
        self.assertFalse(r["registerable"], "a single-source dip is not a confirmed outage: no question to register")

    def test_no_probability_and_nothing_registered(self):
        r = one(tri.triage(snapshot(outage())))
        v = r["verdict_draft"]
        self.assertIsNone(v["probability"])
        self.assertIsNone(v["approved_by"])
        self.assertFalse(v["registered"])
        self.assertEqual(v["window_ends"], "2026-11-15T00:00:00Z")   # 72 whole hours from the start hour
        c = r["cause_draft"]
        self.assertEqual((c["probability"], c["approved_by"], c["registered"]), (None, None, False))
        self.assertNotIn("probability", json.dumps({k: x for k, x in r.items()
                                                    if k not in ("verdict_draft", "cause_draft")}))

    def test_ooni_now_is_shown_but_the_question_does_not_settle_on_it(self):
        ooni = item("blackout", "blackout:ooni:ir:2026-11-12", ["IR"], "anomaly", indicator_id="BO-IND-3")
        r = one(tri.triage(snapshot(outage("IR"), ooni)))
        self.assertEqual(r["ooni_now"]["id"], "blackout:ooni:ir:2026-11-12")
        self.assertNotIn("BO-IND-3", r["verdict_draft"]["rule_draft"])
        self.assertNotIn("already", r["verdict_draft"]["caution"])

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
            alert("TN", "a", "2026-11-12T00:00:00Z"),   # hazard wins in TN
            alert("DZ", "a", "2026-11-12T00:00:00Z"),
            item("cordon", "cordon:advisory:FR", ["FR"], "routine", "2026-01-01T00:00:00Z"),  # standing: no signal
            attention_item("DZ", "2026-11-12"),
            item("watchfloor", "watchfloor:2026-11-12:zone:x", ["LY", "EG"], "anomaly", "2026-11-12T23:59:59Z",
                 baseline_ref={"id": "wf-90d-median"}))
        doc = rates.rates([day1])
        n = len(rates.all_countries())
        d = doc["background"]["days"][0]
        self.assertEqual((d["hazard"], d["recent_notice"], d["raised_attention"]), (1, 2, 3))
        self.assertEqual(d["unusual_attention"], 1, "only Watchfloor's own 28-day z-score makes attention unusual")
        self.assertEqual(d["would_read"], {"natural": 1, tri.TENSION: 1, "open": n - 2})
        self.assertEqual(doc["background"]["country_days"], n)
        self.assertEqual(doc["background"]["would_read_country_days"], n)
        self.assertEqual(d["recent_notice_kinds"], {"alert": 2})
        self.assertEqual(len(doc["cautions"]), 4, "one day, no outages, no ended list, no hit rate: four cautions")
        self.assertEqual(doc["cautions"][-1], rates.HIT_RATE_CAUTION)
        self.assertTrue(any("no list of Elements' ended hazards" in c for c in doc["cautions"]))
        by = doc["background"]["by_country"]
        self.assertEqual(by["TN"], {tri.TENSION: 0.0, "natural": 1.0, "open": 0.0})
        self.assertEqual(by["DZ"][tri.TENSION], 1.0)
        self.assertNotIn("FR", by, "a country that only ever read open is not listed")
        self.assertNotIn("_natural", d)

    def test_events_count_each_notice_once_and_each_attention_run_once(self):
        days = []
        for i, day in enumerate(("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13")):
            feats = [alert("DZ", "a", "2026-11-10T00:00:00Z")] if i < 2 else []   # one alert, counted on two days
            if i in (0, 1, 3):                                                       # attention on 10, 11 and 13 Nov
                feats.append(attention_item("DZ", day))
            s = snapshot(*feats)
            s["generated_at"] = f"{day}T23:00:00Z"
            days.append(s)
        b = rates.rates(days)["background"]
        dz = b["by_country_events"]["DZ"][tri.TENSION]
        self.assertEqual(dz["notices"], 1, "one alert over two days is one notice")
        self.assertEqual(dz["attention_runs"], 2, "10-11 Nov is one run, 13 Nov another")
        self.assertEqual(dz["days"], 3)
        self.assertEqual(b["countries_with_signal"], 1)
        self.assertEqual(b["signal_countries_would_read"][tri.TENSION], 0.75)
        self.assertNotIn("_events", b["days"][0])

    def test_a_regional_alert_is_one_notice_in_each_country(self):
        regional = item("cordon", "cordon:alert:DZ:regional", ["DZ", "TN", "LY"], "routine", "2026-11-12T00:00:00Z",
                        title="US embassy (DZ): Security Alert: U.S. Embassy (November 11, 2026)")
        b = rates.rates([snapshot(regional)])["background"]
        for cc in ("DZ", "TN", "LY"):
            self.assertEqual(b["by_country_events"][cc][tri.TENSION]["notices"], 1)

    def test_base_rate_text_leads_with_the_country_and_names_its_events(self):
        base = rates.rates([snapshot(alert("TN", "a", "2026-11-12T00:00:00Z"))])
        doc = tri.attach_rates(tri.triage(snapshot(outage(), alert("TN", "a", "2026-11-12T00:00:00Z"))), base)
        tn = doc["records"][0]["base_rate"]
        self.assertTrue(tn["text"].startswith('In Tunisia, an outage would have read "tension context" whatever its '
                                              "cause on 1 of 1 counted days, from 1 notice. Across all "), tn["text"])
        self.assertIn("(only 1 country showed any signal)", tn["text"])
        self.assertEqual((tn["this_countrys_days"], tn["counted_days"]), (1, 1))
        quiet = tri.attach_rates(tri.triage(snapshot(outage("DZ"))), rates.rates([snapshot()]))["records"][0]
        self.assertIn("on 1 of 1 counted days. Across all", quiet["base_rate"]["text"],
                      "open names no events")
        self.assertIsNone(quiet["base_rate"]["events"])

    def test_base_rate_text_says_none_when_the_country_never_read_that_way(self):
        base = rates.rates([snapshot()])
        rec = {"reading": tri.TENSION, "country": "TN"}
        doc = tri.attach_rates({"records": [rec], "join": tri.JOIN}, base)
        self.assertIn("on none of 1 counted days. Across all", doc["records"][0]["base_rate"]["text"])

    def test_todays_partial_day_is_left_out_of_the_background(self):
        yesterday, today = snapshot(), snapshot()
        yesterday["generated_at"], today["generated_at"] = "2026-11-14T22:33:00Z", "2026-11-15T01:53:00Z"
        doc = rates.rates([yesterday, today], today="2026-11-15")
        self.assertEqual([d["day"] for d in doc["background"]["days"]], ["2026-11-14"])
        self.assertEqual(doc["archive"]["days_left_out_partial"], ["2026-11-15"])
        self.assertEqual(len(rates.rates([yesterday, today])["background"]["days"]), 2, "no date given: nothing left out")

    def test_a_day_with_a_layer_down_is_left_out_of_the_rates(self):
        good = snapshot()
        bad = snapshot(states={"cordon": "stale"})
        bad["generated_at"] = "2026-11-14T00:21:12Z"
        doc = rates.rates([good, bad])
        self.assertEqual(len(doc["background"]["days"]), 1)
        self.assertEqual(doc["archive"]["days_left_out_layer_down"], ["2026-11-14"])

    def test_replay_counts_an_outage_once_with_its_newest_reading(self):
        first = snapshot(outage())
        second = snapshot(outage(), alert("TN", "a", "2026-11-11T00:00:00Z"))
        second["generated_at"] = "2026-11-14T00:21:12Z"
        doc = rates.rates([second, first])
        self.assertEqual(doc["replay"]["outages"], 1)
        self.assertEqual(doc["replay"]["by_reading"], {tri.TENSION: 1})
        self.assertEqual(doc["replay"]["rows"][0]["source"], "IODA")

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
        for before, direction, reading, bucket in ((2, "raised", tri.TENSION, "recent"),
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
        self.assertEqual(r["reading"], tri.TENSION)
        self.assertEqual(r["layers"]["cordon"]["recent"][0]["direction"], "unknown")
        self.assertTrue(any("no earlier level" in c for c in r["caveats"]))

    def test_whole_day_attention_reads_the_same_in_both_time_formats(self):
        """Watchfloor's items carried the end of their day (T23:59:59Z); from 16 Nov 2026 they say precision "day" with
        the day's midnight. The archive holds both, and a day must be read the same either way: counted on the outage's
        day, not counted when it begins after, and its end is what says whether the data reaches the outage."""
        def new(day):
            f = attention_item("TN", day=day)
            f["properties"].update({"data_as_of": f"{day}T00:00:00Z", "data_as_of_precision": "day"})
            return f
        for day in ("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"):
            a = one(tri.triage(snapshot(outage(), attention_item("TN", day=day)), {}))
            b = one(tri.triage(snapshot(outage(), new(day)), {}))
            for key in ("unusual", "data_reaches_outage"):
                self.assertEqual(a["layers"]["watchfloor"][key], b["layers"]["watchfloor"][key], (day, key))
            self.assertEqual((a["reading"], len(a["layers"]["watchfloor"]["after"])),
                             (b["reading"], len(b["layers"]["watchfloor"]["after"])), day)

    def test_unusual_attention_is_watchfloors_own_28_day_z_score(self):
        # D8 (cop-joins.md M3): Iran was raised on 5 of 16 archived days around the Qeshm strikes, and COP's own
        # "under 25% of archived days" share switched its signal off. Watchfloor's z-score is against the 28 days
        # before the day it measures, so a run of raised days does not switch itself off here.
        z28 = one(tri.triage(snapshot(outage(), attention_item("TN")), {"advisory_levels": {}}))
        self.assertEqual(z28["reading"], tri.TENSION)
        self.assertEqual(z28["layers"]["watchfloor"]["unusual_items"][0]["z"], 3.1)
        ninety = one(tri.triage(snapshot(outage(), attention_item("TN", baseline="wf-90d-median")), {}))
        self.assertEqual(ninety["reading"], "open", "high against the 90-day median is standing attention")
        self.assertEqual((len(ninety["layers"]["watchfloor"]["elevated"]), ninety["layers"]["watchfloor"]["unusual"]),
                         (1, False))
        later = one(tri.triage(snapshot(outage(), attention_item("TN", day="2026-11-13")), {}))
        self.assertEqual(later["reading"], "open", "a Watchfloor day that begins after the outage is not counted")
        self.assertEqual(len(later["layers"]["watchfloor"]["after"]), 1)
        same_day = one(tri.triage(snapshot(outage(), attention_item("TN", day="2026-11-12")), {}))
        self.assertEqual(same_day["reading"], tri.TENSION, "the day the outage falls in is around its hour")

    def test_without_an_archive_baseline_the_record_says_so(self):
        r = one(tri.triage(snapshot(outage())))
        self.assertTrue(any("No archive baseline" in c for c in r["caveats"]))
        r = one(tri.triage(snapshot(outage()), {"advisory_levels": {}, "attention_share": {}}))
        self.assertFalse(any("No archive baseline" in c for c in r["caveats"]))

    def test_the_ooni_question_settles_on_oonis_own_counts_in_every_country(self):
        # Blackout reads OONI's rate for six countries only; a rule settled on that reading could only resolve No
        # in Tunisia. The rule reads OONI's hourly counts instead, in the words Verdict's resolver parses.
        v = one(tri.triage(snapshot(outage())))["verdict_draft"]
        self.assertIsNone(v["probability"])
        self.assertNotIn("rise_form", v)
        self.assertEqual(v["rule_draft"],
                         "Yes if OONI's aggregation API (api.ooni.io, probe_cc=TN, all tests, hourly) counts at "
                         "least 50 measurements from 2026-11-12T00:00:00Z to 2026-11-15T00:00:00Z UTC, at least "
                         "15% of them confirmed or anomalous, and that share at least 2 times the share over the "
                         "120 hours before 2026-11-12T00:00:00Z UTC (a baseline with no measurements counts as "
                         "zero); read on a check after 00:00 UTC on 16 Nov 2026. No otherwise.")
        self.assertEqual(v["window_ends"], "2026-11-15T00:00:00Z")
        self.assertEqual(v["resolution"]["known_at"], "2026-11-16T00:00:00Z")
        self.assertIn("aggregation API", v["resolution"]["source"])
        self.assertIn("probes cannot report", v["caution"])

    def test_history_keeps_each_advisory_level_and_each_alerts_dates(self):
        a = snapshot(item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-05-01T00:00:00Z",
                          measures={"level": 2}),
                     alert("CO", "x", "2026-11-06T21:52:18Z"))
        b = snapshot(item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-11-13T00:00:00Z",
                          measures={"level": 3}),
                     alert("CO", "x", "2026-11-12T22:33:00Z"))
        b["generated_at"] = "2026-11-14T00:21:12Z"
        h = rates.history([a, b])
        self.assertEqual(h["advisory_levels"]["cordon:advisory:TN"],
                         [["2026-05-01T00:00:00Z", 2], ["2026-11-13T00:00:00Z", 3]])
        self.assertEqual(h["alert_dates"]["cordon:alert:CO:x"],
                         [["2026-11-13T00:21:12Z", "2026-11-06T21:52:18Z"],
                          ["2026-11-14T00:21:12Z", "2026-11-12T22:33:00Z"]])
        self.assertNotIn("attention_share", h, "unusual attention is Watchfloor's own z-score, not an archive share")

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
            self.assertEqual(set(q), {"status", "note", "verdict_id", "question", "window_ends", "rule_draft", "caution",
                                      "resolution"})
            c = r["cause_question_draft"]
            self.assertEqual(c["status"], "draft, not registered")
            self.assertIn("government order", c["question"])
        by_cc = {r["country"]: r for r in pub["records"]}
        self.assertEqual((by_cc["TN"]["registerable"], by_cc["SC"]["registerable"]), (True, False))
        self.assertEqual(by_cc["TN"]["cause_question_draft"]["verdict_id"],
                         "verdict:2026-11-19:cop-tn-outage-20261112t0025-cause")
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

    def test_the_public_question_keeps_its_caution(self):
        pub = tri.public(tri.attach_rates(tri.triage(snapshot(outage("IR"))), None))
        q = pub["records"][0]["question_draft"]
        self.assertIn("probe_cc=IR", q["rule_draft"])
        self.assertIn("probes cannot report", q["caution"])

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
        adv = item("cordon", "cordon:advisory:TN", ["TN"], "routine", "2026-05-01T00:00:00Z", measures={"level": 2})
        holiday = alert("TN", "h", "2026-11-11T09:00:00Z", "Security Alert: Holiday Travel, November 13, 2026")
        usual = one(tri.triage(snapshot(outage(), attention_item("TN", baseline="wf-90d-median"), adv, holiday), {}))
        text = tri.showed("watchfloor", usual["layers"]["watchfloor"])
        self.assertIn("90-day median", text)
        self.assertIn("not counted", text)
        self.assertIn("ends before the outage", text)
        cordon = tri.showed("cordon", usual["layers"]["cordon"])
        self.assertIn("1 older notice standing (not counted)", cordon)
        self.assertIn("1 embassy alert of a type not counted (event)", cordon)
        rare = one(tri.triage(snapshot(outage(), attention_item("TN")), {}))
        self.assertIn("unusual here", tri.showed("watchfloor", rare["layers"]["watchfloor"]))
        self.assertIn("z 3.1", tri.showed("watchfloor", rare["layers"]["watchfloor"]))

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
        self.assertIn("Will OONI measure blocking in TN rising", pub["records"][0]["question_draft"]["question"])
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


class NewNoticeKinds(unittest.TestCase):
    """UK FCDO and Canada travel advice count only as a change Cordon saw; CDC health notices are not read."""

    def fcdo(self, tier, statuses=("avoid_all_travel_to_parts",), when="2026-11-11T09:00:00Z"):
        return item("cordon", "cordon:fcdo:tunisia", ["TN"], tier, when,
                    measures={"alert_status": list(statuses), "advice": "x", "new": False})

    def canada(self, tier, level=2, regional=False, when="2026-11-11T09:00:00Z"):
        return item("cordon", "cordon:canada:TN", ["TN"], tier, when, measures={"level": level, "regional": regional})

    def test_an_edit_without_a_change_is_not_counted(self):
        for f in (self.fcdo("routine"), self.canada("routine")):
            r = one(tri.triage(snapshot(outage(), f), {"advisory_levels": {}, "attention_share": {}}))
            self.assertEqual(r["reading"], "open", f["properties"]["id"])
            c = r["layers"]["cordon"]
            self.assertEqual((c["recent"], c["recent_not_raised"][0]["direction"]), ([], "unchanged"))
            self.assertIn("edited without a change", tri.showed("cordon", c))

    def test_a_change_with_no_earlier_level_counts_and_says_so(self):
        for f in (self.fcdo("anomaly"), self.canada("anomaly")):
            r = one(tri.triage(snapshot(outage(), f), {"advisory_levels": {}, "attention_share": {}}))
            self.assertEqual(r["reading"], tri.TENSION, f["properties"]["id"])
            self.assertEqual(r["layers"]["cordon"]["recent"][0]["direction"], "unknown")
            self.assertTrue(any("no earlier level" in c for c in r["caveats"]))
            self.assertEqual(gate.review(tri.JOIN, {"records": [r]})["review_class"], "open")

    def test_a_change_is_compared_with_the_archive(self):
        # Canada: a regional advisory removed at the same level is a lowering (Mexico, 12 Nov 2026).
        f = self.canada("anomaly", level=1, regional=False)
        hist = {"advisory_levels": {"cordon:canada:TN": [["2026-11-01T00:00:00Z", [1, 1]]]}, "attention_share": {}}
        r = one(tri.triage(snapshot(outage(), f), hist))
        self.assertEqual((r["reading"], r["layers"]["cordon"]["recent_not_raised"][0]["direction"]), ("open", "lowered"))
        raised = one(tri.triage(snapshot(outage(), self.canada("anomaly", level=2)), hist))
        self.assertEqual((raised["reading"], raised["layers"]["cordon"]["recent"][0]["direction"]),
                         (tri.TENSION, "raised"))
        # FCDO: adding a statement raises it; removing one lowers it.
        before = tri.notice_level(self.fcdo("routine", ("avoid_all_travel_to_parts",))["properties"])
        hist = {"advisory_levels": {"cordon:fcdo:tunisia": [["2026-11-01T00:00:00Z", before]]}, "attention_share": {}}
        up = self.fcdo("anomaly", ("avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts"))
        down = self.fcdo("anomaly", ())
        self.assertEqual(one(tri.triage(snapshot(outage(), up), hist))["layers"]["cordon"]["recent"][0]["direction"],
                         "raised")
        self.assertEqual(one(tri.triage(snapshot(outage(), down), hist))["reading"], "open")

    def safetravel(self, tier, level="medium", note=None, when="2026-11-11T00:00:00Z"):
        return item("cordon", "cordon:safetravel:TN", ["TN"], tier, when,
                    measures={"level": level, "advice": "x", "regional_note": note, "updated": when[:10], "new": False})

    def test_safetravel_counts_only_a_level_or_regional_note_change(self):
        none = {"advisory_levels": {}, "attention_share": {}}
        edit = one(tri.triage(snapshot(outage(), self.safetravel("routine")), none))
        self.assertEqual((edit["reading"], edit["layers"]["cordon"]["recent_not_raised"][0]["direction"]),
                         ("open", "unchanged"))
        changed = one(tri.triage(snapshot(outage(), self.safetravel("anomaly")), none))
        self.assertEqual((changed["reading"], changed["layers"]["cordon"]["recent"][0]["direction"]),
                         (tri.TENSION, "unknown"))
        hist = {"advisory_levels": {"cordon:safetravel:TN": [["2026-10-01T00:00:00Z", [2, 1]]]}, "attention_share": {}}
        dropped_note = one(tri.triage(snapshot(outage(), self.safetravel("anomaly", "medium", None)), hist))
        self.assertEqual(dropped_note["layers"]["cordon"]["recent_not_raised"][0]["direction"], "lowered")
        up = one(tri.triage(snapshot(outage(), self.safetravel("anomaly", "high", None)), hist))
        self.assertEqual((up["reading"], up["layers"]["cordon"]["recent"][0]["direction"]), (tri.TENSION, "raised"))
        self.assertEqual(tri.notice_level(self.safetravel("routine", "avoid", "some areas")["properties"]), [4, 1])
        self.assertIsNone(tri.notice_level(self.safetravel("routine", "unheard-of")["properties"]))
        self.assertEqual(gate.review(tri.JOIN, {"records": [up]})["review_class"], "open")

    def test_cdc_health_notices_are_not_read(self):
        cdc = item("cordon", "cordon:cdc:measles-globe", ["TN", "DZ"], "anomaly", "2026-11-11T09:00:00Z",
                   measures={"level": 1})
        r = one(tri.triage(snapshot(outage(), cdc), {"advisory_levels": {}, "attention_share": {}}))
        c = r["layers"]["cordon"]
        self.assertEqual((r["reading"], c["recent"], c["recent_not_raised"], c["standing"]), ("open", [], [], []))
        self.assertNotIn("cordon:cdc", tri.JOIN["inputs"])

    def test_every_notice_kind_is_declared(self):
        for kind in tri.NOTICE_KINDS:
            self.assertIn(f"cordon:{kind}", tri.JOIN["inputs"])
        self.assertEqual(gate.review(tri.JOIN)["review_class"], "open")

    def test_base_rate_history_keeps_travel_advice_levels_and_names_late_kinds(self):
        def day(d, *features):
            return {"generated_at": f"{d}T22:00:00Z", "features": list(features),
                    "cop_programs": {k: {"state": "fresh"} for k in ("elements", "cordon", "watchfloor", "blackout")}}
        snaps = [day("2026-11-09"),
                 day("2026-11-14", self.canada("routine", level=1, regional=True, when="2026-11-14T09:00:00Z"))]
        h = rates.history(snaps)
        self.assertEqual(h["advisory_levels"]["cordon:canada:TN"], [["2026-11-14T09:00:00Z", [1, 1]]])
        doc = rates.rates(snaps)
        self.assertTrue(any("canada from 2026-11-14" in c for c in doc["cautions"]), doc["cautions"])
        self.assertEqual(doc["background"]["notice_kinds_in_archive_from"], {"canada": "2026-11-14"})


class TriageFixes(unittest.TestCase):
    """D8 and M5 (Tom, 15 Nov 2026; cop-joins.md M1, M2, M3, M5): the three rules and the wider input."""

    def test_a_cyclone_at_landfall_counts_while_it_is_active(self):
        # MURASU-26 (GDACS red, India): formed 1 Nov 00:00, last advisory 3 Nov 06:00. An outage on the evening of
        # 4 Nov, about 41 hours after the last advisory, read "standing" under the old rule.
        murasu = item("elements", "elements:gdacs:TC-1001346", ["IN"], "anomaly", "2026-11-03T06:00:00Z",
                      measures={"hazard_code": "TC", "from": "2026-11-01T00:00:00Z", "to": "2026-11-03T06:00:00Z"})
        r = one(tri.triage(snapshot(outage("IN", at="2026-11-04T23:00:00Z"), murasu)))
        self.assertEqual(r["reading"], "natural")
        h = r["layers"]["elements"]["significant"][0]
        self.assertEqual((h["episodic"], h["active_until"]), ("tropical cyclone", "2026-11-03T06:00:00Z"))
        late = one(tri.triage(snapshot(outage("IN", at="2026-11-05T23:00:00Z"), murasu)))
        self.assertEqual(late["reading"], "open", "more than 48 hours after its last advisory: standing")
        self.assertEqual(late["layers"]["elements"]["standing"][0]["id"], "elements:gdacs:TC-1001346")

    def test_a_standing_kind_keeps_the_began_in_the_window_rule(self):
        drought = item("elements", "elements:gdacs:DR-1", ["IN"], "anomaly", "2026-11-04T00:00:00Z",
                       measures={"hazard_code": "DR", "from": "2026-06-01T00:00:00Z", "to": "2026-11-04T00:00:00Z"})
        r = one(tri.triage(snapshot(outage("IN", at="2026-11-04T23:00:00Z"), drought)))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(r["layers"]["elements"]["standing"][0]["id"], "elements:gdacs:DR-1")

    def test_embassy_alerts_are_sorted_by_their_type(self):
        cases = {
            "Security Alert: U.S. Mission Saudi Arabia \u2013 November 7, 2026": "security",
            "Demonstration Alert: Protest in Mexico City, November 15, 2026": "demonstration",
            "Weather Alert: El Ni\u00f1o and the Potential for Heavy Rainfall this Winter": "weather",
            "Natural Disaster Alert: Mayon Volcano Alert Level Raised to 3": "natural disaster",
            "Health Alert: U.S. Embassy Abuja, Nigeria (October 20, 2026)": "health",
            "Security Alert: Holiday Travel, November 13, 2026": "event",
            "Security Alert: Cartagena Independence Day (November 11\u201316, 2026)": "event",
            "Security Alert: ASEAN Summit \u2013 Traffic, Security, and Flight Disruptions": "event",
            "Message for U.S. Citizens:": "routine",
            "Routine Message: Remote Services in Angeles - September 9-10, 2026": "routine",
            "ALERT \u2013 MESSAGE FOR U.S. CITIZENS: CONSULAR OFFICERS TO OFFER SERVICES": "routine",
        }
        for subject, kind in cases.items():
            self.assertEqual(tri.alert_type(f"US embassy (XX): {subject}"), kind, subject)
        for subject, kind in cases.items():
            r = one(tri.triage(snapshot(outage(), alert("TN", "a", "2026-11-11T09:00:00Z", subject))))
            self.assertEqual(r["reading"], tri.TENSION if kind in tri.ALERT_COUNTED else "open", subject)

    def test_a_weather_alert_is_listed_with_a_pointer_to_elements(self):
        r = one(tri.triage(snapshot(outage(), alert("TN", "w", "2026-11-11T09:00:00Z",
                                                    "Weather Alert: Hurricane approaching"))))
        self.assertEqual(r["reading"], "open")
        self.assertEqual(r["layers"]["cordon"]["recent_not_counted"][0]["alert_type"], "weather")
        self.assertTrue(any("Elements is the layer for" in c for c in r["caveats"]))

    def test_a_re_dated_alert_counts_once_from_when_it_was_first_seen(self):
        # cordon:alert:CO:937adfcbfd2a: dated 6 Nov 21:52, then 12 Nov 22:33, then 13 Nov 10:03.
        edited = alert("TN", "x", "2026-11-11T09:00:00Z")
        hist = {"advisory_levels": {}, "alert_dates": {"cordon:alert:TN:x": [
            ["2026-11-07T00:00:00Z", "2026-11-06T21:52:18Z"], ["2026-11-11T22:00:00Z", "2026-11-11T09:00:00Z"]]}}
        r = one(tri.triage(snapshot(outage(), edited), hist))
        self.assertEqual(r["reading"], "open")
        row = r["layers"]["cordon"]["recent_not_raised"][0]
        self.assertEqual((row["direction"], row["first_dated"]), ("re-dated", "2026-11-06T21:52:18Z"))
        self.assertIn("re-dated from an earlier date", tri.showed("cordon", r["layers"]["cordon"]))
        # Causal: a snapshot taken after the outage cannot make an alert older than it was at the outage.
        later = {"advisory_levels": {}, "alert_dates": {"cordon:alert:TN:x": [
            ["2026-11-14T00:00:00Z", "2026-11-06T21:52:18Z"]]}}
        self.assertEqual(one(tri.triage(snapshot(outage(), edited), later))["reading"], tri.TENSION)

    def test_the_notice_window_is_48_hours_not_floored_to_midnight(self):
        at = "2026-11-12T20:00:00Z"
        inside = alert("TN", "in", "2026-11-10T21:00:00Z")
        outside = alert("TN", "out", "2026-11-10T03:00:00Z")      # 65 hours before: the old floor counted it
        r = one(tri.triage(snapshot(outage(at=at), inside, outside)))
        c = r["layers"]["cordon"]
        self.assertEqual(([x["id"] for x in c["recent"]], [x["id"] for x in c["standing"]]),
                         (["cordon:alert:TN:in"], ["cordon:alert:TN:out"]))
        day = item("cordon", "cordon:safetravel:TN", ["TN"], "anomaly", "2026-11-10T00:00:00Z",
                   measures={"level": "high"})
        r = one(tri.triage(snapshot(outage(at=at), day), {"advisory_levels": {}}))
        self.assertEqual(r["reading"], tri.TENSION, "a date with no time counts from the start of the day")

    def radar_anomaly(self, cc="IQ", start="2026-11-09T04:45:00Z", end=None, asn=48492):
        span = f"from {start} to {end}" if end else f"since {start}, ongoing"
        return item("blackout", "blackout:radar-anomaly:8ddd8f4a", [cc], "anomaly", end or "2026-11-12T01:45:32Z",
                    indicator_id="BO-IND-10",
                    title=f"Iraq, AS{asn} (IQ-Online): traffic anomaly verified by Cloudflare Radar",
                    summary=f"Cloudflare Radar's anomaly detection flagged this traffic {span}, seen in Radar's "
                            "HTTP data; Cloudflare marked it verified (TP). Radar gives no cause.")

    def test_a_verified_radar_anomaly_is_triaged_from_when_it_began(self):
        r = one(tri.triage(snapshot(self.radar_anomaly())))
        o = r["outage"]
        self.assertEqual((o["source"], o["started_at"], o["confirmed"], o["network"]),
                         ("Cloudflare Radar (verified anomaly)", "2026-11-09T04:45:00Z", True,
                          {"asn": 48492, "name": "IQ-Online"}))
        self.assertEqual(r["reading"], "open")
        self.assertTrue(r["registerable"])
        self.assertTrue(any("one network (AS48492)" in c for c in r["caveats"]))
        self.assertIn("-as48492-", r["cause_draft"]["id_slug"])
        ended = one(tri.triage(snapshot(self.radar_anomaly(end="2026-11-10T02:00:00Z"))))
        self.assertEqual((ended["outage"]["started_at"], ended["outage"]["ended_at"]),
                         ("2026-11-09T04:45:00Z", "2026-11-10T02:00:00Z"))

    def test_a_radar_outage_quotes_cloudflares_cause_and_a_platform_one_is_listed_not_read(self):
        cause = item("blackout", "blackout:radar-outage:1800", ["SY"], "anomaly", "2026-11-12T01:45:32Z",
                     indicator_id="BO-IND-9", title="Syria: government directed (Cloudflare Radar)",
                     summary="Cloudflare Radar's Outage Center lists an outage since 2026-11-11T22:00:00Z, ongoing: "
                             "\"Exams.\" Cause as Cloudflare states it: government directed. Reported by Cloudflare.")
        platform = item("blackout", "blackout:radar-outage:1749", [], "anomaly", "2026-11-12T01:45:32Z",
                        indicator_id="BO-IND-9", title="Platform outage, no place named: technical problem",
                        summary="Cloudflare Radar's Outage Center lists an outage since 2026-11-12T16:17:00Z, ongoing: "
                                "\"M365.\" Cause as Cloudflare states it: technical problem.")
        doc = tri.triage(snapshot(cause, platform))
        r = one(doc)
        self.assertEqual((r["country"], r["outage"]["stated_cause"]), ("SY", "government directed"))
        self.assertIn("settled before it is asked", r["cause_draft"]["caution"])
        self.assertTrue(any("COP quotes it and does not assess it" in c for c in r["caveats"]))
        self.assertEqual([u["id"] for u in doc["unplaced"]], ["blackout:radar-outage:1749"])
        self.assertIn("Cloudflare Radar", tri.public(tri.attach_rates(doc, None))["what"])

    def test_the_cause_question_is_written_the_way_verdict_reads_it(self):
        c = one(tri.triage(snapshot(outage())))["cause_draft"]
        self.assertIn("check after 00:25 UTC on 19 Nov 2026", c["rule_draft"])
        self.assertEqual(c["resolution"]["known_at"], "2026-11-19T00:25:00Z")
        self.assertEqual(c["resolution"]["resolve_by"], "2026-11-26T00:25:00Z")
        for who in ("Cloudflare Radar", "Access Now", "Reuters"):
            self.assertIn(who, c["rule_draft"])
        v = one(tri.triage(snapshot(outage())))["verdict_draft"]
        self.assertIn("check after 00:00 UTC on 16 Nov 2026", v["rule_draft"])
        self.assertEqual(v["resolution"]["known_at"], "2026-11-16T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
