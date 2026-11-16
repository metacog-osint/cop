"""Rebuild UK FCDO travel-advice updates and their "advise against" statements by date, for the STOP replay
(cop-layer-history/1).

Two sources, joined:
- the GOV.UK Content API's change history for each country (every update, its UTC time and the FCDO's own note;
  OGL v3). It has no past statements: `alert_status` is only what is in force now.
- Internet Archive captures of each country's advice page (www.gov.uk/foreign-travel-advice/<slug>), whose warning
  box names the statements in force on the capture date ("FCDO advises against all travel to parts of Myanmar").

Each capture's statements are read (`parse_warning`); where two captures differ, the change happened in an update
between them, and the update whose note speaks of advice is the change (`attribute`). When several updates in the
interval could be it, the first is taken and the row is flagged `estimated-date`. A row is one update, carrying the
statements in force after it. `to_items` turns rows back into the Cordon items triage reads.

    python tools/history_cordon_fcdo.py --start 2024-12-01 --end 2025-12-31 [--countries MM,IN]
Writes history/cordon/<dataset_id>/manifest.json and rows.jsonl. Standard-library Python only.
"""
import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_cordon_common as hc  # noqa: E402

API = "https://www.gov.uk/api/content/foreign-travel-advice/{slug}"
PAGE = "https://www.gov.uk/foreign-travel-advice/{slug}"
GAP_DAYS = 7
LEAD_IN_DAYS = 31          # rows from this long before the start are kept, for "standing" and direction lookups
FCDO_ORDER = ["avoid_all_travel_to_whole_country", "avoid_all_but_essential_travel_to_whole_country",
              "avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts"]
WORDS = {"avoid_all_travel_to_whole_country": "all travel to the whole country",
         "avoid_all_but_essential_travel_to_whole_country": "all but essential travel to the whole country",
         "avoid_all_travel_to_parts": "all travel to parts",
         "avoid_all_but_essential_travel_to_parts": "all but essential travel to parts"}
SOURCES = [
    {"id": "fcdo-change-history", "name": "GOV.UK Content API, foreign travel advice, change history",
     "url": "https://www.gov.uk/api/content/foreign-travel-advice", "licence": "Open Government Licence v3.0",
     "attribution": "Contains public sector information licensed under the Open Government Licence v3.0 "
                    "(UK Foreign, Commonwealth & Development Office)", "how": "api"},
    {"id": "fcdo-page-wayback", "name": "GOV.UK foreign travel advice pages, as captured by the Internet Archive",
     "url": "https://www.gov.uk/foreign-travel-advice", "licence": "Open Government Licence v3.0",
     "attribution": "Contains public sector information licensed under the Open Government Licence v3.0 "
                    "(UK Foreign, Commonwealth & Development Office); captures by the Internet Archive",
     "how": "wayback"},
]
ADVICE_NOTE = re.compile(r"(?i)advis|advice against|all but essential|all travel|travel to|do not travel|"
                         r"warning|risk level|avoid")
STRONG_NOTE = re.compile(r"(?i)(advises? against|advice against|all but essential|all travel|no longer advis|"
                         r"now advis|removed.*advice|advice.*(removed|changed|updated)|summary)")


# ---- reading a capture ---------------------------------------------------------------------------------------

def parse_warning(page_html, names):
    """(alert_status list, flags) from one capture of a country's advice page, or raises ValueError when the page is
    not a travel advice page. names: the country's name and synonyms, to tell the whole country from parts.

    The warning box ("FCDO advises against ...") is read, not the "Areas where FCDO advises against travel" list
    below it. flags: "parsed-best-effort" when a clause could not be placed with certainty."""
    if "foreign-travel-advice" not in page_html and "GOV.UK" not in page_html:
        raise ValueError("not a GOV.UK page")
    if "Warnings and insurance" not in page_html and "advises against" not in page_html:
        raise ValueError("not a travel advice page")
    boxes = re.findall(r'govuk-warning-text__text">(.*?)</strong>', page_html, re.S)
    text = " ".join(hc.strip_tags(b) for b in boxes)
    if "advises against" not in text:
        # older layouts: the statement sits in the summary paragraph
        m = re.search(r"(?s)Warnings and insurance.{0,4000}", hc.strip_tags(re.sub(r"(?s)<(script|style).*?</\1>", "",
                                                                                 page_html)))
        flat = m.group(0) if m else ""
        flat = re.split(r"Areas where FCDO advises", flat)[0]
        text = flat if "advises against" in flat else ""
    flags = []
    found = set()
    low_names = [n.lower() for n in names]
    for clause in re.finditer(r"(?i)advises against (all but essential travel|all travel)\s+to\s+([^.;]*)", text):
        kind = "avoid_all_but_essential_travel" if "essential" in clause.group(1).lower() else "avoid_all_travel"
        # one clause can list two: "... all travel to parts of X and all but essential travel to parts of X"
        target = clause.group(2)
        head = re.split(r"(?i)\s+and\s+(?=all (?:but essential )?travel)", target)[0].strip(" ,:")
        h = head.lower()
        if re.match(r"(parts of|some parts|some areas|areas of|areas within|certain)", h):
            where = "parts"
        elif any(h.startswith(n) or h.startswith("the " + n) for n in low_names) or h in ("the country", "all of"):
            where = "whole_country"
        elif any(n in h for n in low_names):
            where = "whole_country"
            flags.append("parsed-best-effort")
        else:
            where = "parts"
            flags.append("parsed-best-effort")
        found.add(f"{kind}_to_{where}")
        # the same sentence may carry a second statement after "and"
        tail = re.search(r"(?i)\band\s+(all but essential travel|all travel)\s+to\s+(.*)$", target)
        if tail:
            k2 = "avoid_all_but_essential_travel" if "essential" in tail.group(1).lower() else "avoid_all_travel"
            t2 = tail.group(2).lower()
            w2 = "parts" if re.match(r"(parts of|some|areas)", t2) else "whole_country"
            if not re.match(r"(parts of|some|areas)", t2) and not any(t2.startswith(n) for n in low_names):
                flags.append("parsed-best-effort")
            found.add(f"{k2}_to_{w2}")
    return sorted(found, key=lambda x: FCDO_ORDER.index(x) if x in FCDO_ORDER else 99), sorted(set(flags))


def words(statuses):
    if statuses is None:
        return "advice status not yet read"
    s = sorted(statuses, key=lambda x: FCDO_ORDER.index(x) if x in FCDO_ORDER else 99)
    return "advises against " + " and ".join(WORDS.get(x, x.replace("_", " ")) for x in s) if s \
        else "no advice against travel"


# ---- joining captures and updates ----------------------------------------------------------------------------

def _t(s):
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def attribute(entries, captures, start):
    """Rows' statuses from the API's change entries and the page captures.

    entries: [{"at": ISO UTC, "note": str}] any order. captures: [(stamp, statuses list, flags list)] sorted by stamp,
    statuses None when a capture could not be read (skipped). Returns [{at, note, alert_status, changed, from_status,
    quality, refs}] sorted by time, covering entries from start - LEAD_IN_DAYS on. quality: estimated-date (the change
    could be any of several updates), capture-gap (the statements are carried from a capture more than GAP_DAYS away
    while the captures either side of the update disagree), level-unknown (no capture before it), parsed-best-effort."""
    caps = [(hc.stamp_to_iso(s), st, fl, s) for s, st, fl in captures if st is not None]
    caps.sort()
    ents = sorted(({"at": e["at"], "note": e.get("note") or ""} for e in entries), key=lambda e: e["at"])
    # 1. the update that made each change between consecutive captures
    change_at = {}            # entry index -> (from, to, bracket, n_candidates)
    for (ta, sa, fa, stamp_a), (tb, sb, fb, stamp_b) in zip(caps, caps[1:]):
        if sa == sb:
            continue
        inside = [i for i, e in enumerate(ents) if ta < e["at"] <= tb]
        strong = [i for i in inside if STRONG_NOTE.search(ents[i]["note"])]
        weak = [i for i in inside if ADVICE_NOTE.search(ents[i]["note"])]
        pool = strong or weak or inside
        pick = pool[0] if pool else None
        change_at[pick if pick is not None else ("capture", tb)] = (sa, sb, (ta, tb), len(pool),
                                                                    (stamp_a, stamp_b))
    # 2. walk the entries in time order carrying the statuses
    rows = []
    cur = caps[0][1] if caps else None
    cur_from = None
    first_cap_t = caps[0][0] if caps else None
    synth = [(k[1], v) for k, v in change_at.items() if isinstance(k, tuple)]
    for i, e in enumerate(ents):
        ch = change_at.get(i)
        quality, refs = [], []
        if first_cap_t is None or e["at"] < first_cap_t:
            status = None
            quality.append("level-unknown")
        else:
            if ch:
                cur_from, cur = ch[0], ch[1]
                if ch[3] > 1 or not (STRONG_NOTE.search(e["note"]) or ADVICE_NOTE.search(e["note"])):
                    quality.append("estimated-date")
                refs = [ch[4][0], ch[4][1]]
            status = cur
            # nearest captures either side
            before = [c for c in caps if c[0] <= e["at"]]
            after = [c for c in caps if c[0] > e["at"]]
            if not ch and before:
                refs = [before[-1][3]]
                d_before = (_t(e["at"]) - _t(before[-1][0])).days
                d_after = (_t(after[0][0]) - _t(e["at"])).days if after else None
                differs = bool(after) and before[-1][1] != after[0][1]
                if (differs or not after) and d_before > GAP_DAYS and (d_after is None or d_after > GAP_DAYS):
                    quality.append("capture-gap")
            for c in ([before[-1]] if before else []):
                if "parsed-best-effort" in c[2]:
                    quality.append("parsed-best-effort")
        rows.append({"at": e["at"], "note": e["note"], "alert_status": status, "changed": bool(ch),
                     "from_status": cur_from if ch else None, "quality": sorted(set(quality)), "refs": refs})
    # a change seen in captures with no update in the interval at all: a row at the later capture
    for tb, v in synth:
        sa, sb, (ta, _), _n, (stamp_a, stamp_b) = v
        rows.append({"at": tb, "note": "", "alert_status": sb, "changed": True, "from_status": sa,
                     "quality": ["estimated-date", "no-update-in-history"], "refs": [stamp_a, stamp_b]})
    rows.sort(key=lambda r: r["at"])
    return [r for r in rows if r["at"] >= (datetime.combine(start, datetime.min.time(), timezone.utc)
                                           - timedelta(days=LEAD_IN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")]


def make_rows(country, entries, captures, start, end, now):
    cc, name, slug, _host = country
    out = []
    end_iso = (datetime.combine(end, datetime.min.time(), timezone.utc) + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    for r in attribute(entries, captures, start):
        if r["at"] >= end_iso:
            continue
        quality = list(r["quality"])
        if r["at"] < start.isoformat():
            quality.append("lead-in")
        out.append({
            "place": cc, "at": r["at"], "precision": "minute", "source_id": "fcdo-change-history",
            "source_ref": API.format(slug=slug), "retrieved_at": now, "quality": sorted(set(quality)),
            "item_id": f"cordon:fcdo:{slug}", "kind": "fcdo", "slug": slug, "name": name, "note": r["note"],
            "alert_status": r["alert_status"], "alert_status_changed": r["changed"],
            "from_alert_status": r["from_status"],
            "status_refs": [hc.wayback_url(s, PAGE.format(slug=slug)) for s in r["refs"]],
            "countries": [cc]})
    return out


# ---- items ---------------------------------------------------------------------------------------------------

def to_items(rows, at=None):
    """The FCDO items Cordon would have carried, from rows: one per country, the latest update on or before `at` (a
    datetime; None: the latest row). tier is anomaly when the statements changed in the 30 days before `at`, as
    Cordon marks it. An item exists, as live, only when advice against travel is in force, the statements changed in
    the last 30 days, or the advice was updated in the last 72 hours."""
    by = {}
    for r in sorted((r for r in rows if r.get("kind") == "fcdo"), key=lambda r: r["at"]):
        t = _t(r["at"])
        if at is not None and t > at:
            continue
        by.setdefault((r["item_id"], r["place"]), []).append((t, r))
    items = []
    for (item_id, place), seq in by.items():
        t, r = seq[-1]
        horizon = at or t
        changed = [x for x in seq if x[1]["alert_status_changed"] and (horizon - x[0]) <= timedelta(days=30)]
        is_new = (horizon - t) <= timedelta(hours=72)
        st = r["alert_status"]
        if not st and not changed and not is_new:
            continue
        m = {"alert_status": st, "advice": words(st), "change": r["note"] or None, "updated": r["at"], "new": is_new}
        if changed:
            m["changed_at"] = changed[-1][1]["at"]
            m["from"] = changed[-1][1]["from_alert_status"]
        items.append({"type": "Feature", "geometry": None, "properties": {
            "id": item_id, "title": f"UK FCDO, {r['name']}: {words(st)}" + (f" (updated: {r['note']})" if is_new
                                                                              and r["note"] else ""),
            "tier": "anomaly" if changed else "routine", "data_as_of": r["at"], "countries": [place],
            "cop_program": "cordon", "cop_stale": False, "measures": m}})
    return items


# ---- collection and manifest ----------------------------------------------------------------------------------

def fetch_entries(client, slug):
    """([{at, note}], country JSON) from the API, or (None, error)."""
    doc, st = client.json(API.format(slug=slug))
    if doc is None:
        return None, st
    hist = (doc.get("details") or {}).get("change_history") or []
    out = [{"at": _t(h["public_timestamp"]).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": h.get("note") or ""}
           for h in hist if h.get("public_timestamp")]
    return out, doc


def fetch_captures(client, slug, names, start, end, max_caps=90):
    """[(stamp, statuses|None, flags)] for the page, from one capture before the start through the end plus a margin."""
    lo = (start - timedelta(days=120)).strftime("%Y%m%d")
    hi = (end + timedelta(days=30)).strftime("%Y%m%d")
    rows = hc.cdx(client, PAGE.format(slug=slug)[8:], **{"from": lo, "to": hi}, filter="statuscode:200",
                  collapse="timestamp:8")
    if len(rows) > max_caps:      # thin to every Nth day, keeping the first and last
        step = len(rows) / max_caps
        rows = [rows[int(i * step)] for i in range(max_caps)] + [rows[-1]]
    out, failed = [], []
    for r in rows:
        body, st = hc.wayback_raw(client, r["timestamp"], PAGE.format(slug=slug))
        try:
            if body is None or st != 200:
                raise ValueError(st)
            statuses, flags = parse_warning(body.decode("utf-8", "replace"), names)
            out.append((r["timestamp"], statuses, flags))
        except ValueError as e:
            out.append((r["timestamp"], None, []))
            failed.append({"stamp": r["timestamp"], "why": str(e)})
    return out, failed


def validate(client, country, doc, names):
    """Compare the parser with the API's current alert_status on the newest capture: (agree, stamp, parsed, api)."""
    slug = country[2]
    rows = hc.cdx(client, PAGE.format(slug=slug)[8:], **{"from": "20261001"}, filter="statuscode:200",
                  collapse="timestamp:8")
    if not rows:
        return None
    r = rows[-1]
    body, st = hc.wayback_raw(client, r["timestamp"], PAGE.format(slug=slug))
    if not body:
        return None
    try:
        parsed, _ = parse_warning(body.decode("utf-8", "replace"), names)
    except ValueError:
        return None
    api = sorted(set((doc.get("details") or {}).get("alert_status") or []),
                 key=lambda x: FCDO_ORDER.index(x) if x in FCDO_ORDER else 99)
    return {"slug": slug, "stamp": r["timestamp"], "parsed": parsed, "api_now": api}


def manifest(rows, start, end, now, code_sha, per_country, validation):
    covered = [c for c, v in per_country.items() if v.get("entries")]
    gaps = []
    for c, v in per_country.items():
        if not v.get("entries"):
            gaps.append({"from": str(start), "to": str(end), "places": c, "reason": v.get("error", "no data")})
            continue
        if v["captures"] == 0:
            gaps.append({"from": str(start), "to": str(end), "places": c,
                         "reason": "no readable page capture: update dates are exact, statements unknown"})
        elif v["max_gap_days"] > GAP_DAYS:
            gaps.append({"from": v["gap_from"], "to": v["gap_to"], "places": c,
                         "reason": f"longest stretch without a page capture is {v['max_gap_days']} days; a "
                                   "statement change in it is dated to the best-matching update (estimated-date)"})
    ok = [v for v in validation if v and v["parsed"] == v["api_now"]]
    return {
        "contract": "cop-layer-history/1",
        "dataset_id": f"cordon-fcdo-advice-{start}-{end}",
        "layer": "cordon",
        "title": "UK FCDO travel-advice updates and the statements in force, from GOV.UK and Wayback captures",
        "description": "One row per FCDO update to a country's travel advice, with the FCDO's own note, the "
                       "'advise against' statements in force after it, and whether it changed them. For replaying "
                       "triage's FCDO notices (kind fcdo; counted only where the statements changed).",
        "sources": SOURCES,
        "derived_from": [],
        "method": {"code": "tools/history_cordon_fcdo.py", "commit": code_sha,
                   "params": {"api": API, "page": PAGE, "gap_days": GAP_DAYS, "lead_in_days": LEAD_IN_DAYS,
                              "countries": covered,
                              "validation": {"checked": len([v for v in validation if v]), "agree": len(ok),
                                             "disagree": [v for v in validation if v and v not in ok]}},
                   "matches_live": False,
                   "differences": [
                       "Live Cordon marks an item anomaly when its own diff saw the statements change; here the "
                       "change is read from page captures and dated to an update's time, so an update that is "
                       "not the real cause can carry the flag (estimated-date).",
                       "A change that went in and out between two captures is missed.",
                       "Statements are read from the page's warning text (whole country or parts, all travel or all "
                       "but essential), not from alert_status, which the API gives only for today.",
                       "The change history lists only updates the FCDO published as such; the API may omit older "
                       "notes."]},
        "coverage": {"start": str(start), "end": str(end), "time_precision": "minute",
                     "places": sorted(covered), "place_code": "ISO 3166-1 alpha-2"},
        "gaps": gaps,
        "biases": ["Update dates and times are exact (the FCDO's own); the statements by date are only as good as "
                   "the page captures around them.",
                   "Whole-country versus parts is read from wording; a destination named neither as the country nor "
                   "as parts is read as parts and flagged parsed-best-effort.",
                   "Countries the Archive captured rarely have long carried-forward stretches (capture-gap)."],
        "rows": hc.row_stats(rows),
        "row_schema": {
            "place": "ISO alpha-2", "at": "update time, UTC", "precision": "minute",
            "source_id": "fcdo-change-history", "source_ref": "the API URL the entry came from",
            "retrieved_at": "UTC", "quality": "estimated-date, capture-gap, level-unknown, parsed-best-effort, "
                                              "lead-in, no-update-in-history",
            "item_id": "cordon:fcdo:<slug>", "kind": "fcdo", "slug": "GOV.UK slug", "name": "country",
            "note": "the FCDO's own change note", "alert_status": "statements in force after the update, or null",
            "alert_status_changed": "true when this update changed the statements",
            "from_alert_status": "the statements before it, when changed",
            "status_refs": "Wayback captures the statements were read from", "countries": "[place]"},
        "files": [],
        "redistribution": "rows",
        "built_at": now,
        "built_by": "tools/history_cordon_fcdo.py, COP Cordon-history session, 16 Nov 2026",
        "uses": ["replaying triage's FCDO notices for 2025", "base rates of FCDO statement changes per country-day"],
        "not_for": ["a legal or authoritative record of what the FCDO advised on a day"],
        "supersedes": None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2024-12-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--countries", default="", help="comma-separated ISO codes (default: the first 24)")
    ap.add_argument("--extra", action="store_true", help="also the extra countries")
    ap.add_argument("--id-suffix", default="")
    ap.add_argument("--out", default=str(hc.ROOT / "history" / "cordon"))
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    todo = hc.COUNTRIES + (hc.COUNTRIES_EXTRA if a.extra else [])
    if a.countries:
        want = set(a.countries.split(","))
        todo = [c for c in hc.COUNTRIES + hc.COUNTRIES_EXTRA if c[0] in want]
    now = hc.utc_now()
    client = hc.Client()
    rows, per, validation = [], {}, []
    for country in todo:
        cc, name, slug, _ = country
        entries, doc = fetch_entries(client, slug)
        if entries is None:
            per[cc] = {"error": f"GOV.UK API: {doc}"}
            print(cc, "API failed", doc, file=sys.stderr)
            continue
        names = [name.split(" (")[0]] + [((doc.get("details") or {}).get("country") or {}).get("name", "")] + \
                list(((doc.get("details") or {}).get("country") or {}).get("synonyms") or [])
        names = [n for n in names if n] + ["the occupied palestinian territories"] * (cc == "PS")
        caps, failed = fetch_captures(client, slug, names, start, end)
        good = [c for c in caps if c[1] is not None]
        stamps = [hc.stamp_to_iso(c[0])[:10] for c in good]
        mg, g_from, g_to = 0, None, None
        pts = [date.fromisoformat(d) for d in stamps if start - timedelta(days=60) <= date.fromisoformat(d) <=
               end + timedelta(days=30)]
        for x, y in zip(pts, pts[1:]):
            if (y - x).days > mg and y > start and x < end:
                mg, g_from, g_to = (y - x).days, str(max(x, start)), str(min(y, end))
        crow = make_rows(country, entries, caps, start, end, now)
        rows += crow
        per[cc] = {"entries": len(entries), "captures": len(good), "unreadable": len(failed),
                   "max_gap_days": mg, "gap_from": g_from, "gap_to": g_to,
                   "changes": sum(1 for r in crow if r["alert_status_changed"])}
        validation.append(validate(client, country, doc, names))
        print(cc, per[cc], file=sys.stderr)
    rows.sort(key=lambda r: (r["place"], r["at"]))
    man = manifest(rows, start, end, now, hc.code_ref(__file__), per, validation)
    man["method"]["params"]["per_country"] = per
    if a.id_suffix:
        man["dataset_id"] += a.id_suffix
    hc.write_dataset(Path(a.out) / man["dataset_id"], man, rows)
    print(f"{len(rows)} rows, {len(todo)} countries -> {man['dataset_id']}; requests {client.requests}, "
          f"cached {client.hits}")


if __name__ == "__main__":
    sys.exit(main())
