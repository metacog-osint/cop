"""COP's memory: what each subject did before, kept after it leaves the picture. Standard library only.

    python tools/memory.py --snapshot site/data/today.geojson --assets out/memory            one build
    python tools/memory.py --snapshot site/data/today.geojson --assets out/memory --first    no memory yet
    python tools/memory.py --replay out/rates-archive --assets out/memory                    rebuild from archived days

Tom, 21 Nov 2026: when an item drops off COP, COP keeps something to compare the next incoming item against. The suite
ledger (tools/ledger.py) already keeps every item in full, line by line, but nothing read it back. This file is the
compact, readable form: one record per subject (a subject_id where the feed states one, otherwise the item's id with
its date taken out), holding when it was on the picture, the days its events fell on, and its daily value where the
subject is a series. A record is never removed.

A series can change what it counts (Watchfloor's Iran zone went from two countries to nine on 18 Nov 2026). config/
series_scopes.json names the day each new scope starts. Every value of such a series is stamped with its scope in `vs`
and kept; current_series() hands readers only the newest scope, so a change of scope starts a new series.

An absence counts only when the program was read. A day a program's feed failed is not a gap in its subjects' runs.

Noisefloor's event counts come from its own full daily files (data/events/<day>.json), not from its feed: until 15 Nov
2026 the feed carried at most six events a day and after that about twenty, so counts taken from the feed show a surge
that never happened. The files have held about 17 Notable and 2 Major jamming events a day throughout.

Files, in --assets (the build keeps them as an asset of the release "memory", not in the repository or on the site):
    memory.json.gz

Not published. Anything drawn from it for the page goes through a join (tools/direction.py and tools/gate.py).
"""

import argparse
import gzip
import json
import re
import sys
import urllib.request
from datetime import date, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402

FORMAT = "cop-memory/1"
READ_STATES = ("fresh", "late")
NAME = "memory.json.gz"
SCOPES = ROOT / "config" / "series_scopes.json"
FIRST_SCOPE = "original"                     # a value from before a series' first listed change

# Ids that carry the day of the reading and state no subject_id: the same subject under a new id each day.
DATED_ID = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_]\d{2,4}Z?)?:?")
DATED_PREFIXES = ("watchfloor:", "context:", "vis:", "vigil:2", "wake:chokepoint:", "wake:port:")

# Subjects whose value is compared day by day: id prefix -> where the value is.
SERIES = {"watchfloor:zone:": "measures.violent_articles", "wake:chokepoint:": "measures.n_total_7d",
          "blackout:ooni:": "signal.value"}
NOISEFLOOR_EVENTS = "https://noisefloor.theintelligence.company/data/events/"
EVENT_LEVELS = {"major": 2, "notable": 1}    # Noisefloor's minor events are left out: too many to mean a change
EVENT_BACKFILL_DAYS = 45                     # how far back a build looks for daily files it does not hold yet
EVENT_REFRESH_DAYS = 2                       # the newest days are read again, since a day's file can still grow
SIMILAR_KM = 300                             # an item with a point and no country: "here" means within this
UA = "COP/1.0 (The Intelligence Company; https://cop.theintelligence.company)"


def day_of(value) -> str | None:
    text = str(value or "")[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _d(day: str) -> date:
    return date.fromisoformat(day)


def key(p: dict) -> str:
    """The subject an item belongs to."""
    if p.get("subject_id"):
        return str(p["subject_id"])
    item = str(p.get("id"))
    if item.startswith(DATED_PREFIXES):
        return DATED_ID.sub("", item).rstrip(":")
    return item


def continues(mem: dict, k: str) -> str | None:
    """The older record this subject continues, or None.

    When a feed starts stating subject_id (contract 2.1: Context and Vis on 24 Nov 2026), its subjects get new keys
    (context:zone:01_Ukraine_Conflict) while their history sits under the key their dated item ids gave before
    (context:01_Ukraine_Conflict). The old key is still what key() makes of the item's id, so it can be found again.
    Only a record of the same program, itself keyed from its id and begun no later, counts (ideas#205)."""
    r = mem["subjects"].get(k)
    if not r or not r.get("id"):
        return None
    old = key({"id": r["id"]})
    o = mem["subjects"].get(old)
    if old == k or not o or o.get("p") != r.get("p") or key({"id": o.get("id")}) != old or o["first"] > r["first"]:
        return None
    return old


def subject(mem: dict, k: str) -> dict | None:
    """A subject's record joined with the older record it continues. Read only: no stored record is changed."""
    r = mem["subjects"].get(k)
    old = continues(mem, k)
    if old is None:
        return r
    o = mem["subjects"][old]
    spans = []
    for s in sorted([list(x) for x in o["spans"] + r["spans"]]):
        if spans and (s[0] <= spans[-1][1] or not _was_read_between(mem, r["p"], spans[-1][1], s[0])):
            spans[-1][1] = max(spans[-1][1], s[1])
        else:
            spans.append(s)
    out = {**o, **r, "first": min(o["first"], r["first"]), "spans": spans,
           "at": sorted(set(o["at"]) | set(r["at"])), "was": old}
    for field in ("v", "vs"):
        if o.get(field) or r.get(field):
            out[field] = {**(o.get(field) or {}), **(r.get(field) or {})}
    if o.get("hot") or r.get("hot"):
        out["hot"] = max(o.get("hot") or "", r.get("hot") or "")
    return out


def joined_subjects(mem: dict) -> dict:
    """Every subject once: a record that a newer subject continues is read as part of it and not listed alone."""
    lineage = {k: old for k in mem["subjects"] if (old := continues(mem, k))}
    gone = set(lineage.values())
    return {k: (subject(mem, k) if k in lineage else r) for k, r in mem["subjects"].items() if k not in gone}


def family(item_key: str) -> str:
    """The kind of subject, for "the last similar item": cordon:alert, wake:ukmto, noisefloor:event."""
    parts = item_key.split(":")
    if len(parts) > 1:
        return ":".join(parts[:2])
    head = "-".join(item_key.split("-")[:2])
    return "noisefloor:event" if head == "noisefloor-event" else head   # ids before subject_id arrived, 18 Nov 2026


def _dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def series_value(p: dict):
    subject = key(p)
    for prefix, path in SERIES.items():
        if subject.startswith(prefix):
            v = _dig(p, path)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
    return None


def load_scopes() -> dict:
    """subject -> its scope changes, oldest first."""
    series = json.loads(SCOPES.read_text(encoding="utf-8"))["series"]
    return {k: sorted(v, key=lambda x: x["from"]) for k, v in series.items()}


def scope_of(changes: list, day: str) -> str:
    scope = FIRST_SCOPE
    for change in changes:
        if day >= change["from"]:
            scope = change["scope"]
    return scope


def stamp_scopes(mem: dict, scopes: dict | None = None) -> dict:
    """Stamp each value of a listed series with its scope. Adds `vs`; never changes or removes a value."""
    scopes = load_scopes() if scopes is None else scopes
    for k, changes in scopes.items():
        r = mem["subjects"].get(k)
        if r and r.get("v"):
            r["vs"] = {day: scope_of(changes, day) for day in r["v"]}
    return mem


def current_series(r: dict) -> dict:
    """day -> value, for the newest scope only. A series with no stamps is one scope."""
    values, stamps = r.get("v") or {}, r.get("vs")
    if not values or not stamps:
        return values
    newest = stamps[max(stamps)]
    return {d: v for d, v in values.items() if stamps.get(d) == newest}


def _coords(geometry) -> list:
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "GeometryCollection":
        return [c for g in geometry.get("geometries") or [] for c in _coords(g)]
    out = []

    def walk(node):
        if isinstance(node, (list, tuple)) and len(node) >= 2 and all(isinstance(x, (int, float)) for x in node[:2]):
            out.append(node[:2])
        elif isinstance(node, (list, tuple)):
            for x in node:
                walk(x)
    walk(geometry.get("coordinates"))
    return out


def place_point(geometry) -> list | None:
    """One point for a place: the mean of its coordinates, enough to test it against an area later."""
    pts = _coords(geometry)
    if not pts:
        return None
    return [round(sum(p[0] for p in pts) / len(pts), 3), round(sum(p[1] for p in pts) / len(pts), 3)]


def programs_read(snapshot: dict) -> set:
    return {pid for pid, v in (snapshot.get("cop_programs") or {}).items()
            if isinstance(v, dict) and v.get("state") in READ_STATES}


def empty() -> dict:
    return {"format": FORMAT, "updated_at": None, "days": {}, "subjects": {}, "events": {"noisefloor": {}}}


def _was_read_between(mem: dict, program: str, after: str, before: str) -> bool:
    """Was this program read on any day strictly between the two days?"""
    d, end = _d(after) + timedelta(days=1), _d(before)
    while d < end:
        if program in (mem["days"].get(d.isoformat()) or []):
            return True
        d += timedelta(days=1)
    return False


def fold(mem: dict, snapshot: dict) -> dict:
    """Add one snapshot. A snapshot no newer than the memory changes nothing."""
    at = snapshot.get("generated_at")
    today = day_of(at)
    if not today:
        raise SystemExit("memory: the snapshot has no generated_at")
    if mem.get("updated_at") and mem["updated_at"] >= at:
        return mem
    read = programs_read(snapshot)
    mem["days"][today] = sorted(set(mem["days"].get(today) or []) | read)
    for f in snapshot.get("features") or []:
        p = f.get("properties") or {}
        program = p.get("cop_program") or p.get("program")
        if program not in read or p.get("cop_stale") or not p.get("id"):
            continue
        k = key(p)
        r = mem["subjects"].get(k)
        if r is None:
            r = mem["subjects"][k] = {"p": program, "first": today, "spans": [[today, today]], "at": []}
        else:
            end = r["spans"][-1][1]
            if end != today:
                if _was_read_between(mem, program, end, today):
                    r["spans"].append([today, today])
                else:
                    r["spans"][-1][1] = today
        r["id"], r["t"] = p["id"], p.get("title")
        r["c"] = p.get("countries") or []
        point = place_point(f.get("geometry"))
        if point:
            r["xy"] = point
        if p.get("tier") in ("anomaly", "warning"):
            r["hot"] = today
        event = day_of(p.get("event_start")) or day_of(p.get("data_as_of"))
        if event and event not in r["at"]:
            r["at"] = sorted(r["at"] + [event])
        value = series_value(p)
        data_day = day_of(p.get("data_as_of"))
        if value is not None and data_day:
            r.setdefault("v", {})[data_day] = value
    mem["updated_at"] = at
    return stamp_scopes(mem)


def event_rows(doc: dict) -> list:
    """[[lat, lon, level]] for a Noisefloor daily file: its Major and Notable jamming events."""
    rows = []
    for x in (doc or {}).get("anomalies") or []:
        level = EVENT_LEVELS.get(x.get("tier")) if isinstance(x, dict) and x.get("m") == "jam" else None
        if level and isinstance(x.get("lat"), (int, float)) and isinstance(x.get("lon"), (int, float)):
            rows.append([round(x["lat"], 3), round(x["lon"], 3), level])
    return rows


def read_event_day(source: str, day: str) -> dict | None:
    """One Noisefloor daily file from a URL base or a folder, or None. A failed read keeps what the memory holds."""
    try:
        if source.startswith("http"):
            req = urllib.request.Request(f"{source}{day}.json", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        path = Path(source) / f"{day}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:  # noqa: BLE001 - a missing day is left for the next build
        return None


def ingest_events(mem: dict, today: str, source: str) -> int:
    """Add the daily event files the memory does not hold, and read the newest days again. Returns days read."""
    held = mem.setdefault("events", {}).setdefault("noisefloor", {})
    done = 0
    for back in range(1, EVENT_BACKFILL_DAYS + 1):
        day = (_d(today) - timedelta(days=back)).isoformat()
        if day in held and back > EVENT_REFRESH_DAYS:
            continue
        doc = read_event_day(source, day)
        if doc is not None:
            held[day] = event_rows(doc)
            done += 1
    return done


def similar_index(mem: dict) -> dict:
    """(family, country) -> records of that kind in that country; (family, None) -> those with a point and no country."""
    index = {}
    for k, r in joined_subjects(mem).items():
        if r.get("c"):
            for c in r["c"]:
                index.setdefault((family(k), c), []).append(r)
        elif r.get("xy"):
            index.setdefault((family(k), None), []).append(r)
    return index


def _km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (a[1], a[0], b[1], b[0]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 12742 * asin(min(1, sqrt(h)))


def _when(day: str) -> str:
    d = _d(day)
    return f"{d.day} {d:%b}"


def last_similar(mem: dict, k: str, r: dict | None, countries: list, xy, event: str, index: dict) -> str | None:
    """The newest day before this event on which another subject of the same kind fell in the same place."""
    fam = family(k)
    if countries:
        pool = [o for c in countries for o in index.get((fam, c), [])]
    elif xy:
        pool = [o for o in index.get((fam, None), []) if _km(xy, o["xy"]) <= SIMILAR_KM]
    else:
        pool = []
    best = None
    for o in pool:
        if o is r or (r is not None and o.get("id") == r.get("id")):   # r may be a joined copy of the same subject
            continue
        for day in reversed(o["at"]):
            if day < event:
                if best is None or day > best:
                    best = day
                break
    return best


def compare(mem: dict, p: dict, today: str, index: dict | None = None) -> dict:
    """What COP remembers about this item, against everything before it. A short line and its parts."""
    k = key(p)
    r = subject(mem, k)
    index = similar_index(mem) if index is None else index
    start = min(mem["days"]) if mem["days"] else today
    event = p.get("event_day") or day_of(p.get("event_start")) or day_of(p.get("data_as_of")) or (
        r["at"][-1] if r and r["at"] else today)
    xy = p.get("xy") or (r or {}).get("xy")
    prior = last_similar(mem, k, r, p.get("countries") or (r or {}).get("c") or [], xy, event, index)
    gap_before = (_d(event) - _d(prior)).days if prior else 0
    like = f" The last like it here was {_when(prior)}, {gap_before} day{'s' if gap_before != 1 else ''} earlier." if prior else ""
    if r is None or r["first"] == today:
        out = {"state": "new", "line": "New today." + (like or f" Nothing like it here since COP's memory began on "
                                                                f"{_when(start)}.")}
    elif len(r["spans"]) > 1 and r["spans"][-1][0] == today:
        last = r["spans"][-2][1]
        gap = (_d(today) - _d(last)).days
        out = {"state": "back", "last_seen": last, "gap_days": gap,
               "line": f"Back after {gap} days away. Last seen {_when(last)}."}
    else:
        days = (_d(today) - _d(r["spans"][-1][0])).days + 1
        text = f"On the picture {days} day{'s' if days != 1 else ''} running"
        out = {"state": "running", "days": days}
        values = sorted(current_series(r).items())
        if len(values) >= 2 and values[-2][1] != values[-1][1]:
            before, now = values[-2][1], values[-1][1]
            text += f", {'up' if now > before else 'down'} from {before:g} to {now:g}"
            out |= {"before": before, "now": now}
        out["line"] = text + "." + like
    if prior:
        out["last_similar"] = prior
    return out


# --- files ---------------------------------------------------------------------------------------------------------

def _folder(assets) -> Path:
    folder = Path(assets).resolve()
    for d in gate.DEPLOYED:
        if folder == (ROOT / d).resolve() or (ROOT / d).resolve() in folder.parents:
            raise SystemExit(f"memory: {folder} is deployed; the memory is not published")
    return folder


def load(folder) -> dict | None:
    path = Path(folder) / NAME
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        mem = json.load(fh)
    if mem.get("format") != FORMAT:
        raise SystemExit(f"memory: {path} is {mem.get('format')!r}, not {FORMAT}")
    return stamp_scopes(mem)


def save(folder, mem: dict) -> Path:
    path = Path(folder) / NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(mem, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
        fh.write(text.encode("utf-8"))
    return path


def _read(path) -> dict:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def replay(archive, into: dict | None = None) -> dict:
    """Fold the archived daily snapshots, oldest first."""
    files = sorted(Path(archive).glob("????-??-??.geojson.gz"))
    if not files:
        raise SystemExit(f"memory: no archived snapshots in {archive}")
    mem = into or empty()
    for path in files:
        fold(mem, _read(path))
    return mem


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", help="the snapshot just built")
    ap.add_argument("--assets", default=str(ROOT / "out" / "memory"), help="folder of memory.json.gz")
    ap.add_argument("--first", action="store_true", help="there is no memory yet: start from nothing")
    ap.add_argument("--replay", help="rebuild from this folder of archived daily snapshots")
    ap.add_argument("--events", default=NOISEFLOOR_EVENTS, help="Noisefloor's daily event files, a URL base or folder")
    a = ap.parse_args(argv)
    folder = _folder(a.assets)
    if a.replay:
        if load(folder) is not None:
            raise SystemExit(f"memory: {folder} already holds a memory; a replay starts from an empty folder")
        mem = replay(a.replay)
    else:
        if not a.snapshot:
            ap.error("--snapshot or --replay")
        mem = load(folder)
        if mem is None and not a.first:
            raise SystemExit(f"memory: no {NAME} in {folder}. Pass --first or --replay only if it has never run.")
        mem = mem or empty()
    if a.snapshot:
        fold(mem, _read(a.snapshot))
    if mem.get("updated_at"):
        ingest_events(mem, day_of(mem["updated_at"]), a.events)
    path = save(folder, mem)
    print(f"memory {mem['updated_at']}: {len(mem['subjects'])} subjects over {len(mem['days'])} days, "
          f"{path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
