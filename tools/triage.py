"""Outage triage: what the other layers show under each Blackout outage.

A named join (scenario 2 of D:/Projects/_ideas/convergence-scenarios.md), not a count of programs. For every
Blackout outage in a COP snapshot it records what Elements, Cordon and Watchfloor show for that country around
that hour, including "nothing found" and "no current data", which are different answers. It states a reading of
the pattern and drafts the Verdict question. It never states a probability: that is the analyst's.

US state power outages (Blackout's BO-IND-14: a state where ODIN's reporting utilities show more than 50,000 meters
out) are read against Elements for that state, not the whole country (rules approved by Tom, 13 Nov 2026): NWS
alerts by their own county codes, point hazards within STATE_NEAR_KM of the state, and warnings for hazards that
take power lines down counted at Elements' Severe level. Cordon and Watchfloor carry nothing at US-state level, so
they answer "Not covered" and such an outage reads natural, open or incomplete, never deliberate. Its drafted
question asks whether the state is still over the line 24 hours later, in the exact form Verdict settles from
EAGLE-I.

    python tools/triage.py                              # read the published snapshot, write out/triage/
    python tools/triage.py --snapshot site/data/today.geojson
    python tools/triage.py --snapshot <file.geojson.gz> # an archived day
    python tools/triage.py --snapshot site/data/today.geojson --publish site/data   # the build: writes triage.json

PUBLISHED since step 5 (JOIN["published"]): the build runs it after the snapshot, and --publish writes the public
document, triage.json, which the COP page and the MCP server show. The public document carries the drafted
Verdict question (Tom, 13 Nov 2026) as "question_draft", labelled a draft that is not registered and has no
probability; registering one stays Tom's. Every write goes through tools/gate.py, which classes the join
from the layers it declares and the items it cites. It reads hazards, outages, travel and embassy notices and press
attention; Cordon's navigation warnings and conflict-zone airspace bulletins are left out, because they place
military activity and would make this join a held one. Its notices are the State Department's advisories, embassy
alerts and operating status, and since 14 Nov 2026 UK FCDO, Global Affairs Canada and New Zealand MFAT SafeTravel
travel advice, counted only when Cordon saw their level or advice change. CDC's travel health notices are open at the gate but not read: a
disease notice says nothing about whether a network was cut, and one update to CDC's global measles notice
would put a fresh notice in 235 countries.

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
# County FIPS -> [state, name, lat, lon]: the Census 2025 Gazetteer's internal points, the copy Blackout places ODIN's
# counties with (blackout/us_counties.json; Elements' catalog has the same points). Public domain.
US_COUNTIES = ROOT / "config" / "us_counties.json"
PUBLISHED = "https://cop.theintelligence.company/data/today.geojson"
UA = "tic-cop/0.1 (+https://cop.theintelligence.company)"

# The gate decides the review class from these inputs. "published" was turned on in step 5 (13 Nov 2026) on the
# convergence branch; it reaches the site only when Tom merges that branch to main.
JOIN = {"id": "outage-triage", "version": "0.7", "published": True,
        "inputs": ["blackout", "elements", "watchfloor", "cordon:advisory", "cordon:alert", "cordon:posture",
                   "cordon:fcdo", "cordon:canada", "cordon:safetravel"]}
# The Cordon kinds triage reads, all on the gate's open list. CDC (kind cdc) is open there too but not read here.
NOTICE_KINDS = {"advisory", "alert", "posture", "fcdo", "canada", "safetravel"}
# Travel advice that is re-dated for every edit (contacts, a natural-disaster section, an air-traffic note): it
# counts only when Cordon marks the item anomaly, i.e. Cordon saw its level or its advice change (Tom, 14 Nov
# 2026). Measured on the 14 Nov snapshot: 16 of the 17 FCDO and Canada items dated in the 48-hour window were
# such edits. Cordon's anomaly is not direction-aware, so the level is still compared with the archive.
# SafeTravel (14 Nov 2026) the same: MFAT dates its advice by the day of any page update, and Cordon marks it
# anomaly only when the level or the regional note changed.
CHANGE_ONLY_KINDS = {"fcdo", "canada", "safetravel"}
# FCDO's "advise against" statements, strongest first. A country's rank is which of them are in force, compared
# in this order, so adding any statement raises it and removing one without adding a stronger one lowers it.
FCDO_STATEMENTS = ("avoid_all_travel_to_whole_country", "avoid_all_but_essential_travel_to_whole_country",
                   "avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts")
# MFAT SafeTravel's levels, lowest first, as Cordon names them (cordon/safetravel.py ORDER).
SAFETRAVEL_LEVELS = ("normal", "low", "medium", "high", "avoid")
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
POWER_OUTAGE = "BO-IND-14"      # Blackout: a US state with more than 50,000 meters out at ODIN's reporting utilities

# US state power outages (Tom, 13 Nov 2026). The 72- and 6-hour windows above apply unchanged.
STATE_NEAR_KM = 100      # a point hazard counts for a state within this of it (its nearest county internal point)
# NWS warnings (VTEC phenomenon, significance W) for hazards that take power lines down. For a state power outage they
# count at Elements' Severe level, which Elements files as routine; anything Elements rates anomaly counts as well.
# Floods, freezes and marine warnings are not on the list. EH and WC are the codes Extreme Heat and Extreme Cold
# had before NWS renamed them (2025 and 2024).
POWER_HAZARDS = {"HW": "High Wind", "EW": "Extreme Wind", "WS": "Winter Storm", "IS": "Ice Storm", "BZ": "Blizzard",
                 "LE": "Lake Effect Snow", "HU": "Hurricane", "TR": "Tropical Storm", "TY": "Typhoon",
                 "SS": "Storm Surge", "TO": "Tornado", "SV": "Severe Thunderstorm", "EC": "Extreme Cold",
                 "WC": "Extreme Cold", "XH": "Extreme Heat", "EH": "Extreme Heat", "FW": "Red Flag",
                 "DS": "Dust Storm"}
POWER_LINE = 50_000      # DOE-417 criterion 12, Blackout's line for BO-IND-14
POWER_STILL_OUT_H = 24   # the drafted question: still over the line this long after ODIN's reading...
POWER_CHECK_H = 2        # ...for an hour or more inside a window this long (EAGLE-I's rule needs 4 runs, an hour)
EAGLEI_DOI = "10.6084/m9.figshare.24237376"
# Volcano alert levels, ground alert first, then the aviation colour. USGS re-sends each volcano's status every day;
# a re-send at the same or a lower level is not a new hazard (Tom, 13 Nov 2026, for every country).
VOLCANO_ALERT = {"NORMAL": 0, "UNASSIGNED": 0, "ADVISORY": 1, "WATCH": 2, "WARNING": 3}
VOLCANO_COLOUR = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}

READINGS = {
    "incomplete": "A layer has no current data, so this outage cannot be triaged yet. Silence is not quiet.",
    "unconfirmed": "Blackout calls this a single-source dip, not a confirmed outage. Context is listed, no reading.",
    "natural": "A significant natural hazard sits under this outage in place and time. A natural cause has support.",
    "deliberate": "No new hazard under it, and a notice was issued or raised in the two days before it, or press "
                  "attention is above this country's own normal. A deliberate cut is worth a call.",
    "open": "No new hazard, no fresh notice and no unusual attention. Nothing here separates an accident from a "
            "deliberate cut.",
}
# A US state power outage: Cordon and Watchfloor don't cover US states, so these three are the only readings.
POWER_READINGS = {
    "incomplete": "Elements has no current data, so this outage cannot be triaged yet. Silence is not quiet.",
    "natural": "A hazard that takes power lines down began in or near this state in the 72 hours before the reading. "
               "A weather or natural cause has support.",
    "open": "No hazard that takes power lines down began in or near this state in the 72 hours before the reading. "
            "Nothing here separates a storm already past, failed equipment, a utility's planned shutoff or an attack.",
}
POWER_CAVEATS = (
    "Cordon and Watchfloor carry nothing at US-state level, so no layer here can point to a deliberate cut: a state "
    "power outage reads natural, open or incomplete, never deliberate.",
    "One ODIN reading, summed over the state's utilities that report to ODIN. DOE's line is more than 50,000 "
    "customers out for an hour or more, and a meter is close to, not the same as, a customer.",
)
# Elements' feed carries active items only; its list of items that ended in the last 7 days (data/ended.geojson,
# written from its history log) brings back a warning that ran out before this snapshot. Measured 1-14 Nov 2026: the
# live feed alone still held a power-line warning in 19% of the state-times when one had begun in the 72-hour window.
ENDED_STALE_H = 12
ENDED_MISSING = ("Elements' list of hazards that ended in the last 7 days could not be read, so a hazard that ended "
                 "before this snapshot is missing: \"open\" can mean the storm has passed.")
ENDED_OLD = (f"Elements' list of hazards that ended is more than {ENDED_STALE_H} hours older than this snapshot, so a "
             "hazard that ended in between is missing.")
RED_FLAG = ("A Red Flag Warning counts as a hazard even where a utility cut power on purpose for fire safety (a "
            "public-safety power shutoff): the hazard is still the reason.")
VOLCANO_UNKNOWN = ("A volcano alert in the window has no earlier level in COP's archive, so it is counted without "
                   "knowing whether it was raised.")
NOT_COVERED = {
    "cordon": "Not covered: Cordon carries travel advisories and embassy notices for other countries, nothing at "
              "US-state level, so it cannot point to a deliberate cut here.",
    "watchfloor": "Not covered: Watchfloor follows press attention in conflict zones, nothing at US-state level, so "
                  "it cannot point to a deliberate cut here.",
}
EAGLEI_CAVEAT = ("EAGLE-I reads more utilities than ODIN (about 92% of US customers by 2022), so a state over the line "
                 "at ODIN's reporting utilities alone is rarer still. Coverage grew over these years, so earlier "
                 "years may count fewer.")
EAGLEI_SOURCE = (f"ORNL's EAGLE-I outage history, 2018-2025 (figshare doi:{EAGLEI_DOI}, CC BY 4.0), as Blackout "
                 "attaches it to the item.")


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
    if isinstance(p.get("cop_ended"), dict):
        row["ended_at"] = p["cop_ended"].get("ended_at")
    row.update(extra)
    return row


def with_ended(features, ended, snapshot_at=None):
    """(features, info): the snapshot's items plus a program's recently ended ones (Elements' data/ended.geojson),
    each marked cop_ended with its history (first_seen, last_seen, ended_at, end_reason). An item still live in the
    snapshot is not added twice. info: the list's state (fresh, old: generated more than ENDED_STALE_H hours before
    the snapshot, or missing), how many items it added and when it was generated."""
    if not isinstance(ended, dict) or not isinstance(ended.get("features"), list):
        return features, {"state": "missing", "added": 0, "generated_at": None}
    pid = ended.get("program") or "elements"
    live = {f["properties"].get("id") for f in features}
    extra = []
    for f in ended["features"]:
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict) or not p.get("id") or p.get("id") in live:
            continue
        q = {**p, "cop_program": pid, "cop_ended": f.get("history") if isinstance(f.get("history"), dict) else {}}
        extra.append({"type": "Feature", "geometry": f.get("geometry"), "properties": q})
        live.add(p["id"])
    gen = utc(ended.get("generated_at"))
    old = bool(gen and snapshot_at and snapshot_at - gen > timedelta(hours=ENDED_STALE_H))
    return features + extra, {"state": "old" if old else "fresh", "added": len(extra),
                              "generated_at": ended.get("generated_at")}


def ended_caveat(info):
    return None if (info or {}).get("state") == "fresh" else ENDED_OLD if (info or {}).get("state") == "old" \
        else ENDED_MISSING


def _interval(p):
    """(start, end) of what an item describes, from its own fields. An NWS alert starts at its onset: its data_as_of
    moves with every update of the same warning (Tom, 13 Nov 2026)."""
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    at = utc(p.get("data_as_of"))
    onset = utc(m.get("onset")) if _kind(p.get("id")) == "nws" else None
    start = utc(m.get("from")) or onset or at
    end = utc(m.get("to")) or utc(p.get("valid_until")) or at
    if start and end and end < start:
        end = start
    return start, end


def volcano_level(p):
    """A volcano item's level as one number (ground alert, then aviation colour), or None."""
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    alert = VOLCANO_ALERT.get(str(m.get("alert_level") or "").upper())
    if alert is None:
        return None
    return alert * 10 + VOLCANO_COLOUR.get(str(m.get("aviation_color") or "").upper(), 0)


def volcano_change(item_id, dated, level, levels):
    """(raised | unchanged | lowered | unknown, since): a volcano's level against its earlier readings in COP's
    archive, and since when it has been at this level. levels: {id: [[data_as_of, level], ...]} from
    triage_rates.py, or None. Unknown when the archive has no earlier reading of it."""
    earlier = sorted(((utc(d), lv) for d, lv in (levels or {}).get(item_id, []) if utc(d) and dated and utc(d) < dated),
                     key=lambda r: r[0])
    if level is None or not earlier:
        return "unknown", None
    since = dated
    for d, lv in reversed(earlier):
        if lv is None:
            return "unknown", None
        if lv != level:
            return ("raised" if level > lv else "lowered"), iso(since)
        since = d
    return "unchanged", iso(since)


def _power_warning(p):
    """The VTEC phenomenon when an Elements NWS item is a warning for a hazard that takes power lines down."""
    if _kind(p.get("id")) != "nws":
        return None
    parts = str((p.get("measures") or {}).get("vtec") or "").split(".")
    return parts[1] if len(parts) >= 3 and parts[2] == "W" and parts[1] in POWER_HAZARDS else None


def _classify(f, at, lo, hi, volcano_levels=None, power=False):
    """(bucket, row) for one Elements item against the hazard window, or (None, None) outside it. significant: a
    significant hazard that began inside the window; standing: one that began before it, or a volcano alert re-sent
    at the same or a lower level; minor; background (the standing background kinds). power: a state power outage,
    where warnings for hazards that take power lines down count too."""
    p = f["properties"]
    kind = _kind(p.get("id"))
    start, end = _interval(p)
    if not start or end < lo or start > hi:
        return None, None
    extra, standing_only = {}, False
    if kind == "usgs-volcano":
        change, since = volcano_change(p.get("id"), utc(p.get("data_as_of")), volcano_level(p), volcano_levels)
        extra["level_change"] = change
        if change == "raised":
            start = utc(since) or start
            extra["raised_at"] = since
        elif change in ("unchanged", "lowered"):
            standing_only = True
    warning = _power_warning(p) if power else None
    if warning:
        extra["power_hazard"] = POWER_HAZARDS[warning]
    row = _row(p, hours_before_outage=round((at - start).total_seconds() / 3600, 1), **extra)
    if kind in BACKGROUND:
        return "background", row
    if p.get("tier") in ("anomaly", "warning") or warning:
        return ("significant" if start >= lo and not standing_only else "standing"), row
    return "minor", row


def hazards(features, cc, at, volcano_levels=None):
    """Elements items under the outage: in or near the country, overlapping the hazard window. A significant hazard
    counts only if it began inside the window; one that began earlier and is still running is "standing"."""
    lo, hi = at - timedelta(hours=HAZARD_BEFORE_H), at + timedelta(hours=HAZARD_AFTER_H)
    out = {"significant": [], "standing": [], "minor": [], "background": []}
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
        bucket, row = _classify(f, at, lo, hi, volcano_levels)
        if bucket:
            row["km_from_country"] = None if km is None else round(km)
            out[bucket].append(row)
    return out


_COUNTIES, _STATES = None, None


def county_table():
    """County FIPS -> [state, name, lat, lon] (config/us_counties.json)."""
    global _COUNTIES
    if _COUNTIES is None:
        _COUNTIES = json.loads(US_COUNTIES.read_text(encoding="utf-8"))["counties"]
    return _COUNTIES


def us_states():
    """{state: {"points": [(lat, lon), ...], "box": (min lat, max lat, min lon, max lon)}}, from the county table."""
    global _STATES
    if _STATES is None:
        pts = {}
        for st, _name, lat, lon in county_table().values():
            pts.setdefault(st, []).append((lat, lon))
        _STATES = {st: {"points": p, "box": (min(a for a, _ in p), max(a for a, _ in p),
                                              min(b for _, b in p), max(b for _, b in p))}
                   for st, p in sorted(pts.items())}
    return _STATES


def km_to_state(geometry, st):
    """Distance in km from a Point, or the nearest point of a MultiPoint, to the state's nearest county internal
    point, or None. A point inside a county lies within about that county's size of its internal point."""
    if not isinstance(geometry, dict):
        return None
    if geometry.get("type") == "Point":
        pts = [geometry["coordinates"]]
    elif geometry.get("type") == "MultiPoint":
        pts = geometry["coordinates"]
    else:
        return None
    s = us_states().get(st)
    if not s:
        return None
    lat0, lat1, lon0, lon1 = s["box"]
    margin = STATE_NEAR_KM / 111.0 + 1.0   # degrees: a quick test before the distances
    best = None
    for x, y in (pt[:2] for pt in pts):
        if not (lat0 - margin <= y <= lat1 + margin):
            continue
        if not (lon0 - margin / max(math.cos(math.radians(y)), 0.2) <= x <= lon1 + margin / max(
                math.cos(math.radians(y)), 0.2)) and lon1 - lon0 < 180:
            continue
        for lat, lon in s["points"]:
            d = _km(y, x, lat, lon)
            if best is None or d < best:
                best = d
    return best


_NEAREST = {}


def nearest_state(geometry):
    """The state whose county internal point is nearest a Point (or any point of a MultiPoint): which state a point
    inside the US lies in, near enough. It matters where counties are vast: Great Sitkin volcano, inside the
    Aleutians West Census Area, is 292 km from that area's internal point."""
    t = geometry.get("type") if isinstance(geometry, dict) else None
    pts = [geometry["coordinates"]] if t == "Point" else geometry["coordinates"] if t == "MultiPoint" else []
    found = set()
    for pt in pts:
        key = (round(pt[0], 4), round(pt[1], 4))
        if key not in _NEAREST:
            _NEAREST[key] = min((_km(key[1], key[0], lat, lon), st)
                                for st, _name, lat, lon in county_table().values())[1]
        found.add(_NEAREST[key])
    return found


def _in_polygon(lon, lat, geometry):
    """Whether a point is inside a Polygon or MultiPolygon (even-odd over every ring, so holes stay outside)."""
    t = geometry.get("type") if isinstance(geometry, dict) else None
    polys = [geometry["coordinates"]] if t == "Polygon" else geometry["coordinates"] if t == "MultiPolygon" else []
    for rings in polys:
        inside = False
        for ring in rings:
            for i in range(len(ring)):
                (x1, y1), (x2, y2) = ring[i - 1][:2], ring[i][:2]
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
        if inside:
            return True
    return False


def _place(f, st, point):
    """Where an Elements item sits for a US state, as a dict for its row, or None when it is not in or near it.
    NWS alerts by their own county codes. A point Elements places in the US (or the territory) is in the state whose
    county internal point is nearest; any point within STATE_NEAR_KM of the state's county points is near it. An area
    by the state's county points, except the background kinds (SPC's risk areas, a forecast), which are listed only
    over the outage's own point. An item for the whole US with no county or usable point is "country": it cannot be
    placed in a state."""
    p = f["properties"]
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    table = county_table()
    codes = [c for c in m.get("counties") or [] if c in table]
    if codes:
        return {"placed": "county"} if any(table[c][0] == st for c in codes) else None
    g = f.get("geometry") if isinstance(f.get("geometry"), dict) else None
    cc = p.get("countries") or []
    if not g or p.get("precision_class") in ("country", "none"):
        return {"placed": "country"} if ("US" in cc or st in cc) else None
    if g.get("type") in ("Point", "MultiPoint"):
        km = km_to_state(g, st)
        if km is not None and km <= STATE_NEAR_KM:
            return {"placed": "near", "km_from_state": round(km)}
        if ("US" in cc or st in cc) and st in nearest_state(g):
            return {"placed": "in", "km_from_state": None if km is None else round(km)}
        return None
    if g.get("type") in ("Polygon", "MultiPolygon"):
        if _kind(p.get("id")) in BACKGROUND:
            return {"placed": "over_outage"} if point and _in_polygon(point[0], point[1], g) else None
        pts = us_states().get(st, {}).get("points", [])
        return {"placed": "area"} if any(_in_polygon(lon, lat, g) for lat, lon in pts) else None
    return None


def state_hazards(features, st, at, point=None, volcano_levels=None):
    """Elements items under a US state power outage: in or near the state, overlapping the hazard window. point:
    the outage's own [lon, lat] (Blackout weights it by meters out), or None. unplaced: significant hazards for the
    whole US that name no county or point, listed and never counted (minor ones are left out)."""
    lo, hi = at - timedelta(hours=HAZARD_BEFORE_H), at + timedelta(hours=HAZARD_AFTER_H)
    out = {"significant": [], "standing": [], "minor": [], "background": [], "unplaced": []}
    for f in features:
        if f["properties"].get("cop_program") != "elements":
            continue
        bucket, row = _classify(f, at, lo, hi, volcano_levels, power=True)
        if not bucket:
            continue
        place = _place(f, st, point)
        if not place:
            continue
        row.update(place)
        if place["placed"] == "country":
            if bucket in ("significant", "standing"):
                out["unplaced"].append(row)
            continue
        out[bucket].append(row)
    return out


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


def notice_level(p):
    """A notice's level for comparing it with the one it replaced, or None. State's advisories: their level (1-4).
    Canada: [level, regional advisories 1 or 0], so removing a regional advisory at the same level is a lowering.
    FCDO: [1 or 0 for each of FCDO_STATEMENTS]. SafeTravel: [place in SAFETRAVEL_LEVELS, regional note 1/0].
    Lists compare in order, as the archive stores them."""
    kind = _kind(p.get("id"))
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    if kind == "advisory":
        return m.get("level")
    if kind == "canada":
        return None if m.get("level") is None else [m["level"], 1 if m.get("regional") else 0]
    if kind == "fcdo":
        st = m.get("alert_status")
        return [1 if s in st else 0 for s in FCDO_STATEMENTS] if isinstance(st, list) else None
    if kind == "safetravel":
        lv = m.get("level")
        return [SAFETRAVEL_LEVELS.index(lv), 1 if m.get("regional_note") else 0] if lv in SAFETRAVEL_LEVELS else None
    return None


LEVELLED_KINDS = {"advisory", "canada", "fcdo", "safetravel"}


def notices(features, cc, at, levels=None):
    """Cordon's travel and embassy notices for the country, against the outage's time.

    recent: dated in the NOTICE_RECENT_H before the outage and not a lowering or a re-issue at the same level; these
    count toward a reading. recent_not_raised: dated in the window but lowered or unchanged. standing: older.
    after: dated after the outage. An advisory whose earlier level is not in the archive counts, marked "unknown".
    FCDO, Canada and SafeTravel advice that Cordon did not mark as changed is an edit at the same level: direction
    "unchanged", not counted."""
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
        if kind in CHANGE_ONLY_KINDS and p.get("tier") not in ("anomaly", "warning"):
            row["direction"] = "unchanged"     # Cordon saw no change of level or advice: an edit
        elif kind in LEVELLED_KINDS:
            row["direction"] = _direction(p.get("id"), dated, notice_level(p), levels)
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


def us_state_of(p):
    """The state code of a Blackout BO-IND-14 item, from its id (blackout:us-power:<state>:<UTC day>), or None."""
    parts = str(p.get("id")).split(":")
    return parts[2].upper() if len(parts) >= 4 and parts[1] == "us-power" and parts[2] else None


def _state_name(p, st):
    """The state's name as Blackout's title gives it ("California: 13,723 meters ..."), or its code."""
    name = str(p.get("title") or "").split(":")[0].strip()
    return name if re.fullmatch(r"[A-Za-z .]+", name) else st


def not_covered(pid, features, state):
    """A layer that carries nothing at US-state level. Items for the whole US, if it ever carries any, are listed
    and not counted."""
    rows = []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != pid or "US" not in (p.get("countries") or []):
            continue
        if pid == "cordon" and _kind(p.get("id")) not in NOTICE_KINDS:
            continue
        rows.append(_row(p))
    return {"state": state, "covered": False, "found": False, "country_wide": rows}


def eaglei_rule(st, name, start, end, line=POWER_LINE):
    """The resolution in the exact form Verdict's auto-resolve reads (verdict tools/auto_resolve.py, EAGLEI_RE), the
    same text as Blackout's eaglei.verdict_rule."""
    f = "%Y-%m-%d %H:%M"
    return (f"Yes if EAGLE-I's outage history (ORNL, figshare doi:{EAGLEI_DOI}) shows {name} ({st}) with more than "
            f"{line:,} customers out, its counties summed in each 15-minute run, in 4 or more runs with no more than "
            f"3 runs under the line between them, between {start.strftime(f)} and {end.strftime(f)} UTC. "
            "No otherwise.")


def eaglei_rate(br):
    """The state's outage base rate from EAGLE-I's history, as Blackout attaches it to the item, or None."""
    if not isinstance(br, dict) or not br.get("text"):
        return None
    keep = {k: br.get(k) for k in ("years", "per_year", "month", "in_month", "hours_median", "hours_p90", "text")}
    return {**keep, "caveat": EAGLEI_CAVEAT, "source": EAGLEI_SOURCE}


def power_question(st, name, at, br):
    """The drafted Verdict question for a state power outage: still over the line 24 hours after ODIN's reading,
    settled by EAGLE-I with the rule Verdict already reads (Tom, 13 Nov 2026)."""
    base = at.replace(second=0, microsecond=0)
    start = base + timedelta(hours=POWER_STILL_OUT_H)
    end = start + timedelta(hours=POWER_CHECK_H)
    year = (end - timedelta(minutes=1)).year
    ref = (f"EAGLE-I, {br['years'][0]}-{br['years'][1]}: half of {name}'s outages over the line lasted about "
           f"{br['hours_median']:g} hours or less, 1 in 10 more than about {br['hours_p90']:g} hours."
           if isinstance(br, dict) and br.get("hours_median") is not None and br.get("hours_p90") is not None
           and br.get("years") else "No EAGLE-I base rate came with this item.")
    return {
        "registered": False, "probability": None, "approved_by": None,
        "question": f"Will {name} still have more than {POWER_LINE:,} customers out {POWER_STILL_OUT_H} hours after "
                    f"ODIN's reading of {iso(at)}?",
        "window_ends": iso(end),
        "rule_draft": eaglei_rule(st, name, start, end),
        "caution": None, "rise_form": None,
        "reference": ref,
        "known_at": f"{year + 1}-04-30T00:00:00Z",
        "notes": [
            f"EAGLE-I settles it only once figshare publishes {year}'s file, in the first months of {year + 1} "
            "(2025's came on 24 Feb 2026).",
            "EAGLE-I counts more utilities than ODIN (about 92% of US customers by 2022), so it can show the state "
            "over the line when ODIN's utilities alone are not: the question leans Yes.",
            "A new outage over the line inside the window would also settle it Yes.",
            "ODIN's reading moves with each Blackout run (every 4 hours), so this window moves with it until the "
            "question is registered.",
        ],
    }


def power_record(f, features, states, history=None, ended_info=None):
    """One record for a Blackout BO-IND-14 item: a US state over the line, read against Elements for that state."""
    p = f["properties"]
    st, at = us_state_of(p), utc(p.get("data_as_of"))
    if not st or not at:
        return None
    g = f.get("geometry") if isinstance(f.get("geometry"), dict) else {}
    point = g["coordinates"][:2] if g.get("type") == "Point" else None
    haz = state_hazards(features, st, at, point, (history or {}).get("volcano_levels"))
    dead = ["elements"] if states["elements"] in ("stale", "missing") else []
    reading = "incomplete" if dead else "natural" if haz["significant"] else "open"
    name = _state_name(p, st)
    ref = p.get("baseline_ref") if isinstance(p.get("baseline_ref"), dict) else {}
    br = ref.get("base_rate")
    caveats = list(POWER_CAVEATS)
    if ended_caveat(ended_info):
        caveats.append(ended_caveat(ended_info))
    if history is None:
        caveats.append("No archive baseline was given: volcano alert levels are not compared with the ones they "
                       "replaced. Run tools/triage_rates.py first.")
    if any(h.get("level_change") == "unknown" for h in haz["significant"]):
        caveats.append(VOLCANO_UNKNOWN)
    if any(h.get("power_hazard") == "Red Flag" for h in haz["significant"]):
        caveats.append(RED_FLAG)
    if haz["standing"]:
        caveats.append("A hazard that began before the window, or a volcano alert re-sent at the same level, is "
                       "still running in or near this state. It is listed as standing and does not make the reading "
                       "natural.")
    if haz["unplaced"]:
        caveats.append(f"{_n(len(haz['unplaced']), 'significant hazard')} in the US name no county or usable point, "
                       "so they cannot be placed in this state and are not counted.")
    return {
        "join": JOIN["id"], "country": "US", "outage_kind": "power", "us_state": st, "us_state_name": name,
        "outage": _row(p, confirmed=None, indicator_id=p.get("indicator_id"), summary=p.get("summary")),
        "layers": {
            "elements": {"state": states["elements"], "scope": "state", **haz,
                         "found": bool(haz["significant"] or haz["minor"])},
            "cordon": not_covered("cordon", features, states["cordon"]),
            "watchfloor": not_covered("watchfloor", features, states["watchfloor"]),
        },
        "no_current_data": dead,
        "ooni_now": None,
        "reading": reading, "reading_text": POWER_READINGS[reading],
        "caveats": caveats,
        "eaglei": eaglei_rate(br),
        "verdict_draft": power_question(st, name, at, br),
    }


def triage(snapshot, history=None, ended=None):
    """One record per Blackout outage in the snapshot. history: triage_rates.py's "history" block (each advisory's
    earlier levels, each country's share of days with raised attention), or None when there is no archive. ended:
    Elements' list of items that ended in the last 7 days (data/ended.geojson), or None when it could not be read;
    the layer states come from the live snapshot alone."""
    levels = (history or {}).get("advisory_levels")
    shares = (history or {}).get("attention_share")
    volcanoes = (history or {}).get("volcano_levels")
    features = [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]
    states = {pid: layer_state(snapshot, features, pid) for pid in LAYERS + ("blackout",)}
    features, ended_info = with_ended(features, ended, utc(snapshot.get("generated_at")))
    ooni = {}
    for f in features:
        p = f["properties"]
        if p.get("cop_program") == "blackout" and p.get("indicator_id") == OONI_BLOCKING and not p.get("cop_stale"):
            for cc in p.get("countries") or []:
                ooni[cc] = _row(p)
    records = []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "blackout" or p.get("cop_stale"):
            continue
        if p.get("indicator_id") == POWER_OUTAGE:
            rec = power_record(f, features, states, history, ended_info)
            if rec:
                records.append(rec)
            continue
        if _kind(p.get("id")) != "outage":
            continue
        at = utc(p.get("data_as_of"))
        for cc in p.get("countries") or []:
            if not at:
                continue
            haz, note = hazards(features, cc, at, volcanoes), notices(features, cc, at, levels)
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
            caveats = [ended_caveat(ended_info)] if ended_caveat(ended_info) else []
            if not att["data_reaches_outage"] and "watchfloor" not in dead:
                caveats.append(f"Watchfloor's newest data ({att['newest_data']}) ends before this outage, so it "
                               "shows attention before the outage, not during it. Run again after its next day "
                               "is in.")
            if history is None:
                caveats.append("No archive baseline was given: raised attention is counted without checking it "
                               "against this country's own normal, and advisory and volcano alert levels are not "
                               "compared with the ones they replaced. Run tools/triage_rates.py first.")
            if any(n.get("direction") == "unknown" for n in note["recent"]):
                caveats.append("An advisory or travel advice in the window has no earlier level in the archive, so "
                               "it is counted without knowing whether it was raised.")
            if any(h.get("level_change") == "unknown" for h in haz["significant"]):
                caveats.append(VOLCANO_UNKNOWN)
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
                "join": JOIN["id"], "country": cc, "outage_kind": "internet", "us_state": None,
                "us_state_name": None,
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
                "eaglei": None,
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
                        "hazard_near_km": HAZARD_NEAR_KM, "state_near_km": STATE_NEAR_KM,
                        "power_still_out_hours": POWER_STILL_OUT_H, "notice_recent_hours": NOTICE_RECENT_H,
                        "attention_usual_share": ATTENTION_USUAL_SHARE, "ooni_window_hours": OONI_WINDOW_H,
                        "ooni_rise_points": OONI_RISE_POINTS, "provisional": True},
            "baseline_given": history is not None, "ended_items": ended_info,
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
    sb = rates.get("state_background")
    doc["base_rates"] = {"country_days": n, "would_read": b["would_read"],
                         "archive": rates["archive"], "cautions": rates["cautions"],
                         "states": None if not sb else {"state_days": sb["state_days"],
                                                        "would_read": sb["would_read"],
                                                        "cautions": sb["cautions"]}}
    for r in doc["records"]:
        if r.get("outage_kind") == "power":
            r["base_rate"] = _state_rate(r, sb)
            continue
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


def _state_rate(r, sb):
    """How often a US state power outage would have read the way this one did, whatever its cause, from COP's
    archive (triage_rates.py's state_background), or None."""
    share = (sb or {}).get("would_read", {}).get(r["reading"])
    if share is None:
        return None
    n, days = sb["state_days"], sb.get("by_state_days") or 0
    own = (sb.get("by_state") or {}).get(r["us_state"], {"natural": 0.0, "open": 1.0})
    here = own.get(r["reading"])
    return {"share_of_state_days": share, "share_of_this_states_days": here,
            "text": f"A state power outage would have read \"{r['reading']}\" whatever its cause on {share:.1%} of "
                    f"{n} state-days, and on {here:.0%} of {days} days in {r['us_state_name']}.",
            "cautions": sb["cautions"]}


def _n(n, one):
    return f"{n} {one}{'' if n == 1 else 's'}"


def _ended_note(rows):
    n = sum(1 for r in rows if r.get("ended_at"))
    if not n:
        return []
    return [f"{'It' if len(rows) == 1 else (str(n) + ' of them' if n < len(rows) else 'All of them')} had ended "
            "before this snapshot."]


def showed(name, layer):
    """One plain line on what a layer showed. "Nothing found", "No current data" and "Not covered" are different
    answers."""
    if layer.get("covered") is False:
        text = NOT_COVERED[name]
        if layer.get("country_wide"):
            text += f" {_n(len(layer['country_wide']), 'item')} for the whole US listed, not counted."
        return text
    if layer["state"] in ("stale", "missing"):
        return "No current data."
    if name == "elements" and layer.get("scope") == "state":
        sig, minor, standing = layer["significant"], layer["minor"], layer["standing"]
        if sig:
            parts = [f"{_n(len(sig), 'hazard')} that {'takes' if len(sig) == 1 else 'take'} power lines down began "
                     f"in or near this state in the {HAZARD_BEFORE_H} hours before the reading."]
        else:
            parts = [f"Nothing found: no hazard that takes power lines down began in or near this state in the "
                     f"{HAZARD_BEFORE_H} hours before the reading."]
        parts += _ended_note(sig)
        if minor:
            parts.append(f"{_n(len(minor), 'minor hazard')} in or near the state in the window (not counted).")
        if standing:
            parts.append(f"{_n(len(standing), 'hazard')} already running before that (standing, not counted).")
        if layer.get("unplaced"):
            parts.append(f"{_n(len(layer['unplaced']), 'hazard')} for the whole US with no place in a state (not "
                         "counted).")
        return " ".join(parts)
    if name == "elements":
        sig, minor, standing = layer["significant"], layer["minor"], layer["standing"]
        if sig:
            parts = [f"{_n(len(sig), 'significant hazard')} began in the {HAZARD_BEFORE_H} hours before the outage."]
        else:
            parts = [f"Nothing found: no significant hazard began in the {HAZARD_BEFORE_H} hours before the outage."]
        parts += _ended_note(sig)
        if minor:
            parts.append(f"{_n(len(minor), 'minor hazard')} in the window (not counted).")
        if standing:
            parts.append(f"{_n(len(standing), 'hazard')} already running before that (standing, not counted).")
        return " ".join(parts)
    if name == "cordon":
        if layer["recent"]:
            parts = [f"{_n(len(layer['recent']), 'notice')} issued or raised in the {NOTICE_RECENT_H} hours before "
                     "the outage."]
        else:
            parts = [f"Nothing found: no travel or embassy notice was issued or raised in the {NOTICE_RECENT_H} "
                     "hours before the outage."]
        if layer["recent_not_raised"]:
            parts.append(f"{_n(len(layer['recent_not_raised']), 'notice')} lowered, re-issued at the same level "
                         "or edited without a change of level or advice (not counted).")
        if layer["standing"]:
            parts.append(f"{_n(len(layer['standing']), 'older notice')} standing (not counted).")
        if layer["after"]:
            parts.append(f"{_n(len(layer['after']), 'notice')} dated after the outage.")
        return " ".join(parts)
    own = layer["own_rate"]
    if layer["raised"] and layer["unusual"]:
        text = ("Press attention is raised, with no archive baseline to check it against." if own is None else
                f"Press attention is raised, which is unusual here: raised on {own:.0%} of archived days.")
    elif layer["raised"]:
        text = f"Press attention is raised, but it is on {own:.0%} of archived days here, so it is not counted."
    else:
        text = "Nothing found: press attention is not raised."
    if not layer["data_reaches_outage"]:
        text += " Its newest data ends before the outage."
    return text


PUBLIC_NAME = "triage.json"
LAYER_NAMES = {"elements": "Natural hazards (Elements)", "cordon": "Travel and embassy notices (Cordon)",
               "watchfloor": "Press attention (Watchfloor)"}
WHAT = ("For each internet outage Blackout reports, what three other layers show for that country around that "
        "hour: natural hazards (Elements), travel and embassy notices (Cordon: the State Department's advisories "
        "and embassy notices, and UK, Canadian and New Zealand travel advice when its level or advice changes) "
        "and press "
        "attention (Watchfloor). "
        "For each US state where ODIN's reporting utilities show more than 50,000 meters without power, the same "
        "layers for that state: hazards in or near it, while Cordon and Watchfloor do not cover US states.")
NOT_A_WARNING = ("A reading sorts public reports. It is not a warning, not a forecast and not a finding of cause, "
                 "and it carries no probability. \"Nothing found\" and \"No current data\" are different answers.")
DRAFT_NOTE = ("A drafted question that would let this reading be scored later. It is not a registered Verdict "
              "forecast and no likelihood is attached to it.")


def question_draft(v):
    """The drafted Verdict question as the public sees it: the question, its window and rule, and that it is a
    draft. The empty probability and approval fields stay in out/: published, they would read as a forecast."""
    rise = v.get("rise_form")
    out = {"status": "draft, not registered", "note": DRAFT_NOTE, "question": v["question"],
           "window_ends": v["window_ends"], "rule_draft": v["rule_draft"], "caution": v["caution"],
           "rise_form": None if not rise else {"question": rise["question"], "rule_draft": rise["rule_draft"],
                                               "notes": rise["notes"]}}
    if v.get("reference"):            # a state power outage: the EAGLE-I reference and the question's own notes
        out["reference"] = v["reference"]
        out["notes"] = v.get("notes") or []
    return out


RECORD_KEYS = ("join", "country", "outage_kind", "us_state", "us_state_name", "outage", "layers", "no_current_data",
               "ooni_now", "reading", "reading_text", "caveats", "base_rate", "eaglei")


def public(doc):
    """The document the page and the MCP server show: every record with one line per layer on what it showed, the
    base rate, the caveats and the drafted Verdict question, marked a draft."""
    records = []
    for r in doc["records"]:
        rec = {k: r[k] for k in RECORD_KEYS}
        rec["layers"] = {name: {"name": LAYER_NAMES[name], "showed": showed(name, layer), **layer}
                         for name, layer in r["layers"].items()}
        rec["question_draft"] = question_draft(r["verdict_draft"])
        records.append(rec)
    return {"join": doc["join"], "what": WHAT, "not_a_warning": NOT_A_WARNING, "readings": READINGS,
            "power_readings": POWER_READINGS, "snapshot_generated_at": doc["snapshot_generated_at"], "layer_states": doc["layer_states"],
            "windows": doc["windows"], "baseline_given": doc["baseline_given"], "base_rates": doc["base_rates"],
            "ended_items": doc.get("ended_items"), "records": records}


def publish_folder(path):
    """Where the public document may go: site/data and nowhere else under site/. The gate still has the last word."""
    out = Path(path).resolve()
    site = (ROOT / "site").resolve()
    if (out == site or site in out.parents) and out != site / "data":
        raise SystemExit("triage.py publishes only to site/data")
    return out


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
    ap.add_argument("--ended", default=None,
                    help="Elements' ended items (data/ended.geojson): URL or file; without it the readings say the "
                         "ended hazards are missing")
    ap.add_argument("--publish", default=None, metavar="FOLDER",
                    help=f"also write the public document, {PUBLIC_NAME}, here (the build passes site/data)")
    a = ap.parse_args(argv)
    out = safe_out(a.out)
    rates_file = Path(a.rates) if a.rates else out / "base-rates.json"
    rates = json.loads(rates_file.read_text(encoding="utf-8")) if rates_file.exists() else None
    ended = None
    if a.ended and (str(a.ended).startswith("http") or Path(a.ended).exists()):
        try:
            ended = load(a.ended)
        except Exception as e:  # noqa: BLE001 - an unreadable list is a missing list, and the records say so
            print(f"ended items unreadable: {type(e).__name__}")
    doc = attach_rates(triage(load(a.snapshot), (rates or {}).get("history"), ended), rates)
    stamp = (doc["snapshot_generated_at"] or "unknown").replace(":", "")
    if a.publish:
        gate.write(JOIN, public(doc), publish_folder(a.publish), (PUBLIC_NAME,))
    verdict = gate.write(JOIN, doc, out, (f"triage-{stamp}.json", "latest.json"))
    print(f"snapshot {doc['snapshot_generated_at']}  layers {doc['layer_states']}  gate: {verdict['review_class']}")
    e = doc["ended_items"]
    print(f"ended items: {e['state']}, {e['added']} added (generated {e['generated_at']})")
    for r in doc["records"]:
        L = r["layers"]
        print(f"{r['us_state'] or r['country']}  {r['reading']:<11} {r['outage']['title']}")
        if r["base_rate"]:
            print(f"    base rate: {r['base_rate']['text']}")
        if r["outage_kind"] == "power":
            E = L["elements"]
            print(f"    elements in or near {r['us_state']}: {len(E['significant'])} significant, {len(E['minor'])} "
                  f"minor, {len(E['standing'])} standing, {len(E['unplaced'])} unplaced | cordon and watchfloor: "
                  "not covered")
            if r["eaglei"]:
                print(f"    EAGLE-I: {r['eaglei']['text']}")
            continue
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
    if a.publish:
        print(f"published {Path(a.publish) / PUBLIC_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
