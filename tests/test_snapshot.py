"""Offline tests for COP's snapshot builder and design rules. Run as a plain script: python tests/test_snapshot.py"""

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
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


class Card(unittest.TestCase):
    WORLD = {"features": [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [40, 0], [40, 30], [0, 30], [0, 0]]]}}]}

    def test_card_lists_every_placed_pick_and_escapes_titles(self):
        def poison(f):
            f["noisefloor"]["features"][0]["properties"]["title"] = "<script>x</script>"
        today, picks, health, _ = build(mutate=poison)
        page = card.build_html(picks, today, health, self.WORLD)
        self.assertEqual(page.count("<li>"), len(picks["picks"]))
        self.assertNotIn("<script>x", page)
        self.assertIn("Assessed 31 Oct 2026, 20:30 UTC", page)
        self.assertIn("Measurements, not warnings", page)

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
