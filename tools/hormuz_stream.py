"""Hormuz weekly stream (join 0.1): commercial traffic and UKMTO's Attack reports, week by week, side by side.

Each week shows PortWatch's transits through the Strait of Hormuz, UKMTO's Attack reports in the 900 km circle, how
those reports divide by severity, and the share within 150 km of the strait, each against its own history. Both
inputs are read from Wake's items in COP's snapshots; COP reads neither source itself.

The voice rule applies: the panel shows each measurement with its base rate and states no inference. It carries no
probability and no outcome forecast, never divides one series by the other, and never says who fired. UKMTO's reports
are unverified and name no one.

Severity and outcome are read only from codes an item states (measures.severity, measures.outcome,
measures.casualties, and the optional measures.then_action, a second action after the first). Wake codes them from
UKMTO's free text in its reader and publishes only the codes. Until an item carries them, a report counts as "not
coded yet". Nothing here guesses a code from a report's type.

Every report and week is kept in a state file (release "hormuz-stream"); the page shows a window of it.

    python tools/hormuz_stream.py --snapshot site/data/today.geojson --archive out/rates-archive \
        --state out/hormuz-stream --publish site/data

Standard-library Python only.
"""

import argparse
import gzip
import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import three_signal as ts  # noqa: E402
import triage as tri  # noqa: E402

JOIN = {"id": "hormuz-stream", "version": "0.1", "published": True, "inputs": ["wake:ukmto", "wake:chokepoint"]}
SCHEMA = "hormuz-stream/1"
STATE_NAME = "state.json.gz"

AREA = ts.AREAS["hormuz"]
STRAIT = (26.55, 56.45)      # lat, lon: the reference point rule v0 measures from, so the 150 km share matches the codes
NEAR_KM = 150
TRANSIT_PREFIX = "wake:chokepoint:strait-of-hormuz:"
COUNTED = ("Attack",)        # UKMTO's own type name
SEVERITIES = ("L0", "L1", "L2", "L3", "L4")
SEVERITY_WORDS = {"L0": "not stated", "L1": "minor or continuing", "L2": "fire or serious damage",
                  "L3": "disabled or abandoned", "L4": "lost"}
OUTCOMES = ("collide", "careen", "conjoin", "intersect", "undetermined")
THEN_ACTIONS = ("careen", "conjoin", "intersect")   # a second action UKMTO's text states after the first
# PROVISIONAL, like the other joins' windows.
BASE_MIN_WEEKS = 4           # complete weeks before a week's count is set against its own history
FILLING_DAYS = 7             # UKMTO can publish days after an incident, so a week this recent may still grow
SHOW_WEEKS = 26

LIMITS = {
    "ukmto": "UKMTO's reports as published: unverified, and they name no one responsible. \"Attack\" is UKMTO's type "
             "name, not a finding of COP's. The counterpart is left unattributed.",
    "record": "UKMTO's record has gaps: report numbers 45-77 and 79-103 are missing from the copies recovered, plus 7 "
              "single numbers. A week with no report in a gap is unknown, not quiet.",
    "transits": "PortWatch counts AIS-visible transits. A ship crossing with its transponder off is not counted, and "
                "how many do so has not been measured, so the two series are shown side by side and never divided.",
    "ais": "Wake's AIS lane covers the Baltic only, so AIS gaps and listed-IMO broadcasts do not exist for this strait.",
    "severity": "Severity runs L0 to L4 with casualties as a separate flag, and applies to reports coded as a vessel "
                "struck. Codes are Wake's reading of UKMTO's text by rule v0, one coder and no second check; a report "
                "Wake has not coded counts as not coded yet.",
}
WHAT = ("Commercial transits through the Strait of Hormuz beside UKMTO's Attack reports in the 900 km circle, by week "
        "of the incident, each against its own history.")
NOT_A_WARNING = "A measurement, not a warning or a forecast. It carries no probability and says nothing about who fired."


def monday(at):
    """The Monday 00:00 UTC of the week holding `at`."""
    return (at - timedelta(days=at.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def week_key(at):
    return monday(at).strftime("%Y-%m-%d")


def km_from_strait(lat, lon):
    return tri._km(STRAIT[0], STRAIT[1], lat, lon)


def code_severity(measures):
    """L0-L4 when the item states one for a vessel coded as struck, otherwise None. Accepts "L2" or "L2 fire ..."."""
    raw = str((measures or {}).get("severity") or "").strip()
    outcome = code_outcome(measures)
    if outcome not in (None, "collide"):
        return None
    head = raw.split(" ", 1)[0].upper()
    return head if head in SEVERITIES else None


def code_outcome(measures):
    raw = str((measures or {}).get("outcome") or "").strip().lower()
    return raw if raw in OUTCOMES else None


def code_then(measures):
    raw = str((measures or {}).get("then_action") or "").strip().lower()
    return raw if raw in THEN_ACTIONS else None


def new_state():
    return {"schema": SCHEMA, "reports": {}, "transits": {}, "covered_from": None}


def _report(feature):
    """The stored record of one UKMTO item, or None if it has no point or no date."""
    p, m = feature["properties"], feature["properties"]["measures"]
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Point":
        return None
    lon, lat = geom["coordinates"][:2]
    at = tri.utc(m.get("incident_at")) or tri.utc(m.get("created_at")) or tri.utc(p.get("data_as_of"))
    if not at:
        return None
    return {"id": p["id"], "number": m.get("ukmto_number"), "report_type": m.get("report_type"),
            "incident_at": tri.iso(at), "created_at": m.get("created_at"), "lat": lat, "lon": lon,
            "vessel_type": m.get("vessel_type"), "outcome": code_outcome(m), "severity": code_severity(m),
            "casualties": m.get("casualties") if isinstance(m.get("casualties"), bool) else None,
            "then_action": code_then(m)}


def add(state, doc, covers=True):
    """Merge one snapshot or ended list into state, in place. Nothing is ever removed. A report keeps its first copy,
    and takes a code when a later copy states one."""
    got = False
    for f in doc.get("features", []):
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict) or not isinstance(p.get("measures"), dict):
            continue
        pid = str(p.get("id") or "")
        if pid.startswith(ts.UKMTO_PREFIX):
            got = True
            rec = _report(f)
            if rec is None:
                continue
            old = state["reports"].setdefault(pid, rec)
            for k in ("outcome", "severity", "casualties", "then_action"):
                if rec.get(k) is not None:
                    old[k] = rec[k]
        elif pid.startswith(TRANSIT_PREFIX):
            day = pid[len(TRANSIT_PREFIX):]
            at = tri.utc(day + "T00:00:00Z")
            if at:
                m = p["measures"]
                state["transits"][week_key(at)] = {
                    "id": pid, "week_to": day, "per_day": m.get("n_total_7d"), "tankers_per_day": m.get("n_tanker_7d"),
                    "median_90d": m.get("baseline_90d_median"), "median_year_ago": m.get("baseline_year_ago_median"),
                    "change_year_ago_pct": m.get("change_year_ago_pct")}
    at = tri.utc(doc.get("generated_at"))
    if covers and got and at:
        start = at - timedelta(days=ts.UKMTO_KEEP_D)
        old = tri.utc(state.get("covered_from"))
        if old is None or start < old:
            state["covered_from"] = tri.iso(start)
    return state


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def replay(state, folder):
    """Merge every archived snapshot and ended list in folder, oldest first."""
    for path in sorted(Path(folder).glob("*.geojson.gz")):
        add(state, read_gz(path))
    for path in sorted(Path(folder).glob("*-ended.json.gz")):
        add(state, read_gz(path), covers=False)
    return state


def in_circle(rec):
    return tri._km(AREA["center"][1], AREA["center"][0], rec["lat"], rec["lon"]) <= AREA["radius_km"]


def _base(values):
    if len(values) < BASE_MIN_WEEKS:
        return {"enough": False, "weeks": len(values), "min_weeks": BASE_MIN_WEEKS}
    return {"enough": True, "weeks": len(values), "median": statistics.median(values),
            "p90": ts._percentile(values, ts.RATE_PERCENTILE)}


def build(state, now):
    """The published document: one row per week, oldest first, each set against the complete weeks before it."""
    placed = [r for r in state["reports"].values() if in_circle(r)]
    covered = tri.utc(state.get("covered_from"))
    by_week = {}
    for r in placed:
        by_week.setdefault(week_key(tri.utc(r["incident_at"])), []).append(r)
    keys = sorted(set(by_week) | set(state["transits"]))
    if keys:   # every week from first to last is a row, so an empty week is visible
        day, last, keys = tri.utc(keys[0] + "T00:00:00Z"), tri.utc(keys[-1] + "T00:00:00Z"), []
        while day <= last:
            keys.append(day.strftime("%Y-%m-%d"))
            day += timedelta(days=7)
    weeks, past_attacks, past_share = [], [], []
    for key in keys:
        start = tri.utc(key + "T00:00:00Z")
        end = start + timedelta(days=7)
        rows = by_week.get(key, [])
        attacks = [r for r in rows if r["report_type"] in COUNTED]
        near = [r for r in attacks if km_from_strait(r["lat"], r["lon"]) <= NEAR_KM]
        severity = {s: sum(1 for r in attacks if r["severity"] == s) for s in SEVERITIES}
        severity["not_coded"] = sum(1 for r in attacks if r["severity"] is None and r["outcome"] is None)
        outcomes = {o: sum(1 for r in rows if r["outcome"] == o) for o in OUTCOMES}
        outcomes["not_coded"] = sum(1 for r in rows if r["outcome"] is None)
        whole = bool(covered and start >= covered)
        complete = end <= now
        share = round(len(near) / len(attacks), 3) if attacks else None
        weeks.append({
            "week": key, "week_to": (end - timedelta(days=1)).strftime("%Y-%m-%d"), "complete": complete,
            "still_filling": now < end + timedelta(days=FILLING_DAYS), "record_whole": whole,
            "transits": state["transits"].get(key),
            "reports": len(rows), "attacks": len(attacks), "attacks_within_150km": len(near), "share_within_150km": share,
            "severity": severity, "outcomes": outcomes,
            "then_action_reports": sum(1 for r in rows if r.get("then_action")), "casualty_reports": sum(1 for r in attacks if r["casualties"] is True),
            "attacks_base": _base(past_attacks), "share_base": _base(past_share),
            "items": [{"id": r["id"], "number": r["number"]} for r in sorted(attacks, key=lambda x: x["incident_at"])]})
        if complete and whole and now >= end + timedelta(days=FILLING_DAYS):
            past_attacks.append(len(attacks))
            if share is not None:
                past_share.append(share)
    return {"join": JOIN, "schema": SCHEMA, "generated_at": tri.iso(now), "what": WHAT, "not_a_warning": NOT_A_WARNING,
            "credit": {"transits": "IMF PortWatch (International Monetary Fund and University of Oxford), via Wake",
                       "reports": ts.UKMTO_CREDIT},
            "history_from": state.get("covered_from"), "reports_held": len(placed),
            "reports_coded": sum(1 for r in placed if r["outcome"] is not None),
            "data_to": {"transits": max((t["week_to"] for t in state["transits"].values()), default=None),
                        "reports": max((r["incident_at"] for r in placed), default=None)},
            "severity_scale": SEVERITY_WORDS, "limits": LIMITS, "weeks": weeks[-SHOW_WEEKS:]}


def load_state(folder):
    path = Path(folder) / STATE_NAME
    return read_gz(path) if path.exists() else None


def save_state(state, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / STATE_NAME, "wt", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, sort_keys=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--archive", help="folder of archived days, merged every run")
    ap.add_argument("--state", required=True, help="folder holding state.json.gz")
    ap.add_argument("--publish", help="folder for hormuz-stream.json")
    a = ap.parse_args(argv)
    snapshot = json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    now = tri.utc(snapshot.get("generated_at"))
    if not now:
        raise SystemExit("the snapshot has no generated_at")
    state = load_state(a.state) or new_state()
    if a.archive:
        replay(state, a.archive)
    add(state, snapshot)
    save_state(state, a.state)
    doc = build(state, now)
    if a.publish:
        gate.write(JOIN, doc, a.publish, ["hormuz-stream.json"])
    print(f"hormuz-stream: {doc['reports_held']} reports in the circle, {len(state['transits'])} transit weeks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
