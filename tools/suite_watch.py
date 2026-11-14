"""Suite watchdog: say when a program has stopped, read from COP's public snapshot.

    python tools/suite_watch.py

Exit 0 when everything is current, 1 when something is wrong (so a scheduled run fails and its owner is mailed).
It checks three things the picture cannot say about itself:
  1. COP's own snapshot is older than --cop-hours (COP's build stopped).
  2. A program's state in the snapshot is not "fresh" (its feed stopped or failed).
  3. A program's data time is in the future by more than --future-minutes (Elements, 12 Nov 2026: a clock that runs
     ahead looks fresh for ever).
If the environment has HC_PING_URL (a Healthchecks.io check address, set as a secret, never written in a file), it
pings that address on success and its /fail address on a problem. Without it, nothing is sent anywhere.
--test-failure adds one problem that says it is a test, so a hand-started run can prove the alerts reach Tom
(GitHub's failed-run mail and Healthchecks.io's /fail mail) without anything being wrong.
Standard library only. Run by .github/workflows/watch.yml, apart from the build, because a build that has
stopped cannot report itself. Tests: python tests/test_suite_watch.py
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

SNAPSHOT = "https://cop.theintelligence.company/data/today.geojson"


def when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def check(snap, now, cop_hours=6.0, future_minutes=30.0):
    problems = []
    gen = when(snap.get("generated_at"))
    if gen is None:
        problems.append("COP: the snapshot has no build time")
    elif now - gen > timedelta(hours=cop_hours):
        problems.append(f"COP: snapshot is {(now - gen).total_seconds() / 3600:.1f} h old (limit {cop_hours:g} h)")
    for key, prog in sorted((snap.get("cop_programs") or {}).items()):
        name = prog.get("name", key)
        if prog.get("state") != "fresh":
            problems.append(f"{name}: state is \"{prog.get('state')}\", data of {prog.get('data_as_of')}")
        t = when(prog.get("data_as_of"))
        if t is not None and t - now > timedelta(minutes=future_minutes):
            problems.append(f"{name}: data time {prog.get('data_as_of')} is "
                            f"{(t - now).total_seconds() / 3600:.1f} h in the future")
    if not snap.get("cop_programs"):
        problems.append("COP: the snapshot lists no programs")
    return problems


def ping(url, body):
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8")[:9000], method="POST",
                                     headers={"User-Agent": "suite-watch/1"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # a failed ping must not hide the result
        print(f"ping failed: {type(e).__name__}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=SNAPSHOT)
    ap.add_argument("--cop-hours", type=float, default=6.0)
    ap.add_argument("--future-minutes", type=float, default=30.0)
    ap.add_argument("--test-failure", action="store_true",
                    help="fail on purpose, to test that the alerts arrive")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        if a.snapshot.startswith("http"):
            req = urllib.request.Request(a.snapshot, headers={"User-Agent": "suite-watch/1"})
            snap = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
        else:
            snap = json.loads(open(a.snapshot, encoding="utf-8").read())
        problems = check(snap, now, a.cop_hours, a.future_minutes)
    except Exception as e:
        problems = [f"COP: the snapshot could not be read ({type(e).__name__}: {e})"]
    if a.test_failure:
        problems.append("TEST: a failure asked for by hand (--test-failure); nothing is wrong")
    text = "\n".join(problems) if problems else f"all programs current at {now:%Y-%m-%dT%H:%MZ}"
    print(text)
    url = os.environ.get("HC_PING_URL", "").rstrip("/")
    if url:
        ping(url + ("/fail" if problems else ""), text)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
