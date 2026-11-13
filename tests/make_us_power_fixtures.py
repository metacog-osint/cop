"""Record the fixtures for US state outage triage (tests/test_triage_us_power.py). Run by hand; nothing here runs in
the build.

    gh release download snapshots-2026-11 --repo metacog-osint/cop --dir <archive> --pattern "*.geojson.gz"
    gh release download snapshots-2026-10 --repo metacog-osint/cop --dir <archive> --pattern "*.geojson.gz"
    python tests/make_us_power_fixtures.py --blackout D:/Projects/blackout --archive <archive>

Two real sources, nothing invented and no field edited:

1. Blackout's state outage items (BO-IND-14), made by Blackout's own code (blackout/odin.py, blackout/feed.py) from
   ODIN's responses Blackout recorded at 14:40 UTC 13 Nov 2026 (its tests/fixtures/odin). No state was over the
   50,000 line then (California, 13,723, was the most), so the line is lowered to 10,000 for the recording, as
   Blackout's own test does; every number in the items is ODIN's. Blackout attaches each state's EAGLE-I base rate.
2. COP's own archived snapshots (the last of each UTC day): the Elements items in or near the US from 13 Nov, a few
   Cordon and Watchfloor items from the same snapshot, every volcano item from every archived day, and the SPC risk
   areas from the days that had them.
"""

import argparse
import gzip
import importlib.util
import json
import sys
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "us_power"
ODIN_NOW = "2026-11-13T14:40:05Z"     # Blackout's tests/test_lanes.py ODIN_NOW: when its ODIN responses were recorded
LOWER_LINE = 10_000
DAY = "2026-11-13"


def blackout_items(repo):
    sys.path.insert(0, str(repo))
    from blackout import feed, odin  # noqa: E402
    spec = importlib.util.spec_from_file_location("blackout_cli", repo / "tools" / "blackout.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    folder = repo / "tests" / "fixtures" / "odin"
    by_url = {u: n for n, u in json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))["files"].items()}

    def fetch(url):
        return json.loads((folder / by_url[url]).read_text(encoding="utf-8"))

    now = datetime.fromisoformat(ODIN_NOW.replace("Z", "+00:00")).astimezone(timezone.utc)
    with unittest.mock.patch.object(odin, "STATE_THRESHOLD", LOWER_LINE):
        o, problems = odin.fetch_odin(fetch=fetch, now=now)
    assert not problems, problems
    snap = cli.build_snapshot({"mode": "live", "generated_at": ODIN_NOW, "raw_outages": [], "raw_ooni": [],
                               "raw_tips": [], "odin": o})
    fc = feed.build_feed(snap)
    items = [f for f in fc["features"] if f["properties"].get("indicator_id") == "BO-IND-14"]
    assert [f["properties"]["id"].split(":")[2] for f in items] == ["ca", "ny"], items
    return items


def load(p):
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def kind(item_id):
    parts = str(item_id).split(":")
    return parts[1] if len(parts) > 1 else ""


def us_related(f):
    p = f["properties"]
    cc = p.get("countries") or []
    if "US" in cc or "PR" in cc:
        return True
    g = f.get("geometry") or {}
    if not cc and g.get("type") == "Point":   # offshore: keep those off North America and Hawaii
        lon, lat = g["coordinates"][:2]
        return -180 <= lon <= -60 and 15 <= lat <= 72
    return False


def archive_parts(folder):
    files = sorted(Path(folder).glob("*.geojson.gz"))
    assert files, f"no archive in {folder}"
    volcano, spc, day = [], [], None
    for fp in files:
        snap = load(fp)
        feats = [f for f in snap["features"] if isinstance(f.get("properties"), dict)]
        health = {k: {"state": v.get("state")} for k, v in (snap.get("cop_programs") or {}).items()}
        v = [f for f in feats if f["properties"].get("cop_program") == "elements"
             and kind(f["properties"]["id"]) == "usgs-volcano"]
        volcano.append({"generated_at": snap["generated_at"], "cop_programs": health, "features": v})
        s = [f for f in feats if f["properties"].get("cop_program") == "elements" and kind(f["properties"]["id"]) == "spc"]
        if s:
            spc.append({"generated_at": snap["generated_at"], "features": s})
        if fp.name.startswith(DAY):
            el = [f for f in feats if f["properties"].get("cop_program") == "elements" and us_related(f)]
            co = [f for f in feats if f["properties"].get("cop_program") == "cordon"
                  and kind(f["properties"]["id"]) in ("advisory", "alert")][:2]
            wf = [f for f in feats if f["properties"].get("cop_program") == "watchfloor"][:2]
            day = {"generated_at": snap["generated_at"], "cop_programs": health, "features": el + co + wf}
    assert day, f"no {DAY} snapshot in the archive"
    return day, volcano, spc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--blackout", default="D:/Projects/blackout")
    ap.add_argument("--archive", required=True)
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    items = blackout_items(Path(a.blackout))
    day, volcano, spc = archive_parts(a.archive)
    write = lambda name, doc: (OUT / name).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
    write("blackout-us-power.json", {"type": "FeatureCollection", "features": items})
    write(f"cop-{DAY}-us.json", day)
    write("cop-volcano-days.json", volcano)
    write("cop-spc-days.json", spc)
    print(f"{len(items)} Blackout items; {len(day['features'])} items from {day['generated_at']}; "
          f"{len(volcano)} days of volcano items; {len(spc)} days with SPC risk areas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
