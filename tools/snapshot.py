"""Build COP's snapshot from the program feeds.

Fetches every program's contract-v2 feed (the kit's suite registry; drawing rules in config/programs.json), lints it
with the vendored doctrine kit, merges the
items into one contract-v2 FeatureCollection, picks at most five anomalies for the Today view, and reports each
feed's health. A feed that fails never fails the build: its last good items (from the previously published
snapshot) are kept, marked stale, and drawn grey. Silence is never shown as quiet. Nor does one malformed item: every
feature is checked as it comes in (sift), and one COP cannot carry is left out, counted in health.json and listed
under `dropped` in the snapshot, while the rest of its program's feed goes through.

    python tools/snapshot.py                                  # fetch live, write site/data/
    python tools/snapshot.py --fixtures tests/fixtures        # build from saved feeds (tests, offline)
    python tools/snapshot.py --archive out/archive            # also keep a gzip copy named by the UTC day

Standard-library Python only.
"""

import argparse
import gzip
import json
import math
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from tic_doctrine import contract  # noqa: E402
from tic_doctrine import history  # noqa: E402
from tic_doctrine import lint as kitlint  # noqa: E402
from tic_doctrine import tips as kittips  # noqa: E402

CONFIG = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
# Each program's name, family, feed, site and freshness windows come from the suite registry in the doctrine kit
# (vocab/programs.json, kit 0.4.0, 1 Nov 2026), so every program reads one list; config/programs.json keeps only how
# COP draws each program. A program missing from the registry stops the build here.
REGISTRY_FIELDS = ("name", "family", "feed", "site", "fresh_hours", "stale_hours")
REGISTRY = {p["id"]: p for p in kittips.registry()["programs"]}
for _prog in CONFIG["programs"]:
    _prog.update({k: REGISTRY[_prog["id"]][k] for k in REGISTRY_FIELDS})
SOURCES = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"
PREVIOUS = "https://cop.theintelligence.company/data/today.geojson"
UA = "tic-cop/0.1 (+https://cop.theintelligence.company)"
LANGUAGE_KEY = "https://github.com/metacog-osint/noise-floor/blob/main/doctrine/kit/docs/language-key.md"
DROP = {"headlines", "machine_summary"}  # large text the map never shows; the program's own page has it


def utc(s):
    """ISO 8601 UTC string to an aware datetime, or None."""
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def strength(props, rules):
    """(value, label, rule index, rank) from the first rule whose 'when' matches and whose path holds a number.
    Rules rank in the order listed, so a lower index outranks any value under a higher one. Within a rule, rank is
    the value, or its size when the rule says "abs": true (14 Nov 2026, for signed measures such as Headwind's z, where
    a fall and a rise of the same size are equally unusual). The label always shows the signed value."""
    for i, rule in enumerate(rules):
        if any(dig(props, k) != v for k, v in (rule.get("when") or {}).items()):
            continue
        v = dig(props, rule["path"])
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v, rule["label"], i, abs(v) if rule.get("abs") else v
    return None, None, None, None


def parse_feed(body):
    """The body as a FeatureCollection, or ValueError. Its features are checked one by one later, by sift()."""
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ValueError("not a FeatureCollection")
    return data


def fetch(url, timeout=45):
    """(feed dict or None, fetch report)."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json, application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = r.status
        data = parse_feed(body)
        return data, {"ok": True, "status": status, "seconds": round(time.time() - t0, 2), "bytes": len(body)}
    except Exception as e:  # noqa: BLE001 - any failure is a failed fetch, reported, never fatal
        return None, {"ok": False, "status": getattr(e, "code", None), "seconds": round(time.time() - t0, 2),
                      "error": f"{type(e).__name__}: {e}"[:200]}


def read_local(path):
    p = Path(path)
    if not p.exists():
        return None, {"ok": False, "status": None, "seconds": 0, "error": f"missing {p.name}"}
    body = p.read_bytes()
    try:
        return parse_feed(body), {"ok": True, "status": 200, "seconds": 0, "bytes": len(body)}
    except ValueError as e:  # a saved feed that is not one reads as a failed fetch, as a live one does
        return None, {"ok": False, "status": 200, "seconds": 0, "error": f"{type(e).__name__}: {e}"[:200]}


# --- one malformed item never stops the build -------------------------------------------------------------------
# Review of 15 Nov 2026: an item with no id, with properties null, or a feature that was a string raised in build()
# or in the kit's contract check, the build failed, nothing deployed and the whole picture froze. sift() now reads
# every feature first. What it refuses is a wrong *shape*, the kind that raises here, in tools/agent_index.py or
# in the page (site/cop.js reads id, geometry and countries of every item before it draws anything). A wrong or
# missing *value* (an unknown tier, no sources, a date that is not UTC) is the linter's business: it is counted as
# a lint error and the item stays, as before.
GEOMETRY_DEPTH = {"Point": 0, "MultiPoint": 1, "LineString": 1, "MultiLineString": 2, "Polygon": 2, "MultiPolygon": 3}


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _coordinates(c, depth):
    if depth == 0:
        return isinstance(c, list) and len(c) >= 2 and all(_number(v) for v in c)
    return isinstance(c, list) and all(_coordinates(x, depth - 1) for x in c)


def _geometry(g):
    """A GeoJSON geometry the map can draw: a known type whose positions are numbers, or a collection of such."""
    if not isinstance(g, dict) or not isinstance(g.get("type"), str):
        return False
    if g["type"] == "GeometryCollection":
        return isinstance(g.get("geometries"), list) and all(_geometry(x) for x in g["geometries"])
    return g["type"] in GEOMETRY_DEPTH and _coordinates(g.get("coordinates"), GEOMETRY_DEPTH[g["type"]])


def _finite(v):
    """No NaN or Infinity anywhere inside. Python reads them from a feed and writes them back, but they are not JSON:
    a browser refuses the whole file, so one such number would blank the page and the MCP server."""
    if isinstance(v, float):
        return math.isfinite(v)
    if isinstance(v, dict):
        return all(_finite(x) for x in v.values())
    if isinstance(v, list):
        return all(_finite(x) for x in v)
    return True


def unsound(feature):
    """Why COP cannot carry this feature, or None when it can."""
    if not isinstance(feature, dict):
        return "the feature is not an object"
    props = feature.get("properties")
    if not isinstance(props, dict):
        return "properties is not an object"
    if not isinstance(props.get("id"), str) or not props["id"].strip():
        return "id is not a string"
    if feature.get("geometry") is not None and not _geometry(feature["geometry"]):
        return "geometry is not a GeoJSON geometry with numeric positions"
    for key, kind, word in (("countries", str, "strings"), ("sources", dict, "objects")):  # the lists COP walks
        v = props.get(key)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, kind) for x in v)):
            return f"{key} is not a list of {word}"
    if not _finite(feature):
        return "it holds a number that is not finite (NaN or Infinity)"
    return None


def sift(features, pid=None):
    """(the features COP can carry, their lint problems, one row for each it left out).

    A feature goes through when unsound() finds nothing and the kit's contract check can read it; the check's
    findings come back as (item id, problem) pairs for lint_feed. A row is {"id", "program", "index", "reason"}:
    the id when it could be read (else None), and the feature's place in its feed, counted from 0."""
    kept, problems, dropped = [], [], []
    for n, f in enumerate(features):
        why, found = unsound(f), []
        if why is None:
            try:
                found = contract.check(f)
            except Exception as e:  # noqa: BLE001 - a shape the kit's check cannot read must not stop the build
                why = f"the contract check could not read it ({type(e).__name__})"
        if why is None:
            kept.append(f)
            problems += [(f["properties"]["id"], p) for p in found]
            continue
        props = f.get("properties") if isinstance(f, dict) else None
        props = props if isinstance(props, dict) else {}
        iid, owner = props.get("id"), pid or props.get("cop_program")
        dropped.append({"id": iid if isinstance(iid, str) and iid.strip() else None,
                        "program": owner if isinstance(owner, str) else None, "index": n, "reason": why})
    return kept, problems, dropped


def features_of(doc):
    """A document's features, or none when it is not a FeatureCollection (a previous snapshot read from a file)."""
    return doc["features"] if isinstance(doc, dict) and isinstance(doc.get("features"), list) else []


def header_text(v):
    """A feed's header line (attribution, licence) when it is text, else None: it is joined into COP's own."""
    return v if isinstance(v, str) else None


def load_previous(where):
    """Last published snapshot (URL or file), or None."""
    if not where:
        return None
    try:
        if str(where).startswith("http"):
            data, rep = fetch(where, timeout=30)
            return data if rep["ok"] else None
        return json.loads(Path(where).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def lint_feed(feed, item_problems):
    """Lint counts for health.json: the kit's header check, plus what its contract check found in each item that
    sift() let through (item_problems)."""
    errors, warnings, examples = 0, 0, []
    problems = [("feed", p) for p in kitlint.check_header({k: v for k, v in feed.items() if k != "features"})]
    for item_id, (std, sev, msg) in problems + item_problems:
        if sev == "error":
            errors += 1
            if len(examples) < 3:
                examples.append(f"{item_id}: {msg}")
        else:
            warnings += 1
    return {"errors": errors, "warnings": warnings, "examples": examples}


def state_for(age_hours, prog, fetched):
    if not fetched or age_hours is None:
        return "stale"
    if age_hours <= prog["fresh_hours"]:
        return "fresh"
    if age_hours <= prog["stale_hours"]:
        return "late"
    return "stale"


def also_in(p, prog):
    """Presets besides the program's own that show this item (config also_in): kind listed and text matched."""
    kind = (str(p.get("id") or "").split(":") + ["", ""])[1]
    text = f"{p.get('title') or ''} {p.get('summary') or ''}"
    return [r["preset"] for r in prog.get("also_in") or []
            if kind in r.get("kinds", [kind]) and re.search(r["match"], text, re.I)]


def source_lanes(feed) -> dict | None:
    """{"lanes": [...], "lanes_read": [...]} from a feed's own per-source `health` block (Cordon's, Blackout's), or
    None when it has none. The suite ledger ends a program's item only when the item's own lane was read; the kit's
    lanes_ok decides read (last success is the last check, and the entry's state, if any, is fresh, late or up)."""
    h = feed.get("health")
    if not isinstance(h, dict) or not h:
        return None
    return {"lanes": sorted(h), "lanes_read": sorted(history.lanes_ok(h))}


def unread_sources(feed, prog) -> dict:
    """{lane: health entry} for the sources a feed's own health block says were not read well on its last run, for a
    program whose config sets "source_health" (Headwind, 15 Nov 2026). Such a program keeps a failed source's last
    good items in its feed; COP draws those items grey and never picks them, as it does a dead feed's copies. Read is
    the kit's lanes_ok (last success is the last check, and the state, if any, is fresh, late or up)."""
    h = feed.get("health") if isinstance(feed, dict) else None
    if not prog.get("source_health") or not isinstance(h, dict) or not h:
        return {}
    ok = history.lanes_ok(h)
    return {k: v for k, v in h.items() if k not in ok}


def source_summary(feed, prog) -> dict | None:
    """health.json's `sources` for a source_health program: how many sources its feed names, and those not read."""
    h = feed.get("health") if isinstance(feed, dict) else None
    if not prog.get("source_health") or not isinstance(h, dict) or not h:
        return None
    off = unread_sources(feed, prog)
    return {"total": len(h), "not_read": [{"id": k, "state": (v or {}).get("state") if isinstance(v, dict) else None,
                                           "detail": (v or {}).get("detail") if isinstance(v, dict) else None}
                                          for k, v in sorted(off.items())]}


def build(feeds, reports, previous, now):
    """feeds: {program id: feed or None}. Returns (today, picks, health, countries)."""
    features, health, attributions = [], [], []
    candidates = {}
    lanes_by_program = {}
    all_dropped = []
    previous = previous if isinstance(previous, dict) else {}
    previous_items, _, previous_dropped = sift(features_of(previous))
    for prog in CONFIG["programs"]:
        pid = prog["id"]
        feed, rep = feeds.get(pid), reports[pid]
        from_previous = False
        if feed is not None:
            items, problems, dropped = sift(features_of(feed), pid)
            lint = lint_feed(feed, problems)
            attribution, licence = header_text(feed.get("attribution")), header_text(feed.get("licence"))
            lanes_by_program[pid] = source_lanes(feed)
            off, sources = unread_sources(feed, prog), source_summary(feed, prog)
        else:
            off = {}
            sources = ((previous.get("cop_programs") or {}).get(pid) or {}).get("sources") if prog.get("source_health") else None
            items = [f for f in previous_items if f["properties"].get("cop_program") == pid]
            dropped = [d for d in previous_dropped if d["program"] == pid]
            from_previous = bool(items)
            lint = None
            prev_prog = (previous.get("cop_programs") or {}).get(pid) or {}
            attribution, licence = header_text(prev_prog.get("attribution")), header_text(prev_prog.get("licence"))
        all_dropped += dropped
        newest = max((utc(i.get("properties", {}).get("data_as_of")) for i in items
                      if utc(i.get("properties", {}).get("data_as_of"))), default=None)
        if feed is not None and prog.get("freshness") == "feed" and utc(feed.get("generated_at")):
            newest = utc(feed.get("generated_at"))  # notice programs: items carry issue dates, the feed is current
        age = round((now - newest).total_seconds() / 3600, 1) if newest else None
        state = state_for(age, prog, feed is not None)
        # A source's register key is its id, else its publisher; one that gives neither as text is the linter's.
        keys = {s.get("id") or s.get("publisher") for i in items for s in (i["properties"].get("sources") or [])
                if isinstance(s.get("id") or s.get("publisher"), str)}
        unregistered = sorted(keys - set(SOURCES))
        ranked = []
        lane_of = history.mapped_lane(prog.get("lane_map") or {})
        for order, item in enumerate(items):
            p = dict(item.get("properties") or {})
            for k in DROP:
                p.pop(k, None)
            p.pop("cop_source_stale", None)
            src_off = bool(off) and lane_of(item) in off
            if src_off:
                p["cop_source_stale"] = True     # its own source was not read on the program's last run
            value, label, rule, rank = strength(p, prog["strength"])
            p.update({"cop_program": pid, "cop_stale": state == "stale" or src_off, "cop_strength": value,
                      "cop_strength_text": label.replace("{v}", f"{value:g}") if value is not None else None})
            p.pop("cop_also", None)
            also = also_in(p, prog)
            if also:
                p["cop_also"] = "," + ",".join(also) + ","   # a string, so MapLibre can test it
            feature = {"type": "Feature", "geometry": item.get("geometry"), "properties": p}
            features.append(feature)
            if p.get("tier") == "anomaly" and item.get("geometry") and state != "stale" and not src_off:
                ranked.append((99 if rule is None else rule, -(rank or 0), order, feature))
        candidates[pid] = [f for *_, f in sorted(ranked, key=lambda r: r[:3])]
        if attribution:
            attributions.append(attribution)
        health.append({
            "id": pid, "name": prog["name"], "state": state, "fetch": rep, "from_previous": from_previous,
            "items": len(items), "anomalies": sum(1 for i in items if i.get("properties", {}).get("tier") == "anomaly"),
            "placed": sum(1 for i in items if i.get("geometry")),
            "data_as_of": iso(newest) if newest else None, "age_hours": age,
            "fresh_hours": prog["fresh_hours"], "stale_hours": prog["stale_hours"],
            "lint": lint, "unregistered_sources": unregistered, "attribution": attribution, "licence": licence,
            "site": prog["site"], "feed": prog["feed"],
            **({"sources": sources} if sources is not None else {}),
            # Malformed items left out of the picture (sift): how many, and up to three with the reason.
            "dropped": {"count": len(dropped), "examples": [
                f"{d['id'] or 'item ' + str(d['index'] + 1) + ' of the feed'}: {d['reason']}" for d in dropped[:3]]},
        })

    # Picks: each program's strongest placed anomaly first, one program at a time, at most max_picks.
    picks, round_ = [], 0
    while len(picks) < CONFIG["max_picks"]:
        added = False
        for prog in CONFIG["programs"]:
            queue = candidates[prog["id"]]
            if round_ < len(queue) and len(picks) < CONFIG["max_picks"]:
                f = queue[round_]
                p = f["properties"]
                rank = "strongest" if round_ == 0 else "next strongest"
                owner = prog["name"] + ("'" if prog["name"].endswith("s") else "'s")
                reason = f"{owner} {rank} anomaly with a place"
                if p.get("cop_strength") is not None:
                    reason += f": {p['cop_strength_text']}"
                picks.append({"n": len(picks) + 1, "id": p["id"], "program": prog["id"], "reason": reason})
                added = True
        if not added:
            break
        round_ += 1
    picked = {p["id"]: p["n"] for p in picks}
    for f in features:
        f["properties"]["cop_pick"] = picked.get(f["properties"]["id"])

    countries = country_shapes(features)
    programs_block = {h["id"]: {"name": h["name"], "state": h["state"], "data_as_of": h["data_as_of"],
                                "attribution": h["attribution"], "licence": h["licence"], "site": h["site"]}
                      | (lanes_by_program.get(h["id"]) or {})
                      | ({"sources": h["sources"]} if h.get("sources") is not None else {})
                      for h in health}
    today = {
        "type": "FeatureCollection", "contract": "tic-feed/2", "program": "cop", "generated_at": iso(now),
        "licence": "Each item keeps its program's licence (cop_programs below).",
        "attribution": "COP, The Intelligence Company. " + " ".join(attributions),
        "language_key": LANGUAGE_KEY, "cop_programs": programs_block,
        # Items left out as malformed, by the kit's own name for items held back: the suite ledger does not end an
        # item listed here (history.update reads `dropped`), since it left the picture, not its program's feed.
        "dropped": all_dropped, "features": features,
    }
    public = [{k: p[k] for k in ("id", "name", "family", "preset", "site", "mark", "color")} |
              {"country_mark": p.get("country_mark", "fill"), "area_style": p.get("area_style", "solid"),
               "routine_in_preset": bool(p.get("routine_in_preset")),
               "routine_label": p.get("routine_label", "standing notices"),
               "also_in": [{"preset": r["preset"], "label": r["label"]} for r in p.get("also_in") or []]} |
              {"strength_labels": [r["label"] for r in p["strength"]]} for p in CONFIG["programs"]]
    health_doc = {"generated_at": iso(now), "programs": health, "config": {
        "programs": public, "presets": CONFIG["presets"], "basemaps": CONFIG["basemaps"],
        "stale_color": CONFIG["stale_color"], "max_picks": CONFIG["max_picks"]}}
    return today, {"generated_at": iso(now), "picks": picks}, health_doc, countries


def country_shapes(features):
    """Outlines for every country named by a country-level item of a program that shades countries (country_mark
    'fill'); halo programs ring a label point instead. Only those countries, to keep the page light."""
    fills = {p["id"] for p in CONFIG["programs"] if p.get("country_mark", "fill") == "fill"}
    wanted = {cc for f in features if f["properties"].get("precision_class") == "country"
              and f["properties"].get("cop_program") in fills
              for cc in (f["properties"].get("countries") or [])}
    data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
    out = []
    for c in data["countries"]:
        if c["cc"] in wanted:
            out.append({"type": "Feature", "properties": {"cc": c["cc"], "name": c["name"]},
                        "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in c["rings"]]}})
    return {"type": "FeatureCollection", "source": data["source"], "features": out}


def world_shapes(step=0.1):
    """Every country outline, simplified to `step` degrees: the land at world zoom and the fallback base map."""
    data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
    feats = []
    for c in data["countries"]:
        polys = []
        for ring in c["rings"]:
            out = []
            for x, y in ring:
                pt = [round(x / step) * step, round(y / step) * step]
                pt = [round(pt[0], 2), round(pt[1], 2)]
                if not out or out[-1] != pt:
                    out.append(pt)
            if len(out) >= 4:
                if out[0] != out[-1]:
                    out.append(out[0])
                polys.append([out])
        if polys:
            feats.append({"type": "Feature", "properties": {"cc": c["cc"]},
                          "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "source": data["source"] + f"; simplified to {step} degrees for world zoom",
            "features": feats}


def write_json(path, obj, compact=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None,
                      indent=None if compact else 1)
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def fetch_ended(now, out, fixtures=None, archive=None):
    """Each program's list of items that ended recently (config "ended": Elements' data/ended.geojson, its last 7
    days), written to out/<program>.geojson for joins that ask what happened in a window. Not drawn and not
    published: the map shows what is active. With an archive folder, a gzip copy per UTC day,
    <day>.<program>-ended.json.gz, so base rates can read what had ended by each archived snapshot. A failed fetch
    removes the old file, so a reader says so rather than reading an old list. Returns one line per program for the
    log."""
    lines = []
    out.mkdir(parents=True, exist_ok=True)
    for prog in CONFIG["programs"]:
        if not prog.get("ended"):
            continue
        pid, dest = prog["id"], out / f"{prog['id']}.geojson"
        data, rep = (read_local(Path(fixtures) / f"{prog['id']}-ended.geojson") if fixtures else fetch(prog["ended"]))
        if not rep["ok"]:
            dest.unlink(missing_ok=True)
            lines.append(f"{pid} ended items: fetch failed ({rep.get('error')}); readers will say it is missing")
            continue
        data["features"], _, left_out = sift(data["features"], pid)  # the joins that read this list need sound items too
        data["cop_fetched_at"] = iso(now)
        write_json(dest, data)
        if archive:
            archive.mkdir(parents=True, exist_ok=True)
            with gzip.open(archive / f"{now:%Y-%m-%d}.{pid}-ended.json.gz", "wt", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{pid} ended items: {len(data['features'])}, generated {data.get('generated_at')}"
                     + (f"; {len(left_out)} malformed left out" if left_out else ""))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    ap.add_argument("--fixtures", help="read <program>.geojson from this folder instead of fetching")
    ap.add_argument("--previous", default=None, help=f"last published snapshot, URL or file (live default: {PREVIOUS})")
    ap.add_argument("--archive", help="also write a gzip copy of today's snapshot here, named by its UTC day")
    ap.add_argument("--ended-out", default=str(ROOT / "out" / "ended"),
                    help="where each program's ended-items file goes (<program>.geojson; not published, not drawn)")
    ap.add_argument("--now", help="pretend the time is this ISO UTC instant (tests)")
    ap.add_argument("--dead", action="append", default=[], help="treat this program's feed as failed (tests)")
    a = ap.parse_args(argv)

    now = utc(a.now) if a.now else datetime.now(timezone.utc).replace(microsecond=0)
    feeds, reports = {}, {}
    for prog in CONFIG["programs"]:
        pid = prog["id"]
        if pid in a.dead:
            feeds[pid], reports[pid] = None, {"ok": False, "status": None, "seconds": 0, "error": "simulated failure"}
        elif a.fixtures:
            feeds[pid], reports[pid] = read_local(Path(a.fixtures) / f"{pid}.geojson")
        else:
            feeds[pid], reports[pid] = fetch(prog["feed"])
    previous = load_previous(a.previous if a.previous is not None else (None if a.fixtures else PREVIOUS)) \
        if any(f is None for f in feeds.values()) else None

    today, picks, health, countries = build(feeds, reports, previous, now)
    out = Path(a.out)
    write_json(out / "today.geojson", today)
    write_json(out / "picks.json", picks, compact=False)
    write_json(out / "health.json", health, compact=False)
    write_json(out / "countries-today.geojson", countries)
    write_json(out / "world.geojson", world_shapes())
    if a.archive:
        arch = Path(a.archive)
        arch.mkdir(parents=True, exist_ok=True)
        with gzip.open(arch / f"{now:%Y-%m-%d}.geojson.gz", "wt", encoding="utf-8") as fh:
            json.dump(today, fh, ensure_ascii=False, separators=(",", ":"))
    for line in fetch_ended(now, Path(a.ended_out), a.fixtures, Path(a.archive) if a.archive else None):
        print(line)
    for h in health["programs"]:
        lint = h["lint"] or {}
        print(f"{h['name']:<11} {h['state']:<6} items={h['items']:<4} anomalies={h['anomalies']:<3} "
              f"age={h['age_hours']}h lint_errors={lint.get('errors', '-')} "
              f"{'(last good copy)' if h['from_previous'] else ''}{' fetch failed: ' + h['fetch'].get('error', '') if not h['fetch']['ok'] else ''}")
        if h["dropped"]["count"]:  # an annotation on the Actions run, never a failure
            print(f"::warning::{h['name']}: {h['dropped']['count']} malformed item(s) left out of the picture: "
                  + "; ".join(h["dropped"]["examples"]))
    print(f"picks: {', '.join(p['program'] + ':' + p['id'][:40] for p in picks['picks'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
