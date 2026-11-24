"""Suite watchdog: say when a program has stopped, read from COP's public snapshot.

    python tools/suite_watch.py

Exit 0 when everything is current, 1 when something is wrong (so a scheduled run fails and its owner is mailed).
It checks six things the picture cannot say about itself:
  1. COP's own snapshot is older than --cop-hours (COP's build stopped).
  2. A program's state in the snapshot is not "fresh" (its feed stopped or failed).
  3. A program's data time is in the future by more than --future-minutes (Elements, 12 Nov 2026: a clock that runs
     ahead looks fresh for ever).
  4. COP's health.json (beside the snapshot, or --health) counts a malformed item that the build left out of the
     picture (15 Nov 2026: the build no longer stops on one, so without this nothing would say a program is losing
     items). A health.json that cannot be read is a problem too, never a quiet pass.
  5. The hub's site check (tic-sitecheck, a Worker that opens the hub and every program's page in headless Chrome at
     05:10 and 17:10 UTC) failed a page, or has not finished a run in --sitecheck-hours (18 Nov 2026, Tom's yes). It is
     read only when the snapshot is the live one, or when --sitecheck names a file or address, so tests stay offline.
     A site check that cannot be read is a problem, never a quiet pass.
  6. Verdict's scores.json (the public forecast ledger's scorecard) has a generated_at older than --verdict-hours, 48 h
     (ideas#161, 23 Nov 2026). Read only when the snapshot is the live one, or when --verdict names a file or address.
     A scorecard that cannot be read is a problem, never a quiet pass.
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
SITECHECK = "https://tic-sitecheck.metacog.workers.dev/"
VERDICT = "https://metacog-osint.github.io/verdict/data/scores.json"  # until verdict.theintelligence.company has DNS
VERDICT_HOURS = 48.0
SITECHECK_HOURS = 14.0  # runs 12 h apart, about a minute each; two hours' grace


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


def check_health(health):
    """Problems from COP's health.json: every program whose feed had items the build left out as malformed."""
    progs = health.get("programs") if isinstance(health, dict) else None
    if not isinstance(progs, list) or not progs:
        return ["COP: health.json lists no programs, so malformed items were not checked"]
    problems = []
    for prog in progs:
        if not isinstance(prog, dict):
            continue
        dropped = prog.get("dropped")
        count = dropped.get("count") if isinstance(dropped, dict) else None
        if isinstance(count, int) and count > 0:
            examples = [e for e in (dropped.get("examples") or []) if isinstance(e, str)]
            problems.append(f"{prog.get('name') or prog.get('id')}: {count} malformed item{'' if count == 1 else 's'} "
                            "left out of the picture" + (" (" + "; ".join(examples) + ")" if examples else ""))
    return problems


def check_sitecheck(doc, now, max_hours=SITECHECK_HOURS):
    """Problems from the hub's site check (contract tic-sitecheck/1): each page that failed, or a run too old."""
    if not isinstance(doc, dict) or not str(doc.get("contract", "")).startswith("tic-sitecheck/"):
        return ["Site check: the latest run is not a tic-sitecheck document, so pages were not checked"]
    problems = []
    done = when(doc.get("finished_at"))
    if done is None:
        problems.append("Site check: the latest run has no finish time")
    elif now - done > timedelta(hours=max_hours):
        problems.append(f"Site check: last run finished {(now - done).total_seconds() / 3600:.1f} h ago "
                        f"(limit {max_hours:g} h)")
    results = doc.get("results")
    if not isinstance(results, list) or not results:
        return problems + ["Site check: the latest run lists no pages"]
    for r in results:
        if isinstance(r, dict) and r.get("ok") is not True:
            why = [p for p in (r.get("problems") or []) if isinstance(p, str)]
            problems.append(f"Site check: {r.get('id') or r.get('url')} page failed"
                            + (" (" + "; ".join(why[:3]) + ")" if why else "") + f", {r.get('url')}")
    return problems


def check_verdict(doc, now, max_hours=VERDICT_HOURS):
    """Problems from Verdict's scores.json: no build time, or one older than max_hours."""
    gen = when(doc.get("generated_at")) if isinstance(doc, dict) else None
    if gen is None:
        return ["Verdict: scores.json has no build time"]
    if now - gen > timedelta(hours=max_hours):
        return [f"Verdict: scores.json is {(now - gen).total_seconds() / 3600:.1f} h old (limit {max_hours:g} h)"]
    return []


def health_beside(snapshot):
    """health.json sits beside today.geojson, on the site and in a local build."""
    if snapshot.startswith("http"):
        return snapshot.rsplit("/", 1)[0] + "/health.json"
    return os.path.join(os.path.dirname(os.path.abspath(snapshot)), "health.json")


def read_json(where):
    if where.startswith("http"):
        req = urllib.request.Request(where, headers={"User-Agent": "suite-watch/1"})
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
    return json.loads(open(where, encoding="utf-8").read())


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
    ap.add_argument("--health", help="COP's health.json (default: beside the snapshot)")
    ap.add_argument("--sitecheck", help=f"the hub's site check, file or address (default {SITECHECK}, "
                                        "read only when the snapshot is an address)")
    ap.add_argument("--sitecheck-hours", type=float, default=SITECHECK_HOURS)
    ap.add_argument("--verdict", help=f"Verdict's scores.json, file or address (default {VERDICT}, "
                                      "read only when the snapshot is an address)")
    ap.add_argument("--verdict-hours", type=float, default=VERDICT_HOURS)
    ap.add_argument("--test-failure", action="store_true",
                    help="fail on purpose, to test that the alerts arrive")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        problems = check(read_json(a.snapshot), now, a.cop_hours, a.future_minutes)
    except Exception as e:
        problems = [f"COP: the snapshot could not be read ({type(e).__name__}: {e})"]
    try:
        problems += check_health(read_json(a.health or health_beside(a.snapshot)))
    except Exception as e:
        problems.append(f"COP: health.json could not be read ({type(e).__name__}: {e}), "
                        "so malformed items were not checked")
    sitecheck = a.sitecheck or (SITECHECK if a.snapshot.startswith("http") else None)
    if sitecheck:
        try:
            problems += check_sitecheck(read_json(sitecheck), now, a.sitecheck_hours)
        except Exception as e:
            problems.append(f"Site check: the latest run could not be read ({type(e).__name__}: {e}), "
                            "so pages were not checked")
    verdict = a.verdict or (VERDICT if a.snapshot.startswith("http") else None)
    if verdict:
        try:
            problems += check_verdict(read_json(verdict), now, a.verdict_hours)
        except Exception as e:
            problems.append(f"Verdict: scores.json could not be read ({type(e).__name__}: {e}), "
                            "so its age was not checked")
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
