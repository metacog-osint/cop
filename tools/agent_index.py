"""Build the small files COP's MCP server reads (site/data/agent/).

The server at /mcp (functions/mcp.js) runs on Cloudflare's free plan, which allows about 10 ms of CPU per request.
today.geojson is about 1.5 MB, and parsing it alone takes about that long, so this step, run right after
snapshot.py, writes small files the server reads instead:

    index.json      one compact row per bus item, naming the shard that holds the full item
    items/NN.json   the full contract-v2 items, in SHARDS files, keyed by id
    programs.json   every program in the suite registry (live and planned), with COP's health for each live one
    verdict.json    Verdict's forecasts, scores and event calendar; a part that fails to fetch keeps its last
                    published copy, marked stale
    events.json     dated events from the programs' own feeds, beside Verdict's calendar: Orbit's scheduled launches

    python tools/agent_index.py                                   # after snapshot.py; fetches Verdict
    python tools/agent_index.py --verdict tests/fixtures/verdict  # read Verdict's files from a folder (tests)

Standard-library Python only. Nothing here is private: every field is already public on COP or Verdict.
config/sources.json (with its commercial_ok notes) is never read; licences reach agents per item, from each item's
own sources[].licence.
"""

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)  # also puts the vendored kit on sys.path

CONTRACT = "tic-agent/1"
SHARDS = 32
INDEX_WARN_BYTES = 600 * 1024
VERDICT = "https://metacog-osint.github.io/verdict/data/"
VERDICT_FILES = {"forecasts": "forecasts.json", "scores": "scores.json", "calendar": "calendar.geojson"}
PREVIOUS = "https://cop.theintelligence.company/data/agent/verdict.json"
BUS_URL = "https://cop.theintelligence.company/data/today.geojson"


def fetch_json(url, timeout=30):
    """(any JSON document or None, fetch report). snapshot.fetch only accepts FeatureCollections."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": snap.UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        return json.loads(body), {"ok": True, "seconds": round(time.time() - t0, 2), "bytes": len(body)}
    except Exception as e:  # noqa: BLE001 - any failure is a failed fetch, reported, never fatal
        return None, {"ok": False, "seconds": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"[:200]}


def read_json(path):
    p = Path(path)
    if not p.exists():
        return None, {"ok": False, "seconds": 0, "error": f"missing {p.name}"}
    return json.loads(p.read_text(encoding="utf-8")), {"ok": True, "seconds": 0, "bytes": p.stat().st_size}


def _positions(coords):
    """Every [lon, lat] in a GeoJSON coordinates array, at any depth."""
    if isinstance(coords, (list, tuple)) and len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
        yield coords[0], coords[1]
    elif isinstance(coords, (list, tuple)):
        for c in coords:
            yield from _positions(c)


def place(geometry):
    """(point, bbox) for a geometry: a Point is its own point; anything larger gets the centre of its bounding box.
    Rounded to 3 decimals (about 100 m), which is finer than any item's own precision class."""
    if not isinstance(geometry, dict):
        return None, None
    pos = []
    if geometry.get("type") == "GeometryCollection":
        for g in geometry.get("geometries") or []:
            pos.extend(_positions(g.get("coordinates")))
    else:
        pos = list(_positions(geometry.get("coordinates")))
    if not pos:
        return None, None
    lons, lats = [p[0] for p in pos], [p[1] for p in pos]
    bbox = [round(min(lons), 3), round(min(lats), 3), round(max(lons), 3), round(max(lats), 3)]
    if geometry.get("type") == "Point":
        point = [round(pos[0][0], 3), round(pos[0][1], 3)]
    else:
        point = [round((bbox[0] + bbox[2]) / 2, 3), round((bbox[1] + bbox[3]) / 2, 3)]
    return point, bbox


FIELDS = ("id", "program", "kind", "tier", "status_class", "title", "data_as_of", "published_at", "valid_until",
          "countries", "point", "bbox", "link", "us_person_data", "stale", "shard")


def row(feature, shard):
    p = feature.get("properties") or {}
    point, bbox = place(feature.get("geometry"))
    if bbox and bbox[0] == bbox[2] and bbox[1] == bbox[3]:
        bbox = None  # a single point: the point says it all
    return {
        "id": p.get("id"),
        "program": p.get("cop_program") or p.get("program"),
        "kind": p.get("kind"),
        "tier": p.get("tier"),
        "status_class": p.get("status_class"),
        "title": p.get("title"),
        "data_as_of": p.get("data_as_of"),
        "published_at": p.get("published_at"),
        "valid_until": p.get("valid_until"),
        "countries": p.get("countries") or [],
        "point": point,
        "bbox": bbox,
        "link": p.get("link"),
        "us_person_data": p.get("us_person_data"),
        "stale": bool(p.get("cop_stale")),
        "shard": shard,
    }


def build_index(today, now):
    """(index document, list of shard documents) from COP's merged snapshot. The index is columnar (`fields` names
    the columns of each row in `rows`), which keeps it about a third smaller than one object per item."""
    feats = [f for f in today.get("features") or [] if (f.get("properties") or {}).get("id")]
    shards = [{"contract": CONTRACT, "shard": i, "items": {}} for i in range(SHARDS)]
    rows = []
    for n, f in enumerate(feats):
        s = n % SHARDS
        shards[s]["items"][f["properties"]["id"]] = f
        rows.append(row(f, s))
    programs = {}
    for pid, cp in (today.get("cop_programs") or {}).items():
        programs[pid] = {k: cp.get(k) for k in ("name", "state", "data_as_of", "attribution", "licence")}
    index = {
        "contract": CONTRACT,
        "generated_at": snap.iso(now),
        "bus_generated_at": today.get("generated_at"),
        "bus_url": BUS_URL,
        "language_key": today.get("language_key"),
        "shards": SHARDS,
        "programs": programs,
        "fields": list(FIELDS),
        "rows": [[r[k] for k in FIELDS] for r in rows],
    }
    return index, shards


def build_programs(registry, health, today, now):
    """Every program in the suite registry, in registry order, with COP's health for the ones COP carries."""
    by_id = {h["id"]: h for h in (health or {}).get("programs") or []}
    keep = ("id", "name", "status", "status_note", "family", "site", "feed", "terms", "fresh_hours", "stale_hours",
            "owns")
    out = []
    for p in registry.get("programs") or []:
        entry = {k: p[k] for k in keep if k in p}
        h = by_id.get(p["id"])
        entry["cop"] = None if h is None else {
            k: h.get(k) for k in ("state", "data_as_of", "age_hours", "items", "anomalies", "placed", "from_previous",
                                  "attribution", "licence")}
        out.append(entry)
    return {
        "contract": CONTRACT,
        "generated_at": snap.iso(now),
        "bus_generated_at": (today or {}).get("generated_at"),
        "bus_url": BUS_URL,
        "bus_refresh_hours": (registry.get("bus") or {}).get("refresh_hours"),
        "programs": out,
    }


def _forecasts(doc):
    return {"as_of": doc.get("as_of"), "items": doc.get("forecasts") or []}


def _calendar(doc):
    events = []
    for f in doc.get("features") or []:
        p = f.get("properties") or {}
        ev = p.get("event") or {}
        events.append({
            "id": p.get("id"),
            "title": p.get("title"),
            "date": ev.get("date"),
            "at": ev.get("at"),
            "kind": ev.get("kind"),
            "verified": ev.get("verified"),
            "occurred": ev.get("occurred"),
            "origin": ev.get("origin"),
            "ref": ev.get("ref"),
            "forecast_ids": ev.get("forecast_ids") or [],
            "note": ev.get("note"),
            "countries": p.get("countries") or [],
            "link": (p.get("visual") or {}).get("url"),
        })
    events.sort(key=lambda e: (e["date"] or "9999", e["id"] or ""))
    return {"generated_at": doc.get("generated_at"), "events": events}


SHAPERS = {"forecasts": _forecasts, "scores": lambda d: d, "calendar": _calendar}


def build_verdict(docs, reports, previous, now):
    """verdict.json. docs: part -> parsed document or None. A missing part reuses the previous copy, marked stale;
    with no previous copy it is null and listed under `missing`."""
    out = {
        "contract": CONTRACT,
        "generated_at": snap.iso(now),
        "source": VERDICT.rsplit("data/", 1)[0],
        "attribution": "Verdict, The Intelligence Company",
        "licence": "Verdict ledger (public); each forecast's sources as cited",
        "missing": [],
    }
    for part, shaper in SHAPERS.items():
        doc = docs.get(part)
        if doc is not None:
            out[part] = {"stale": False, "fetched_at": snap.iso(now), "data": shaper(doc)}
        elif previous and isinstance(previous.get(part), dict):
            out[part] = {**previous[part], "stale": True,
                         "error": (reports.get(part) or {}).get("error")}
        else:
            out[part] = None
            out["missing"].append(part)
    return out


def unreadable_parts(docs, reports):
    """A Verdict file that came but cannot be read (the wrong shape) counts as a failed fetch: its part becomes None
    here, so build_verdict keeps the last published copy, marked stale, as it does for a fetch that failed. Without
    this one malformed Verdict file stopped this step, and with it the build (15 Nov 2026). Returns the parts."""
    bad = []
    for part, shaper in SHAPERS.items():
        if docs.get(part) is None:
            continue
        try:
            shaper(docs[part])
        except Exception as e:  # noqa: BLE001
            docs[part] = None
            reports[part] = {"ok": False, "seconds": 0, "error": f"could not be read ({type(e).__name__}: {e})"[:200]}
            bad.append(part)
    return bad


# --- events.json: the programs' own dated events, for upcoming_events beside Verdict's calendar ---------------------
# Orbit's scheduled launches carry their time only in the title and summary ("..., 2026-11-14 17:32 UTC", "status Go
# for Launch; NET precision Minute"); the feed contract has no field for an event's time (checked 14 Nov 2026). An
# item's own `event` object, in Verdict's calendar shape, wins over the text if Orbit ever publishes one.
EVENT_PREFIXES = ("orbit:upcoming:",)
WHEN = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) UTC")
NET_PRECISION = re.compile(r"NET precision ([^;]+?)\)(?:\s|$|[.,;])")
LAUNCH_STATUS = re.compile(r"\(status ([^;()]+);")
TIMED = {"Second", "Minute", "Hour"}  # Launch Library 2 precisions where the time of day means something
DATED = ("Day", "Morning", "Afternoon", "Night")  # the day is known, the time is not
LAUNCH_NOTE = ("A scheduled time is the provider's or press's, as Launch Library 2 lists it, not Orbit's, and often "
               "moves.")


def orbit_event(p):
    """(event, None) for one Orbit scheduled launch, in Verdict's calendar shape plus the launch fields; or
    (None, reason) when its date cannot be read or is coarser than a day (a week, a month: a placeholder date)."""
    # Text that is not text reads as empty, so one odd item is skipped and counted, never a failed build (15 Nov 2026).
    title, summary = (v if isinstance(v, str) else "" for v in (p.get("title"), p.get("summary")))
    text = f"{title}\n{summary}"
    own = p.get("event") if isinstance(p.get("event"), dict) else {}
    m = WHEN.search(title) or WHEN.search(summary)
    pm = NET_PRECISION.search(text)
    precision = pm.group(1).strip() if pm else None
    sm = LAUNCH_STATUS.search(text)
    if isinstance(own.get("date"), str) and own["date"]:
        date, at = own["date"], own.get("at") if isinstance(own.get("at"), str) else None
    elif not m:
        return None, "no time in the title or summary"
    elif precision in TIMED:
        date, at = m.group(1), f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00Z"
    elif precision is None or precision.startswith(DATED):
        date, at = m.group(1), None
    else:
        return None, f"NET precision {precision}"
    srcs = p.get("sources") or []
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "date": date,
        "at": at,
        "kind": own.get("kind") or "launch",
        "verified": False,  # Launch Library 2 aggregates providers' and press reports
        "occurred": False,
        "origin": "orbit",
        "ref": None,
        "forecast_ids": [],
        "note": LAUNCH_NOTE,
        "net_precision": precision,
        "launch_status": sm.group(1).strip() if sm else None,
        "data_as_of": p.get("data_as_of"),
        "stale": bool(p.get("cop_stale")),
        "countries": p.get("countries") or [],
        "link": p.get("link") or (p["visual"].get("url") if isinstance(p.get("visual"), dict) else None),
        "source_url": srcs[0].get("url") if srcs else None,
    }, None


def build_events(today, now):
    """events.json from COP's merged snapshot. `skipped` counts items left out and why, so a change in Orbit's
    wording shows up as a number rather than as silence."""
    events, skipped = [], {}
    for f in today.get("features") or []:
        p = f.get("properties") or {}
        if not str(p.get("id") or "").startswith(EVENT_PREFIXES):
            continue
        ev, why = orbit_event(p)
        if ev:
            events.append(ev)
        else:
            skipped[why] = skipped.get(why, 0) + 1
    events.sort(key=lambda e: (e["date"], e["at"] or "", e["id"] or ""))
    cp = (today.get("cop_programs") or {}).get("orbit")
    return {
        "contract": CONTRACT,
        "generated_at": snap.iso(now),
        "bus_generated_at": today.get("generated_at"),
        "bus_url": BUS_URL,
        "programs": {"orbit": {k: cp.get(k) for k in ("name", "state", "data_as_of", "attribution", "licence")}}
        if cp else {},
        "skipped": skipped,
        "events": events,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "site" / "data"), help="where snapshot.py wrote today.geojson")
    ap.add_argument("--verdict", help="read Verdict's files from this folder instead of fetching")
    ap.add_argument("--previous", default=None, help=f"last published verdict.json, URL or file (default {PREVIOUS})")
    ap.add_argument("--now", help="pretend the time is this ISO UTC instant (tests)")
    a = ap.parse_args(argv)

    now = snap.utc(a.now) if a.now else datetime.now(timezone.utc).replace(microsecond=0)
    data = Path(a.data)
    today = json.loads((data / "today.geojson").read_text(encoding="utf-8"))
    health = json.loads((data / "health.json").read_text(encoding="utf-8"))
    registry = snap.kittips.registry()

    docs, reports = {}, {}
    for part, name in VERDICT_FILES.items():
        docs[part], reports[part] = read_json(Path(a.verdict) / name) if a.verdict else fetch_json(VERDICT + name)
    unreadable_parts(docs, reports)
    previous = None
    if any(d is None for d in docs.values()):
        where = a.previous if a.previous is not None else (None if a.verdict else PREVIOUS)
        if where:
            previous = (fetch_json(where)[0] if str(where).startswith("http") else read_json(where)[0])

    index, shards = build_index(today, now)
    out = data / "agent"
    snap.write_json(out / "index.json", index)
    for s in shards:
        snap.write_json(out / "items" / f"{s['shard']:02d}.json", s)
    snap.write_json(out / "programs.json", build_programs(registry, health, today, now))
    verdict = build_verdict(docs, reports, previous, now)
    snap.write_json(out / "verdict.json", verdict)
    events = build_events(today, now)
    snap.write_json(out / "events.json", events)

    size = (out / "index.json").stat().st_size
    # The free plan allows 10 ms of CPU per request. Measured 6 Nov 2026: a 278 KB index cost 2.1 ms on a cold
    # request (parse plus two searches), so 600 KB is about 5 ms, half the limit. Past that, slim the rows.
    if size > INDEX_WARN_BYTES:  # an annotation on the Actions run, never a failure
        print(f"::warning::agent index.json is {size // 1024} KB, over {INDEX_WARN_BYTES // 1024} KB; "
              "the MCP server's cold requests are nearing half the free plan's 10 ms CPU limit")
    print(f"agent index: {len(index['rows'])} items, {size // 1024} KB, {SHARDS} shards; "
          f"verdict: {', '.join(p + ('' if verdict[p] and not verdict[p].get('stale') else ' (stale)' if verdict[p] else ' (missing)') for p in VERDICT_FILES)}; "
          f"program events: {len(events['events'])}"
          + (f", skipped {events['skipped']}" if events["skipped"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
