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

    def test_a_listed_source_not_read_twice_is_named_and_once_is_left_to_the_next_run(self):
        h = health()
        h["programs"].append({"id": "context", "name": "Context", "sources": {"total": 4, "not_read": [
            {"id": "google-news", "state": "failing", "detail": "none answered", "failures": 1},
            {"id": "gdelt-gkg", "state": "failing", "detail": "timeout", "failures": 3},
            {"id": "cop-bus", "state": None, "detail": None, "failures": None}]}})
        self.assertEqual(watch.check_health(h), [
            "Context: source gdelt-gkg is listed but was not read (failing, 3 runs in a row, timeout)",
            "Context: source cop-bus is listed but was not read"])
        h["programs"][-1]["sources"] = {"total": 4, "not_read": []}
        self.assertEqual(watch.check_health(h), [])

    def _vigil(self, state="stale", detail="Last run read 8 rows, under its floor of 500", failures=None):
        h = health()
        h["programs"].append({"id": "vigil", "name": "Vigil", "sources": {"total": 40, "not_read": [
            {"id": "threatcluster", "state": state, "detail": detail, "failures": failures}]}})
        return h

    ENTRY = {"program": "vigil", "source": "threatcluster", "state": "stale", "detail_has": "under its floor",
             "until": "2026-12-31", "reason": "a capped free file, kept on purpose"}

    def test_a_documented_degraded_source_passes_and_is_named(self):
        notes = []
        self.assertEqual(watch.check_health(self._vigil(), [self.ENTRY], NOW, notes), [])
        self.assertEqual(len(notes), 1)
        self.assertIn("accepted: Vigil: source threatcluster (stale, Last run read 8 rows, under its floor of 500)", notes[0])
        self.assertIn("until 2026-12-31: a capped free file, kept on purpose", notes[0])

    def test_a_source_reading_differently_from_its_documentation_is_a_problem_again(self):
        for h in (self._vigil(state="failing"), self._vigil(detail="HTTP 503 from the file"),
                  self._vigil(state="failing", detail="HTTP 503", failures=3)):
            notes = []
            problems = watch.check_health(h, [self.ENTRY], NOW, notes)
            self.assertEqual(len(problems), 1, problems)
            self.assertEqual(notes, [])
        other = dict(self.ENTRY, source="threatfox")
        self.assertEqual(len(watch.check_health(self._vigil(), [other], NOW)), 1, "another source is not covered")
        other = dict(self.ENTRY, program="orbit")
        self.assertEqual(len(watch.check_health(self._vigil(), [other], NOW)), 1, "another program is not covered")

    def test_the_documentation_lapses_on_its_date(self):
        last = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(watch.check_health(self._vigil(), [self.ENTRY], last), [])
        late = datetime(2027, 1, 1, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(len(watch.check_health(self._vigil(), [self.ENTRY], late)), 1)
        self.assertEqual(len(watch.check_health(self._vigil(), [self.ENTRY])), 1, "no clock, no acceptance")

    def test_an_incomplete_entry_accepts_nothing_and_says_so(self):
        good, problems = watch.load_accepted({"accepted": [dict(self.ENTRY, reason=""), dict(self.ENTRY, until="soon"),
                                                          {"program": "vigil"}, self.ENTRY]})
        self.assertEqual(good, [self.ENTRY])
        self.assertEqual(len(problems), 3)
        self.assertEqual(len(watch.load_accepted({})[1]), 1)
        self.assertEqual(len(watch.load_accepted([])[1]), 1)

    def test_run_passes_with_a_documented_source_and_fails_without_the_file(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "acc.json").write_text(json.dumps({"accepted": [self.ENTRY]}), encoding="utf-8")
            code, text = run(tmp, snap(), self._vigil(), ["--accepted", str(tmp / "acc.json"), "--sitecheck", "", "--verdict", ""])
            self.assertEqual((code, text.splitlines()[0][:22]), (0, "all programs current a"), text)
            self.assertIn("accepted: Vigil: source threatcluster", text)
            code, text = run(tmp, snap(), self._vigil(), ["--accepted", str(tmp / "missing.json"), "--sitecheck", "", "--verdict", ""])
            self.assertEqual(code, 1)
            self.assertIn("watch_accepted.json could not be read", text)
            self.assertIn("source threatcluster is listed but was not read", text)

    def test_the_shipped_list_is_complete_and_names_only_documented_sources(self):
        import json
        doc = json.loads((ROOT / "config" / "watch_accepted.json").read_text(encoding="utf-8"))
        good, problems = watch.load_accepted(doc)
        self.assertEqual(problems, [])
        self.assertEqual([(e["program"], e["source"]) for e in good], [("vigil", "threatcluster")])

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


def sitecheck(finished="2026-11-14T17:11:00Z", **bad):
    """The hub's site check as tic-sitecheck/1 writes it: one result per page, ok or with its problems."""
    results = [{"id": "hub", "url": "https://theintelligence.company/", "ok": True, "problems": [],
                "failed_requests": ["404 https://theintelligence.company/favicon.ico"]}]
    for pid, problems in bad.items():
        results.append({"id": pid, "url": f"https://{pid}.theintelligence.company/", "ok": False,
                        "problems": problems})
    return {"contract": "tic-sitecheck/1", "finished_at": finished, "sites": len(results),
            "ok": sum(r["ok"] for r in results), "results": results}


class VerdictAge(unittest.TestCase):
    def test_a_recent_scorecard_has_no_problems(self):
        self.assertEqual(watch.check_verdict({"generated_at": "2026-11-13T21:00:00Z"}, NOW), [])

    def test_a_scorecard_over_48_hours_old_is_named(self):
        out = watch.check_verdict({"generated_at": "2026-11-12T20:00:00Z"}, NOW)
        self.assertEqual(len(out), 1)
        self.assertIn("Verdict", out[0])
        self.assertIn("49.0 h", out[0])

    def test_no_build_time_or_a_wrong_shape_is_a_problem_not_quiet(self):
        self.assertEqual(len(watch.check_verdict({}, NOW)), 1)
        self.assertEqual(len(watch.check_verdict([], NOW)), 1)


class SiteCheck(unittest.TestCase):
    def test_every_page_passing_has_no_problems_and_failed_requests_are_evidence_only(self):
        self.assertEqual(watch.check_sitecheck(sitecheck(), NOW), [])

    def test_a_failed_page_is_named_with_its_reasons_and_address(self):
        problems = watch.check_sitecheck(sitecheck(cordon=["no house bar", "map did not draw"]), NOW)
        self.assertEqual(problems, ["Site check: cordon page failed (no house bar; map did not draw), "
                                    "https://cordon.theintelligence.company/"])

    def test_an_old_run_means_the_site_check_stopped(self):
        problems = watch.check_sitecheck(sitecheck(finished="2026-11-14T05:11:00Z"), NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn("last run finished 15.8 h ago (limit 14 h)", problems[0])
        self.assertEqual(watch.check_sitecheck(sitecheck(finished="2026-11-14T08:00:00Z"), NOW), [])

    def test_a_document_of_the_wrong_shape_is_a_problem_not_quiet(self):
        for bad in ({}, [], "x", {"contract": "tic-sitecheck/1", "finished_at": "2026-11-14T17:11:00Z"},
                    {"contract": "tic-sitecheck/1", "finished_at": "2026-11-14T17:11:00Z", "results": []}):
            self.assertTrue(watch.check_sitecheck(bad, NOW), bad)

    def test_a_failed_page_fails_the_run_when_a_site_check_is_given(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sc = Path(d) / "sitecheck.json"
            sc.write_text(json.dumps(sitecheck(finished=datetime.now(timezone.utc).isoformat(),
                                               elements=["HTTP 503"])), encoding="utf-8")
            code, out = run(Path(d), snap(), health(), ["--sitecheck", str(sc)])
            self.assertEqual(code, 1)
            self.assertIn("Site check: elements page failed (HTTP 503)", out)
            code, out = run(Path(d), snap(), health(), ["--sitecheck", str(Path(d) / "missing.json")])
            self.assertEqual(code, 1)
            self.assertIn("pages were not checked", out)

    def test_a_local_snapshot_reads_no_site_check_unless_asked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run(Path(d), snap(), health())[0], 0)
        self.assertEqual(watch.SITECHECK, "https://tic-sitecheck.metacog.workers.dev/")


if __name__ == "__main__":
    unittest.main()
