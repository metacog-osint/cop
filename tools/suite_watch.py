"""Suite watchdog: say when a program has stopped, read from COP's public snapshot.

    python tools/suite_watch.py [--state watch-state.json]

Exit 0 when everything is current, 1 when something is wrong (so a scheduled run fails and its owner is mailed).
It checks seven things the picture cannot say about itself:
  1. COP's own snapshot is older than --cop-hours (COP's build stopped).
  2. A program's state in the snapshot is not "fresh" (its feed stopped or failed).
  3. A program's data time is in the future by more than --future-minutes (Elements, 12 Nov 2026: a clock that runs
     ahead looks fresh for ever).
  4. COP's health.json (beside the snapshot, or --health) counts a malformed item that the build left out of the
     picture (15 Nov 2026: the build no longer stops on one, so without this nothing would say a program is losing
     items). A health.json that cannot be read is a problem too, never a quiet pass.
  5. The hub's site check (tic-sitecheck, a Worker that opens the hub and every program's page in headless Chrome at
     05:10 and 17:10 UTC) failed a page, or has not finished a run in --sitecheck-hours (18 Nov 2026). It is read
     only when the snapshot is the live one, or when --sitecheck names a file or address, so tests stay offline.
     A site check that cannot be read is a problem, never a quiet pass.
  6. Verdict's scores.json (the public forecast ledger's scorecard) has a generated_at older than --verdict-hours, 48 h
     (since 23 Nov 2026). Read only when the snapshot is the live one, or when --verdict names a file or address.
     A scorecard that cannot be read is a problem, never a quiet pass.
  7. A source a program's feed lists in its own health block was not read (health.json's sources.not_read), on
     UNREAD_RUNS runs in a row by the feed's own failure count, or with no count given (since 24 Nov 2026). One
     failed run is left to the next, so a passing timeout does not mail anyone.
  A source named in config/watch_accepted.json (a reason, a state, a phrase its detail must hold, and an `until` date)
  is not a problem while it reads exactly as documented and the date has not passed: the run says so on its own line
  ("accepted: ...") and passes. Any other state or detail, or a lapsed date, is a problem again (25 Nov 2026).

Each problem has a stable id made where it is found (the check, the program, the source where there is one:
`state:vigil`, `dropped:elements`, `unread:blackout:ioda-gatech`, `snapshot-unreadable`, `sitecheck-page:orbit`,
`verdict-age`), so its wording and numbers can change between runs without making it a new problem.

Two in a row, with --state (a JSON file kept between runs). Only problems that one bad read can cause wait for a
second run: a program's state that is not fresh, and a document (the snapshot, health.json, the site check,
Verdict's scorecard) that could not be read. Seen for the first time, such a problem is printed as a warning and
remembered; the next run fails if it is still there. Everything else fails the run at once: COP's snapshot age, the
site check's failed pages and its age, Verdict's age, malformed items, a data time in the future, a listed source not
read (which already waits for its feed's own second failure) and problems with watch_accepted.json.
  - The record must be usable: a missing or unreadable state file, one of another format, or one written more than
    STATE_HOURS ago (runs are dispatched every 3 h) is no record, and every problem fails at once, as without --state.
  - A program's state is confirmed only by a newer build. The record keeps the snapshot's generated_at; a run that
    reads the same build neither confirms nor clears that program's problem: it passes with the warning and carries
    the sighting forward. A build that has stopped is still caught at once by the snapshot's age.
  - A run that could not read the snapshot keeps the programs' sightings from the record, so a run that could not
    look never counts as a clear one.
  - --test-failure always fails and is never remembered. Without --state every problem fails at once.
A failed run prints `::error::` for each problem that failed it and `::warning::` for each one that waits.

If the environment has HC_PING_URL (a Healthchecks.io check address, set as a secret, never written in a file), it
pings that address on success and its /fail address on a failed run (a run with only waiting problems pings success,
with the warnings in the body). Without it, nothing is sent anywhere.
--test-failure adds one problem that says it is a test, so a hand-started run can prove the alerts arrive
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
from pathlib import Path

SNAPSHOT = "https://cop.theintelligence.company/data/today.geojson"
SITECHECK = "https://tic-sitecheck.metacog.workers.dev/"
VERDICT = "https://verdict.theintelligence.company/data/scores.json"
VERDICT_HOURS = 48.0
SITECHECK_HOURS = 14.0  # runs 12 h apart, about a minute each; two hours' grace
UNREAD_RUNS = 2         # a listed source not read this many runs in a row (the feed's own count) is a problem
STATE_FORMAT = "suite-watch-state/2"
STATE_HOURS = 4.0       # a record older than this (a little over one 3-hour dispatch gap) is no record
ACCEPTED = Path(__file__).resolve().parents[1] / "config" / "watch_accepted.json"


class Problem(str):
    """A problem's text, with its stable `id` and whether it `waits` for a second run before it fails one."""

    def __new__(cls, text, pid, waits=False):
        p = super().__new__(cls, text)
        p.id = pid
        p.waits = waits
        return p


def when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def check(snap, now, cop_hours=6.0, future_minutes=30.0):
    problems = []
    gen = when(snap.get("generated_at"))
    if gen is None:
        problems.append(Problem("COP: the snapshot has no build time", "snapshot-no-time"))
    elif now - gen > timedelta(hours=cop_hours):
        problems.append(Problem(f"COP: snapshot is {(now - gen).total_seconds() / 3600:.1f} h old "
                                f"(limit {cop_hours:g} h)", "snapshot-age"))
    for key, prog in sorted((snap.get("cop_programs") or {}).items()):
        name = prog.get("name", key)
        if prog.get("state") != "fresh":
            problems.append(Problem(f"{name}: state is \"{prog.get('state')}\", data of {prog.get('data_as_of')}",
                                    f"state:{key}", waits=True))
        t = when(prog.get("data_as_of"))
        if t is not None and t - now > timedelta(minutes=future_minutes):
            problems.append(Problem(f"{name}: data time {prog.get('data_as_of')} is "
                                    f"{(t - now).total_seconds() / 3600:.1f} h in the future", f"future:{key}"))
    if not snap.get("cop_programs"):
        problems.append(Problem("COP: the snapshot lists no programs", "snapshot-no-programs"))
    return problems


def load_accepted(doc):
    """The documented degraded sources in config/watch_accepted.json, as (entries, problems). An entry needs a program,
    a source, the state it is accepted in, a reason, a phrase its detail holds and an until date; one that lacks any
    is a problem, never accepted."""
    entries = doc.get("accepted") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return [], [Problem("COP: watch_accepted.json has no \"accepted\" list, so no degraded source is accepted",
                            "accepted-list")]
    good, problems = [], []
    for i, e in enumerate(entries):
        ok = (isinstance(e, dict) and all(isinstance(e.get(k), str) and e.get(k).strip()
                                          for k in ("program", "source", "state", "reason", "detail_has", "until"))
              and when(e["until"] + "T00:00:00Z") is not None)
        if ok:
            good.append(e)
        else:
            problems.append(Problem(f"COP: an entry of watch_accepted.json is incomplete ({json.dumps(e)[:120]}), "
                                    "so it accepts nothing", f"accepted-entry:{i}"))
    return good, problems


def accepted_by(entries, prog_id, src, now):
    """The entry that documents this source as it reads now (same program, source and state, its detail holding the
    phrase, `until` not yet over), or None."""
    for e in entries:
        end = when(e["until"] + "T00:00:00Z")
        if (e["program"] == prog_id and e["source"] == src.get("id") and e["state"] == src.get("state")
                and e["detail_has"].lower() in str(src.get("detail") or "").lower()
                and now is not None and end is not None and now < end + timedelta(days=1)):
            return e
    return None


def check_health(health, accepted=(), now=None, notes=None):
    """Problems from COP's health.json: every program whose feed had items the build left out as malformed.
    A listed source documented in `accepted` and reading as documented is not a problem; it is added to `notes`."""
    progs = health.get("programs") if isinstance(health, dict) else None
    if not isinstance(progs, list) or not progs:
        return [Problem("COP: health.json lists no programs, so malformed items were not checked",
                        "health-no-programs")]
    problems = []
    for prog in progs:
        if not isinstance(prog, dict):
            continue
        pid = prog.get("id") or prog.get("name")
        dropped = prog.get("dropped")
        count = dropped.get("count") if isinstance(dropped, dict) else None
        if isinstance(count, int) and count > 0:
            examples = [e for e in (dropped.get("examples") or []) if isinstance(e, str)]
            problems.append(Problem(f"{prog.get('name') or prog.get('id')}: {count} malformed "
                                    f"item{'' if count == 1 else 's'} left out of the picture"
                                    + (" (" + "; ".join(examples) + ")" if examples else ""), f"dropped:{pid}"))
        sources = prog.get("sources")
        for s in (sources.get("not_read") or []) if isinstance(sources, dict) else []:
            if not isinstance(s, dict):
                continue
            runs = s.get("failures")
            if isinstance(runs, int) and not isinstance(runs, bool) and runs < UNREAD_RUNS:
                continue
            doc = accepted_by(accepted, prog.get("id"), s, now)
            if doc is not None:
                if notes is not None:
                    notes.append(f"accepted: {prog.get('name') or prog.get('id')}: source {s.get('id')} "
                                 f"({s.get('state')}, {s.get('detail')}) until {doc['until']}: {doc['reason']}")
                continue
            how = ", ".join(str(x) for x in (s.get("state"), f"{runs} runs in a row" if isinstance(runs, int) else None,
                                              s.get("detail")) if x)
            problems.append(Problem(f"{prog.get('name') or prog.get('id')}: source {s.get('id')} is listed but was "
                                    "not read" + (f" ({how})" if how else ""), f"unread:{pid}:{s.get('id')}"))
    return problems


def check_sitecheck(doc, now, max_hours=SITECHECK_HOURS):
    """Problems from the hub's site check (contract tic-sitecheck/1): each page that failed, or a run too old."""
    if not isinstance(doc, dict) or not str(doc.get("contract", "")).startswith("tic-sitecheck/"):
        return [Problem("Site check: the latest run is not a tic-sitecheck document, so pages were not checked",
                        "sitecheck-shape")]
    problems = []
    done = when(doc.get("finished_at"))
    if done is None:
        problems.append(Problem("Site check: the latest run has no finish time", "sitecheck-no-time"))
    elif now - done > timedelta(hours=max_hours):
        problems.append(Problem(f"Site check: last run finished {(now - done).total_seconds() / 3600:.1f} h ago "
                                f"(limit {max_hours:g} h)", "sitecheck-age"))
    results = doc.get("results")
    if not isinstance(results, list) or not results:
        return problems + [Problem("Site check: the latest run lists no pages", "sitecheck-no-pages")]
    for r in results:
        if isinstance(r, dict) and r.get("ok") is not True:
            why = [p for p in (r.get("problems") or []) if isinstance(p, str)]
            problems.append(Problem(f"Site check: {r.get('id') or r.get('url')} page failed"
                                    + (" (" + "; ".join(why[:3]) + ")" if why else "") + f", {r.get('url')}",
                                    f"sitecheck-page:{r.get('id') or r.get('url')}"))
    return problems


def check_verdict(doc, now, max_hours=VERDICT_HOURS):
    """Problems from Verdict's scores.json: no build time, or one older than max_hours."""
    gen = when(doc.get("generated_at")) if isinstance(doc, dict) else None
    if gen is None:
        return [Problem("Verdict: scores.json has no build time", "verdict-no-time")]
    if now - gen > timedelta(hours=max_hours):
        return [Problem(f"Verdict: scores.json is {(now - gen).total_seconds() / 3600:.1f} h old "
                        f"(limit {max_hours:g} h)", "verdict-age")]
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
    with open(where, encoding="utf-8") as f:
        return json.loads(f.read())


def read_state(path, now):
    """The last run's record as {"seen": set of problem ids, "generated_at": the build it read}, or None when there is
    no usable record: no file, one that cannot be read, of another format, without a time, or written more than
    STATE_HOURS ago (or in the future). None means every problem fails at once. A record with nothing in it is a
    record: the last run saw no problem."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.loads(f.read())
    except Exception:
        return None
    if not isinstance(doc, dict) or doc.get("format") != STATE_FORMAT or not isinstance(doc.get("seen"), list):
        return None
    at = when(doc.get("at"))
    if at is None or not timedelta(minutes=-5) <= now - at <= timedelta(hours=STATE_HOURS):
        return None
    gen = doc.get("generated_at")
    return {"seen": {k for k in doc["seen"] if isinstance(k, str)}, "generated_at": gen if isinstance(gen, str) else None}


def write_state(path, seen, generated_at, now):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": STATE_FORMAT, "at": f"{now:%Y-%m-%dT%H:%M:%SZ}", "generated_at": generated_at,
                       "seen": sorted(seen)}, f)
    except OSError as e:
        print(f"state not written: {e}", file=sys.stderr)


def confirm(problems, state, generated_at):
    """Split problems into (confirmed, first, same_build).
    confirmed fail the run: every problem that does not wait, and a waiting one the last run saw too (a program's
    state only when this run read a newer build). With no usable record (state None) every problem is confirmed.
    first: waiting problems seen now for the first time. same_build: a program's state seen last run from the build
    this run reads again, which neither confirms nor clears it."""
    confirmed, first, same = [], [], []
    for p in problems:
        if not getattr(p, "waits", False) or state is None:
            confirmed.append(p)
        elif p.id not in state["seen"]:
            first.append(p)
        elif p.id.startswith("state:") and generated_at is not None and generated_at == state["generated_at"]:
            same.append(p)
        else:
            confirmed.append(p)
    return confirmed, first, same


def remembered(problems, state, snapshot_read):
    """The ids the next run will read as seen: every waiting problem of this run, plus the programs' sightings from
    the record when this run could not read the snapshot (a run that could not look is not a clear run)."""
    seen = {p.id for p in problems if getattr(p, "waits", False)}
    if not snapshot_read and state is not None:
        seen |= {k for k in state["seen"] if k.startswith("state:")}
    return seen


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
    ap.add_argument("--state", help="a JSON file kept between runs; with it a problem that one bad read can cause "
                                    "fails on its second run in a row")
    ap.add_argument("--accepted", default=str(ACCEPTED),
                    help="the documented degraded sources (default config/watch_accepted.json)")
    ap.add_argument("--test-failure", action="store_true",
                    help="fail on purpose, to test that the alerts arrive")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    generated_at = None
    try:
        snap = read_json(a.snapshot)
        generated_at = snap.get("generated_at") if isinstance(snap, dict) else None
        problems = check(snap, now, a.cop_hours, a.future_minutes)
    except Exception as e:
        problems = [Problem(f"COP: the snapshot could not be read ({type(e).__name__}: {e})", "snapshot-unreadable",
                            waits=True)]
    snapshot_read = not any(p.id == "snapshot-unreadable" for p in problems)
    accepted, notes = [], []
    try:
        accepted, bad = load_accepted(read_json(a.accepted))
        problems += bad
    except Exception as e:
        problems.append(Problem(f"COP: watch_accepted.json could not be read ({type(e).__name__}: {e}), "
                                "so no degraded source is accepted", "accepted-unreadable"))
    try:
        problems += check_health(read_json(a.health or health_beside(a.snapshot)), accepted, now, notes)
    except Exception as e:
        problems.append(Problem(f"COP: health.json could not be read ({type(e).__name__}: {e}), "
                                "so malformed items were not checked", "health-unreadable", waits=True))
    sitecheck = a.sitecheck or (SITECHECK if a.snapshot.startswith("http") else None)
    if sitecheck:
        try:
            problems += check_sitecheck(read_json(sitecheck), now, a.sitecheck_hours)
        except Exception as e:
            problems.append(Problem(f"Site check: the latest run could not be read ({type(e).__name__}: {e}), "
                                    "so pages were not checked", "sitecheck-unreadable", waits=True))
    verdict = a.verdict or (VERDICT if a.snapshot.startswith("http") else None)
    if verdict:
        try:
            problems += check_verdict(read_json(verdict), now, a.verdict_hours)
        except Exception as e:
            problems.append(Problem(f"Verdict: scores.json could not be read ({type(e).__name__}: {e}), "
                                    "so its age was not checked", "verdict-unreadable", waits=True))
    preface = []
    if a.state:
        state = read_state(a.state, now)
        confirmed, first, same = confirm(problems, state, generated_at)
        if state is None and any(getattr(p, "waits", False) for p in problems):
            preface.append(f"no usable record of the last run (missing, unreadable or over {STATE_HOURS:g} h old), "
                           "so every problem fails at once")
        build = generated_at if snapshot_read else (state or {}).get("generated_at")
        write_state(a.state, remembered(problems, state, snapshot_read), build, now)
    else:
        confirmed, first, same = list(problems), [], []
    if a.test_failure:
        confirmed.append(Problem("TEST: a failure asked for by hand (--test-failure); nothing is wrong", "test"))
    lines = (preface + confirmed + [f"first sighting, fails if still there next run: {p}" for p in first]
             + [f"same build as the last run, waits for a newer one: {p}" for p in same])
    text = "\n".join(lines) if confirmed or first or same else f"all programs current at {now:%Y-%m-%dT%H:%MZ}"
    if notes:
        text += "\n" + "\n".join(notes)
    print(text)
    for p in confirmed:
        print(f"::error::{p}")
    for p in first:
        print(f"::warning::first sighting (fails if still there next run): {p}")
    for p in same:
        print(f"::warning::same build as the last run (waits for a newer one): {p}")
    url = os.environ.get("HC_PING_URL", "").rstrip("/")
    if url:
        ping(url + ("/fail" if confirmed else ""), text)
    return 1 if confirmed else 0


if __name__ == "__main__":
    sys.exit(main())
