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
    python tools/snapshot.py --archive out/archive            # also keep a gzip copy named by the UTC day, and one
                                                              # per 6-hour slot (<day>T00Z.snap.json.gz and so on)
    python tools/snapshot.py --dropped-out out/dropped.jsonl  # also write the malformed items left out, with reasons

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
sys.path.insert(0, str(ROOT / "tools"))
from tic_doctrine import contract  # noqa: E402
from tic_doctrine import history  # noqa: E402
from tic_doctrine import ladder  # noqa: E402
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
# A register key ending in * covers every source id that starts with the rest (usembassy-*: one id per embassy post).
SOURCE_PREFIXES = tuple(k[:-1] for k in SOURCES if k.endswith("*"))


def registered(key) -> bool:
    return key in SOURCES or key.startswith(SOURCE_PREFIXES)
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"
PREVIOUS = "https://cop.theintelligence.company/data/today.geojson"
UA = "tic-cop/0.1 (+https://cop.theintelligence.company)"
LANGUAGE_KEY = "https://github.com/metacog-osint/noise-floor/blob/main/doctrine/kit/docs/language-key.md"
SLOT_HOURS = 6  # the archive also keeps one snapshot per 6-hour slot of the UTC day (00, 06, 12, 18), not only the day's last
DROP = {"headlines", "machine_summary"}  # large text the map never shows; the program's own page has it


def utc(s):
    """ISO 8601 UTC string to an aware datetime, or None."""
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def covered_until(props, now):
    """When an item's data stops being news, for judging a program's freshness. A source that gives no time
    (data_as_of_precision "day" or "month", kit 0.5.6) puts data_as_of at midnight UTC at the start of that day or
    month, but the data covers the whole period, so it counts to the end of it, never later than now. Without this a
    program publishing whole-UTC-day items read a day older than it is (Watchfloor and Noisefloor, 16 Nov 2026)."""
    dt = utc(props.get("data_as_of"))
    if dt is None:
        return None
    prec = props.get("data_as_of_precision")
    if prec == "day":
        dt += timedelta(days=1)
    elif prec == "month":
        dt = dt.replace(year=dt.year + (dt.month == 12), month=dt.month % 12 + 1)
    return min(dt, now) if prec in ("day", "month") else dt


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
        if "title_has" in rule:     # a feed with no measure for its scope (Blackout): the scope its title states
            if rule["title_has"].lower() in str(props.get("title") or "").lower():
                return rule["value"], rule["label"], i, rule["value"]
            continue
        v = dig(props, rule["path"])
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v, strength_label(props, rule), i, abs(v) if rule.get("abs") else v
    return None, None, None, None


def strength_label(props, rule):
    """The rule's label, or its "alt" label when the path named in alt["when_set"] holds a value (25 Nov 2026: Wake's
    change against the same weeks a year earlier is measured against a fixed window while a moved level stands, so
    the label says so). Only the words change: the value, the rule and the rank are the rule's own."""
    alt = rule.get("alt")
    if alt and dig(props, alt["when_set"]) not in (None, "", False):
        return alt["label"]
    return rule["label"]


# --- Shared voices (15 Nov 2026, suite review: "COP should treat independence_group gdelt as one voice") ------------
# Watchfloor and Context both read GDELT and stamp independence_group "gdelt" on their sources, and they number their
# zones and Watch Topics alike: Context's CX-WP-Z07 is Watchfloor's WF-WP-Z07, CX-WP-T3 is WF-WP-T3, and a Watchfloor
# country item inside a zone carries that zone's WF-WP-Zxx. Their agreement is one upstream read twice, never two
# layers, so COP gives them one seat in the picks and joins their readings of one zone on one pick. Each item keeps
# its own id, sources, licence and attribution: joined, never merged.
SHARED_GROUPS = ("gdelt",)
# Context's Z17 (Horn of Africa: DJ, ER, ET, SO) is Watchfloor's Z18 (Ethiopia and Eritrea) plus Z19 (Somalia).
ZONE_ALIASES = {"CX-WP-Z17": ("Z18", "Z19")}
_WP = re.compile(r"^([A-Z]+-WP-)([ZT])(\d+)$")


def shared_group(props):
    """The shared independence group an item's sources name ("gdelt"), or None."""
    groups = {s.get("independence_group") for s in (props.get("sources") or []) if isinstance(s, dict)}
    return next((g for g in SHARED_GROUPS if g in groups), None)


def voice_keys(props):
    """The shared voices an item speaks for: ["gdelt:Z07"] for a zone (Context's Z17 gives ["gdelt:Z18",
    "gdelt:Z19"]), ["gdelt:T3"] for a Watch Topic, ["gdelt:<country>"] for a GDELT item outside every zone, [] for an
    item of no shared group. Two items are one voice when their keys meet."""
    group = shared_group(props)
    if not group:
        return []
    wp = props.get("warning_problem_id")
    m = _WP.match(wp) if isinstance(wp, str) else None
    if m:
        if wp in ZONE_ALIASES:
            return [f"{group}:{z}" for z in ZONE_ALIASES[wp]]
        letter, n = m.group(2), int(m.group(3))
        return [f"{group}:{letter}{n:02d}" if letter == "Z" else f"{group}:{letter}{n}"]
    return sorted({f"{group}:{c}" for c in (props.get("countries") or []) if isinstance(c, str)})


def seat_of(props):
    """Who an item speaks for in the picks: its shared group, else its own program."""
    return shared_group(props) or props.get("cop_program")


def reading(props):
    """One program's reading of a shared voice, as a pick carries it: its own id, program, tier and size."""
    return {"id": props["id"], "program": props.get("cop_program"), "tier": props.get("tier"),
            "warning_problem_id": props.get("warning_problem_id"), "strength": props.get("cop_strength_text"),
            "stale": bool(props.get("cop_stale"))}


def no_longer_current(props, now=None):
    """The program itself says this has ended (Elements' GDACS items, measures.current false), or the item is past its
    own valid_until (ideas#164: 21 items were on 23 Nov, 4 Wake anomalies among them): never a pick. Stored and shown
    as before; only the picks skip it."""
    if dig(props, "measures.current") is False:
        return True
    until = utc(props.get("valid_until")) if now is not None else None
    return bool(until and until < now)


def not_admitted(props, prog, now=None):
    """Why this program's own pick rules leave the item out, or None (ideas#164: like with like). Each rule in the
    program's "pick_admits" names the ids it covers by prefix and either the last_change values it admits (Cordon:
    FCDO, Canada and SafeTravel advice only when its level moved, the rule COP's outage reading uses) or how many hours from
    the item's own date it stays a candidate (Cordon: a CDC notice for 72 hours, Tom 23 Nov 2026). Stored and shown
    as before; only the picks skip it."""
    pid = str(props.get("id") or "")
    for rule in prog.get("pick_admits") or []:
        if not pid.startswith(tuple(rule["id_prefix"])):
            continue
        if "last_change" in rule and props.get("last_change") not in rule["last_change"]:
            return rule["why"]
        if "within_hours" in rule and now is not None:
            at = utc(props.get("last_change_at")) or utc(props.get("data_as_of"))
            if not at or (now - at).total_seconds() > rule["within_hours"] * 3600:
                return rule["why"]
    return None


def rests_on_claim(props):
    """The item rests only on a party's claim or on state media (kit event ladder, docs/event-ladder.md): it is
    labelled, raises nothing on its own and is never a pick, whatever tier its feed gives it, until it is verified on
    an origin outside the party and outside state media. Stored and shown as before; only the picks skip it."""
    return ladder.rests_on_claim(props)


def standing(props):
    """The feed says this did not change on its last reading (contract 2.1, last_change "none")."""
    return props.get("last_change") == "none"


def pick(candidates, max_picks, programs):
    """Picks from {program id: its placed anomalies, strongest first}. One seat at a time, in config order, each seat
    giving its next candidate per round. A seat is a program, except that the programs of a shared group (GDELT:
    Watchfloor and Context) share one seat, alternating their own queues, since strength is never compared across
    programs. A candidate whose voice an earlier pick already speaks for is never a second pick. Returns a list of
    (feature, program, rank within its program's queue)."""
    seats, order = {}, []
    for prog in programs:
        for i, f in enumerate(candidates.get(prog["id"]) or []):
            seat = seat_of(f["properties"])
            if seat not in seats:
                seats[seat] = {}
                order.append(seat)
            seats[seat].setdefault(prog["id"], []).append((i, f))
    queues = {}
    for seat in order:      # alternate the programs within a seat: first of each, then second of each, ...
        lanes = list(seats[seat].values())
        queues[seat] = [lane[k] + (pid,) for k in range(max(map(len, lanes)))
                        for pid, lane in seats[seat].items() if k < len(lane)]
    taken, out, at = set(), [], {s: 0 for s in order}
    while len(out) < max_picks:
        added = False
        for seat in order:
            q = queues[seat]
            while at[seat] < len(q):
                i, f, pid = q[at[seat]]
                at[seat] += 1
                keys = set(voice_keys(f["properties"]))
                if keys & taken:
                    continue        # the same voice is already a pick: it rides on that pick as a reading
                taken |= keys
                out.append((f, pid, i))
                added = True
                break
            if len(out) >= max_picks:
                break
        if not added:
            break
    return out


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
    for key, kind, word in (("countries", str, "strings"), ("sources", dict, "objects"),
                            ("sea_areas", str, "strings")):  # the lists COP walks
        v = props.get(key)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, kind) for x in v)):
            return f"{key} is not a list of {word}"
    # Contract revision 2.1 (kit 0.6.0): the suite ledger keeps an item under its subject_id and names its lane from
    # its first source's `lane`; either as anything but text would raise there, so the item is left out instead.
    if props.get("subject_id") is not None and not (isinstance(props["subject_id"], str) and props["subject_id"].strip()):
        return "subject_id is not a string"
    if any(s.get("lane") is not None and not isinstance(s["lane"], str) for s in props.get("sources") or []):
        return "a source's lane is not a string"
    if not _finite(feature):
        return "it holds a number that is not finite (NaN or Infinity)"
    return None


def sift(features, pid=None, rev=None):
    """(the features COP can carry, their lint problems, one row for each it left out).

    A feature goes through when unsound() finds nothing and the kit's contract check can read it; the check's
    findings come back as (item id, problem) pairs for lint_feed. A row is {"id", "program", "index", "reason"}:
    the id when it could be read (else None), and the feature's place in its feed, counted from 0. rev is the feed
    header's contract_rev: a feed that states "2.1" is checked strictly, as its own linter checks it (kit 0.6.0)."""
    kept, problems, dropped = [], [], []
    for n, f in enumerate(features):
        why, found = unsound(f), []
        if why is None:
            try:
                found = contract.check(f, rev=rev)
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
    sift() let through (item_problems). Returns (lint, notes): notes counts kit 0.6.0's revision 2.1 notices (a new
    field absent), kept out of `warnings` so a feed's count does not jump on the day COP vendors the kit; they are
    the `revision` member beside `coverage`. A revision field of the wrong shape is an error and counts as one."""
    errors, warnings, notes, examples = 0, 0, 0, []
    problems = [("feed", p) for p in kitlint.check_header({k: v for k, v in feed.items() if k != "features"})]
    for item_id, (std, sev, msg) in problems + item_problems:
        if sev == "error":
            errors += 1
            if len(examples) < 3:
                examples.append(f"{item_id}: {msg}")
        elif std == contract.REVISION:
            notes += 1
        else:
            warnings += 1
    return {"errors": errors, "warnings": warnings, "examples": examples}, notes


def coverage(items) -> dict:
    """{revision 2.1 field: items carrying it}, the kit lint report's `coverage`, for one program's items: the
    migration's dashboard (feed-contract proposal, section 5) and decision D9's trigger."""
    out = dict.fromkeys(contract.REVISION_FIELDS, 0)
    for i in items:
        p = i.get("properties") or {}
        for k in out:
            out[k] += p.get(k) is not None
    return out


def revision(feed, notes) -> dict:
    """What a feed's header says of contract revision 2.1 (contract_rev, the kit it vendors) and how many 2.1 notices
    its items drew. None of a header value that is not text."""
    return {"contract_rev": header_text(feed.get("contract_rev")), "kit": header_text(feed.get("kit")), "notes": notes}


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
    program whose config sets "source_health" (Headwind from 15 Nov 2026; every program with a health block from 24 Nov). Such a program keeps a failed source's last
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
                                           "detail": (v or {}).get("detail") if isinstance(v, dict) else None,
                                           "failures": (v or {}).get("failures") if isinstance(v, dict) else None}
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
            items, problems, dropped = sift(features_of(feed), pid, rev=header_text(feed.get("contract_rev")))
            lint, notes = lint_feed(feed, problems)
            rev = revision(feed, notes)
            attribution, licence = header_text(feed.get("attribution")), header_text(feed.get("licence"))
            lanes_by_program[pid] = source_lanes(feed)
            off, sources = unread_sources(feed, prog), source_summary(feed, prog)
        else:
            off = {}
            sources = ((previous.get("cop_programs") or {}).get(pid) or {}).get("sources") if prog.get("source_health") else None
            items = [f for f in previous_items if f["properties"].get("cop_program") == pid]
            dropped = [d for d in previous_dropped if d["program"] == pid]
            from_previous = bool(items)
            lint, rev = None, None
            prev_prog = (previous.get("cop_programs") or {}).get(pid) or {}
            attribution, licence = header_text(prev_prog.get("attribution")), header_text(prev_prog.get("licence"))
        all_dropped += dropped
        newest = max((utc(i.get("properties", {}).get("data_as_of")) for i in items
                      if utc(i.get("properties", {}).get("data_as_of"))), default=None)
        covered = max((c for c in (covered_until(i.get("properties") or {}, now) for i in items) if c), default=None)
        if feed is not None and prog.get("freshness") == "feed" and utc(feed.get("generated_at")):
            newest = utc(feed.get("generated_at"))  # notice programs: items carry issue dates, the feed is current
        if feed is not None and prog.get("freshness") == "feed" and utc(feed.get("generated_at")):
            covered = newest      # a feed-time program is judged by the feed, not by its items
        age = round((now - covered).total_seconds() / 3600, 1) if covered else None
        state = state_for(age, prog, feed is not None)
        # A source's register key is its id, else its publisher; one that gives neither as text is the linter's.
        keys = {s.get("id") or s.get("publisher") for i in items for s in (i["properties"].get("sources") or [])
                if isinstance(s.get("id") or s.get("publisher"), str)}
        unregistered = sorted(k for k in keys if not registered(k))
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
            if p.get("tier") == "anomaly" and item.get("geometry") and state != "stale" and not src_off \
                    and not no_longer_current(p, now) and not not_admitted(p, prog, now) and not rests_on_claim(p):
                # What changed comes before what is merely still there (Tom, 21 Nov 2026).
                ranked.append((standing(p), 99 if rule is None else rule, -(rank or 0), order, feature))
        candidates[pid] = [f for *_, f in sorted(ranked, key=lambda r: r[:4])]
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
            # Contract revision 2.1 (kit 0.6.0): items carrying each new field, and what the feed's header states.
            "coverage": coverage(items), "revision": rev,
        })

    # Picks: each program's strongest placed anomaly first, one program at a time, at most max_picks. Watchfloor and
    # Context (GDELT) are one seat and one voice per zone (pick() above); a pick of a shared voice lists every
    # reading of that voice, its own first, then anomalies before routine items, in config order.
    names = {p["id"]: p["name"] for p in CONFIG["programs"]}
    place = {p["id"]: i for i, p in enumerate(CONFIG["programs"])}
    voiced = [(i, f, set(voice_keys(f["properties"]))) for i, f in enumerate(features)]
    voiced = [v for v in voiced if v[2]]
    picks = []
    for f, pid, i in pick(candidates, CONFIG["max_picks"], CONFIG["programs"]):
        p = f["properties"]
        rank = "strongest" if i == 0 else "next strongest"
        owner = names[pid] + ("'" if names[pid].endswith("s") else "'s")
        reason = f"{owner} {rank} anomaly with a place"
        if p.get("cop_strength") is not None:
            reason += f": {p['cop_strength_text']}"
        entry = {"n": len(picks) + 1, "id": p["id"], "program": pid, "reason": reason}
        keys = voice_keys(p)
        if keys:
            others = sorted((v for v in voiced if v[1] is not f and v[2] & set(keys)),
                            key=lambda v: (v[1]["properties"].get("tier") != "anomaly",
                                           place.get(v[1]["properties"].get("cop_program"), 99),
                                           -(v[1]["properties"].get("cop_strength") or 0), v[0]))
            entry |= {"voice": keys, "readings": [reading(p)] + [reading(v[1]["properties"]) for v in others]}
        picks.append(entry)
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
              {"strength_labels": [r["label"] + (f" (while a moved level stands: {r['alt']['label']})" if r.get("alt") else "")
                                   for r in p["strength"]],
               "pick_admits": [r["why"] for r in p.get("pick_admits") or []]} for p in CONFIG["programs"]]
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


def place_points(geometry):
    """One (lon, lat) per separate place in a geometry: each part of a Multi- shape stands alone, so two places far
    apart never average to a spot between them. A part's point is the mean of its vertices, with longitude averaged
    around the circle so a shape across the antimeridian stays there."""
    kind, c = geometry["type"], geometry["coordinates"]
    if kind == "Point":
        parts = [[c]]
    elif kind == "MultiPoint":
        parts = [[p] for p in c]
    elif kind == "LineString":
        parts = [c]
    elif kind == "MultiLineString":
        parts = c
    elif kind == "Polygon":
        parts = c[:1]
    elif kind == "MultiPolygon":
        parts = [poly[0] for poly in c if poly]
    else:
        return []
    points = []
    for part in parts:
        ring = part[:-1] if len(part) > 1 and part[0] == part[-1] else part
        if not ring:
            continue
        east = sum(math.cos(math.radians(p[0])) for p in ring)
        north = sum(math.sin(math.radians(p[0])) for p in ring)
        points.append((math.degrees(math.atan2(north, east)), sum(p[1] for p in ring) / len(ring)))
    return points


def home_points(today, now):
    """site/data/home-points.json: where today's unusual findings are, for the small world map on the hub's home page,
    which must not fetch the whole snapshot. Places only, to 0.1 degree, with no id, title or program. The same items
    COP's picks may draw from (placed anomalies whose program and source were read), less any past its valid_until."""
    places = set()
    for f in today["features"]:
        p = f["properties"]
        until = utc(p.get("valid_until"))
        if p.get("tier") != "anomaly" or not f.get("geometry") or p.get("cop_stale") or (until and until < now):
            continue
        places |= {(round(lon, 1) + 0.0, round(lat, 1) + 0.0) for lon, lat in place_points(f["geometry"])}
    return {"generated_at": iso(now), "points": [list(place) for place in sorted(places)]}


def write_json(path, obj, compact=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None,
                      indent=None if compact else 1)
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_gz_json(path, obj):
    """A gzip copy of obj with a fixed header (no file name, time 0), so the same object gives the same bytes under
    any name and at any time: the day's copy and the slot's copy of one build are one file to an archive that stores
    files by content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
        fh.write(data)


def slot_name(now):
    """The archive name of the 6-hour slot `now` falls in: <day>T<00|06|12|18>Z.snap.json.gz. Not .geojson.gz, so the
    readers that glob the daily copies (<day>.geojson.gz) never see it."""
    return f"{now:%Y-%m-%d}T{now.hour // SLOT_HOURS * SLOT_HOURS:02d}Z.snap.json.gz"


def dropped_rows(today, feeds, now, ended_dropped=()):
    """Rows for tools/dropped.py: each feature sift() left out of this build's feeds (and ended lists), with its reason
    and the item as published. today["dropped"] gives program and place in the feed; a program read from its last good
    copy is not in `feeds` (that copy's drops were logged when it was fresh)."""
    import dropped as droplog
    rows, at = [], iso(now)
    for d in today.get("dropped") or []:
        feed = feeds.get(d.get("program"))
        feats = features_of(feed) if feed is not None else []
        if isinstance(d.get("index"), int) and 0 <= d["index"] < len(feats):
            rows.append(droplog.row_for(d, feats[d["index"]], at))
    rows += [droplog.row_for(d, item, at, source="ended") for d, item in ended_dropped]
    return rows


def fetch_ended(now, out, fixtures=None, archive=None, dropped=None):
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
        original = data["features"]
        data["features"], _, left_out = sift(original, pid)  # the joins that read this list need sound items too
        if dropped is not None:  # kept for the dropped-items log (tools/dropped.py): the item and why
            dropped += [(d, original[d["index"]]) for d in left_out]
        data["cop_fetched_at"] = iso(now)
        write_json(dest, data)
        if archive:
            write_gz_json(archive / f"{now:%Y-%m-%d}.{pid}-ended.json.gz", data)
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
                    help="where each program's ended-items file goes (<program>.geojson; not drawn, not served from the site)")
    ap.add_argument("--dropped-out", help="write the malformed items this build left out, with reasons, as JSON lines "
                                          "(tools/dropped.py keeps them with the ledger); removed when there are none")
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
    write_json(out / "home-points.json", home_points(today, now))
    if a.archive:
        arch = Path(a.archive)
        write_gz_json(arch / f"{now:%Y-%m-%d}.geojson.gz", today)
        write_gz_json(arch / slot_name(now), today)  # first run of each slot is kept by the workflow
    ended_dropped = []
    for line in fetch_ended(now, Path(a.ended_out), a.fixtures, Path(a.archive) if a.archive else None, ended_dropped):
        print(line)
    if a.dropped_out:
        dest = Path(a.dropped_out)
        rows = dropped_rows(today, feeds, now, ended_dropped)
        if rows:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows),
                            encoding="utf-8", newline="\n")
        else:
            dest.unlink(missing_ok=True)
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
