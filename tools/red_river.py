"""The Red River Valley view: what COP's snapshot holds for North Dakota and Minnesota, layer by layer.

It reads only the snapshot COP has already built, so it adds no source. For each state it lists the items placed there
by Cordon and Elements (their own ISO 3166-2 `subdivisions`) and Blackout's ODIN state power items (ids
blackout:us-power:<state>:<UTC day>), with each layer's freshness and sources.

It is a list by place. It makes no reading, score or inference across layers. An empty layer says which sources were
read and when, because nothing listed is not the same as nothing happening: Blackout raises a state only above its
meter line, and a source that was not read is named as not read.

    python tools/red_river.py --snapshot site/data/today.geojson --publish site/data

Standard-library Python only.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
from triage import POWER_LINE, us_state_of  # noqa: E402

PUBLIC_NAME = "red-river.json"
JOIN = {"id": "red-river", "version": "0.1", "published": True, "inputs": ["cordon", "elements", "blackout:us-power"]}
STATES = (("ND", "North Dakota"), ("MN", "Minnesota"))
LAYERS = (
    {"program": "cordon", "label": "Official notices", "lanes": {"faa-tfr": "FAA temporary flight restrictions"}},
    {"program": "elements", "label": "Natural hazards", "lanes": {
        "nws-alerts": "National Weather Service alerts", "usgs-earthquakes": "USGS earthquakes",
        "usgs-volcanoes": "USGS volcanoes", "nifc-wfigs": "National Interagency Fire Center",
        "gdacs": "GDACS", "noaa-tsunami": "NOAA tsunami warning centres"}},
    {"program": "blackout", "label": "Grid outages", "lanes": {"odin-ornl": "ODIN (Oak Ridge National Laboratory)"}},
)
ITEM_KEYS = ("id", "title", "tier", "kind", "status_class", "data_as_of", "data_as_of_precision", "valid_until", "link")


def in_state(p, code):
    """Whether a snapshot item belongs to this layer's list for the state."""
    if p.get("cop_program") == "blackout":
        return us_state_of(p) == code
    return p.get("cop_program") in ("cordon", "elements") and f"US-{code}" in (p.get("subdivisions") or [])


def row(p):
    out = {k: p[k] for k in ITEM_KEYS if p.get(k) is not None}
    out["stale"] = bool(p.get("cop_stale"))
    out["publishers"] = sorted({s["publisher"] for s in p.get("sources") or [] if isinstance(s, dict) and s.get("publisher")})
    return out


def join_names(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def empty_text(layer, state_name, unread, program_state):
    """What an empty layer says. It reports the check and never calls the state quiet."""
    if program_state == "stale" or unread:
        what = join_names(unread) if unread else "this program's feed"
        return f"No current data from {what}, so this list cannot tell you what is happening in {state_name}."
    text = f"Nothing placed in {state_name} in this build. Checked: {join_names(layer['lanes'].values())}."
    if layer["program"] == "blackout":
        text += f" Blackout lists a state only above {POWER_LINE:,} meters without power, so smaller outages do not appear here."
    return text


def build(snapshot):
    programs = snapshot.get("cop_programs") or {}
    states = []
    for code, name in STATES:
        layers = []
        for layer in LAYERS:
            info = programs.get(layer["program"]) or {}
            read = info.get("lanes_read")
            unread = [] if read is None else [n for lane, n in layer["lanes"].items() if lane not in read]
            items = [row(f["properties"]) for f in snapshot.get("features", []) if in_state(f["properties"], code)
                     and f["properties"].get("cop_program") == layer["program"]]
            items.sort(key=lambda r: r.get("data_as_of") or "", reverse=True)
            state = info.get("state") or "missing"
            layers.append({"program": layer["program"], "name": info.get("name") or layer["program"],
                           "label": layer["label"], "state": state, "data_as_of": info.get("data_as_of"),
                           "site": info.get("site"), "sources": list(layer["lanes"].values()), "not_read": unread,
                           "lanes_known": read is not None, "items": items,
                           "empty_text": empty_text(layer, name, unread or ([] if state != "missing" else ["this program's feed"]), state)})
        states.append({"code": code, "name": name, "layers": layers})
    return {"join": dict(JOIN), "snapshot_generated_at": snapshot.get("generated_at"), "power_line": POWER_LINE, "states": states}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=str(gate.ROOT / "site" / "data" / "today.geojson"))
    ap.add_argument("--publish", required=True, metavar="FOLDER", help="the build passes site/data")
    a = ap.parse_args(argv)
    doc = build(json.loads(Path(a.snapshot).read_text(encoding="utf-8")))
    verdict = gate.write(JOIN, doc, a.publish, (PUBLIC_NAME,))
    counts = {s["code"]: sum(len(l["items"]) for l in s["layers"]) for s in doc["states"]}
    print(f"red river view: {counts}  gate: {verdict['review_class']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
