"""Outage triage: what the other layers show under each Blackout outage.

A named join (scenario 2 of [local path]), not a count of programs. For every
Blackout outage in a COP snapshot it records what Elements, Cordon and Watchfloor show for that country around
that hour, including "nothing found" and "no current data", which are different answers. It states a reading of
the pattern and drafts the Verdict question. It never states a probability: that is the analyst's.

    python tools/triage.py                              # read the published snapshot, write out/triage/
    python tools/triage.py --snapshot site/data/today.geojson
    python tools/triage.py --snapshot <file.geojson.gz> # an archived day

NOT PUBLISHED. Every write goes through tools/gate.py, which classes the join from the layers it declares and the
items it cites, and refuses a deployed folder while "published" is off. It is not called by the build. It reads
hazards, outages, travel and embassy notices and press attention; Cordon's navigation warnings and conflict-zone
airspace bulletins are left out, because they place military activity and would make this join a held one.

Standard-library Python only.
"""

import argparse
import gzip
import json
import math
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"
PUBLISHED = "https://cop.theintelligence.company/data/today.geojson"
UA = "tic-cop/0.1 (+https://cop.theintelligence.company)"

# The gate decides the review class from these inputs; "published" stays off until Tom has reviewed the display.
JOIN = {"id": "outage-triage", "version": "0.3", "published": False,
        "inputs": ["blackout", "elements", "watchfloor", "cordon:advisory", "cordon:alert", "cordon:posture"]}
NOTICE_KINDS = {"advisory", "alert", "posture"}   # the Cordon kinds on the gate's open list
LAYERS = ("elements", "cordon", "watchfloor")
# Windows, all measured from the outage's own time (its data_as_of). PROVISIONAL (Tom, 12 Nov 2026): first picks, not
# validated; look at them again when the base rates are rerun on a deeper archive (about 28 Nov 2026).
HAZARD_BEFORE_H = 72     # a hazard that BEGAN this long before the outage can explain it; one already running is
                         # standing context (a volcano alert in Hawaii does not explain an outage in the US)
HAZARD_AFTER_H = 6       # reports of a hazard trail the event a little
HAZARD_NEAR_KM = 300     # a hazard with no country (an offshore earthquake) counts within this of the country
NOTICE_RECENT_H = 48     # a notice dated this long before the outage counts; older ones are standing context
ATTENTION_USUAL_SHARE = 0.25  # raised attention counts only where the country had it on fewer than this share of
                              # archived days: against its own baseline, not the world's
OONI_WINDOW_H = 72       # the Verdict question's window
OONI_RISE_POINTS = 10    # where OONI is already over its threshold, the question asks about a rise this large
# Elements items that are standing background, not an event that takes a network down.
BACKGROUND = {"firms", "usdm", "nifc", "spc"}
CONFIRMED_OUTAGE = "BO-IND-1"   # Blackout: a drop seen by more than one signal
OONI_BLOCKING = "BO-IND-3"      # Blackout: OONI rate at or above its fixed threshold

READINGS = {
    "incomplete": "A layer has no current data, so this outage cannot be triaged yet. Silence is not quiet.",
    "unconfirmed": "Blackout calls this a single-source dip, not a confirmed outage. Context is listed, no reading.",
    "natural": "A significant natural hazard sits under this outage in place and time. A natural cause has support.",
    "deliberate": "No new hazard under it, and a notice was issued or raised in the two days before it, or press "
                  "attention is above this country's own normal. A deliberate cut is worth a call.",
    "open": "No new hazard, no fresh notice and no unusual attention. Nothing here separates an accident from a "
            "deliberate cut.",
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
    """Elements items under the outage: in or near the country, overlapping the hazard window. A significant hazard
    counts only if it began inside the window; one that began earlier and is still running is "standing"."""
    lo, hi = at - timedelta(hours=HAZARD_BEFORE_H), at + timedelta(hours=HAZARD_AFTER_H)
    significant, standing, minor, background = [], [], [], []
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
            (significant if start >= lo else standing).append(row)
        else:
            minor.append(row)
    return {"significant": significant, "standing": standing, "minor": minor, "background": background}


def _direction(item_id, dated, level, levels):
    """raised, lowered, unchanged or unknown: an advisory's level against the one it replaced.

    levels: {advisory id: [[date, level], ...]} from the archive (triage_rates.py), or None."""
    earlier = [(d, lv) for d, lv in (levels or {}).get(item_id, []) if utc(d) and dated and utc(d) < dated]
    if level is None or not earlier:
        return "unknown"
    before = max(earlier)[1]
    if before is None:
        return "unknown"
    return "raised" if level > before else "lowered" if level < before else "unchanged"


def notices(features, cc, at, levels=None):
    """Cordon's travel and embassy notices for the country, against the outage's time.

    recent: dated in the NOTICE_RECENT_H before the outage and not a lowering or a re-issue at the same level; these
    count toward a reading. recent_not_raised: dated in the window but lowered or unchanged. standing: older.
    after: dated after the outage. An advisory whose earlier level is not in the archive counts, marked "unknown"."""
    lo = (at - timedelta(hours=NOTICE_RECENT_H)).replace(hour=0, minute=0, second=0, microsecond=0)  # dates are days
    recent, not_raised, standing, after = [], [], [], []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "cordon" or cc not in (p.get("countries") or []):
            continue
        if _kind(p.get("id")) not in NOTICE_KINDS:
            continue
        dated = utc(p.get("data_as_of"))
        m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
        kind = _kind(p.get("id"))
        row = _row(p, kind=kind, level=m.get("level"))
        if kind == "advisory":
            row["direction"] = _direction(p.get("id"), dated, m.get("level"), levels)
        if not dated or dated < lo:
            standing.append(row)
        elif dated > at:
            after.append(row)
        elif row.get("direction") in ("lowered", "unchanged"):
            not_raised.append(row)
        else:
            recent.append(row)
    return {"recent": recent, "recent_not_raised": not_raised, "standing": standing, "after": after}


def attention(features, cc, at, shares=None):
    """Watchfloor items naming the country, whether that data reaches the outage's hour, and whether raised
    attention is unusual for this country. shares: {cc: share of archived days with raised attention}, or None."""
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
    own = None if shares is None else shares.get(cc, 0.0)
    return {"raised": raised, "routine": routine,
            "own_rate": own,                       # None: no archive baseline was given
            "unusual": bool(raised) and (own is None or own < ATTENTION_USUAL_SHARE),
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


def _rate(title):
    m = re.search(r"(\d+(?:\.\d+)?)%", str(title))
    return float(m.group(1)) if m else None


def triage(snapshot, history=None):
    """One record per Blackout outage in the snapshot. history: triage_rates.py's "history" block (each advisory's
    earlier levels, each country's share of days with raised attention), or None when there is no archive."""
    levels = (history or {}).get("advisory_levels")
    shares = (history or {}).get("attention_share")
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
            haz, note = hazards(features, cc, at), notices(features, cc, at, levels)
            att = attention(features, cc, at, shares)
            dead = [pid for pid in LAYERS if states[pid] in ("stale", "missing")]
            confirmed = p.get("indicator_id") == CONFIRMED_OUTAGE
            if dead:
                reading = "incomplete"
            elif not confirmed:
                reading = "unconfirmed"
            elif haz["significant"]:
                reading = "natural"
            elif note["recent"] or att["unusual"]:
                reading = "deliberate"
            else:
                reading = "open"
            until = at + timedelta(hours=OONI_WINDOW_H)
            blocked_now = cc in ooni
            caveats = []
            if not att["data_reaches_outage"] and "watchfloor" not in dead:
                caveats.append(f"Watchfloor's newest data ({att['newest_data']}) ends before this outage, so it "
                               "shows attention before the outage, not during it. Run again after its next day "
                               "is in.")
            if history is None:
                caveats.append("No archive baseline was given: raised attention is counted without checking it "
                               "against this country's own normal, and advisory levels are not compared with "
                               "the ones they replaced. Run tools/triage_rates.py first.")
            if any(n.get("direction") == "unknown" for n in note["recent"]):
                caveats.append("An advisory in the window has no earlier level in the archive, so it is counted "
                               "without knowing whether it was raised.")
            if haz["standing"]:
                caveats.append("A hazard that began before the window is still running in this country. It is "
                               "listed as standing and does not make the reading natural.")
            now_rate = _rate(ooni[cc]["title"]) if blocked_now else None
            rise = None
            if blocked_now and now_rate is not None:
                target = round(now_rate + OONI_RISE_POINTS, 1)
                rise = {
                    "question": f"Will Blackout's OONI rate for {cc} reach {target}% or more within "
                                f"{OONI_WINDOW_H} hours of the outage of {iso(at)}?",
                    "rule_draft": f"Yes if a Blackout OONI reading for {cc} dated inside the window ending "
                                  f"{iso(until)} is {target}% or more, a rise of {OONI_RISE_POINTS} points over the "
                                  f"{now_rate}% reading at the outage. No otherwise.",
                    "notes": ["The baseline is the reading at the outage, not a 30-day rate: COP's archive holds "
                              "Blackout only since 12 Nov 2026.",
                              "Blackout's OONI rate covers the past 7 days, so a one-day rise shows up damped.",
                              f"The {OONI_RISE_POINTS}-point rise is provisional."]}
            records.append({
                "join": JOIN["id"], "country": cc,
                "outage": _row(p, confirmed=confirmed, indicator_id=p.get("indicator_id"), summary=p.get("summary")),
                "layers": {
                    "elements": {"state": states["elements"], **haz,
                                 "found": bool(haz["significant"] or haz["minor"])},
                    "cordon": {"state": states["cordon"], **note, "found": bool(note["recent"])},
                    "watchfloor": {"state": states["watchfloor"], **att, "found": bool(att["unusual"])},
                },
                "no_current_data": dead,
                "ooni_now": ooni.get(cc),
                "reading": reading, "reading_text": READINGS[reading],
                "caveats": caveats,
                "verdict_draft": {
                    "registered": False, "probability": None, "approved_by": None,
                    "question": f"Will OONI confirm blocking in {cc} within {OONI_WINDOW_H} hours of the outage "
                                f"of {iso(at)}?",
                    "window_ends": iso(until),
                    "rule_draft": f"Yes if Blackout's OONI reading for {cc} is at or above its {OONI_BLOCKING} "
                                  f"threshold for a day inside the window ending {iso(until)}. No otherwise.",
                    "caution": ("OONI is already at or above the threshold here, so this question is settled "
                                "before it is asked. Use the rise form instead, or skip it.") if blocked_now else None,
                    "rise_form": rise,
                },
            })
    records.sort(key=lambda r: (r["reading"] in ("incomplete", "unconfirmed"), r["outage"]["data_as_of"] or ""),
                 reverse=False)
    return {"join": JOIN, "snapshot_generated_at": snapshot.get("generated_at"), "layer_states": states,
            "windows": {"hazard_before_hours": HAZARD_BEFORE_H, "hazard_after_hours": HAZARD_AFTER_H,
                        "hazard_near_km": HAZARD_NEAR_KM, "notice_recent_hours": NOTICE_RECENT_H,
                        "attention_usual_share": ATTENTION_USUAL_SHARE, "ooni_window_hours": OONI_WINDOW_H,
                        "ooni_rise_points": OONI_RISE_POINTS, "provisional": True},
            "baseline_given": history is not None,
            "records": records}


def attach_rates(doc, rates):
    """Put the background rate beside every reading that has one. rates: triage_rates.py's document, or None."""
    if not rates:
        doc["base_rates"] = None
        for r in doc["records"]:
            r["base_rate"] = None
        return doc
    b = rates["background"]
    n = b["would_read_country_days"]
    doc["base_rates"] = {"country_days": n, "would_read": b["would_read"],
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
                    f"{n} country-days worldwide, and on {here:.0%} of {days} days in {r['country']}.",
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
    doc = attach_rates(triage(load(a.snapshot), (rates or {}).get("history")), rates)
    stamp = (doc["snapshot_generated_at"] or "unknown").replace(":", "")
    verdict = gate.write(JOIN, doc, out, (f"triage-{stamp}.json", "latest.json"))
    print(f"snapshot {doc['snapshot_generated_at']}  layers {doc['layer_states']}  gate: {verdict['review_class']}")
    for r in doc["records"]:
        L = r["layers"]
        print(f"{r['country']}  {r['reading']:<11} {r['outage']['title']}")
        if r["base_rate"]:
            print(f"    base rate: {r['base_rate']['text']}")
        print(f"    elements: {len(L['elements']['significant'])} significant, {len(L['elements']['minor'])} minor"
              f" | cordon: {len(L['cordon']['recent'])} recent, {len(L['cordon']['standing'])} standing"
              f" | watchfloor: {len(L['watchfloor']['raised'])} raised"
              f"{' (usual here)' if L['watchfloor']['raised'] and not L['watchfloor']['unusual'] else ''}"
              f"{'' if L['watchfloor']['data_reaches_outage'] else ' (data ends before the outage)'}")
    if rates is None:
        print("no base rates: run tools/triage_rates.py first")
    if not doc["records"]:
        print("no Blackout outages in this snapshot")
    print(f"wrote {out / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
