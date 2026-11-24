"""Data-quality checks on COP's public snapshot: report only.

    python tools/data_quality.py [--snapshot URL|FILE] [--health URL|FILE] [--zones FILE] [--state FILE]

Each rule comes from a finding of the suite database review of 24 Nov 2026 (ideas#210, decision 12; built under
ideas#245). The checks read the bus (today.geojson), COP's health.json and the kit's zone list. They never drop, edit
or hold an item, and they never fail a run: the exit code is 0 whatever they find, so a hit is a warning in the run's
summary, not an alert mail. Read the hits; a real one becomes an issue on the program (a frozen program's fix waits
for its own item).

The rules, each named with the finding it comes from:
  expired        an item still on the bus after its own valid_until, judged at the snapshot's build time with an
                 hour's grace (F19, Wake's AIS items).
  source-ended   the item's source says it has ended (measures.current false, a status such as "ended", or the
                 source's own end date, measures.to, more than 48 h ago) but the item is still emitted (F18, GDACS).
  status-no-base a baseline-relative status word (situation_status, status, trend) where the item says its baseline
                 is missing ("insufficient" in status_basis), or an indicator marked present whose signal has a tail
                 but no expected value, no baseline_n and no baseline_ref (F21, Context's "holding").
  z-zero-base    a z value beside a zero median, expected value or spread, or a z of 1,000 or more (ideas#212, Wake's
                 Kerch Strait row at z 428571428.6).
  hours-covered  an anomaly or warning whose coverage_fraction is under 0.9: the measurement that stands out covers
                 less than nine tenths of its window (F9, Noisefloor's truncated days). Routine items that state a
                 partial coverage are doing what the rule asks for.
  rows-low       a program read successfully whose item count, or one source's item count, is under a quarter of
                 its median over the runs before (F4, Vigil's upstream file falling from 3,402 victims to 10). Needs
                 --state (kept between runs in the Actions cache); without it, or with under 4 earlier runs, it
                 reports that it has no norm yet.
  zone-unknown   a zone id (watchfloor_zone, a *-WP-Z<nn> warning problem, or a zone key in an item id) that is not
                 a current zone in the kit's vocab/zones.json; a retired zone is named as retired (F10, Z17).
Items COP holds as a stale last good copy are left out of the item rules: the watchdog already reports the feed.
Standard library only. Tests: python tests/test_data_quality.py
"""
import argparse
import json
import os
import re
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "https://cop.theintelligence.company/data/today.geojson"
ZONES = ROOT / "doctrine" / "kit" / "vocab" / "zones.json"
EXAMPLES = 5
COVERAGE_MIN = 0.9
ENDED_GRACE_H = 48
EXPIRED_GRACE_H = 1  # a feed read just before an item expired is not late
ROWS_RATIO = 0.25
ROWS_MIN_MEDIAN = 10
ROWS_MIN_RUNS = 4
ROWS_KEEP_RUNS = 24
Z_IMPLAUSIBLE = 1000

RULES = {
    "expired": "still on the bus after its own valid_until (F19)",
    "source-ended": "its source says it has ended, but it is still emitted (F18)",
    "status-no-base": "a status word with no baseline under it (F21)",
    "z-zero-base": "a z value on a zero median, expected value or spread (ideas#212)",
    "hours-covered": f"stands out but covers under {COVERAGE_MIN:.0%} of its window (F9)",
    "rows-low": f"read successfully with under {ROWS_RATIO:.0%} of its usual item count (F4)",
    "zone-unknown": "a zone id that is not a current zone in the kit (F10)",
}
ENDED_WORDS = {"ended", "closed", "expired", "cancelled", "canceled", "lifted", "resolved", "inactive", "over"}
NEUTRAL_STATUS = {"", "INSUFFICIENT", "BUILDING", "UNKNOWN", "NONE", "N/A", "NA"}
STATUS_KEYS = ("situation_status", "status", "trend")
ZONE_KEY = re.compile(r"(?<![0-9A-Za-z])(\d{2})_[A-Z][A-Za-z_]+")
ZONE_WP = re.compile(r"^[A-Z]{2}-WP-Z(\d{2})$")


def when(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Hits:
    """Hits per rule and program: a count and the first few examples."""

    def __init__(self):
        self.by = {}

    def add(self, rule, program, example):
        h = self.by.setdefault(rule, {}).setdefault(program, {"count": 0, "examples": []})
        h["count"] += 1
        if len(h["examples"]) < EXAMPLES:
            h["examples"].append(example)

    def report(self):
        return {rule: dict(sorted(self.by.get(rule, {}).items())) for rule in RULES}


def items_of(snap):
    for f in (snap.get("features") or []) if isinstance(snap, dict) else []:
        p = f.get("properties") if isinstance(f, dict) else None
        if isinstance(p, dict) and not p.get("cop_stale"):
            yield p


def check_expired(p, now, hits):
    t = when(p.get("valid_until"))
    if t is not None and now - t > timedelta(hours=EXPIRED_GRACE_H) and p.get("last_change") != "ended":
        hits.add("expired", p.get("program"), f"{p.get('id')} (valid until {p.get('valid_until')})")


def check_source_ended(p, now, hits):
    m = p.get("measures")
    if not isinstance(m, dict) or p.get("last_change") == "ended":
        return
    for key in ("current", "iscurrent", "is_current", "active"):
        if m.get(key) is False:
            hits.add("source-ended", p.get("program"), f"{p.get('id')} ({key} false)")
            return
    for key in ("status", "state"):
        if isinstance(m.get(key), str) and m[key].strip().lower() in ENDED_WORDS:
            hits.add("source-ended", p.get("program"), f"{p.get('id')} ({key} \"{m[key]}\")")
            return
    t = when(m.get("to"))
    if t is not None and now - t > timedelta(hours=ENDED_GRACE_H):
        hits.add("source-ended", p.get("program"), f"{p.get('id')} (source end date {m.get('to')})")


def check_status_no_base(p, hits):
    m = p.get("measures")
    if isinstance(m, dict):
        basis = str(m.get("status_basis") or "").lower()
        for key in STATUS_KEYS:
            word = m.get(key)
            if isinstance(word, str) and word.strip().upper() not in NEUTRAL_STATUS and "insufficient" in basis:
                hits.add("status-no-base", p.get("program"), f"{p.get('id')} ({key} \"{word}\", basis {basis})")
                return
    s = p.get("signal")
    if (p.get("indicator_state") == "present" and isinstance(s, dict) and s.get("tail")
            and s.get("expected") is None and s.get("baseline_n") is None and not p.get("baseline_ref")):
        hits.add("status-no-base", p.get("program"), f"{p.get('id')} (indicator present, signal has no expected)")


def _z_keys(key):
    """For a z key, the sibling words that name its baseline."""
    if key == "z":
        return lambda k: k in ("expected", "median", "sd", "std", "mad", "spread", "baseline_median")
    stem = key[2:] if key.startswith("z_") else key[:-2]
    return lambda k: stem in k and any(w in k for w in ("median", "expected", "sd", "std", "mad", "spread"))


def _walk_z(o, path, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if num(v) and (k == "z" or k.startswith("z_") or k.endswith("_z")):
                base = _z_keys(k)
                zero = [s for s, sv in o.items() if s != k and base(s) and num(sv) and sv == 0]
                if zero or abs(v) >= Z_IMPLAUSIBLE:
                    out.append(f"{path}{k} {v:g}" + (f" beside {zero[0]} 0" if zero else ""))
            else:
                _walk_z(v, f"{path}{k}.", out)
    elif isinstance(o, list):
        for v in o:
            _walk_z(v, f"{path}[].", out)


def check_z_zero_base(p, hits):
    out = []
    _walk_z(p.get("measures"), "measures.", out)
    _walk_z(p.get("signal"), "signal.", out)
    if out:
        hits.add("z-zero-base", p.get("program"), f"{p.get('id')} ({out[0]})")


def check_hours_covered(p, hits):
    c = p.get("coverage_fraction")
    if num(c) and c < COVERAGE_MIN and p.get("tier") in ("anomaly", "warning"):
        hits.add("hours-covered", p.get("program"), f"{p.get('id')} (coverage {c:g})")


def zone_table(zones):
    current = {z["key"][:2] for z in (zones.get("zones") or []) if isinstance(z, dict) and z.get("key")}
    retired = {z["key"][:2]: z["key"] for z in (zones.get("retired") or []) if isinstance(z, dict) and z.get("key")}
    return current, retired


def check_zone(p, table, hits):
    current, retired = table
    found = []
    for field in ("watchfloor_zone", "warning_problem_id"):
        m = ZONE_WP.match(str(p.get(field) or ""))
        if m:
            found.append((m.group(1), f"{field} {p.get(field)}"))
    for m in ZONE_KEY.finditer(str(p.get("id") or "")):
        found.append((m.group(1), f"id zone {m.group(0)}"))
    for n, where in found:
        if n not in current:
            what = f"retired zone {retired[n]}" if n in retired else "not in the kit"
            hits.add("zone-unknown", p.get("program"), f"{p.get('id')} ({where}, {what})")
            return


def counts_of(snap):
    """Items per program and per program/source id (stale copies left out)."""
    counts = {}
    for p in items_of(snap):
        prog = str(p.get("program"))
        counts[prog] = counts.get(prog, 0) + 1
        for s in {s.get("id") for s in (p.get("sources") or []) if isinstance(s, dict) and s.get("id")}:
            counts[f"{prog}/{s}"] = counts.get(f"{prog}/{s}", 0) + 1
    return counts


def read_ok(health):
    """Programs whose feed was read this run: fresh, fetched, not a last good copy."""
    ok = set()
    for prog in (health.get("programs") or []) if isinstance(health, dict) else []:
        if (isinstance(prog, dict) and prog.get("state") == "fresh" and not prog.get("from_previous")
                and (prog.get("fetch") or {}).get("ok") is not False):
            ok.add(str(prog.get("id")))
    return ok


def check_rows(counts, ok, history, hits, notes):
    """Compare this run's counts with the median of the runs before, for programs read this run."""
    runs = [r for r in history if isinstance(r, dict) and isinstance(r.get("counts"), dict)]
    if len(runs) < ROWS_MIN_RUNS:
        notes.append(f"rows-low: {len(runs)} earlier run{'' if len(runs) == 1 else 's'} kept, "
                     f"needs {ROWS_MIN_RUNS} for a norm")
        return
    keys = {k for r in runs for k in r["counts"]} | set(counts)
    for key in sorted(keys):
        prog = key.split("/", 1)[0]
        if prog not in ok:
            continue
        past = [r["counts"].get(key, 0) for r in runs if prog in (r.get("ok") or [])]
        if len(past) < ROWS_MIN_RUNS:
            continue
        med = statistics.median(past)
        now_n = counts.get(key, 0)
        if med >= ROWS_MIN_MEDIAN and now_n < ROWS_RATIO * med:
            hits.add("rows-low", prog, f"{key}: {now_n} items against a median of {med:g} over {len(past)} runs")


def check(snap, health, zones, now, history=None):
    """All rules. Returns (report, notes, this run's state record)."""
    hits, notes = Hits(), []
    built = when(snap.get("generated_at")) if isinstance(snap, dict) else None
    at = min(built, now) if built else now  # an item that expired after the build is not the feed's fault
    table = zone_table(zones) if isinstance(zones, dict) else None
    if table is None or not table[0]:
        notes.append("zone-unknown: the kit's zone list could not be read, so zone ids were not checked")
    for p in items_of(snap):
        check_expired(p, at, hits)
        check_source_ended(p, at, hits)
        check_status_no_base(p, hits)
        check_z_zero_base(p, hits)
        check_hours_covered(p, hits)
        if table and table[0]:
            check_zone(p, table, hits)
    counts, ok = counts_of(snap), read_ok(health)
    if history is None:
        notes.append("rows-low: no --state, so there is no norm to compare with")
    elif not ok:
        notes.append("rows-low: health.json lists no program read this run, so counts were not compared")
    else:
        check_rows(counts, ok, history, hits, notes)
    record = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts, "ok": sorted(ok)}
    return hits.report(), notes, record


def render(report, notes, now):
    """Plain text for the log, markdown for the job summary, and one ::warning:: per rule and program."""
    lines, md, warn = [], [f"## Data quality at {now:%Y-%m-%d %H:%M} UTC (report only)", ""], []
    md += ["| Rule | Program | Items | Examples |", "|---|---|---|---|"]
    total = 0
    for rule, progs in report.items():
        for prog, h in progs.items():
            total += h["count"]
            ex = "; ".join(h["examples"])
            lines.append(f"{rule} {prog}: {h['count']} {RULES[rule]}. {ex}")
            md.append(f"| {rule} | {prog} | {h['count']} | {ex.replace('|', '/')} |")
            warn.append(f"::warning title=data quality {rule}::{prog}: {h['count']} {RULES[rule]}")
    if not total:
        md = md[:2] + ["No hits on any rule."]
        lines.append("data quality: no hits on any rule")
    md += [""] + [f"- {n}" for n in notes]
    lines += notes
    return "\n".join(lines), "\n".join(md) + "\n", warn


def read_json(where):
    where = str(where)
    if where.startswith("http"):
        req = urllib.request.Request(where, headers={"User-Agent": "suite-watch/1"})
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
    return json.loads(Path(where).read_text(encoding="utf-8"))


def health_beside(snapshot):
    if snapshot.startswith("http"):
        return snapshot.rsplit("/", 1)[0] + "/health.json"
    return str(Path(snapshot).resolve().parent / "health.json")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=SNAPSHOT)
    ap.add_argument("--health", help="COP's health.json (default: beside the snapshot)")
    ap.add_argument("--zones", default=str(ZONES))
    ap.add_argument("--state", help="file of earlier runs' item counts, read and rewritten (rows-low)")
    ap.add_argument("--json", help="also write the report here")
    ap.add_argument("--now", help="the time to check against (tests)")
    a = ap.parse_args(argv)
    now = when(a.now) if a.now else datetime.now(timezone.utc)
    docs, notes = {}, []
    for name, where in (("snapshot", a.snapshot), ("health", a.health or health_beside(a.snapshot)),
                        ("zones", a.zones)):
        try:
            docs[name] = read_json(where)
        except Exception as e:  # report only: an unreadable input is a note, never a failed run
            docs[name] = None
            notes.append(f"{name} could not be read ({type(e).__name__}: {e})")
    history = None
    if a.state:
        try:
            history = json.loads(Path(a.state).read_text(encoding="utf-8")).get("runs") or []
        except Exception:
            history = []
    if docs["snapshot"] is None:
        report, more, record = {r: {} for r in RULES}, ["no rule was checked"], None
    else:
        report, more, record = check(docs["snapshot"], docs["health"], docs["zones"], now, history)
    text, md, warn = render(report, notes + more, now)
    print(text)
    for w in warn:
        print(w)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)
    if a.state and record is not None:
        runs = (history or []) + [record]
        Path(a.state).write_text(json.dumps({"runs": runs[-ROWS_KEEP_RUNS:]}), encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rules": RULES,
                                            "hits": report, "notes": notes + more}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
