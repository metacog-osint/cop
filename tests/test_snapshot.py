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
