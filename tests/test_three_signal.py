"""The three-signal join, published since 15 Nov 2026: python tests/test_three_signal.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import three_signal as ts  # noqa: E402

NOW = "2026-11-13T00:21:12Z"
DUBAI = {"type": "Point", "coordinates": [55.3, 25.25]}
YEREVAN = {"type": "Point", "coordinates": [44.5, 40.2]}
HORMUZ = {"type": "Point", "coordinates": [56.4, 26.6]}
BANDAR = {"type": "Point", "coordinates": [56.378, 27.218]}


def item(program, id_, countries=(), tier="routine", at="2026-11-12T12:00:00Z", geometry=None, **props):
    p = {"id": id_, "title": id_, "summary": "", "tier": tier, "data_as_of": at, "countries": list(countries),
         "cop_program": program, "cop_stale": False}
    p.update(props)
    return {"type": "Feature", "geometry": geometry, "properties": p}


def snapshot(*features, states=None, at=NOW, navwarns_moving=True):
    states = {k: "fresh" for k in ts.LAYERS} | (states or {})
    # A fresh warning far away (the Pacific) shows that Cordon's navigation warnings are still being updated.
    alive = [item("cordon", "cordon:navwarn:XII-alive", [], "routine", "2026-11-10T00:00:00Z",
                  {"type": "Point", "coordinates": [-150.0, 20.0]}, title="NAVAREA XII: DERELICT BUOY ADRIFT")]
    return {"generated_at": at, "features": list(features) + (alive if navwarns_moving else []),
            "cop_programs": {k: {"state": v} for k, v in states.items()}}


def jam(at="2026-11-11T23:59:59Z", geometry=DUBAI, kind="event", n="1", countries=("AE",)):
    return item("noisefloor", f"noisefloor-{kind}-2026-11-11-{n}", countries, "anomaly", at, geometry)


def navwarn(title="HYDROPAC 1/26: MISSILE FIRING EXERCISE, STRAIT OF HORMUZ", at="2026-11-12T06:00:00Z",
            geometry=HORMUZ):
    return item("cordon", "cordon:navwarn:P-1-26", [], "routine", at, geometry, title=title)


def outage(cc="IR", indicator="BO-IND-1", at="2026-11-12T18:30:00Z"):
    return item("blackout", f"blackout:outage:{cc.lower()}:{at[:13]}", [cc], "anomaly", at, indicator_id=indicator)


def press(cc="BH", fips="BA", z=5.68, measure="violence", at="2026-11-12T00:00:00Z", tier="anomaly"):
    return item("watchfloor", f"watchfloor:{at[:10]}:country:{fips}:{measure}", [cc], tier, at, None,
                data_as_of_precision="day", measures={"articles": 84, "score": {"z": z, "ratio": 31.55}})


def alert(title="US embassy (BH): Security Alert: U.S. Embassy Manama - November 12, 2026", cc="BH",
          at="2026-11-12T20:33:28Z", n="77ef89632b5b"):
    return item("cordon", f"cordon:alert:{cc}:{n}", [cc], "routine", at, None, title=title)


def czib(title, until, at="2026-07-08T00:00:00Z"):
    return item("cordon", "cordon:czib:CZIB-2026-04", ["IR"], "routine", at,
                {"type": "Point", "coordinates": [54.9, 32.2]}, title=title, valid_until=until)


def vis(light="green"):
    return item("vis", "vis:2026-11-13:zone:11_Iran_Red_Sea", ["IR", "YE"], "routine", "2026-11-13T00:10:00Z", BANDAR,
                measures={"station": "Bandar Abbas", "stoplight_72h": light, "dark_low_light_min": 685,
                          "moon_lit": 0.18})


def hormuz(doc):
    return next(r for r in doc["records"] if r["area"] == "hormuz")


class ThreeSignal(unittest.TestCase):
    def test_all_four_inside_the_window_converge(self):
        r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), press(), vis())))
        self.assertEqual(r["reading"], "converged")
        self.assertEqual(r["signals_present"], list(ts.SIGNALS))
        self.assertLessEqual(r["tightest_span"]["hours"], ts.WINDOW_H)
        self.assertEqual(r["weather"]["state"], "green")
        self.assertEqual(r["weather"]["text"], "Vis's 72-hour weather forecast at 1 point in the area: Green at worst.")

    def test_two_of_four_is_partial_and_drafts_no_question(self):
        r = hormuz(ts.read(snapshot(jam(), navwarn(), vis())))
        self.assertEqual(r["reading"], "partial")
        self.assertIsNone(r["verdict_draft"])

    def test_three_of_four_is_still_partial(self):
        """Tom, 18 Nov 2026: converged needs all four. 15 Nov at Bahrain (interference, an embassy alert, press, no
        outage) reads partial."""
        r = hormuz(ts.read(snapshot(jam(), alert(), press(), vis())))
        self.assertEqual((r["reading"], r["signals_present"]), ("partial", ["interference", "notice", "press"]))

    def test_three_present_but_more_than_the_window_apart_is_partial(self):
        r = hormuz(ts.read(snapshot(jam(at="2026-11-09T06:00:00Z"), navwarn(at="2026-11-09T12:00:00Z"),
                                    outage(at="2026-11-12T18:30:00Z"), press(), vis())))
        self.assertEqual(r["signals_present"], list(ts.SIGNALS))
        self.assertGreater(r["tightest_span"]["hours"], ts.WINDOW_H)
        self.assertEqual(r["reading"], "partial")

    def test_nothing_is_none_and_a_dead_layer_is_incomplete(self):
        self.assertEqual(hormuz(ts.read(snapshot(vis())))["reading"], "none")
        r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), press(), vis(), states={"blackout": "stale"})))
        self.assertEqual((r["reading"], r["no_current_data"]), ("incomplete", ["blackout"]))
        r = hormuz(ts.read(snapshot(jam(), vis(), states={"watchfloor": "missing"})))
        self.assertEqual((r["reading"], r["no_current_data"]), ("incomplete", ["watchfloor"]))

    def test_navigation_warnings_that_stopped_updating_make_the_area_incomplete(self):
        stale = navwarn(at="2024-05-10T00:00:00Z")
        r = hormuz(ts.read(snapshot(jam(), stale, vis(), navwarns_moving=False)))
        self.assertEqual((r["reading"], r["no_current_data"]), ("incomplete", ["cordon:navwarn"]))
        self.assertTrue(any("stopped moving" in c for c in r["caveats"]))
        self.assertEqual(hormuz(ts.read(snapshot(jam(), vis())))["reading"], "partial")

    def test_interference_is_placed_by_the_circle_not_by_the_country_list(self):
        far = jam(geometry=YEREVAN, kind="new", n="x", countries=("AM", "AZ", "IR", "TR"))
        r = hormuz(ts.read(snapshot(far, vis())))
        self.assertFalse(r["signals"]["interference"]["present"])
        r = hormuz(ts.read(snapshot(jam(), vis())))
        self.assertTrue(r["signals"]["interference"]["present"])
        self.assertLess(r["signals"]["interference"]["items"][0]["km_from_centre"], 300)

    def test_interference_that_ended_is_context_not_a_signal(self):
        r = hormuz(ts.read(snapshot(jam(kind="ended"), vis())))
        self.assertFalse(r["signals"]["interference"]["present"])
        self.assertEqual(len(r["context"]["interference_ended"]), 1)

    def test_cordons_routine_tier_does_not_stop_a_notice_counting(self):
        r = hormuz(ts.read(snapshot(navwarn(), vis())))
        self.assertEqual(r["signals"]["notice"]["items"][0]["tier"], "routine")
        self.assertIn("MISSILE", r["signals"]["notice"]["items"][0]["military_words"])

    def test_a_warning_about_a_drifting_buoy_or_an_old_one_is_context(self):
        buoy = navwarn(title="HYDROPAC 2/26: DERELICT BUOY ADRIFT, GULF OF OMAN")
        old = navwarn(at="2026-10-01T00:00:00Z")
        old["properties"]["id"] = "cordon:navwarn:P-0-26"
        r = hormuz(ts.read(snapshot(buoy, old, vis())))
        self.assertFalse(r["signals"]["notice"]["present"])
        self.assertEqual((len(r["context"]["notices_other"]), len(r["context"]["notices_standing"])), (1, 1))

    def test_a_revised_airspace_bulletin_counts_only_when_the_archive_shows_the_revision(self):
        r2 = item("cordon", "cordon:czib:CZIB-2026-04", ["IR"], "routine", "2026-07-08T00:00:00Z",
                  {"type": "Point", "coordinates": [54.9, 32.2]}, title="EASA CZIB-2026-04-R2: Airspace of Iran")
        r3 = json.loads(json.dumps(r2))
        r3["properties"]["title"] = "EASA CZIB-2026-04-R3: Airspace of Iran"
        self.assertFalse(hormuz(ts.read(snapshot(r3, vis())))["signals"]["notice"]["present"], "no archive: standing")
        old_day = snapshot(r2, at="2026-11-11T00:21:12Z")
        new_day = snapshot(r3, vis(), at="2026-11-12T00:21:12Z")
        seen = ts.first_seen([old_day, new_day])
        self.assertEqual(list(seen["bulletins"]), ["cordon:czib:CZIB-2026-04 | EASA CZIB-2026-04-R3: Airspace of Iran"])
        r = hormuz(ts.read(snapshot(r3, vis()), seen))
        self.assertTrue(r["signals"]["notice"]["present"])
        self.assertFalse(hormuz(ts.read(snapshot(r2, vis()), seen))["signals"]["notice"]["present"])

    def test_a_us_embassy_security_alert_is_a_notice_and_other_alerts_are_context(self):
        """Gap 1 (a) of the Bahrain case review: the Manama alert of 15 Nov 2026 was filed as context."""
        r = hormuz(ts.read(snapshot(alert(), vis())))
        self.assertTrue(r["signals"]["notice"]["present"])
        self.assertEqual(r["signals"]["notice"]["items"][0]["alert_type"], "security")
        demo = alert("US embassy (KW): Demonstration Alert: U.S. Embassy Kuwait", "KW", n="d1")
        holiday = alert("US embassy (QA): Security Alert: National Day celebrations", "QA", n="h1")
        r = hormuz(ts.read(snapshot(demo, holiday, vis())))
        self.assertFalse(r["signals"]["notice"]["present"])
        self.assertEqual(sorted(a["alert_type"] for a in r["context"]["embassy_alerts"]), ["demonstration", "event"])
        outside = alert("US embassy (SA): Security Alert: U.S. Mission Saudi Arabia", "SA", n="s1")
        self.assertFalse(hormuz(ts.read(snapshot(outside, vis())))["signals"]["notice"]["present"])

    def test_a_re_dated_alert_keeps_the_first_date_the_archive_saw(self):
        first = snapshot(alert(at="2026-11-06T23:04:49Z"), at="2026-11-07T00:21:12Z")
        redated = alert(at="2026-11-12T08:00:00Z")
        seen = ts.first_seen([first, snapshot(redated)])
        self.assertTrue(hormuz(ts.read(snapshot(redated, vis())))["signals"]["notice"]["present"], "no archive")
        r = hormuz(ts.read(snapshot(redated, vis()), seen))
        self.assertFalse(r["signals"]["notice"]["present"], "first dated 6 Nov, outside the look-back")

    def test_an_easa_revision_at_its_expiry_is_a_renewal_and_mid_term_it_is_a_notice(self):
        """Gap 1 (b): EASA renewed CZIB-2026-04 (R2 to R3) on its 16 Nov 2026 expiry; that is context."""
        r2 = czib("EASA CZIB-2026-04-R2: Airspace of Iran", "2026-11-13T23:59:00Z")
        r3 = czib("EASA CZIB-2026-04-R3: Airspace of Iran", "2027-01-31T23:59:00Z")
        seen = ts.first_seen([snapshot(r2, at="2026-11-10T00:21:12Z"), snapshot(r2, at="2026-11-11T00:21:12Z"),
                              snapshot(r3, at="2026-11-12T00:21:12Z")])
        r = hormuz(ts.read(snapshot(r3, vis()), seen))
        self.assertFalse(r["signals"]["notice"]["present"])
        self.assertEqual(r["context"]["notices_renewal"][0]["renews"]["title"], "EASA CZIB-2026-04-R2: Airspace of Iran")
        self.assertTrue(any("scheduled renewal" in c for c in r["caveats"]))
        # The same revision two months before the old expiry changes the bulletin mid-term: a notice.
        early = czib("EASA CZIB-2026-04-R2: Airspace of Iran", "2027-01-13T23:59:00Z")
        seen = ts.first_seen([snapshot(early, at="2026-11-10T00:21:12Z"), snapshot(early, at="2026-11-11T00:21:12Z"),
                              snapshot(r3, at="2026-11-12T00:21:12Z")])
        r = hormuz(ts.read(snapshot(r3, vis()), seen))
        self.assertTrue(r["signals"]["notice"]["present"])
        self.assertEqual(r["context"]["notices_renewal"], [])

    def test_press_counts_violence_at_z_3_or_more_in_the_area_countries(self):
        """Gap 2: Watchfloor saw Bahrain on 13 and 15 Nov 2026."""
        r = hormuz(ts.read(snapshot(press(), vis())))
        self.assertTrue(r["signals"]["press"]["present"])
        self.assertEqual(r["signals"]["press"]["items"][0]["countries"], ["BH"])
        r = hormuz(ts.read(snapshot(press(z=2.59), press(measure="attention", z=3.84), vis())))
        self.assertFalse(r["signals"]["press"]["present"])
        self.assertEqual(len(r["context"]["press_other"]), 2)
        r = hormuz(ts.read(snapshot(press(cc="SA", fips="SA"), vis())))
        self.assertEqual((r["signals"]["press"]["present"], r["context"]["press_other"]), (False, []))
        old = press(at="2026-11-07T00:00:00Z")
        self.assertFalse(hormuz(ts.read(snapshot(old, vis())))["signals"]["press"]["present"], "outside look-back")

    def test_a_saturated_signal_counts_only_when_it_rises_above_its_own_rate(self):
        """Gap 7 (a): interference fired on every archived day, so it separated nothing."""
        def rows(n, interference):
            return [{"day": f"2026-11-{12 - i - 1:02d}" if 12 - i - 1 > 0 else f"2026-10-{31 + 12 - i - 1:02d}",
                     "area": "hormuz", "reading": "partial",
                     "counts": {"interference": interference, "notice": 0, "connectivity": 0, "press": 0}}
                    for i in range(n)]
        snap = snapshot(jam(), jam(n="2"), navwarn(), outage(), press(), vis())
        r = hormuz(ts.read(snap, None, None, rows(20, 2)))
        self.assertEqual(r["signals_at_usual_rate"], ["interference"])
        self.assertFalse(r["signals"]["interference"]["counts"])
        self.assertEqual(r["reading"], "partial")
        self.assertTrue(r["signals"]["interference"]["rate"]["saturated"])
        self.assertTrue(any("its own 90th percentile" in c for c in r["caveats"]))
        snap = snapshot(jam(), jam(n="2"), jam(n="3"), navwarn(), outage(), press(), vis())
        r = hormuz(ts.read(snap, None, None, rows(20, 2)))
        self.assertEqual((r["reading"], r["signals_at_usual_rate"]), ("converged", []))
        # Too few archived days: the rate is shown and every signal counts as it fires.
        r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), press(), vis()), None, None, rows(5, 2)))
        self.assertEqual((r["reading"], r["base_rates"]["enough"]), ("converged", False))
        self.assertTrue(any("Base rates need 14" in c for c in r["caveats"]))
        # Rows from the day itself or more than 30 days back never count.
        late = rows(20, 2) + [{"day": "2026-11-13", "area": "hormuz", "reading": "partial",
                               "counts": {"interference": 9, "notice": 0, "connectivity": 0, "press": 0}}]
        self.assertEqual(ts.signal_rates(late, "hormuz", "2026-11-13")["signals"]["interference"]["p90_count"], 2)
        self.assertEqual(ts.signal_rates(rows(20, 2), "hormuz", "2026-12-30")["days"], 0)

    def test_replay_rows_keep_raw_counts(self):
        got = ts.replay([snapshot(jam(), jam(n="2"), vis())])["rows"][0]
        self.assertEqual(got["counts"], {"interference": 2, "notice": 0, "connectivity": 0, "press": 0})

    def test_a_single_source_dip_or_an_outage_elsewhere_does_not_count(self):
        r = hormuz(ts.read(snapshot(outage(indicator="BO-IND-4"), outage("TN"), vis())))
        self.assertFalse(r["signals"]["connectivity"]["present"])
        self.assertEqual(len(r["context"]["single_source_dips"]), 1)

    def test_vis_is_context_and_never_a_signal(self):
        for light in ("green", "amber", "red"):
            r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), press(), vis(light))))
            self.assertEqual((r["reading"], r["weather"]["state"]), ("converged", light))
        r = hormuz(ts.read(snapshot(vis("green"))))
        self.assertEqual((r["reading"], r["signals_present"]), ("none", []))
        warn = vis("red")
        warn["properties"]["tier"] = "warning"
        self.assertEqual(hormuz(ts.read(snapshot(warn)))["signals_present"], [], "a Vis warning is still not a signal")

    def test_irans_ooni_reading_is_context_and_its_movement_is_measured(self):
        ooni = item("blackout", "blackout:ooni:ir:2026-11-13", ["IR"], "anomaly", "2026-11-13T00:00:00Z",
                    indicator_id="BO-IND-3", title="Iran: 44.0% OONI censorship anomaly rate")
        past = {"IR": [["2026-11-12T00:21:12Z", 37.0]]}
        r = hormuz(ts.read(snapshot(ooni, vis()), None, past))
        self.assertFalse(r["signals"]["connectivity"]["present"])
        self.assertEqual((r["context"]["ooni"][0]["points_above_lowest"], r["context"]["ooni"][0]["moved"]), (7.0, True))

    def test_no_probability_and_nothing_registered(self):
        v = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), press(), vis())))["verdict_draft"]
        self.assertIsNone(v["probability"])
        self.assertFalse(v["registered"])
        self.assertEqual(v["window_ends"], "2026-11-20T00:21:12Z")

    def test_a_whole_day_item_counts_to_the_end_of_its_day(self):
        """From 16 Nov 2026 Noisefloor's items say precision "day" with data_as_of at the day's midnight; older snapshots
        carry T23:59:59Z. Both read the same: the day counts until its end, so the 96-hour look-back is unchanged."""
        for hours_after_day_end, seen in ((95, True), (97, False)):
            at = f"2026-11-{12 + (hours_after_day_end // 24):02d}T{hours_after_day_end % 24:02d}:00:00Z"
            old = jam()
            new = jam(at="2026-11-11T00:00:00Z")
            new["properties"]["data_as_of_precision"] = "day"
            for label, f in (("old", old), ("new", new)):
                found = hormuz(ts.read(snapshot(f, navwarn(), outage(), vis(), at=at)))["signals"]["interference"]["present"]
                self.assertEqual(found, seen, (label, at))

    def test_replay_counts_only_days_with_every_layer(self):
        a = snapshot(jam(at="2026-11-10T23:59:59Z"), vis(), at="2026-11-11T00:21:12Z", states={"blackout": "missing"})
        b = snapshot(jam(), vis())
        got = ts.replay([a, b])["by_area"]["hormuz"]
        self.assertEqual((got["days"], got["days_with_every_layer"]), (2, 1))
        self.assertEqual(got["days_each_signal_present"]["interference"], 1)
        self.assertEqual(got["days_by_reading"]["incomplete"], 1)


    def test_tracklines_and_collections_are_placed_by_every_vertex(self):
        # Cordon draws NGA tracklines as lines, and a warning with an area and a trackline as a collection.
        multi = {"type": "MultiLineString", "coordinates": [[[56, 26], [57, 26.5]], [[58, 25], [59, 24]]]}
        self.assertEqual(ts._points(multi), [[56, 26], [57, 26.5], [58, 25], [59, 24]])
        area = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]]}
        line = {"type": "LineString", "coordinates": [[177.0, 53.0], [180.5, 53.5], [184.0, 54.0]]}
        gc = {"type": "GeometryCollection", "geometries": [area, line]}
        self.assertEqual(ts._points(gc), ts._points(area) + ts._points(line))
        self.assertEqual(ts._points({"type": "GeometryCollection", "geometries": []}), [])
        # Longitudes past 180 (kept continuous across the antimeridian) still measure the short way round.
        self.assertLess(ts.km_to_area(line, {"center": [-176.0, 54.0]}), 1)


class Published(unittest.TestCase):
    def test_the_gate_lets_it_publish(self):
        doc = ts.read(snapshot(jam(), navwarn(), outage(), press(), vis()))
        v = gate.review(ts.JOIN, doc)
        self.assertEqual((v["review_class"], v["may_publish"]), ("open", True), v["reasons"])
        self.assertEqual(gate.review(ts.JOIN, ts.public(doc))["may_publish"], True)

    def test_the_public_record_has_the_reading_the_base_rate_and_a_draft_question_only(self):
        snaps = [snapshot(jam(), vis(), at="2026-11-12T00:21:12Z"), snapshot(jam(), navwarn(), outage(), press(), vis())]
        doc = ts.read(snaps[-1])
        doc["archive_replay"] = ts.replay(snaps)
        pub = ts.public(doc)
        r = pub["records"][0]
        self.assertEqual(r["reading"], "converged")
        self.assertIn("Over 2 archived days with every layer current", r["base_rate"]["text"])
        self.assertIn("all four fell within the window on 1", r["base_rate"]["text"])
        self.assertEqual(r["base_rate"]["days_by_reading"]["converged"], 1)
        q = r["question_draft"]
        self.assertEqual(q["status"], "draft, not registered")
        self.assertNotIn("probability", q)
        self.assertNotIn("verdict_draft", r)
        self.assertIn("not a warning", pub["not_a_warning"])

    def test_the_voice_rule_no_inference_in_cops_own_words(self):
        # Tom, 15 Nov 2026: "That doesn't mean we have to spell it out." The record shows what lines up and how often;
        # it never names an operation, an intent or a window for one in COP's voice.
        doc = ts.read(snapshot(jam(), navwarn(), outage(), press(), vis()))
        pub = ts.public(doc)
        r = pub["records"][0]
        own_words = " ".join([pub["what"], pub["not_a_warning"], r["reading_text"], r["weather"]["text"],
                              *r["caveats"], *ts.READINGS.values()]).lower()
        for phrase in ("workable", "operation", "would allow", "strike window", "likely", "intent", "imminent"):
            self.assertNotIn(phrase, own_words, phrase)

    def test_it_writes_the_public_file_only_to_site_data_and_its_working_file_outside_deployed_folders(self):
        snap = Path(tempfile.mkdtemp()) / "s.geojson"
        snap.write_text(json.dumps(snapshot(jam(), vis())), encoding="utf-8")
        for folder in (ROOT / "site" / "data", ROOT / "functions", ROOT / "mcp"):
            with self.assertRaises(SystemExit, msg=str(folder)):
                ts.main(["--snapshot", str(snap), "--out", str(folder)])
        with self.assertRaises(SystemExit):
            ts.main(["--snapshot", str(snap), "--out", tempfile.mkdtemp(), "--publish", str(ROOT / "site")])
        out, pub = tempfile.mkdtemp(), tempfile.mkdtemp()
        ts.main(["--snapshot", str(snap), "--out", out, "--publish", pub])
        got = json.loads((Path(pub) / ts.PUBLIC_NAME).read_text(encoding="utf-8"))
        self.assertEqual(got["gate"]["may_publish"], True)
        self.assertTrue((Path(out) / "latest.json").exists())

    def test_the_build_the_page_and_the_mcp_server_read_it(self):
        build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/three_signal.py --snapshot site/data/today.geojson", build)
        self.assertLess(build.index("tools/three_signal.py"), build.index("python tools/gate.py --check"))
        self.assertIn("data/three-signal.json", (ROOT / "site/cop.js").read_text(encoding="utf-8"))
        self.assertIn("three-signal-sec", (ROOT / "site/index.html").read_text(encoding="utf-8"))
        self.assertIn("/data/three-signal.json", (ROOT / "mcp/data.js").read_text(encoding="utf-8"))
        self.assertIn('"three_signal"', (ROOT / "mcp/server.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
