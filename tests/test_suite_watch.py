"""The suite watchdog: python tests/test_suite_watch.py"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_an_entry_without_a_state_accepts_nothing(self):
        """An entry holds only while its source reads in the state it names: one naming none would accept the source
        in any state, a source gone from stale to failing included."""
        no_state = {k: v for k, v in self.ENTRY.items() if k != "state"}
        good, problems = watch.load_accepted({"accepted": [no_state]})
        self.assertEqual((good, len(problems)), ([], 1))
        self.assertEqual(len(watch.check_health(self._vigil(state="failing"), good, NOW)), 1)

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
        self.assertEqual(watch.beside(watch.SNAPSHOT, "health.json"), "https://cop.theintelligence.company/data/health.json")

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


def build(n):
    """The snapshot time of build n: each run that reads a new build gets a later one."""
    return f"2026-11-14T{n:02d}:20:00Z"


LATE = {"name": "Vigil", "state": "late", "data_as_of": "2026-11-14T00:00:00Z"}
STALE = {"name": "Vigil", "state": "stale", "data_as_of": "2026-11-13T00:00:00Z"}


class TwoInARow(unittest.TestCase):
    """With --state, a problem one bad read can cause fails the run only when the run before saw it too; every other
    problem fails at once. Each test drives main() through a sequence of runs."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def go(self, snapshot, health_doc=None, extra=(), snapshot_error=None):
        """One run with the record kept in self.state; snapshot_error makes the snapshot unreadable with it."""
        from unittest import mock
        args = ["--state", str(self.state), *extra]
        if snapshot_error is None:
            return run(self.dir, snapshot, health_doc or health(), args)
        real = watch.read_json

        def read(where):
            if where.endswith("today.geojson"):
                raise snapshot_error
            return real(where)
        with mock.patch.object(watch, "read_json", read):
            return run(self.dir, snapshot, health_doc or health(), args)

    def codes(self, runs):
        return [self.go(*r)[0] for r in runs]

    def write_record(self, hours_ago, seen=(), generated_at=None):
        import json
        at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        self.state.write_text(json.dumps({"format": watch.STATE_FORMAT, "at": f"{at:%Y-%m-%dT%H:%M:%SZ}",
                                          "generated_at": generated_at, "seen": list(seen)}), encoding="utf-8")

    # 1. A problem's identity is its id, not its wording.
    def test_each_problem_carries_a_stable_id(self):
        p = watch.check(snap(vigil=STALE), NOW)[0]
        self.assertEqual((p.id, p.waits), ("state:vigil", True))
        self.assertEqual(watch.check(snap(vigil=LATE), NOW)[0].id, "state:vigil")
        h = health(elements=(4, ["elements:a: no time"]))
        self.assertEqual([(p.id, p.waits) for p in watch.check_health(h)], [("dropped:elements", False)])
        h["programs"].append({"id": "blackout", "name": "Blackout", "sources": {"not_read": [
            {"id": "ioda-gatech", "state": "failing", "detail": "HTTP 503", "failures": 2}]}})
        self.assertEqual(watch.check_health(h)[-1].id, "unread:blackout:ioda-gatech")
        self.assertEqual(watch.check_sitecheck(sitecheck(orbit=["map did not draw"]), NOW)[0].id,
                         "sitecheck-page:orbit")
        self.assertEqual(watch.check_verdict({"generated_at": "2026-11-01T00:00:00Z"}, NOW)[0].id, "verdict-age")

    def test_malformed_items_with_other_counts_and_ids_fail_every_run(self):
        runs = [(snap(generated=build(0)),)]
        for n, (count, item) in enumerate(((4, "a"), (5, "b"), (3, "c"), (6, "d")), start=1):
            runs.append((snap(generated=build(n)), health(elements=(count, [f"elements:{item}: no time"]))))
        self.assertEqual(self.codes(runs), [0, 1, 1, 1, 1])

    def test_an_unreadable_snapshot_with_other_errors_fails_the_second_run(self):
        import urllib.error
        errors = [urllib.error.HTTPError(watch.SNAPSHOT, 502, "Bad Gateway", {}, None),
                  urllib.error.HTTPError(watch.SNAPSHOT, 503, "Service Unavailable", {}, None),
                  TimeoutError("timed out")]
        codes = [self.go(snap(generated=build(0)))[0]] + [self.go(snap(), snapshot_error=e)[0] for e in errors]
        self.assertEqual(codes, [0, 0, 1, 1])

    def test_late_then_stale_is_one_problem_and_keeps_failing(self):
        runs = [(snap(generated=build(0)),)] + [(snap(generated=build(n), vigil=v),)
                                                for n, v in enumerate((LATE, LATE, STALE, STALE), start=1)]
        self.assertEqual(self.codes(runs), [0, 0, 1, 1, 1])

    def test_first_sighting_warns_second_fails_and_a_clear_run_forgets(self):
        self.assertEqual(self.go(snap(generated=build(0)))[0], 0)
        code, out = self.go(snap(generated=build(1), vigil=STALE))
        self.assertEqual(code, 0)
        self.assertIn("first sighting", out)
        self.assertIn("::warning::", out)
        code, out = self.go(snap(generated=build(2), vigil=STALE))
        self.assertEqual(code, 1)
        self.assertIn('::error::Vigil: state is "stale"', out)
        self.assertEqual(self.go(snap(generated=build(3)))[0], 0)
        self.assertEqual(self.go(snap(generated=build(4), vigil=STALE))[0], 0, "cleared in between: first again")

    def test_a_blip_between_good_runs_never_fails(self):
        late = {"name": "Noisefloor", "state": "late", "data_as_of": "2026-11-13T00:00:00Z"}
        runs = [(snap(generated=build(0)),), (snap(generated=build(1), noisefloor=late),),
                (snap(generated=build(2)),)]
        self.assertEqual(self.codes(runs), [0, 0, 0])

    # 2. No usable record fails at once, as without --state.
    def test_no_record_on_every_run_fails_every_run(self):
        codes = []
        for n in range(3):
            if self.state.exists():
                self.state.unlink()
            codes.append(self.go(snap(generated=build(n), vigil=STALE))[0])
        self.assertEqual(codes, [1, 1, 1])

    def test_unreadable_state_is_no_record_and_fails_at_once(self):
        self.state.write_text("not json", encoding="utf-8")
        code, out = self.go(snap(generated=build(1), vigil=STALE))
        self.assertEqual(code, 1)
        self.assertIn("no usable record of the last run", out)

    def test_a_record_of_another_format_is_no_record(self):
        self.state.write_text('{"at": "2026-11-27T00:00:00Z", "seen": []}', encoding="utf-8")
        self.assertEqual(self.go(snap(generated=build(1), vigil=STALE))[0], 1)

    def test_an_old_record_does_not_turn_a_first_sighting_into_a_wait(self):
        self.write_record(13, generated_at=build(0))
        self.assertEqual(self.go(snap(generated=build(1), vigil=STALE))[0], 1)
        self.write_record(1, generated_at=build(1))
        self.assertEqual(self.go(snap(generated=build(2), vigil=STALE))[0], 0, "a recent empty record is a record")

    def test_the_same_old_record_coming_back_each_run_fails_each_run(self):
        codes = []
        for n in range(3):
            self.write_record(watch.STATE_HOURS + 1, generated_at=build(0))
            codes.append(self.go(snap(generated=build(n + 1), vigil=STALE))[0])
        self.assertEqual(codes, [1, 1, 1])

    def test_the_record_is_written_with_its_build_and_ids(self):
        import json
        self.go(snap(generated=build(1), vigil=STALE))
        doc = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual((doc["format"], doc["generated_at"], doc["seen"]),
                         (watch.STATE_FORMAT, build(1), ["state:vigil"]))

    # 3. Only checks that can blip wait.
    def test_checks_that_cannot_blip_fail_on_their_first_run(self):
        import json
        now = datetime.now(timezone.utc)
        sc = self.dir / "sitecheck.json"
        vd = self.dir / "scores.json"
        ahead = f"{now + timedelta(hours=2):%Y-%m-%dT%H:%M:%SZ}"
        unread = {"programs": [{"id": "blackout", "name": "Blackout", "sources": {"not_read": [
            {"id": "ioda-gatech", "state": "failing", "failures": 2}]}}]}
        cases = {
            "snapshot age": dict(snapshot=snap(generated=f"{now - timedelta(hours=6.5):%Y-%m-%dT%H:%M:%SZ}"),
                                 extra=["--cop-hours", "6"]),
            "future data time": dict(snapshot=snap(elements={"name": "Elements", "state": "fresh",
                                                             "data_as_of": ahead})),
            "malformed items": dict(snapshot=snap(), health_doc=health(elements=(1, []))),
            "unread source": dict(snapshot=snap(), health_doc=unread),
            "failed page": dict(snapshot=snap(), extra=["--sitecheck", str(sc)]),
            "old site check": dict(snapshot=snap(), extra=["--sitecheck", str(sc), "--sitecheck-hours", "0.0001"]),
            "old scorecard": dict(snapshot=snap(), extra=["--verdict", str(vd)]),
            "accepted list": dict(snapshot=snap(), extra=["--accepted", str(self.dir / "accepted.json")]),
        }
        sc.write_text(json.dumps(sitecheck(finished=f"{now:%Y-%m-%dT%H:%M:%SZ}", orbit=["map did not draw"])),
                      encoding="utf-8")
        vd.write_text(json.dumps({"generated_at": "2026-11-01T00:00:00Z"}), encoding="utf-8")
        (self.dir / "accepted.json").write_text("{}", encoding="utf-8")
        for name, case in cases.items():
            with self.subTest(name):
                self.write_record(0, generated_at=build(0))
                code, out = self.go(case["snapshot"], case.get("health_doc"), case.get("extra", ()))
                self.assertEqual(code, 1, out)
                self.assertIn("::error::", out)
                self.assertNotIn("first sighting", out)

    # 4. A second sighting needs a newer build.
    def test_one_bad_build_read_twice_then_a_good_build_never_fails(self):
        runs = [(snap(generated=build(0)),), (snap(generated=build(1), vigil=STALE),),
                (snap(generated=build(1), vigil=STALE),), (snap(generated=build(2)),)]
        self.assertEqual(self.codes(runs), [0, 0, 0, 0])

    def test_the_same_build_carries_the_sighting_to_the_next_build(self):
        self.go(snap(generated=build(0)))
        self.go(snap(generated=build(1), vigil=STALE))
        code, out = self.go(snap(generated=build(1), vigil=STALE))
        self.assertEqual(code, 0)
        self.assertIn("same build as the last run", out)
        self.assertEqual(self.go(snap(generated=build(2), vigil=STALE))[0], 1)

    # 5. A run that could not read the snapshot does not wipe the programs' sightings.
    def test_a_run_that_could_not_look_is_not_a_clear_run(self):
        codes = [self.go(snap(generated=build(0)))[0], self.go(snap(generated=build(1), vigil=STALE))[0],
                 self.go(snap(), snapshot_error=TimeoutError("timed out"))[0],
                 self.go(snap(generated=build(2), vigil=STALE))[0]]
        self.assertEqual(codes, [0, 0, 0, 1])

    # 6. A failed run names what failed it.
    def test_a_failed_run_annotates_its_confirmed_problems_as_errors(self):
        late = {"name": "Noisefloor", "state": "late", "data_as_of": "2026-11-13T00:00:00Z"}
        self.go(snap(generated=build(0)))
        self.go(snap(generated=build(1), vigil=STALE))
        code, out = self.go(snap(generated=build(2), vigil=STALE, noisefloor=late))
        self.assertEqual(code, 1)
        self.assertIn('::error::Vigil: state is "stale"', out)
        self.assertIn('::warning::first sighting (fails if still there next run): Noisefloor: state is "late"', out)
        self.assertNotIn("::error::Noisefloor", out)

    def test_without_state_a_problem_fails_at_once(self):
        self.assertEqual(run(self.dir, snap(vigil=STALE), health())[0], 1)

    def test_a_test_failure_fails_on_its_first_run_and_is_never_remembered(self):
        import json
        self.write_record(0, generated_at=build(0))
        code, out = self.go(snap(generated=build(1)), extra=["--test-failure"])
        self.assertEqual(code, 1)
        self.assertIn("TEST:", out)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["seen"], [])

    def test_the_workflow_keeps_the_record_in_the_cache_and_its_schedule(self):
        text = (ROOT / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/suite_watch.py --state watch-state.json", text)
        self.assertIn('cron: "47 */3 * * *"', text)
        self.assertNotIn("at worst an alert comes one run late", text)


class MoreChecks(unittest.TestCase):
    def prog(self, **extra):
        return {"programs": [{"id": "launch", "name": "Launch", "state": "fresh", "items": 5,
                              "lint": {"errors": 0, "warnings": 3, "examples": []}, **extra}]}

    def test_lint_warnings_pass_and_lint_errors_fail_at_once(self):
        self.assertEqual(watch.check_health(self.prog()), [])
        [p] = watch.check_health(self.prog(lint={"errors": 2, "examples": ["a: bad date"]}))
        self.assertEqual((p.id, p.waits), ("lint:launch", False))
        self.assertIn("2 contract lint errors", p)
        self.assertIn("a: bad date", p)

    def test_a_fresh_program_with_no_items_waits_for_a_newer_build(self):
        [p] = watch.check_health(self.prog(items=0))
        self.assertEqual((p.id, p.waits), ("empty:launch", True))
        self.assertEqual(watch.check_health(self.prog(items=0, state="stale")), [])
        seen = {"seen": {"empty:launch"}, "generated_at": "g1"}
        self.assertEqual(watch.confirm([p], seen, "g1"), ([], [], [p]))
        self.assertEqual(watch.confirm([p], seen, "g2"), ([p], [], []))

    def test_triage_must_come_from_the_snapshot_read(self):
        gen = "2026-11-14T20:20:00Z"
        self.assertEqual(watch.check_triage({"snapshot_generated_at": gen}, gen), [])
        old = watch.check_triage({"snapshot_generated_at": "2026-11-14T18:20:00Z"}, gen)
        self.assertEqual([p.id for p in old], ["triage-stale"])
        self.assertEqual([p.id for p in watch.check_triage({}, gen)], ["triage-no-time"])

    def test_a_stopped_or_missing_ledger_is_a_problem(self):
        def doc(at):
            return {"assets": [{"name": "2026-11-14.jsonl.gz", "updated_at": "2026-11-14T20:00:00Z"},
                               {"name": "state.json.gz", "updated_at": at}]}
        self.assertEqual(watch.check_ledger(doc("2026-11-14T20:00:00Z"), NOW), [])
        self.assertEqual([p.id for p in watch.check_ledger(doc("2026-11-14T10:00:00Z"), NOW)], ["ledger-age"])
        self.assertEqual([p.id for p in watch.check_ledger({"assets": []}, NOW)], ["ledger-no-state"])

    def test_a_server_that_does_not_answer_is_a_waiting_problem(self):
        [p] = watch.check_mcp("http://127.0.0.1:1/mcp")
        self.assertEqual((p.id, p.waits), ("mcp-down", True))

    def test_the_new_files_are_read_when_named(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code, out = run(Path(d), snap(), health(), ["--triage", str(Path(d) / "none.json")])
            self.assertEqual(code, 1)
            self.assertIn("triage.json could not be read", out)


if __name__ == "__main__":
    unittest.main()
