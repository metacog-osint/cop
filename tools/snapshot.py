"""Build COP's snapshot from the program feeds.

Fetches every program's contract-v2 feed (the kit's suite registry; drawing rules in config/programs.json), lints it
with the vendored doctrine kit, merges the
items into one contract-v2 FeatureCollection, picks at most five anomalies for the Today view, and reports each
feed's health. A feed that fails never fails the build: its last good items (from the previously published
snapshot) are kept, marked stale, and drawn grey. Silence is never shown as quiet.

    python tools/snapshot.py                                  # fetch live, write site/data/
    python tools/snapshot.py --fixtures tests/fixtures        # build from saved feeds (tests, offline)
    python tools/snapshot.py --archive out/archive            # also keep a gzip copy named by the UTC day

Standard-library Python only.
"""

import argparse
import gzip
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from tic_doctrine import contract  # noqa: E402
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
    """(value, label, rule index) from the first rule whose 'when' matches and whose path holds a number.
    Rules rank in the order listed, so a lower index outranks any value under a higher one."""
    for i, rule in enumerate(rules):
        if any(dig(props, k) != v for k, v in (rule.get("when") or {}).items()):
            continue
        v = dig(props, rule["path"])
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v, rule["label"], i
    return None, None, None


def fetch(url, timeout=45):
    """(feed dict or None, fetch report)."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json, application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = r.status
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise ValueError("not a FeatureCollection")
        return data, {"ok": True, "status": status, "seconds": round(time.time() - t0, 2), "bytes": len(body)}
    except Exception as e:  # noqa: BLE001 - any failure is a failed fetch, reported, never fatal
        return None, {"ok": False, "status": getattr(e, "code", None), "seconds": round(time.time() - t0, 2),
                      "error": f"{type(e).__name__}: {e}"[:200]}


def read_local(path):
    p = Path(path)
    if not p.exists():
        return None, {"ok": False, "status": None, "seconds": 0, "error": f"missing {p.name}"}
    body = p.read_bytes()
    return json.loads(body), {"ok": True, "status": 200, "seconds": 0, "bytes": len(body)}


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


def lint_feed(feed):
    errors, warnings, examples = 0, 0, []
    problems = [("feed", p) for p in kitlint.check_header({k: v for k, v in feed.items() if k != "features"})]
    for item in feed["features"]:
        props, _ = contract.split(item)
        problems += [(props.get("id", "?"), p) for p in contract.check(item)]
    for item_id, (std, sev, msg) in problems:
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


def build(feeds, reports, previous, now):
    """feeds: {program id: feed or None}. Returns (today, picks, health, countries)."""
    features, health, attributions = [], [], []
    candidates = {}
    for prog in CONFIG["programs"]:
        pid = prog["id"]
        feed, rep = feeds.get(pid), reports[pid]
        from_previous = False
        if feed is not None:
            items = feed["features"]
            lint = lint_feed(feed)
            attribution, licence = feed.get("attribution"), feed.get("licence")
        else:
            items = [f for f in (previous or {}).get("features", []) if f.get("properties", {}).get("cop_program") == pid]
            from_previous = bool(items)
            lint = None
            prev_prog = ((previous or {}).get("cop_programs") or {}).get(pid, {})
            attribution, licence = prev_prog.get("attribution"), prev_prog.get("licence")
        newest = max((utc(i.get("properties", {}).get("data_as_of")) for i in items
                      if utc(i.get("properties", {}).get("data_as_of"))), default=None)
        if feed is not None and prog.get("freshness") == "feed" and utc(feed.get("generated_at")):
            newest = utc(feed.get("generated_at"))  # notice programs: items carry issue dates, the feed is current
        age = round((now - newest).total_seconds() / 3600, 1) if newest else None
        state = state_for(age, prog, feed is not None)
        unregistered = sorted({s.get("id") or s.get("publisher") for i in items
                               for s in (i.get("properties", {}).get("sources") or [])
                               if (s.get("id") or s.get("publisher")) not in SOURCES})
        ranked = []
        for order, item in enumerate(items):
            p = dict(item.get("properties") or {})
            for k in DROP:
                p.pop(k, None)
            value, label, rule = strength(p, prog["strength"])
            p.update({"cop_program": pid, "cop_stale": state == "stale", "cop_strength": value,
                      "cop_strength_text": label.replace("{v}", f"{value:g}") if value is not None else None})
            feature = {"type": "Feature", "geometry": item.get("geometry"), "properties": p}
            features.append(feature)
            if p.get("tier") == "anomaly" and item.get("geometry") and state != "stale":
                ranked.append((99 if rule is None else rule, -(value or 0), order, feature))
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
                      for h in health}
    today = {
        "type": "FeatureCollection", "contract": "tic-feed/2", "program": "cop", "generated_at": iso(now),
        "licence": "Each item keeps its program's licence (cop_programs below).",
        "attribution": "COP, The Intelligence Company. " + " ".join(attributions),
        "language_key": LANGUAGE_KEY, "cop_programs": programs_block, "features": features,
    }
    public = [{k: p[k] for k in ("id", "name", "family", "preset", "site", "mark", "color")} |
              {"country_mark": p.get("country_mark", "fill"), "area_style": p.get("area_style", "solid"),
               "routine_in_preset": bool(p.get("routine_in_preset")),
               "routine_label": p.get("routine_label", "standing notices")} |
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
        data["cop_fetched_at"] = iso(now)
        write_json(dest, data)
        if archive:
            archive.mkdir(parents=True, exist_ok=True)
            with gzip.open(archive / f"{now:%Y-%m-%d}.{pid}-ended.json.gz", "wt", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{pid} ended items: {len(data['features'])}, generated {data.get('generated_at')}")
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
    print(f"picks: {', '.join(p['program'] + ':' + p['id'][:40] for p in picks['picks'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
