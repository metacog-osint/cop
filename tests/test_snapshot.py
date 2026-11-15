"""Offline tests for COP's snapshot builder and design rules. Run as a plain script: python tests/test_snapshot.py"""

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
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

    def test_noisefloor_events_outrank_new_areas(self):
        today, picks, _, _ = build()
        nf = [p for p in picks["picks"] if p["program"] == "noisefloor"]
        self.assertTrue(nf and "% of aircraft with degraded GPS" in nf[0]["reason"], nf)

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
        self.assertEqual(sum(1 for v in tags.values() if v), 2, "nothing else is tagged")
        cordon = next(p for p in health["config"]["programs"] if p["id"] == "cordon")
        self.assertEqual(cordon["also_in"], [{"preset": "spectrum", "label": "launch, space-debris and reentry warnings"}])
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn('["coalesce", ["get", "cop_also"], ""]', js)
        self.assertRegex(js, r'const MAP_KEYS = \[[^\]]*"cop_also"', "the map's copy of each item keeps the tag")

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

    def test_page_reads_valid_until(self):
        js = (ROOT / "site" / "cop.js").read_text(encoding="utf-8")
        self.assertIn("Date.parse(f.properties.valid_until)", js)
        self.assertRegex(js, r'const MAP_KEYS = \[[^\]]*"cop_expired"', "the map's copy of each item knows it expired")
        self.assertIn("const standsOut = (p) => STANDS_OUT.includes(p.tier) && !p.cop_expired;", js)
        self.assertIn('["in", ["get", "tier"], ["literal", STANDS_OUT]], NOT_EXPIRED]', js)
        self.assertIn('fact(p.cop_expired ? "Expired" : "Valid until", timeEl(p.valid_until))', js)

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
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script src=\"http", html)
        self.assertNotIn("style=\"", html)


if __name__ == "__main__":
    unittest.main()
