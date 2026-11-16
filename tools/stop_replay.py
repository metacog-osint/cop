"""Replay COP's outage triage on Access Now's documented 2025 shutdowns, two layers of three (cop-layer-history/1).

A first hit rate for triage (cop-purpose sequence step 4). For each STOP shutdown that began in the year: what
Elements (significant hazards, rebuilt by tools/history_elements.py) and Watchfloor (unusual attention, rebuilt by
tools/history_watchfloor.py) would have shown in that country, read with triage's own rules (tri.hazards; Watchfloor's
Rising list = its wf-28d anomaly). Cordon's notices are not replayed yet, so a tension-context reading here rests on
attention alone and the true rate is higher on both sides.

The same reading on control days (the same countries, every day of the year with no STOP shutdown starting, running
or ending within CONTROL_BUFFER_D days) gives the false-alarm side. Results compare each shutdown with its own
country's control days (the matched rate), because shutdowns cluster in a few countries.

STOP has no data licence: the run publishes counts and rates only, never STOP's rows.

    python tools/stop_replay.py --stop [local path] \
        --elements history/elements/elements-hazards-2024-11-28-2025-12-31 \
        --watchfloor history/watchfloor/watchfloor-country-attention-2024-12-01-2025-12-31 --year 2025
"""
import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_elements as hel  # noqa: E402
import triage as tri  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTROL_BUFFER_D = 7
OUTAGE_HOUR = 12            # STOP dates a shutdown by the day; read it at midday UTC
OPEN_ENDED_D = 30           # a shutdown with no end date is taken to run this long, for the control exclusion
# STOP's country names that the kit's short names don't match.
ALIASES = {"Iran (Islamic Republic of)": "IR", "Palestine, State of": "PS", "Tanzania, United Republic of": "TZ",
           "Russian Federation": "RU", "Syrian Arab Republic": "SY", "Venezuela (Bolivarian Republic of)": "VE",
           "Congo (Democratic Republic of the)": "CD", "Viet Nam": "VN", "United States of America": "US",
           "Bolivia (Plurinational State of)": "BO", "Korea (Republic of)": "KR", "Moldova (Republic of)": "MD",
           "Lao People's Democratic Republic": "LA", "Türkiye": "TR", "Congo": "CG"}
READINGS = ("natural", tri.TENSION, "open", "incomplete")
CAVEATS = [
    "Two of triage's three layers: Cordon's travel and embassy notices are not replayed yet, so tension context "
    "here rests on unusual attention alone, and the real rate is higher for shutdowns and control days alike.",
    "Watchfloor's zone items are not replayed, only its country items.",
    "STOP dates a shutdown by the day; each is read at 12:00 UTC on its first day, with Watchfloor's last full day "
    "before it.",
    "STOP's 2025 list is mostly shutdowns in conflict and protests, and Myanmar and India are over half of it, so "
    "the country rows matter more than the pooled ones.",
    "Most STOP shutdowns are regional; triage reads countries, so a regional shutdown is read as its country.",
    "Each shutdown is compared with its own country's control days. Myanmar has none (its shutdowns, with 7 days "
    "either side and open-ended ones taken as 30 days, cover all of 2025), so its 83 shutdowns are in the "
    "all-shutdowns shares and left out of the matched comparison.",
    "A replay says how often a documented shutdown would have read each way, and how often any day would have. It is "
    "not a test of whether triage finds shutdowns, and no reading is a finding of cause.",
]


def _date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        return None


SCOPES = {"It only affected one city, county, or village": "one city or district",
          "It affected more than one city in the same state, province, or region": "several cities in one region",
          "It affected locations in more than one state, province, or region": "several regions"}


def _scope(r):
    if (r.get("area_name") or "").strip().lower() == "nationwide":
        return "nationwide"
    return SCOPES.get((r.get("geo_scope") or "").strip(), "unknown")


def write_labels_manifest(path, year, shutdowns, sha, out_root):
    """history/labels/stop-<year>/manifest.json: the labels this replay used. STOP has no data licence, so the rows
    stay where Blackout keeps them and only this description and counts are written here."""
    man = {
        "contract": "cop-layer-history/1", "dataset_id": f"labels-stop-{year}", "layer": "labels",
        "title": f"Access Now's documented {year} internet shutdowns (#KeepItOn STOP), as outage labels",
        "description": "Each STOP row that began in the year, one per country it names: the shutdown's start day, "
                       "end day if any, geographic scope, shutdown type and STOP's actual cause. Labels outages of "
                       "known cause (government-ordered shutdowns) for replays.",
        "sources": [{"id": "accessnow-stop", "name": "Access Now #KeepItOn STOP dataset, public sheet, first tab",
                     "url": "https://docs.google.com/spreadsheets/d/1DvPAuHNLp5BXGb0nnZDGNoiIwEeu2ogdXEIDvT4Hyfk",
                     "licence": "No data licence stated by Access Now; credited as the source, counts only",
                     "attribution": "Access Now, #KeepItOn STOP", "how": "archive-file"}],
        "derived_from": [],
        "method": {"code": "tools/stop_replay.py load_stop", "commit": "see the replay run", "params": {"year": year},
                   "matches_live": True, "differences": []},
        "coverage": {"start": f"{year}-01-01", "end": f"{year}-12-31", "time_precision": "day",
                     "places": sorted({s["country"] for s in shutdowns}), "place_code": "ISO 3166-1 alpha-2"},
        "gaps": [{"from": f"{year}-01-01", "to": f"{year}-12-31", "places": "all",
                  "reason": "STOP lists shutdowns Access Now documented; undocumented ones are missing"}],
        "biases": ["Weighted to countries where Access Now's partners work (Myanmar and India are over half).",
                   f"{sum(s['estimated'] for s in shutdowns)} start dates are STOP's estimates."],
        "rows": {"count": len(shutdowns), "places": len({s["country"] for s in shutdowns}), "days": None},
        "row_schema": {"country": "ISO alpha-2", "start": "first day", "end": "last day or null",
                       "estimated": "STOP's start_date_type is Estimated", "scope": "nationwide or STOP's geo scope",
                       "type": "STOP shutdown_type", "cause": "STOP actual_cause"},
        "files": [{"path_or_url": str(path), "sha256": sha, "bytes": Path(path).stat().st_size}],
        "redistribution": "aggregates",
        "built_at": tri.iso(datetime.now(timezone.utc)), "built_by": "tools/stop_replay.py",
        "uses": ["hit rates for joins that read outages", "outage cause labels by country and day"],
        "not_for": ["publishing STOP's rows", "a complete record of shutdowns"],
        "supersedes": None,
    }
    out = Path(out_root) / "labels" / man["dataset_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    return man


def country_codes():
    data = json.loads(tri.COUNTRIES.read_text(encoding="utf-8"))
    return {c["name"].lower(): c["cc"] for c in data["countries"]}


def load_stop(path, year):
    """[(row index, shutdown)] for STOP rows that began in `year`. A row naming several countries ("Cameroon;
    Central African Republic") is one shutdown in each."""
    names = country_codes()
    out, unmapped = [], Counter()
    with open(path, encoding="utf-8") as f:
        for i, r in enumerate(csv.DictReader(f)):
            start = _date(r.get("start_date"))
            if not start or start.year != year:
                continue
            ccs = []
            for name in (n.strip() for n in r["country"].split(";")):
                cc = ALIASES.get(name) or names.get(name.lower())
                (ccs.append(cc) if cc else unmapped.update([name]))
            end = _date(r.get("end_date"))
            for cc in ccs:
                out.append({"row": i, "country": cc, "start": start, "end": end,
                            "estimated": (r.get("start_date_type") or "").strip().lower() == "estimated",
                            "scope": _scope(r),
                            "type": (r.get("shutdown_type") or "").strip() or "unknown",
                            "cause": (r.get("actual_cause") or "").strip() or "unknown"})
    if unmapped:
        raise SystemExit(f"STOP country names with no ISO code: {dict(unmapped)}; add them to ALIASES")
    return out


def load_rows(folder):
    folder = Path(folder)
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    p = folder / Path(man["files"][0]["path_or_url"]).name
    body = p.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    if sha != man["files"][0]["sha256"]:
        raise SystemExit(f"{p}: sha256 does not match its manifest")
    text = gzip.decompress(body).decode("utf-8") if p.suffix == ".gz" else body.decode("utf-8")
    return man, [json.loads(line) for line in text.splitlines() if line.strip()], sha


NOT_SCORED = {"partial-day", "unscored-day"}
SAME_DAY_LABEL = ("Sensitivity variant, not triage's rule: unusual attention on the shutdown's own day also counts "
                  "(live triage reads only Watchfloor days that ended before the outage, and attention on the day "
                  "can be about the shutdown itself).")


class Cordon:
    """Cordon's notices rebuilt from its history datasets (State advisories, FCDO advice, US embassy alerts), as the
    items Cordon would have carried at a time, with the archive lookups triage reads beside them: advisory levels for
    a notice's direction (State's levels, FCDO's statements as triage's notice_level ranks them) and each embassy
    alert's first-seen date. Only the countries in `places` are read; elsewhere this layer is not replayed."""

    def __init__(self, folders):
        import history_cordon_embassy as emb
        import history_cordon_fcdo as fcdo
        import history_cordon_state as state
        self.mods = {"advisory": state, "fcdo": fcdo, "alert": emb}
        self.rows = {k: [] for k in self.mods}
        self.manifests = []
        for folder in folders:
            man, rows, sha = load_rows(folder)
            self.manifests.append({"dataset_id": man["dataset_id"], "sha256": sha})
            for r in rows:
                if r.get("kind") in self.rows:
                    self.rows[r["kind"]].append(r)
        self.places = {r["place"] for k in ("fcdo", "alert") for r in self.rows[k]}
        self.levels = state.levels_from_rows(self.rows["advisory"])
        for r in self.rows["fcdo"]:
            st = r.get("alert_status")
            if isinstance(st, list):
                self.levels.setdefault(r["item_id"], []).append([r["at"], [1 if s in st else 0
                                                                          for s in tri.FCDO_STATEMENTS]])
        self.alert_dates = {}
        for r in self.rows["alert"]:
            self.alert_dates.setdefault(r["item_id"], []).append([r.get("first_seen") or r["at"], r["at"]])
        self._cache = {}

    def items(self, at):
        if at not in self._cache:
            self._cache = {at: [f for k, m in self.mods.items() for f in m.to_items(self.rows[k], at)]}
        return self._cache[at]

    def counted(self, cc, at):
        return bool(tri.notices(self.items(at), cc, at, self.levels, self.alert_dates)["recent"])


def read_day(cc, day, items, wf, same_day=False, cordon=None):
    """One country-day, read the way triage would: natural, tension context, open, or incomplete (no complete,
    scored Watchfloor day before it, and no hazard or counted notice). Tension context is unusual attention, and with
    `cordon` also a counted notice in the 48 hours before. `same_day` is the sensitivity variant: the day's own
    attention counts too."""
    at = datetime(day.year, day.month, day.day, OUTAGE_HOUR, tzinfo=timezone.utc)
    if tri.hazards(items, cc, at)["significant"]:
        return "natural"
    if cordon is not None and cordon.counted(cc, at):
        return tri.TENSION
    if same_day:
        cur = wf.get((cc, str(day)))
        if cur is not None and cur["unusual"] and not NOT_SCORED & set(cur["quality"]):
            return tri.TENSION
    prev = wf.get((cc, str(day - timedelta(days=1))))
    if prev is None:
        # A country absent from a complete day had no articles that day: nothing unusual, but only if the day exists.
        return "open" if str(day - timedelta(days=1)) in wf.get("_days", set()) else "incomplete"
    if NOT_SCORED & set(prev["quality"]):
        return "incomplete"
    return tri.TENSION if prev["unusual"] else "open"


def _shares(c):
    n = sum(c.values())
    return {k: round(c.get(k, 0) / n, 4) if n else None for k in READINGS}


def replay(shutdowns, items, wf, year, same_day=False, cordon=None):
    by_cc = defaultdict(list)
    for s in shutdowns:
        by_cc[s["country"]].append(s)
    excluded = defaultdict(set)
    for cc, ss in by_cc.items():
        for s in ss:
            end = s["end"] if s["end"] and s["end"] >= s["start"] else s["start"] + timedelta(days=OPEN_ENDED_D)
            d = s["start"] - timedelta(days=CONTROL_BUFFER_D)
            while d <= end + timedelta(days=CONTROL_BUFFER_D):
                excluded[cc].add(d)
                d += timedelta(days=1)
    control = {cc: Counter() for cc in by_cc}
    d = date(year, 1, 1)
    while d.year == year:       # day by day, so Cordon's items are rebuilt once a day
        for cc in by_cc:
            if d not in excluded[cc]:
                control[cc][read_day(cc, d, items, wf, same_day, cordon)] += 1
        d += timedelta(days=1)
    hits, hits_m = Counter(), Counter()
    groups = {"scope": defaultdict(Counter), "cause": defaultdict(Counter), "country": defaultdict(Counter)}
    groups_m = {g: defaultdict(Counter) for g in groups}
    gmatched = {g: defaultdict(Counter) for g in groups}
    matched = Counter()
    for s in shutdowns:
        r = read_day(s["country"], s["start"], items, wf, same_day, cordon)
        s["reading"] = r
        hits[r] += 1
        for g in groups:
            groups[g][s[g]][r] += 1
        if not sum(control[s["country"]].values()):
            continue    # no control day in its country: left out of the matched comparison
        hits_m[r] += 1
        cs = _shares(control[s["country"]])
        for g in groups:
            groups_m[g][s[g]][r] += 1
            for k in READINGS:
                gmatched[g][s[g]][k] += cs[k]
        for k in READINGS:
            matched[k] += cs[k]
    n_m = sum(hits_m.values())

    def grouped(g):
        out = {}
        for key, c in sorted(groups[g].items(), key=lambda kv: -sum(kv[1].values())):
            cm = groups_m[g][key]
            n_gm = sum(cm.values())
            out[key] = {"shutdowns": sum(c.values()), "read": dict(c), "shares": _shares(c),
                        "matched": {"shutdowns": n_gm, "shares": _shares(cm) if n_gm else None,
                                    "control_shares": ({k: round(gmatched[g][key][k] / n_gm, 4) for k in READINGS}
                                                       if n_gm else None)}}
        return out
    n = len(shutdowns)
    countries = []
    for cc, c in sorted(groups["country"].items(), key=lambda kv: -sum(kv[1].values())):
        countries.append({"country": cc, "name": tri.country_name(cc), "shutdowns": sum(c.values()),
                          "shutdowns_read": dict(c), "control_days": sum(control[cc].values()),
                          "control_read": dict(control[cc]), "control_shares": _shares(control[cc])})
    return {
        "shutdowns": n,
        "distinct_country_days": len({(s["country"], s["start"]) for s in shutdowns}),
        "shutdowns_read": dict(hits), "shutdowns_shares": _shares(hits),
        "matched": {"how": "shutdowns in countries with at least one control day, each set against its own "
                           "country's control shares",
                    "shutdowns": n_m, "left_out": sorted({s["country"] for s in shutdowns
                                                         if not sum(control[s["country"]].values())}),
                    "shutdowns_shares": _shares(hits_m) if n_m else None,
                    "control_shares": {k: round(matched[k] / n_m, 4) for k in READINGS} if n_m else None},
        "control_days": sum(sum(c.values()) for c in control.values()),
        "by_country": countries,
        "by_scope": grouped("scope"),
        "by_cause": grouped("cause"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stop", required=True)
    ap.add_argument("--elements", required=True)
    ap.add_argument("--watchfloor", required=True)
    ap.add_argument("--cordon", nargs="*", default=[],
                    help="Cordon history datasets: adds the third layer, read for the countries they cover")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--out", default=str(ROOT / "history" / "replays"))
    a = ap.parse_args(argv)
    stop_sha = hashlib.sha256(Path(a.stop).read_bytes()).hexdigest()
    shutdowns = load_stop(a.stop, a.year)
    eman, erows, esha = load_rows(a.elements)
    wman, wrows, wsha = load_rows(a.watchfloor)
    items = hel.to_items(erows)
    wf = {(r["place"], r["at"]): r for r in wrows}
    wf["_days"] = {r["at"] for r in wrows if not {"partial-day", "unscored-day"} & set(r["quality"])}
    write_labels_manifest(a.stop, a.year, shutdowns, stop_sha, ROOT / "history")
    if a.cordon:
        return main_three(a, shutdowns, stop_sha, items, wf, (eman, esha), (wman, wsha))
    res = replay(shutdowns, items, wf, a.year)
    res_same = replay([dict(s) for s in shutdowns], items, wf, a.year, same_day=True)
    now = tri.iso(datetime.now(timezone.utc))
    run_id = f"stop-{a.year}-two-layer-{now[:10]}"
    man = {
        "contract": "cop-layer-history/1", "run_id": run_id,
        "title": f"Triage replayed on Access Now's documented {a.year} shutdowns: Elements and Watchfloor",
        "inputs": [{"dataset_id": eman["dataset_id"], "sha256": esha}, {"dataset_id": wman["dataset_id"], "sha256": wsha}],
        "labels": {"source": "Access Now #KeepItOn STOP dataset (public sheet, first tab, the 2025 dataset)",
                   "file": str(a.stop), "sha256": stop_sha, "licence": "No data licence stated; counts only",
                   "attribution": "Access Now, #KeepItOn STOP", "rows_used": len({s["row"] for s in shutdowns}),
                   "redistribution": "aggregates"},
        "controls": {"how": f"every {a.year} day in each shutdown country with no STOP shutdown starting, running or "
                            f"ending within {CONTROL_BUFFER_D} days (open-ended shutdowns taken as {OPEN_ENDED_D} "
                            "days)", "seed": None},
        "layers_replayed": ["elements", "watchfloor (country items)"],
        "layers_not_replayed": ["cordon", "watchfloor zone items"],
        "method": {"code": "tools/stop_replay.py",
                   "commit": "uncommitted sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
                   "reading_rule": "natural if a significant hazard (tri.hazards); else tension context if the "
                                   "country was on a Watchfloor Rising list the day before; else open; incomplete "
                                   "when that Watchfloor day is missing, partial or unscored (no country had a "
                                   "28-day z)",
                   "outage_hour_utc": OUTAGE_HOUR},
        "results": res,
        "sensitivity": [{"id": "same-day-attention", "label": SAME_DAY_LABEL, "results": res_same}],
        "caveats": CAVEATS,
        "redistribution": "aggregates",
        "built_at": now,
    }
    out = Path(a.out) / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(man, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"{res['shutdowns']} shutdowns ({res['distinct_country_days']} country-days), "
          f"{res['control_days']} control days -> {out}")
    for name, r in (("triage's rule", res), ("same-day attention (sensitivity)", res_same)):
        s, mm = r["shutdowns_shares"], r["matched"]
        print(f" {name}; matched on {mm['shutdowns']} shutdowns (left out, no control days: {mm['left_out']})")
        for k in READINGS:
            print(f"  {k:16} all shutdowns {s[k] or 0:6.1%}   matched shutdowns {mm['shutdowns_shares'][k] or 0:6.1%}"
                  f"   their control days {mm['control_shares'][k] or 0:6.1%}")


CAVEATS_THREE = [
    "Run only for shutdowns in the 25 countries Cordon's history covers (FCDO's 24 and the US embassies', with "
    "Israel and Palestine apart); elsewhere Cordon is not replayed. Both runs, two layers and three, are over the "
    "same shutdowns and control days.",
    "Cordon's history: FCDO travel advice (24 countries) and US embassy alerts (23; Iran has no US embassy, so it has "
    "no alerts here, which means no data, not no alerts) are dense; State's advisory levels (39 countries) are weak, "
    "rebuilt from Wayback captures of State's RSS with 85% of the window in stretches over 7 days between captures.",
    "Canada's and New Zealand's travel advice, which live triage also reads, are not rebuilt; nor are State's "
    "departure-posture items. Israel's and Palestine's State levels are unknown (State's RSS never lists Israel).",
    "A counted notice reads tension context even when Watchfloor's day is incomplete; live triage would read the "
    "outage incomplete until every layer has data.",
] + [c for c in CAVEATS if not c.startswith("Two of triage's three layers")]


def main_three(a, shutdowns, stop_sha, items, wf, el, wl):
    cordon = Cordon(a.cordon)
    sub_s = [s for s in shutdowns if s["country"] in cordon.places]
    now = tri.iso(datetime.now(timezone.utc))
    two = replay([dict(s) for s in sub_s], items, wf, a.year)
    three = replay([dict(s) for s in sub_s], items, wf, a.year, cordon=cordon)
    run_id = f"stop-{a.year}-three-layer-{now[:10]}"
    man = {
        "contract": "cop-layer-history/1", "run_id": run_id,
        "title": f"Triage replayed on Access Now's documented {a.year} shutdowns: Elements, Watchfloor and Cordon, "
                 "beside the two-layer reading of the same shutdowns",
        "inputs": [{"dataset_id": el[0]["dataset_id"], "sha256": el[1]},
                   {"dataset_id": wl[0]["dataset_id"], "sha256": wl[1]}] + cordon.manifests,
        "labels": {"source": "Access Now #KeepItOn STOP dataset (public sheet, first tab, the 2025 dataset)",
                   "file": str(a.stop), "sha256": stop_sha, "licence": "No data licence stated; counts only",
                   "attribution": "Access Now, #KeepItOn STOP", "rows_used": len({s["row"] for s in sub_s}),
                   "redistribution": "aggregates"},
        "places": sorted(cordon.places),
        "controls": {"how": f"every {a.year} day in each shutdown country with no STOP shutdown starting, running or "
                            f"ending within {CONTROL_BUFFER_D} days (open-ended shutdowns taken as {OPEN_ENDED_D} "
                            "days)", "seed": None},
        "layers_replayed": ["elements", "watchfloor (country items)",
                            "cordon (State advisories, FCDO advice, US embassy alerts)"],
        "layers_not_replayed": ["watchfloor zone items", "cordon canada", "cordon safetravel",
                                "State departure-posture items"],
        "method": {"code": "tools/stop_replay.py",
                   "commit": "uncommitted sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
                   "reading_rule": "natural if a significant hazard (tri.hazards); else tension context if "
                                   "tri.notices counts a Cordon notice in the 48 hours before, or the country was on "
                                   "a Watchfloor Rising list the day before; else open; incomplete when that "
                                   "Watchfloor day is missing, partial or unscored and nothing else counted",
                   "outage_hour_utc": OUTAGE_HOUR},
        "results": {"two_layer": two, "three_layer": three},
        "caveats": CAVEATS_THREE,
        "redistribution": "aggregates",
        "built_at": now,
    }
    out = Path(a.out) / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(man, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"{three['shutdowns']} shutdowns in Cordon's {len(cordon.places)} countries, {three['control_days']} "
          f"control days -> {out}")
    for name, r in (("two layers", two), ("three layers", three)):
        s, mm = r["shutdowns_shares"], r["matched"]
        print(f" {name}; matched on {mm['shutdowns']} shutdowns (left out: {mm['left_out']})")
        for k in READINGS:
            print(f"  {k:16} all shutdowns {s[k] or 0:6.1%}   matched shutdowns {mm['shutdowns_shares'][k] or 0:6.1%}"
                  f"   their control days {mm['control_shares'][k] or 0:6.1%}")


if __name__ == "__main__":
    sys.exit(main())
