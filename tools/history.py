"""History across programs: what every layer showed for a place and day (cop-history/1).

    python tools/history.py --archive out/history-in/snapshots --ledger out/history-in/ledger \
        --logs out/history-in/logs --publish site/data/history

Reads three kinds of source, all left as they are (this set is derived and can be rebuilt from them at any time):
- COP's daily snapshots (release snapshots-YYYY-MM, <day>.geojson.gz, the last build of each UTC day): every item each
  program's feed carried that day. One row per item, day and country: the table `readings`.
- COP's suite ledger (release ledger, <day>.jsonl.gz, tic-history/1) and each program's own log (history/log in its
  repository, the same format, config/history.json): every item new, changed or ended, with its time. The table
  `events`. Each change is counted once: a program with its own log has its events from that log from the log's
  first day, and from COP's ledger only before it (the ledger records the same change again at COP's build).
- config/areas.json: the warning problems and Watchfloor's conflict zones, so each row also names the areas it falls
  in (the `zones` column), by the same rules as the direction of concern (tools/direction.py in_area, with Blackout's
  country outages placed by country). A worldwide problem holds only the items of its own layers.

Keys on every row: date (UTC day), country (ISO 3166-1 alpha-2, empty when the item names none), zones, subject_id.
subject_basis says whether the feed sent the subject_id (feed contract 2.1) or it was derived here from the item id with
its date removed ("derived"), so a reader can see where the feeds do not send one yet.

Id churn (the ledger's main noise): a subject that ends and comes back new at the same build, because its feed gave it
a new id, is not a change. Such lines are kept with churn = true, and readers leave them out unless asked.

Comparability: each row keeps its program's method_version, and `note` says when the row cannot be compared with rows
outside its own period: a change of Watchfloor's zone scope, and the named breaks in config/history.json. A change of
method version is not marked as a break by itself (most versions change wording, not the measurement): compare
within one method_version, which the manifest lists per program with its dates. A row with a note is still a true record of what was shown.

What is published: rows from the programs' own feed items only, as COP's picture showed them. Files in a program's
history/ folder other than its log (lanes outside its feed) are never read. Summaries and source lists are left out.

Output in --publish (a join through tools/gate.py; site/data/history):
    readings-YYYY-MM.parquet, events-YYYY-MM.parquet   the Parquet set, one file per table and month (needs pyarrow)
    manifest.json                                      what was read, per program: days, method versions, fields
                                                       missing from the feeds, breaks; the Parquet files and their size
    place/<KEY>/<monday>.json                          per country (ISO code) or area id and week (Monday to
                                                       Sunday): runs of unchanged readings and the events, for the
                                                       MCP server's history tool
    areas/<area>.json                                  per area, per program, per day: items and anomalies, for the
                                                       place pages
"""

import argparse
import gzip
import json
import re
import sys
import time
from datetime import date, timedelta
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direction as dr  # noqa: E402
import gate  # noqa: E402

ROOT = gate.ROOT
FORMAT = "cop-history/1"
CONFIG = ROOT / "config" / "history.json"
AREAS = ROOT / "config" / "areas.json"
JOIN = {"id": "history", "version": "0.1", "published": True, "inputs": []}   # inputs: every program, set in build()
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(geojson|jsonl)\.gz$")
DATED = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_]\d{2}(?::?\d{2})?(?::?\d{2})?Z?)?")
READ_STATES = ("fresh", "late")
READING_COLS = ["date", "program", "subject_id", "subject_basis", "item_id", "country", "zones", "kind", "tier",
                "status_class", "title", "value", "value_text", "method_version", "scope", "data_as_of", "stale",
                "link", "note", "source"]
EVENT_COLS = ["date", "at", "program", "lane", "subject_id", "item_id", "change", "tier", "title", "method_version",
              "country", "zones", "churn", "note", "source"]
RUN_COLS = ["program", "subject_id", "item_id", "from", "to", "days", "kind", "tier", "value", "value_text", "title",
            "method_version", "note", "link"]
PLACE_EVENT_COLS = ["at", "program", "subject_id", "item_id", "change", "tier", "title", "method_version", "churn",
                    "note", "source"]
FIELDS_WATCHED = ("subject_id", "countries", "measures", "method_version", "valid_until")


def short_version(v):
    return str(v).split(" (")[0].strip() if v else None


def subject_of(p, item_id):
    """(subject_id, basis): the feed's own subject_id (contract 2.1), else the item id with its dates removed."""
    s = p.get("subject_id")
    if s:
        return str(s), "feed"
    return re.sub(r"::+", ":", DATED.sub("", str(item_id or ""))).strip(":-") or str(item_id), "derived"


def point_of(geom):
    """A representative [lon, lat]: the point, or the middle of the shape's bounding box."""
    if not isinstance(geom, dict):
        return None
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
            xs.append(c[0]), ys.append(c[1])
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    walk(geom.get("coordinates"))
    for g in geom.get("geometries") or []:
        walk(g.get("coordinates"))
    if not xs:
        return None
    return [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]


def load_areas():
    cfg = json.loads(AREAS.read_text(encoding="utf-8"))
    return list(cfg.get("problems") or []) + list(cfg.get("areas") or [])


_ZONES = {}


def zones_for(subject, item_id, countries, geom, areas):
    """zones_of, kept per item: an item carried for many days is placed once."""
    key = (subject, item_id, tuple(countries), json.dumps(geom, sort_keys=True)[:200] if geom else None)
    if key not in _ZONES:
        _ZONES[key] = ",".join(zones_of(subject, item_id, countries, point_of(geom), areas))
    return _ZONES[key]


def _in_layer(subject, item_id, r, layer):
    return dr.in_family(str(subject), r, layer) or dr.in_family(str(item_id), r, layer)


def zones_of(subject, item_id, countries, xy, areas):
    """The areas a row falls in: by direction's rules (circle or countries), or by naming the area's press zone.

    Direction's rules in full: a Blackout country outage is placed by the countries it names, as on the direction board,
    not by its point. A worldwide problem holds only the items of its own layers (direction in_family; layers that read
    an item's title, such as notices, are not used by any worldwide problem): every item of every program is somewhere
    in the world, so the area alone says nothing about which ones belong to it."""
    out = []
    r = {"c": list(countries), "xy": xy}
    text = f"{subject} {item_id}"
    layer = "outages" if _in_layer(subject, item_id, r, "outages") else None
    for a in areas:
        if a.get("worldwide"):
            hit = any(_in_layer(subject, item_id, r, lay) for lay in a.get("layers") or [])
        else:
            hit = dr.in_area(subject, r, a, layer)
        if not hit and a.get("press_zone") and a["press_zone"] in text:
            hit = True
        if hit:
            out.append(a["area"])
    return out


def load_breaks():
    return json.loads(CONFIG.read_text(encoding="utf-8")).get("breaks") or []


def break_note(row, breaks, measures=None, backfill=None):
    for b in breaks:
        if b.get("program") and b["program"] != row["program"]:
            continue
        if b.get("source") and b["source"] != row["source"]:
            continue
        if b.get("subject_contains") and b["subject_contains"] not in f"{row['subject_id']} {row['item_id']}":
            continue
        if b.get("before") and not row["date"] < b["before"]:
            continue
        if b.get("missing_measure") and (measures is None or b["missing_measure"] in measures):
            continue
        if b.get("backfill") and not backfill:
            continue
        return b["note"]
    return None


def _open(path):
    return gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else open(path, encoding="utf-8")


# --- readings: the daily snapshots ----------------------------------------------------------------------------------

def snapshot_files(archive):
    out = []
    for f in sorted(Path(archive).glob("*.geojson.gz")):
        m = DAY_FILE.match(f.name)
        if m:
            out.append((m.group(1), f))
    return out


def readings_of(day, doc, areas, breaks, stats):
    rows = []
    read = {k for k, v in (doc.get("cop_programs") or {}).items() if isinstance(v, dict) and v.get("state") in READ_STATES}
    for f in doc.get("features") or []:
        p = f.get("properties") or {}
        program = p.get("program") or p.get("cop_program")
        if not program or not p.get("id"):
            continue
        subject, basis = subject_of(p, p["id"])
        countries = [c for c in (p.get("countries") or []) if isinstance(c, str) and len(c) == 2]
        zones = zones_for(subject, p["id"], countries, f.get("geometry"), areas)
        measures = p.get("measures") if isinstance(p.get("measures"), dict) else None
        st = stats[program]
        st["rows"] += 1
        for k in FIELDS_WATCHED:
            if p.get(k) in (None, "", []):
                st["missing"][k] += 1
        base = {"date": day, "program": program, "subject_id": subject, "subject_basis": basis, "item_id": p["id"],
                "zones": zones, "kind": p.get("kind"), "tier": p.get("tier"), "status_class": p.get("status_class"),
                "title": p.get("title"), "value": _num(p.get("cop_strength")), "value_text": p.get("cop_strength_text"),
                "method_version": short_version(p.get("method_version")),
                "scope": (measures or {}).get("zone_scope"), "data_as_of": p.get("data_as_of"),
                "stale": bool(p.get("cop_stale")), "link": p.get("link"), "note": None, "source": "cop-snapshot"}
        base["note"] = break_note(base, breaks, measures)
        for c in countries or [""]:
            rows.append(dict(base, country=c))
    return rows, read


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def mark_method_changes(rows):
    """Rows of a subject read under a Watchfloor zone scope other than its newest get a note. A change of method
    version alone is not marked: every row carries its version, and readers compare within one."""
    newest = {}
    for r in rows:
        k = (r["program"], r["subject_id"])
        if k not in newest or r["date"] >= newest[k][0]:
            newest[k] = (r["date"], r["method_version"], r.get("scope"))
    for r in rows:
        if r["note"]:
            continue
        _, mv, scope = newest[(r["program"], r["subject_id"])]
        if scope and r.get("scope") and r["scope"] != scope:
            r["note"] = "Watchfloor zone scope changed since this row: compare within one scope"


# --- events: the ledger and the programs' own logs ------------------------------------------------------------------

def log_files(folder):
    """Every tic-history/1 day file under folder: <day>.jsonl.gz, or log/YYYY/MM/DD.jsonl(.gz)."""
    out = []
    for f in sorted(Path(folder).rglob("*.jsonl*")):
        if not f.name.endswith((".jsonl", ".jsonl.gz")):
            continue
        m = DAY_FILE.match(f.name)
        if m:
            day = m.group(1)
        else:
            parts = f.parts[-3:]
            try:
                day = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2].split('.')[0]):02d}"
            except ValueError:
                continue
        if f.name.endswith(".jsonl") and Path(str(f) + ".gz").exists():
            continue    # a closed day is kept once, as .gz
        out.append((day, f))
    return out


def events_of(folder, source, areas, breaks, program=None):
    rows = []
    for day, f in log_files(folder):
        with _open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ln = json.loads(line)
                except ValueError:
                    continue
                if ln.get("format") not in (None, "tic-history/1") or not ln.get("change"):
                    continue
                item = ln.get("item") if isinstance(ln.get("item"), dict) else {}
                p = item.get("properties") if isinstance(item.get("properties"), dict) else item
                item_id = ln.get("id") or p.get("id")
                prog = ln.get("program") or p.get("program") or program
                if not item_id or not prog:
                    continue
                subject = ln.get("subject_id") or subject_of(p, item_id)[0]
                countries = [c for c in (p.get("countries") or []) if isinstance(c, str) and len(c) == 2]
                base = {"date": str(ln.get("at") or day)[:10], "at": ln.get("at"), "program": prog,
                        "lane": ln.get("lane"), "subject_id": subject, "item_id": item_id, "change": ln["change"],
                        "tier": p.get("tier") or ln.get("peak_tier"), "title": p.get("title"),
                        "method_version": short_version(p.get("method_version")),
                        "zones": zones_for(subject, item_id, countries, item.get("geometry"), areas),
                        "churn": False, "source": source}
                base["note"] = break_note(base, breaks, p.get("measures") if isinstance(p.get("measures"), dict)
                                          else None, ln.get("backfill"))
                for c in countries or [""]:
                    rows.append(dict(base, country=c))
    mark_churn(rows)
    return rows


def mark_churn(rows):
    """A subject that ends and is new again at the same time is id churn, not a change."""
    seen = defaultdict(set)
    for r in rows:
        seen[(r["source"], r["program"], r["subject_id"], r["at"])].add(r["change"])
    for r in rows:
        if {"new", "ended"} <= seen[(r["source"], r["program"], r["subject_id"], r["at"])]:
            r["churn"] = True


# --- place files and area series ------------------------------------------------------------------------------------

def runs_of(rows, read_days):
    """Collapse each subject's daily rows for one place into runs of the same reading on consecutive days read."""
    out = []
    by = defaultdict(list)
    for r in rows:
        by[(r["program"], r["subject_id"])].append(r)
    for (program, subject), rs in by.items():
        rs.sort(key=lambda r: r["date"])
        days = read_days.get(program) or sorted({r["date"] for r in rs})
        pos = {d: i for i, d in enumerate(days)}
        cur = None
        for r in rs:
            same = (r["tier"], r["value_text"], r["title"], r["method_version"], r["note"])
            if cur and cur["same"] == same and pos.get(r["date"], -9) == pos.get(cur["to"], -99) + 1:
                cur["to"], cur["days"], cur["item_id"], cur["link"] = r["date"], cur["days"] + 1, r["item_id"], r["link"]
                continue
            cur = {"same": same, "program": program, "subject_id": subject, "item_id": r["item_id"], "from": r["date"],
                   "to": r["date"], "days": 1, "kind": r["kind"], "tier": r["tier"], "value": r["value"],
                   "value_text": r["value_text"], "title": r["title"], "method_version": r["method_version"],
                   "note": r["note"], "link": r["link"]}
            out.append(cur)
    out.sort(key=lambda x: (x["from"], x["program"], x["subject_id"]))
    return [[x[c] for c in RUN_COLS] for x in out]


def _dedupe_place(rows):
    """One row per subject and day within a place (an item listing two countries of one area counts once)."""
    seen, out = set(), []
    for r in rows:
        k = (r["date"], r["program"], r["subject_id"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def area_series(rows, days, read_days, area):
    """Per program and day: items and anomalies in the area, whether the program was read, and any note."""
    layers = {}
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r["program"]][r["date"]].append(r)
    for program in sorted(set(by) | set(read_days)):
        read = set(read_days.get(program) or [])
        items, anomalies, notes, was_read = [], [], [], []
        for d in days:
            rs = by[program].get(d) or []
            ok = d in read or bool(rs)
            was_read.append(ok)
            items.append(len(rs) if ok else None)
            anomalies.append(sum(1 for r in rs if r["tier"] in ("anomaly", "warning")) if ok else None)
            notes.append(next((r["note"] for r in rs if r["note"]), None))
        if any(items):
            layers[program] = {"items": items, "anomalies": anomalies, "read": was_read, "notes": notes,
                               "method_versions": sorted({r["method_version"] for rs in by[program].values()
                                                          for r in rs if r["method_version"]})}
    return {"format": FORMAT, "join": dict(JOIN), "area": area["area"], "name": area.get("name"),
            "countries": area.get("countries") or [], "days": days, "layers": layers}


# --- the build ------------------------------------------------------------------------------------------------------

def collect(archive, ledger=None, logs=None, config=None):
    areas = load_areas()
    breaks = load_breaks()
    stats = defaultdict(lambda: {"rows": 0, "missing": defaultdict(int)})
    readings, read_days = [], defaultdict(list)
    for day, f in snapshot_files(archive):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"history: {f.name} unreadable ({e}); that day is missing", file=sys.stderr)
            continue
        rows, read = readings_of(day, doc, areas, breaks, stats)
        readings.extend(rows)
        for prog in read:
            read_days[prog].append(day)
    mark_method_changes(readings)
    events = []
    logs_read, log_from = {}, {}
    for program in sorted((config or {}).get("program_logs") or {}):
        folder = Path(logs or "") / program
        if logs and folder.is_dir():
            rows = events_of(folder, f"program-log:{program}", areas, breaks, program)
            events.extend(rows)
            logs_read[program] = len(rows)
            days = [day for day, _ in log_files(folder)]
            if days:
                log_from[program] = min(days)
        else:
            logs_read[program] = None
    if ledger and Path(ledger).is_dir():
        events.extend(ledger_events(events_of(ledger, "cop-ledger", areas, breaks), log_from))
    return {"areas": areas, "readings": readings, "events": events, "read_days": dict(read_days), "stats": stats,
            "logs_read": logs_read, "log_from": log_from}


def ledger_events(rows, log_from):
    """COP's ledger lines, less those a program's own log already holds. COP's ledger records the same change as the
    program's log, minutes later, at COP's build: kept both, one change would count twice. So a program with its own
    log has its events from that log from the log's first day, and from COP's ledger only for the days before it."""
    return [r for r in rows if r["program"] not in log_from or r["date"] < log_from[r["program"]]]


def week_of(day):
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def months_of(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["date"][:7]].append(r)
    return by


def write_parquet(rows, cols, path):
    """Write rows as a zstd Parquet file; False when pyarrow is not installed."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False
    types = {"value": pa.float64(), "stale": pa.bool_(), "churn": pa.bool_()}
    schema = pa.schema([(c, types.get(c, pa.string())) for c in cols])
    table = pa.Table.from_pylist([{c: r.get(c) for c in cols} for r in rows], schema=schema)
    pq.write_table(table, path, compression="zstd")
    return True


def manifest(data, files, built_at):
    readings, events = data["readings"], data["events"]
    programs = {}
    for prog in sorted({r["program"] for r in readings} | {e["program"] for e in events}):
        rs = [r for r in readings if r["program"] == prog]
        versions = {}
        for r in rs:
            v = versions.setdefault(r["method_version"] or "unstated", [r["date"], r["date"]])
            v[0], v[1] = min(v[0], r["date"]), max(v[1], r["date"])
        st = data["stats"].get(prog) or {"rows": 0, "missing": {}}
        ev = [e for e in events if e["program"] == prog]
        programs[prog] = {
            "days_read": len(data["read_days"].get(prog) or []),
            "first_day": min((r["date"] for r in rs), default=None),
            "last_day": max((r["date"] for r in rs), default=None),
            "method_versions": [{"version": k, "from": v[0], "to": v[1]} for k, v in sorted(versions.items(), key=lambda kv: kv[1])],
            "subject_id_from_feed": round(sum(1 for r in rs if r["subject_basis"] == "feed") / len(rs), 3) if rs else None,
            "fields_missing_share": {k: round(st["missing"].get(k, 0) / st["rows"], 3) for k in FIELDS_WATCHED
                                     if st["rows"] and st["missing"].get(k)},
            "notes": sorted({r["note"] for r in rs if r["note"]} | {e["note"] for e in ev if e["note"]}),
            "events": len(ev), "churn_events": sum(1 for e in ev if e["churn"]),
            "own_log": data["logs_read"].get(prog, "not kept") if prog in data["logs_read"] else "not kept",
            "events_from": (f"its own log from {data['log_from'][prog]}, COP's ledger before that"
                            if prog in data.get("log_from", {}) else "COP's ledger"),
        }
    days = sorted({r["date"] for r in readings})
    return {"format": FORMAT, "join": dict(JOIN), "built_at": built_at,
            "what": "What every layer of COP showed, per day, country, area and subject, from COP's daily snapshots, "
                    "its ledger and the programs' own logs. Derived: the sources are unchanged and the set can be "
                    "rebuilt from them.",
            "keys": ["date", "country", "zones", "subject_id"],
            "first_day": days[0] if days else None, "last_day": days[-1] if days else None, "days": len(days),
            "read_days": data["read_days"],
            "churn_rule": "a subject that ends and is new again at the same time is id churn (churn = true); "
                          "readers leave those lines out unless asked",
            "comparability_rule": "a row with a note cannot be compared with rows outside its own period (a change of "
                                  "zone scope or a named break); compare rows within one method_version",
            "areas": [{"area": a["area"], "name": a.get("name")} for a in data["areas"]],
            "programs": programs, "files": files,
            "rows": {"readings": len(readings), "events": len(events),
                     "churn_events": sum(1 for e in events if e["churn"])}}


def build(archive, publish, ledger=None, logs=None, now=None, before=None):
    t0 = time.time()
    JOIN["inputs"] = sorted(p for p in gate.programs() if p not in gate.READ_NOT_DRAWN)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    data = collect(archive, ledger, logs, config)
    if before:    # closed days only: the day still being built is left for the next night's set
        data["readings"] = [r for r in data["readings"] if r["date"] < before]
        data["events"] = [e for e in data["events"] if e["date"] < before]
        data["read_days"] = {k: [d for d in v if d < before] for k, v in data["read_days"].items()}
    if not data["readings"]:
        raise SystemExit("history: no snapshot was read; nothing written")
    ids = {r["item_id"] for r in data["readings"]} | {e["item_id"] for e in data["events"]}
    verdict = gate.review(JOIN, {"cited": [{"id": i} for i in sorted(ids)]})
    refusal = gate.allowed(verdict, publish)
    if refusal:
        raise SystemExit(f"history: {refusal}. " + "; ".join(verdict["reasons"]))
    out = Path(publish)
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("*.parquet")) + list(out.glob("place/*/*.json")) + list(out.glob("areas/*.json")):  # noqa
        old.unlink()
    files = []
    for table, rows, cols in (("readings", data["readings"], READING_COLS), ("events", data["events"], EVENT_COLS)):
        for month, rs in sorted(months_of(rows).items()):
            path = out / f"{table}-{month}.parquet"
            if write_parquet(rs, cols, path):
                files.append({"name": path.name, "rows": len(rs), "bytes": path.stat().st_size})
    if not files:
        print("history: pyarrow is not installed; the Parquet files were not written", file=sys.stderr)
    # Per place: every country and area, per month.
    places = defaultdict(lambda: defaultdict(lambda: {"r": [], "e": []}))
    for r in data["readings"]:
        keys = ([r["country"]] if r["country"] else []) + [z for z in r["zones"].split(",") if z]
        for k in keys:
            places[k][week_of(r["date"])]["r"].append(r)
    for e in data["events"]:
        keys = ([e["country"]] if e["country"] else []) + [z for z in e["zones"].split(",") if z]
        for k in keys:
            places[k][week_of(e["date"])]["e"].append(e)
    stamp = {"review_class": verdict["review_class"], "may_publish": verdict["may_publish"]}
    n_place = 0
    for key, months in places.items():
        folder = out / "place" / key
        folder.mkdir(parents=True, exist_ok=True)
        for week, part in months.items():
            seen, evs = set(), []
            for e in sorted(part["e"], key=lambda e: (e["at"] or "", e["program"], e["subject_id"])):
                k = (e["at"], e["source"], e["program"], e["subject_id"], e["change"])
                if k not in seen:
                    seen.add(k)
                    evs.append([e[c] for c in PLACE_EVENT_COLS])
            doc = {"format": "cop-history-place/1", "join": dict(JOIN), "gate": stamp, "place": key, "week": week,
                   "runs": {"cols": RUN_COLS, "rows": runs_of(_dedupe_place(part["r"]), data["read_days"])},
                   "events": {"cols": PLACE_EVENT_COLS, "rows": evs}}
            (folder / f"{week}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                                                 encoding="utf-8", newline="\n")
            n_place += 1
    days = sorted({r["date"] for r in data["readings"]})
    (out / "areas").mkdir(exist_ok=True)
    for a in data["areas"]:
        rows = _dedupe_place([r for r in data["readings"] if a["area"] in r["zones"].split(",")])
        doc = area_series(rows, days, data["read_days"], a)
        doc["gate"] = stamp
        (out / "areas" / f"{a['area']}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                                                         encoding="utf-8", newline="\n")
    built_at = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc = manifest(data, files, built_at)
    doc["place_files"] = n_place
    doc["build_seconds"] = round(time.time() - t0, 1)
    gate.write(JOIN, doc, out, ("manifest.json",))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", required=True, help="folder of COP's daily snapshots (<day>.geojson.gz)")
    ap.add_argument("--ledger", help="folder of the ledger's day files (<day>.jsonl.gz)")
    ap.add_argument("--logs", help="folder holding <program>/ copies of each program's history/log")
    ap.add_argument("--publish", default=str(ROOT / "site" / "data" / "history"))
    ap.add_argument("--before", help="leave out this UTC day and later (the build passes today: closed days only)")
    ap.add_argument("--now", help="build time to stamp (tests)")
    a = ap.parse_args(argv)
    doc = build(a.archive, a.publish, a.ledger, a.logs, a.now, a.before)
    size = sum(f["bytes"] for f in doc["files"])
    print(f"history: {doc['days']} days {doc['first_day']}..{doc['last_day']}, {doc['rows']['readings']} readings, "
          f"{doc['rows']['events']} events ({doc['rows']['churn_events']} churn), {len(doc['files'])} Parquet files "
          f"{size / 1e6:.2f} MB, {doc['place_files']} place files, {doc['build_seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
