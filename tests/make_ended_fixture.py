"""Write tests/fixtures/us_power/elements-ended.json from a recorded Elements data/ended.geojson: whole features as
recorded, trimmed to the ones the tests need, and a line in that folder's MANIFEST.json. Nothing is edited.

    python tests/make_ended_fixture.py PATH_TO_RECORDED_ENDED_GEOJSON

The recording: Elements' build (branch history/ended-items) at 02:33 UTC 14 Nov 2026, from its history log rebuilt
from its own git history up to the data commit of 02:06 UTC (tools/history_backfill.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "us_power"
KEEP = ("elements:nws:KOKX.TO.W.0006.2026",    # Tornado Warning, New York, 12 Nov 18:14Z: ended 19:00Z
        "elements:nws:KBGM.WS.W.0004.2026",    # Winter Storm Warning, New York, 13 Nov 13:47Z: ended 18:00Z
        "elements:nws:KEKA.HW.W.0003.2026",    # High Wind Warning, California, 13 Nov 15:17Z: left the feed 17:36Z
        "elements:nws:PAFG.UP.W.2425.2026")    # still live in COP's 20:21Z snapshot: must not be added twice


def main(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = [f for f in doc["features"] if f["properties"]["id"] in KEEP]
    quake = next(f for f in doc["features"] if f["properties"]["id"].startswith("elements:emsc:"))
    feats.append(quake)                        # an ended item far from the US
    missing = set(KEEP) - {f["properties"]["id"] for f in feats}
    assert not missing, missing
    out = {k: v for k, v in doc.items() if k != "features"} | {"features": feats}
    (OUT / "elements-ended.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                             newline="\n")
    m = json.loads((OUT / "MANIFEST.json").read_text(encoding="utf-8"))
    m["files"]["elements-ended.json"] = (
        f"Elements' data/ended.geojson generated {doc['generated_at']} by its build (branch history/ended-items), from "
        f"its history log rebuilt from its git history to the data commit of 02:06 UTC 14 Nov; {len(doc['features'])} "
        f"items, trimmed to {len(feats)} whole features: "
        f"{', '.join(KEEP)} and {quake['properties']['id']} (made by tests/make_ended_fixture.py).")
    (OUT / "MANIFEST.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(feats)} features written")


if __name__ == "__main__":
    main(sys.argv[1])
