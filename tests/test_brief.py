"""Offline tests for COP's daily brief (tools/brief.py). Run as a plain script: python tests/test_brief.py"""

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("brief", ROOT / "tools" / "brief.py")
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)
snap = brief.snap

NOW = datetime(2026, 11, 24, 12, 20, tzinfo=timezone.utc)
ORDER = [p["id"] for p in snap.CONFIG["programs"]]
DRAWN = [p for p in ORDER if p != "vis"]


def item(pid, n=1, **kw):
    p = {"id": f"{pid}:test:{n}", "cop_program": pid, "tier": "anomaly", "kind": "observation",
         "title": f"{pid} item {n}.", "data_as_of": snap.iso(NOW - timedelta(hours=3)), "last_change": "new",
         "baseline_ref": {"window": "previous 28 complete days"},
         "sources": [{"publisher": f"Publisher {pid}"}], "link": f"https://example.org/{pid}/{n}",
         "countries": ["FI"], "cop_stale": False}
    geometry = kw.pop("geometry", {"type": "Point", "coordinates": [25.0, 61.0]})
    p.update(kw)
    return {"type": "Feature", "geometry": geometry, "properties": p}


def snapshot(*features, at=NOW):
    return {"type": "FeatureCollection", "generated_at": snap.iso(at), "features": list(features)}


def verdict(*events, stale=False):
    return {"calendar": {"stale": stale, "data": {"events": list(events)}}}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        brief.main(argv)
    return out.getvalue()


class Choosing(unittest.TestCase):
    def test_one_per_program_at_most_four_in_config_order(self):
        feats = [item(pid, n) for pid in DRAWN for n in (1, 2)]
        got = brief.changed(snapshot(*feats), NOW)
        self.assertEqual(len(got), brief.CHANGED_MAX)
        self.assertEqual([f["properties"]["cop_program"] for f in got], DRAWN[:4])

    def test_what_may_not_be_news_is_left_out(self):
        pid = DRAWN[0]
        bad = [item(pid, 1, cop_stale=True), item(pid, 2, cop_source_stale=True), item(pid, 3, last_change="none"),
               item(pid, 4, tier="routine"), item(pid, 5, geometry=None), item(pid, 6, kind="forecast"),
               item(pid, 7, valid_until=snap.iso(NOW - timedelta(minutes=1))),
               item(pid, 8, data_as_of=snap.iso(NOW - timedelta(hours=brief.RECENT_HOURS + 1))),
               item(pid, 9, data_as_of="2026-11-01T00:00:00Z", data_as_of_precision="month"),
               item(pid, 10, measures={"current": False})]
        self.assertEqual(brief.changed(snapshot(*bad), NOW), [])

    def test_a_whole_day_counts_to_its_end(self):
        f = item(DRAWN[0], data_as_of="2026-11-22T00:00:00Z", data_as_of_precision="day")   # covers to 23 Nov 00:00
        self.assertEqual(len(brief.changed(snapshot(f), NOW)), 1)

    def test_repeats_and_turns(self):
        first = brief.compose(snapshot(*[item(pid) for pid in DRAWN]), None, None, NOW)
        heard = [s["program"] for s in first["sentences"]]
        self.assertEqual(heard, DRAWN[:4])
        later = NOW + timedelta(days=1)
        same = [item(pid, data_as_of=snap.iso(later - timedelta(hours=1))) for pid in DRAWN]
        nxt = brief.compose(snapshot(*same, at=later), None, None, later, first)
        progs = [s["program"] for s in nxt["sentences"]]
        self.assertTrue(set(progs).isdisjoint(heard), "an unchanged item is not said twice, and others go first")
        renamed = [item(pid, title="new title", data_as_of=snap.iso(later)) for pid in DRAWN[:1]]
        again = brief.compose(snapshot(*renamed, at=later), None, None, later, first)
        self.assertEqual([s["program"] for s in again["sentences"]], DRAWN[:1], "a changed title is news again")

    def test_sentence_parts(self):
        b = brief.compose(snapshot(item(DRAWN[0])), None, None, NOW)
        s = b["sentences"][0]
        self.assertEqual(s["link"], f"https://example.org/{DRAWN[0]}/1")
        self.assertEqual(s["publishers"], [f"Publisher {DRAWN[0]}"])
        self.assertIn(f"source: Publisher {DRAWN[0]}", s["text"])
        self.assertIn("compared with the previous 28 complete days", s["text"])
        self.assertTrue(s["text"].endswith(")."))

    def test_baseline_wording(self):
        a = brief.against
        self.assertEqual(a({"baseline_ref": {"window": "fixed: listed by X; kept 72 h"}}), "by a fixed rule: listed by X")
        self.assertEqual(a({"baseline_ref": {"window": "USGS PAGER alert scale (green)"}}),
                         "on the USGS PAGER alert scale (green)")
        self.assertEqual(a({"baseline_ref": {"window": "the warnings in force 72 hours earlier"}}),
                         "compared with the warnings in force 72 hours earlier")
        self.assertEqual(a({"baseline_ref": {"window": "30 days before the last 7"}}),
                         "compared with the 30 days before the last 7")
        self.assertEqual(a({"baseline_ref": {"window": "previous 28 days, weekday-adjusted (log-scale z)"}}),
                         "compared with the previous 28 days, weekday-adjusted (log-scale z)")
        self.assertIsNone(a({}))


class Watching(unittest.TestCase):
    def cal(self, day, cid="ES", n=""):
        return {"id": f"verdict:calendar:{day}{n}", "title": f"Scheduled {day}: Spain: election", "date": day,
                "at": None, "occurred": False, "countries": [cid], "link": "https://www.example.gov/x"}

    def launch(self, at, n=1):
        return {"id": f"orbit:upcoming:{n}", "title": f"Scheduled launch: Rocket {n}, {at[:16].replace('T', ' ')} UTC",
                "date": at[:10], "at": at, "origin": "orbit", "occurred": False, "stale": False, "countries": ["US"],
                "note": "A scheduled time is the provider's.", "link": "https://orbit.example/launches"}

    def test_calendar_first_then_launch(self):
        launches = {"events": [self.launch("2026-11-25T10:00:00Z")]}
        w = brief.watch(launches, verdict(self.cal("2026-11-29")), snapshot(), NOW, set())
        self.assertEqual((w["program"], w["title"], w["publishers"]), ("verdict", "Spain: election", ["example.gov"]))
        w = brief.watch(launches, verdict(self.cal("2026-12-09")), snapshot(), NOW, set())
        self.assertEqual((w["program"], w["title"]), ("orbit", "Scheduled launch: Rocket 1"))
        self.assertIn("provider's", brief.plain(w))
        self.assertIsNone(brief.watch({"events": [self.launch("2026-11-24T10:00:00Z")]}, verdict(), snapshot(), NOW,
                                      set()), "a past launch is not ahead")
        self.assertIsNone(brief.watch(None, verdict(self.cal("2026-11-29"), stale=True), snapshot(), NOW, set()))

    def test_named_country_then_not_said_before(self):
        cal = verdict(self.cal("2026-11-26", "ES", "a"), self.cal("2026-11-28", "FI", "b"), self.cal("2026-11-27", "DE", "c"))
        self.assertEqual(brief.watch(None, cal, snapshot(), NOW, {"FI"})["date"], "2026-11-28")
        got = brief.watch(None, cal, snapshot(), NOW, set(), {"verdict:calendar:2026-11-26a"})
        self.assertEqual(got["date"], "2026-11-27")


class Keeping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.snapfile = self.dir / "today.geojson"
        self.snapfile.write_text(json.dumps(snapshot(*[item(pid) for pid in DRAWN])), encoding="utf-8")
        self.store, self.pub, self.new = self.dir / "store", self.dir / "pub", self.dir / "new.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, now, *extra):
        return run(["--snapshot", str(self.snapfile), "--store", str(self.store), "--publish", str(self.pub),
                    "--new-out", str(self.new), "--now", now, *extra])

    def test_once_a_day_never_replaced(self):
        self.go("2026-11-24T11:59:00Z")
        self.assertEqual(self.new.read_text(encoding="utf-8"), "", "not due before 12:00 UTC")
        self.go("2026-11-24T12:20:00Z")
        path = Path(self.new.read_text(encoding="utf-8"))
        self.assertEqual(path.name, "2026-11-24.json")
        kept = path.read_bytes()
        self.snapfile.write_text(json.dumps(snapshot(item(DRAWN[5], title="other"))), encoding="utf-8")
        self.go("2026-11-24T22:20:00Z")
        self.assertEqual(self.new.read_text(encoding="utf-8"), "")
        self.assertEqual(path.read_bytes(), kept, "a stored brief is never replaced")
        self.go("2026-11-25T12:20:00Z")
        self.assertEqual(sorted(p.name for p in self.store.iterdir()), ["2026-11-24.json", "2026-11-25.json"])
        index = (self.pub / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="2026-11-24.html"', index, "past briefs stay listed")
        self.assertTrue((self.pub / "2026-11-24.html").exists())
        feed = ET.parse(self.pub / "feed.xml").getroot()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        self.assertEqual([e.findtext("a:title", namespaces=ns) for e in feed.findall("a:entry", ns)],
                         ["COP daily brief, 2026-11-25", "COP daily brief, 2026-11-24"])

    def test_seed_draws_past_briefs_and_writes_nothing(self):
        self.go("2026-11-24T12:20:00Z")
        live = self.dir / "briefs.json"
        (self.pub / "briefs.json").replace(live)
        empty = self.dir / "empty-store"
        run(["--store", str(empty), "--publish", str(self.pub), "--render-only", "--seed", str(live)])
        self.assertFalse(empty.exists(), "nothing is written to the store")
        self.assertIn('href="2026-11-24.html"', (self.pub / "index.html").read_text(encoding="utf-8"))
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            brief.main(["--store", str(empty), "--publish", str(self.pub), "--seed", str(live)])

    def test_a_page_for_a_brief_not_kept_goes(self):
        self.go("2026-11-24T12:20:00Z")
        Path(self.new.read_text(encoding="utf-8")).unlink()         # what the build does when the upload fails
        run(["--store", str(self.store), "--publish", str(self.pub), "--render-only"])
        self.assertFalse((self.pub / "2026-11-24.html").exists())

    def test_pages_follow_the_csp_and_name_the_zone(self):
        self.go("2026-11-24T12:20:00Z")
        for page in self.pub.glob("*.html"):
            html = page.read_text(encoding="utf-8")
            self.assertNotIn('style="', html, page.name)
            self.assertNotRegex(html, r"<script(?![^>]* src=)[^>]*>", f"{page.name}: no inline script (CSP)")
            self.assertIn('<script src="../shared/tic-time.js"></script>', html)
            self.assertIn('<script src="../brief.js"></script>', html)
            for m in re.finditer(r'<time datetime="([^"]+)"( data-tic="datetime")?>([^<]*)</time>', html):
                self.assertTrue(m.group(3).endswith("UTC") or m.group(3).endswith("(UTC)"), "the zone is named")
            text = re.sub(r"<[^>]+>", " ", html).lower()
            for w in ("likely", "strike window", "attack likely", "we assess", "probably"):
                self.assertNotIn(w, text, "the voice rule")
        self.assertTrue((ROOT / "site" / "brief.js").exists())


class Pages(unittest.TestCase):
    def test_scoring_page_states_the_brief_rules(self):
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
        for phrase in (f"at or after {brief.BRIEF_HOUR:02d}:00 UTC", f"up to {brief.CHANGED_MAX} sentences",
                       f"into the last {brief.RECENT_HOURS} hours", f"calendar within {brief.WATCH_DAYS} days",
                       f"launch within {brief.WATCH_LAUNCH_HOURS} hours"):
            self.assertIn(phrase, text)
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="brief/"', index, "the map links the brief")
        self.assertIn("site/brief/", (ROOT / ".gitignore").read_text(encoding="utf-8"), "drawn pages are not tracked")


if __name__ == "__main__":
    unittest.main(verbosity=1)
