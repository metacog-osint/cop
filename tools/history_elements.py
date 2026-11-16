"""Rebuild Elements' significant hazards for a past range, for the STOP replay (cop-layer-history/1).

Outside the US, Elements' significant hazards (tier anomaly) come from two sources whose past can be queried:
- GDACS events alerted orange or red (EQ, TC, FL, VO, DR, WF): `elements/feed.py` sets hot = alert in (orange, red).
  GDACS's event list API takes a date range and an alert level.
- USGS earthquakes of M4.5 or more with a PAGER alert of yellow or higher: Elements reads the M4.5 week feed and
  sets hot = PAGER rank >= 1. USGS's FDSN event service takes a date range and an alert level.

Not rebuilt (named in the manifest's gaps): NWS, SPC and USGS volcano alerts (US only), MeteoAlarm red warnings
(Europe; no public archive), tsunami messages (most follow a PAGER-alerted earthquake, which is rebuilt), CEMS and
EMSC (routine in Elements).

Each row is one hazard in one country: a country it names (GDACS's affected countries) or, for an earthquake, the
country it lies in or any country whose outline is within triage's HAZARD_NEAR_KM. `to_items` turns rows back into
the Elements items triage reads.

    python tools/history_elements.py --start 2024-11-28 --end 2025-12-31
Writes history/elements/<dataset_id>/manifest.json and rows.jsonl. Standard-library Python only.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage as tri  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
GDACS_TYPES = ("EQ", "TC", "FL", "VO", "DR", "WF")
PAGER_HOT = ("yellow", "orange", "red")
UA = "history-rebuild/1 (python urllib)"
SOURCES = [
    {"id": "gdacs", "name": "GDACS event list (European Commission JRC and UN OCHA)", "url": GDACS_URL,
     "licence": "GDACS data free to use with attribution (gdacs.org terms of use)",
     "attribution": "GDACS, European Commission Joint Research Centre and UN OCHA", "how": "api"},
    {"id": "usgs", "name": "USGS earthquake catalog (FDSN event service), PAGER alerts", "url": USGS_URL,
     "licence": "US Government work, public domain", "attribution": "U.S. Geological Survey", "how": "api"},
]


def _get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def _months(start, end):
    d = start
    while d <= end:
        nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield d, min(end, nxt - timedelta(days=1))
        d = nxt


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def places_for_point(lon, lat):
    """[(cc, km)] for a point: the country it lies in (km 0), else every country within HAZARD_NEAR_KM."""
    data = json.loads(tri.COUNTRIES.read_text(encoding="utf-8"))
    for c in data["countries"]:
        if any(_in_ring(lon, lat, r) for r in c["rings"]):
            return [(c["cc"], 0)]
    near = []
    for c in data["countries"]:
        km = tri.km_to_country({"type": "Point", "coordinates": [lon, lat]}, c["cc"])
        if km is not None and km <= tri.HAZARD_NEAR_KM:
            near.append((c["cc"], round(km)))
    return sorted(near, key=lambda x: x[1])


def _iso(s):
    t = tri.utc(s if s.endswith("Z") or "+" in s[10:] else s + "Z")
    return tri.iso(t) if t else None


def gdacs_rows(start, end, now):
    rows, seen = [], set()
    for a, b in _months(start, end):
        q = urllib.parse.urlencode({"eventlist": ";".join(GDACS_TYPES), "fromDate": a.isoformat(),
                                    "toDate": b.isoformat(), "alertlevel": "Orange;Red"}, safe=";")
        url = f"{GDACS_URL}?{q}"
        for f in (_get(url) or {}).get("features", []):
            p = f["properties"]
            key = (p["eventtype"], p["eventid"])
            if key in seen:
                continue
            seen.add(key)
            countries = sorted({c["iso2"] for c in p.get("affectedcountries") or [] if c.get("iso2")})
            quality = [] if countries else ["unplaced"]
            if not countries and f.get("geometry", {}).get("type") == "Point":
                countries = [cc for cc, _ in places_for_point(*f["geometry"]["coordinates"][:2])]
            for cc in countries or [None]:
                rows.append({"place": cc, "at": _iso(p["fromdate"]), "precision": "minute", "source_id": "gdacs",
                             "source_ref": url, "retrieved_at": now, "quality": quality,
                             "item_id": f"elements:gdacs:{p['eventtype']}-{p['eventid']}", "tier": "anomaly",
                             "hazard_code": p["eventtype"], "alert_level": p["alertlevel"].lower(),
                             "from": _iso(p["fromdate"]), "to": _iso(p["todate"]), "name": p.get("name"),
                             "severity": (p.get("severitydata") or {}).get("severitytext"),
                             "countries": countries, "geometry": f.get("geometry")})
        time.sleep(1)
    return rows


def usgs_rows(start, end, now):
    rows = []
    for level in PAGER_HOT:
        q = urllib.parse.urlencode({"format": "geojson", "starttime": start.isoformat(),
                                    "endtime": (end + timedelta(days=1)).isoformat(), "minmagnitude": 4.5,
                                    "alertlevel": level, "orderby": "time-asc"})
        url = f"{USGS_URL}?{q}"
        for f in (_get(url) or {}).get("features", []):
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"][:2]
            at = tri.iso(datetime.fromtimestamp(p["time"] / 1000, timezone.utc))
            places = places_for_point(lon, lat)
            for cc, km in places or [(None, None)]:
                rows.append({"place": cc, "at": at, "precision": "minute", "source_id": "usgs", "source_ref": url,
                             "retrieved_at": now, "quality": [] if cc else ["unplaced"],
                             "item_id": f"elements:usgs:{f['id']}", "tier": "anomaly", "hazard_code": "EQ",
                             "alert_level": level, "magnitude": p.get("mag"), "name": p.get("title"),
                             "km_from_country": km, "countries": [c for c, k in places if k == 0],
                             "geometry": {"type": "Point", "coordinates": [lon, lat]}})
        time.sleep(1)
    return rows


def to_items(rows):
    """Elements items (tic-feed/2 shape, as triage reads them) from rows, one per hazard."""
    items = {}
    for r in rows:
        if r["item_id"] in items:
            continue
        m = {"hazard_code": r["hazard_code"], "latest_alert": r["alert_level"]}
        if r.get("from"):
            m["from"] = r["from"]
        if r.get("to"):
            m["to"] = r["to"]
        items[r["item_id"]] = {"type": "Feature", "geometry": r.get("geometry"), "properties": {
            "id": r["item_id"], "title": r.get("name"), "tier": r["tier"], "data_as_of": r["at"],
            "countries": r.get("countries") or [], "cop_program": "elements", "cop_stale": False, "measures": m}}
    return list(items.values())


def manifest(rows, start, end, built_at, code_sha):
    days = (end - start).days + 1
    return {
        "contract": "cop-layer-history/1",
        "dataset_id": f"elements-hazards-{start}-{end}",
        "layer": "elements",
        "title": "Elements' significant hazards outside the US, rebuilt from GDACS and USGS PAGER",
        "description": "One row per hazard per country: GDACS events alerted orange or red, and USGS earthquakes of "
                       "M4.5+ with a PAGER alert of yellow or higher, as Elements would have carried them (tier "
                       "anomaly). For replaying triage's natural reading.",
        "sources": SOURCES,
        "derived_from": [],
        "method": {"code": "tools/history_elements.py", "commit": code_sha,
                   "params": {"gdacs_types": GDACS_TYPES, "gdacs_alerts": ["orange", "red"], "usgs_min_mag": 4.5,
                              "pager_alerts": PAGER_HOT, "near_km": tri.HAZARD_NEAR_KM},
                   "matches_live": False,
                   "differences": [
                       "GDACS's list gives each event's alert level as it stands now, not as it stood on each day: "
                       "an event raised to orange late counts from its start.",
                       "Elements reads GDACS and USGS live every 2 hours; an event GDACS or USGS revised or deleted "
                       "since is as it stands now.",
                       "Earthquakes are placed by the kit's thinned Natural Earth outlines (point in polygon), not by "
                       "Elements' own place lookup.",
                       "NWS, SPC, USGS volcano, MeteoAlarm, tsunami, CEMS and EMSC items are not rebuilt."]},
        "coverage": {"start": str(start), "end": str(end), "time_precision": "minute", "places": "all",
                     "place_code": "ISO 3166-1 alpha-2"},
        "gaps": [{"from": str(start), "to": str(end), "places": "Europe",
                  "reason": "MeteoAlarm red warnings have no public archive"},
                 {"from": str(start), "to": str(end), "places": "US",
                  "reason": "NWS Extreme alerts, SPC level 4-5 outlooks and USGS volcano alerts not rebuilt"},
                 {"from": str(start), "to": str(end), "places": "all",
                  "reason": "tsunami messages not rebuilt (most follow a PAGER-alerted earthquake, which is)"}],
        "biases": ["Low in Europe and the US, where Elements' other sources make most significant hazards.",
                   "GDACS alert levels are the latest, so a hazard that peaked later is counted early."],
        "rows": {"count": len(rows), "places": len({r["place"] for r in rows if r["place"]}), "days": days},
        "row_schema": {
            "place": "ISO alpha-2 of a country the hazard names or lies in or near (null: unplaced)",
            "at": "start of the hazard, UTC", "precision": "minute", "source_id": "gdacs or usgs",
            "source_ref": "the query URL that returned it", "retrieved_at": "UTC", "quality": "list of flags",
            "item_id": "the Elements item id it would have had", "tier": "anomaly",
            "hazard_code": "EQ, TC, FL, VO, DR, WF", "alert_level": "GDACS or PAGER level",
            "from": "GDACS event start", "to": "GDACS event end (episodic TC and FL count while active)",
            "magnitude": "USGS magnitude", "km_from_country": "0 inside; else km to the outline",
            "countries": "the countries the item names", "geometry": "GeoJSON point",
            "name": "title", "severity": "GDACS severity text"},
        "files": [],
        "redistribution": "rows",
        "built_at": built_at,
        "built_by": "tools/history_elements.py, COP STOP-replay session, 15-16 Nov 2026",
        "uses": ["replaying triage's natural reading outside the US for 2025",
                 "base rates of significant hazards per country-day"],
        "not_for": ["US or European hazard rates (gaps above)", "a record of GDACS alert levels by day"],
        "supersedes": None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default=str(ROOT / "history" / "elements"))
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    now = tri.iso(datetime.now(timezone.utc))
    rows = gdacs_rows(start, end, now) + usgs_rows(start, end, now)
    rows.sort(key=lambda r: (r["at"] or "", r["item_id"], r["place"] or ""))
    code_sha = "uncommitted sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    man = manifest(rows, start, end, now, code_sha)
    out = Path(a.out) / man["dataset_id"]
    out.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode("utf-8")
    (out / "rows.jsonl").write_bytes(body)
    man["files"] = [{"path_or_url": f"history/elements/{man['dataset_id']}/rows.jsonl",
                     "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}]
    (out / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    by = {}
    for r in rows:
        by[r["source_id"]] = by.get(r["source_id"], 0) + 1
    print(f"{len(rows)} rows ({by}), {len({r['item_id'] for r in rows})} hazards -> {out}")


if __name__ == "__main__":
    sys.exit(main())
