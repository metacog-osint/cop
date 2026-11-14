"""Write tests/fixtures/rebuild_ended/ from recorded data: whole lines of Elements' history log and the matching
features of the ended list Elements served on 14 Nov 2026. Nothing is edited.

    python tests/make_rebuild_fixture.py ELEMENTS_HISTORY_DIR SERVED_ENDED_JSON_GZ

ELEMENTS_HISTORY_DIR: Elements' history/ at commit 2e9a5e1 (log from 31 Oct 22:02 UTC to 14 Nov 03:36 UTC).
SERVED_ENDED_JSON_GZ: COP's archive copy of what Elements served, 2026-11-14.elements-ended.json.gz (release
snapshots-2026-11), generated 03:42:36 UTC 14 Nov."""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rebuild_ended as rb  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "rebuild_ended"
IDS = ("elements:nws:KOKX.TO.W.0006.2026",   # Tornado Warning, New York: new 12 Nov, ended 19:00Z
       "elements:nws:KBGM.WS.W.0004.2026",   # Winter Storm Warning, New York: new and ended 13 Nov
       "elements:spc:day1:slgt")             # SPC's Slight risk area: ends and comes back day after day


def main(history, served):
    lines = rb.read_log(history)
    keep = [x for x in lines if x["id"] in IDS]
    first = lines[0]                          # the log's first line: where the log starts
    if first not in keep:
        keep.insert(0, first)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "log.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in keep),
                                   encoding="utf-8", newline="\n")
    with gzip.open(served, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    feats = [f for f in doc["features"] if f["properties"]["id"] in IDS]
    (OUT / "served-2026-11-14.json").write_text(
        json.dumps({k: v for k, v in doc.items() if k != "features"} | {"features": feats}, indent=1,
                   ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "made_by": "tests/make_rebuild_fixture.py",
        "log.jsonl": f"{len(keep)} whole lines of Elements' history log (commit 2e9a5e1): every line for {list(IDS)}, "
                     "and the log's first line",
        "served-2026-11-14.json": f"the ended list Elements served, generated {doc['generated_at']} (COP release "
                                  f"snapshots-2026-11, 2026-11-14.elements-ended.json.gz), trimmed to {len(feats)} "
                                  "whole features for the same ids"}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(keep)} log lines, {len(feats)} served features")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
