"""Private archive lanes: every file a build of build.yml writes outside the tracked code (out/, site/) and the
committed history/ land in the private archive (tools/tic_archive.py, metacog-osint/tic-archive) and restore byte for
byte. A file that a later build replaces stays restorable from the snapshot that held it.

The test makes an offline build in a temporary folder (snapshot.py on the fixtures, agent_index.py, the ledger's first
run), adds a file at every path the workflow uploads to a release or pushes to a branch, then runs the workflow's own
tic_archive.py commands, read from build.yml, against a local DirStore. Nothing reaches GitHub: GhStore is replaced by
the DirStore and any subprocess call during an archive command fails the test.

It also reads every release upload and branch push in build.yml: one the table below does not know fails the test, so
a new kind of kept file cannot be added without saying which lane holds it."""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import tic_archive as ta  # noqa: E402

WF = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
PROGRAM = "cop"
NOW = "2026-10-31T20:30:00Z"
DAY = "2026-10-31"
FIRST, SECOND = "20261031T203000Z", "20261031T223000Z"
ARCHIVE_STEP = "Private archive (build outputs and history, to tic-archive; checks the snapshots and site lanes)"
SNAPSHOTS_STEP = "Private archive of this build's snapshot copies (before the release replaces the day's copy)"
SITE_STEP = "Private archive of the site as deployed"
ARCHIVE_STEPS = (SNAPSHOTS_STEP, SITE_STEP, ARCHIVE_STEP)
# Minutes the steps before the deploy take on a normal build, with room: the three longest successful builds of late
# November 2026 took 91 to 109 s for the whole job.
PRE_DEPLOY_MINUTES = 5
LOG_STEP = "Direction log on the site (site/data/direction-log.jsonl, from the branch direction-log)"

# Every release upload in build.yml, as written there (the file arguments), and the files of the offline build that
# stand for it, each with the lane that must hold it. out/history.tar.gz is the history set packed for its release;
# the site lane holds the same set unpacked, as served.
UPLOADS = {
    "out/archive/*.geojson.gz $(ls out/archive/*-ended.json.gz 2>/dev/null)": [
        ("snapshots", f"out/archive/{DAY}.geojson.gz"), ("snapshots", f"out/archive/{DAY}.elements-ended.json.gz")],
    '"$f"': [("snapshots", f"out/archive/{DAY}T18Z.snap.json.gz")],
    "out/triage-log/*.gz": [("run", "out/triage-log/state.json.gz"), ("run", f"out/triage-log/{DAY}.jsonl.gz")],
    "out/hormuz-stream/state.json.gz": [("run", "out/hormuz-stream/state.json.gz")],
    '"$NEW"': [("run", f"out/briefs/{DAY}.json")],
    "out/history.tar.gz": [("site", "site/data/history/manifest.json")],
    "out/ledger/state-before-subject-id.json.gz": [("run", "out/ledger/state-before-subject-id.json.gz")],
    '"out/ledger/$DAY.jsonl.gz"': [("run", f"out/ledger/{DAY}.jsonl.gz")],
    "out/ledger/state.json.gz": [("run", "out/ledger/state.json.gz")],
    '"out/memory/$MONTH"': [("run", "out/memory/memory-2026-10.json.gz")],
    "out/memory/memory.json.gz": [("run", "out/memory/memory.json.gz")],
    '"out/ledger-dropped/$DAY.dropped.jsonl.gz"': [("run", f"out/ledger-dropped/{DAY}.dropped.jsonl.gz")],
}
# Every branch the build pushes to, and the file it pushes.
PUSHES = {"lane-log": "out/lane-log.jsonl", "direction-log": "out/direction-log.jsonl"}
# Written by the build into out/ and kept nowhere else: the Undertow joins while their publishing is off.
OUT_ONLY = ["out/undertow-sanctions/latest.json", "out/undertow-elections/latest.json"]
# In out/ but not archived by the run lane: downloads of public releases and other repositories, and the history
# set the site lane holds as served.
NOT_IN_RUN = ["out/rates-archive/2026-10-30.geojson.gz", "out/history-in/snapshots/2026-10-30.geojson.gz",
              "out/history-in/repo-elements/.git/HEAD", "out/history-prev/history.tar.gz",
              "out/history-new/manifest.json", "out/history.tar.gz"]


def steps() -> dict[str, str]:
    """The build job's steps by name (unnamed `uses:` steps by their action)."""
    out = {}
    body = WF.split("\n  build:\n", 1)[1].split("\n    steps:\n", 1)[1]
    for chunk in re.split(r"(?m)^      - ", body)[1:]:
        m = re.match(r"name: (.+)|uses: (\S+)", chunk)
        out[(m.group(1) or m.group(2)).strip()] = chunk
    return out


STEPS = steps()


def step(prefix: str) -> str:
    (name,) = [n for n in STEPS if n.startswith(prefix)]
    return STEPS[name]


def shell_lines(text: str) -> list[str]:
    """The step's shell lines, continuations joined, comments left out."""
    flat = text.replace("\\\n", " ")
    return [ln.strip() for ln in flat.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def uploads_in(text: str) -> list[str]:
    """The file arguments of every release upload in one step (direct, or through the steps' `up` helper)."""
    found = []
    rx = re.compile(r"(?:gh release upload|(?<![\w-])up) (\S+) (.+?)(?:\s+--clobber)?(?=\s+\|\||\s*;|\s+&&|\s*$)")
    for line in shell_lines(text):
        for m in rx.finditer(line):
            if m.group(1) == '"$@"':
                continue            # the helper's own definition
            found.append(m.group(2).strip())
    return found


def uploads() -> list[str]:
    """The file arguments of every release upload in the build job."""
    return [u for text in STEPS.values() for u in uploads_in(text)]


def pushes() -> dict[str, str]:
    """{branch: the file its step builds the pushed commit from} for every git push in the build job."""
    out = {}
    for text in STEPS.values():
        for m in re.finditer(r'git push origin "\$COMMIT:refs/heads/([\w-]+)"', text):
            (log,) = re.findall(r"\bLOG=(\S+?);", text)
            out[m.group(1)] = log
    return out


def archive_calls(text: str, cmd: str) -> list[list[str]]:
    """Every `python tools/tic_archive.py <cmd> ...` in a step, as argv after the script name."""
    calls = []
    for line in shell_lines(text):
        for m in re.finditer(r"python tools/tic_archive\.py (" + cmd + r")\b([^;|&]*)", line):
            calls.append([m.group(1), *shlex.split(m.group(2))])
    return calls


def arg(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, t in enumerate(argv) if t == flag]


SITE_ARCHIVED = 'if [ "$SITE_LANE" = "success" ]; then'


def run_lane_calls() -> tuple[list[str], list[str]]:
    """The last step's two run-lane puts: (when the site lane kept this build's site, when it did not)."""
    flat = shell_lines(STEPS[ARCHIVE_STEP])
    start = flat.index(SITE_ARCHIVED)
    rest = flat[start + 1:]
    els, fi = rest.index("else"), rest.index("fi")
    (with_site,) = archive_calls("\n".join(rest[:els]), "put")
    (without_site,) = archive_calls("\n".join(rest[els + 1:fi]), "put")
    return with_site, without_site


def put_calls(site_archived: bool = True) -> list[list[str]]:
    """Every lane's put as one build runs them: the site lane only when the deploy published the site."""
    calls = archive_calls(STEPS[SNAPSHOTS_STEP], "put")
    if site_archived:
        calls += archive_calls(STEPS[SITE_STEP], "put")
    with_site, without_site = run_lane_calls()
    calls.append(with_site if site_archived else without_site)
    calls += [c for c in archive_calls(STEPS[ARCHIVE_STEP], "put") if lane_of(c) != "run"]
    return calls


def timeout(text: str) -> int | None:
    m = re.search(r"(?m)^        timeout-minutes: (\d+)$", text)
    return int(m.group(1)) if m else None


def run_block(text: str) -> str:
    """A step's `run: |` script, as the runner gets it."""
    body = text.split("        run: |\n", 1)[1]
    lines = []
    for ln in body.splitlines():
        if ln.strip() and not ln.startswith("          "):
            break
        lines.append(ln[10:])
    return "\n".join(lines) + "\n"


def lane_of(argv: list[str]) -> str:
    return arg(argv, "--lane")[0]


def tree(root: Path) -> dict[str, str]:
    """{posix path relative to root: sha256} for every file."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def run_archive(argv: list[str], cwd: Path, store: Path, stamp: str = FIRST) -> int:
    """One tic_archive.py command as the workflow gives it, in cwd, against a DirStore at store."""
    def no_subprocess(*a, **k):
        raise AssertionError("an archive command tried to run a subprocess (gh): nothing may leave the test")

    old = os.getcwd()
    os.chdir(cwd)
    try:
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(ta, "GhStore", lambda *a, **k: ta.DirStore(store)), \
                mock.patch.object(ta, "utc_stamp", lambda now=None: stamp), \
                mock.patch.object(subprocess, "run", no_subprocess):
            return ta.main(argv)
    finally:
        os.chdir(old)


def gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"), 9, mtime=0)


class Build:
    """One offline build, made once: snapshot, MCP files, the ledger's first run, and a file at every kept path."""
    tmp: Path | None = None

    @classmethod
    def make(cls) -> Path:
        if cls.tmp is not None:
            return cls.tmp
        tmp = Path(tempfile.mkdtemp(prefix="cop-lanes-"))
        shutil.copytree(ROOT / "site", tmp / "site")
        shutil.copytree(ROOT / "history", tmp / "history")
        py = [sys.executable]
        for argv in (
                [str(ROOT / "tools" / "snapshot.py"), "--fixtures", str(ROOT / "tests" / "fixtures"), "--now", NOW,
                 "--out", str(tmp / "site" / "data"), "--archive", str(tmp / "out" / "archive"),
                 "--dropped-out", str(tmp / "out" / "dropped.jsonl"), "--ended-out", str(tmp / "out" / "ended")],
                [str(ROOT / "tools" / "agent_index.py"), "--data", str(tmp / "site" / "data"),
                 "--verdict", str(ROOT / "tests" / "fixtures" / "verdict"), "--now", NOW],
                [str(ROOT / "tools" / "ledger.py"), "--snapshot", str(tmp / "site" / "data" / "today.geojson"),
                 "--assets", str(tmp / "out" / "ledger"), "--first"]):
            r = subprocess.run(py + argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            if r.returncode != 0:
                raise RuntimeError(f"{Path(argv[0]).name} failed: {r.stderr[-400:]}")
        # Every other kept path, as the later steps leave it (the fixture build fetches no ended list).
        stand_ins = {path for paths in UPLOADS.values() for _, path in paths} | set(PUSHES.values()) \
            | set(OUT_ONLY) | set(NOT_IN_RUN) | {"out/ended/elements.geojson", "out/dropped.jsonl", "out/triage/rates.json",
                                                 "out/three-signal/latest.json", "out/direction-log-read.jsonl",
                                                 "out/brief-new.txt", "site/data/direction-log.jsonl",
                                                 "site/data/triage.json", "site/data/direction.json",
                                                 "site/brief/briefs.json"}
        for rel in sorted(stand_ins):
            p = tmp / rel
            if p.exists():
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            body = f'{{"stands_for": "{rel}", "build": 1}}\n'
            p.write_bytes(gz(body) if rel.endswith(".gz") else body.encode("utf-8"))
        # The served history set is the unpacked history-new; the log on the site is the branch's file.
        shutil.copyfile(tmp / "out/history-new/manifest.json", tmp / "site/data/history/manifest.json")
        shutil.copyfile(tmp / "out/direction-log.jsonl", tmp / "site/data/direction-log.jsonl")
        cls.tmp = tmp
        return tmp


def tearDownModule():
    if Build.tmp is not None:
        shutil.rmtree(Build.tmp, ignore_errors=True)


class TestWhatTheBuildKeeps(unittest.TestCase):
    def test_every_release_upload_is_known(self):
        found = uploads()
        self.assertEqual(len(found), len(set(found)), "the same upload twice: give each its own entry")
        self.assertEqual(sorted(set(found) - set(UPLOADS)), [],
                         "a new release upload in build.yml: put its files in an archive lane and add it to UPLOADS")
        self.assertEqual(sorted(set(UPLOADS) - set(found)), [], "UPLOADS names an upload build.yml no longer has")

    def test_every_branch_push_is_known(self):
        self.assertEqual(pushes(), PUSHES)

    def test_the_deploy_sends_site(self):
        self.assertIn("pages deploy site --project-name cop", step("Deploy site/"))


class TestWorkflowLanes(unittest.TestCase):
    def test_lanes_are_snapshots_run_site_history(self):
        lanes = {lane_of(c): c for c in put_calls()}
        self.assertEqual(len(lanes), len(put_calls()), "one put per lane")
        self.assertEqual(set(lanes), {"snapshots", "run", "site", "history"})
        for c in lanes.values():
            self.assertEqual(arg(c, "--program"), [PROGRAM])
            # COP's own outputs and copies of public files: archived as they are, so they match the public copy.
            self.assertFalse([t for t in c if t.startswith("--redact")])
            self.assertEqual(arg(c, "--include"), [])
            self.assertEqual(arg(c, "--min-interval-hours"), [])     # every build, never thinned
            self.assertNotIn("--dry-run", c)
        self.assertEqual(arg(lanes["snapshots"], "--src"), ["out/archive"])
        self.assertEqual(arg(lanes["run"], "--src"), ["out"])
        self.assertEqual(arg(lanes["site"], "--src"), ["site"])
        self.assertEqual(arg(lanes["history"], "--src"), ["history"])
        self.assertEqual(arg(lanes["run"], "--exclude"),
                         ["archive/*", "rates-archive/*", "history-in/*", "history-prev/*", "history.tar.gz",
                          "history-new/*"])
        for lane in ("snapshots", "site", "history"):
            self.assertEqual(arg(lanes[lane], "--exclude"), [], lane)
        # Without a site snapshot, the run lane is the same put but keeps the history set this build made.
        with_site, without_site = run_lane_calls()
        self.assertEqual(with_site, without_site + ["--exclude", "history-new/*"])
        self.assertEqual({lane_of(c) for c in put_calls(site_archived=False)}, {"snapshots", "run", "history"})

    def test_each_archive_step_is_loud_on_failure(self):
        names = list(STEPS)
        self.assertEqual(names[-1], ARCHIVE_STEP, "the last archive step runs after every step that writes what it keeps")
        text = STEPS[ARCHIVE_STEP]
        self.assertIn("if: ${{ !cancelled() }}", text)
        self.assertNotIn("continue-on-error", text)
        self.assertEqual(text.count("exit 1"), 2)            # token missing; any lane failed
        self.assertEqual(text.count("|| failed=1"), 3)       # the run lane (one of two) and history, each tried
        # The earlier lanes never hold the build back, and the last step turns the run red when one failed.
        for name in (SNAPSHOTS_STEP, SITE_STEP):
            self.assertIn("continue-on-error: true", STEPS[name], name)
            self.assertEqual(STEPS[name].count("exit 1"), 1, name)       # token missing
        self.assertIn("SNAPSHOTS_LANE: ${{ steps.archive_snapshots.outcome }}", text)
        self.assertIn("SITE_LANE: ${{ steps.archive_site.outcome }}", text)
        self.assertIn("id: archive_snapshots", STEPS[SNAPSHOTS_STEP])
        self.assertIn("id: archive_site", STEPS[SITE_STEP])
        flat = shell_lines(text)
        self.assertIn('[ "$SNAPSHOTS_LANE" = "success" ] || { echo "::error::snapshots lane: $SNAPSHOTS_LANE '
                      '(its step above)"; failed=1; }', flat)
        self.assertIn('[ "$SITE_LANE" != "failure" ] || { echo "::error::site lane: failed (its step above)"; '
                      'failed=1; }', flat)
        for name in ARCHIVE_STEPS:
            self.assertIn("GH_TOKEN: ${{ secrets.TIC_ARCHIVE_TOKEN }}", STEPS[name], name)
            self.assertNotIn("github.token", STEPS[name], name)
            self.assertNotIn("|| true", STEPS[name], name)
            self.assertIn('[ -z "$GH_TOKEN" ]', STEPS[name], name)
        self.assertNotIn("TIC_ARCHIVE_TOKEN", step("Deploy site/"))
        self.assertEqual([n for n in names if "TIC_ARCHIVE_TOKEN" in STEPS[n]], list(ARCHIVE_STEPS))

    def test_lanes_wait_for_the_steps_that_write_them(self):
        text = STEPS[ARCHIVE_STEP]
        self.assertIn("SNAPSHOT: ${{ steps.snapshot.outcome }}", text)
        self.assertIn("id: snapshot", STEPS["Snapshot of the program feeds"])
        self.assertIn("id: deploy", step("Deploy site/"))
        flat = "\n".join(shell_lines(text))
        gate = flat.index('if [ "$SNAPSHOT" != "success" ]; then')
        self.assertLess(gate, flat.index("[ -z \"$GH_TOKEN\" ]"))
        self.assertLess(gate, flat.index("python tools/tic_archive.py"))
        # The snapshots lane runs only after the snapshot step succeeded (a step's default `if:`).
        self.assertNotIn("        if:", STEPS[SNAPSHOTS_STEP])

    def test_the_run_lane_holds_every_kept_out_file(self):
        (run,) = [c for c in put_calls() if lane_of(c) == "run"]
        excludes = arg(run, "--exclude")
        kept = [p for paths in UPLOADS.values() for lane, p in paths if lane == "run"] + list(PUSHES.values()) \
            + OUT_ONLY + ["out/direction-log-unpushed.jsonl"]
        for p in kept:
            rel = p[len("out/"):]
            self.assertFalse([g for g in excludes if fnmatch.fnmatch(rel, g)], p)
        for p in NOT_IN_RUN:
            self.assertTrue([g for g in excludes if fnmatch.fnmatch(p[len("out/"):], g)], p)


class TestArchiveBeforeReplace(unittest.TestCase):
    """A build that stops part way (a step that hangs, the job's time limit) has already kept what it replaced in
    public: run 45677997691 (25 Nov 2026) hung after the deploy until the job was cancelled at its 10-minute limit,
    when the archive ran only as the last step."""

    def test_the_snapshot_copies_are_kept_before_the_release_replaces_the_day_copy(self):
        names = list(STEPS)
        snap = names.index("Snapshot of the program feeds")
        self.assertEqual(names.index(SNAPSHOTS_STEP), snap + 1, "straight after the snapshot is written")
        self.assertEqual(arg(archive_calls(STEPS[SNAPSHOTS_STEP], "put")[0], "--src"), ["out/archive"])
        # Every release upload of out/archive comes after the lane has kept it.
        for n in names:
            if any(u.startswith("out/archive/") or u == '"$f"'
                   for u in uploads_in(STEPS[n])):
                self.assertGreater(names.index(n), names.index(SNAPSHOTS_STEP), n)

    def test_the_site_is_kept_straight_after_the_deploy(self):
        names = list(STEPS)
        deploy = names.index(next(n for n in names if n.startswith("Deploy site/")))
        self.assertEqual(names.index(SITE_STEP), deploy + 1, "straight after the deploy")
        self.assertEqual(arg(archive_calls(STEPS[SITE_STEP], "put")[0], "--src"), ["site"])

    def test_the_site_lane_waits_for_a_deploy_that_ran(self):
        """The deploy step exits 0 without the Cloudflare secrets: that is not a deployed site."""
        deploy = shell_lines(step("Deploy site/"))
        skip = next(i for i, ln in enumerate(deploy) if "skipping deploy" in ln)
        wrangler = next(i for i, ln in enumerate(deploy) if "pages deploy site" in ln)
        self.assertIn("exit 0", deploy[skip])
        self.assertEqual(deploy[-1], 'echo "deployed=yes" >> "$GITHUB_OUTPUT"')
        self.assertLess(skip, wrangler)
        # Wrangler runs in a retry loop; the output is set only once a try has succeeded.
        tail = deploy[wrangler + 1:-1]
        self.assertTrue(any("deployed=yes; break" in ln for ln in tail), "a try that succeeds leaves the loop")
        self.assertTrue(any('"$deployed" != yes' in ln and "exit 1" in ln for ln in tail),
                        "three failed tries stop the step before the output is set")
        self.assertIn("if: ${{ !cancelled() && steps.deploy.outputs.deployed == 'yes' }}", STEPS[SITE_STEP])

    def test_a_hung_step_fails_at_its_own_limit_before_the_job_limit(self):
        """Each step from the deploy on has a time limit, and the job's limit leaves room for all of them after a
        normal run of the steps before, so the archive steps always get to run."""
        names = list(STEPS)
        deploy = names.index(next(n for n in names if n.startswith("Deploy site/")))
        after = names[deploy + 1:]
        missing = [n for n in after if timeout(STEPS[n]) is None]
        self.assertEqual(missing, [], "a step after the deploy has no timeout-minutes")
        job = int(re.search(r"(?m)^  build:\n(?:    .*\n)*?    timeout-minutes: (\d+)$", WF).group(1))
        budget = PRE_DEPLOY_MINUTES + sum(timeout(STEPS[n]) for n in after)
        self.assertGreaterEqual(job, budget, f"job limit {job} min under {budget} min")
        self.assertIsNotNone(timeout(STEPS[SNAPSHOTS_STEP]))
        self.assertLessEqual(timeout(STEPS[SNAPSHOTS_STEP]), 3)


class TestDirectionLogOnTheSite(unittest.TestCase):
    """The branch direction-log's file is published on the site, so it can be read without this repository."""

    def test_written_before_the_join_check_and_the_deploy(self):
        names = list(STEPS)
        self.assertLess(names.index(LOG_STEP), names.index(next(n for n in names if n.startswith("Join check"))))
        self.assertLess(names.index(LOG_STEP), names.index(next(n for n in names if n.startswith("Deploy site/"))))

    def test_the_same_file_the_lock_step_pushes(self):
        text = STEPS[LOG_STEP]
        self.assertIn("git show refs/remotes/origin/direction-log:direction-log.jsonl > site/data/direction-log.jsonl",
                      text.replace("\\\n", " "))
        lock = step("Lock the direction words")
        self.assertIn("printf '100644 blob %s\\tdirection-log.jsonl\\n'", lock)
        self.assertIn("https://cop.theintelligence.company/data/direction-log.jsonl", text)
        self.assertNotIn("exit 1", text)                     # never fails the build
        self.assertNotIn("git push", text)                   # read only


class TestEveryBuildFileIsArchived(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Build.make()
        cls.work = Path(tempfile.mkdtemp(prefix="cop-store-"))
        cls.store = cls.work / "store"
        cls.before = {top: tree(cls.root / top) for top in ("out", "site", "history")}
        cls.results = {lane_of(argv): run_archive(argv, cls.root, cls.store, FIRST) for argv in put_calls()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def restore_all(self, dest: Path, snapshots: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
        """Every lane restored, mapped back to build paths: {path: (lane, sha256)}."""
        got = {}
        for argv in put_calls():
            lane, (src,) = lane_of(argv), arg(argv, "--src")
            cmd = ["restore", "--program", PROGRAM, "--lane", lane, "--dest", str(dest / lane)]
            if snapshots and lane in snapshots:
                cmd += ["--snapshot", snapshots[lane]]
            self.assertEqual(run_archive(cmd, self.root, self.store), 0, lane)
            for rel, sha in tree(dest / lane).items():
                path = f"{src}/{rel}"
                self.assertNotIn(path, got, "two lanes hold the same file")
                got[path] = (lane, sha)
        return got

    def snapshot_names(self, stamp: str) -> dict[str, str]:
        out = {}
        for tag in self.store.iterdir():
            for p in tag.iterdir():
                if p.name.startswith(stamp) and p.name.endswith(".files.json"):
                    out[tag.name[len(PROGRAM) + 1:].rsplit("-", 2)[0]] = p.name
        return out

    def test_every_put_succeeded(self):
        self.assertEqual(self.results, {"snapshots": 0, "run": 0, "site": 0, "history": 0})
        tags = {t.name for t in self.store.iterdir()}
        self.assertEqual({re.sub(r"-\d{4}-\d{2}$", "", t) for t in tags},
                         {f"cop-{lane}" for lane in ("snapshots", "run", "site", "history")})

    def test_every_file_of_the_build_restores_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            got = self.restore_all(Path(td), self.snapshot_names(FIRST))
        want = {f"{top}/{rel}": sha for top, files in self.before.items() for rel, sha in files.items()
                if f"{top}/{rel}" not in NOT_IN_RUN}
        self.assertEqual(sorted(set(want) - set(got)), [], "files of the build missing from the archive")
        self.assertEqual(sorted(set(got) - set(want)), [], "the archive holds files the build did not keep")
        self.assertEqual({p: s for p, (_, s) in got.items() if want[p] != s}, {}, "restored bytes differ")
        # Each kept path is in the lane the table names, and the files others read from the site are there too.
        for paths in UPLOADS.values():
            for lane, p in paths:
                self.assertEqual(got[p][0], lane, p)
        for p in list(PUSHES.values()) + OUT_ONLY:
            self.assertEqual(got[p][0], "run", p)
        for p in ("site/data/today.geojson", "site/data/picks.json", "site/data/health.json",
                  "site/data/agent/index.json", "site/data/agent/verdict.json", "site/data/direction-log.jsonl",
                  "site/index.html", "history/CONTRACT.md", "out/dropped.jsonl", "out/ended/elements.geojson"):
            self.assertIn(p, got)
        # The committed history restores as git holds it.
        self.assertEqual({p: s for p, (lane, s) in got.items() if lane == "history"},
                         {f"history/{rel}": sha for rel, sha in tree(ROOT / "history").items()})

    def test_replaced_files_stay_restorable(self):
        root = self.root
        replaced = (f"out/archive/{DAY}.geojson.gz", "out/ledger/state.json.gz", "out/memory/memory.json.gz",
                    "site/data/today.geojson", "site/data/direction-log.jsonl")
        old = {p: (root / p).read_bytes() for p in replaced}
        try:
            # The next build of the day: its own day copy (the release replaces the one before), new states, a new
            # deploy; the slot's file was kept by the first build of the slot and is not written again.
            for p, data in old.items():
                (root / p).write_bytes(gz("next build\n") if p.endswith(".gz") else data + b"\n")
            for argv in put_calls():
                self.assertEqual(run_archive(argv, root, self.store, SECOND), 0, lane_of(argv))
            first, second = self.snapshot_names(FIRST), self.snapshot_names(SECOND)
            self.assertEqual(set(first), {"snapshots", "run", "site", "history"})
            self.assertEqual(set(second), {"snapshots", "run", "site"})     # history unchanged: nothing uploaded
            with tempfile.TemporaryDirectory() as td:
                now = self.restore_all(Path(td) / "now")
                then = self.restore_all(Path(td) / "then", first)
            for p, data in old.items():
                self.assertEqual(then[p][1], hashlib.sha256(data).hexdigest(), p)
                self.assertEqual(now[p][1], hashlib.sha256((root / p).read_bytes()).hexdigest(), p)
                self.assertNotEqual(now[p][1], then[p][1], p)
        finally:
            for p, data in old.items():
                (root / p).write_bytes(data)


def gzip_header(data: bytes) -> tuple[int, int]:
    """(flags, mtime) of a gzip member's header."""
    return data[3], int.from_bytes(data[4:8], "little")


class TestOneCopyPerContent(unittest.TestCase):
    """The archive stores files by their bytes, so a file whose content has not changed must keep its bytes: the
    day's copy and the slot's copy of one build are one stored file, and an unchanged state is not stored again."""

    def test_the_day_and_slot_copies_are_the_same_bytes(self):
        root = Build.make()
        day = (root / f"out/archive/{DAY}.geojson.gz").read_bytes()
        slot = (root / f"out/archive/{DAY}T18Z.snap.json.gz").read_bytes()
        self.assertEqual(day, slot)
        self.assertEqual(gzip_header(day), (0, 0), "a gzip header with a file name or a time")
        self.assertEqual(gzip.decompress(day), (root / "site/data/today.geojson").read_bytes())

    def test_the_ended_list_copy_has_a_fixed_header(self):
        import snapshot
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as td:
            fx, arch = Path(td) / "fx", Path(td) / "archive"
            fx.mkdir()
            shutil.copyfile(ROOT / "tests/fixtures/elements.geojson", fx / "elements-ended.geojson")
            snapshot.fetch_ended(datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc), Path(td) / "ended", str(fx),
                                 arch)
            data = (arch / f"{DAY}.elements-ended.json.gz").read_bytes()
            self.assertEqual(gzip_header(data), (0, 0), "a gzip header with a file name or a time")
            self.assertEqual(json.loads(gzip.decompress(data))["cop_fetched_at"], NOW)

    def test_the_snapshots_lane_stores_them_once(self):
        root = Build.make()
        with tempfile.TemporaryDirectory() as td:
            store = Path(td)
            (snap,) = archive_calls(STEPS[SNAPSHOTS_STEP], "put")
            self.assertEqual(run_archive(snap, root, store), 0)
            (index,) = [p for p in store.rglob("*.files.json")]
            doc = json.loads(index.read_text(encoding="utf-8"))
            members = {f["path"]: f["member"] for f in doc["files"]}
            self.assertEqual(members[f"{DAY}.geojson.gz"], members[f"{DAY}T18Z.snap.json.gz"])
            self.assertEqual(doc["new_files"], doc["file_count"] - 1)

    def test_an_unchanged_hormuz_state_keeps_its_bytes(self):
        import hormuz_stream
        state = {"covered_from": "2026-10-01T00:00:00Z", "transits": {}, "reports": {}}
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a", Path(td) / "b"
            hormuz_stream.save_state(state, a)
            with mock.patch("time.time", lambda: 2_000_000_000.0):
                hormuz_stream.save_state(state, b)
            one = (a / hormuz_stream.STATE_NAME).read_bytes()
            self.assertEqual(one, (b / hormuz_stream.STATE_NAME).read_bytes())
            self.assertEqual(gzip_header(one), (0, 0))
            self.assertEqual(hormuz_stream.load_state(a), state)


class TestRunLaneWithoutTheSite(unittest.TestCase):
    """A build whose site was not archived (the deploy failed or skipped itself) has still replaced the release's
    history set when it rebuilt it: the run lane keeps the set this build made."""

    def test_the_history_set_is_kept_by_the_run_lane(self):
        root = Build.make()
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "store"
            calls = put_calls(site_archived=False)
            self.assertEqual([lane_of(c) for c in calls], ["snapshots", "run", "history"])
            for argv in calls:
                self.assertEqual(run_archive(argv, root, store), 0, lane_of(argv))
            dest = Path(td) / "run"
            self.assertEqual(run_archive(["restore", "--program", PROGRAM, "--lane", "run", "--dest", str(dest)],
                                         root, store), 0)
            got = tree(dest)
            self.assertEqual(got["history-new/manifest.json"],
                             hashlib.sha256((root / "out/history-new/manifest.json").read_bytes()).hexdigest())
            self.assertNotIn("history.tar.gz", got)
            self.assertFalse([p for p in got if p.startswith(("rates-archive/", "history-in/", "history-prev/",
                                                              "archive/"))])


def posix_bash() -> str | None:
    """A bash that can run the workflow's scripts with git (the runner's, or Git Bash); None where there is none."""
    bash = shutil.which("bash")
    if not bash or not shutil.which("git"):
        return None
    try:
        r = subprocess.run([bash, "-c", "command -v git >/dev/null && echo ok"], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return bash if r.stdout.strip() == "ok" else None


@unittest.skipIf(posix_bash() is None, "needs bash and git (the runner has both)")
class TestDirectionLogPushThatFails(unittest.TestCase):
    """The lock step's own script against a local origin: when the push is refused, out/direction-log.jsonl must be
    the branch's log (the run lane's copy is the locked record) and this build's lines are kept apart."""

    BRANCH_LINE = '{"line": 1}\n'
    NEW_LINE = '{"line": 2}\n'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cop-dlog-"))
        self.env = {**os.environ, "GITHUB_SHA": "0" * 40, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                    "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")}
        self.origin, self.work = self.tmp / "origin.git", self.tmp / "work"
        self.git("init", "-q", "--bare", str(self.origin))
        self.git("init", "-q", str(self.work))
        self.git("-C", str(self.work), "remote", "add", "origin", self.origin.as_posix())
        log = self.work / "seed.jsonl"
        log.write_text(self.BRANCH_LINE, encoding="utf-8", newline="\n")
        blob = self.git("-C", str(self.work), "hash-object", "-w", str(log))
        tree_id = self.git("-C", str(self.work), "mktree", input=f"100644 blob {blob}\tdirection-log.jsonl\n")
        commit = self.git("-C", str(self.work), "commit-tree", tree_id, "-m", "seed")
        self.git("-C", str(self.work), "push", "-q", "origin", f"{commit}:refs/heads/direction-log")
        log.unlink()
        for rel in ("site/data/direction.json", "out/memory/memory.json.gz"):
            (self.work / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.work / rel).write_text("{}", encoding="utf-8")
        # Stands in for tools/direction_log.py: adds one line to the log it is given.
        (self.work / "tools").mkdir()
        (self.work / "tools/direction_log.py").write_text(
            "import sys\nlog = sys.argv[sys.argv.index('--log') + 1]\n"
            f"open(log, 'a', encoding='utf-8', newline='\\n').write({self.NEW_LINE!r})\n", encoding="utf-8")
        (self.tmp / "step.sh").write_text(run_block(step("Lock the direction words")), encoding="utf-8",
                                          newline="\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *argv, input=None) -> str:
        # Bytes in and out, so no newline is translated on the way (git mktree reads its lines exactly).
        r = subprocess.run(["git", *argv], capture_output=True, input=input.encode("utf-8") if input else None,
                           env=self.env, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"git {argv[0]}: {r.stderr.decode('utf-8', 'replace')}")
        return r.stdout.decode("utf-8").strip()

    def run_step(self) -> subprocess.CompletedProcess:
        return subprocess.run([posix_bash(), "--noprofile", "--norc", "-eo", "pipefail", str(self.tmp / "step.sh")],
                              cwd=self.work, capture_output=True, text=True, env=self.env, timeout=120)

    def branch_log(self) -> str:
        return self.git("--git-dir", str(self.origin), "show", "direction-log:direction-log.jsonl") + "\n"

    def test_a_refused_push_leaves_the_branch_log_and_keeps_the_lines_apart(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        r = self.run_step()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("could not be pushed", r.stdout)
        self.assertEqual(self.branch_log(), self.BRANCH_LINE)
        self.assertEqual((self.work / "out/direction-log.jsonl").read_text(encoding="utf-8"), self.BRANCH_LINE)
        self.assertEqual((self.work / "out/direction-log-unpushed.jsonl").read_text(encoding="utf-8"),
                         self.BRANCH_LINE + self.NEW_LINE)

    def test_a_push_that_lands_leaves_no_unpushed_file(self):
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.branch_log(), self.BRANCH_LINE + self.NEW_LINE)
        self.assertEqual((self.work / "out/direction-log.jsonl").read_text(encoding="utf-8"),
                         self.BRANCH_LINE + self.NEW_LINE)
        self.assertFalse((self.work / "out/direction-log-unpushed.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
