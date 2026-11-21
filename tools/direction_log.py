"""The locked record of COP's direction words, for Verdict to score. Standard library only.

    python tools/direction_log.py --log out/direction-log.jsonl --direction site/data/direction.json --memory out/memory

Rule adopted by Tom, 21 Nov 2026 (ideas#126). Each Monday the first build before 06:00 UTC writes one "call" line per
place: the word, each layer's state and the numbers behind its test, read from data through Sunday. Ten days after that
Monday a "followup" line per place holds the same layers' numbers for that Monday through Sunday. A week whose Monday
window was missed gets one "missed" line, so a gap shows. Words before the first locked week are never logged.

The file is append-only. Each line carries the SHA-256 of the line before it, and a broken chain stops the tool. This
holds counts and medians, never the memory itself. A direction word is not a forecast and carries no probability:
Verdict scores whether the measured change was still there a week later.
"""

import argparse
import hashlib
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import direction as d  # noqa: E402
import memory as mem_tool  # noqa: E402

SCHEMA = "tic-direction-log/1"
RULE = "1"                      # a change to any threshold or layer bumps this and starts a new count
FIRST_WEEK = "2026-11-23"       # the first Monday locked; nothing earlier is ever a scored call
LOCK_BEFORE_HOUR = 6            # UTC on the Monday
FOLLOWUP_AFTER_DAYS = 10        # the Thursday after the follow-up week, which allows the three-day data lag
MAX_READING_AGE_HOURS = 2       # a reading older than this is not locked
GENESIS = "0" * 64
UNSCORED = {"shutdowns": "A worldwide count of one layer that overlaps every other place."}


def line_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_log(path):
    """The log's lines as (text, record). Stops on a broken chain: a changed record is never built upon."""
    path = Path(path)
    if not path.exists():
        return []
    out, prev = [], GENESIS
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        rec = json.loads(text)
        if rec.get("prev") != prev:
            raise SystemExit(f"direction log: the chain breaks at line {n}")
        out.append((text, rec))
        prev = line_hash(text)
    return out


def append(path, lines, records):
    """Add records in order, each chained to the line before."""
    prev = line_hash(lines[-1][0]) if lines else GENESIS
    new = []
    for rec in records:
        text = json.dumps(rec | {"prev": prev}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        new.append(text)
        prev = line_hash(text)
    if new:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(x + "\n" for x in new))
    return len(new)


def monday_of(day):
    return (day - timedelta(days=day.weekday())).isoformat()


def thresholds():
    return {"min_events": d.MIN_EVENTS, "min_read_days": d.MIN_RECENT, "move_p": d.MOVE_P, "move_ratio": d.MOVE_RATIO,
            "level_ratio": d.LEVEL_RATIO}


def call_records(doc, week):
    out = []
    for x in doc["problems"] + doc["areas"]:
        layers = []
        for l in x["layers"]:
            row = {"layer": l["layer"], "program": l["program"], "state": l["state"]}
            if l["layer"] in d.CONDITIONS:
                row["condition"] = True
            elif l.get("test"):
                row["test"] = l["test"]
            layers.append(row)
        counted = [l for l in x["layers"] if l["layer"] not in d.CONDITIONS]
        rec = {"type": "call", "schema": SCHEMA, "rule": RULE, "join_version": doc["join"]["version"], "week": week,
               "logged_at": doc["generated_at"], "area": x["area"], "name": x["name"], "word": x["direction"],
               "press_zone": x.get("press_zone"), "scored": x["area"] not in UNSCORED,
               "up": [l["layer"] for l in counted if l["state"] == "up"],
               "down": [l["layer"] for l in counted if l["state"] == "down"],
               "layers": layers, "thresholds": thresholds()}
        if x["area"] in UNSCORED:
            rec["why_unscored"] = UNSCORED[x["area"]]
        out.append(rec)
    return out


def measure(mem, area, layer, days, shapes):
    """The layer's numbers for exactly these days, however late the data arrived."""
    if layer == "interference":
        held = (mem.get("events") or {}).get("noisefloor") or {}
        got = [x for x in days if x in held]
        return {"kind": "count", "days_read": len(got),
                "a": sum(1 for x in got for e in held[x] if d.point_in_area(e[0], e[1], area, shapes))}
    if layer == "press":
        r = mem["subjects"].get(f"watchfloor:zone:{area.get('press_zone')}") or {}
        values = [r["v"][x] for x in days if x in (r.get("v") or {})]
        return {"kind": "level", "days_read": len(values), "median": statistics.median(values) if values else None}
    return {"kind": "count", "days_read": len(d._read_days(mem, d.LAYER_PROGRAM[layer], days)),
            "a": len(d.dated_events(mem, area, layer, set(days)))}


def followup_records(mem, config, calls, week, read_at):
    start = date.fromisoformat(week)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    areas = {a["area"]: a for a in config["problems"] + config["areas"]}
    shapes = d.load_shapes()
    out = []
    for call in calls:
        area = areas.get(call["area"])
        if area is None:
            out.append({"type": "followup", "schema": SCHEMA, "rule": call["rule"], "week": week, "read_at": read_at,
                        "area": call["area"], "window": [days[0], days[-1]], "layers": [],
                        "note": "This place is no longer configured."})
            continue
        layers = [{"layer": l["layer"]} | measure(mem, area, l["layer"], days, shapes)
                  for l in call["layers"] if l.get("test")]
        out.append({"type": "followup", "schema": SCHEMA, "rule": call["rule"], "week": week, "read_at": read_at,
                    "area": call["area"], "window": [days[0], days[-1]], "layers": layers})
    return out


def update(path, doc, mem, config, now):
    """Append whatever is due. Returns (calls, followups, missed) written."""
    lines = read_log(path)
    records = [r for _, r in lines]
    today = now.date()
    week = monday_of(today)
    seen = {r["week"] for r in records if r["type"] in ("call", "missed")}
    new_calls, missed = [], []
    if week >= FIRST_WEEK and week not in seen:
        made = datetime.fromisoformat(doc["generated_at"].replace("Z", "+00:00"))
        in_window = today.weekday() == 0 and now.hour < LOCK_BEFORE_HOUR
        fresh = made.date() == today and timedelta(0) <= now - made <= timedelta(hours=MAX_READING_AGE_HOURS)
        if in_window and fresh:
            new_calls = call_records(doc, week)
        elif not in_window:
            missed = [{"type": "missed", "schema": SCHEMA, "rule": RULE, "week": week,
                       "noted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "why": f"No reading was locked before {LOCK_BEFORE_HOUR:02d}:00 UTC on the Monday."}]
    done = {r["week"] for r in records if r["type"] == "followup"}
    followups = []
    for w in sorted({r["week"] for r in records if r["type"] == "call"} - done):
        if (today - date.fromisoformat(w)).days >= FOLLOWUP_AFTER_DAYS:
            calls = [r for r in records if r["type"] == "call" and r["week"] == w]
            followups += followup_records(mem, config, calls, w, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    append(path, lines, followups + missed + new_calls)
    return len(new_calls), len(followups), len(missed)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True)
    ap.add_argument("--direction", required=True)
    ap.add_argument("--memory", required=True, help="folder holding memory.json.gz")
    ap.add_argument("--now", help="ISO time, for tests")
    a = ap.parse_args(argv)
    mem = mem_tool.load(a.memory)
    if mem is None:
        raise SystemExit("direction log: no memory to read")
    doc = json.loads(Path(a.direction).read_text(encoding="utf-8"))
    config = json.loads((ROOT / "config" / "areas.json").read_text(encoding="utf-8"))
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(timezone.utc)
    calls, followups, missed = update(a.log, doc, mem, config, now)
    print(f"direction log: {calls} calls, {followups} follow-ups, {missed} missed weeks written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
