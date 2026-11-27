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
ARCHIVE_STEP = "Private archive (snapshots, build outputs, site and history, to tic-archive)"
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


def uploads() -> list[str]:
    """The file arguments of every release upload in the build job (direct, or through the steps' `up` helper)."""
    found = []
    rx = re.compile(r"(?:gh release upload|(?<![\w-])up) (\S+) (.+?)(?:\s+--clobber)?(?=\s+\|\||\s*;|\s+&&|\s*$)")
    for text in STEPS.values():
        for line in shell_lines(text):
            for m in rx.finditer(line):
                if m.group(1) == '"$@"':
                    continue            # the helper's own definition
                found.append(m.group(2).strip())
    return found


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


def put_calls() -> list[list[str]]:
    return archive_calls(STEPS[ARCHIVE_STEP], "put")


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
                         ["archive/*", "rates-archive/*", "history-in/*", "history-prev/*", "history-new/*",
                          "history.tar.gz"])
        for lane in ("snapshots", "site", "history"):
            self.assertEqual(arg(lanes[lane], "--exclude"), [], lane)

    def test_the_last_step_and_loud_on_failure(self):
        names = list(STEPS)
        self.assertEqual(names[-1], ARCHIVE_STEP, "the archive step runs after every step that writes what it keeps")
        text = STEPS[ARCHIVE_STEP]
        self.assertIn("if: ${{ !cancelled() }}", text)
        self.assertIn("GH_TOKEN: ${{ secrets.TIC_ARCHIVE_TOKEN }}", text)
        self.assertNotIn("github.token", text)
        self.assertNotIn("|| true", text)
        self.assertNotIn("continue-on-error", text)
        self.assertIn('[ -z "$GH_TOKEN" ]', text)
        self.assertEqual(text.count("exit 1"), 2)            # token missing; any lane failed
        self.assertEqual(text.count("|| failed=1"), 4)       # each lane tried even if another failed
        self.assertNotIn("TIC_ARCHIVE_TOKEN", step("Deploy site/"))
        self.assertEqual([n for n in names if "TIC_ARCHIVE_TOKEN" in STEPS[n]], [ARCHIVE_STEP])

    def test_lanes_wait_for_the_steps_that_write_them(self):
        text = STEPS[ARCHIVE_STEP]
        self.assertIn("SNAPSHOT: ${{ steps.snapshot.outcome }}", text)
        self.assertIn("DEPLOYED: ${{ steps.deploy.outcome }}", text)
        self.assertIn("id: snapshot", STEPS["Snapshot of the program feeds"])
        self.assertIn("id: deploy", step("Deploy site/"))
        flat = "\n".join(shell_lines(text))
        gate = flat.index('if [ "$SNAPSHOT" != "success" ]; then')
        self.assertLess(gate, flat.index("[ -z \"$GH_TOKEN\" ]"))
        self.assertLess(gate, flat.index("python tools/tic_archive.py"))
        self.assertRegex(flat, r'if \[ "\$DEPLOYED" = "success" \]; then\npython tools/tic_archive\.py put '
                               r'--program cop --lane site ')

    def test_the_run_lane_holds_every_kept_out_file(self):
        (run,) = [c for c in put_calls() if lane_of(c) == "run"]
        excludes = arg(run, "--exclude")
        kept = [p for paths in UPLOADS.values() for lane, p in paths if lane == "run"] + list(PUSHES.values()) \
            + OUT_ONLY
        for p in kept:
            rel = p[len("out/"):]
            self.assertFalse([g for g in excludes if fnmatch.fnmatch(rel, g)], p)
        for p in NOT_IN_RUN:
            self.assertTrue([g for g in excludes if fnmatch.fnmatch(p[len("out/"):], g)], p)


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


if __name__ == "__main__":
    unittest.main()
