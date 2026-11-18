"""Outage triage: what the other layers show under each Blackout outage.

A named join (scenario 2 of D:/Projects/_ideas/convergence-scenarios.md), not a count of programs. For every
Blackout outage in a COP snapshot it records what Elements, Cordon and Watchfloor show for that country around
that hour, including "nothing found" and "no current data", which are different answers. It states a reading of
the pattern and drafts the Verdict question. It never states a probability: that is the analyst's.

US state power outages (Blackout's BO-IND-14: a state where ODIN's reporting utilities show more than 50,000 meters
out) are read against Elements for that state, not the whole country (rules approved by Tom, 13 Nov 2026): NWS
alerts by their own county codes, point hazards within STATE_NEAR_KM of the state, and warnings for hazards that
take power lines down counted at Elements' Severe level. Cordon and Watchfloor carry nothing at US-state level, so
they answer "Not covered" and such an outage reads natural, open or incomplete. Where Blackout's
item lists the counties with meters out (us_counties, Blackout 2ea3a4d; Tom, 14 Nov 2026, "build the county-level
match"), Elements is read for those counties instead of the whole state: NWS alerts by shared county codes, point
hazards within STATE_NEAR_KM of an outage county's internal point, areas over one; a hazard elsewhere in the state
is listed and not counted. An item without the list keeps the state rule. Its drafted
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
import triage_replay as replay_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"
# County FIPS -> [state, name, lat, lon]: the Census 2025 Gazetteer's internal points, the copy Blackout places ODIN's
# counties with (blackout/us_counties.json; Elements' catalog has the same points). Public domain.
US_COUNTIES = ROOT / "config" / "us_counties.json"
PUBLISHED = "https://cop.theintelligence.company/data/today.geojson"
UA = "tic-cop/0.1 (+https://cop.theintelligence.company)"

# The gate decides the review class from these inputs. "published" was turned on in step 5 (13 Nov 2026) on the
# convergence branch; it reaches the site only when Tom merges that branch to main.
JOIN = {"id": "outage-triage", "version": "0.9", "published": True,
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
HAZARD_BEFORE_H = 72     # a hazard that BEGAN this long before the outage counts; one already running is standing
                         # context (a volcano alert in Hawaii says nothing about an outage in the US)
HAZARD_AFTER_H = 6       # reports of a hazard trail the event a little
HAZARD_NEAR_KM = 300     # a hazard with no country (an offshore earthquake) counts within this of the country
# Episodic hazards (D8, Tom, 15 Nov 2026; cop-joins.md M2): a cyclone or flood counts while it is active, from its
# GDACS "from" to its "to" plus this lag, not only when it began in the 72 hours. GDACS dates a cyclone from its
# formation: MURASU-26 (red, India) formed 1 Nov, last advisory 3 Nov 06:00, and read "standing" on 4 and 5 Nov.
# Standing kinds (drought, volcano status) keep the "began in the window" rule.
EPISODIC_GDACS = {"TC": "tropical cyclone", "FL": "flood"}
EPISODIC_LAG_H = 48      # an episodic hazard still counts this long after its last advisory (damage outlasts it)
NOTICE_RECENT_H = 48     # a notice dated this long before the outage counts; older ones are standing context
# Embassy alerts are sorted by the type their own titles give (D8; cop-joins.md M1). Security and demonstration
# alerts count. Weather, natural-disaster and health alerts, routine messages, and security alerts about a holiday
# or a scheduled event are listed and not counted.
ALERT_COUNTED = ("security", "demonstration")
ALERT_TYPES = (("weather alert", "weather"), ("natural disaster alert", "natural disaster"),
               ("health alert", "health"), ("demonstration alert", "demonstration"),
               ("security alert", "security"), ("emergency alert", "security"))
EVENT_WORDS = ("holiday", "independence day", "national day", "summit", "anniversary", "festival", "celebration",
               "christmas", "new year", "marathon", "parade", "world cup", "olympic", "concert", "conference")
# Unusual attention (D8; cop-joins.md M3): Watchfloor's own z-score against the 28 complete days before the day it
# measures (its baseline wf-28d), which never includes that day. Watchfloor's 90-day "elevated" items are standing
# attention: listed, not counted.
ATTENTION_BASELINE = "wf-28d"
OONI_WINDOW_H = 72       # the Verdict question's window, in whole UTC hours from the outage's start hour
# The OONI question settles on OONI's own hourly counts for the country (since 18 Nov 2026; Tom chose option (b)),
# not on Blackout's BO-IND-3 reading, which exists only for the six countries Blackout reads OONI for, so a question
# settled on it could only resolve No anywhere else. The lines are Blackout's own outage-window lines
# (blackout/ooni.py outage_window), so both programs mean the same thing by rising blocking; the baseline is the
# 120 hours before the window, since the same hours of the 5 days before would overlap a 72-hour window.
OONI_MIN_MEASUREMENTS = 50
OONI_MIN_SHARE = 0.15
OONI_RISE = 2               # the window's share at least this many times the baseline's
OONI_BASE_H = 120
OONI_LATE_H = 24            # OONI takes late uploads: read the counts no earlier than this after the window
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

# The reading once called "deliberate" is "tension context" (D8, Tom, 15 Nov 2026; cop-joins.md M1): it names what
# the layers showed, not a cause. "Deliberate" stays Blackout's own word, for its likely-shutdown judgment (BO-H-1A).
TENSION = "tension context"
READINGS = {
    "incomplete": "A layer has no current data, so this outage cannot be triaged yet. Silence is not quiet.",
    "unconfirmed": "Blackout calls this a single-source dip, not a confirmed outage. Context is listed, no reading.",
    "natural": "A significant natural hazard was active in or near this country around the outage's hour. The base "
               "rate below says how often that is so anyway.",
    TENSION: "No active hazard, and a security or demonstration notice was issued or raised in the two days before "
             "the outage, or press attention was unusual for this country against its own last 28 days. That is the "
             "context the outage sits in, not its cause; the base rate below says how often it is there anyway.",
    # Reworded on Tom's yes, 18 Nov 2026: the STOP replay found open less often on shutdown days (62%) than on
    # ordinary days (84%), so "nothing that tells one kind of outage from another" said more than the layers know.
    "open": "No active hazard, no fresh notice and no unusual attention. These layers show none of the context this "
            "triage looks for. The replay rates below say how often that is so on shutdown days and on ordinary "
            "days.",
}
# A US state power outage: Cordon and Watchfloor don't cover US states, so these three are the only readings.
POWER_READINGS = {
    "incomplete": "Elements has no current data, so this outage cannot be triaged yet. Silence is not quiet.",
    "natural": "A hazard that takes power lines down began in or near this state in the 72 hours before the reading. "
               "The base rate below says how often that is so anyway.",
    "open": "No hazard that takes power lines down began in or near this state in the 72 hours before the reading. "
            "Nothing here separates a storm already past, failed equipment, a utility's planned shutoff or an attack.",
}
POWER_CAVEATS = (
    "Cordon and Watchfloor carry nothing at US-state level, so no layer here can show tension context: a state "
    "power outage reads natural, open or incomplete.",
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
              "US-state level.",
    "watchfloor": "Not covered: Watchfloor follows press attention in conflict zones, nothing at US-state level.",
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
_NAMES = None


def _rings(cc):
    global _RINGS
    if _RINGS is None:
        data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
        _RINGS = {c["cc"]: c["rings"] for c in data["countries"]}
    return _RINGS.get(cc) or []


def country_name(cc):
    """The kit's name for an ISO alpha-2 code, or the code itself."""
    global _NAMES
    if _NAMES is None:
        data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
        _NAMES = {c["cc"]: c.get("name") or c["cc"] for c in data["countries"]}
    return _NAMES.get(cc) or cc


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
    episodic = episodic_kind(p)
    if episodic:
        extra.update(episodic=episodic, active_until=iso(end))
    row = _row(p, hours_before_outage=round((at - start).total_seconds() / 3600, 1), **extra)
    if kind in BACKGROUND:
        return "background", row
    if p.get("tier") in ("anomaly", "warning") or warning:
        if episodic:   # active at the outage: from its start to its last advisory plus the lag
            active = start <= hi and end + timedelta(hours=EPISODIC_LAG_H) >= at
            return ("significant" if active else "standing"), row
        return ("significant" if start >= lo and not standing_only else "standing"), row
    return "minor", row


def episodic_kind(p):
    """"tropical cyclone" or "flood" for a GDACS item of an episodic kind, else None."""
    if _kind(p.get("id")) != "gdacs":
        return None
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    code = str(m.get("hazard_code") or str(p.get("id")).split(":")[-1].split("-")[0]).upper()
    return EPISODIC_GDACS.get(code)


def hazards(features, cc, at, volcano_levels=None):
    """Elements items under the outage: in or near the country, overlapping the hazard window. A significant hazard
    counts only if it began inside the window, or, for a cyclone or flood, if it was active at the outage (its last
    advisory less than EPISODIC_LAG_H before it); one that began earlier and is still running is "standing"."""
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


def outage_counties(p, st):
    """The FIPS codes of the counties with meters out that Blackout lists on a BO-IND-14 item (us_counties, from
    Blackout 2ea3a4d, 14 Nov 2026), kept only where COP's county table puts them in the item's state; None when the
    item has no usable list, so the state rule applies."""
    table = county_table()
    rows = p.get("us_counties") if isinstance(p.get("us_counties"), list) else []
    codes = sorted({r.get("fips") for r in rows if isinstance(r, dict) and r.get("fips") in table
                    and table[r["fips"]][0] == st})
    return codes or None


def km_to_counties(geometry, codes):
    """Distance in km from a Point, or the nearest point of a MultiPoint, to the nearest internal point of the given
    counties, or None."""
    t = geometry.get("type") if isinstance(geometry, dict) else None
    pts = [geometry["coordinates"]] if t == "Point" else geometry["coordinates"] if t == "MultiPoint" else []
    table = county_table()
    best = None
    for x, y in (pt[:2] for pt in pts):
        for c in codes:
            d = _km(y, x, table[c][2], table[c][3])
            if best is None or d < best:
                best = d
    return best


def _at_outage(f, place, codes):
    """Narrow a state placement to the outage's own counties: the place when the hazard is in or near them, else
    {"placed": "elsewhere", "in_state": <how it sits in the state>}. Background kinds keep their own test (over the
    outage's point) and a whole-US item stays "country"."""
    if place["placed"] in ("country", "over_outage"):
        return place
    p, g = f["properties"], f.get("geometry")
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    want = set(codes)
    if place["placed"] == "county":
        hit = sorted(want & set(m.get("counties") or []))
        return {"placed": "county", "outage_counties": hit} if hit else {"placed": "elsewhere", "in_state": "county"}
    if place["placed"] in ("near", "in"):
        km = km_to_counties(g, codes)
        if km is not None and km <= STATE_NEAR_KM:
            near = [c for c in codes if (km_to_counties(g, [c]) or STATE_NEAR_KM + 1) <= STATE_NEAR_KM]
            return {"placed": "near_outage", "km_from_outage": round(km), "outage_counties": near}
        return {"placed": "elsewhere", "in_state": place["placed"],
                "km_from_outage": None if km is None else round(km)}
    if place["placed"] == "area":
        table = county_table()
        inside = [c for c in codes if _in_polygon(table[c][3], table[c][2], g)]
        return {"placed": "area", "outage_counties": inside} if inside else {"placed": "elsewhere", "in_state": "area"}
    return place


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


def state_hazards(features, st, at, point=None, volcano_levels=None, counties=None):
    """Elements items under a US state power outage: in or near the state, overlapping the hazard window. point:
    the outage's own [lon, lat] (Blackout weights it by meters out), or None. unplaced: significant hazards for the
    whole US that name no county or point, listed and never counted (minor ones are left out). counties: the outage's
    own county FIPS codes (outage_counties), or None for the state rule; with them a hazard must be in or near those
    counties, and a significant or standing one elsewhere in the state goes to "elsewhere", listed, never counted."""
    lo, hi = at - timedelta(hours=HAZARD_BEFORE_H), at + timedelta(hours=HAZARD_AFTER_H)
    out = {"significant": [], "standing": [], "minor": [], "background": [], "unplaced": []}
    if counties:
        out["elsewhere"] = []
    for f in features:
        if f["properties"].get("cop_program") != "elements":
            continue
        bucket, row = _classify(f, at, lo, hi, volcano_levels, power=True)
        if not bucket:
            continue
        place = _place(f, st, point)
        if not place:
            continue
        if counties:
            place = _at_outage(f, place, counties)
        row.update(place)
        if place["placed"] == "elsewhere":
            if bucket in ("significant", "standing"):
                out["elsewhere"].append({**row, "bucket": bucket})
            continue
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


def alert_type(title):
    """An embassy alert's type from its own title ("US embassy (MX): Weather Alert: ..."): security, demonstration,
    weather, natural disaster, health, event (a security alert about a holiday or a scheduled event) or routine
    (messages for US citizens, consular visits, anything that names no alert type)."""
    t = str(title or "")
    t = t.split("): ", 1)[1] if t.startswith("US embassy (") and "): " in t else t
    low = t.strip().lower()
    for prefix, kind in ALERT_TYPES:
        if low.startswith(prefix):
            if kind == "security" and any(w in low for w in EVENT_WORDS):
                return "event"
            return kind
    return "routine"


def alert_first_dated(item_id, dated, at, alert_dates):
    """The earliest date COP has seen on this embassy alert in snapshots taken at or before `at`, or `dated`.
    Embassy alerts are re-dated when edited (cordon:alert:CO:937adfcbfd2a: 6 Nov, then 12 Nov, then 13 Nov), and
    an edit is not a fresh notice. alert_dates: {id: [[snapshot generated_at, data_as_of], ...]} from
    triage_rates.py, or None."""
    best = dated
    for seen, d in (alert_dates or {}).get(item_id, []):
        s, d = utc(seen), utc(d)
        if s and d and s <= at and (best is None or d < best):
            best = d
    return best


def day_span(p):
    """(start, end) of the UTC day a whole-day item measures, from its own fields. Watchfloor's and Noisefloor's items
    say `data_as_of_precision` "day" with data_as_of at that day's midnight (from 16 Nov 2026, kit 0.5.6), so the day
    runs from data_as_of for 24 hours. Snapshots archived before then carry the end of the day (T23:59:59Z) and no
    precision, so the day is the 24 hours before data_as_of. (None, None) with no time."""
    dated = utc(p.get("data_as_of"))
    if dated is None:
        return None, None
    if p.get("data_as_of_precision") == "day":
        return dated, dated + timedelta(days=1)
    return dated - timedelta(days=1), dated


def _day_dated(p, dated):
    """True when a notice carries a date and no time (SafeTravel dates its advice by the day)."""
    return p.get("data_as_of_precision") == "day" or (dated and (dated.hour, dated.minute, dated.second) == (0, 0, 0))


def notices(features, cc, at, levels=None, alert_dates=None):
    """Cordon's travel and embassy notices for the country, against the outage's time.

    recent: dated in the NOTICE_RECENT_H before the outage and not a lowering or a re-issue at the same level; these
    count toward a reading. recent_not_raised: dated in the window but lowered, unchanged, or an embassy alert
    re-dated from an earlier date. recent_not_counted: embassy alerts in the window of a type that is not counted
    (weather, natural disaster, health, event, routine). standing: older. after: dated after the outage. An
    advisory whose earlier level is not in the archive counts, marked "unknown". FCDO, Canada and SafeTravel advice
    that Cordon did not mark as changed is an edit at the same level: direction "unchanged", not counted. A notice
    dated by the day only counts from the start of the day the window opens on."""
    lo = at - timedelta(hours=NOTICE_RECENT_H)
    lo_day = lo.replace(hour=0, minute=0, second=0, microsecond=0)
    recent, not_raised, not_counted, standing, after = [], [], [], [], []
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
        first = dated
        if kind == "alert":
            row["alert_type"] = alert_type(p.get("title"))
            first = alert_first_dated(p.get("id"), dated, at, alert_dates) if dated else None
            if first and dated and first < dated:
                row["first_dated"] = iso(first)
        in_window = bool(dated) and (dated >= lo or (_day_dated(p, dated) and dated >= lo_day))
        if not dated or (not in_window and dated < at):
            standing.append(row)
        elif dated > at:
            after.append(row)
        elif row.get("direction") in ("lowered", "unchanged"):
            not_raised.append(row)
        elif kind == "alert" and first < lo:
            row["direction"] = "re-dated"      # first seen before the window: an edit, not a fresh notice
            not_raised.append(row)
        elif kind == "alert" and row["alert_type"] not in ALERT_COUNTED:
            not_counted.append(row)
        else:
            recent.append(row)
    return {"recent": recent, "recent_not_raised": not_raised, "recent_not_counted": not_counted,
            "standing": standing, "after": after}


def _wf_z(p):
    """The z-score a Watchfloor item carries against its own baseline (country items: measures.score; zone and topic
    items: measures.attention, else the violence score), or None."""
    m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
    for key in ("score", "attention"):
        if isinstance(m.get(key), dict) and m[key].get("z") is not None:
            return m[key]["z"]
    v = m.get("violence") if isinstance(m.get("violence"), dict) else {}
    s = v.get("named_score") if isinstance(v.get("named_score"), dict) else v
    return s.get("z") if isinstance(s, dict) else None


def attention(features, cc, at):
    """Watchfloor items naming the country, whether that data reaches the outage's hour, and whether attention is
    unusual for this country: an item Watchfloor raised on its own 28-day z-score (baseline wf-28d: the 28 complete
    days before the day it measures, so the day never sets its own baseline). Raised on the 90-day median only
    ("elevated") is standing attention, listed and not counted. A Watchfloor day that begins after the outage is
    listed as after it and not counted."""
    unusual, elevated, routine, after, newest = [], [], [], [], None
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "watchfloor":
            continue
        dated = utc(p.get("data_as_of"))
        day_start, day_end = day_span(p)
        if day_end and (newest is None or day_end > newest):
            newest = day_end
        if cc not in (p.get("countries") or []):
            continue
        base = (p.get("baseline_ref") or {}).get("id") if isinstance(p.get("baseline_ref"), dict) else None
        row = _row(p, kind=_kind_wf(p.get("id")), z=_wf_z(p), baseline=base)
        if day_start and day_start >= at:                 # the day it measures begins after the outage
            after.append(row)
        elif p.get("tier") not in ("anomaly", "warning"):
            routine.append(row)
        elif base == ATTENTION_BASELINE:
            unusual.append(row)
        else:
            elevated.append(row)
    return {"raised": unusual + elevated, "unusual_items": unusual, "elevated": elevated, "routine": routine,
            "after": after, "baseline": ATTENTION_BASELINE,
            "unusual": bool(unusual),
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
        "caution": None,
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


def meters_under(p, rows):
    """{"meters", "of", "share", "largest"}: how many of the outage's meters out sit in counties a counted hazard
    covers (rows' outage_counties), and the largest county with none. A reading turns natural on any one hazard, so
    this says how much of the outage the hazards explain."""
    out = [r for r in (p.get("us_counties") or []) if isinstance(r, dict) and isinstance(r.get("meters"), int)]
    covered = {c for r in rows for c in (r.get("outage_counties") or [])}
    total = sum(r["meters"] for r in out)
    under = sum(r["meters"] for r in out if r.get("fips") in covered)
    bare = max((r for r in out if r.get("fips") not in covered), key=lambda r: r["meters"], default=None)
    return {"meters": under, "of": total, "share": round(under / total, 3) if total else None,
            "largest_without": {k: bare[k] for k in ("fips", "name", "meters")} if bare else None}


def power_record(f, features, states, history=None, ended_info=None):
    """One record for a Blackout BO-IND-14 item: a US state over the line, read against Elements for that state."""
    p = f["properties"]
    st, at = us_state_of(p), utc(p.get("data_as_of"))
    if not st or not at:
        return None
    g = f.get("geometry") if isinstance(f.get("geometry"), dict) else {}
    point = g["coordinates"][:2] if g.get("type") == "Point" else None
    counties = outage_counties(p, st)
    haz = state_hazards(features, st, at, point, (history or {}).get("volcano_levels"), counties)
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
    if counties:
        caveats.append(f"Hazards are matched to the {_n(len(counties), 'county')} where ODIN's utilities showed "
                       f"meters out: NWS alerts by shared county codes, points within {STATE_NEAR_KM} km of one of "
                       "those counties' internal points, areas over one. A county is matched whole, so a hazard in "
                       "one corner of a large western county counts for an outage in another.")
        under = meters_under(p, haz["significant"])
        if haz["significant"] and under["share"] is not None and under["share"] < 0.5:
            b = under["largest_without"]
            caveats.append(f"The hazards that make this reading natural cover counties holding {under['share']:.0%} "
                           f"of the meters out ({under['meters']:,} of {under['of']:,}); "
                           + (f"{b['name']}, with {b['meters']:,}, has none." if b else ""))
        if haz["elsewhere"]:
            k = len(haz["elsewhere"])
            new = sum(1 for h in haz["elsewhere"] if h["bucket"] == "significant")
            caveats.append(f"{_n(k, 'hazard')} that {'takes' if k == 1 else 'take'} power lines down "
                           f"{'is' if k == 1 else 'are'} elsewhere in {name}, away from the outage's counties "
                           f"({new} began in the window): listed, not counted.")
        caveats.append("The base rate below is measured at state level (any hazard in or near the state), so for a "
                       "reading matched to the outage's counties it overstates how often an outage would read natural.")
    else:
        caveats.append("This item lists no counties (Blackout before 14 Nov 2026), so hazards are matched to the "
                       "whole state.")
    return {
        "join": JOIN["id"], "country": "US", "outage_kind": "power", "us_state": st, "us_state_name": name,
        "outage": _row(p, confirmed=None, indicator_id=p.get("indicator_id"), summary=p.get("summary")),
        "layers": {
            "elements": {"state": states["elements"], "scope": "counties" if counties else "state",
                         **({"outage_counties": counties,
                             "meters_under_hazards": meters_under(p, haz["significant"])} if counties else {}),
                         **haz,
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


# The Blackout kinds triage reads (M5, Tom, 15 Nov 2026; cop-joins.md M5). IODA's country-level drops, Cloudflare
# Radar's verified traffic anomalies (one network or a country) and Radar's Outage Center entries that name a
# country. A Radar entry that names no place (a platform outage) cannot be placed, so it is listed, not triaged.
INPUT_KINDS = {"outage": "IODA", "radar-anomaly": "Cloudflare Radar (verified anomaly)",
               "radar-outage": "Cloudflare Radar Outage Center"}
_ISO = r"(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?Z)"
_SPAN_RE = re.compile(rf"(?:since {_ISO}, ongoing|from {_ISO} to {_ISO})")
_CAUSE_RE = re.compile(r"Cause as Cloudflare states it: ([^.]+)\.")
_NET_RE = re.compile(r", AS(\d+)(?: \(([^)]*)\))?:")
OLD_START_D = 7          # Elements keeps ended hazards for 7 days: an outage that began earlier is read with a gap
CAUSE_WINDOW_D = 7       # the cause question's window (cop-joins.md M6)
CAUSE_RESOLVERS = ("Cloudflare Radar's Outage Center lists it with the cause \"government directed\"; Access Now "
                   "names it a shutdown (a #KeepItOn statement or its STOP data); or two of Reuters, the Associated "
                   "Press and AFP report that authorities ordered it")
CAUSE_SOURCE = "Cloudflare Radar Outage Center; Access Now (#KeepItOn, STOP); Reuters, Associated Press, AFP"
OONI_SOURCE = "OONI's aggregation API (api.ooni.io; OONI data, CC BY-NC-SA 4.0), read directly"
RESOLVE_GRACE_D = 7      # resolve_by: this long after the check time


def radar_span(p):
    """(start, end) of a Cloudflare Radar item from Blackout's summary ("since <t>, ongoing" or "from <t> to <t>"):
    its data_as_of is the run time while it is ongoing (cop-joins.md D5). (None, None) when the summary has neither."""
    m = _SPAN_RE.search(str(p.get("summary") or ""))
    if not m:
        return None, None
    return (utc(m.group(1)), None) if m.group(1) else (utc(m.group(2)), utc(m.group(3)))


def outage_input(p):
    """What triage reads from one Blackout outage item, or None if it is not one triage reads: {source, at (when it
    began), ended, confirmed, network, stated_cause, timed_from}."""
    kind = _kind(p.get("id"))
    if kind not in INPUT_KINDS:
        return None
    if kind == "outage":
        return {"source": INPUT_KINDS[kind], "at": utc(p.get("data_as_of")), "ended": None,
                "confirmed": p.get("indicator_id") == CONFIRMED_OUTAGE, "network": None, "stated_cause": None,
                "timed_from": "start"}
    start, end = radar_span(p)
    net = _NET_RE.search(str(p.get("title") or ""))
    cause = _CAUSE_RE.search(str(p.get("summary") or ""))
    return {"source": INPUT_KINDS[kind], "at": start or utc(p.get("data_as_of")), "ended": iso(end) if end else None,
            "confirmed": True,       # Cloudflare's analysts verified the anomaly or listed the outage
            "network": {"asn": int(net.group(1)), "name": net.group(2)} if net else None,
            "stated_cause": cause.group(1).strip() if cause and kind == "radar-outage" else None,
            "timed_from": "start" if start else "run"}


def _check_phrase(t):
    """Verdict's wording for a rule's earliest check: "check after HH:MM UTC on D Mon YYYY"."""
    return f"check after {t.strftime('%H:%M')} UTC on {t.day} {t.strftime('%b %Y')}"


def _qid(cc, inp, at, what):
    net = f"-as{inp['network']['asn']}" if inp.get("network") else ""
    return f"cop-{cc.lower()}{net}-outage-{at.strftime('%Y%m%dt%H%M')}-{what}"


def cause_question(cc, inp, at):
    """The second resolver (D6; cop-joins.md M6): whether an outside source attributes the outage to a government
    order within CAUSE_WINDOW_D days. It names cause, which the OONI question does not."""
    check = at.replace(second=0, microsecond=0) + timedelta(days=CAUSE_WINDOW_D)
    where = cc + (f" (AS{inp['network']['asn']})" if inp.get("network") else "")
    caution = None
    sc = (inp.get("stated_cause") or "").lower()
    if "government" in sc:
        caution = ("Cloudflare already states a government-directed cause, so this question is settled before it "
                   "is asked. Skip it.")
    elif sc:
        caution = (f"Cloudflare already states another cause ({inp['stated_cause']}); a Yes needs Cloudflare to "
                   "change it or another resolver to name a government order.")
    return {
        "registered": False, "probability": None, "approved_by": None, "what": "cause",
        "id_slug": _qid(cc, inp, at, "cause"),
        "question": f"Will an outside source attribute the outage in {where} that began {iso(at)} to a government "
                    f"order within {CAUSE_WINDOW_D} days?",
        "window_ends": iso(check),
        "rule_draft": f"Yes if, on a {_check_phrase(check)}, any of these has attributed this outage to a "
                      f"government order or a government-directed shutdown: {CAUSE_RESOLVERS}. No otherwise.",
        "resolution": {"source": CAUSE_SOURCE, "known_at": iso(check),
                       "resolve_by": iso(check + timedelta(days=RESOLVE_GRACE_D)),
                       "void_if": "an attribution of this outage to a government order was published before this "
                                  "forecast's published_at"},
        "caution": caution,
    }


def ooni_rule(cc, start, until, check):
    """The OONI rule in the words Verdict's resolver reads (verdict/tools/auto_resolve.py, OONI_RE)."""
    return (f"Yes if OONI's aggregation API (api.ooni.io, probe_cc={cc}, all tests, hourly) counts at least "
            f"{OONI_MIN_MEASUREMENTS} measurements from {iso(start)} to {iso(until)} UTC, at least "
            f"{OONI_MIN_SHARE:.0%} of them confirmed or anomalous, and that share at least {OONI_RISE} times the "
            f"share over the {OONI_BASE_H} hours before {iso(start)} UTC (a baseline with no measurements counts "
            f"as zero); read on a {_check_phrase(check)}. No otherwise.")


def ooni_question(cc, inp, at):
    """The OONI question (scenario 2's scoring line): whether OONI's blocking share in the outage's country rises
    in the 72 whole hours from the outage's start hour against the 120 hours before. Settled on OONI's own counts,
    so it can resolve either way in every country; the share is relative, so a country already over 15% (Iran)
    needs a real rise, not a standing level."""
    start = at.replace(minute=0, second=0, microsecond=0)
    until = start + timedelta(hours=OONI_WINDOW_H)
    check = until + timedelta(hours=OONI_LATE_H)
    return {
        "registered": False, "probability": None, "approved_by": None, "what": "ooni",
        "id_slug": _qid(cc, inp, at, "ooni"),
        "question": f"Will OONI measure blocking in {cc} rising in the {OONI_WINDOW_H} hours from the outage of "
                    f"{iso(at)}?",
        "window_ends": iso(until),
        "rule_draft": ooni_rule(cc, start, until, check),
        "resolution": {"source": OONI_SOURCE, "known_at": iso(check),
                       "resolve_by": iso(check + timedelta(days=RESOLVE_GRACE_D)), "void_if": None},
        "caution": ("OONI measures blocking on networks that are up. In a full shutdown the probes cannot report, "
                    "so the count falls and this question can resolve No even when the outage is a shutdown.")
                   if inp.get("network") is None else
                   ("The outage is on one network, but OONI is counted for the whole country: a probe on the "
                    "affected network cannot report while it is down."),
    }


def triage(snapshot, history=None, ended=None):
    """One record per Blackout outage in the snapshot. history: triage_rates.py's "history" block (each advisory's
    and volcano's earlier levels, the dates COP has seen on each embassy alert), or None when there is no archive.
    ended: Elements' list of items that ended in the last 7 days (data/ended.geojson), or None when it could not be
    read; the layer states come from the live snapshot alone."""
    levels = (history or {}).get("advisory_levels")
    alert_dates = (history or {}).get("alert_dates")
    volcanoes = (history or {}).get("volcano_levels")
    features = [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]
    states = {pid: layer_state(snapshot, features, pid) for pid in LAYERS + ("blackout",)}
    snap_at = utc(snapshot.get("generated_at"))
    features, ended_info = with_ended(features, ended, snap_at)
    ooni = {}
    for f in features:
        p = f["properties"]
        if p.get("cop_program") == "blackout" and p.get("indicator_id") == OONI_BLOCKING and not p.get("cop_stale"):
            for cc in p.get("countries") or []:
                ooni[cc] = _row(p)
    records, unplaced = [], []
    for f in features:
        p = f["properties"]
        if p.get("cop_program") != "blackout" or p.get("cop_stale"):
            continue
        if p.get("indicator_id") == POWER_OUTAGE:
            rec = power_record(f, features, states, history, ended_info)
            if rec:
                records.append(rec)
            continue
        inp = outage_input(p)
        if not inp:
            continue
        if not p.get("countries"):
            unplaced.append(_row(p, source=inp["source"]))
            continue
        at = inp["at"]
        for cc in p.get("countries") or []:
            if not at:
                continue
            haz = hazards(features, cc, at, volcanoes)
            note = notices(features, cc, at, levels, alert_dates)
            att = attention(features, cc, at)
            dead = [pid for pid in LAYERS if states[pid] in ("stale", "missing")]
            confirmed = inp["confirmed"]
            if dead:
                reading = "incomplete"
            elif not confirmed:
                reading = "unconfirmed"
            elif haz["significant"]:
                reading = "natural"
            elif note["recent"] or att["unusual"]:
                reading = TENSION
            else:
                reading = "open"
            caveats = [ended_caveat(ended_info)] if ended_caveat(ended_info) else []
            if inp["network"]:
                caveats.append(f"Cloudflare saw this on one network (AS{inp['network']['asn']}), and the other "
                               "layers are read for the whole country.")
            if inp["timed_from"] == "run":
                caveats.append("Blackout's item gives no start time, so the outage is timed from Blackout's run.")
            if snap_at and at < snap_at - timedelta(days=OLD_START_D):
                caveats.append(f"The outage began more than {OLD_START_D} days before this snapshot, so a hazard "
                               "that ended before then is not in Elements' lists, and the layers show today's "
                               "items, not those of the day it began.")
            if inp["stated_cause"]:
                caveats.append(f"Cloudflare states a cause for this outage ({inp['stated_cause']}); COP quotes it "
                               "and does not assess it.")
            if not att["data_reaches_outage"] and "watchfloor" not in dead:
                caveats.append(f"Watchfloor's newest data ({att['newest_data']}) ends before this outage, so it "
                               "shows attention before the outage, not during it. Run again after its next day "
                               "is in.")
            if att["after"] and not att["unusual_items"] and not att["elevated"] and not att["routine"]:
                caveats.append("Watchfloor's data in this snapshot begins after the outage, so it is listed and "
                               "not counted.")
            if history is None:
                caveats.append("No archive baseline was given: advisory and volcano alert levels are not compared "
                               "with the ones they replaced, and an embassy alert's earlier dates are not known. "
                               "Run tools/triage_rates.py first.")
            if any(n.get("direction") == "unknown" for n in note["recent"]):
                caveats.append("An advisory or travel advice in the window has no earlier level in the archive, so "
                               "it is counted without knowing whether it was raised.")
            if any(n.get("alert_type") in ("weather", "natural disaster") for n in note["recent_not_counted"]):
                caveats.append("An embassy weather or natural-disaster alert sits in the window. It points to a "
                               "hazard, which Elements is the layer for, so it is listed and not counted.")
            if any(h.get("level_change") == "unknown" for h in haz["significant"]):
                caveats.append(VOLCANO_UNKNOWN)
            if haz["standing"]:
                caveats.append("A hazard that began before the window is still running in this country. It is "
                               "listed as standing and is not counted.")
            others = sorted({g["properties"].get("id") for g in features
                             if g["properties"].get("cop_program") == "blackout" and g is not f
                             and cc in (g["properties"].get("countries") or [])
                             and outage_input(g["properties"])})
            if others:
                caveats.append(f"Blackout reports {_n(len(others), 'other outage item')} for {cc} in this snapshot, "
                               "each read on its own: " + ", ".join(others) + ".")
            records.append({
                "join": JOIN["id"], "country": cc, "outage_kind": "internet", "us_state": None,
                "us_state_name": None,
                "outage": _row(p, confirmed=confirmed, indicator_id=p.get("indicator_id"), summary=p.get("summary"),
                               source=inp["source"], started_at=iso(at), ended_at=inp["ended"],
                               network=inp["network"], stated_cause=inp["stated_cause"]),
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
                "verdict_draft": ooni_question(cc, inp, at),
                "cause_draft": cause_question(cc, inp, at),
                "registerable": bool(confirmed),
            })
    records.sort(key=lambda r: (r["reading"] in ("incomplete", "unconfirmed"),
                                r["outage"].get("started_at") or r["outage"]["data_as_of"] or ""))
    return {"join": JOIN, "snapshot_generated_at": snapshot.get("generated_at"), "layer_states": states,
            "windows": {"hazard_before_hours": HAZARD_BEFORE_H, "hazard_after_hours": HAZARD_AFTER_H,
                        "hazard_near_km": HAZARD_NEAR_KM, "episodic_lag_hours": EPISODIC_LAG_H,
                        "episodic_kinds": sorted(EPISODIC_GDACS.values()), "state_near_km": STATE_NEAR_KM,
                        "power_still_out_hours": POWER_STILL_OUT_H, "notice_recent_hours": NOTICE_RECENT_H,
                        "alert_types_counted": list(ALERT_COUNTED), "attention_baseline": ATTENTION_BASELINE,
                        "ooni_window_hours": OONI_WINDOW_H, "ooni_baseline_hours": OONI_BASE_H,
                        "ooni_min_measurements": OONI_MIN_MEASUREMENTS, "ooni_min_share": OONI_MIN_SHARE,
                        "ooni_rise": OONI_RISE,
                        "cause_window_days": CAUSE_WINDOW_D, "provisional": True},
            "inputs": sorted(INPUT_KINDS.values()), "unplaced": unplaced,
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
                         "counted_days": b.get("by_country_days"), "countries": b.get("countries"),
                         "countries_with_signal": b.get("countries_with_signal"),
                         "signal_countries_would_read": b.get("signal_countries_would_read"),
                         "archive": rates["archive"], "cautions": rates["cautions"],
                         "states": None if not sb else {"state_days": sb["state_days"],
                                                        "would_read": sb["would_read"],
                                                        "days": sb.get("by_state_days"),
                                                        "states": sb.get("states"),
                                                        "states_with_signal": sb.get("states_with_signal"),
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
        own = (b.get("by_country") or {}).get(r["country"], {"natural": 0.0, TENSION: 0.0, "open": 1.0})
        here = own.get(r["reading"])
        ev = (b.get("by_country_events") or {}).get(r["country"]) or {
            "natural": {"days": 0, "hazards": 0}, TENSION: {"days": 0, "notices": 0, "attention_runs": 0}}
        k = days - ev["natural"]["days"] - ev[TENSION]["days"] if r["reading"] == "open" else ev[r["reading"]]["days"]
        world = (f"Across all {b['countries']} countries, {share:.1%} of {n:,} country-days"
                 + (f" (only {b['countries_with_signal']} countr{'y' if b['countries_with_signal'] == 1 else 'ies'} "
                    "showed any signal)."
                    if b.get("countries_with_signal") is not None else "."))
        r["base_rate"] = {
            "share_of_country_days": share,
            "share_of_this_countrys_days": here,
            "this_countrys_days": k,
            "counted_days": days,
            "events": None if r["reading"] == "open" else ev[r["reading"]],
            "text": f"In {country_name(r['country'])}, an outage would have read \"{r['reading']}\" whatever its "
                    f"cause {_on_days(k, days, 'counted days')}{_from_events(r['reading'], ev, k)}. {world}",
            "cautions": rates["cautions"]}
    return doc


def attach_replay(doc, replay):
    """Put the STOP replay beside the base rates (cop-purpose sequence step 4). replay: triage_replay.load()'s
    document, or None. Each internet-outage record whose reading the replay has figures for gets `replay_rate`; the
    others get None (US power outages and unconfirmed dips are not in STOP). With a replay on file, the country base
    rates' "no hit rate yet" caution gives way to the replay's own; the state power rates keep theirs, reworded to say
    the replay does not cover them, because no power outage of known cause has been replayed."""
    import triage_rates  # noqa: E402  (it imports this module, so it is read at call time)
    doc["replay"] = replay
    for r in doc["records"]:
        r["replay_rate"] = None if (not replay or r.get("outage_kind") == "power") \
            else replay_mod.record_rate(replay, r["country"], r["reading"], country_name(r["country"]))
    if replay and doc.get("base_rates"):
        def swap(cs):
            return [replay["caution"] if c == triage_rates.HIT_RATE_CAUTION else c for c in cs or []]
        def swap_state(cs):
            return [replay_mod.STATE_CAUTION if c == triage_rates.HIT_RATE_CAUTION else c for c in cs or []]
        doc["base_rates"]["cautions"] = swap(doc["base_rates"].get("cautions"))
        if doc["base_rates"].get("states"):
            doc["base_rates"]["states"]["cautions"] = swap_state(doc["base_rates"]["states"].get("cautions"))
        for r in doc["records"]:
            if r.get("base_rate"):
                r["base_rate"]["cautions"] = (swap_state if r.get("outage_kind") == "power" else swap)(
                    r["base_rate"].get("cautions"))
    return doc


def _on_days(k, days, word):
    return f"on none of {days} {word}" if k == 0 else f"on {k} of {days} {word}"


def _from_events(reading, ev, k):
    """", from 2 notices and 1 run of unusual attention": the distinct events behind a country's days."""
    if not k or reading == "open":
        return ""
    if reading == "natural":
        parts = [_n(ev["natural"]["hazards"], "hazard")]
    else:
        t = ev[TENSION]
        parts = ([_n(t["notices"], "notice")] if t["notices"] else []) + (
            [f"{t['attention_runs']} run{'' if t['attention_runs'] == 1 else 's'} of unusual attention"]
            if t["attention_runs"] else [])
    return ", from " + " and ".join(parts) if parts else ""


def _state_rate(r, sb):
    """How often a US state power outage would have read the way this one did, whatever its cause, from COP's
    archive (triage_rates.py's state_background), or None."""
    share = (sb or {}).get("would_read", {}).get(r["reading"])
    if share is None:
        return None
    n, days = sb["state_days"], sb.get("by_state_days") or 0
    own = (sb.get("by_state") or {}).get(r["us_state"], {"natural": 0.0, "open": 1.0})
    here = own.get(r["reading"])
    ev = (sb.get("by_state_events") or {}).get(r["us_state"]) or {"days": 0, "hazards": 0}
    k = ev["days"] if r["reading"] == "natural" else days - ev["days"]
    hz = ev["hazards"]
    src = (f", from {hz} hazard{' that takes' if hz == 1 else 's that take'} power lines down"
           if r["reading"] == "natural" and k else "")
    return {"share_of_state_days": share, "share_of_this_states_days": here,
            "this_states_days": k, "days": days, "events": ev if r["reading"] == "natural" else None,
            "text": f"In {r['us_state_name']}, a state power outage would have read \"{r['reading']}\" whatever its "
                    f"cause {_on_days(k, days, 'days')}{src}. Across all {sb.get('states', '')} states, DC and Puerto Rico included, {share:.1%} of "
                    f"{n:,} state-days.",
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
    if name == "elements" and layer.get("scope") in ("state", "counties"):
        sig, minor, standing = layer["significant"], layer["minor"], layer["standing"]
        where = "in or near the outage's counties" if layer["scope"] == "counties" else "in or near this state"
        if sig:
            parts = [f"{_n(len(sig), 'hazard')} that {'takes' if len(sig) == 1 else 'take'} power lines down began "
                     f"{where} in the {HAZARD_BEFORE_H} hours before the reading."]
        else:
            parts = [f"Nothing found: no hazard that takes power lines down began {where} in the "
                     f"{HAZARD_BEFORE_H} hours before the reading."]
        parts += _ended_note(sig)
        mu = layer.get("meters_under_hazards") or {}
        if sig and mu.get("share") is not None:
            parts.append(f"They cover counties holding {mu['share']:.0%} of the meters out.")
        if minor:
            parts.append(f"{_n(len(minor), 'minor hazard')} {where} in the window (not counted).")
        if layer.get("elsewhere"):
            parts.append(f"{_n(len(layer['elsewhere']), 'hazard')} elsewhere in the state (not counted).")
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
            parts.append(f"{_n(len(layer['recent_not_raised']), 'notice')} lowered, re-issued at the same level, "
                         "edited without a change of level or advice, or an embassy alert re-dated from an earlier "
                         "date (not counted).")
        if layer.get("recent_not_counted"):
            kinds = sorted({r.get("alert_type") or "routine" for r in layer["recent_not_counted"]})
            parts.append(f"{_n(len(layer['recent_not_counted']), 'embassy alert')} of a type not counted "
                         f"({', '.join(kinds)}).")
        if layer["standing"]:
            parts.append(f"{_n(len(layer['standing']), 'older notice')} standing (not counted).")
        if layer["after"]:
            parts.append(f"{_n(len(layer['after']), 'notice')} dated after the outage.")
        return " ".join(parts)
    if layer["unusual"]:
        zs = [r["z"] for r in layer["unusual_items"] if isinstance(r.get("z"), (int, float))]
        text = ("Press attention is unusual here: Watchfloor raised it against this country's own last 28 days"
                + (f" (z {max(zs):g})." if zs else "."))
    elif layer.get("elevated"):
        text = ("Press attention is high against its 90-day median, but not unusual against its own last 28 days, "
                "so it is standing attention and not counted.")
    else:
        text = "Nothing found: press attention is not raised."
    if layer.get("after"):
        text += f" {_n(len(layer['after']), 'Watchfloor item')} for days after the outage (not counted)."
    if not layer["data_reaches_outage"]:
        text += " Its newest data ends before the outage."
    return text


PUBLIC_NAME = "triage.json"
LAYER_NAMES = {"elements": "Natural hazards (Elements)", "cordon": "Travel and embassy notices (Cordon)",
               "watchfloor": "Press attention (Watchfloor)"}
WHAT = ("For each internet outage Blackout reports from IODA (country-level drops) and Cloudflare Radar (anomalies "
        "Cloudflare verified, on a network or a country, and Outage Center entries that name a country), what "
        "three other layers show for that country around the hour it began: natural hazards (Elements), travel "
        "and embassy notices (Cordon: the State Department's advisories, embassy security and demonstration "
        "alerts, and UK, Canadian and New Zealand travel advice when its level or advice changes) and press "
        "attention (Watchfloor, against each country's own last 28 days). A Radar entry that names no place is "
        "listed, not read. "
        "For each US state where ODIN's reporting utilities show more than 50,000 meters without power, the same "
        "layers for that state: hazards in or near it, while Cordon and Watchfloor do not cover US states.")
NOT_A_WARNING = ("A reading sorts public reports. It is not a warning, not a forecast and not a finding of cause, "
                 "and it carries no probability. \"Nothing found\" and \"No current data\" are different answers.")
DRAFT_NOTE = ("A drafted question that would let this reading be scored later. It is not a registered Verdict "
              "forecast and no likelihood is attached to it.")
CAUSE_NOTE = ("A second drafted question, on cause, beside the OONI line: whether an outside source names a "
              "government order. It is not a registered Verdict forecast and no likelihood is attached to it.")


def verdict_id(v):
    """The id the drafted question would get in Verdict's ledger: verdict:<day it is known>:<slug>, or None."""
    known = ((v.get("resolution") or {}).get("known_at") or "")[:10]
    return f"verdict:{known}:{v['id_slug']}" if known and v.get("id_slug") else None


def question_draft(v, note=DRAFT_NOTE):
    """The drafted Verdict question as the public sees it: the question, its window and rule, and that it is a
    draft. The empty probability and approval fields stay in out/: published, they would read as a forecast."""
    out = {"status": "draft, not registered", "note": note, "verdict_id": verdict_id(v), "question": v["question"],
           "window_ends": v["window_ends"], "rule_draft": v["rule_draft"], "caution": v["caution"],
           "resolution": v.get("resolution")}
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
        rec["replay_rate"] = r.get("replay_rate")
        rec["layers"] = {name: {"name": LAYER_NAMES[name], "showed": showed(name, layer), **layer}
                         for name, layer in r["layers"].items()}
        rec["question_draft"] = question_draft(r["verdict_draft"])
        rec["cause_question_draft"] = question_draft(r["cause_draft"], CAUSE_NOTE) if r.get("cause_draft") else None
        rec["registerable"] = bool(r.get("registerable"))
        records.append(rec)
    return {"join": doc["join"], "what": WHAT, "not_a_warning": NOT_A_WARNING, "readings": READINGS,
            "power_readings": POWER_READINGS, "snapshot_generated_at": doc["snapshot_generated_at"], "layer_states": doc["layer_states"],
            "windows": doc["windows"], "baseline_given": doc["baseline_given"], "base_rates": doc["base_rates"],
            "replay": doc.get("replay"), "ended_items": doc.get("ended_items"), "inputs": doc.get("inputs"), "unplaced": doc.get("unplaced"),
            "records": records}


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
    ap.add_argument("--replays", default=str(replay_mod.REPLAYS),
                    help="folder of the STOP replay runs (history/replays); without them the readings carry no replay figures")
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
    doc = attach_replay(attach_rates(triage(load(a.snapshot), (rates or {}).get("history"), ended), rates),
                        replay_mod.load(a.replays))
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
        if r.get("replay_rate"):
            print(f"    STOP replay: {r['replay_rate']['text']}")
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
              f" | watchfloor: {len(L['watchfloor']['unusual_items'])} unusual, {len(L['watchfloor']['elevated'])}"
              " elevated"
              f"{'' if L['watchfloor']['data_reaches_outage'] else ' (data ends before the outage)'}")
    if doc["replay"] is None:
        print("no STOP replay on file: the readings carry no replay figures")
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
