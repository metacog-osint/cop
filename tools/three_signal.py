"""Three-signal join: interference, a new notice and a connectivity drop in one area inside 48 hours.

Scenario 1 of D:/Projects/_ideas/convergence-scenarios.md, with Iran and the Strait of Hormuz as the live case. Each
signal alone is routine and has an innocent explanation; the join asks only whether all three fall in one named area
within WINDOW_H of each other, and what Vis forecasts for the weather there.

  interference   a Noisefloor anomaly (new, rise or event; never "ended" or the daily summary) inside the area
  notice         a new Cordon navigation warning inside the area whose text names military activity, or a new or
                 revised EASA conflict-zone bulletin for one of the area's countries. Cordon marks every item
                 "routine", so tier is not used here.
  connectivity   a confirmed Blackout outage (BO-IND-1) or a Cloudflare-verified traffic anomaly in one of the
                 area's countries. A single-source dip is listed and does not count.

Vis is a condition, not a signal: a green 72-hour window means the weather would allow an operation. It never adds
to the count, and a red window does not remove a signal; it is reported beside the reading.

HELD. This join places public signals around military activity (the mosaic effect). tools/gate.py classes it "held":
it writes only under out/held/, the build never runs it, and nothing publishes it. Only Tom releases a held file, by
hand (COP's CLAUDE.md).

    python tools/three_signal.py                       # the published snapshot, every area
    python tools/three_signal.py --archive out/archive # also replay the archive: how often each signal fires anyway

It never states a probability: that is the analyst's. Standard-library Python only.
"""

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import triage as tri  # noqa: E402

JOIN = {"id": "three-signal", "version": "0.1", "published": False,
        "inputs": ["noisefloor", "vis", "blackout", "cordon:navwarn", "cordon:czib", "cordon:alert",
                   "cordon:advisory"]}
LAYERS = ("noisefloor", "cordon", "blackout", "vis")
SIGNALS = ("interference", "notice", "connectivity")

# PROVISIONAL, like triage.py's windows: first picks, to be looked at again with a deeper archive.
WINDOW_H = 48        # the three signals must fall within this many hours of each other
LOOKBACK_H = 96      # how far back from the snapshot a signal may be dated and still be read
QUESTION_DAYS = 7    # the Verdict question's window
OONI_MOVE_POINTS = 5 # an OONI rate that moved this much against the archive is listed as context
NAVWARN_BLIND_DAYS = 14  # Cordon's newest navigation warning anywhere older than this: that feed has stopped moving,
                         # so "no new warning" means nothing (found 12 Nov 2026: newest was dated 10 May 2024)

# An area is a circle, because interference and warnings ignore borders, plus the countries whose country-level
# items (outages, airspace bulletins, embassy alerts) are read with it.
AREAS = {
    "hormuz": {"name": "Strait of Hormuz and the Gulf", "center": [56.378, 27.218], "radius_km": 900,
               "center_name": "Bandar Abbas", "countries": ["IR", "AE", "OM", "QA", "BH", "KW"]},
}
# Words that mark a navigation warning as about military activity or a threat, not a drifting buoy or a survey.
MILITARY_WORDS = ("FIRING", "GUNNERY", "MISSILE", "ROCKET", "MILITARY", "NAVAL EXERCISE", "EXERCISE", "HAZARDOUS OP",
                  "MINE", "DRONE", "UAV", "UNMANNED", "ATTACK", "GPS", "GNSS", "INTERFERENCE", "JAMMING", "SPOOFING",
                  "WARSHIP", "SUBMARINE", "SPACE DEBRIS", "LAUNCH")
INTERFERENCE_KINDS = {"new", "rise", "event"}

READINGS = {
    "incomplete": "A layer has no current data, so this area cannot be read. Silence is not quiet.",
    "none": "None of the three signals is present in this area.",
    "partial": "Some of the three signals are present, not all within the window. Each has an innocent explanation "
               "alone.",
    "converged": "All three signals fall in this area within the window. That is the pattern this join looks for; "
                 "it is not a warning, and the call is the analyst's.",
}


def _points(geometry):
    if not isinstance(geometry, dict):
        return []
    t, c = geometry.get("type"), geometry.get("coordinates")
    try:
        if t == "Point":
            return [c[:2]]
        if t in ("MultiPoint", "LineString"):
            return [p[:2] for p in c]
        if t == "Polygon":
            ring = [p[:2] for p in c[0]]
        elif t == "MultiPolygon":
            ring = [p[:2] for poly in c for p in poly[0]]
        else:
            return []
    except (TypeError, IndexError):
        return []
    if not ring:
        return []
    centre = [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    return ring + [centre]


def km_to_area(geometry, area):
    """Shortest distance in km from the geometry's points to the area's centre, or None without geometry."""
    lon0, lat0 = area["center"]
    ds = [tri._km(lat0, lon0, lat, lon) for lon, lat in _points(geometry)]
    return min(ds) if ds else None


def _in_area(feature, area, by_country=False):
    """(inside, km). An item with a place is judged by the circle alone: interference "near Yerevan" lists Iran as
    a country and is 1,700 km from the Strait. by_country is for country-level items (airspace bulletins)."""
    km = km_to_area(feature.get("geometry"), area)
    if by_country:
        inside = bool(set(feature["properties"].get("countries") or []) & set(area["countries"]))
        return inside, None if km is None else round(km)
    if km is None:
        return False, None
    return km <= area["radius_km"], round(km)


def _nf_kind(item_id):
    parts = str(item_id).split("-")
    return parts[1] if len(parts) > 1 else ""


def _military(p):
    text = (str(p.get("title")) + " " + str(p.get("summary"))).upper()
    return sorted(w for w in MILITARY_WORDS if w in text)


def first_seen(snapshots):
    """{id | title: generated_at of the first archived snapshot that carried it}, for airspace bulletins: a
    bulletin keeps its id and original date through revisions, so a revision shows only as a changed title."""
    seen, first = {}, None
    for snap in sorted(snapshots, key=lambda s: s.get("generated_at") or ""):
        first = first or snap.get("generated_at")
        for f in snap.get("features", []):
            p = f.get("properties") or {}
            if p.get("cop_program") == "cordon" and tri._kind(p.get("id")) == "czib":
                seen.setdefault(f"{p.get('id')} | {p.get('title')}", snap.get("generated_at"))
    # Whatever the oldest snapshot carried was already there: it is not "new" on that day.
    return {"archive_starts": first, "bulletins": {k: v for k, v in seen.items() if v != first}}


def ooni_history(snapshots):
    """{cc: [[snapshot time, rate], ...]} of Blackout's OONI readings, oldest first."""
    out = {}
    for snap in sorted(snapshots, key=lambda s: s.get("generated_at") or ""):
        for f in snap.get("features", []):
            p = f.get("properties") or {}
            if p.get("cop_program") == "blackout" and tri._kind(p.get("id")) == "ooni":
                rate = tri._rate(p.get("title"))
                for cc in p.get("countries") or []:
                    if rate is not None:
                        out.setdefault(cc, []).append([snap.get("generated_at"), rate])
    return out


def read_area(snapshot, key, area, seen=None, ooni=None):
    """One record for one area from one snapshot."""
    at = tri.utc(snapshot.get("generated_at"))
    features = [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]
    states = {pid: tri.layer_state(snapshot, features, pid) for pid in LAYERS}
    lo = at - timedelta(hours=LOOKBACK_H)
    found = {s: [] for s in SIGNALS}
    context = {"interference_ended": [], "notices_other": [], "notices_standing": [], "single_source_dips": [],
               "embassy_alerts": [], "ooni": []}
    weather = []
    bulletins = (seen or {}).get("bulletins")
    newest_navwarn = max((tri.utc(f["properties"].get("data_as_of")) for f in features
                          if f["properties"].get("cop_program") == "cordon"
                          and tri._kind(f["properties"].get("id")) == "navwarn"
                          and tri.utc(f["properties"].get("data_as_of"))), default=None)
    blind = [] if newest_navwarn and newest_navwarn >= at - timedelta(days=NAVWARN_BLIND_DAYS) else ["cordon:navwarn"]
    for f in features:
        p = f["properties"]
        if p.get("cop_stale"):
            continue
        pid, dated = p.get("cop_program"), tri.utc(p.get("data_as_of"))
        if pid == "noisefloor":
            inside, km = _in_area(f, area)
            if not inside:
                continue
            row = tri._row(p, kind=_nf_kind(p.get("id")), km_from_centre=km, at=p.get("data_as_of"))
            if row["kind"] in INTERFERENCE_KINDS and p.get("tier") in ("anomaly", "warning") and dated and dated >= lo:
                found["interference"].append(row)
            elif row["kind"] == "ended":
                context["interference_ended"].append(row)
        elif pid == "cordon":
            kind = tri._kind(p.get("id"))
            if kind == "navwarn":
                inside, km = _in_area(f, area)
                if not inside:
                    continue
                words = _military(p)
                row = tri._row(p, kind=kind, km_from_centre=km, military_words=words, at=p.get("data_as_of"))
                if not dated or dated < lo:
                    context["notices_standing"].append(row)
                elif words:
                    found["notice"].append(row)
                else:
                    context["notices_other"].append(row)
            elif kind == "czib":
                inside, km = _in_area(f, area, by_country=True)
                if not inside:
                    continue
                new_at = dated if dated and dated >= lo else None
                if new_at is None and bulletins is not None:
                    first = tri.utc(bulletins.get(f"{p.get('id')} | {p.get('title')}"))
                    new_at = first if first and first >= lo else None
                row = tri._row(p, kind=kind, km_from_centre=km, at=tri.iso(new_at) if new_at else None,
                               valid_until=p.get("valid_until"))
                (found["notice"] if new_at else context["notices_standing"]).append(row)
            elif kind == "alert" and set(p.get("countries") or []) & set(area["countries"]) and dated and dated >= lo:
                context["embassy_alerts"].append(tri._row(p, kind=kind))
        elif pid == "blackout":
            kind = tri._kind(p.get("id"))
            ccs = sorted(set(p.get("countries") or []) & set(area["countries"]))
            if not ccs:
                continue
            if kind in ("outage", "radar-anomaly") and dated and dated >= lo:
                row = tri._row(p, kind=kind, countries=ccs, indicator_id=p.get("indicator_id"), at=p.get("data_as_of"))
                if kind == "outage" and p.get("indicator_id") != tri.CONFIRMED_OUTAGE:
                    context["single_source_dips"].append(row)
                else:
                    found["connectivity"].append(row)
            elif kind == "ooni":
                rate = tri._rate(p.get("title"))
                for cc in ccs:
                    past = [r for t, r in (ooni or {}).get(cc, []) if tri.utc(t) and tri.utc(t) < at]
                    low = min(past) if past else None
                    moved = None if rate is None or low is None else round(rate - low, 1)
                    context["ooni"].append(tri._row(p, country=cc, rate=rate, lowest_in_archive=low,
                                                    points_above_lowest=moved,
                                                    moved=bool(moved is not None and moved >= OONI_MOVE_POINTS)))
        elif pid == "vis":
            inside, km = _in_area(f, area)
            m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
            if inside and m.get("stoplight_72h"):
                weather.append(tri._row(p, station=m.get("station"), stoplight_72h=m.get("stoplight_72h"),
                                        dark_low_light_hours=None if m.get("dark_low_light_min") is None
                                        else round(m["dark_low_light_min"] / 60, 1),
                                        moon_lit=m.get("moon_lit"), km_from_centre=km, kind="forecast"))
    # The tightest span that holds one item of every signal, if there is one.
    span = None
    times = {s: sorted(tri.utc(r["at"]) for r in found[s] if tri.utc(r.get("at"))) for s in SIGNALS}
    if all(times.values()):
        for start in sorted(t for ts in times.values() for t in ts):
            picks = [next((t for t in times[s] if t >= start), None) for s in SIGNALS]
            if all(picks):
                hours = (max(picks) - start).total_seconds() / 3600
                if span is None or hours < span["hours"]:
                    span = {"from": tri.iso(start), "to": tri.iso(max(picks)), "hours": round(hours, 1)}
    present = [s for s in SIGNALS if found[s]]
    dead = [pid for pid in LAYERS if states[pid] in ("stale", "missing")]
    if dead:
        reading = "incomplete"
    elif span and span["hours"] <= WINDOW_H:
        reading = "converged"
    elif blind:
        reading = "incomplete"
    elif present:
        reading = "partial"
    else:
        reading = "none"
    lights = {w["stoplight_72h"] for w in weather}
    window = ("unknown" if not weather or "vis" in dead else
              "workable" if lights == {"green"} else "against" if "red" in lights else "marginal")
    caveats = ["Vis's items are forecasts at indicative thresholds, not official forecasts and not fit for flight "
               "planning. The weather window is a condition beside the reading, never a signal."]
    if blind:
        caveats.append("Cordon's navigation warnings have stopped moving: the newest one anywhere is dated "
                       f"{tri.iso(newest_navwarn) if newest_navwarn else 'never'}. A new warning cannot be seen, so "
                       "the notice signal can fire only on an airspace bulletin, and this area reads incomplete "
                       "unless all three converge without it.")
    if seen is None:
        caveats.append("No archive was given: a revised airspace bulletin cannot be told from a standing one, so "
                       "only a bulletin first issued inside the lookback counts.")
    if found["connectivity"] or context["single_source_dips"]:
        caveats.append("Blackout's outages are country-level: an outage anywhere in a listed country counts for "
                       "the whole area.")
    if context["ooni"]:
        caveats.append("Blackout's OONI tier is a fixed threshold, so Iran is an anomaly there on any ordinary day. "
                       "The rate's movement against the archive is listed as context and is not a signal.")
    until = at + timedelta(days=QUESTION_DAYS)
    return {
        "join": JOIN["id"], "area": key, "area_name": area["name"],
        "circle": {"center": area["center"], "center_name": area["center_name"], "radius_km": area["radius_km"]},
        "countries": area["countries"], "read_at": tri.iso(at), "layer_states": states, "no_current_data": dead + blind,
        "signals": {s: {"present": bool(found[s]), "items": found[s]} for s in SIGNALS},
        "signals_present": present, "tightest_span": span,
        "reading": reading, "reading_text": READINGS[reading],
        "weather_window": {"state": window, "forecasts": weather},
        "context": context, "caveats": caveats,
        "verdict_draft": None if reading != "converged" else {
            "registered": False, "probability": None, "approved_by": None,
            "question": f"Will a strike or incursion be reported in the area of the {area['name']} within "
                        f"{QUESTION_DAYS} days of {tri.iso(at)}?",
            "window_ends": tri.iso(until),
            "rule_draft": "Yes if two of Reuters, AP and AFP report a military strike on, or an armed incursion "
                          f"into, the territory or territorial waters of {', '.join(area['countries'])} dated "
                          f"inside the window ending {tri.iso(until)}. No otherwise.",
            "note": "Rare events: a base rate from the archive is needed before any probability means much."},
    }


def read(snapshot, seen=None, ooni=None):
    return {"join": JOIN, "snapshot_generated_at": snapshot.get("generated_at"),
            "windows": {"window_hours": WINDOW_H, "lookback_hours": LOOKBACK_H, "question_days": QUESTION_DAYS,
                        "provisional": True},
            "archive_given": seen is not None,
            "records": [read_area(snapshot, k, a, seen, ooni) for k, a in sorted(AREAS.items())]}


def replay(snapshots):
    """How often each signal fires in each area anyway, one row per archived day and area."""
    snapshots = sorted(snapshots, key=lambda s: s.get("generated_at") or "")
    seen, ooni = first_seen(snapshots), ooni_history(snapshots)
    rows = []
    for snap in snapshots:
        for r in read(snap, seen, ooni)["records"]:
            rows.append({"day": r["read_at"][:10], "area": r["area"], "reading": r["reading"],
                         "signals_present": r["signals_present"], "no_current_data": r["no_current_data"],
                         "weather_window": r["weather_window"]["state"]})
    by_area = {}
    for key in sorted(AREAS):
        mine = [r for r in rows if r["area"] == key]
        full = [r for r in mine if r["reading"] != "incomplete"]
        by_area[key] = {
            "days": len(mine), "days_with_every_layer": len(full),
            "days_each_signal_present": {s: sum(s in r["signals_present"] for r in full) for s in SIGNALS},
            "days_by_reading": {k: sum(r["reading"] == k for r in mine) for k in READINGS},
            "caution": "A day counts only when all four layers had current data. Blackout joined COP on 12 Nov 2026, "
                       "so earlier days are incomplete and say nothing about how often the three converge."}
    return {"by_area": by_area, "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=tri.PUBLISHED, help="snapshot URL, .geojson file or .geojson.gz archive copy")
    ap.add_argument("--archive", default=None, help="folder of YYYY-MM-DD.geojson.gz: revisions, OONI history, replay")
    ap.add_argument("--out", default=str(gate.HELD_DIR / "three-signal"))
    a = ap.parse_args(argv)
    seen = ooni = past = None
    if a.archive:
        files = sorted(Path(a.archive).glob("*.geojson.gz"))
        if not files:
            raise SystemExit(f"no *.geojson.gz in {a.archive}")
        snaps = [tri.load(f) for f in files]
        seen, ooni, past = first_seen(snaps), ooni_history(snaps), replay(snaps)
    doc = read(tri.load(a.snapshot), seen, ooni)
    doc["archive_replay"] = past
    stamp = (doc["snapshot_generated_at"] or "unknown").replace(":", "")
    verdict = gate.write(JOIN, doc, a.out, (f"three-signal-{stamp}.json", "latest.json"))
    print(f"snapshot {doc['snapshot_generated_at']}  gate: {verdict['review_class']} (not for publication)")
    for r in doc["records"]:
        print(f"{r['area_name']}: {r['reading']}; signals present: {', '.join(r['signals_present']) or 'none'}; "
              f"weather window {r['weather_window']['state']}; layers {r['layer_states']}")
        for s in SIGNALS:
            for it in r["signals"][s]["items"]:
                print(f"    {s}: {it['id']}  {it.get('at')}  {str(it['title'])[:90]}")
        if r["tightest_span"]:
            print(f"    tightest span holding all three: {r['tightest_span']['hours']} h")
        c = r["context"]
        print(f"    context: {len(c['notices_other'])} other new warnings, {len(c['notices_standing'])} standing "
              f"notices, {len(c['single_source_dips'])} single-source dips, {len(c['embassy_alerts'])} embassy alerts, "
              f"{len(c['interference_ended'])} interference ended")
        for o in c["ooni"]:
            print(f"    OONI {o['country']}: {o['rate']}%  lowest in archive {o['lowest_in_archive']}  "
                  f"{'MOVED' if o['moved'] else 'no move'}")
    if past:
        for key, b in past["by_area"].items():
            print(f"archive, {key}: {b['days_with_every_layer']} of {b['days']} days had every layer; signal days "
                  f"{b['days_each_signal_present']}; readings {b['days_by_reading']}")
    else:
        print("no archive given: run with --archive out/archive")
    print(f"wrote {Path(a.out).resolve() / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
