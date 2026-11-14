"""The suite watchdog: python tests/test_suite_watch.py"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import suite_watch as watch  # noqa: E402

NOW = datetime(2026, 11, 14, 21, 0, tzinfo=timezone.utc)


def snap(generated="2026-11-14T20:20:00Z", **programs):
    progs = {"vis": {"name": "Vis", "state": "fresh", "data_as_of": "2026-11-14T20:00:00Z"}}
    progs.update(programs)
    return {"generated_at": generated, "cop_programs": progs}


class Watch(unittest.TestCase):
    def test_a_current_picture_has_no_problems(self):
        self.assertEqual(watch.check(snap(), NOW), [])

    def test_an_old_snapshot_means_the_build_stopped(self):
        problems = watch.check(snap(generated="2026-11-14T10:00:00Z"), NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn("COP: snapshot is 11.0 h old", problems[0])

    def test_a_program_that_is_not_fresh_is_named(self):
        problems = watch.check(snap(vigil={"name": "Vigil", "state": "stale",
                                           "data_as_of": "2026-11-12T00:00:00Z"}), NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn('Vigil: state is "stale"', problems[0])

    def test_a_data_time_in_the_future_is_caught_though_it_reads_fresh(self):
        problems = watch.check(snap(elements={"name": "Elements", "state": "fresh",
                                              "data_as_of": "2026-11-14T23:00:00Z"}), NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn("Elements", problems[0])
        self.assertIn("2.0 h in the future", problems[0])

    def test_a_few_minutes_ahead_is_allowed(self):
        self.assertEqual(watch.check(snap(elements={"name": "Elements", "state": "fresh",
                                                    "data_as_of": "2026-11-14T21:20:00Z"}), NOW), [])

    def test_no_build_time_and_no_programs_are_problems_not_quiet(self):
        problems = watch.check({"features": []}, NOW)
        self.assertEqual(len(problems), 2)

    def test_an_unreadable_snapshot_exits_1(self):
        self.assertEqual(watch.main(["--snapshot", str(ROOT / "tests" / "no-such-file.geojson")]), 1)

    def test_a_test_failure_exits_1_and_says_it_is_a_test(self):
        import contextlib
        import io
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "today.geojson"
            path.write_text(json.dumps(snap()), encoding="utf-8")
            args = ["--snapshot", str(path), "--cop-hours", "1e9"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(watch.main(args), 0, "the fixture alone must pass")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(watch.main(args + ["--test-failure"]), 1)
        self.assertIn("TEST:", out.getvalue())

    def test_dispatched_runs_never_test(self):
        text = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")
        self.assertIn("default: false", text)
        self.assertIn("inputs.test_failure", text)

    def test_the_workflow_runs_apart_from_the_build_and_keeps_the_ping_address_secret(self):
        text = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/suite_watch.py", text)
        self.assertIn("secrets.HC_PING_URL", text)
        self.assertNotIn("hc-ping.com", text, "the address lives in the secret, never in the file")
        self.assertNotIn("push:", text, "a push to main must not start the watchdog")


if __name__ == "__main__":
    unittest.main()
