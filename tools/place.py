"""Place pages: what each layer of COP's snapshot holds now for every watched area (site/place.html).

The trend lines on the page come from the history set (tools/history.py, site/data/history/areas/<area>.json). This
tool adds only the current reading, from the snapshot COP has already built, placed by the history set's own rule
(tools/history.py zones_of: direction's circle or country list, or the area's press zone named in the item; a
worldwide problem holds only its own layers' items), so the line and today's figure count the same things. It adds no source and makes no reading across layers: each layer is a
count of what its program placed there, with the program's freshness.

Each area also gets its row of the coverage table (kit format tic-coverage/1, docs/event-ladder.md): how many
independent source groups stand behind the items COP holds for it, counting only items read on time (not a stale
copy, and not an item its program says has ended). A source that watches a place but has nothing to report there is
missed, so every count is a lower bound. The row also carries `layers`: each program's own class for the place, from
that program's items alone, so a layer with thin coverage shows even where the layers together read "several".

    python tools/place.py --snapshot site/data/today.geojson --publish site/data

Writes <publish>/places/index.json, <publish>/places/<area>.json and <publish>/places/coverage.json through
tools/gate.py. Standard-library Python.
"""

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "doctrine" / "kit" / "python"))
import gate  # noqa: E402
import history as hi  # noqa: E402
import snapshot as snap  # noqa: E402
from tic_doctrine import ladder, vocab  # noqa: E402

FORMAT = "cop-place/1"
JOIN = {"id": "place", "version": "0.1", "published": True, "inputs": []}   # inputs: every program, set in build()
TOP = 3          # items listed per layer under the count
FOLDER = "places"
WINDOWS = (30, 90)   # days in each trend line
TREND_SHARE = 0.8    # a line is drawn only when its program was read on at least this share of the window's days
COVERAGE_NAME = "coverage.json"
COVERAGE_BASIS = ("independence groups behind the items COP holds for each place at this snapshot, placed by the "
                  "place pages' rule, from programs and sources read on time; stale copies and items their program "
                  "says have ended are not counted. A source that watches a place but has nothing to report there is "
                  "missed, so each count is a lower bound")


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
        layers = [lay for lay in area.get("layers") or [] if lay in hi.dr.LAYER_NAMES]
        return {"kind": "worldwide", "layers": layers, "programs": worldwide_programs(area),
                "counts": "Worldwide, and only this problem's own layers: "
                          + "; ".join(f"{hi.dr.LAYER_NAMES[lay]} ({hi.dr.LAYER_PROGRAM[lay].capitalize()})"
                                      for lay in layers)
                          + ". Other programs' items are not counted here, wherever they are."}
    if area.get("radius_km") and area.get("center"):
        return {"kind": "circle", "center": area["center"], "radius_km": area["radius_km"],
                "countries": area.get("countries") or []}
    return {"kind": "countries", "countries": area.get("countries") or []}


def worldwide_programs(area):
    """The programs whose layers a worldwide problem reads (config/areas.json layers, direction's LAYER_PROGRAM)."""
    return sorted({hi.dr.LAYER_PROGRAM[lay] for lay in area.get("layers") or [] if lay in hi.dr.LAYER_PROGRAM})


def in_place(p, geometry, area):
    subject, _ = hi.subject_of(p, p["id"])
    countries = [c for c in (p.get("countries") or []) if isinstance(c, str) and len(c) == 2]
    return bool(hi.zones_of(subject, p["id"], countries, hi.point_of(geometry), [area]))


def counts_for_coverage(p, now):
    """An item counts toward its place's coverage only when it was read on time (not a stale copy, and its own source
    was read on its program's last run) and its program has not said it ended (snapshot.no_longer_current)."""
    return not p.get("cop_stale") and not snap.no_longer_current(p, now)


def layer_coverage(area, name, held, programs, infos):
    """Each program's own coverage class for a place: the kit's coverage_place() over that program's items alone (the
    same items read on time as the place's row). A program with no such item reads none ("Not monitored"), which
    also covers a program that watches the place but has nothing to report there: a lower bound, like the row.
    Groups are listed by id only; their labels are in the row's own groups."""
    out = {}
    for prog in programs:
        row = ladder.coverage_place(area, name, [p for p in held if (p.get("cop_program") or p.get("program")) == prog])
        out[prog] = {"class": row["class"], "independent": row["independent"], "groups": [g["id"] for g in row["groups"]],
                     "set_aside": len(row["set_aside"]), "not_counted": len(row["not_counted"]),
                     "state": (infos.get(prog) or {}).get("state") or "missing"}
    return out


def item_row(p):
    return {k: p[k] for k in ("id", "title", "tier", "kind", "status_class", "data_as_of", "data_as_of_precision",
                              "countries") if p.get(k) is not None} | {"stale": bool(p.get("cop_stale"))}


def place_doc(snapshot, area, programs, hist=None):
    infos = snapshot.get("cop_programs") or {}
    now = snap.utc(snapshot.get("generated_at"))
    if area.get("worldwide"):    # only the programs of its own layers: the others would read as "nothing placed here"
        programs = [k for k in programs if k in worldwide_programs(area)]
    by = {k: [] for k in programs}
    held = []
    for f in snapshot.get("features") or []:
        p = f.get("properties") or {}
        prog = p.get("cop_program") or p.get("program")
        if prog in by and p.get("id") and in_place(p, f.get("geometry"), area):
            by[prog].append(p)
            if counts_for_coverage(p, now):
                held.append(p)
    layers = {}
    for prog in programs:
        info = infos.get(prog) or {}
        rows = by[prog]
        anomalies = [p for p in rows if p.get("tier") in ("anomaly", "warning")]
        top = sorted(anomalies, key=lambda p: p.get("data_as_of") or "", reverse=True)[:TOP]
        layers[prog] = {"name": info.get("name") or prog, "state": info.get("state") or "missing",
                        "data_as_of": info.get("data_as_of"), "site": info.get("site"),
                        "items": len(rows), "anomalies": len(anomalies), "top": [item_row(p) for p in top]}
    name = area.get("name") or area["area"]
    coverage = ladder.coverage_place(area["area"], name, held) | {
        "layers": layer_coverage(area["area"], name, held, programs, infos)}
    return {"format": FORMAT, "join": dict(JOIN), "area": area["area"], "name": name,
            "definition": definition(area), "snapshot_generated_at": snapshot.get("generated_at"), "layers": layers,
            "coverage": coverage, "coverage_method": coverage_method(),
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


def coverage_method():
    """How the coverage rows are counted: COP's basis, the kit's class rule and the kit page that defines both."""
    return {"format": ladder.COVERAGE_FORMAT, "basis": COVERAGE_BASIS,
            "rule": vocab.load("event_ladder")["coverage"]["rule"], "url": ladder.url()}


def coverage_doc(snapshot, docs):
    """The coverage table for every watched area (kit format tic-coverage/1). It is counted from the snapshot, so its
    generated_at is the snapshot's own time."""
    m = coverage_method()
    return {"format": m["format"], "join": dict(JOIN), "generated_at": snapshot.get("generated_at"),
            "basis": m["basis"], "rule": m["rule"], "url": m["url"], "places": [d["coverage"] for d in docs]}


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
    snapshot = json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    index, docs = build(snapshot, a.history)
    folder = Path(a.publish) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    verdict = gate.write(JOIN, index, str(folder), ("index.json",))
    for d in docs:
        gate.write(JOIN, d, str(folder), (f"{d['area']}.json",))
    gate.write(JOIN, coverage_doc(snapshot, docs), str(folder), (COVERAGE_NAME,))
    print(f"places: {len(docs)} areas  gate: {verdict['review_class']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
