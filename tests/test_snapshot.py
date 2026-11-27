"""Offline tests for COP's snapshot builder and design rules. Run as a plain script: python tests/test_snapshot.py"""

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)
from tic_doctrine import lint as kitlint  # noqa: E402  (snapshot.py put the vendored kit on sys.path)
spec = importlib.util.spec_from_file_location("card", ROOT / "tools" / "card.py")
card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)

NOW = datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc)
IDS = [p["id"] for p in snap.CONFIG["programs"]]


def feeds(dead=()):
    out, reps = {}, {}
    for pid in IDS:
        if pid in dead:
            out[pid], reps[pid] = None, {"ok": False, "status": None, "seconds": 0, "error": "test"}
        else:
            out[pid], reps[pid] = snap.read_local(FIX / f"{pid}.geojson")
    return out, reps


def build(dead=(), previous=None, now=NOW, mutate=None):
    f, r = feeds(dead)
    if mutate:
        mutate(f)
    return snap.build(f, r, previous, now)


def health_of(health, pid):
    return next(h for h in health["programs"] if h["id"] == pid)


def luminance(hex_):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    h = hex_.lstrip("#")
    r, g, b = (ch(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Build(unittest.TestCase):
    def test_all_fresh_and_linted(self):
        today, picks, health, _ = build()
        for pid in IDS:
            h = health_of(health, pid)
            self.assertIn(h["state"], ("fresh", "late"), pid)
            self.assertEqual(h["lint"]["errors"], 0, (pid, h["lint"]["examples"]))
            self.assertTrue(h["attribution"], pid)
        self.assertEqual(len(today["features"]), sum(health_of(health, p)["items"] for p in IDS))

    def test_strength_can_rank_a_signed_measure_by_its_size(self):
        rules = [{"path": "measures.z", "abs": True, "label": "z-score {v} against its own range"}]
        fall, rise = {"measures": {"z": -3.1}}, {"measures": {"z": 2.4}}
        self.assertEqual(snap.strength(fall, rules), (-3.1, rules[0]["label"], 0, 3.1))
        self.assertGreater(snap.strength(fall, rules)[3], snap.strength(rise, rules)[3])
        plain = [{"path": "measures.z", "label": "{v}"}]
        self.assertLess(snap.strength(fall, plain)[3], snap.strength(rise, plain)[3], "without abs a fall ranks low")
        self.assertEqual(snap.strength({}, rules), (None, None, None, None))

    def test_cordon_compares_like_with_like(self):
        """Each scale has its own rule and label; a CDC notice never ranks as a travel advisory."""
        cd = next(p for p in snap.CONFIG["programs"] if p["id"] == "cordon")
        item = lambda name, v: {"signal": {"name": name, "value": v}, "measures": {"level": v}}
        state = snap.strength(item("State Dept advisory level 1-4", 2), cd["strength"])
        cdc = snap.strength(item("CDC travel notice level 1-4", 3), cd["strength"])
        canada = snap.strength(item("Canada risk level 0-3", 1), cd["strength"])
        self.assertEqual(state[1].replace("{v}", "2"), "State Department travel advisory level 2 of 4")
        self.assertEqual(canada[1].replace("{v}", "1"), "Canada risk level 1 (0 to 3)")
        self.assertLess(state[2], cdc[2], "a travel advisory outranks a health notice at any level")
        self.assertEqual(snap.strength({"measures": {"level": 4}}, cd["strength"]), (None, None, None, None),
                         "a level on no named scale has no strength")

    def test_cordon_pick_admits(self):
        """Canada, FCDO and SafeTravel only on a level move; a CDC notice for 72 hours."""
        from datetime import datetime, timezone
        cd = next(p for p in snap.CONFIG["programs"] if p["id"] == "cordon")
        now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
        text_only = {"id": "cordon:canada:CN", "last_change": "changed"}
        self.assertTrue(snap.not_admitted(text_only, cd, now))
        self.assertIsNone(snap.not_admitted({"id": "cordon:canada:CN", "last_change": "raised"}, cd, now))
        self.assertTrue(snap.not_admitted({"id": "cordon:fcdo:mali", "last_change": "new"}, cd, now))
        self.assertIsNone(snap.not_admitted({"id": "cordon:advisory:ML", "last_change": "changed"}, cd, now))
        old = {"id": "cordon:cdc:malaria-panama", "data_as_of": "2026-11-18T05:00:00Z"}
        fresh = {"id": "cordon:cdc:measles-x", "data_as_of": "2026-11-22T05:00:00Z"}
        self.assertTrue(snap.not_admitted(old, cd, now))
        self.assertIsNone(snap.not_admitted(fresh, cd, now))
        self.assertTrue(snap.not_admitted({"id": "cordon:cdc:undated"}, cd, now), "an undated notice is not current")
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        hours = next(r["within_hours"] for r in cd["pick_admits"] if "within_hours" in r)
        self.assertIn(f"<b>{hours} hours</b>", html)

    def test_headwind_ranks_its_anomalies_by_the_size_of_z(self):
        hw = next(p for p in snap.CONFIG["programs"] if p["id"] == "headwind")
        self.assertEqual([(r["path"], r.get("abs")) for r in hw["strength"]], [("measures.z", True)])

    def test_picks_rule(self):
        today, picks, _, _ = build()
        ps = picks["picks"]
        self.assertTrue(0 < len(ps) <= snap.CONFIG["max_picks"])
        by_id = {f["properties"]["id"]: f for f in today["features"]}
        for p in ps:
            f = by_id[p["id"]]
            self.assertEqual(f["properties"]["tier"], "anomaly")
            self.assertIsNotNone(f["geometry"], "a pick needs a place")
            self.assertEqual(f["properties"]["cop_pick"], p["n"])
            self.assertTrue(p["reason"])
            self.assertTrue(snap.utc(f["properties"].get("data_as_of")), "every pick card shows its data time")
            self.assertTrue(f["properties"].get("sources"), "every pick card shows its source line")
        self.assertTrue(snap.utc(picks["generated_at"]), "the header shows the assessment time")
        # one program at a time: the first round has no repeats
        first_round = [p["program"] for p in ps][:len({p["program"] for p in ps})]
        self.assertEqual(len(first_round), len(set(first_round)))
        self.assertEqual([p["n"] for p in ps], list(range(1, len(ps) + 1)))

    def test_an_item_past_its_valid_until_is_never_a_pick(self):
        today, picks, _, _ = build()
        first = picks["picks"][0]["id"]

        def mutate(f):
            for feed in f.values():
                for x in feed.get("features", []) if isinstance(feed, dict) else []:
                    if x["properties"].get("id") == first:
                        x["properties"]["valid_until"] = snap.iso(NOW - timedelta(minutes=1))
        today2, picks2, _, _ = build(mutate=mutate)
        self.assertNotIn(first, [p["id"] for p in picks2["picks"]])
        self.assertIn(first, [f["properties"]["id"] for f in today2["features"]], "still stored and shown")

    def test_an_item_resting_on_a_party_claim_or_state_media_is_never_a_pick(self):
        """Kit event ladder (docs/event-ladder.md): such an item is labelled and raises nothing on its own."""
        _, picks, _, _ = build()
        first = picks["picks"][0]["id"]
        lead = {"state": "lead", "basis": None, "origins": 1, "official": None, "confirmed_at": None,
                "verified_at": None, "first_reported_at": "2026-10-30T00:00:00Z", "rule": "ladder-1: test"}

        def with_corroboration(c):
            def mutate(f):
                for feed in f.values():
                    for x in feed.get("features", []) if isinstance(feed, dict) else []:
                        if x["properties"].get("id") == first:
                            x["properties"]["corroboration"] = c
            return mutate

        def picked(c):
            today, ps, _, _ = build(mutate=with_corroboration(c))
            self.assertIn(first, [f["properties"]["id"] for f in today["features"]], "still stored and shown")
            return first in [p["id"] for p in ps["picks"]]

        self.assertFalse(picked(dict(lead, party_claim=True)), "a party's claim alone")
        self.assertFalse(picked(dict(lead, state_media=True)), "state media alone")
        self.assertFalse(picked(dict(lead, state="reported", party_claim=True)), "the kit 0.6.2 name of a lead")
        self.assertTrue(picked(dict(lead)), "a lead with no claim is still a candidate")
        verified = dict(lead, state="verified", basis="origins", origins=2, verified_at="2026-10-30T06:00:00Z",
                        party_claim=True)
        self.assertTrue(picked(verified), "verified on an origin outside the party: it may be a pick again")
        self.assertTrue(snap.rests_on_claim({"corroboration": dict(lead, state_media=True)}))
        self.assertFalse(snap.rests_on_claim({}), "an item outside the ladder rests on no claim")

    def test_valid_until_ahead_or_absent_still_picks(self):
        later = {"valid_until": snap.iso(NOW + timedelta(hours=1))}
        self.assertFalse(snap.no_longer_current(later, NOW))
        self.assertFalse(snap.no_longer_current({}, NOW))
        self.assertTrue(snap.no_longer_current({"valid_until": snap.iso(NOW - timedelta(seconds=1))}, NOW))
        self.assertTrue(snap.no_longer_current({"measures": {"current": False}}, NOW))

    def test_noisefloor_events_outrank_new_areas(self):
        today, picks, _, _ = build()
        nf = [p for p in picks["picks"] if p["program"] == "noisefloor"]
        self.assertTrue(nf and "% of aircraft with severe GPS error" in nf[0]["reason"], nf)

    def test_headwind_items_from_a_source_it_did_not_read_go_grey(self):
        hw = json.loads((FIX / "headwind.geojson").read_text(encoding="utf-8"))
        lanes = sorted({f["properties"]["sources"][0]["id"] for f in hw["features"]})
        self.assertIn("ecb-eurofxref", lanes)
        at = "2026-10-31T20:00:00Z"

        def mutate(f):
            f["headwind"]["health"] = {k: {"state": "fresh", "last_ok": at, "checked_at": at} for k in lanes}
            f["headwind"]["health"]["ecb-eurofxref"] = {"state": "failing", "last_ok": "2026-10-30T20:00:00Z",
                                                        "checked_at": at, "detail": "29 currencies"}
            for x in f["headwind"]["features"]:       # make one ECB item an anomaly with a place: never a pick now
                if x["properties"]["sources"][0]["id"] == "ecb-eurofxref" and x.get("geometry"):
                    x["properties"].update({"tier": "anomaly", "baseline_ref": "rolling", "measures": {"z": 9.0}})
                    break
        today, picks, health, _ = build(mutate=mutate)
        mine = [f["properties"] for f in today["features"] if f["properties"]["cop_program"] == "headwind"]
        ecb = [p for p in mine if p["sources"][0]["id"] == "ecb-eurofxref"]
        self.assertTrue(ecb and all(p["cop_stale"] and p["cop_source_stale"] for p in ecb))
        self.assertTrue(all(not p["cop_stale"] and "cop_source_stale" not in p for p in mine if p not in ecb))
        self.assertNotIn("headwind", {p["program"] for p in picks["picks"]})
        h = health_of(health, "headwind")
        self.assertEqual(h["state"], "fresh")                  # the feed itself is current
        self.assertEqual(h["sources"], {"total": len(lanes), "not_read": [
            {"id": "ecb-eurofxref", "state": "failing", "detail": "29 currencies", "failures": None}]})
        self.assertEqual(today["cop_programs"]["headwind"]["sources"]["total"], len(lanes))
        # A program whose feed has no health block (Launch) has no sources summary.
        self.assertNotIn("sources", health_of(health, "launch"))

    def test_a_prefix_key_registers_every_source_id_that_starts_with_it(self):
        self.assertTrue(snap.registered("usembassy-ye"))
        self.assertTrue(snap.registered("watchfloor:watchfloor:2026-11-23:zone:19_Somalia"))
        self.assertFalse(snap.registered("usembassy"))
        self.assertFalse(snap.registered("no-such-source"))

    def test_source_health_on_for_every_program_but_those_without_a_health_block(self):
        # Every program whose feed carries a per-source health block reads it. Watchfloor and Launch
        # publish none today; turn it on for them when they do.
        off = {p["id"] for p in snap.CONFIG["programs"] if not p.get("source_health")}
        self.assertEqual(off, {"watchfloor", "launch"})

    def test_dead_feed_keeps_last_good_copy_and_goes_grey(self):
        prev, _, _, _ = build()
        today, picks, health, _ = build(dead=("noisefloor",), previous=prev)
        h = health_of(health, "noisefloor")
        self.assertEqual(h["state"], "stale")
        self.assertTrue(h["from_previous"])
        nf = [f for f in today["features"] if f["properties"]["cop_program"] == "noisefloor"]
        self.assertTrue(nf and all(f["properties"]["cop_stale"] for f in nf))
        self.assertNotIn("noisefloor", [p["program"] for p in picks["picks"]], "stale items are never picked")

    def test_dead_feed_without_history_is_no_data(self):
        today, _, health, _ = build(dead=("vigil",))
        h = health_of(health, "vigil")
        self.assertEqual((h["state"], h["items"], h["from_previous"]), ("stale", 0, False))

    def test_old_data_is_stale(self):
        _, picks, health, _ = build(now=datetime(2026, 11, 20, tzinfo=timezone.utc))
        self.assertTrue(all(h["state"] == "stale" for h in health["programs"]))
        self.assertEqual(picks["picks"], [])

    def test_merged_snapshot_is_valid_contract_v2(self):
        today, _, _, _ = build()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "today.geojson"
            p.write_text(json.dumps(today), encoding="utf-8")
            rep = kitlint.lint_files([p])
        errors = {k: v for k, v in rep["standards"].items() if v["error"]}
        self.assertEqual(errors, {}, errors)

    def test_cordon_launch_warnings_also_show_under_spectrum(self):
        def add(f):
            base = f["cordon"]["features"][0]
            def item(i, title, summary=""):
                x = json.loads(json.dumps(base))
                x["properties"].update({"id": i, "title": title, "summary": summary})
                return x
            f["cordon"]["features"] += [
                item("cordon:navwarn:P-3249-2026", "HYDROPAC 3249/2026: HAZARDOUS OPERATIONS, SPACE DEBRIS 2215Z TO 0700Z"),
                item("cordon:navwarn:4-1078-2026", "NAVAREA IV 1078/2026: NORTH ATLANTIC.",
                     "1. HAZARDOUS OPERATIONS, ROCKET LAUNCHING 131754Z TO 131932Z NOV"),
                item("cordon:navwarn:12-800-2026", "NAVAREA XII 800/2026: HAZARDOUS OPERATIONS, MISSILE FIRING"),
                item("cordon:tfr:6-7113", "FAA TFR 6/7113 (space operations): Cape Canaveral, FL (Reentry)"),
            ]
        today, _, health, _ = build(mutate=add)
        tags = {f["properties"]["id"]: f["properties"].get("cop_also") for f in today["features"]}
        self.assertEqual(tags["cordon:navwarn:P-3249-2026"], ",spectrum,")
        self.assertEqual(tags["cordon:navwarn:4-1078-2026"], ",spectrum,", "the summary counts too")
        self.assertIsNone(tags["cordon:navwarn:12-800-2026"], "missile firings stay in Cordon's view")
        self.assertIsNone(tags["cordon:tfr:6-7113"], "TFRs are not listed: Launch draws the space-operations ones")
        self.assertEqual(sum(1 for k, v in tags.items() if v and k.startswith("cordon:")), 2, "nothing else is tagged")
        cordon = next(p for p in health["config"]["programs"] if p["id"] == "cordon")
        self.assertEqual(cordon["also_in"], [{"preset": "spectrum", "label": "launch, space-debris and reentry warnings"}])
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('["coalesce", ["get", "cop_also"], ""]', js)
        self.assertRegex(js, r'const MAP_KEYS = \[[^\]]*"cop_also"', "the map's copy of each item keeps the tag")

    def test_noisefloor_spoofing_anomalies_carry_their_strength(self):
        """Noisefloor's spoofing items (18 Nov 2026): each of its three measures has a strength, and they rank
        below the jamming rules, so a jamming event stays Noisefloor's pick."""
        cases = [("noisefloor-spoof-new-2026-11-17-1", "aircraft_shown_at_new_false_position", 31,
                  "31 aircraft shown at a new false position"),
                 ("noisefloor-spoof-region-above-2026-11-17-1", "spoofed_active_aircraft_hours", 69,
                  "69 spoofed aircraft-hours in the region"),
                 ("noisefloor-spoof-above-2026-11-17-1", "aircraft_shown_at_false_position", 57,
                  "57 aircraft shown at the false position")]

        def add(f):
            for i, name, v, _ in cases:
                x = json.loads(json.dumps(f["noisefloor"]["features"][0]))
                x["properties"].update({"id": i, "tier": "anomaly", "measure": {"name": name, "value": v}})
                f["noisefloor"]["features"].append(x)
        today, picks, _, _ = build(mutate=add)
        got = {f["properties"]["id"]: f["properties"].get("cop_strength_text") for f in today["features"]}
        for i, _, _, label in cases:
            self.assertEqual(got[i], label)
        nf = [p for p in picks["picks"] if p["program"] == "noisefloor"]
        self.assertTrue(nf and "% of aircraft with severe GPS error" in nf[0]["reason"], nf)

    def test_noisefloor_military_air_shows_under_conflict_with_its_strength(self):
        """Noisefloor's military air items (18 Nov 2026): routine measurements, so they stay off the picks; they show
        under Conflict as well as Spectrum, and their strength says the count is a lower bound."""
        def add(f):
            x = json.loads(json.dumps(f["noisefloor"]["features"][0]))
            x["properties"].update({
                "id": "noisefloor-milair-gulf-2026-11-17", "tier": "routine",
                "title": "Military aircraft seen over the Persian Gulf and Strait of Hormuz: 70 on 17 Nov 2026, "
                         "near its usual level (a lower bound)",
                "summary": "Military aircraft seen over the Persian Gulf and Strait of Hormuz on 17 Nov 2026: 70 "
                           "aircraft, 183 aircraft-hours, against a usual 191.5.",
                "measure": {"name": "military_aircraft_hours", "value": 183, "unit": "aircraft-hours",
                            "expected": 191.5}})
            f["noisefloor"]["features"].append(x)
        today, picks, health, _ = build(mutate=add)
        p = next(f["properties"] for f in today["features"] if f["properties"]["id"].startswith("noisefloor-milair-"))
        self.assertEqual(p["cop_also"], ",conflict,")
        self.assertEqual(p["cop_strength_text"], "183 military aircraft-hours seen, a lower bound")
        others = [f["properties"] for f in today["features"] if f["properties"].get("cop_program") == "noisefloor"
                  and not f["properties"]["id"].startswith("noisefloor-milair-")]
        self.assertTrue(others and not any(o.get("cop_also") for o in others), "only military air is tagged")
        self.assertNotIn("noisefloor-milair-gulf-2026-11-17", [k["id"] for k in picks["picks"]], "routine: never a pick")
        nf = next(q for q in health["config"]["programs"] if q["id"] == "noisefloor")
        self.assertEqual([r["preset"] for r in nf["also_in"]], ["conflict"])

    def test_a_standing_shift_says_what_it_is_measured_against(self):
        """Wake's wake-2.6 items in a moved-level run (25 Nov 2026) compare with a window fixed at the run's first
        week, so the size label says so. Only the words change: the value, the rule and the pick order do not."""
        def standing(f):
            for x in f["wake"]["features"]:
                if x["properties"]["id"] == "wake:port:istanbul:2026-11-06":
                    x["properties"]["measures"].update({"standing_since": "2026-09-25", "standing_weeks": 9})
        before, picks_before, _, _ = build()
        after, picks_after, health, _ = build(mutate=standing)
        a = next(f["properties"] for f in after["features"] if f["properties"]["id"] == "wake:port:istanbul:2026-11-06")
        b = next(f["properties"] for f in before["features"] if f["properties"]["id"] == "wake:port:istanbul:2026-11-06")
        self.assertEqual(a["cop_strength"], b["cop_strength"])
        self.assertIn("against the same weeks a year before the shift began", a["cop_strength_text"])
        self.assertIn("against the same weeks a year earlier", b["cop_strength_text"])
        self.assertEqual([p["id"] for p in picks_after["picks"]], [p["id"] for p in picks_before["picks"]])
        wake = next(q for q in health["config"]["programs"] if q["id"] == "wake")
        self.assertIn("while a moved level stands", wake["strength_labels"][0])

    def test_strength_label_alt_needs_a_value(self):
        rule = {"path": "m.v", "abs": True, "label": "{v} plain", "alt": {"when_set": "m.since", "label": "{v} alt"}}
        for props, want in (({"m": {"v": -5}}, "{v} plain"), ({"m": {"v": -5, "since": None}}, "{v} plain"),
                            ({"m": {"v": -5, "since": "2026-03-08"}}, "{v} alt")):
            value, label, i, rank = snap.strength(props, [rule])
            self.assertEqual((value, label, i, rank), (-5, want, 0, 5))

    def test_wake_kinds_are_drawn_and_tagged(self):
        """Wake's six kinds (17 Nov 2026): every kind shows under Maritime; UKMTO's attack, hijack and boarding reports
        also under Conflict, its advisories not; listed-IMO records also under Economic stress; ports rank on the
        same signed change as chokepoints; AIS gap and slow anomalies have no value on it and rank after them."""
        today, picks, health, _ = build()
        wake = {f["properties"]["id"]: f for f in today["features"] if f["properties"]["cop_program"] == "wake"}
        kinds = {i.split(":")[1] for i in wake}
        self.assertEqual(kinds, {"chokepoint", "port", "ais-listed", "ais-gap", "ais-slow", "ukmto"})
        tag = lambda i: wake[i]["properties"].get("cop_also")
        self.assertEqual(tag("wake:ukmto:ukmto-209"), ",conflict,")
        self.assertIsNone(tag("wake:ukmto:ukmto-178"), "an advisory stays in Wake's own view")
        self.assertEqual(tag("wake:ais-listed:8414477:273350220"), ",economy,")
        self.assertIsNone(wake["wake:ais-listed:9288746:273271850"]["geometry"], "no position: Off the map")
        self.assertFalse(any(tag(i) for i in wake if i.split(":")[1] in ("chokepoint", "port", "ais-gap", "ais-slow")))
        port = wake["wake:port:istanbul:2026-11-06"]["properties"]
        self.assertEqual(port["cop_strength"], -43.7)
        self.assertIn("against the same weeks a year earlier", port["cop_strength_text"])
        self.assertIsNone(wake["wake:ais-slow:230941810:1794911990"]["properties"]["cop_strength"])
        w = next(p for p in health["config"]["programs"] if p["id"] == "wake")
        self.assertEqual([r["preset"] for r in w["also_in"]], ["conflict", "economy"])
        self.assertEqual(health_of(health, "wake")["sources"]["not_read"], [], "Wake's health block keys its lanes")
        for f in wake.values():
            for s in f["properties"]["sources"]:
                self.assertIn(s["id"], snap.SOURCES, s["id"])

    def test_country_shapes_only_for_country_level_items(self):
        today, _, _, countries = build()
        wanted = {cc for f in today["features"] if f["properties"].get("precision_class") == "country"
                  for cc in f["properties"].get("countries") or []}
        got = {f["properties"]["cc"] for f in countries["features"]}
        self.assertTrue(got and got <= wanted)

    def test_unregistered_sources_are_listed(self):
        def add(f):
            f["context"] = copy.deepcopy(f["context"])
            f["context"]["features"][0]["properties"]["sources"].append(
                {"id": "mystery-nc-source", "publisher": "Mystery", "licence": "CC BY-NC 4.0"})
        _, _, health, _ = build(mutate=add)
        self.assertIn("mystery-nc-source", health_of(health, "context")["unregistered_sources"])

    def test_every_fixture_source_is_registered(self):
        _, _, health, _ = build()
        for h in health["programs"]:
            self.assertEqual(h["unregistered_sources"], [], h["id"])


def spoil(pid, change, index=0):
    """A feeds mutator: one item of a program's feed becomes change(item)."""
    def mutate(f):
        f[pid]["features"][index] = change(f[pid]["features"][index])
    return mutate


def props(**kv):
    def change(x):
        x["properties"].update(kv)
        return x
    return change


def geometry(g):
    return lambda x: {**x, "geometry": g}


def no_id(x):
    del x["properties"]["id"]
    return x


# Shapes a program could publish by mistake, with the reason health.json gives. Before 15 Nov 2026 each one stopped
# the build (in snapshot.py, the kit's contract check, agent_index.py or card.py), or, for NaN, wrote a snapshot no
# browser can read. The last one stands for any shape the kit's check itself cannot read.
BAD = [
    ("no id", no_id, "id is not a string"),
    ("properties null", lambda x: {**x, "properties": None}, "properties is not an object"),
    ("properties a list", lambda x: {**x, "properties": []}, "properties is not an object"),
    ("a string for a feature", lambda x: "oops", "the feature is not an object"),
    ("null for a feature", lambda x: None, "the feature is not an object"),
    ("id a number", props(id=5), "id is not a string"),
    ("sources a string", props(sources="x"), "sources is not a list of objects"),
    ("a source that is a string", props(sources=["x"]), "sources is not a list of objects"),
    ("countries a number", props(countries=5), "countries is not a list of strings"),
    ("geometry a string", geometry("x"), "geometry is not a GeoJSON geometry"),
    ("a polygon with no rings", geometry({"type": "Polygon", "coordinates": None}), "geometry is not a GeoJSON geometry"),
    ("a collection of a string", geometry({"type": "GeometryCollection", "geometries": ["x"]}),
     "geometry is not a GeoJSON geometry"),
    ("a measure that is NaN", props(measures={"z": float("nan")}), "not finite"),
    ("a tier the kit cannot read", props(tier=["anomaly"]), "the contract check could not read it"),
]
NAMED = {"sources a string", "a source that is a string", "countries a number", "geometry a string",
         "a polygon with no rings", "a collection of a string", "a measure that is NaN", "a tier the kit cannot read"}


def spoiled_fixtures(folder, pid="blackout"):
    """A copy of the fixtures in `folder` where `pid`'s feed also carries one item of every BAD shape. Returns the
    number added. Written as a program would write it: Python's json puts NaN in the file as it stands."""
    for p in FIX.glob("*.geojson"):
        (folder / p.name).write_bytes(p.read_bytes())
    feed = json.loads((FIX / f"{pid}.geojson").read_text(encoding="utf-8"))
    first = feed["features"][0]
    for n, (_label, change, _why) in enumerate(BAD):
        item = copy.deepcopy(first)
        item["properties"]["id"] = f"{pid}:spoiled:{n}"
        feed["features"].append(change(item))
    (folder / f"{pid}.geojson").write_text(json.dumps(feed), encoding="utf-8")
    return len(BAD)


class Malformed(unittest.TestCase):
    """One malformed item is left out and counted. It never stops the build, and its program stays in the picture."""

    def test_each_malformed_shape_is_left_out_and_counted(self):
        _, _, clean, _ = build()
        first = feeds()[0]["blackout"]["features"][0]["properties"]["id"]
        for label, change, why in BAD:
            with self.subTest(label):
                today, picks, health, _ = build(mutate=spoil("blackout", change))
                h = health_of(health, "blackout")
                self.assertEqual((h["state"], h["items"], h["dropped"]["count"]),
                                 ("fresh", health_of(clean, "blackout")["items"] - 1, 1))
                self.assertIn(why, h["dropped"]["examples"][0])
                self.assertNotIn(first, [f["properties"]["id"] for f in today["features"]])
                row, = today["dropped"]
                self.assertEqual((row["id"], row["program"], row["index"]),
                                 (first if label in NAMED else None, "blackout", 0))
                self.assertIn(why, row["reason"])
                for pid in IDS:                                      # every other program is untouched
                    if pid != "blackout":
                        self.assertEqual((health_of(health, pid)["items"], health_of(health, pid)["dropped"]["count"]),
                                         (health_of(clean, pid)["items"], 0), pid)
                self.assertTrue(picks["picks"])
                json.dumps(today, allow_nan=False)                   # NaN is not JSON: a browser refuses the file

    def test_a_clean_build_drops_nothing(self):
        today, _, health, _ = build()
        self.assertEqual(today["dropped"], [])
        self.assertEqual([h["dropped"] for h in health["programs"]], [{"count": 0, "examples": []}] * len(IDS))

    def test_a_wrong_value_is_a_lint_error_and_the_item_stays(self):
        for change in (props(tier="red"), props(title=None), props(sources=[]), props(data_as_of="soon")):
            _, _, clean, _ = build()
            today, _, health, _ = build(mutate=spoil("blackout", change))
            h = health_of(health, "blackout")
            self.assertEqual((h["items"], h["dropped"]["count"]), (health_of(clean, "blackout")["items"], 0))
            self.assertGreater(h["lint"]["errors"], 0)

    def test_a_last_good_copy_with_a_malformed_item_still_stands_in_for_a_dead_feed(self):
        prev, _, _, _ = build()
        prev["features"] += ["oops", {"type": "Feature", "geometry": None, "properties": None}]
        today, _, health, _ = build(dead=("noisefloor",), previous=prev)
        h = health_of(health, "noisefloor")
        self.assertEqual((h["state"], h["from_previous"]), ("stale", True))
        self.assertGreater(h["items"], 0)
        _, _, health, _ = build(dead=("vigil",), previous="not a snapshot")
        self.assertEqual((health_of(health, "vigil")["state"], health_of(health, "vigil")["items"]), ("stale", 0))

    def test_the_build_command_runs_and_reports_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures, out = Path(tmp) / "fixtures", Path(tmp) / "out"
            fixtures.mkdir()
            added = spoiled_fixtures(fixtures)
            log = io.StringIO()
            with redirect_stdout(log):
                code = snap.main(["--fixtures", str(fixtures), "--out", str(out), "--ended-out", str(Path(tmp) / "ended"),
                                  "--now", "2026-10-31T20:30:00Z"])
            self.assertEqual(code, 0)
            # parse_constant is called for NaN and Infinity, which Python reads and a browser refuses
            today = json.loads((out / "today.geojson").read_text(encoding="utf-8"), parse_constant=self.fail)
            health = json.loads((out / "health.json").read_text(encoding="utf-8"), parse_constant=self.fail)
            h = health_of(health, "blackout")
            self.assertEqual((h["state"], h["dropped"]["count"], len(h["dropped"]["examples"])), ("fresh", added, 3))
            self.assertEqual(len(today["dropped"]), added)
            self.assertIn(f"::warning::Blackout: {added} malformed item(s) left out of the picture", log.getvalue())
            # the share card's page still builds from that snapshot
            picks = json.loads((out / "picks.json").read_text(encoding="utf-8"))
            self.assertIn("Today’s picks", card.build_html(picks, today, health, Card.WORLD))

    def test_a_saved_feed_that_is_not_a_feed_reads_as_a_failed_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.geojson"
            for body in ('["not", "a", "feed"]', '{"features": "none"}', "not json"):
                p.write_text(body, encoding="utf-8")
                feed, rep = snap.read_local(p)
                self.assertEqual((feed, rep["ok"]), (None, False), body)


class Card(unittest.TestCase):
    WORLD = {"features": [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [40, 0], [40, 30], [0, 30], [0, 0]]]}}]}

    def test_a_pick_whose_title_or_time_cannot_be_read_still_makes_a_card(self):
        _, picks, _, _ = build()
        pick = next(p["id"] for p in picks["picks"] if p["program"] == "noisefloor")

        def odd(f):
            for x in f["noisefloor"]["features"]:
                if x["properties"]["id"] == pick:
                    x["properties"].update({"title": 5, "data_as_of": "soon"})
        today, picks, health, _ = build(mutate=odd)
        self.assertIn(pick, [p["id"] for p in picks["picks"]], "a lint error drops nothing, so it is still a pick")
        page = card.build_html(picks, today, health, self.WORLD)
        self.assertEqual(page.count("<li>"), len(picks["picks"]))
        self.assertIsNone(card.day("soon"))
        self.assertIsNone(card.day(5))

    def test_card_lists_every_placed_pick_and_escapes_titles(self):
        def poison(f):
            f["noisefloor"]["features"][0]["properties"]["title"] = "<script>x</script>"
        today, picks, health, _ = build(mutate=poison)
        page = card.build_html(picks, today, health, self.WORLD)
        self.assertEqual(page.count("<li>"), len(picks["picks"]))
        self.assertNotIn("<script>x", page)
        self.assertIn("Assessed 31 Oct 2026, 20:30 UTC", page)
        self.assertIn("Measured or reported, not warnings", page)

    def test_place_of_lines_and_collections(self):
        # Same rule as cop.js placeOf: the middle of the box around a line's positions, a collection's first part.
        line = {"type": "LineString", "coordinates": [[177.0, 53.0], [180.5, 53.5], [184.0, 54.0]]}
        self.assertEqual(card.place_of(line), [180.5, 53.5])
        multi = {"type": "MultiLineString", "coordinates": [[[0, 0], [2, 1]], [[4, 3], [6, 2]]]}
        self.assertEqual(card.place_of(multi), [3.0, 1.5])
        area = {"type": "Polygon", "coordinates": [[[10, 10], [12, 10], [12, 14], [10, 10]]]}
        self.assertEqual(card.place_of({"type": "GeometryCollection", "geometries": [area, line]}), [11.0, 12.0])
        self.assertIsNone(card.place_of({"type": "GeometryCollection", "geometries": []}))
        self.assertEqual(card.place_of({"type": "MultiPoint", "coordinates": [[0, 0], [4, 2]]}), [2.0, 1.0])

    def test_page_draws_lines_and_splits_collections(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('["LineString", "MultiLineString"]', js)
        for layer in ("lines-casing", "lines", "lines-hit"):
            self.assertIn(f'id: "{layer}"', js)
        self.assertIn('"lines-hit", "halos", "points"', js, "clicks on a line open its card")
        self.assertIn('g.type === "GeometryCollection"', js)

    # The page has no browser test (the review of 15 Nov 2026 says so), so these keep the fixes of that date from being
    # undone by a later edit: they check that the page still reads what it must, not how it looks. Each was checked in
    # a browser when written, on a picture built from the live feeds.
    def test_page_opens_the_top_mark_with_one_click_handler(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('const HIT_LAYERS = ["cf-fill", "cells-fill", "lines-hit", "halos", "points"]', js)
        self.assertIn("map.queryRenderedFeatures(point, { layers: HIT_LAYERS })", js)
        self.assertEqual(js.count('map.on("click"'), 1, "one handler: a handler per layer let the country fill's run last")
        self.assertIn("here: ids", js, "the card lists what else is under the click")

    def test_card_shows_size_and_the_lane_record_together(self):
        """The card's first facts, run in Node with the page's own code: "Size" stands in for "Why it's here" on an item
        that is not a pick, and the lane record is its own row, so a lane (every program has one) never hides the size."""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        start = js.index("  const fact = (k, v) => facts.append(")
        block = js[start:js.index("  const voicePick = ", start)]
        script = ("const run = (p, pick, given) => { const keys = [];"
                  " const facts = { append: (dt) => keys.push(dt.text) };"
                  " const h = (tag, attrs) => attrs || {}; const laneFor = () => given;\n" + block +
                  "\nreturn keys; };\n"
                  "const p = { cop_program: 'elements', id: 'elements:usgs:1', cop_strength_text: 'magnitude 6.1' };"
                  " const lane = { text: 'No outside check yet.' };"
                  " console.log(JSON.stringify([run(p, null, lane), run(p, { n: 1, reason: 'x' }, lane),"
                  " run(p, null, null), run({ id: 'x' }, null, lane)]));")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [["Size", "Lane record"], ["Why it's here", "Lane record"], ["Size"],
                                           ["Lane record"]])

    def test_page_reads_valid_until(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn("Date.parse(f.properties.valid_until)", js)
        self.assertRegex(js, r'const MAP_KEYS = \[[^\]]*"cop_expired"', "the map's copy of each item knows it expired")
        self.assertIn("const standsOut = (p) => STANDS_OUT.includes(p.tier) && !p.cop_expired;", js)
        self.assertIn('["in", ["get", "tier"], ["literal", STANDS_OUT]], NOT_EXPIRED]', js)
        self.assertIn('fact(p.cop_expired ? "Expired" : "Valid until", timeEl(p.valid_until))', js)

    def test_card_shows_the_event_ladder_state(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn("const lb = ladderBlock(p);", js)
        for words in ("es.label", "es.meaning", "es.basis_label", "es.claim_label", "es.claim_meaning", "es.url",
                      '"How this is set"'):
            self.assertIn(words, js)
        self.assertIn("if (!es) return null;", js, "an item outside the ladder shows no state")

    def test_page_lists_warnings_first_and_never_as_picks(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertLess(page.index('id="warnings"'), page.index('id="picks"'), "warnings come before the picks")
        self.assertIn('out.filter((f) => f.properties.tier === "warning")', js)
        self.assertIn("'s own judgment: ", js)
        _, picks, _, _ = build()
        today, _, _, _ = build()
        tiers = {f["properties"]["id"]: f["properties"]["tier"] for f in today["features"]}
        self.assertEqual({tiers[p["id"]] for p in picks["picks"]}, {"anomaly"}, "a warning is never a pick")

    def test_page_writes_its_sentences_from_the_snapshot(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Every finding today is a measurement", page)
        self.assertIn('id="how-counts"', page)
        self.assertIn('$("how-counts").textContent = text;', js)
        self.assertNotIn("No findings with a place for this view today. See Off the map below.", js)
        self.assertIn("listed under Also watching.", js)
        # "Measured" only where the item's own status_class says its program measured it
        self.assertEqual(js.count('"Measured, not a warning"'), 1)
        self.assertIn('p.status_class === "assessed" ? "Measured, not a warning" : "Measured or reported, not a judgment"', js)

    def test_whole_day_items_are_fresh_to_the_end_of_their_day(self):
        """A day item's data_as_of is midnight UTC at its start, but it covers the whole day: judged from the end of that
        day (never later than now), so a program publishing yesterday's items is not a day older than it is."""
        def day_only(f):
            for item in f["watchfloor"]["features"]:
                item["properties"].update({"data_as_of": "2026-10-30T00:00:00Z", "data_as_of_precision": "day"})
        plain = health_of(build(mutate=lambda f: [i["properties"].update(
            {"data_as_of": "2026-10-30T00:00:00Z"}) for i in f["watchfloor"]["features"]])[2], "watchfloor")
        day = health_of(build(mutate=day_only)[2], "watchfloor")
        self.assertEqual(plain["age_hours"], 44.5)          # 30 Oct 00:00 to 31 Oct 20:30
        self.assertEqual(day["age_hours"], 20.5)            # the day ended 31 Oct 00:00
        self.assertEqual(day["data_as_of"], "2026-10-30T00:00:00Z", "health still reports the source's own time")
        future = health_of(build(mutate=lambda f: [i["properties"].update(
            {"data_as_of": "2026-10-31T00:00:00Z", "data_as_of_precision": "day"}) for i in f["watchfloor"]["features"]])[2],
            "watchfloor")
        self.assertEqual(future["age_hours"], 0.0)          # today's day is still running: never in the future
        month = snap.covered_until({"data_as_of": "2026-12-01T00:00:00Z", "data_as_of_precision": "month"},
                                   datetime(2027, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(month, datetime(2027, 1, 1, tzinfo=timezone.utc))   # December ends at New Year

    def test_date_only_items_show_their_date(self):
        """data_as_of_precision (kit 0.5.6): a date-only or month-only time is shown as written, never moved into the
        viewer's zone (Undertow's '2025-06' read 'May 31, 7:00 PM CDT' before)."""
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('import { judgmentChips, asOfDateLabel, eventState } from "./vendor/tic-doctrine.js";', js)
        self.assertIn('fact("Data as of", asOfEl(p));', js)
        self.assertIn('asOfEl(p, "date")', js)
        self.assertEqual(js.count("timeEl(p.data_as_of"), 1, "only asOfEl's own fallback for an exact time")
        kit = (ROOT / "site" / "vendor" / "tic-doctrine.js").read_text(encoding="utf-8")
        self.assertIn("export function asOfDateLabel(item, locale)", kit)

    def test_reported_activity_is_told_apart(self):
        """Undertow's reported-activity items (16 Nov 2026): the card names the reporter, its grade, the reports and
        the state, says it is not a takedown, and never calls a quiet operation ended. The MCP rule says the same."""
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn("const a = p.activity;", js)
        self.assertIn('"Reported activity: a researcher\'s report, not a takedown"', js)
        for k in ('fact("Reported by"', 'fact("First report"', 'fact("Last report"', 'fact("State"'):
            self.assertIn(k, js)
        self.assertIn("(not a sign the operation ended)", js)
        server = (ROOT / "mcp" / "server.js").read_text(encoding="utf-8")
        rule = next(line for line in server.splitlines() if line.startswith("8. Undertow"))
        for k in ("reported-activity item", "activity block", "DFRLab", "CheckFirst", "CORRECTIV",
                  "quiet never means the operation ended", "not that it found the operation running",
                  "They are not an origin", "only stated targets are marked on the map",
                  "records as targets are marked on the map"):
            self.assertIn(k, server if k in ("DFRLab", "CheckFirst", "CORRECTIV") else rule)

    def test_view_keeps_every_pick_inside_the_map(self):
        today, picks, health, _ = build()
        by_id = {f["properties"]["id"]: f for f in today["features"]}
        pts = [card.place_of(by_id[p["id"]]["geometry"]) for p in picks["picks"]]
        view = card.View(pts, card.MAP_W, card.H)
        for lon, lat in pts:
            x, y = view.px(lon, lat)
            self.assertTrue(0 < x < card.MAP_W and 0 < y < card.H, (lon, lat, x, y))

    def test_spread_separates_pins(self):
        out = card.spread([(100, 100), (101, 100), (100, 102)])
        for i in range(3):
            for j in range(i + 1, 3):
                self.assertGreaterEqual(((out[i][0] - out[j][0]) ** 2 + (out[i][1] - out[j][1]) ** 2) ** 0.5, 39)

    def test_page_stamp_versions_both_image_addresses(self):
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        stamped, n = card.stamp_page(page, "2026-10-31T22:08:05Z")
        self.assertEqual(n, 2)
        self.assertEqual(stamped.count("card.png?v=202610312208\""), 2)
        again, _ = card.stamp_page(stamped, "2026-11-01T00:17:00Z")
        self.assertEqual(again.count("card.png?v=202611010017\""), 2)

    def test_fallback_card_is_committed_at_the_right_size(self):
        self.assertEqual(card.png_size(card.FALLBACK), (card.W, card.H))


class Design(unittest.TestCase):
    def test_outlines_reach_3_to_1_on_both_basemaps(self):
        bm = snap.CONFIG["basemaps"]
        colours = [p["color"] for p in snap.CONFIG["programs"]] + [snap.CONFIG["stale_color"]]
        for c in colours:
            for mode, key in (("light", "outline_light"), ("dark", "outline_dark")):
                for surface in ("land", "water"):
                    ratio = contrast(c[key], bm[mode][surface])
                    self.assertGreaterEqual(ratio, 3.0, (c, mode, surface, round(ratio, 2)))

    def test_one_colour_per_program(self):
        fills = [p["color"]["fill"] for p in snap.CONFIG["programs"]]
        self.assertEqual(len(fills), len(set(fills)))

    def test_programs_come_from_the_suite_registry(self):
        raw = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
        for p in raw["programs"]:
            self.assertFalse(set(snap.REGISTRY_FIELDS) & set(p), f"{p['id']}: keep these in the kit registry only")
            reg = snap.REGISTRY[p["id"]]
            self.assertEqual(reg["status"], "live", p["id"])
            self.assertTrue(reg["feed"].startswith("https://"), p["id"])
        for p in snap.CONFIG["programs"]:
            self.assertTrue(all(p.get(k) for k in snap.REGISTRY_FIELDS), p["id"])

    def test_vendored_kit_js_matches_the_kit(self):
        a = (ROOT / "site" / "vendor" / "tic-doctrine.js").read_bytes().replace(b"\r\n", b"\n")
        b = (ROOT / "doctrine" / "kit" / "js" / "tic-doctrine.js").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(a, b, "copy doctrine/kit/js/tic-doctrine.js to site/vendor/ after a kit sync")

    def test_no_cdn_scripts(self):
        for page in ("index.html", "scoring.html"):
            html = (ROOT / "site" / page).read_text(encoding="utf-8")
            self.assertNotIn("<script src=\"http", html, page)
            self.assertNotIn("style=\"", html, page)
            self.assertNotRegex(html, r"<script(?![^>]* src=)[^>]*>", f"{page}: no inline script (CSP)")

    def test_scoring_page_states_the_thresholds_the_code_uses(self):
        """scoring.html writes every threshold in words; each must match the constant it describes (18 Nov 2026)."""
        import re
        sys.path.insert(0, str(ROOT / "tools"))
        import three_signal as ts
        import triage as tri
        from tic_doctrine import vocab
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
        hormuz = ts.AREAS["hormuz"]
        cov = {c["id"]: c for c in vocab.load("event_ladder")["coverage"]["classes"]}
        self.assertEqual(set(cov), {"several", "few", "no_independent", "none"})
        for phrase in (f"{cov['several']['label']} when {cov['several']['min']} or more independent source groups",
                       f"{cov['few']['label']} for {cov['few']['min']} or {cov['few']['max']}",
                       f"{cov['no_independent']['label']} for {cov['no_independent']['max']} when COP still holds "
                       "items there from sources it sets aside or does not count",
                       f"{cov['none']['label']} for {cov['none']['max']} when it holds none",
                       "rests only on a party's claim or on state media is never a candidate",
                       "never raise a place's class", "each count is a lower bound"):
            self.assertIn(phrase, text)
        self.assertIsNone(cov["several"]["max"], "several has no upper bound: the page says 'or more'")
        for phrase in (f"{tri.HAZARD_BEFORE_H} hours before the outage", f"up to {tri.HAZARD_AFTER_H} hours after",
                       f"within {tri.HAZARD_NEAR_KM} km", f"last advisory plus {tri.EPISODIC_LAG_H} hours",
                       f"dated in the {tri.NOTICE_RECENT_H} hours before", "28 complete days",
                       f"more than {tri.POWER_LINE:,} meters", f"within {tri.STATE_NEAR_KM} km of an outage county",
                       f"within {ts.WINDOW_H} hours of each other", f"{hormuz['radius_km']} km around {hormuz['center_name']}",
                       f"in the {ts.LOOKBACK_H} hours before the reading", f"more than {ts.NAVWARN_BLIND_DAYS} days old",
                       f"{ts.OONI_MOVE_POINTS} points or more", f"until there are {snap.CONFIG['max_picks']} picks",
                       f"z-score of {ts.WATCHFLOOR_Z:g} or more", f"{ts.RENEWAL_BEFORE_D} days before",
                       f"{ts.RENEWAL_AFTER_H} hours after its", f"last {ts.RATE_DAYS} archived days",
                       f"{round(ts.RATE_PERCENTILE * 100)}th percentile", f"{ts.RATE_MIN_DAYS} such days",
                       f"all {len(ts.SIGNALS)} counted within {ts.WINDOW_H} hours".replace("all 4", "all four"),
                       # Join 0.4: UKMTO's attacks, measured on their own series by the same percentile test.
                       f"{round(ts.RATE_PERCENTILE * 100)}th percentile of UKMTO's daily counts over the last "
                       f"{ts.RATE_DAYS} days", f"Wake keeps {ts.UKMTO_KEEP_D} days",
                       f"{ts.RATE_DAYS} days plus the {ts.LOOKBACK_H} hours"):
            self.assertIn(phrase, text)
        self.assertEqual(tri.ATTENTION_BASELINE, "wf-28d")
        self.assertEqual(set(tri.ALERT_COUNTED), {"security", "demonstration"})
        self.assertEqual(ts.RATE_SATURATED, 0.5, "the page says 'more than half'")
        for word in ts.MILITARY_WORDS:     # every word the join reads is named on the page, in plain English
            stem = {"HAZARDOUS OP": "hazardous operations", "UAV": "UAVs", "NAVAL EXERCISE": "exercise"}.get(word, word)
            self.assertIn(stem.lower().split()[0], text.lower(), word)
        for w in ("likely", "strike window", "attack likely"):
            self.assertNotIn(w, text.lower(), "the voice rule: no inference in COP's voice")
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="scoring.html"', index, "the map links the page")
        js = (ROOT / "site" / "scoring.js").read_text(encoding="utf-8")
        for f in ("health.json", "triage.json", "three-signal.json"):
            self.assertIn(f, js)


def gdelt_only():
    """Every program dead but Watchfloor and Context, so all five picks come from the GDELT voice."""
    return tuple(pid for pid in IDS if pid not in ("watchfloor", "context"))


def raise_items(f, changes):
    """changes: {item id: measures}; makes each item an anomaly with those measures (the program's strength path)."""
    for pid in ("watchfloor", "context"):
        for x in f[pid]["features"]:
            m = changes.get(x["properties"]["id"])
            if m is not None:
                x["properties"].update({"tier": "anomaly", "baseline_ref": "rolling", "measures": m})


WF, CX = "watchfloor:2026-10-30:", "context:2026-10-31_1200Z:"


class GdeltVoice(unittest.TestCase):
    """Watchfloor and Context both read GDELT (independence_group "gdelt") and number their zones alike (WF-WP-Z07 is
    CX-WP-Z07; Context's Z17, Horn of Africa, is Watchfloor's Z18 plus Z19). Suite review of 15 Nov 2026: COP's picks
    took one per program, so GDELT held two of five slots; they are one voice, joined on zone, never two layers."""

    def test_voice_keys_join_on_zone_number(self):
        gd = [{"id": "gdelt-gkg", "independence_group": "gdelt"}]
        cx = gd + [{"id": "vertex-gemini", "independence_group": "context-ai"}]
        vk = snap.voice_keys
        self.assertEqual(vk({"warning_problem_id": "WF-WP-Z07", "sources": gd}), ["gdelt:Z07"])
        self.assertEqual(vk({"warning_problem_id": "CX-WP-Z07", "sources": cx}), ["gdelt:Z07"])
        self.assertEqual(vk({"warning_problem_id": "CX-WP-Z17", "sources": cx}), ["gdelt:Z18", "gdelt:Z19"])
        self.assertEqual(vk({"warning_problem_id": "WF-WP-Z18", "sources": gd}), ["gdelt:Z18"])
        self.assertEqual(vk({"warning_problem_id": "WF-WP-T3", "sources": gd}), vk({"warning_problem_id": "CX-WP-T3",
                                                                                     "sources": cx}))
        # A Watchfloor country item outside every zone is its own country's voice.
        self.assertEqual(vk({"countries": ["GN"], "sources": gd}), ["gdelt:GN"])
        # Not GDELT: no shared voice, whatever its problem id says.
        self.assertEqual(vk({"warning_problem_id": "WF-WP-Z07", "sources": [{"id": "x", "independence_group": "nws"}]}),
                         [])
        self.assertEqual(vk({"countries": ["US"], "sources": []}), [])

    def test_gdelt_takes_one_slot_a_round_not_one_per_program(self):
        # The fixtures reproduce the review's case: a Watchfloor anomaly and a Context anomaly each took a slot.
        _, picks, _, _ = build()
        gd = [p for p in picks["picks"] if p["program"] in ("watchfloor", "context")]
        self.assertEqual(len(picks["picks"]), snap.CONFIG["max_picks"])
        self.assertEqual(len(gd), 1, [p["id"] for p in picks["picks"]])
        self.assertEqual(gd[0]["voice"], ["gdelt:QA"])

    def test_one_pick_per_zone_voice_carrying_both_readings(self):
        def mutate(f):
            raise_items(f, {WF + "zone:07_Russia_NATO": {"score": {"z": 9.0}},
                            CX + "07_Russia_NATO": {"volume_12h": 900}})
        today, picks, _, _ = build(dead=gdelt_only(), mutate=mutate)
        ps = picks["picks"]
        self.assertTrue(ps)
        voices = [k for p in ps for k in p["voice"]]
        self.assertEqual(len(voices), len(set(voices)), "two picks share a GDELT voice: " + str(voices))
        z07 = [p for p in ps if "gdelt:Z07" in p["voice"]]
        self.assertEqual(len(z07), 1, ps)
        pick = z07[0]
        self.assertEqual(pick["id"], WF + "zone:07_Russia_NATO", "Watchfloor's own strongest is picked")
        ids = [r["id"] for r in pick["readings"]]
        self.assertEqual(ids[0], pick["id"], "the pick's own reading comes first")
        self.assertIn(CX + "07_Russia_NATO", ids, "Context's reading of the same zone rides on the pick")
        self.assertEqual({r["program"] for r in pick["readings"]}, {"watchfloor", "context"})
        by_id = {f["properties"]["id"]: f["properties"] for f in today["features"]}
        self.assertIsNone(by_id[CX + "07_Russia_NATO"]["cop_pick"], "joined, not a second pick")
        # Joined, never merged: both items stay in the picture with their own program, sources and licence line.
        self.assertEqual(by_id[CX + "07_Russia_NATO"]["cop_program"], "context")
        self.assertTrue(by_id[CX + "07_Russia_NATO"]["sources"])
        self.assertTrue(today["cop_programs"]["context"]["attribution"])
        self.assertEqual(sum(1 for f in today["features"] if f["properties"]["cop_program"] == "context"), 21)
        cx = next(r for r in pick["readings"] if r["program"] == "context")
        self.assertEqual((cx["tier"], cx["warning_problem_id"]), ("anomaly", "CX-WP-Z07"))
        self.assertEqual(cx["strength"], "900 articles in the last 12 hours")

    def test_horn_of_africa_joins_two_watchfloor_zones(self):
        def mutate(f):
            raise_items(f, {CX + "17_Horn_of_Africa": {"volume_12h": 900},
                            WF + "zone:19_Somalia": {"score": {"z": 1.0}}})
        _, picks, _, _ = build(dead=gdelt_only(), mutate=mutate)
        by_id = {p["id"]: p for p in picks["picks"]}
        self.assertIn(CX + "17_Horn_of_Africa", by_id, "Context's strongest is picked")
        horn = by_id[CX + "17_Horn_of_Africa"]
        self.assertEqual(horn["voice"], ["gdelt:Z18", "gdelt:Z19"])
        self.assertNotIn(WF + "country:ET:violence", by_id, "Watchfloor's Ethiopia (Z18) is the same voice")
        self.assertNotIn(WF + "zone:19_Somalia", by_id, "Watchfloor's Somalia (Z19) is the same voice")
        ids = [r["id"] for r in horn["readings"]]
        for wf in ("country:ET:violence", "zone:18_Ethiopia_Eritrea", "zone:19_Somalia"):
            self.assertIn(WF + wf, ids)
        # Readings list anomalies before routine items, each program's own strongest first.
        tiers = [r["tier"] for r in horn["readings"]]
        self.assertEqual(tiers, sorted(tiers, key=lambda t: t != "anomaly"))

    def test_same_zone_items_of_one_program_make_one_pick(self):
        # Watchfloor's Korea violence and attention items are both WF-WP-Z03: one voice, one pick.
        def mutate(f):
            raise_items(f, {WF + "country:KN:violence": {"score": {"z": 8.0}},
                            WF + "country:KN:attention": {"score": {"z": 7.0}}})
        _, picks, _, _ = build(dead=gdelt_only(), mutate=mutate)
        kn = [p for p in picks["picks"] if p["id"].startswith(WF + "country:KN:")]
        self.assertEqual([p["id"] for p in kn], [WF + "country:KN:violence"], picks["picks"])
        self.assertEqual(kn[0]["voice"], ["gdelt:Z03"])
        self.assertIn(WF + "country:KN:attention", [r["id"] for r in kn[0]["readings"]])
        self.assertIn(CX + "03_Korea_Peninsula", [r["id"] for r in kn[0]["readings"]], "Context's routine reading too")

    def test_picks_never_count_gdelt_agreement_as_two(self):
        # Whatever the round, the GDELT seat gives one pick per round, as one program would.
        _, picks, _, _ = build()
        programs = [p["program"] for p in picks["picks"]]
        seats = ["gdelt" if p in ("watchfloor", "context") else p for p in programs]
        first = seats[:len(set(seats))]
        self.assertEqual(len(first), len(set(first)))

    def test_page_shows_the_other_reading_on_the_pick_row(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn("pick.readings", js, "the pick row and card list the other program's reading")
        self.assertIn("READING_OF", js, "a reading on a shown pick is not listed again under Also watching")


    def test_home_points_are_placed_current_anomalies_and_nothing_else(self):
        def item(geometry, **props):
            return {"type": "Feature", "geometry": geometry,
                    "properties": {"id": "secret-id", "title": "secret title", "tier": "anomaly"} | props}
        point = lambda lon, lat: {"type": "Point", "coordinates": [lon, lat]}
        today = {"features": [
            item(point(10.04, 20.06)),
            item(point(10.01, 20.09)),                                     # the same place to 0.1 degree: one point
            item(point(1, 1), tier="routine"),
            item(point(2, 2), cop_stale=True),
            item(point(3, 3), valid_until="2026-10-31T20:00:00Z"),
            item(point(4, 4), valid_until="2026-10-31T21:00:00Z"),
            item(None),
            item({"type": "MultiPoint", "coordinates": [[-70, 40], [100, -30]]}),   # two places stay two
            item({"type": "Polygon", "coordinates": [[[179, 0], [-179, 0], [-179, 2], [179, 2], [179, 0]]]}),
        ]}
        doc = snap.home_points(today, NOW)
        self.assertEqual(set(doc), {"generated_at", "points"})
        self.assertEqual(doc["generated_at"], "2026-10-31T20:30:00Z")
        across = [p for p in doc["points"] if abs(p[0]) == 180]
        self.assertEqual(len(across), 1, "a shape across the antimeridian stays there")
        self.assertEqual(across[0][1], 1.0)
        self.assertEqual([p for p in doc["points"] if p not in across],
                         [[-70.0, 40.0], [4.0, 4.0], [10.0, 20.1], [100.0, -30.0]])
        self.assertNotIn("secret", json.dumps(doc), "places only: no id or title")

    def test_build_writes_a_small_home_points_file(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            snap.main(["--fixtures", str(FIX), "--now", "2026-10-31T20:30:00Z", "--out", d,
                       "--ended-out", str(Path(d) / "ended")])
            path = Path(d) / "home-points.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(doc["points"], "the fixtures hold placed anomalies")
            self.assertLess(path.stat().st_size, 20000)


if __name__ == "__main__":
    unittest.main()
