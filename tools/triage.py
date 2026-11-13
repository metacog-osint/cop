"""Outage triage: what the other layers show under each Blackout outage.

A named join (scenario 2 of D:/Projects/_ideas/convergence-scenarios.md), not a count of programs. For every
Blackout outage in a COP snapshot it records what Elements, Cordon and Watchfloor show for that country around
that hour, including "nothing found" and "no current data", which are different answers. It states a reading of
the pattern and drafts the Verdict question. It never states a probability: that is the analyst's.

    python tools/triage.py                              # read the published snapshot, write out/triage/
    python tools/triage.py --snapshot site/data/today.geojson
    python tools/triage.py --snapshot <file.geojson.gz> # an archived day

NOT PUBLISHED. It writes under out/ (git-ignored) and refuses a path inside site/. It is not called by the build.
Review class "open": hazards, outages, notices and press attention; no military activity is placed by this join.

Standard-library Python only.
"""

import argparse
import gzip
import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"
PUBLISHED = "https://cop.theintelligence.company/data/today.geojson"
UA = "tic-cop/0.1 (+https://cop.theintelligence.company)"

JOIN = {"id": "outage-triage", "version": "0.1", "review_class": "open", "published": False}
LAYERS = ("elements", "cordon", "watchfloor")
# Windows, all measured from the outage's own time (its data_as_of).
HAZARD_BEFORE_H = 72     # a hazard that began or was still running this long before the outage can explain it
HAZARD_AFTER_H = 6       # reports of a hazard trail the event a little
HAZARD_NEAR_KM = 300     # a hazard with no country (an offshore earthquake) counts within this of the country
NOTICE_RECENT_DAYS = 14  # an advisory, alert or bulletin dated this recently counts as recent
OONI_WINDOW_H = 72       # the Verdict question's window
# Elements items that are standing background, not an event that takes a network down.
BACKGROUND = {"firms", "usdm", "nifc", "spc"}
CONFIRMED_OUTAGE = "BO-IND-1"   # Blackout: a drop seen by more than one signal
OONI_BLOCKING = "BO-IND-3"      # Blackout: OONI rate at or above its fixed threshold

READINGS = {
    "incomplete": "A layer has no current data, so this outage cannot be triaged yet. Silence is not quiet.",
    "unconfirmed": "Blackout calls this a single-source dip, not a confirmed outage. Context is listed, no reading.",
    "natural": "A significant natural hazard sits under this outage in place and time. A natural cause has support.",
    "deliberate": "No significant hazard under it, and notices or press attention are raised. A deliberate cut is "
                  "worth a call.",
    "open": "No significant hazard, no recent notice and no raised attention. Nothing here separates an accident "
            "from a deliberate cut.",
}


def utc(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(where):
    """A snapshot from a URL, a .geojson file or a .geojson.gz archive copy."""
    if str(where).startswith("http"):
        req = urllib.request.Request(where, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    p = Path(where)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(p.read_text(encoding="utf-8"))


_RINGS = None


def _rings(cc):
    global _RINGS
    if _RINGS is None:
        data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
        _RINGS = {c["cc"]: c["rings"] for c in data["countries"]}
    return _RINGS.get(cc) or []


def _km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def km_to_country(geometry, cc):
    """Distance in km from a Point geometry to the nearest outline vertex of the country, or None."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None
    lon, lat = geometry["coordinates"][:2]
    best = None
    for ring in _rings(cc):
        for x, y in ring:
            d = _km(lat, lon, y, x)
            if best is None or d < best:
                best = d
    return best


def _kind(item_id):
    parts = str(item_id).split(":")
    return parts[1] if len(parts) > 1 else ""


def _row(p, **extra):
    row = {"id": p.get("id"), "title": p.get("title"), "tier": p.get("tier"), "data_as_of": p.get("data_as_of"),
           "link": p.get("link")}
    row.update(extra)
    return row


def _interval(p):
    """(start, end) of what an item describes, from its own fields."""
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    at = utc(p.get("data_as_of"))
    start = utc(m.get("from")) or at
    end = utc(m.get("to")) or utc(p.get("valid_until")) or at
    if start and end and end < start:
        end = start
    return start, end


def hazards(features, cc, at):
    """Elements items under the outage: in or near the country, overlapping the hazard window."""
    lo, hi = at - timedelta(hours=HAZARD_BEFORE_H), at + timedelta(hours=HAZARD_AFTER_H)
    significant, minor, background = [], [], []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "elements":
            continue
        km = None
        if cc not in (p.get("countries") or []):
            if p.get("countries"):
                continue
            km = km_to_country(f.get("geometry"), cc)
            if km is None or km > HAZARD_NEAR_KM:
                continue
        start, end = _interval(p)
        if not start or end < lo or start > hi:
            continue
        row = _row(p, hours_before_outage=round((at - start).total_seconds() / 3600, 1),
                   km_from_country=None if km is None else round(km))
        if _kind(p.get("id")) in BACKGROUND:
            background.append(row)
        elif p.get("tier") in ("anomaly", "warning"):
            significant.append(row)
        else:
            minor.append(row)
    return {"significant": significant, "minor": minor, "background": background}


def notices(features, cc, at):
    """Cordon items for the country, each marked recent or standing against the outage's time."""
    lo = at - timedelta(days=NOTICE_RECENT_DAYS)
    recent, standing = [], []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "cordon" or cc not in (p.get("countries") or []):
            continue
        dated = utc(p.get("data_as_of"))
        m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
        row = _row(p, kind=_kind(p.get("id")), level=m.get("level"))
        (recent if dated and dated >= lo else standing).append(row)
    return {"recent": recent, "standing": standing}


def attention(features, cc, at):
    """Watchfloor items naming the country, and whether that data reaches the outage's hour."""
    raised, routine, newest = [], [], None
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "watchfloor":
            continue
        dated = utc(p.get("data_as_of"))
        if dated and (newest is None or dated > newest):
            newest = dated
        if cc not in (p.get("countries") or []):
            continue
        (raised if p.get("tier") in ("anomaly", "warning") else routine).append(_row(p, kind=_kind_wf(p.get("id"))))
    return {"raised": raised, "routine": routine,
            "data_reaches_outage": bool(newest and newest >= at),
            "newest_data": iso(newest) if newest else None}


def _kind_wf(item_id):
    parts = str(item_id).split(":")
    return parts[2] if len(parts) > 2 else ""


def layer_state(snapshot, features, pid):
    """fresh, late, stale or missing, from the snapshot's own health block and stale marks."""
    block = (snapshot.get("cop_programs") or {}).get(pid)
    if block and block.get("state"):
        return block["state"]
    mine = [f for f in features if f["properties"].get("cop_program") == pid]
    if not mine:
        return "missing"
    return "stale" if all(f["properties"].get("cop_stale") for f in mine) else "fresh"


def triage(snapshot):
    """One record per Blackout outage in the snapshot."""
    features = [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]
    states = {pid: layer_state(snapshot, features, pid) for pid in LAYERS + ("blackout",)}
    ooni = {}
    for f in features:
        p = f["properties"]
        if p.get("cop_program") == "blackout" and p.get("indicator_id") == OONI_BLOCKING and not p.get("cop_stale"):
            for cc in p.get("countries") or []:
                ooni[cc] = _row(p)
    records = []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "blackout" or _kind(p.get("id")) != "outage" or p.get("cop_stale"):
            continue
        at = utc(p.get("data_as_of"))
        for cc in p.get("countries") or []:
            if not at:
                continue
            haz, note, att = hazards(features, cc, at), notices(features, cc, at), attention(features, cc, at)
            dead = [pid for pid in LAYERS if states[pid] in ("stale", "missing")]
            confirmed = p.get("indicator_id") == CONFIRMED_OUTAGE
            if dead:
                reading = "incomplete"
            elif not confirmed:
                reading = "unconfirmed"
            elif haz["significant"]:
                reading = "natural"
            elif note["recent"] or att["raised"]:
                reading = "deliberate"
            else:
                reading = "open"
            until = at + timedelta(hours=OONI_WINDOW_H)
            blocked_now = cc in ooni
            records.append({
                "join": JOIN["id"], "country": cc,
                "outage": _row(p, confirmed=confirmed, indicator_id=p.get("indicator_id"), summary=p.get("summary")),
                "layers": {
                    "elements": {"state": states["elements"], **haz,
                                 "found": bool(haz["significant"] or haz["minor"])},
                    "cordon": {"state": states["cordon"], **note, "found": bool(note["recent"])},
                    "watchfloor": {"state": states["watchfloor"], **att, "found": bool(att["raised"])},
                },
                "no_current_data": dead,
                "ooni_now": ooni.get(cc),
                "reading": reading, "reading_text": READINGS[reading],
                "caveats": ([] if att["data_reaches_outage"] or "watchfloor" in dead else
                            [f"Watchfloor's newest data ({att['newest_data']}) ends before this outage, so it shows "
                             "attention before the outage, not during it. Run again after its next day is in."]),
                "verdict_draft": {
                    "registered": False, "probability": None, "approved_by": None,
                    "question": f"Will OONI confirm blocking in {cc} within {OONI_WINDOW_H} hours of the outage "
                                f"of {iso(at)}?",
                    "window_ends": iso(until),
                    "rule_draft": f"Yes if Blackout's OONI reading for {cc} is at or above its {OONI_BLOCKING} "
                                  f"threshold for a day inside the window ending {iso(until)}. No otherwise.",
                    "caution": ("OONI is already at or above the threshold here, so this question is settled "
                                "before it is asked. Ask about a rise instead, or skip it.") if blocked_now else None,
                },
            })
    records.sort(key=lambda r: (r["reading"] in ("incomplete", "unconfirmed"), r["outage"]["data_as_of"] or ""),
                 reverse=False)
    return {"join": JOIN, "snapshot_generated_at": snapshot.get("generated_at"), "layer_states": states,
            "windows": {"hazard_before_hours": HAZARD_BEFORE_H, "hazard_after_hours": HAZARD_AFTER_H,
                        "hazard_near_km": HAZARD_NEAR_KM, "notice_recent_days": NOTICE_RECENT_DAYS,
                        "ooni_window_hours": OONI_WINDOW_H},
            "records": records}


def attach_rates(doc, rates):
    """Put the background rate beside every reading that has one. rates: triage_rates.py's document, or None."""
    if not rates:
        doc["base_rates"] = None
        for r in doc["records"]:
            r["base_rate"] = None
        return doc
    b = rates["background"]
    doc["base_rates"] = {"country_days": b["country_days"], "would_read": b["would_read"],
                         "archive": rates["archive"], "cautions": rates["cautions"]}
    for r in doc["records"]:
        share = b["would_read"].get(r["reading"])
        if share is None:
            r["base_rate"] = None
            continue
        days = b.get("by_country_days") or 0
        own = (b.get("by_country") or {}).get(r["country"], {"natural": 0.0, "deliberate": 0.0, "open": 1.0})
        here = own.get(r["reading"])
        r["base_rate"] = {
            "share_of_country_days": share,
            "share_of_this_countrys_days": here,
            "text": f"An outage would have read \"{r['reading']}\" whatever its cause on {share:.1%} of "
                    f"{b['country_days']} country-days worldwide, and on {here:.0%} of {days} days in {r['country']}.",
            "cautions": rates["cautions"]}
    return doc


def safe_out(path):
    """The output folder, refused if it sits inside site/ (which the build deploys)."""
    out = Path(path).resolve()
    site = (ROOT / "site").resolve()
    if out == site or site in out.parents:
        raise SystemExit("triage.py does not write inside site/: its output is not reviewed for publication")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=PUBLISHED, help="snapshot URL, .geojson file or .geojson.gz archive copy")
    ap.add_argument("--out", default=str(ROOT / "out" / "triage"))
    ap.add_argument("--rates", default=None, help="base-rates.json from triage_rates.py (default: <out>/base-rates.json)")
    a = ap.parse_args(argv)
    out = safe_out(a.out)
    rates_file = Path(a.rates) if a.rates else out / "base-rates.json"
    rates = json.loads(rates_file.read_text(encoding="utf-8")) if rates_file.exists() else None
    doc = attach_rates(triage(load(a.snapshot)), rates)
    out.mkdir(parents=True, exist_ok=True)
    stamp = (doc["snapshot_generated_at"] or "unknown").replace(":", "")
    for name in (f"triage-{stamp}.json", "latest.json"):
        (out / name).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"snapshot {doc['snapshot_generated_at']}  layers {doc['layer_states']}")
    for r in doc["records"]:
        L = r["layers"]
        print(f"{r['country']}  {r['reading']:<11} {r['outage']['title']}")
        if r["base_rate"]:
            print(f"    base rate: {r['base_rate']['text']}")
        print(f"    elements: {len(L['elements']['significant'])} significant, {len(L['elements']['minor'])} minor"
              f" | cordon: {len(L['cordon']['recent'])} recent, {len(L['cordon']['standing'])} standing"
              f" | watchfloor: {len(L['watchfloor']['raised'])} raised"
              f"{'' if L['watchfloor']['data_reaches_outage'] else ' (data ends before the outage)'}")
    if rates is None:
        print("no base rates: run tools/triage_rates.py first")
    if not doc["records"]:
        print("no Blackout outages in this snapshot")
    print(f"wrote {out / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
