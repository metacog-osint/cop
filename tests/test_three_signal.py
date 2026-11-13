"""The held three-signal join: python tests/test_three_signal.py"""

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


def vis(light="green"):
    return item("vis", "vis:2026-11-13:zone:11_Iran_Red_Sea", ["IR", "YE"], "routine", "2026-11-13T00:10:00Z", BANDAR,
                measures={"station": "Bandar Abbas", "stoplight_72h": light, "dark_low_light_min": 685,
                          "moon_lit": 0.18})


def hormuz(doc):
    return next(r for r in doc["records"] if r["area"] == "hormuz")


class ThreeSignal(unittest.TestCase):
    def test_all_three_inside_the_window_converge(self):
        r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), vis())))
        self.assertEqual(r["reading"], "converged")
        self.assertEqual(r["signals_present"], list(ts.SIGNALS))
        self.assertLessEqual(r["tightest_span"]["hours"], ts.WINDOW_H)
        self.assertEqual(r["weather_window"]["state"], "workable")

    def test_two_of_three_is_partial_and_drafts_no_question(self):
        r = hormuz(ts.read(snapshot(jam(), navwarn(), vis())))
        self.assertEqual(r["reading"], "partial")
        self.assertIsNone(r["verdict_draft"])

    def test_three_present_but_more_than_the_window_apart_is_partial(self):
        r = hormuz(ts.read(snapshot(jam(at="2026-11-09T06:00:00Z"), navwarn(at="2026-11-09T12:00:00Z"),
                                    outage(at="2026-11-12T18:30:00Z"), vis())))
        self.assertEqual(r["signals_present"], list(ts.SIGNALS))
        self.assertGreater(r["tightest_span"]["hours"], ts.WINDOW_H)
        self.assertEqual(r["reading"], "partial")

    def test_nothing_is_none_and_a_dead_layer_is_incomplete(self):
        self.assertEqual(hormuz(ts.read(snapshot(vis())))["reading"], "none")
        r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), vis(), states={"blackout": "stale"})))
        self.assertEqual((r["reading"], r["no_current_data"]), ("incomplete", ["blackout"]))

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

    def test_a_single_source_dip_or_an_outage_elsewhere_does_not_count(self):
        r = hormuz(ts.read(snapshot(outage(indicator="BO-IND-4"), outage("TN"), vis())))
        self.assertFalse(r["signals"]["connectivity"]["present"])
        self.assertEqual(len(r["context"]["single_source_dips"]), 1)

    def test_vis_is_a_condition_and_never_a_signal(self):
        for light, state in (("green", "workable"), ("amber", "marginal"), ("red", "against")):
            r = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), vis(light))))
            self.assertEqual((r["reading"], r["weather_window"]["state"]), ("converged", state))
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
        v = hormuz(ts.read(snapshot(jam(), navwarn(), outage(), vis())))["verdict_draft"]
        self.assertIsNone(v["probability"])
        self.assertFalse(v["registered"])
        self.assertEqual(v["window_ends"], "2026-11-20T00:21:12Z")

    def test_replay_counts_only_days_with_every_layer(self):
        a = snapshot(jam(at="2026-11-10T23:59:59Z"), vis(), at="2026-11-11T00:21:12Z", states={"blackout": "missing"})
        b = snapshot(jam(), vis())
        got = ts.replay([a, b])["by_area"]["hormuz"]
        self.assertEqual((got["days"], got["days_with_every_layer"]), (2, 1))
        self.assertEqual(got["days_each_signal_present"]["interference"], 1)
        self.assertEqual(got["days_by_reading"]["incomplete"], 1)


class Held(unittest.TestCase):
    def test_the_gate_holds_this_join_and_its_output(self):
        doc = ts.read(snapshot(jam(), navwarn(), outage(), vis()))
        v = gate.review(ts.JOIN, doc)
        self.assertEqual((v["review_class"], v["may_publish"]), ("held", False))
        self.assertEqual(gate.review(ts.JOIN | {"published": True}, doc)["may_publish"], False)

    def test_its_default_folder_is_under_out_held_and_git_ignores_it(self):
        self.assertIsNone(gate.allowed(gate.review(ts.JOIN), gate.HELD_DIR / "three-signal"))
        self.assertIn("out/", (ROOT / ".gitignore").read_text(encoding="utf-8").split())

    def test_it_cannot_be_written_into_a_deployed_folder_or_plain_out(self):
        snap = Path(tempfile.mkdtemp()) / "s.geojson"
        snap.write_text(json.dumps(snapshot(jam(), vis())), encoding="utf-8")
        for folder in (ROOT / "site" / "data", ROOT / "functions", ROOT / "mcp", ROOT / "out" / "triage"):
            with self.assertRaises(SystemExit, msg=str(folder)):
                ts.main(["--snapshot", str(snap), "--out", str(folder)])

    def test_the_build_the_page_and_the_mcp_server_do_not_know_about_it(self):
        for rel in ("tools/snapshot.py", "tools/agent_index.py", "site/cop.js", "site/index.html", "functions/mcp.js",
                    "mcp/tools.js", "mcp/data.js", "mcp/server.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("three_signal", text, rel)
            self.assertNotIn("three-signal", text, rel)
        build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertNotIn("tools/three_signal", build)


if __name__ == "__main__":
    unittest.main()
