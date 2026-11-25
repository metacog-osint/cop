"""Rebuild Watchfloor's country attention for a past range, for the STOP replay (cop-layer-history/1).

Watchfloor's own backfill (`more-context/scripts/backfill_events.py`) downloads GDELT Events 2.0's 15-minute files
for past days and builds each day's `stories/YYYY/MM/DD/countries.json` with Watchfloor's own code: distinct articles
per country-day with an event located there (attention) or a CAMEO 18-20 violent event (violence), each scored
against the country's previous 28 complete days (log z, weekday-adjusted), and the Rising lists (z >= 2.5, at least
1.5x expected, at least 40 articles, or 15 for violence; top 15 each). Watchfloor raises a country item on its 28-day
baseline (wf-28d), the "unusual attention" triage counts, exactly when the country is on a Rising list.

This reads those day files, converts FIPS 10-4 to ISO alpha-2 (Watchfloor's `catalog/fips_iso.json`; the West
Bank and Gaza are both PS), and writes one row per country-day.

    python tools/history_watchfloor.py --root "$HOME/cop-history/gdelt-events" \
        --fips ../more-context/catalog/fips_iso.json --start 2024-12-01 --end 2025-12-31
"""
import argparse
import gzip
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [
    {"id": "gdelt-events", "name": "GDELT Events 2.0, 15-minute export files (English and translated streams)",
     "url": "http://data.gdeltproject.org/gdeltv2/", "licence": "GDELT: free and open for any use with citation",
     "attribution": "The GDELT Project (gdeltproject.org)", "how": "archive-file"},
]


def fips_map(path):
    codes = json.loads(Path(path).read_text(encoding="utf-8"))["codes"]
    return {f: c["iso"] for f, c in codes.items() if c.get("iso")}


def day_rows(doc, fmap, retrieved_at):
    """Rows for one countries.json: one per ISO country, FIPS codes that share an ISO code merged (counts summed,
    rising if any part rose, the highest z kept)."""
    lists = doc.get("lists") or {}
    rise = {m: set((lists.get(m) or {}).get("rising") or []) for m in ("urls", "violent")}
    out = {}
    for f, c in (doc.get("countries") or {}).items():
        iso = fmap.get(f)
        if not iso:
            continue
        r = out.setdefault(iso, {"place": iso, "at": doc["day"], "precision": "day", "source_id": "gdelt-events",
                                 "source_ref": f"stories/{doc['day'].replace('-', '/')}/countries.json",
                                 "retrieved_at": retrieved_at,
                                 "quality": ["partial-day"] if doc.get("partial") else [],
                                 "fips": [], "urls": 0, "violent": 0, "urls_z": None, "urls_ratio": None,
                                 "violent_z": None, "rising_attention": False, "rising_violence": False})
        r["fips"].append(f)
        r["urls"] += c.get("urls", 0)
        r["violent"] += c.get("violent", 0)
        for m, key in (("urls", "urls"), ("violent", "violent")):
            s = c.get(f"{m}_score") or {}
            if s.get("z") is not None and (r[f"{key}_z"] is None or s["z"] > r[f"{key}_z"]):
                r[f"{key}_z"] = s["z"]
                if m == "urls":
                    r["urls_ratio"] = s.get("ratio")
        r["rising_attention"] |= f in rise["urls"]
        r["rising_violence"] |= f in rise["violent"]
    for r in out.values():
        r["unusual"] = r["rising_attention"] or r["rising_violence"]
        r["fips"].sort()
        if len(r["fips"]) > 1:
            r["quality"].append("merged-fips")
    return sorted(out.values(), key=lambda r: r["place"])


def build(root, fmap, start, end):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    rows, missing, partial, unscored = [], [], [], []
    d = start
    while d <= end:
        p = Path(root) / "stories" / f"{d:%Y/%m/%d}" / "countries.json"
        if not p.exists():
            missing.append(str(d))
        else:
            doc = json.loads(p.read_text(encoding="utf-8"))
            day = day_rows(doc, fmap, now)
            if doc.get("partial"):
                partial.append(str(d))
            elif mark_unscored(day):
                unscored.append(str(d))
            rows += day
        d += timedelta(days=1)
    return rows, missing, partial, unscored, now


def mark_unscored(day):
    """A complete day on which no country has a z-score: fewer than 14 complete days sat in its 28-day baseline
    (Watchfloor's MIN_BASELINE_DAYS), as after GDELT's June 2025 gap, so no country could be Rising. Its rows are
    marked `unscored-day`, and a replay reads that day as incomplete, not as quiet."""
    if day and all(r["urls_z"] is None and r["violent_z"] is None for r in day):
        for r in day:
            r["quality"].append("unscored-day")
        return True
    return False


def unusual_index(rows):
    """{(iso, 'YYYY-MM-DD'): row}, for the replay."""
    return {(r["place"], r["at"]): r for r in rows}


def manifest(rows, start, end, missing, partial, built_at, code_sha, root, unscored=()):
    return {
        "contract": "cop-layer-history/1",
        "dataset_id": f"watchfloor-country-attention-{start}-{end}",
        "layer": "watchfloor",
        "title": "Watchfloor's country attention and violence, rebuilt from GDELT Events with Watchfloor's own code",
        "description": "One row per country-day: distinct GDELT articles with an event located there, and with a "
                       "violent event, each with its 28-day z, and whether the country was on Watchfloor's Rising "
                       "list (the wf-28d anomaly triage reads as unusual attention).",
        "sources": SOURCES,
        "derived_from": [],
        "method": {"code": "more-context/scripts/backfill_events.py (Watchfloor), then tools/history_watchfloor.py",
                   "commit": code_sha,
                   "params": {"root": Path(root).name + " (local backfill folder)", "rising_z": 2.5, "rising_ratio": 1.5,
                              "min_articles": {"attention": 40, "violence": 15}, "listed_max": 15},
                   "matches_live": True,
                   "differences": [
                       "Country items only. Watchfloor's zone items (attention on press named for a zone, from "
                       "GDELT's GKG) are not rebuilt; triage also counts those.",
                       "Built from GDELT's files as they stand now; the live cycle read them within minutes.",
                       "FIPS codes sharing one ISO code (West Bank and Gaza: PS) are merged; the live feed keeps "
                       "them apart."]},
        "coverage": {"start": str(start), "end": str(end), "time_precision": "day", "places": "all",
                     "place_code": "ISO 3166-1 alpha-2 (from FIPS 10-4 by more-context/catalog/fips_iso.json)"},
        "gaps": ([{"from": d, "to": d, "places": "all", "reason": "no day file"} for d in missing]
                 + [{"from": d, "to": d, "places": "all",
                     "reason": "partial day (over a tenth of GDELT's files missing): counted, not scored"}
                    for d in partial]
                 + [{"from": d, "to": d, "places": "all",
                     "reason": "unscored day (under 14 complete days in its 28-day baseline): counted, no z, "
                               "no Rising list possible"} for d in unscored]),
        "biases": ["GDELT geocodes place names in the text, so attention is noisy; violence is less so.",
                   "English and machine-translated press only, as GDELT carries them."],
        "rows": {"count": len(rows), "places": len({r["place"] for r in rows}),
                 "days": (end - start).days + 1 - len(missing)},
        "row_schema": {"place": "ISO alpha-2", "at": "the UTC day", "precision": "day",
                       "source_id": "gdelt-events", "source_ref": "the day file it came from",
                       "retrieved_at": "UTC", "quality": "partial-day, unscored-day, merged-fips", "fips": "GDELT codes merged",
                       "urls": "distinct articles with an event there", "violent": "of those, with a violent event",
                       "urls_z": "28-day log z of urls", "urls_ratio": "urls / expected",
                       "violent_z": "28-day log z of violent", "rising_attention": "on the attention Rising list",
                       "rising_violence": "on the violence Rising list",
                       "unusual": "either: what triage counts as unusual attention"},
        "files": [],
        "redistribution": "rows",
        "built_at": built_at,
        "built_by": "tools/history_watchfloor.py, COP STOP-replay session, 15-16 Nov 2026",
        "uses": ["replaying triage's attention signal for any country and day in range",
                 "per-country attention baselines"],
        "not_for": ["zone-level attention", "a count of events (it counts articles)"],
        "supersedes": None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="the backfill root holding stories/")
    ap.add_argument("--fips", required=True, help="more-context/catalog/fips_iso.json")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default=str(ROOT / "history" / "watchfloor"))
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    rows, missing, partial, unscored, now = build(a.root, fips_map(a.fips), start, end)
    code_sha = "uncommitted sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    man = manifest(rows, start, end, missing, partial, now, code_sha, a.root, unscored)
    out = Path(a.out) / man["dataset_id"]
    out.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")
    gz = gzip.compress(body, mtime=0)
    (out / "rows.jsonl.gz").write_bytes(gz)
    man["files"] = [{"path_or_url": f"history/watchfloor/{man['dataset_id']}/rows.jsonl.gz",
                     "sha256": hashlib.sha256(gz).hexdigest(), "bytes": len(gz)}]
    (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} rows, {len(missing)} days missing, {len(partial)} partial, {len(unscored)} unscored, "
          f"{sum(r['unusual'] for r in rows)} unusual country-days -> {out} ({len(gz):,} bytes)")


if __name__ == "__main__":
    sys.exit(main())
