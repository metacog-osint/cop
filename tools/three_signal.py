"""Three-signal join (four signals since join 0.3): interference, a new notice, a connectivity drop and unusual
press reporting of violence in one area inside 48 hours.

Scenario 1 of the private planning notes, with Iran and the Strait of Hormuz as the live case. Each
signal alone is routine and has an innocent explanation; the join asks only whether all four fall in one named area
within WINDOW_H of each other, and what Vis forecasts for the weather there. The id stays "three-signal" (the file
name, the page section and the MCP tool use it).

  interference   a Noisefloor anomaly (new, rise or event; never "ended" or the daily summary) inside the area
  notice         a new Cordon navigation warning inside the area whose text names military activity; a new EASA
                 conflict-zone bulletin, or a revision that is not a scheduled renewal, for one of the area's
                 countries; or a US embassy security alert for one of them (from join 0.3). Cordon marks every item
                 "routine", so tier is not used here.
  connectivity   a confirmed Blackout outage (BO-IND-1) or a Cloudflare-verified traffic anomaly in one of the
                 area's countries. A single-source dip is listed and does not count.
  press          a Watchfloor country item for one of the area's countries: violent-event articles raised at a
                 z-score of WATCHFLOOR_Z or more (from join 0.3). Attention items and lower scores are listed only.
                 Watchfloor lags about a day, so this signal confirms more than it tips.

Base rates (join 0.3): a signal that fired on more than half of the area's last 30 archived days with every layer
current counts only on a day its item count is above its own 90th percentile over those days. Until the archive
holds RATE_MIN_DAYS such days, each signal counts as it fires and the record says so.

Vis is context, not a signal: its 72-hour weather forecast for the area is listed beside the reading (the worst
stoplight among its points there). It never adds to the count and never removes a signal.

Published since 15 Nov 2026, when Tom retired the mosaic gate (COP's CLAUDE.md). The voice rule that replaced it
applies here: the record shows each layer's items, how close they fall in time and how often each fires anyway
(the archive replay), and never states what the pattern means. The build runs it after outage triage and writes
site/data/three-signal.json for the page and the MCP server's three_signal tool.

    python tools/three_signal.py                       # the published snapshot, every area
    python tools/three_signal.py --archive out/archive # also replay the archive: how often each signal fires anyway
    python tools/three_signal.py --snapshot site/data/today.geojson --archive out/rates-archive --publish site/data

It never states a probability: that is the analyst's. Standard-library Python only.
"""

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import triage as tri  # noqa: E402

JOIN = {"id": "three-signal", "version": "0.4", "published": True,
        "inputs": ["noisefloor", "vis", "blackout", "cordon:navwarn", "cordon:czib", "cordon:alert",
                   "cordon:advisory", "watchfloor", "wake:ukmto"]}
LAYERS = ("noisefloor", "cordon", "blackout", "vis", "watchfloor")
SIGNALS = ("interference", "notice", "connectivity", "press")
SIGNAL_PROGRAM = {"interference": "noisefloor", "notice": "cordon", "connectivity": "blackout", "press": "watchfloor"}

# PROVISIONAL, like triage.py's windows: first picks, to be looked at again with a deeper archive.
WINDOW_H = 48        # the three signals must fall within this many hours of each other
LOOKBACK_H = 96      # how far back from the snapshot a signal may be dated and still be read
QUESTION_DAYS = 7    # the Verdict question's window
OONI_MOVE_POINTS = 5 # an OONI rate that moved this much against the archive is listed as context
NAVWARN_BLIND_DAYS = 14  # Cordon's newest navigation warning anywhere older than this: that feed has stopped moving,
                         # so "no new warning" means nothing (found 12 Nov 2026: newest was dated 10 May 2024)
# Join 0.3 (Tom's yes of 18 Nov 2026 to gaps 1, 2 and 7 of the private planning notes).
WATCHFLOOR_Z = 3.0       # press: violent-event articles at or above this z-score (Watchfloor's own, robust since 17 Nov)
RENEWAL_BEFORE_D = 7     # an EASA revision first seen this many days before the previous version's expiry ...
RENEWAL_AFTER_H = 48     # ... or up to this many hours after it, with a later expiry, is a scheduled renewal
RATE_DAYS = 30           # trailing archived days for each signal's base rate in the area
RATE_MIN_DAYS = 14       # days with every layer current needed before a base rate changes what counts
RATE_SATURATED = 0.5     # a signal present on more than this share of those days counts only when it rises ...
RATE_PERCENTILE = 0.9    # ... above this percentile of its own daily item counts (nearest rank)
# Join 0.4 (Tom's option D, 18 Nov 2026, the private planning notes): UKMTO's reports in
# the circle, read from Wake's items, are context; its Attack reports count toward the notice only when their number
# in the look-back is above its own RATE_PERCENTILE over the RATE_DAYS days before (the test 0.3 applies to a
# saturated signal), measured on UKMTO's own series, never pooled with Cordon's notices.
UKMTO_PREFIX = "wake:ukmto:"
UKMTO_COUNTED = ("Attack",)  # UKMTO's own type name; Advisory, Suspicious Activity, Hijack and the rest are context
UKMTO_KEEP_D = 30            # Wake keeps a report in its feed for 30 days from the incident (Wake's ukmto.py WINDOW_DAYS)
UKMTO_CREDIT = "UKMTO (UK Maritime Trade Operations, Royal Navy), via Wake"
UKMTO_NOTE = ("UKMTO's reports as published: Wake has not verified them and they name no one responsible. A report "
              "type such as \"Attack\" is UKMTO's, not a finding of COP's.")

# An area is a circle, because interference and warnings ignore borders, plus the countries whose country-level
# items (outages, airspace bulletins, embassy alerts) are read with it.
AREAS = {
    "hormuz": {"name": "Strait of Hormuz and the Gulf", "center": [56.378, 27.218], "radius_km": 900,
               "center_name": "Bandar Abbas", "countries": ["IR", "AE", "OM", "QA", "BH", "KW"]},
}
# Words that mark a navigation warning as about military activity or a threat, not a drifting buoy or a survey.
MILITARY_WORDS = ("FIRING", "GUNNERY", "MISSILE", "ROCKET", "MILITARY", "NAVAL EXERCISE", "EXERCISE", "HAZARDOUS OP",
                  "MINE", "DRONE", "UAV", "UNMANNED", "ATTACK", "GPS", "GNSS", "INTERFERENCE", "JAMMING", "SPOOFING",
                  "WARSHIP", "SUBMARINE", "SPACE DEBRIS", "LAUNCH")
INTERFERENCE_KINDS = {"new", "rise", "event"}

READINGS = {
    "incomplete": "A layer has no current data, so this area cannot be read. Silence is not quiet.",
    "none": "None of the four signals is present in this area.",
    "partial": "Some of the four signals are present, not all counted within the window. Each has an innocent "
               "explanation alone.",
    "converged": "All four signals fall in this area within the window, each above its own usual rate where the "
                 "archive gives one. That is the pattern this join looks for; it is not a warning, and the call is "
                 "the analyst's.",
}


def _points(geometry):
    if not isinstance(geometry, dict):
        return []
    t, c = geometry.get("type"), geometry.get("coordinates")
    if t == "GeometryCollection":  # an NGA warning with both an area and a trackline
        return [p for g in geometry.get("geometries") or [] for p in _points(g)]
    try:
        if t == "Point":
            return [c[:2]]
        if t in ("MultiPoint", "LineString"):
            return [p[:2] for p in c]
        if t == "MultiLineString":  # tracklines: every vertex counts, no centre
            return [p[:2] for line in c for p in line]
        if t == "Polygon":
            ring = [p[:2] for p in c[0]]
        elif t == "MultiPolygon":
            ring = [p[:2] for poly in c for p in poly[0]]
        else:
            return []
    except (TypeError, IndexError):
        return []
    if not ring:
        return []
    centre = [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    return ring + [centre]


def km_to_area(geometry, area):
    """Shortest distance in km from the geometry's points to the area's centre, or None without geometry."""
    lon0, lat0 = area["center"]
    ds = [tri._km(lat0, lon0, lat, lon) for lon, lat in _points(geometry)]
    return min(ds) if ds else None


def _in_area(feature, area, by_country=False):
    """(inside, km). An item with a place is judged by the circle alone: interference "near Yerevan" lists Iran as
    a country and is 1,700 km from the Strait. by_country is for country-level items (airspace bulletins)."""
    km = km_to_area(feature.get("geometry"), area)
    if by_country:
        inside = bool(set(tri.place_countries(feature["properties"])) & set(area["countries"]))
        return inside, None if km is None else round(km)
    if km is None:
        return False, None
    return km <= area["radius_km"], round(km)


def _nf_kind(item_id):
    parts = str(item_id).split("-")
    return parts[1] if len(parts) > 1 else ""


def _military(p):
    text = (str(p.get("title")) + " " + str(p.get("summary"))).upper()
    return sorted(w for w in MILITARY_WORDS if w in text)


def first_seen(snapshots):
    """What the archive saw first. bulletins: {id | title: generated_at of the first archived snapshot that carried
    it}, for airspace bulletins: a bulletin keeps its id and original date through revisions, so a revision shows
    only as a changed title. versions: {id: [[first seen, title, valid_until], ...]} oldest first, so a revision can
    be told from a renewal. alert_dates: {embassy alert id: the earliest data_as_of any snapshot carried}, so a
    re-dated alert keeps its first date (as triage does)."""
    seen, first, versions, alerts = {}, None, {}, {}
    for snap in sorted(snapshots, key=lambda s: s.get("generated_at") or ""):
        first = first or snap.get("generated_at")
        for f in snap.get("features", []):
            p = f.get("properties") or {}
            if p.get("cop_program") != "cordon":
                continue
            kind = tri._kind(p.get("id"))
            if kind == "czib":
                key = f"{p.get('id')} | {p.get('title')}"
                if key not in seen:
                    seen[key] = snap.get("generated_at")
                    versions.setdefault(str(p.get("id")), []).append(
                        [snap.get("generated_at"), p.get("title"), p.get("valid_until")])
            elif kind == "alert" and tri.utc(p.get("data_as_of")):
                old = alerts.get(p.get("id"))
                if old is None or tri.utc(p["data_as_of"]) < tri.utc(old):
                    alerts[p.get("id")] = p.get("data_as_of")
    # Whatever the oldest snapshot carried was already there: it is not "new" on that day.
    return {"archive_starts": first, "bulletins": {k: v for k, v in seen.items() if v != first},
            "versions": versions, "alert_dates": alerts}


def renewal_of(versions, item_id, title, new_at, valid_until):
    """The previous version when this revision of an airspace bulletin is a scheduled renewal: first seen from
    RENEWAL_BEFORE_D days before the previous version's expiry to RENEWAL_AFTER_H hours after it, with a later
    expiry. None otherwise (a revision mid-term, or no earlier version in the archive)."""
    hist = (versions or {}).get(str(item_id)) or []
    titles = [v[1] for v in hist]
    if title not in titles or titles.index(title) == 0:
        return None
    prev = hist[titles.index(title) - 1]
    prev_until, until = tri.utc(prev[2]), tri.utc(valid_until)
    if not (prev_until and until and new_at) or until <= prev_until:
        return None
    if prev_until - timedelta(days=RENEWAL_BEFORE_D) <= new_at <= prev_until + timedelta(hours=RENEWAL_AFTER_H):
        return {"title": prev[1], "valid_until": prev[2]}
    return None


def _percentile(values, q):
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, -(-int(q * 100) * len(ordered) // 100) - 1))]


def signal_rates(rows, area, day):
    """Each signal's base rate in the area over the RATE_DAYS archived days before `day` (YYYY-MM-DD) that had every
    layer current, from replay rows (raw counts, before any rate was applied)."""
    lo = (tri.utc(day + "T00:00:00Z") - timedelta(days=RATE_DAYS)).strftime("%Y-%m-%d")
    mine = [r for r in rows or [] if r["area"] == area and lo <= r["day"] < day and r["reading"] != "incomplete"]
    out = {"days": len(mine), "enough": len(mine) >= RATE_MIN_DAYS, "window_days": RATE_DAYS,
           "min_days": RATE_MIN_DAYS, "signals": {}}
    for s in SIGNALS:
        counts = [int((r.get("counts") or {}).get(s, 0)) for r in mine]
        present = sum(1 for c in counts if c)
        share = round(present / len(counts), 3) if counts else None
        out["signals"][s] = {"days_present": present, "share": share,
                             "p90_count": _percentile(counts, RATE_PERCENTILE) if counts else None,
                             "saturated": bool(out["enough"] and share is not None and share > RATE_SATURATED)}
    return out


def _is_ukmto(p):
    return str(p.get("id") or "").startswith(UKMTO_PREFIX)


def ukmto_add(hist, doc, covers=True):
    """Add the UKMTO reports a snapshot (or, with covers=False, one of Wake's ended lists) carries to hist, in place.
    A report keeps its earliest copy. A snapshot carrying any report covers every report published from
    UKMTO_KEEP_D days before it, because Wake's feed holds the last 30 days; an ended list covers nothing by itself."""
    got = False
    for f in doc.get("features", []):
        p = f.get("properties") if isinstance(f, dict) else None
        if isinstance(p, dict) and _is_ukmto(p) and isinstance(p.get("measures"), dict):
            got = True
            hist["reports"].setdefault(p["id"], f)
    at = tri.utc(doc.get("generated_at"))
    if covers and got and at:
        start = at - timedelta(days=UKMTO_KEEP_D)
        old = tri.utc(hist.get("covered_from"))
        if old is None or start < old:
            hist["covered_from"] = tri.iso(start)
    return hist


def ukmto_history(docs=(), ended=()):
    """Every UKMTO report the archive's snapshots (docs) and Wake's ended lists carried: {"reports": {id: feature},
    "covered_from": the earliest publication time from which every report is in hand, or None}. COP never reads
    UKMTO itself: Wake is the suite's reader."""
    hist = {"reports": {}, "covered_from": None}
    for d in docs:
        ukmto_add(hist, d)
    for d in ended:
        ukmto_add(hist, d, covers=False)
    return hist


def ukmto_read(hist, area, at):
    """What UKMTO's reports in the circle say at `at`: the look-back's rows (newest first), its Attack count, the
    daily series of look-back Attack counts over the RATE_DAYS days before and whether the count is above its own
    RATE_PERCENTILE. Each report is dated by when UKMTO published it (measures.created_at), never its incident time:
    report 209 was published 17 Nov 2026 for an incident on 13 Nov. Causal: nothing published after `at` is read."""
    lo = at - timedelta(hours=LOOKBACK_H)
    placed = []
    for f in (hist or {}).get("reports", {}).values():
        p, m = f["properties"], f["properties"]["measures"]
        created = tri.utc(m.get("created_at")) or tri.utc(p.get("data_as_of"))
        if not created or created > at:
            continue
        inside, km = _in_area(f, area)
        if inside:
            placed.append((created, f, km))
    attacks = [c for c, f, _ in placed if f["properties"]["measures"].get("report_type") in UKMTO_COUNTED]
    rows = []
    for created, f, km in sorted((x for x in placed if x[0] > lo), key=lambda x: (x[0], x[1]["properties"]["id"]),
                                 reverse=True):
        p, m = f["properties"], f["properties"]["measures"]
        rows.append(tri._row(p, kind="ukmto", source="ukmto", report_type=m.get("report_type"), place=m.get("place"),
                             published_at=tri.iso(created), incident_at=m.get("incident_at"), km_from_centre=km,
                             at=tri.iso(created), counted_type=m.get("report_type") in UKMTO_COUNTED))
    now_n = sum(1 for c in attacks if lo < c <= at)
    covered = tri.utc((hist or {}).get("covered_from"))
    need = at - timedelta(days=RATE_DAYS, hours=LOOKBACK_H)
    enough = bool(covered and covered <= need)
    series = [sum(1 for c in attacks if t - timedelta(hours=LOOKBACK_H) < c <= t)
              for t in (at - timedelta(days=k) for k in range(1, RATE_DAYS + 1))] if enough else []
    p90 = _percentile(series, RATE_PERCENTILE) if series else None
    return {"credit": UKMTO_CREDIT, "note": UKMTO_NOTE, "reports": rows, "attacks_in_lookback": now_n,
            "rate": {"enough": enough, "history_from": tri.iso(covered) if covered else None,
                     "history_needed_from": tri.iso(need), "window_days": RATE_DAYS,
                     "days_with_an_attack": sum(1 for c in series if c) if series else None,
                     "mean_per_lookback": round(sum(series) / len(series), 2) if series else None,
                     "p90_count": p90},
            "counts": bool(enough and now_n > p90)}


def ooni_history(snapshots):
    """{cc: [[snapshot time, rate], ...]} of Blackout's OONI readings, oldest first."""
    out = {}
    for snap in sorted(snapshots, key=lambda s: s.get("generated_at") or ""):
        for f in snap.get("features", []):
            p = f.get("properties") or {}
            if p.get("cop_program") == "blackout" and tri._kind(p.get("id")) == "ooni":
                rate = tri.item_rate(p)
                for cc in p.get("countries") or []:
                    if rate is not None:
                        out.setdefault(cc, []).append([snap.get("generated_at"), rate])
    return out


def changed_at(p, dated):
    """When a notice last became new (revision 2.1): its last_change_at when it states last_change new or raised,
    None when it states any other last_change (an edit, a lowering, standing: not a fresh notice), else `dated`, the
    old reading from data_as_of."""
    lc, at = tri.stated_change(p)
    if lc is None:
        return dated
    return (at or dated) if lc in ("new", "raised") else None


def read_area(snapshot, key, area, seen=None, ooni=None, rates=None, ukmto=None):
    """One record for one area from one snapshot. rates: signal_rates() for this area and day, or None. ukmto: the
    archive's ukmto_history() (the snapshot's own reports are added to it here), or None for the snapshot alone."""
    at = tri.utc(snapshot.get("generated_at"))
    features = [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]
    states = {pid: tri.layer_state(snapshot, features, pid) for pid in LAYERS}
    lo = at - timedelta(hours=LOOKBACK_H)
    found = {s: [] for s in SIGNALS}
    context = {"interference_ended": [], "notices_other": [], "notices_standing": [], "notices_renewal": [],
               "single_source_dips": [], "embassy_alerts": [], "ooni": [], "press_other": []}
    weather = []
    bulletins = (seen or {}).get("bulletins")
    newest_navwarn = max((tri.utc(f["properties"].get("data_as_of")) for f in features
                          if f["properties"].get("cop_program") == "cordon"
                          and tri._kind(f["properties"].get("id")) == "navwarn"
                          and tri.utc(f["properties"].get("data_as_of"))), default=None)
    blind = [] if newest_navwarn and newest_navwarn >= at - timedelta(days=NAVWARN_BLIND_DAYS) else ["cordon:navwarn"]
    for f in features:
        p = f["properties"]
        if p.get("cop_stale"):
            continue
        pid, dated = p.get("cop_program"), tri.utc(p.get("data_as_of"))
        if pid == "noisefloor":
            inside, km = _in_area(f, area)
            if not inside:
                continue
            row = tri._row(p, kind=_nf_kind(p.get("id")), km_from_centre=km, at=p.get("data_as_of"))
            day_end = tri.day_span(p)[1]       # a whole-day item counts to the end of its day, not its midnight start
            if row["kind"] in INTERFERENCE_KINDS and p.get("tier") in ("anomaly", "warning") and day_end and day_end >= lo:
                found["interference"].append(row)
            elif row["kind"] == "ended":
                context["interference_ended"].append(row)
        elif pid == "cordon":
            kind = tri._kind(p.get("id"))
            if kind == "navwarn":
                inside, km = _in_area(f, area)
                if not inside:
                    continue
                words = _military(p)
                row = tri._row(p, kind=kind, km_from_centre=km, military_words=words, at=p.get("data_as_of"))
                dated = changed_at(p, dated)
                if not dated or dated < lo:
                    context["notices_standing"].append(row)
                elif words:
                    found["notice"].append(row)
                else:
                    context["notices_other"].append(row)
            elif kind == "czib":
                inside, km = _in_area(f, area, by_country=True)
                if not inside:
                    continue
                dated = changed_at(p, dated)
                new_at = dated if dated and dated >= lo else None
                if new_at is None and bulletins is not None and tri.stated_change(p)[0] is None:
                    first = tri.utc(bulletins.get(f"{p.get('id')} | {p.get('title')}"))
                    new_at = first if first and first >= lo else None
                prev = renewal_of((seen or {}).get("versions"), p.get("id"), p.get("title"), new_at,
                                  p.get("valid_until")) if new_at and not (dated and dated >= lo) else None
                row = tri._row(p, kind=kind, km_from_centre=km, at=tri.iso(new_at) if new_at else None,
                               valid_until=p.get("valid_until"))
                if prev:
                    row["renews"] = prev
                    context["notices_renewal"].append(row)
                else:
                    (found["notice"] if new_at else context["notices_standing"]).append(row)
            elif kind == "alert" and set(tri.place_countries(p)) & set(area["countries"]):
                if tri.stated_change(p)[0] is not None:
                    since = changed_at(p, dated)       # Cordon's own record (revision 2.1) before COP's archive
                else:
                    first = tri.utc(((seen or {}).get("alert_dates") or {}).get(p.get("id")))
                    since = min(d for d in (dated, first) if d) if (dated or first) else None
                if not since or since < lo:
                    continue
                atype = tri.alert_type(p.get("title"))
                row = tri._row(p, kind=kind, alert_type=atype, at=tri.iso(since))
                us = str(p.get("title") or "").startswith("US embassy (")
                (found["notice"] if us and atype == "security" else context["embassy_alerts"]).append(row)
        elif pid == "blackout":
            kind = tri._kind(p.get("id"))
            ccs = sorted(set(tri.place_countries(p)) & set(area["countries"]))
            if not ccs:
                continue
            if kind in ("outage", "radar-anomaly") and dated and dated >= lo:
                row = tri._row(p, kind=kind, countries=ccs, indicator_id=p.get("indicator_id"), at=p.get("data_as_of"))
                if kind == "outage" and p.get("indicator_id") != tri.CONFIRMED_OUTAGE:
                    context["single_source_dips"].append(row)
                else:
                    found["connectivity"].append(row)
            elif kind == "ooni":
                rate = tri.item_rate(p)
                for cc in ccs:
                    past = [r for t, r in (ooni or {}).get(cc, []) if tri.utc(t) and tri.utc(t) < at]
                    low = min(past) if past else None
                    moved = None if rate is None or low is None else round(rate - low, 1)
                    context["ooni"].append(tri._row(p, country=cc, rate=rate, lowest_in_archive=low,
                                                    points_above_lowest=moved,
                                                    moved=bool(moved is not None and moved >= OONI_MOVE_POINTS)))
        elif pid == "watchfloor":
            parts = str(p.get("id")).split(":")
            ccs = sorted(set(tri.place_countries(p)) & set(area["countries"]))
            if len(parts) < 5 or parts[2] != "country" or not ccs:
                continue
            day_end = tri.day_span(p)[1]
            if not day_end or day_end < lo:
                continue
            score = ((p.get("measures") or {}).get("score") or {}) if isinstance(p.get("measures"), dict) else {}
            z = score.get("z") if isinstance(score.get("z"), (int, float)) else None
            row = tri._row(p, kind=f"country-{parts[4]}", countries=ccs, z=z, ratio=score.get("ratio"),
                           at=p.get("data_as_of"))
            if parts[4] == "violence" and p.get("tier") == "anomaly" and z is not None and z >= WATCHFLOOR_Z:
                found["press"].append(row)
            else:
                context["press_other"].append(row)
        elif pid == "vis":
            inside, km = _in_area(f, area)
            m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
            if inside and m.get("stoplight_72h"):
                weather.append(tri._row(p, station=m.get("station"), stoplight_72h=m.get("stoplight_72h"),
                                        dark_low_light_hours=None if m.get("dark_low_light_min") is None
                                        else round(m["dark_low_light_min"] / 60, 1),
                                        moon_lit=m.get("moon_lit"), km_from_centre=km, kind="forecast"))
    # UKMTO's reports (join 0.4): context always; its attacks join the notice signal only on a surge.
    hist = ukmto_add({"reports": dict((ukmto or {}).get("reports") or {}),
                      "covered_from": (ukmto or {}).get("covered_from")}, snapshot)
    uk = ukmto_read(hist, area, at)
    context["ukmto"] = uk
    surge = [r for r in uk["reports"] if r["counted_type"]] if uk["counts"] else []
    # Base rates: a saturated signal counts only when today's count rises above its own 90th percentile.
    sig_rates = (rates or {}).get("signals") or {}
    counts = {}
    for s in SIGNALS:
        r = sig_rates.get(s) or {}
        counts[s] = bool(found[s]) and not (r.get("saturated") and len(found[s]) <= (r.get("p90_count") or 0))
    # Cordon's notices keep their own rate; UKMTO's attacks are measured on their own series and are added after.
    cordon_counts = counts["notice"]
    counts["notice"] = cordon_counts or bool(surge)
    # The tightest span that holds one counted item of every signal, if there is one.
    span = None
    times = {s: sorted(tri.utc(r["at"]) for r in found[s] if tri.utc(r.get("at"))) if counts[s] else []
             for s in SIGNALS}
    times["notice"] = sorted(([tri.utc(r["at"]) for r in found["notice"] if tri.utc(r.get("at"))] if cordon_counts
                              else []) + [tri.utc(r["at"]) for r in surge])
    if all(times.values()):
        for start in sorted(t for ts in times.values() for t in ts):
            picks = [next((t for t in times[s] if t >= start), None) for s in SIGNALS]
            if all(picks):
                hours = (max(picks) - start).total_seconds() / 3600
                if span is None or hours < span["hours"]:
                    span = {"from": tri.iso(start), "to": tri.iso(max(picks)), "hours": round(hours, 1)}
    present = [s for s in SIGNALS if counts[s]]
    found["notice"] = found["notice"] + surge
    usual = [s for s in SIGNALS if found[s] and not counts[s]]
    dead = [pid for pid in LAYERS if states[pid] in ("stale", "missing")]
    if dead:
        reading = "incomplete"
    elif span and span["hours"] <= WINDOW_H:
        reading = "converged"
    elif blind:
        reading = "incomplete"
    elif present or usual:
        reading = "partial"
    else:
        reading = "none"
    lights = {w["stoplight_72h"] for w in weather}
    worst = ("unknown" if not weather or "vis" in dead else
             "red" if "red" in lights else "amber" if lights - {"green"} else "green")
    weather_text = ("No current Vis forecast inside the area." if worst == "unknown" else
                    f"Vis's 72-hour weather forecast at {len(weather)} point{'s' if len(weather) != 1 else ''} in the "
                    f"area: {worst.capitalize()} at worst.")
    caveats = ["Vis's items are forecasts at indicative thresholds, not official forecasts and not fit for flight "
               "planning. The weather is listed beside the reading as context and never counts as a signal."]
    if blind:
        caveats.append("Cordon's navigation warnings have stopped moving: the newest one anywhere is dated "
                       f"{tri.iso(newest_navwarn) if newest_navwarn else 'never'}. A new warning cannot be seen, so "
                       "the notice signal can fire only on an airspace bulletin, and this area reads incomplete "
                       "unless all three converge without it.")
    if seen is None:
        caveats.append("No archive was given: a revised airspace bulletin cannot be told from a standing one, so "
                       "only a bulletin first issued inside the lookback counts.")
    if found["connectivity"] or context["single_source_dips"]:
        caveats.append("Blackout's outages are country-level: an outage anywhere in a listed country counts for "
                       "the whole area.")
    if rates is None:
        caveats.append("No archive replay was given, so no signal was measured against its own rate in the area.")
    elif not rates["enough"]:
        caveats.append(f"Base rates need {RATE_MIN_DAYS} archived days with every layer current in the last "
                       f"{RATE_DAYS}; this area has {rates['days']}, so each signal counts as it fires.")
    for s in usual:
        r = sig_rates[s]
        caveats.append(f"{s.capitalize()} was present on {r['days_present']} of the last {rates['days']} archived "
                       f"days with every layer current, so it counts only above {r['p90_count']} "
                       f"item{'s' if r['p90_count'] != 1 else ''} (its own 90th percentile); today it has "
                       f"{len(found[s])}.")
    if context["notices_renewal"]:
        caveats.append("An airspace bulletin revised on or just before its previous expiry, with a later expiry, is "
                       "read as a scheduled renewal and listed, not counted. COP holds the bulletin's title and "
                       "dates, not its text: check EASA's page before reading a renewal as unchanged.")
    if found["press"] or context["press_other"]:
        caveats.append("Watchfloor's country items measure press reporting of a whole country for a whole day, a day "
                       "behind; they say how much was written, not what happened.")
    if uk["reports"]:
        caveats.append(f"UKMTO's reports (Royal Navy), via Wake: {UKMTO_NOTE}")
    n, ur = uk["attacks_in_lookback"], uk["rate"]
    if not ur["enough"] and n:
        caveats.append(f"UKMTO's Attack reports count toward the notice only when their number in the {LOOKBACK_H} "
                       f"hours is above its own {round(RATE_PERCENTILE * 100)}th percentile over the last {RATE_DAYS} "
                       f"days. COP holds UKMTO's reports only from {ur['history_from'] or 'today'}, not yet from "
                       f"{ur['history_needed_from']}, so the {n} in the look-back are listed as context, not counted.")
    elif ur["enough"] and n and not uk["counts"]:
        caveats.append(f"UKMTO published {n} Attack report{'s' if n != 1 else ''} in the circle in the {LOOKBACK_H} "
                       f"hours, within its own rate (90th percentile {ur['p90_count']} over the last {RATE_DAYS} days, "
                       f"an attack in the look-back on {ur['days_with_an_attack']} of them), so they are listed, not "
                       "counted.")
    elif uk["counts"]:
        caveats.append(f"UKMTO published {n} Attack reports in the circle in the {LOOKBACK_H} hours, above their own "
                       f"90th percentile ({ur['p90_count']}) over the last {RATE_DAYS} days, so they count toward the "
                       "notice. The count is UKMTO's; it says nothing about who was responsible.")
    if context["ooni"]:
        caveats.append("Blackout's OONI tier is a fixed threshold, so Iran is an anomaly there on any ordinary day. "
                       "The rate's movement against the archive is listed as context and is not a signal.")
    until = at + timedelta(days=QUESTION_DAYS)
    return {
        "join": JOIN["id"], "area": key, "area_name": area["name"],
        "circle": {"center": area["center"], "center_name": area["center_name"], "radius_km": area["radius_km"]},
        "countries": area["countries"], "read_at": tri.iso(at), "layer_states": states, "no_current_data": dead + blind,
        "signals": {s: {"present": bool(found[s]), "counts": counts[s], "items": found[s],
                        "rate": sig_rates.get(s)} for s in SIGNALS},
        "signals_present": present, "signals_at_usual_rate": usual, "tightest_span": span,
        "base_rates": None if rates is None else {k: rates[k] for k in ("days", "enough", "window_days", "min_days")},
        "reading": reading, "reading_text": READINGS[reading],
        "weather": {"state": worst, "text": weather_text, "forecasts": weather},
        "context": context, "caveats": caveats,
        "verdict_draft": None if reading != "converged" else {
            "registered": False, "probability": None, "approved_by": None,
            "question": f"Will a strike or incursion be reported in the area of the {area['name']} within "
                        f"{QUESTION_DAYS} days of {tri.iso(at)}?",
            "window_ends": tri.iso(until),
            "rule_draft": "Yes if two of Reuters, AP and AFP report a military strike on, or an armed incursion "
                          f"into, the territory or territorial waters of {', '.join(area['countries'])} dated "
                          f"inside the window ending {tri.iso(until)}. No otherwise.",
            "note": "Rare events: a base rate from the archive is needed before any probability means much."},
    }


def read(snapshot, seen=None, ooni=None, rows=None, ukmto=None):
    """rows: replay rows (raw counts) for the base rates; None reads every signal as it fires. ukmto: the archive's
    ukmto_history(), or None (the snapshot's own 30 days of reports, never enough for a rate)."""
    day = (snapshot.get("generated_at") or "")[:10]
    return {"join": JOIN, "snapshot_generated_at": snapshot.get("generated_at"),
            "windows": {"window_hours": WINDOW_H, "lookback_hours": LOOKBACK_H, "question_days": QUESTION_DAYS,
                        "rate_days": RATE_DAYS, "rate_min_days": RATE_MIN_DAYS, "watchfloor_z": WATCHFLOOR_Z,
                        "provisional": True},
            "archive_given": seen is not None,
            "records": [read_area(snapshot, k, a, seen, ooni, None if rows is None else signal_rates(rows, k, day),
                                  ukmto) for k, a in sorted(AREAS.items())]}


def replay(snapshots, ended=()):
    """How often each signal fires in each area anyway, one row per archived day and area. UKMTO's history grows
    day by day from the snapshots (and Wake's ended lists) dated up to each day, so each day reads only what COP had
    then. Rows keep raw counts: notice is Cordon's items only, and UKMTO's attacks have their own column."""
    snapshots = sorted(snapshots, key=lambda s: s.get("generated_at") or "")
    ended = sorted(ended, key=lambda s: s.get("generated_at") or "")
    seen, ooni = first_seen(snapshots), ooni_history(snapshots)
    rows, hist, e = [], ukmto_history(), 0
    for snap in snapshots:
        while e < len(ended) and (ended[e].get("generated_at") or "") <= (snap.get("generated_at") or ""):
            ukmto_add(hist, ended[e], covers=False)
            e += 1
        ukmto_add(hist, snap)
        for r in read(snap, seen, ooni, None, hist)["records"]:
            uk = r["context"]["ukmto"]
            rows.append({"day": r["read_at"][:10], "area": r["area"], "reading": r["reading"],
                         "signals_present": r["signals_present"], "no_current_data": r["no_current_data"],
                         "counts": {s: len([i for i in r["signals"][s]["items"] if i.get("source") != "ukmto"])
                                    for s in SIGNALS},
                         "ukmto_attacks": uk["attacks_in_lookback"],
                         "ukmto_counted": uk["counts"] if uk["rate"]["enough"] else None,
                         "weather": r["weather"]["state"]})
    by_area = {}
    for key in sorted(AREAS):
        mine = [r for r in rows if r["area"] == key]
        full = [r for r in mine if r["reading"] != "incomplete"]
        by_area[key] = {
            "days": len(mine), "days_with_every_layer": len(full),
            "days_each_signal_present": {s: sum(s in r["signals_present"] for r in full) for s in SIGNALS},
            "days_by_reading": {k: sum(r["reading"] == k for r in mine) for k in READINGS},
            "ukmto": {"days_with_history": sum(r.get("ukmto_counted") is not None for r in mine),
                      "days_counted": sum(bool(r.get("ukmto_counted")) for r in mine),
                      "days_with_an_attack": sum(bool(r.get("ukmto_attacks")) for r in mine)},
            "caution": "A day counts only when all five layers had current data. Blackout joined COP on 12 Nov 2026, "
                       "so earlier days are incomplete and say nothing about how often the four converge. The counts "
                       "here are raw: they are what each day's base rate is measured from."}
    return {"by_area": by_area, "rows": rows}


PUBLIC_NAME = "three-signal.json"
WHAT = ("For the Strait of Hormuz and the Gulf (900 km around Bandar Abbas, with Iran, the UAE, Oman, Qatar, Bahrain "
        "and Kuwait for country-level items): whether GPS interference (Noisefloor), a new notice (Cordon: a "
        "navigation warning that names military activity, a new or revised airspace bulletin that is not a scheduled "
        "renewal, or a US embassy security alert), a confirmed internet outage (Blackout) and unusual press reporting "
        "of violence in one of those countries (Watchfloor) fall in the area within "
        f"{WINDOW_H} hours of each other, each measured against how often it fires there anyway, with Vis's weather "
        "forecast and UKMTO's reports in the circle (via Wake) beside them. UKMTO's Attack reports add to the notice "
        "only when their number rises above its own rate there.")
NOT_A_WARNING = ("A reading says which of the four signals are present and how close together they fall. It is not "
                 "a warning, a forecast or a finding of cause, and it carries no probability. Each signal has an "
                 "innocent explanation alone. \"None\" and \"incomplete\" (a layer with no current data) are "
                 "different answers.")
DRAFT_NOTE = ("A drafted question that would let this reading be scored later. It is not a registered Verdict "
              "forecast and no likelihood is attached to it.")
PUBLIC_KEYS = ("join", "area", "area_name", "circle", "countries", "read_at", "layer_states", "no_current_data",
               "signals", "signals_present", "signals_at_usual_rate", "base_rates", "tightest_span", "reading",
               "reading_text", "weather", "context", "caveats")


def base_rate(by_area):
    """How often each signal fires anyway in the area, from the archive replay, as numbers and one sentence."""
    if not by_area:
        return None
    n, each = by_area["days_with_every_layer"], by_area["days_each_signal_present"]
    if not n:
        text = (f"None of the {by_area['days']} archived days had every layer current, so there is no base rate "
                "yet.")
    else:
        text = (f"Over {n} archived day{'s' if n != 1 else ''} with every layer current, interference was present "
                f"on {each['interference']}, a new notice on {each['notice']}, a connectivity signal on "
                f"{each['connectivity']} and unusual press reporting on {each['press']}; all four fell within the "
                f"window on {by_area['days_by_reading']['converged']}.")
    return {"text": text, "caution": by_area["caution"], **{k: by_area[k] for k in
            ("days", "days_with_every_layer", "days_each_signal_present", "days_by_reading")}}


def question_draft(v):
    """The drafted Verdict question as the public sees it, marked a draft, with no probability or approval field."""
    if not v:
        return None
    return {"status": "draft, not registered", "note": DRAFT_NOTE, "question": v["question"],
            "window_ends": v["window_ends"], "rule_draft": v["rule_draft"], "caution": v["note"]}


def public(doc):
    """The document the page and the MCP server show."""
    by_area = ((doc.get("archive_replay") or {}).get("by_area")) or {}
    records = []
    for r in doc["records"]:
        rec = {k: r[k] for k in PUBLIC_KEYS}
        rec["base_rate"] = base_rate(by_area.get(r["area"]))
        rec["question_draft"] = question_draft(r["verdict_draft"])
        records.append(rec)
    return {"join": doc["join"], "what": WHAT, "not_a_warning": NOT_A_WARNING, "readings": READINGS,
            "snapshot_generated_at": doc["snapshot_generated_at"], "windows": doc["windows"],
            "archive_given": doc["archive_given"], "records": records}


def publish_folder(path):
    """Where the public document may go: site/data and nowhere else under site/."""
    out = Path(path).resolve()
    site = (gate.ROOT / "site").resolve()
    if (out == site or site in out.parents) and out != site / "data":
        raise SystemExit("three_signal.py publishes only to site/data")
    return out


def safe_out(path):
    """The working folder, refused inside a deployed folder: the working document keeps the empty probability."""
    out = Path(path).resolve()
    for d in gate.DEPLOYED:
        folder = (gate.ROOT / d).resolve()
        if out == folder or folder in out.parents:
            raise SystemExit("three_signal.py does not write its working document inside " + d + "/")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", default=tri.PUBLISHED, help="snapshot URL, .geojson file or .geojson.gz archive copy")
    ap.add_argument("--archive", default=None, help="folder of YYYY-MM-DD.geojson.gz: revisions, OONI history, replay")
    ap.add_argument("--out", default=str(gate.ROOT / "out" / "three-signal"))
    ap.add_argument("--publish", default=None, metavar="FOLDER",
                    help=f"also write the public document, {PUBLIC_NAME}, here (the build passes site/data)")
    a = ap.parse_args(argv)
    seen = ooni = past = None
    if a.archive:
        files = sorted(Path(a.archive).glob("*.geojson.gz"))
        if not files:
            raise SystemExit(f"no *.geojson.gz in {a.archive}")
        snaps = [tri.load(f) for f in files]
        ended = [tri.load(f) for f in sorted(Path(a.archive).glob("*.wake-ended.json.gz"))]
        seen, ooni, past = first_seen(snaps), ooni_history(snaps), replay(snaps, ended)
        uk_hist = ukmto_history(snaps, ended)
    else:
        uk_hist = None
    doc = read(tri.load(a.snapshot), seen, ooni, past["rows"] if past else None, uk_hist)
    doc["archive_replay"] = past
    stamp = (doc["snapshot_generated_at"] or "unknown").replace(":", "")
    out = safe_out(a.out)
    if a.publish:
        gate.write(JOIN, public(doc), publish_folder(a.publish), (PUBLIC_NAME,))
    verdict = gate.write(JOIN, doc, out, (f"three-signal-{stamp}.json", "latest.json"))
    print(f"snapshot {doc['snapshot_generated_at']}  gate: {verdict['review_class']}"
          f"{'  published ' + PUBLIC_NAME if a.publish else ''}")
    for r in doc["records"]:
        print(f"{r['area_name']}: {r['reading']}; signals counted: {', '.join(r['signals_present']) or 'none'}; "
              f"at their usual rate: {', '.join(r['signals_at_usual_rate']) or 'none'}; "
              f"weather {r['weather']['state']}; layers {r['layer_states']}")
        for s in SIGNALS:
            for it in r["signals"][s]["items"]:
                print(f"    {s}: {it['id']}  {it.get('at')}  {str(it['title'])[:90]}")
        if r["tightest_span"]:
            print(f"    tightest span holding all four: {r['tightest_span']['hours']} h")
        c = r["context"]
        print(f"    context: {len(c['notices_other'])} other new warnings, {len(c['notices_standing'])} standing "
              f"notices, {len(c['notices_renewal'])} renewals, {len(c['single_source_dips'])} single-source dips, "
              f"{len(c['embassy_alerts'])} other embassy alerts, {len(c['press_other'])} other press items, "
              f"{len(c['interference_ended'])} interference ended")
        u = c["ukmto"]
        print(f"    UKMTO: {len(u['reports'])} reports in the circle in the look-back, {u['attacks_in_lookback']} "
              f"Attack; history from {u['rate']['history_from']} (needs {u['rate']['history_needed_from']}); "
              f"p90 {u['rate']['p90_count']}; {'COUNTED' if u['counts'] else 'context only'}")
        for o in c["ooni"]:
            print(f"    OONI {o['country']}: {o['rate']}%  lowest in archive {o['lowest_in_archive']}  "
                  f"{'MOVED' if o['moved'] else 'no move'}")
    if past:
        for key, b in past["by_area"].items():
            print(f"archive, {key}: {b['days_with_every_layer']} of {b['days']} days had every layer; signal days "
                  f"{b['days_each_signal_present']}; readings {b['days_by_reading']}; UKMTO {b['ukmto']}")
    else:
        print("no archive given: run with --archive out/archive")
    print(f"wrote {out / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
