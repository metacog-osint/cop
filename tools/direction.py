"""Direction of concern: which places are getting more or less concerning, layer by layer. Standard library only.

    python tools/direction.py --memory out/memory --snapshot site/data/today.geojson \
        --verdict site/data/agent/verdict.json --publish site/data

Tom, 21 Nov 2026: the brag is the areas of increasing or decreasing concern, driven by the changes in each layer's data.
For each warning problem and each of Watchfloor's zones, every layer's last seven days are compared with the fourteen
days before, from COP's memory (tools/memory.py), so an item that has left the picture still counts as what came
before. Each layer is read on its own and shown side by side. Measurements are never added together.

The area's word is a count of layers moving, stated with its rule:
    Rising        two or more layers up, and more up than down
    Easing        two or more layers down, and more down than up
    Mixed         layers moving both ways, in equal numbers or one each
    Edging up     one layer up, none down
    Edging down   one layer down, none up
    Steady        layers read, none moved
    Building      fewer than two layers have enough history to read

Ship transits are a condition, listed beside the reading and not counted, since PortWatch lags by up to 16 days.

The voice rule holds. This says which measurements moved against their own recent normal. It is not a warning, a
forecast or a finding of cause, and it carries no probability. Probabilities live on Verdict's registered questions.
"""

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import memory as mem_tool  # noqa: E402
import three_signal as ts  # noqa: E402
import triage as tri  # noqa: E402

JOIN = {"id": "direction", "version": "0.1", "published": True,
        "inputs": ["noisefloor", "cordon", "blackout", "wake", "watchfloor", "verdict"]}
PUBLIC_NAME = "direction.json"

RECENT_DAYS = 7          # the window read
PRIOR_DAYS = 14          # the days before it, the area's own recent normal
MIN_RECENT = 5           # days the layer must have been read in the window ...
MIN_PRIOR = 7            # ... and in the days before it
MIN_EVENTS = 4           # a count layer needs this many events across both windows to move at all
MOVE_RATIO = 1.5         # a count must change by this factor ...
MOVE_P = 0.10            # ... and be this unlikely if the daily rate had not changed (one-sided exact binomial)
LEVEL_RATIO = 1.25       # a level's median must change by this factor and leave the middle half of the days before
TRAFFIC_PCT = 25         # chokepoint transits this far from their 90-day median ...
TRAFFIC_Z = 1.5          # ... and this many deviations from it
MAX_LAG_DAYS = 3         # a layer whose newest data is older than this has no current data
WIDE_SHARE = 1 / 3       # a many-country item counts for an area only if this share of its countries are in the area
ITEMS_SHOWN = 4          # items listed under a layer of a warning problem
NEAR_KM = 150            # a Noisefloor event's point is a grid cell's centre: within this of a country counts for it
FEED_HISTORY_DAYS = {"attacks": 30}   # Wake's feed carries UKMTO's reports for 30 days, so its history predates Wake
CONDITIONS = ("traffic",)  # shown beside the reading, never counted: PortWatch lags up to 16 days (Gulf charter, 4)
COUNTRIES = ROOT / "doctrine" / "kit" / "geo" / "countries.json"

LAYER_NAMES = {"attacks": "Attack reports (UKMTO)", "interference": "GPS interference events", "notices": "Security notices",
               "outages": "Confirmed internet outages", "press": "Press reports of violence", "traffic": "Ship transits"}
LAYER_PROGRAM = {"attacks": "wake", "interference": "noisefloor", "notices": "cordon", "outages": "blackout",
                 "press": "watchfloor", "traffic": "wake"}
AREA_LAYERS = ("interference", "notices", "outages", "press", "traffic")
SHORT = {"attacks": "attack reports", "interference": "GPS interference", "notices": "security notices",
         "outages": "outages", "press": "press reports of violence", "traffic": "ship transits"}
NOTICE_WORDS = tuple(ts.MILITARY_WORDS) + ("SECURITY INCIDENT",)

WORDS = {"rising": "Rising", "easing": "Easing", "mixed": "Mixed", "edging_up": "Edging up",
         "edging_down": "Edging down", "steady": "Steady", "building": "Building a baseline"}
WHAT = ("Which places are getting more or less concerning: each layer's last seven days against the fourteen days "
        "before, in that place. Items that have left the picture still count, from COP's memory.")
NOT_A_WARNING = ("A direction says which measurements moved against their own recent normal. It is not a warning, a "
                 "forecast or a finding of cause, and it carries no probability. Measurements are never added "
                 "together: the word counts how many layers moved.")


def _d(day):
    return date.fromisoformat(day)


def _days(end, n):
    return [(end - timedelta(days=i)).isoformat() for i in range(n)]


def _when(day):
    d = _d(day)
    return f"{d.day} {d:%b}"


def in_family(k, r, layer):
    """Does this subject count toward this layer?"""
    title = str(r.get("t") or "")
    if layer == "attacks":
        return k.startswith("wake:ukmto:") and ": Attack," in title
    if layer == "interference":
        return k.startswith(("noisefloor:event:", "noisefloor-event-"))
    if layer == "outages":
        return k.startswith(("blackout:radar-outage:", "blackout:netblocks:"))
    if layer == "notices":
        if k.startswith("cordon:alert:"):
            return tri.alert_type(title) in tri.ALERT_COUNTED    # a holiday-travel "security alert" is not one
        if k.startswith("cordon:navwarn:"):
            return any(w in title.upper() for w in NOTICE_WORDS)
    return False


def in_area(k, r, area, layer):
    """Inside the problem's circle, or among its countries. A wide item needs a third of its countries in the area."""
    if area.get("worldwide"):
        return True
    wanted = set(area.get("countries") or [])
    countries = set(r.get("c") or [])
    by_country = layer == "outages" or k.startswith("cordon:alert:")
    if area.get("radius_km") and not by_country:
        xy = r.get("xy")
        if not xy:
            return False
        lon0, lat0 = area["center"]
        return tri._km(lat0, lon0, xy[1], xy[0]) <= area["radius_km"]
    if not countries:
        return False
    return len(countries & wanted) / len(countries) >= WIDE_SHARE


def load_shapes():
    """cc -> [(ring, bbox)] from the kit's country outlines."""
    data = json.loads(COUNTRIES.read_text(encoding="utf-8"))
    out = {}
    for c in data["countries"]:
        out[c["cc"]] = [(ring, (min(p[0] for p in ring), min(p[1] for p in ring),
                                max(p[0] for p in ring), max(p[1] for p in ring))) for ring in c["rings"] if ring]
    return out


def _inside_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(p[:2] for p in ring):
        xj, yj = ring[j][:2]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_area(lat, lon, area, shapes):
    """Inside the problem's circle; for an area of countries, inside one of them or within NEAR_KM of its outline."""
    if area.get("worldwide"):
        return True
    if area.get("radius_km"):
        lon0, lat0 = area["center"]
        return tri._km(lat0, lon0, lat, lon) <= area["radius_km"]
    pad = NEAR_KM / 80
    for cc in area.get("countries") or []:
        for ring, (x0, y0, x1, y1) in shapes.get(cc) or []:
            if not (x0 - pad <= lon <= x1 + pad and y0 - pad <= lat <= y1 + pad):
                continue
            if _inside_ring(lon, lat, ring) or any(tri._km(lat, lon, p[1], p[0]) <= NEAR_KM for p in ring[::3]):
                return True
    return False


def tail_up(a, n, p):
    return sum(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(a, n + 1))


def tail_down(a, n, p):
    return sum(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(0, a + 1))


def count_move(a, b, ra, rb):
    """"up", "down" or "steady" for a events in ra read days against b events in rb read days."""
    n = a + b
    if n < MIN_EVENTS:
        return "steady"
    p = ra / (ra + rb)
    rate_a, rate_b = a / ra, b / rb
    if rate_a >= MOVE_RATIO * rate_b and tail_up(a, n, p) <= MOVE_P:
        return "up"
    if rate_a * MOVE_RATIO <= rate_b and tail_down(a, n, p) <= MOVE_P:
        return "down"
    return "steady"


def level_move(recent, prior):
    med_r, med_p = statistics.median(recent), statistics.median(prior)
    q = statistics.quantiles(prior, n=4, method="inclusive")
    if med_r >= LEVEL_RATIO * med_p and med_r > q[2]:
        return "up"
    if med_r * LEVEL_RATIO <= med_p and med_r < q[0]:
        return "down"
    return "steady"


def _through(mem, program, today):
    """The layer's last complete day with data: yesterday, or its newest data day if it lags. None if too old."""
    newest = None
    for r in mem["subjects"].values():
        if r["p"] != program or not r["at"]:
            continue
        days = [x for x in r["at"] if x <= today]
        if days and (newest is None or days[-1] > newest):
            newest = days[-1]
    if newest is None:
        return None
    end = min(_d(today) - timedelta(days=1), _d(newest))
    return None if (_d(today) - end).days > MAX_LAG_DAYS else end


def _read_days(mem, program, days):
    return [d for d in days if program in (mem["days"].get(d) or [])]


def _same(k, r):
    """One report under two ids (Cordon's embassy alerts reach it by two routes) is one report."""
    return (mem_tool.family(k), tuple(sorted(r.get("c") or [])), " ".join(str(r.get("t") or "").lower().split()))


def _listed(mem, area, layer, today, recent, index):
    """The newest items of this layer in the area, with what COP remembers of each."""
    hits, want, seen = [], set(recent), set()
    for k, r in mem["subjects"].items():
        if r["p"] != LAYER_PROGRAM[layer] or not in_family(k, r, layer) or not in_area(k, r, area, layer):
            continue
        days = [d for d in r["at"] if d in want]
        if days:
            hits.append((max(days), k, r))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    out = []
    for d, k, r in hits:
        if _same(k, r) in seen:
            continue
        seen.add(_same(k, r))
        out.append({"id": r["id"], "title": r.get("t"), "day": d,
                    "memory": mem_tool.compare(mem, {"id": r["id"], "subject_id": k, "countries": r.get("c"),
                                                     "event_day": d}, today, index)["line"]})
        if len(out) == ITEMS_SHOWN:
            break
    return out


def read_interference(mem, area, today, index, detail, shapes):
    """Counts from Noisefloor's full daily files in the memory, so the feed's old six-a-day cap cannot read as a rise."""
    held = (mem.get("events") or {}).get("noisefloor") or {}
    base = {"layer": "interference", "name": LAYER_NAMES["interference"], "program": "noisefloor"}
    days = sorted(d for d in held if d < today)
    if not days or (_d(today) - _d(days[-1])).days > MAX_LAG_DAYS:
        return base | {"state": "thin", "text": "No current data."}
    through = _d(days[-1])
    recent, prior = _days(through, RECENT_DAYS), _days(through - timedelta(days=RECENT_DAYS), PRIOR_DAYS)
    got_r, got_p = [d for d in recent if d in held], [d for d in prior if d in held]
    if len(got_r) < MIN_RECENT or len(got_p) < MIN_PRIOR:
        return base | {"state": "thin", "text": f"Not enough history yet: {len(got_p)} of the {PRIOR_DAYS} days before."}
    a = sum(1 for d in got_r for x in held[d] if point_in_area(x[0], x[1], area, shapes))
    b = sum(1 for d in got_p for x in held[d] if point_in_area(x[0], x[1], area, shapes))
    usual = b / len(got_p) * RECENT_DAYS
    out = base | {"state": count_move(a, b, len(got_r), len(got_p)), "recent": a, "usual_per_week": round(usual, 1),
                  "test": {"kind": "count", "a": a, "b": b, "ra": len(got_r), "rb": len(got_p), "through": days[-1]},
                  "text": f"{a} Major or Notable in the last 7 days, against about {usual:.1f} a week in the 14 days "
                          "before."}
    if a == 0 and b == 0:
        out |= {"state": "quiet", "text": "None in the last 21 days."}
    if detail:
        listed = _listed(mem, area, "interference", today, recent, index)
        if listed:
            out["items"] = listed
    return out


def dated_events(mem, area, layer, wanted):
    """One (report, day) pair per report on each wanted day, so one report under two ids counts once."""
    program, dated = LAYER_PROGRAM[layer], set()
    for k, r in mem["subjects"].items():
        if r["p"] != program or not in_family(k, r, layer) or not in_area(k, r, area, layer):
            continue
        dated |= {(_same(k, r), d) for d in r["at"] if d in wanted}
    return dated


def read_count(mem, area, layer, today, through, index, detail):
    program = LAYER_PROGRAM[layer]
    recent, prior = _days(through, RECENT_DAYS), _days(through - timedelta(days=RECENT_DAYS), PRIOR_DAYS)
    ra, rb = len(_read_days(mem, program, recent)), len(_read_days(mem, program, prior))
    if layer in FEED_HISTORY_DAYS and mem["days"]:
        # The feed itself carries this many days back, so days before the program joined COP are still covered.
        reach = (_d(min(d for d, read in mem["days"].items() if program in read))
                 - timedelta(days=FEED_HISTORY_DAYS[layer])).isoformat()
        ra, rb = sum(1 for d in recent if d >= reach), sum(1 for d in prior if d >= reach)
    if ra < MIN_RECENT or rb < MIN_PRIOR:
        return {"layer": layer, "name": LAYER_NAMES[layer], "program": program, "state": "thin",
                "text": f"Not enough history yet: read on {rb} of the {PRIOR_DAYS} days before this week."}
    want_r, want_p = set(recent), set(prior)
    dated = dated_events(mem, area, layer, want_r | want_p)
    a = sum(1 for _, d in dated if d in want_r)
    b = sum(1 for _, d in dated if d in want_p)
    state = count_move(a, b, ra, rb)
    usual = b / rb * RECENT_DAYS
    out = {"layer": layer, "name": LAYER_NAMES[layer], "program": program, "state": state, "recent": a,
           "usual_per_week": round(usual, 1),
           "test": {"kind": "count", "a": a, "b": b, "ra": ra, "rb": rb, "through": through.isoformat()},
           "text": f"{a} in the last 7 days, against about {usual:.1f} a week in the 14 days before."}
    if a == 0 and b == 0:
        out |= {"state": "quiet", "text": "None in the last 21 days."}
    if detail:
        listed = _listed(mem, area, layer, today, recent, index)
        if listed:
            out["items"] = listed
    return out


def read_press(mem, area, today, through):
    zone = area.get("press_zone")
    r = mem["subjects"].get(f"watchfloor:zone:{zone}") if zone else None
    label = f" ({area['press_label']})" if area.get("press_label") else ""
    base = {"layer": "press", "name": LAYER_NAMES["press"] + label, "program": "watchfloor"}
    if not r or not r.get("v"):
        return base | {"state": "thin", "text": "No press series for this area yet."}
    values = mem_tool.current_series(r)           # a change in what the series counts starts a new series
    recent = [values[d] for d in _days(through, RECENT_DAYS) if d in values]
    prior = [values[d] for d in _days(through - timedelta(days=RECENT_DAYS), PRIOR_DAYS) if d in values]
    if len(recent) < MIN_RECENT or len(prior) < MIN_PRIOR:
        restart = (f" What this series counts changed on {_when(min(values))}, so it starts again there."
                   if values and len(values) < len(r["v"]) else "")
        return base | {"state": "thin",
                       "text": f"Not enough history yet: {len(prior)} of the {PRIOR_DAYS} days before.{restart}"}
    a, b = statistics.median(recent), statistics.median(prior)
    q = statistics.quantiles(prior, n=4, method="inclusive")
    return base | {"state": level_move(recent, prior), "recent": a, "usual": b, "id": r["id"],
                   "test": {"kind": "level", "recent_median": a, "prior_median": b, "prior_q1": q[0], "prior_q3": q[2],
                            "ra": len(recent), "rb": len(prior), "through": through.isoformat()},
                   "text": f"A median of {a:g} articles a day, against {b:g} in the 14 days before."}


def read_traffic(snapshot, area):
    slug = area.get("chokepoint")
    base = {"layer": "traffic", "name": LAYER_NAMES["traffic"], "program": "wake"}
    p = None
    for f in snapshot.get("features") or []:
        props = f.get("properties") or {}
        if str(props.get("id")).startswith(f"wake:chokepoint:{slug}:") and not props.get("cop_stale"):
            p = props
            break
    m = (p or {}).get("measures") or {}
    pct, z, n = m.get("change_90d_pct"), m.get("z_90d"), m.get("n_total_7d")
    if p is None or n is None:
        return base | {"state": "thin", "text": "No current transit count."}
    lag = "A condition, not counted: PortWatch's counts lag by up to 16 days."
    if pct is None or z is None:
        # A count with no usable comparison (a strait whose 90-day baseline is zero or too short) is still a count.
        return base | {"state": "thin", "recent": n, "usual": m.get("baseline_90d_median"), "id": p["id"],
                       "text": f"{n:g} a day in the week to {_when(p['data_as_of'][:10])}. Its 90-day baseline is too "
                               f"thin to compare against. {lag}",
                       "label": "Condition: no baseline", "condition": True}
    # Fewer ships through a strait is the direction of concern here, so a fall reads as "up".
    state = "up" if pct <= -TRAFFIC_PCT and z <= -TRAFFIC_Z else "down" if pct >= TRAFFIC_PCT and z >= TRAFFIC_Z else "steady"
    side = ("level with" if round(abs(pct)) == 0 else f"{abs(pct):.0f}% below" if pct < 0 else f"{pct:.0f}% above")
    return base | {"state": state, "recent": n, "usual": m.get("baseline_90d_median"), "id": p["id"],
                   "text": f"{n:g} a day in the week to {_when(p['data_as_of'][:10])}, {side} its 90-day median of "
                           f"{m.get('baseline_90d_median'):g}. {lag}",
                   "label": {"up": "Condition: fewer ships", "down": "Condition: more ships"}.get(state, "Condition: steady"),
                   "condition": True}


def word_for(layers):
    moved = [x["state"] for x in layers if x["layer"] not in CONDITIONS]
    readable = [s for s in moved if s != "thin"]
    up, down = moved.count("up"), moved.count("down")
    if len(readable) < 2:
        if up:
            return "edging_up"
        if down:
            return "edging_down"
        return "building"
    if up >= 2 and up > down:
        return "rising"
    if down >= 2 and down > up:
        return "easing"
    if up and down:
        return "mixed"
    if up:
        return "edging_up"
    if down:
        return "edging_down"
    return "steady"


def read_area(mem, snapshot, area, names, today, index, shapes, detail=False):
    layers = []
    for layer in names:
        if layer == "traffic":
            if area.get("chokepoint"):
                layers.append(read_traffic(snapshot, area))
            continue
        if layer == "interference":
            layers.append(read_interference(mem, area, today, index, detail, shapes))
            continue
        program = LAYER_PROGRAM[layer]
        through = _through(mem, program, today)
        if through is None:
            layers.append({"layer": layer, "name": LAYER_NAMES[layer], "program": program, "state": "thin",
                           "text": "No current data."})
        elif layer == "press":
            layers.append(read_press(mem, area, today, through))
        else:
            layers.append(read_count(mem, area, layer, today, through, index, detail))
    for x in layers:
        if x["layer"] == "outages":      # the same measure sits on two cards, so each says what it covers
            x["name"] += " (worldwide)" if area.get("worldwide") else f" ({area['name']})"
    word = word_for(layers)
    return {"area": area["area"], "name": area["name"], "direction": word, "word": WORDS[word],
            "up": [x["name"] for x in layers if x["state"] == "up" and x["layer"] not in CONDITIONS],
            "down": [x["name"] for x in layers if x["state"] == "down" and x["layer"] not in CONDITIONS],
            "layers": layers}


def record(verdict):
    """Verdict's record as it stands, read only: its registered numbers, never one of COP's."""
    rows = ((verdict or {}).get("forecasts") or {}).get("data") or []
    rows = rows if isinstance(rows, list) else rows.get("items") or []
    done = [x for x in rows if x.get("status") == "resolved" and x.get("outcome") in (0, 1)]
    if not rows:
        return None
    called = [x for x in done if x.get("probability") != 0.5]
    right = [x for x in called if (x["probability"] > 0.5) == (x["outcome"] == 1)]
    latest = max(done, key=lambda x: (x.get("resolved_at") or "", x.get("id"))) if done else None
    scores = ((verdict.get("scores") or {}).get("data") or {}).get("by_program", {}).get("all", {})
    upcoming = sorted((x for x in rows if x.get("status") == "open" and x.get("check_after")),
                      key=lambda x: x["check_after"])
    out = {"registered": len(rows), "resolved": len(done), "on_called_side": len(right), "called": len(called),
           "independent": scores.get("n_independent"), "open": sum(1 for x in rows if x.get("status") == "open")}
    if latest:
        out["last"] = {"id": latest["id"], "title": latest.get("title"), "probability": latest.get("probability"),
                       "likelihood": latest.get("likelihood"), "outcome": latest["outcome"],
                       "resolved_at": latest.get("resolved_at")}
    if upcoming:
        out["next"] = {"id": upcoming[0]["id"], "title": upcoming[0].get("title"), "check_after": upcoming[0]["check_after"]}
    return out


def summary(board):
    """One sentence for the hub, from the words. The same press series in two areas is one voice, named once."""
    groups, zones = {}, set()
    for x in board:
        moved = [SHORT[l["layer"]] for l in x["layers"] if l["state"] in ("up", "down") and l["layer"] not in CONDITIONS]
        zone = x.get("press_zone")
        if moved == [SHORT["press"]] and zone in zones:
            continue
        if "press" in [l["layer"] for l in x["layers"] if l["state"] in ("up", "down")] and zone:
            zones.add(zone)
        if x["direction"] in ("rising", "edging_up", "mixed", "easing", "edging_down"):
            groups.setdefault(x["direction"], []).append(f"{x['name']} ({', '.join(moved)})")
    parts = [f"{WORDS[w]}: {'; '.join(groups[w])}." for w in ("rising", "edging_up", "mixed", "easing", "edging_down")
             if groups.get(w)]
    steady = sum(1 for x in board if x["direction"] == "steady")
    if steady:
        parts.append(f"{steady} other place{'s' if steady != 1 else ''} steady.")
    return " ".join(parts) or "No place has moved against its own last two weeks."


def log_directions(mem, doc):
    """Keep each day's word for each place, and say how long the current word has held."""
    log = mem.setdefault("directions", {})
    day = mem_tool.day_of(doc["generated_at"])
    for x in doc["problems"] + doc["areas"]:
        days = log.setdefault(x["area"], {})
        days[day] = x["direction"]
        ordered = sorted(days)
        since = day
        for d in reversed(ordered):
            if days[d] != x["direction"]:
                x["was"] = WORDS[days[d]]
                break
            since = d
        x["since"] = since


def backfill(mem, archive, config):
    """The words each archived day would have read, in order, so the first page already knows how long each has held."""
    rolling, held = mem_tool.empty(), (mem.get("events") or {}).get("noisefloor") or {}
    log = mem.setdefault("directions", {})
    for path in sorted(Path(archive).glob("????-??-??.geojson.gz")):
        snap = mem_tool._read(path)
        mem_tool.fold(rolling, snap)
        day = mem_tool.day_of(snap["generated_at"])
        rolling["events"]["noisefloor"] = {k: v for k, v in held.items() if k < day}
        doc = build(rolling, snap, config)
        for x in doc["problems"] + doc["areas"]:
            log.setdefault(x["area"], {})[day] = x["direction"]


def build(mem, snapshot, config, verdict=None):
    today = mem_tool.day_of(snapshot["generated_at"])
    index = mem_tool.similar_index(mem)
    shapes = load_shapes()
    problems = [read_area(mem, snapshot, a, a["layers"], today, index, shapes, detail=True) for a in config["problems"]]
    areas = [read_area(mem, snapshot, a, AREA_LAYERS, today, index, shapes) for a in config["areas"]]
    for x, a in zip(problems + areas, config["problems"] + config["areas"]):
        x["press_zone"] = a.get("press_zone")
    board = [x for x in problems if x["area"] != "shutdowns"] + areas
    return {"join": JOIN, "generated_at": snapshot["generated_at"], "what": WHAT, "not_a_warning": NOT_A_WARNING,
            "windows": {"recent_days": RECENT_DAYS, "prior_days": PRIOR_DAYS},
            "memory": {"since": min(mem["days"]), "days": len(mem["days"]), "subjects": len(mem["subjects"]),
                       "gone": sum(1 for r in mem["subjects"].values() if r["spans"][-1][1] < today)},
            "problems": problems, "areas": areas,
            "rising": [x["name"] for x in board if x["direction"] == "rising"],
            "easing": [x["name"] for x in board if x["direction"] == "easing"],
            "summary": summary(board),
            "record": record(verdict)}


RISING = ("rising",)   # the words that move a pick to the front; Edging up is named, never reordered


def pick_places(doc, snapshot, picks, config):
    """For each of today's picks, the places on the board it falls in and their word. A pick in a Rising place is
    listed first by top_picks and says so. Pick ids sit under "pick", not "id": this join cites none of them."""
    by_id = {(f.get("properties") or {}).get("id"): f for f in snapshot.get("features") or []}
    areas = {a["area"]: a for a in config["problems"] + config["areas"]}
    out = []
    for p in (picks or {}).get("picks") or []:
        f = by_id.get(p.get("id"))
        if f is None:
            continue
        props = f.get("properties") or {}
        r = {"c": props.get("countries") or [], "xy": mem_tool.place_point(f.get("geometry"))}
        places = []
        for x in doc["problems"] + doc["areas"]:
            area = areas.get(x["area"])
            if area is None or area.get("worldwide") or not in_area(str(p["id"]), r, area, None):
                continue
            if x["direction"] in ("building", "steady"):
                continue
            places.append({"area": x["area"], "name": x["name"], "direction": x["direction"], "word": x["word"],
                           "up": x["up"], "down": x["down"]})
        if places:
            out.append({"pick": p["id"], "places": places,
                        "rising": any(pl["direction"] in RISING for pl in places)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory", required=True, help="folder holding memory.json.gz")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--verdict", help="COP's copy of Verdict's files (site/data/agent/verdict.json)")
    ap.add_argument("--publish", required=True, help="folder to write direction.json in")
    ap.add_argument("--picks", help="today's picks (site/data/picks.json), to say which fall in a moving place")
    ap.add_argument("--archive", help="archived daily snapshots, read once to backfill the log of words")
    a = ap.parse_args(argv)
    mem = mem_tool.load(a.memory)
    if mem is None:
        raise SystemExit("direction: no memory to read")
    snapshot = json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    config = json.loads((ROOT / "config" / "areas.json").read_text(encoding="utf-8"))
    verdict = None
    if a.verdict and Path(a.verdict).exists():
        verdict = json.loads(Path(a.verdict).read_text(encoding="utf-8"))
    if a.archive and "directions" not in mem:
        backfill(mem, a.archive, config)
    doc = build(mem, snapshot, config, verdict)
    if a.picks and Path(a.picks).exists():
        doc["picks"] = pick_places(doc, snapshot, json.loads(Path(a.picks).read_text(encoding="utf-8")), config)
    log_directions(mem, doc)
    mem_tool.save(a.memory, mem)
    gate.write(JOIN, doc, a.publish, [PUBLIC_NAME])
    print(f"direction: rising {doc['rising'] or 'none'}; easing {doc['easing'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
