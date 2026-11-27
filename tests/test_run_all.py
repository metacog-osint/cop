"""The test runner (tests/run_all.py) keeps running the archive-lane tests: python tests/test_run_all.py

The archive-lane tests (tests/test_archive_lanes.py) are the only check that every file a build keeps reaches the
private archive. The runner already refuses a test file it does not list, but a change that removed both the file and
its line, or that removed that check, would leave CI and the build's Tests step green with nothing checking the lanes.
This module fails when the archive-lane tests are no longer listed, no longer on disk or no longer run by both
workflows, and when the runner stops refusing a list that leaves them out."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import run_all  # noqa: E402

# Modules the runner must always hold. This one is listed too, so dropping it is caught by run_all's own check.
REQUIRED = ("test_archive_lanes.py", "test_run_all.py")


class TestArchiveLanesStayInTheRunner(unittest.TestCase):
    def test_listed_once_and_on_disk(self):
        for name in REQUIRED:
            with self.subTest(name=name):
                self.assertEqual(run_all.SCRIPTS.count(name), 1)
                self.assertTrue((ROOT / "tests" / name).is_file())

    def test_both_workflows_run_the_runner(self):
        for wf in ("build.yml", "tests.yml"):
            with self.subTest(workflow=wf):
                text = (ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
                self.assertIn("run: python tests/run_all.py", text)

    def test_the_runner_refuses_a_list_without_the_archive_lane_tests(self):
        """The conflict this guards against: a merge that keeps another branch's line in place of this one."""
        dropped = [n for n in run_all.SCRIPTS if n != "test_archive_lanes.py"]
        out = io.StringIO()
        with mock.patch.object(run_all, "SCRIPTS", dropped), \
                mock.patch.object(run_all.subprocess, "run", side_effect=AssertionError("ran a test")), \
                contextlib.redirect_stdout(out):
            code = run_all.main()
        self.assertEqual(code, 1)
        self.assertIn("Not listed: ['test_archive_lanes.py']", out.getvalue())


if __name__ == "__main__":
    unittest.main()
