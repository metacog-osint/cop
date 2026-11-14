"""Offline tests for the MCP server's data files (tools/agent_index.py). Run as a plain script:
python tests/test_agent_index.py"""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
spec = importlib.util.spec_from_file_location("agent_index", ROOT / "tools" / "agent_index.py")
ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)
snap = ai.snap

NOW = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
IDS = [p["id"] for p in snap.CONFIG["programs"]]


def bus():
    feeds, reps = {}, {}
    for pid in IDS:
        feeds[pid], reps[pid] = snap.read_local(FIX / f"{pid}.geojson")
    today, _picks, health, _countries = snap.build(feeds, reps, None, NOW)
    return today, health


def verdict_docs():
    return {part: json.loads((FIX / "verdict" / name).read_text(encoding="utf-8"))
            for part, name in ai.VERDICT_FILES.items()}


class AgentIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.today, cls.health = bus()
        cls.index, cls.shards = ai.build_index(cls.today, NOW)

    def test_every_item_once_in_the_index_and_once_in_its_shard(self):
        ids = [f["properties"]["id"] for f in self.today["features"]]
        rows = self.index["rows"]
        col = self.index["fields"].index
        self.assertEqual([r[col("id")] for r in rows], ids)
        in_shards = [i for s in self.shards for i in s["items"]]
        self.assertEqual(sorted(in_shards), sorted(ids))
        for r in rows:
            self.assertIn(r[col("id")], self.shards[r[col("shard")]]["items"])

    def test_shards_keep_every_field(self):
        by_id = {i: f for s in self.shards for i, f in s["items"].items()}
        for f in self.today["features"]:
            self.assertEqual(by_id[f["properties"]["id"]], f)

    def test_rows_match_the_columns(self):
        self.assertEqual(self.index["fields"], list(ai.FIELDS))
        for r in self.index["rows"]:
            self.assertEqual(len(r), len(ai.FIELDS))
        self.assertEqual(self.index["shards"], ai.SHARDS)
        self.assertEqual(len(self.shards), ai.SHARDS)

    def test_rows_stay_small(self):
        # The real 6 Nov bus made 278-290 KB for 956-996 items, about 300 bytes a row. A row that grows much past
        # that eats into the MCP server's CPU budget (see INDEX_WARN_BYTES in tools/agent_index.py).
        size = len(json.dumps(self.index, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertLess(size / len(self.index["rows"]), 330)

    def test_index_carries_the_bus_time_and_attribution(self):
        self.assertEqual(self.index["bus_generated_at"], self.today["generated_at"])
        for pid in IDS:
            self.assertTrue(self.index["programs"][pid]["attribution"], pid)

    def test_place(self):
        self.assertEqual(ai.place(None), (None, None))
        self.assertEqual(ai.place({"type": "Point", "coordinates": [10.12345, -2.5]}),
                         ([10.123, -2.5], [10.123, -2.5, 10.123, -2.5]))
        poly = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 2], [0, 2], [0, 0]]]}
        self.assertEqual(ai.place(poly), ([2.0, 1.0], [0, 0, 4, 2]))
        self.assertEqual(ai.place({"type": "MultiPoint", "coordinates": [[1, 1], [3, 5]]}), ([2.0, 3.0], [1, 1, 3, 5]))
        gc = {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [1, 1]},
                                                           {"type": "Point", "coordinates": [3, 3]}]}
        self.assertEqual(ai.place(gc), ([2.0, 2.0], [1, 1, 3, 3]))
        self.assertEqual(ai.place({"type": "Point", "coordinates": []}), (None, None))

    def test_a_point_has_no_separate_box(self):
        col = self.index["fields"].index
        pts = [r for r in self.index["rows"] if r[col("point")] and not r[col("bbox")]]
        self.assertTrue(pts)


class Programs(unittest.TestCase):
    def test_every_registry_program_in_order_with_health_for_cop_layers(self):
        today, health = bus()
        reg = snap.kittips.registry()
        doc = ai.build_programs(reg, health, today, NOW)
        self.assertEqual([p["id"] for p in doc["programs"]], [p["id"] for p in reg["programs"]])
        by = {p["id"]: p for p in doc["programs"]}
        for pid in IDS:
            self.assertIsNotNone(by[pid]["cop"], pid)
            self.assertIn(by[pid]["cop"]["state"], ("fresh", "late", "stale", "failed", "unknown"), pid)
        planned = [p for p in doc["programs"] if p["status"] == "planned"]
        self.assertTrue(planned)
        for p in planned:
            self.assertIsNone(p["cop"], p["id"])
        self.assertEqual(doc["bus_generated_at"], today["generated_at"])


class Verdict(unittest.TestCase):
    def test_all_parts_fresh(self):
        v = ai.build_verdict(verdict_docs(), {}, None, NOW)
        self.assertEqual(v["missing"], [])
        for part in ai.VERDICT_FILES:
            self.assertFalse(v[part]["stale"], part)
        self.assertEqual(len(v["forecasts"]["data"]["items"]), 8)
        events = v["calendar"]["data"]["events"]
        self.assertEqual([e["date"] for e in events], sorted(e["date"] for e in events))
        self.assertTrue(all(e["origin"] in ("verdict", "cordon") for e in events))

    def test_a_failed_part_keeps_its_last_copy_marked_stale(self):
        good = ai.build_verdict(verdict_docs(), {}, None, NOW)
        docs = verdict_docs()
        docs["calendar"] = None
        v = ai.build_verdict(docs, {"calendar": {"ok": False, "error": "HTTP 503"}}, good, NOW)
        self.assertTrue(v["calendar"]["stale"])
        self.assertEqual(v["calendar"]["error"], "HTTP 503")
        self.assertEqual(v["calendar"]["data"], good["calendar"]["data"])
        self.assertFalse(v["forecasts"]["stale"])
        self.assertEqual(v["missing"], [])

    def test_a_failed_part_with_no_last_copy_is_missing(self):
        docs = verdict_docs()
        docs["scores"] = None
        v = ai.build_verdict(docs, {}, None, NOW)
        self.assertIsNone(v["scores"])
        self.assertEqual(v["missing"], ["scores"])


class ProgramEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.today, _health = bus()
        cls.doc = ai.build_events(cls.today, NOW)

    @staticmethod
    def launch(title_time="2026-11-14 17:32 UTC", precision="Minute", **extra):
        return {"id": "orbit:upcoming:x", "title": f"Scheduled launch: Rocket | Payload (Go), {title_time}",
                "summary": f"Launch Library 2 lists Rocket | Payload for {title_time} (status Go for Launch; "
                           f"NET precision {precision}) from Pad 1.",
                "countries": ["US"], "sources": [{"url": "https://ll.example/1"}], **extra}

    def test_every_fixture_launch_becomes_an_event(self):
        upcoming = [f for f in self.today["features"] if f["properties"]["id"].startswith("orbit:upcoming:")]
        self.assertEqual(len(self.doc["events"]), len(upcoming))
        self.assertEqual(self.doc["skipped"], {})
        self.assertEqual(self.doc["programs"]["orbit"]["name"], "Orbit")
        for e in self.doc["events"]:
            self.assertEqual((e["origin"], e["kind"], e["verified"], e["occurred"]), ("orbit", "launch", False, False))
            self.assertTrue(e["at"] is None or e["at"].startswith(e["date"]), e["id"])
        keys = [(e["date"], e["at"] or "", e["id"]) for e in self.doc["events"]]
        self.assertEqual(keys, sorted(keys))

    def test_time_kept_only_to_the_stated_precision(self):
        e, _ = ai.orbit_event(self.launch(precision="Minute"))
        self.assertEqual((e["date"], e["at"], e["launch_status"]), ("2026-11-14", "2026-11-14T17:32:00Z", "Go for Launch"))
        self.assertEqual(e["source_url"], "https://ll.example/1")
        for day in ("Day", "Morning (local)"):
            e, _ = ai.orbit_event(self.launch("2026-11-14 00:00 UTC", day))
            self.assertEqual((e["date"], e["at"], e["net_precision"]), ("2026-11-14", None, day))
        for coarse in ("Week", "Month", "1st Quarter"):
            e, why = ai.orbit_event(self.launch("2026-12-01 00:00 UTC", coarse))
            self.assertIsNone(e)
            self.assertEqual(why, f"NET precision {coarse}")

    def test_no_time_is_skipped_and_counted(self):
        bad = self.launch()
        bad["title"] = bad["summary"] = "Scheduled launch: Rocket | Payload (Go)"
        today = {"features": [{"properties": bad}], "generated_at": "2026-11-06T14:00:00Z"}
        doc = ai.build_events(today, NOW)
        self.assertEqual(doc["events"], [])
        self.assertEqual(doc["skipped"], {"no time in the title or summary": 1})

    def test_an_items_own_event_object_wins(self):
        e, _ = ai.orbit_event(self.launch(event={"date": "2026-11-15", "at": "2026-11-15T01:00:00Z"}))
        self.assertEqual((e["date"], e["at"]), ("2026-11-15", "2026-11-15T01:00:00Z"))


class EndToEnd(unittest.TestCase):
    def test_main_writes_every_file_and_nothing_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = "2026-11-06T15:00:00Z"
            self.assertEqual(snap.main(["--fixtures", str(FIX), "--out", tmp, "--now", now]), 0)
            self.assertEqual(ai.main(["--data", tmp, "--verdict", str(FIX / "verdict"), "--now", now]), 0)
            agent = Path(tmp) / "agent"
            for name in ("index.json", "programs.json", "verdict.json", "events.json"):
                self.assertTrue((agent / name).exists(), name)
            self.assertEqual(len(list((agent / "items").glob("*.json"))), ai.SHARDS)
            for p in agent.rglob("*.json"):
                self.assertNotIn("commercial_ok", p.read_text(encoding="utf-8"), p.name)


if __name__ == "__main__":
    unittest.main()
