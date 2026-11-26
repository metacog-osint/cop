"""COP's test list, shared by build.yml (every build) and tests.yml (every pull request).

Runs each script from the repository root, then `npm test`, and stops at the first failure.
Needs `npm ci --ignore-scripts` first. Add a new tests/test_*.py here; a check below fails if one is missing.
"""
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = [
    "test_snapshot.py",
    "test_agent_index.py",
    "test_gate.py",
    "test_suite_watch.py",
    "test_data_quality.py",
    "test_triage.py",
    "test_triage_replay.py",
    "test_triage_us_power.py",
    "test_triage_ended.py",
    "test_register_question.py",
    "test_rebuild_ended.py",
    "test_three_signal.py",
    "test_hormuz_stream.py",
    "test_undertow_sanctions.py",
    "test_undertow_elections.py",
    "test_ledger.py",
    "test_triage_log.py",
    "test_stop_replay.py",
    "test_history_cordon.py",
    "test_rev21.py",
    "test_dropped.py",
    "test_memory.py",
    "test_direction.py",
    "test_direction_log.py",
    "test_lane_log.py",
    "test_brief.py",
    "test_red_river.py",
]


def main():
    found = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    missing = sorted(found - set(SCRIPTS))
    gone = sorted(set(SCRIPTS) - found)
    if missing or gone:
        print(f"tests/run_all.py is out of date. Not listed: {missing}. Listed but absent: {gone}.")
        return 1
    for name in SCRIPTS:
        print(f"== {name}", flush=True)
        code = subprocess.run([sys.executable, str(ROOT / "tests" / name)], cwd=ROOT).returncode
        if code:
            print(f"FAILED: {name} (exit {code})")
            return code
    print("== npm test", flush=True)
    npm = shutil.which("npm")
    if not npm:
        print("FAILED: npm not found")
        return 1
    return subprocess.run([npm, "test"], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
