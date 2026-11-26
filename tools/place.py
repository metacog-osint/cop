"""Place pages: what each layer of COP's snapshot holds now for every watched area (site/place.html).

The trend lines on the page come from the history set (tools/history.py, site/data/history/areas/<area>.json). This
tool adds only the current reading, from the snapshot COP has already built, placed by the history set's own rule
(tools/history.py zones_of: direction's circle or country list, or the area's press zone named in the item), so the
line and today's figure count the same things. It adds no source and makes no reading across layers: each layer is a
count of what its program placed there, with the program's freshness.

    python tools/place.py --snapshot site/data/today.geojson --publish site/data

Writes <publish>/places/index.json and <publish>/places/<area>.json through tools/gate.py. Standard-library Python.
"""

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import history as hi  # noqa: E402

FORMAT = "cop-place/1"
JOIN = {"id": "place", "version": "0.1", "published": True, "inputs": []}   # inputs: every program, set in build()
TOP = 3          # items listed per layer under the count
FOLDER = "places"
WINDOWS = (30, 90)   # days in each trend line
TREND_SHARE = 0.8    # a line is drawn only when its program was read on at least this share of the window's days


def window(series, days, n):
    """One trend window ending on the history's last closed day: the daily counts, or why there is no line.

    A day the program was not read (or before the history starts) has no value, never a zero. The line is drawn only
    when the program was read on at least TREND_SHARE of the window's days; otherwise the page says too little history.
    """
    if not days:
        return {"days": n, "enough": False, "read_days": 0, "start": None, "end": None}
    end = date.fromisoformat(days[-1])
    want = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    at = {d: i for i, d in enumerate(days)}

    def pick(key):
        vals = series.get(key) or []
        return [vals[at[d]] if d in at and at[d] < len(vals) and (series.get("read") or [False] * len(days))[at[d]]
                else None for d in want]

    items, anomalies = pick("items"), pick("anomalies")
    notes = [(series.get("notes") or [None] * len(days))[at[d]] if d in at else None for d in want]
    read = [v is not None for v in items]
    seen = [v for v in items if v is not None]
    return {"days": n, "start": want[0], "end": want[-1], "read_days": sum(read),
            "enough": sum(read) >= TREND_SHARE * n, "items": items, "anomalies": anomalies, "notes": notes,
            "median": statistics.median(seen) if seen else None}


def trends(hist):
    """Each layer's 30- and 90-day windows from the history set's per-area file, with its method versions."""
    if not hist:
        return None
    days = hist.get("days") or []
    return {prog: {"windows": [window(s, days, n) for n in WINDOWS], "method_versions": s.get("method_versions") or []}
            for prog, s in (hist.get("layers") or {}).items()}


def definition(area):
    """How the area is drawn: a circle, a list of countries, or the whole world."""
    if area.get("worldwide"):
        return {"kind": "worldwide"}
    if area.get("radius_km") and area.get("center"):
        return {"kind": "circle", "center": area["center"], "radius_km": area["radius_km"],
                "countries": area.get("countries") or []}
    return {"kind": "countries", "countries": area.get("countries") or []}


def in_place(p, geometry, area):
    if area.get("worldwide"):
        return True
    subject, _ = hi.subject_of(p, p["id"])
    countries = [c for c in (p.get("countries") or []) if isinstance(c, str) and len(c) == 2]
    return bool(hi.zones_of(subject, p["id"], countries, hi.point_of(geometry), [area]))


def item_row(p):
    return {k: p[k] for k in ("id", "title", "tier", "kind", "status_class", "data_as_of", "data_as_of_precision")
            if p.get(k) is not None} | {"stale": bool(p.get("cop_stale"))}


def place_doc(snapshot, area, programs, hist=None):
    infos = snapshot.get("cop_programs") or {}
    by = {k: [] for k in programs}
    for f in snapshot.get("features") or []:
        p = f.get("properties") or {}
        prog = p.get("cop_program") or p.get("program")
        if prog in by and p.get("id") and in_place(p, f.get("geometry"), area):
            by[prog].append(p)
    layers = {}
    for prog in programs:
        info = infos.get(prog) or {}
        rows = by[prog]
        anomalies = [p for p in rows if p.get("tier") in ("anomaly", "warning")]
        top = sorted(anomalies, key=lambda p: p.get("data_as_of") or "", reverse=True)[:TOP]
        layers[prog] = {"name": info.get("name") or prog, "state": info.get("state") or "missing",
                        "data_as_of": info.get("data_as_of"), "site": info.get("site"),
                        "items": len(rows), "anomalies": len(anomalies), "top": [item_row(p) for p in top]}
    return {"format": FORMAT, "join": dict(JOIN), "area": area["area"], "name": area.get("name") or area["area"],
            "definition": definition(area), "snapshot_generated_at": snapshot.get("generated_at"), "layers": layers,
            "history": {"available": hist is not None, "first_day": (hist or {}).get("days", [None])[0] if hist else None,
                        "last_day": (hist or {}).get("days", [None])[-1] if hist else None,
                        "trend_share": TREND_SHARE, "layers": trends(hist)}}


def build(snapshot, history_folder=None):
    programs = sorted(k for k, v in (snapshot.get("cop_programs") or {}).items() if isinstance(v, dict))
    JOIN["inputs"] = programs
    areas = hi.load_areas()
    docs = [place_doc(snapshot, a, programs, read_history(history_folder, a["area"])) for a in areas]
    index = {"format": FORMAT, "join": dict(JOIN), "snapshot_generated_at": snapshot.get("generated_at"),
             "areas": [{"area": d["area"], "name": d["name"], "kind": d["definition"]["kind"]} for d in docs]}
    return index, docs


def read_history(folder, area):
    p = Path(folder) / f"{area}.json" if folder else None
    if not p or not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=str(gate.ROOT / "site" / "data" / "today.geojson"))
    ap.add_argument("--history", default=str(gate.ROOT / "site" / "data" / "history" / "areas"),
                    help="the history set's per-area files (tools/history.py)")
    ap.add_argument("--publish", required=True, metavar="FOLDER", help="the build passes site/data")
    a = ap.parse_args(argv)
    index, docs = build(json.loads(Path(a.snapshot).read_text(encoding="utf-8")), a.history)
    folder = Path(a.publish) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    verdict = gate.write(JOIN, index, str(folder), ("index.json",))
    for d in docs:
        gate.write(JOIN, d, str(folder), (f"{d['area']}.json",))
    print(f"places: {len(docs)} areas  gate: {verdict['review_class']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
