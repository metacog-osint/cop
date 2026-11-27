"""Outage triage for US state power outages (Blackout's BO-IND-14): python tests/test_triage_us_power.py

Every test replays real recorded items (tests/fixtures/us_power, see its MANIFEST.json): Blackout's own code on
ODIN's recorded responses, and COP's own archived snapshots. Rules approved 13 Nov 2026.
"""

import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import triage as tri  # noqa: E402
import triage_rates as rates  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "us_power"
# Verdict's auto-resolve (verdict tools/auto_resolve.py, EAGLEI_RE, Verdict f4e6a38 and e7989ae), copied: the drafted
# question's rule must be text Verdict settles without a person rewriting it.
EAGLEI_RE = re.compile(
    r"EAGLE-I's outage history \(ORNL, figshare doi:10\.6084/m9\.figshare\.24237376\) shows (?P<name>[A-Za-z .]+?) "
    r"\((?P<st>[A-Z]{2})\) with more than (?P<line>[\d,]+) customers out, its counties summed in each 15-minute "
    r"run, in 4 or more runs with no more than 3 runs under the line between them, between "
    r"(?P<a>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) and (?P<b>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\."
)
AK_BLIZZARD = "elements:nws:PAFG.BZ.W.0037.2026"
CA_WARNINGS = {"elements:nws:KMFR.HW.W.0005.2026", "elements:nws:KSTO.WS.W.0005.2026",
               "elements:nws:KREV.WS.W.0004.2026", "elements:nws:KHNX.WS.W.0006.2026"}


def fixture(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def blackout_items():
    """Blackout's items as COP's bus carries them: snapshot.py tags each with its program and a stale flag."""
    items = fixture("blackout-us-power.json")["features"]
    for f in items:
        f["properties"].update({"cop_program": "blackout", "cop_stale": False})
    return items


def day_snapshot(states=None, extra=()):
    day = fixture("cop-2026-11-13-us.json")
    health = {k: dict(v) for k, v in day["cop_programs"].items()}
    for k, v in (states or {}).items():
        health[k] = {"state": v}
    return {"generated_at": day["generated_at"], "cop_programs": health,
            "features": day["features"] + blackout_items() + list(extra)}


def volcano_snapshots():
    return [{"generated_at": d["generated_at"], "cop_programs": d["cop_programs"], "features": d["features"]}
            for d in fixture("cop-volcano-days.json")]


def by_state(doc):
    return {r["us_state"]: r for r in doc["records"] if r["outage_kind"] == "power"}


def ids(rows):
    return {r["id"] for r in rows}


class RecordedItems(unittest.TestCase):
    def test_the_items_are_blackouts_state_outages(self):
        items = blackout_items()
        self.assertEqual([tri.us_state_of(f["properties"]) for f in items], ["CA", "NY"])
        for f in items:
            p = f["properties"]
            self.assertEqual((p["indicator_id"], p["countries"], f["geometry"]["type"]), ("BO-IND-14", ["US"], "Point"))
            self.assertTrue(p["baseline_ref"]["base_rate"]["text"].startswith("In EAGLE-I's record for 2018-2025"))
        self.assertTrue((FX / "MANIFEST.json").exists())

    def test_county_table_is_blackouts(self):
        table = tri.county_table()
        self.assertEqual(len(table), 3222)
        self.assertEqual(table["06037"][:2], ["CA", "Los Angeles County"])
        self.assertEqual(len(tri.us_states()), 52)  # 50 states, DC and Puerto Rico


class StatePlace(unittest.TestCase):
    def setUp(self):
        self.doc = tri.triage(day_snapshot())
        self.rec = by_state(self.doc)

    def test_a_state_outage_reads_against_its_own_state(self):
        ca, ny = self.rec["CA"], self.rec["NY"]
        self.assertEqual((ca["reading"], ny["reading"]), ("natural", "open"))
        self.assertEqual(ids(ca["layers"]["elements"]["significant"]), CA_WARNINGS)
        self.assertTrue(all(h["placed"] == "county" for h in ca["layers"]["elements"]["significant"]))
        self.assertEqual(ny["layers"]["elements"]["significant"], [])
        self.assertEqual((ca["country"], ca["us_state_name"], ca["outage_kind"]), ("US", "California", "power"))

    def test_alaskas_blizzard_no_longer_reads_for_the_whole_country(self):
        at = tri.utc(self.rec["NY"]["outage"]["data_as_of"])
        features = day_snapshot()["features"]
        # Under the country rule an outage anywhere in the US would have found Alaska's Blizzard Warning ...
        self.assertIn(AK_BLIZZARD, ids(tri.hazards(features, "US", at)["significant"]))
        # ... at state level it sits under neither state, only under Alaska.
        for st in ("CA", "NY"):
            self.assertNotIn(AK_BLIZZARD, ids(self.rec[st]["layers"]["elements"]["significant"]))
        self.assertIn(AK_BLIZZARD, ids(tri.state_hazards(features, "AK", at)["significant"]))

    def test_power_warnings_count_at_severe_only_for_power_outages(self):
        hw = "elements:nws:KMFR.HW.W.0005.2026"
        ca = self.rec["CA"]["layers"]["elements"]["significant"]
        self.assertEqual(next(h for h in ca if h["id"] == hw)["power_hazard"], "High Wind")
        # Elements files it as routine (CAP Severe), so for an internet outage it stays minor.
        at = tri.utc(self.rec["CA"]["outage"]["data_as_of"])
        self.assertIn(hw, ids(tri.hazards(day_snapshot()["features"], "US", at)["minor"]))

    def test_floods_freezes_and_marine_warnings_do_not_count(self):
        at = tri.utc(self.rec["CA"]["outage"]["data_as_of"])
        features = day_snapshot()["features"]
        az = tri.state_hazards(features, "AZ", at)
        self.assertIn("elements:nws:KPSR.FA.W.0044.2026", ids(az["minor"]))     # Flood Warning, Maricopa and Yuma
        self.assertEqual(az["significant"], [])
        freezes = [f["properties"] for f in features if f["properties"]["title"].startswith("Freeze Warning")]
        self.assertTrue(freezes)
        for p in freezes:   # real Freeze Warnings: routine, and not on the power list
            self.assertEqual((p["tier"], tri._power_warning(p)), ("routine", None))
        # Heavy Freezing Spray Warnings name marine zones, not counties: not placed, and not significant, so not listed.
        ak = tri.state_hazards(features, "AK", at)
        self.assertFalse([h for h in ak["unplaced"] + ak["minor"] if "Freezing Spray" in h["title"]])

    def test_a_warning_begins_at_its_onset_not_its_latest_update(self):
        ws = next(h for h in self.rec["CA"]["layers"]["elements"]["significant"]
                  if h["id"] == "elements:nws:KSTO.WS.W.0005.2026")
        # Sent 20:12 UTC 12 Nov (18.5 hours before the reading), onset 18:00 UTC 13 Nov (3.3 hours after it).
        self.assertEqual(ws["data_as_of"], "2026-11-12T20:12:00Z")
        self.assertEqual(ws["hours_before_outage"], -3.3)
        listed = ids(sum((self.rec["CA"]["layers"]["elements"][k] for k in ("significant", "minor", "standing")), []))
        self.assertNotIn("elements:nws:KVEF.WS.W.0004.2026", listed)   # onset 21:00, 6.3 hours after: past the window
        self.assertNotIn("elements:nws:KLOX.CF.W.0002.2026", listed)   # onset 11:00 the next day

    def test_a_point_counts_within_100_km_and_inside_the_state(self):
        features = {f["properties"]["id"]: f for f in day_snapshot()["features"]}
        flood = features["elements:gdacs:FL-1103888"]["geometry"]       # GDACS flood at the Four Corners
        self.assertLessEqual(tri.km_to_state(flood, "NM"), tri.STATE_NEAR_KM)
        self.assertIsNone(tri.km_to_state(flood, "CA"))
        oregon = features["elements:usgs:us7000txrk"]                     # M4.5 off the coast of Oregon
        self.assertIsNone(tri._place(oregon, "OR", None))                # offshore, beyond 100 km
        kilauea = features["elements:usgs-volcano:332010"]
        self.assertEqual(tri._place(kilauea, "HI", None)["placed"], "near")

    def test_a_point_inside_a_vast_county_belongs_to_its_state(self):
        sitkin = next(f for d in volcano_snapshots() for f in d["features"]
                      if f["properties"]["id"] == "elements:usgs-volcano:311120")
        # Great Sitkin is inside the Aleutians West Census Area, 292 km from that area's internal point.
        self.assertGreater(tri.km_to_state(sitkin["geometry"], "AK"), tri.STATE_NEAR_KM)
        self.assertEqual(tri._place(sitkin, "AK", None), {"placed": "in", "km_from_state": 292})
        self.assertIsNone(tri._place(sitkin, "HI", None))

    def test_country_wide_items_are_never_placed_in_a_state(self):
        features = {f["properties"]["id"]: f for f in day_snapshot()["features"]}
        # FIRMS and the Drought Monitor count the whole US at its label point, which falls in Kansas.
        for item_id in ("elements:firms:US", "elements:usdm:us"):
            self.assertEqual(tri._place(features[item_id], "KS", None), {"placed": "country"})

    def test_spc_risk_is_background_over_the_outage_point_only(self):
        day = next(d for d in fixture("cop-spc-days.json")
                   if any(f["properties"]["id"].endswith(":enh") for f in d["features"]))
        enh = next(f for f in day["features"] if f["properties"]["id"].endswith(":enh"))
        inside = next((st, lat, lon) for st, _n, lat, lon in tri.county_table().values()
                      if tri._in_polygon(lon, lat, enh["geometry"]))
        st, lat, lon = inside
        at = tri.utc(day["generated_at"])
        over = tri.state_hazards(day["features"], st, at, [lon, lat])
        self.assertIn(enh["properties"]["id"], ids(over["background"]))
        self.assertEqual(over["significant"], [])
        self.assertEqual(tri.state_hazards(day["features"], st, at, None)["background"], [])


class Readings(unittest.TestCase):
    def test_cordon_and_watchfloor_are_not_covered_and_never_deliberate(self):
        doc = tri.triage(day_snapshot())
        for r in by_state(doc).values():
            for name in ("cordon", "watchfloor"):
                L = r["layers"][name]
                self.assertEqual((L["covered"], L["found"]), (False, False))
                self.assertTrue(tri.showed(name, L).startswith("Not covered"))
            self.assertIn(r["reading"], tri.POWER_READINGS)
            self.assertNotEqual(r["reading"], "deliberate")
        # The snapshot does carry Cordon notices and Watchfloor items, for other countries.
        progs = {f["properties"]["cop_program"] for f in day_snapshot()["features"]}
        self.assertTrue({"cordon", "watchfloor"} <= progs)

    def test_only_elements_going_dark_makes_a_state_outage_incomplete(self):
        quiet_others = by_state(tri.triage(day_snapshot({"cordon": "stale", "watchfloor": "missing"})))
        self.assertEqual(quiet_others["CA"]["reading"], "natural")
        dark = by_state(tri.triage(day_snapshot({"elements": "stale"})))
        self.assertEqual(dark["CA"]["reading"], "incomplete")
        self.assertEqual(dark["CA"]["no_current_data"], ["elements"])
        self.assertEqual(tri.showed("elements", dark["CA"]["layers"]["elements"]), "No current data.")

    def test_wording_and_caveats(self):
        ca = by_state(tri.triage(day_snapshot()))["CA"]
        self.assertIn("4 hazards that take power lines down began", tri.showed("elements", ca["layers"]["elements"]))
        for c in tri.POWER_CAVEATS:
            self.assertIn(c, ca["caveats"])
        self.assertTrue(any("No archive baseline" in c for c in ca["caveats"]))
        ny = by_state(tri.triage(day_snapshot(), {"volcano_levels": {}}))["NY"]
        self.assertTrue(tri.showed("elements", ny["layers"]["elements"]).startswith("Nothing found"))
        self.assertFalse(any("No archive baseline" in c for c in ny["caveats"]))


class Volcanoes(unittest.TestCase):
    """USGS re-sends each volcano's status daily; only a raise is a new hazard (every country, 13 Nov 2026)."""

    def setUp(self):
        self.snaps = volcano_snapshots()
        self.hist = rates.history(self.snaps)

    def significant(self, snap, levels):
        return [h["title"].split(":")[0] for h in
                tri.hazards(snap["features"], "US", tri.utc(snap["generated_at"]), levels)["significant"]]

    def test_the_archive_holds_each_volcanos_levels(self):
        kilauea = dict(self.hist["volcano_levels"]["elements:usgs-volcano:332010"])
        self.assertEqual(kilauea["2026-11-03T00:06:36Z"], 22)   # Watch / Orange
        self.assertEqual(kilauea["2026-11-07T18:24:50Z"], 11)   # Advisory / Yellow

    def test_a_resend_at_the_same_level_is_standing(self):
        with_levels = {s["generated_at"][:10]: self.significant(s, self.hist["volcano_levels"]) for s in self.snaps}
        without = {s["generated_at"][:10]: self.significant(s, None) for s in self.snaps}
        # Without the archive every daily re-send looked new: the US had a "new" volcano on 13 of 14 days.
        self.assertEqual(sum(bool(v) for v in without.values()), 13)
        # With it: the first day COP saw each volcano (no earlier level: counted, marked unknown), then Kilauea's
        # raise on 3 Nov for the 72 hours after it, and nothing else.
        self.assertEqual({d: v for d, v in with_levels.items() if v},
                         {"2026-11-01": ["Kilauea", "Great Sitkin", "Shishaldin"], "2026-11-03": ["Kilauea"],
                          "2026-11-04": ["Kilauea"], "2026-11-05": ["Kilauea"]})

    def test_a_raise_counts_from_when_it_was_raised(self):
        snap = next(s for s in self.snaps if s["generated_at"].startswith("2026-11-05"))
        hi = tri.state_hazards(snap["features"], "HI", tri.utc(snap["generated_at"]), None,
                               self.hist["volcano_levels"])
        k = hi["significant"][0]
        self.assertEqual((k["level_change"], k["raised_at"]), ("raised", "2026-11-03T00:06:36Z"))
        late = next(s for s in self.snaps if s["generated_at"].startswith("2026-11-06"))
        hi = tri.state_hazards(late["features"], "HI", tri.utc(late["generated_at"]), None,
                               self.hist["volcano_levels"])
        self.assertEqual((hi["significant"], [h["level_change"] for h in hi["standing"]]), ([], ["raised"]))

    def test_a_lowered_alert_is_standing(self):
        snap = next(s for s in self.snaps if s["generated_at"].startswith("2026-11-07"))
        haz = tri.hazards(snap["features"], "US", tri.utc(snap["generated_at"]), self.hist["volcano_levels"])
        kilauea = next(h for h in haz["standing"] if h["title"].startswith("Kilauea"))
        self.assertEqual(kilauea["level_change"], "lowered")


class Question(unittest.TestCase):
    def setUp(self):
        self.ca = by_state(tri.triage(day_snapshot()))["CA"]

    def test_the_rule_is_the_text_verdict_settles(self):
        v = self.ca["verdict_draft"]
        m = EAGLEI_RE.search(v["rule_draft"])
        self.assertIsNotNone(m, v["rule_draft"])
        self.assertEqual((m["name"], m["st"], m["line"], m["a"], m["b"]),
                         ("California", "CA", "50,000", "2026-11-14 14:40", "2026-11-14 16:40"))
        self.assertEqual(v["question"], "Will California still have more than 50,000 customers out 24 hours after "
                                        "ODIN's reading of 2026-11-13T14:40:05Z?")
        self.assertEqual((v["window_ends"], v["known_at"]), ("2026-11-14T16:40:00Z", "2027-04-30T00:00:00Z"))
        self.assertEqual((v["registered"], v["probability"]), (False, None))

    def test_same_text_as_blackouts_rule(self):
        # Blackout's tests/test_lanes.py holds this exact text for eaglei.verdict_rule("CA", 8 Jan 12:00, 9 Jan 12:00,
        # 500,000); Verdict's test holds the same.
        r = tri.eaglei_rule("CA", "California", datetime(2025, 1, 8, 12, tzinfo=timezone.utc),
                            datetime(2025, 1, 9, 12, tzinfo=timezone.utc), 500_000)
        self.assertEqual(r, (
            "Yes if EAGLE-I's outage history (ORNL, figshare doi:10.6084/m9.figshare.24237376) shows California (CA) "
            "with more than 500,000 customers out, its counties summed in each 15-minute run, in 4 or more runs with "
            "no more than 3 runs under the line between them, between 2025-01-08 12:00 and 2025-01-09 12:00 UTC. No "
            "otherwise."))

    def test_the_reference_is_the_states_eaglei_rate(self):
        v = self.ca["verdict_draft"]
        self.assertEqual(v["reference"], "EAGLE-I, 2018-2025: half of California's outages over the line lasted "
                                         "about 5.5 hours or less, 1 in 10 more than about 43.3 hours.")
        self.assertTrue(any("leans Yes" in n for n in v["notes"]))
        self.assertEqual(self.ca["eaglei"]["per_year"], 16.62)
        self.assertIn("92% of US customers", self.ca["eaglei"]["caveat"])


class RatesAndPublic(unittest.TestCase):
    def test_state_rate_counts_one_long_hazard_once_and_says_none(self):
        first, second = day_snapshot(), day_snapshot()
        second["generated_at"] = "2026-11-14T23:30:00Z"
        s = rates.rates([first, second])["state_background"]
        ev = s["by_state_events"]
        self.assertEqual(ev["CA"], {"days": 2, "hazards": 5}, "the same five hazards on two days count five times, not ten")
        self.assertEqual(s["states_with_signal"], 4)
        self.assertNotIn("_events", s["days"][0])
        r = {"reading": "natural", "us_state": "TX", "us_state_name": "Texas"}
        tx = tri._state_rate(r, s)
        self.assertIn("on none of 2 days.", tx["text"])
        self.assertNotIn("from", tx["text"].split(".")[0])

    def test_state_rate_leaves_out_todays_partial_day(self):
        first, today = day_snapshot(), day_snapshot()
        today["generated_at"] = "2026-11-15T01:53:00Z"
        doc = rates.rates([first, today], today="2026-11-15")
        self.assertEqual(doc["state_background"]["by_state_days"], 1)
        self.assertEqual(doc["archive"]["days_left_out_partial"], ["2026-11-15"])

    def test_state_background_from_the_archive(self):
        doc = rates.rates([day_snapshot()])
        s = doc["state_background"]
        # Alaska's blizzard, California's and Nevada's winter storm; Hawaii because a one-day archive has no earlier
        # level for Kilauea, so its alert counts, marked unknown (over the 14 archived days it is a re-send).
        self.assertEqual(s["days"][0]["natural_states"], ["AK", "CA", "HI", "NV"])
        self.assertEqual((s["state_days"], s["would_read"]["natural"]), (52, round(4 / 52, 4)))
        self.assertTrue(any("1 of 1 days have no list of Elements' ended hazards" in c for c in s["cautions"]))
        self.assertIn("volcano_levels", doc["history"])
        replay = {o["us_state"]: o["reading"] for o in doc["replay"]["rows"] if o["us_state"]}
        self.assertEqual(replay, {"CA": "natural", "NY": "open"})

    def test_public_record_passes_the_gate_and_carries_no_probability(self):
        snap = day_snapshot()
        r = rates.rates([snap])
        pub = tri.public(tri.attach_rates(tri.triage(snap, r["history"]), r))
        ca = by_state(pub)["CA"]
        self.assertEqual(ca["base_rate"]["text"], "In California, a state power outage would have read \"natural\" "
                                                  "whatever its cause on 1 of 1 days, from 5 hazards that take power "
                                                  "lines down. Across all 52 states, DC and Puerto Rico included, 7.7% "
                                                  "of 52 state-days.")
        self.assertEqual((ca["base_rate"]["this_states_days"], ca["base_rate"]["events"]), (1, {"days": 1, "hazards": 5}))
        self.assertIn(rates.HIT_RATE_CAUTION, ca["base_rate"]["cautions"])
        q = ca["question_draft"]
        self.assertEqual(q["status"], "draft, not registered")
        self.assertTrue(q["reference"] and q["notes"])
        self.assertNotIn("probability", json.dumps(pub["records"]))   # no probability field, as the MCP test checks
        self.assertEqual(pub["base_rates"]["states"]["state_days"], 52)
        self.assertEqual(pub["power_readings"], tri.POWER_READINGS)
        verdict = gate.review(tri.JOIN, pub)
        self.assertEqual(verdict["review_class"], "open", verdict)
        self.assertIn("blackout:us-power:ca:2026-11-13", gate.cited_ids(pub))

    def test_the_build_runs_these_tests(self):
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("python tests/run_all.py", build)
        self.assertIn('"test_triage_us_power.py"', (ROOT / "tests" / "run_all.py").read_text(encoding="utf-8"))


def county_items():
    """The same two items made by Blackout's code at 2ea3a4d, which lists the counties with meters out."""
    items = fixture("blackout-us-power-counties.json")["features"]
    for f in items:
        f["properties"].update({"cop_program": "blackout", "cop_stale": False})
    return items


def county_snapshot(states=None):
    snap = day_snapshot(states)
    snap["features"] = [f for f in snap["features"] if f["properties"].get("cop_program") != "blackout"] + county_items()
    return snap


class CountyMatch(unittest.TestCase):
    """14 Nov 2026: the county-level match. Real items: Blackout's own code on ODIN's recorded
    responses lists 10 California counties (Los Angeles 11,231 of 13,723 meters) and 32 in New York."""

    def setUp(self):
        self.rec = by_state(tri.triage(county_snapshot()))
        self.ca = self.rec["CA"]["layers"]["elements"]

    def test_the_items_list_their_counties(self):
        ca, ny = (f["properties"] for f in county_items())
        self.assertEqual((len(ca["us_counties"]), len(ny["us_counties"])), (10, 32))
        self.assertEqual(ca["us_counties"][0], {"fips": "06037", "name": "Los Angeles County", "meters": 11231})
        codes = tri.outage_counties(ca, "CA")
        self.assertEqual(codes, sorted(r["fips"] for r in ca["us_counties"]))
        self.assertIn("06037", codes)
        self.assertIsNone(tri.outage_counties(ca, "NY"))      # a county outside the item's state never counts
        self.assertIsNone(tri.outage_counties({"us_counties": []}, "CA"))

    def test_only_warnings_over_the_outage_counties_count(self):
        self.assertEqual(self.ca["scope"], "counties")
        sig = {h["id"]: h for h in self.ca["significant"]}
        # Medford's High Wind Warning names Siskiyou (06093), Hanford's Winter Storm Warning names Fresno (06019):
        # both counties have meters out. Sacramento's and Reno's warnings name no outage county.
        self.assertEqual(set(sig), {"elements:nws:KMFR.HW.W.0005.2026", "elements:nws:KHNX.WS.W.0006.2026"})
        self.assertEqual(sig["elements:nws:KMFR.HW.W.0005.2026"]["outage_counties"], ["06093"])
        self.assertEqual({h["id"] for h in self.ca["elsewhere"]},
                         {"elements:nws:KSTO.WS.W.0005.2026", "elements:nws:KREV.WS.W.0004.2026"})
        self.assertTrue(all(h["bucket"] == "significant" for h in self.ca["elsewhere"]))
        self.assertEqual(CA_WARNINGS, set(sig) | {h["id"] for h in self.ca["elsewhere"]})
        self.assertEqual((self.rec["CA"]["reading"], self.rec["NY"]["reading"]), ("natural", "open"))

    def test_the_reading_says_how_much_of_the_outage_the_hazards_cover(self):
        mu = self.ca["meters_under_hazards"]
        self.assertEqual(mu["of"], 13723)
        self.assertLess(mu["share"], 0.5)
        self.assertEqual(mu["largest_without"]["name"], "Los Angeles County")
        caveats = " ".join(self.rec["CA"]["caveats"])
        self.assertIn("Los Angeles County, with 11,231, has none.", caveats)
        self.assertIn("elsewhere in California", caveats)
        self.assertIn("overstates how often an outage would read natural", caveats)
        self.assertIn("in or near the outage's counties", tri.showed("elements", self.ca))

    def test_an_item_without_counties_keeps_the_state_rule(self):
        ca = by_state(tri.triage(day_snapshot()))["CA"]
        self.assertEqual(ca["layers"]["elements"]["scope"], "state")
        self.assertNotIn("elsewhere", ca["layers"]["elements"])
        self.assertEqual(ids(ca["layers"]["elements"]["significant"]), CA_WARNINGS)
        self.assertIn("lists no counties", " ".join(ca["caveats"]))

    def test_elements_dark_is_still_incomplete(self):
        rec = by_state(tri.triage(county_snapshot({"elements": "stale"})))
        self.assertEqual(rec["CA"]["reading"], "incomplete")

    def test_the_public_record_carries_the_match_and_passes_the_gate(self):
        snap = county_snapshot()
        r = rates.rates([snap])
        pub = tri.public(tri.attach_rates(tri.triage(snap, r["history"]), r))
        rec = [r for r in pub["records"] if r.get("us_state") == "CA"][0]
        self.assertEqual(rec["layers"]["elements"]["scope"], "counties")
        self.assertEqual(len(rec["layers"]["elements"]["outage_counties"]), 10)
        verdict = gate.review(tri.JOIN, pub)
        self.assertEqual(verdict["review_class"], "open", verdict)
        self.assertEqual(tri.JOIN["version"], "0.9")


if __name__ == "__main__":
    unittest.main()
