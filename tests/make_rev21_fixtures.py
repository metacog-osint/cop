"""Write tests/fixtures/rev21/{old,new}/blackout.geojson from three items of Blackout's live feed (18 Nov 2026).

    python tests/make_rev21_fixtures.py <a saved copy of Blackout's feed of 18 Nov 2026> .

old: the items as Blackout sent them (contract 2, no revision 2.1 fields), header cut to the members COP reads.
new: the same items with the revision 2.1 fields Blackout would state for them, values matching what COP reads from
the old shape today (so a build reads both alike), and a 2.1 header (contract_rev, kit, documented health)."""
import copy
import json
import sys
from pathlib import Path

src, repo = Path(sys.argv[1]), Path(sys.argv[2])
feed = json.loads(src.read_text(encoding="utf-8"))
want = ["blackout:outage:nl:2026-11-18T02", "blackout:ooni:ir:2026-11-18",
        "blackout:radar-anomaly:c595977b-aa94-4659-bb59-45d1884015f0"]
by_id = {f["properties"]["id"]: f for f in feed["features"]}
items = [copy.deepcopy(by_id[i]) for i in want]
header = {k: feed[k] for k in ("type", "contract", "program", "generated_at", "mode", "language_key")}
header["licence"] = "Blackout code MIT; OONI data CC BY-NC-SA 4.0; IODA credited; Cloudflare Radar data CC BY-NC 4.0 (cut for a test fixture)"
header["attribution"] = "Blackout, The Intelligence Company (cut for a test fixture)."
lanes = ("ioda-gatech", "ooni-api", "cloudflare-radar")
header["health"] = {k: feed["health"][k] for k in lanes}

old = dict(header, features=items)

new_items = copy.deepcopy(items)
outage, ooni, radar = (f["properties"] for f in new_items)
outage.update({"subject_id": "blackout:outage:nl", "event_start": outage["data_as_of"], "last_change": "new",
               "last_change_at": "2026-11-18T02:28:00Z", "place_basis": "location"})
ooni.update({"subject_id": "blackout:ooni:ir", "event_start": "2026-11-12T00:00:00Z",
             "event_end": "2026-11-18T00:00:00Z", "event_time_precision": "day", "last_change": "none",
             "place_basis": "location",
             "signal": {"name": "ooni_anomaly_rate", "value": 0.36, "unit": "share", "tail": "high"}})
radar.update({"subject_id": radar["id"], "event_start": "2026-11-17T00:15:00Z", "last_change": "new",
              "last_change_at": "2026-11-17T00:15:00Z", "place_basis": "location"})
for p in (outage, ooni, radar):
    p["sources"][0]["lane"] = p["sources"][0]["id"]
    p["sea_areas"] = []                       # placed on land
health = {}
for k in lanes:
    h = feed["health"][k]
    health[k] = {"state": h.get("state", "fresh"), "checked_at": h["checked_at"], "last_ok": h["last_ok"],
                 "last_error": None, "failures": 0, "data_as_of": h["checked_at"], "detail": "fixture lane"}
new = dict(header, contract_rev="2.1", kit="0.6.0", health=health, features=new_items)

for name, doc in (("old", old), ("new", new)):
    out = repo / "tests" / "fixtures" / "rev21" / name / "blackout.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", out)
