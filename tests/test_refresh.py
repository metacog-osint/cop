"""Refresh times (tools/refresh.py, health.json `refresh`, site/refresh.html). Run: python tests/test_refresh.py"""

import importlib.util
import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import refresh as rf  # noqa: E402

NOW = datetime(2026, 11, 27, 8, 20, tzinfo=timezone.utc)


def at(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def lane(checked, state="fresh"):
    return {"state": state, "checked_at": checked, "last_ok": checked, "failures": 0, "detail": "x"}


class Clock(unittest.TestCase):
    def test_every_registry_program_has_a_schedule(self):
        spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
        snap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(snap)
        self.assertEqual(sorted(rf.CADENCE["programs"]), sorted(p["id"] for p in snap.CONFIG["programs"]))
        for pid, c in rf.CADENCE["programs"].items():
            self.assertGreater(c["every_hours"], 0, pid)
            self.assertTrue(c["runs"] and c["scheduler"], pid)
            self.assertIn(c.get("last_update", "lanes"), ("lanes", "generated", "data"), pid)

    def test_cordon_on_time_then_late(self):
        """Every 30 minutes: late once the newest lane check is more than 2 x 0.5 h + 0.5 h = 1.5 h old."""
        feed = {"generated_at": at(0.2), "health": {"a": lane(at(0.9)), "b": lane(at(1.2))}}
        c = rf.clock("cordon", feed, NOW)
        self.assertEqual((c["status"], c["last_update"], c["last_update_basis"]), ("on_time", at(0.9), "lanes"))
        self.assertEqual(c["late_after_hours"], 1.5)
        self.assertEqual(sorted(c["lanes"]), ["a", "b"])
        feed["health"] = {"a": lane(at(1.6))}
        self.assertEqual(rf.clock("cordon", feed, NOW)["status"], "late")

    def test_a_live_feed_is_judged_by_its_sources_not_its_own_time(self):
        """Context's feed is served live: its generated_at is the moment COP asked. Its last update is the run."""
        feed = {"generated_at": at(0), "health": {"gdelt-gkg": lane(at(8.1))}}
        c = rf.clock("context", feed, NOW)
        self.assertEqual((c["last_update"], c["status"]), (at(8.1), "on_time"))
        feed["health"]["gdelt-gkg"] = lane(at(25))
        self.assertEqual(rf.clock("context", feed, NOW)["status"], "late", "two 12-hour runs missed")

    def test_a_feed_with_no_health_block_uses_its_own_time(self):
        c = rf.clock("launch", {"generated_at": at(13)}, NOW)
        self.assertEqual((c["last_update_basis"], c["status"], c["lanes"]), ("generated", "late", {}))

    def test_watchfloor_by_the_end_of_its_newest_day(self):
        end = datetime(2026, 11, 27, tzinfo=timezone.utc)
        c = rf.clock("watchfloor", {"generated_at": at(0)}, NOW, end)
        self.assertEqual((c["last_update"], c["last_update_basis"], c["status"]), ("2026-11-27T00:00:00Z", "data", "on_time"))
        self.assertEqual(rf.clock("watchfloor", {"generated_at": at(0)}, NOW, end - timedelta(days=3))["status"], "late")

    def test_a_feed_cop_could_not_read(self):
        c = rf.clock("cordon", None, NOW)
        self.assertEqual((c["status"], c["last_update"]), ("not_read", None))

    def test_a_time_in_the_future_is_taken_as_now(self):
        c = rf.clock("cordon", {"health": {"a": lane(at(-2))}}, NOW)
        self.assertEqual((c["last_update"], c["age_hours"], c["status"]), (at(0), 0, "on_time"))

    def test_the_snapshot_writes_it_into_health(self):
        spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
        snap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(snap)
        feeds, reports = {}, {}
        for p in snap.CONFIG["programs"]:
            feeds[p["id"]], reports[p["id"]] = snap.read_local(ROOT / "tests" / "fixtures" / f"{p['id']}.geojson")
        feeds["cordon"], reports["cordon"] = None, {"ok": False, "status": None, "seconds": 0, "error": "test"}
        now = datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc)
        _, _, health, _ = snap.build(feeds, reports, None, now)
        by = {h["id"]: h["refresh"] for h in health["programs"]}
        self.assertEqual(by["cordon"]["status"], "not_read")
        self.assertTrue(all(r["status"] in ("on_time", "late", "not_read", "unknown") for r in by.values()))
        self.assertEqual(health["refresh"]["cop"]["last_update"], "2026-10-31T20:30:00Z")
        self.assertEqual(set(health["refresh"]["method"]["lane_states"]),
                         {"fresh", "late", "stale", "failing", "never", "up"})


class Page(unittest.TestCase):
    def test_the_method_states_the_rule_the_code_uses(self):
        text = rf.CADENCE["late_rule"] + " " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")))
        words = {1: "one", 2: "two", 3: "three"}
        self.assertIn(f"more than {words[rf.LATE_INTERVALS]} expected intervals plus half an hour", text)
        self.assertEqual(rf.LATE_GRACE_H, 0.5)

    def test_the_page_is_linked_and_safe(self):
        html = (ROOT / "site" / "refresh.html").read_text(encoding="utf-8")
        js = (ROOT / "site" / "refresh.js").read_text(encoding="utf-8")
        self.assertIn('<script type="module" src="refresh.js"></script>', html)
        self.assertIn('<script src="shared/tic-time.js"></script>', html, "times in the viewer's zone")
        self.assertNotIn('style="', html)
        self.assertNotRegex(html, r"<script(?![^>]* src=)[^>]*>", "no inline script (CSP)")
        self.assertNotIn("innerHTML", js)
        self.assertIn('getJSON("data/health.json")', js)
        self.assertIn('"data-tic": "datetime"', js)
        for page in ("index.html", "scoring.html"):
            self.assertIn('href="refresh.html"', (ROOT / "site" / page).read_text(encoding="utf-8"), page)


if __name__ == "__main__":
    unittest.main()
