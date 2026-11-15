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


def health(**counts):
    """health.json as snapshot.py writes it: a list of programs, each with dropped {count, examples}."""
    progs = [{"id": "vis", "name": "Vis", "dropped": {"count": 0, "examples": []}}]
    for pid, (n, examples) in counts.items():
        progs.append({"id": pid, "name": pid.capitalize(), "dropped": {"count": n, "examples": examples}})
    return {"generated_at": "2026-11-14T20:20:00Z", "programs": progs}


def run(tmp, snapshot, health_doc=None, extra=()):
    """main() on files in tmp; returns (exit code, printed text). health_doc None writes no health.json."""
    import contextlib
    import io
    import json
    (tmp / "today.geojson").write_text(json.dumps(snapshot), encoding="utf-8")
    if health_doc is not None:
        (tmp / "health.json").write_text(json.dumps(health_doc), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = watch.main(["--snapshot", str(tmp / "today.geojson"), "--cop-hours", "1e9", *extra])
    return code, out.getvalue()


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
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run(Path(d), snap(), health())[0], 0, "the fixture alone must pass")
            code, out = run(Path(d), snap(), health(), ["--test-failure"])
        self.assertEqual(code, 1)
        self.assertIn("TEST:", out)

    # health.json (15 Nov 2026): the build leaves a malformed item out instead of stopping, so the watchdog says so.
    def test_a_clean_health_file_has_no_problems(self):
        self.assertEqual(watch.check_health(health()), [])

    def test_a_malformed_item_left_out_is_named_with_its_program_and_reason(self):
        problems = watch.check_health(health(vigil=(2, ["vigil:x: sources is not a list", "item 4 of the feed: id"])))
        self.assertEqual(len(problems), 1)
        self.assertIn("Vigil: 2 malformed items left out of the picture", problems[0])
        self.assertIn("vigil:x: sources is not a list", problems[0])

    def test_one_item_reads_singular_and_each_program_is_its_own_line(self):
        problems = watch.check_health(health(vigil=(1, []), orbit=(3, ["orbit:y: NaN"])))
        self.assertEqual(problems, ["Vigil: 1 malformed item left out of the picture",
                                    "Orbit: 3 malformed items left out of the picture (orbit:y: NaN)"])

    def test_a_health_file_of_the_wrong_shape_is_a_problem_not_quiet(self):
        for bad in ({}, {"programs": []}, {"programs": {"vis": {}}}, [], "x"):
            self.assertEqual(len(watch.check_health(bad)), 1, bad)
        self.assertEqual(watch.check_health({"programs": [None, {"id": "vis", "dropped": "x"}]}), [])

    def test_dropped_items_fail_the_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code, out = run(Path(d), snap(), health(cordon=(1, ["cordon:z: geometry has no rings"])))
        self.assertEqual(code, 1)
        self.assertIn("Cordon: 1 malformed item", out)

    def test_a_missing_health_file_fails_the_run_and_says_what_was_not_checked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code, out = run(Path(d), snap())
        self.assertEqual(code, 1)
        self.assertIn("health.json could not be read", out)
        self.assertIn("malformed items were not checked", out)

    def test_health_json_is_read_beside_the_snapshot_on_the_site(self):
        self.assertEqual(watch.health_beside(watch.SNAPSHOT), "https://cop.theintelligence.company/data/health.json")

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
