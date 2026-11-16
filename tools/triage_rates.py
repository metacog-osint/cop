"""Base rates for outage triage, from COP's archived daily snapshots.

Three things, from replaying tools/triage.py over the archive (GitHub releases snapshots-YYYY-MM, the last snapshot of
each UTC day):

1. History: each travel advisory's level on each date it was issued (State's; Canada's, the UK FCDO's and New
   Zealand SafeTravel's as triage.notice_level ranks them, from 14 Nov 2026), each volcano's alert level on each day USGS
   sent it, and each embassy alert's date in each snapshot that carried it. triage.py needs all three: to tell a
   raised advisory or volcano alert from a lowered one or a re-send, and a fresh embassy alert from an edited one
   (alerts are re-dated when edited). Unusual attention needs no history here: it is Watchfloor's own 28-day z-score,
   carried on each day's items, so the replay reads it as it stood that day (D8, 15 Nov 2026).
2. Replay: what reading did each real outage get? One row per outage (the newest day that saw it), counted by reading.
3. Background: on an ordinary day, in how many countries would an outage have found a new hazard, a fresh notice or
   unusual attention anyway? A reading that fires for a third of the world says little; this is the number to hold
   each reading against. Every country in the kit's list, every archived day, at that snapshot's own time.
4. State background: the same for US state power outages (Blackout's BO-IND-14), over every state in the county
   table: on how many state-days would one have read natural anyway.

    gh release download snapshots-2026-11 --repo metacog-osint/cop --dir out/archive --pattern "*.geojson.gz"
    python tools/triage_rates.py --archive out/archive      # writes out/triage/base-rates.json

RERUN about 28 Nov 2026, when the archive holds 28 days, and look at triage.py's provisional windows then.

Writes to out/ only (the whole history stays there); triage.py copies the few numbers it shows into the public
document. Every write goes through tools/gate.py. Standard-library Python only.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage as tri  # noqa: E402

THIN_DAYS = 28        # fewer archived days than this: say the rates are thin
THIN_OUTAGES = 20     # fewer real outages than this: the replay counts are anecdotes, not rates
# Cordon began carrying embassy alerts on 8 Nov 2026. Days before that had a different notice signal, so the notice
# count and the readings are measured only from this day on. Hazards and attention use every day.
NOTICE_VALID_FROM = "2026-11-08"
# No outage of known cause has been replayed yet (the STOP replay, cop-purpose sequence step 4), so the join has a
# false-alarm side and no hit rate. Said beside every rate until a replay measures one.
HIT_RATE_CAUTION = ("No hit rate yet: no outage of known cause has been replayed, so it has not been shown that a "
                    "reading tells one kind of outage from another. These rates say only how often each reading "
                    "happens anyway.")


def all_countries():
    data = json.loads(tri.COUNTRIES.read_text(encoding="utf-8"))
    return sorted(c["cc"] for c in data["countries"])


def _features(snapshot):
    return [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]


def _with_ended(snapshot, features):
    """The day's live items plus Elements' items that had ended by that snapshot, when the archive kept that day's
    ended list (<day>.elements-ended.json.gz, from 14 Nov 2026); the live items alone before that."""
    return tri.with_ended(features, snapshot.get("cop_ended"), tri.utc(snapshot.get("generated_at")))[0]


def ended_list_kind(snapshot):
    """served (the list Elements served that day), rebuilt (from Elements' history log by rebuild_ended.py), short
    (rebuilt from a log younger than the hazard window at that time) or none."""
    e = snapshot.get("cop_ended")
    if not isinstance(e, dict):
        return "none"
    r = e.get("rebuilt")
    if not isinstance(r, dict):
        return "served"
    start, at = tri.utc(r.get("log_from")), tri.utc(snapshot.get("generated_at"))
    return "short" if start and at and at - start < tri.timedelta(hours=tri.HAZARD_BEFORE_H) else "rebuilt"


def _list_cautions(rows, label, log_from=None):
    kinds = [r["ended_list"] for r in rows]
    out = []
    if kinds.count("none"):
        return out
    rebuilt = kinds.count("rebuilt") + kinds.count("short")
    if rebuilt:
        out.append(f"For {rebuilt} of the {len(rows)} {label} the list of Elements' ended hazards was rebuilt from "
                   "Elements' history log (it was not served before 14 Nov 2026)"
                   + (f"; the log starts {log_from}." if log_from else "."))
    if kinds.count("short"):
        out.append(f"On {kinds.count('short')} of them the log was younger than the {tri.HAZARD_BEFORE_H}-hour hazard "
                   "window, so a hazard that ended before the log began is missed and those days run low.")
    return out


def attach_ended(snapshot, path):
    """Put the archived ended list for a snapshot's day on it (cop_ended), when the file exists."""
    ended = Path(str(path).replace(".geojson.gz", ".elements-ended.json.gz"))
    if ended.exists():
        snapshot["cop_ended"] = tri.load(ended)
    return snapshot


def history(snapshots):
    """What triage.py needs from the past: advisory and volcano alert levels by date, and each embassy alert's dates
    with the snapshot that carried each (triage.alert_first_dated reads only snapshots taken by the outage's time)."""
    levels, volcanoes, alerts = {}, {}, {}
    for snap in snapshots:
        features = _features(snap)
        for f in features:
            p = f["properties"]
            if (p.get("cop_program") == "cordon" and tri._kind(p.get("id")) in tri.LEVELLED_KINDS
                    and p.get("data_as_of")):
                levels.setdefault(p["id"], {})[p["data_as_of"]] = tri.notice_level(p)
            elif (p.get("cop_program") == "elements" and tri._kind(p.get("id")) == "usgs-volcano"
                  and p.get("data_as_of")):
                volcanoes.setdefault(p["id"], {})[p["data_as_of"]] = tri.volcano_level(p)
            if (p.get("cop_program") == "cordon" and tri._kind(p.get("id")) == "alert" and p.get("data_as_of")
                    and snap.get("generated_at")):
                alerts.setdefault(p["id"], set()).add((snap["generated_at"], p["data_as_of"]))
    return {"advisory_levels": {k: [[d, lv] for d, lv in sorted(v.items())] for k, v in sorted(levels.items())},
            "volcano_levels": {k: [[d, lv] for d, lv in sorted(v.items())] for k, v in sorted(volcanoes.items())},
            "alert_dates": {k: [list(r) for r in sorted(v)] for k, v in sorted(alerts.items())}}


def background_day(snapshot, countries, hist=None):
    """For one snapshot: which countries would show each signal to an outage at the snapshot's own time."""
    at = tri.utc(snapshot.get("generated_at"))
    features = _features(snapshot)
    states = {pid: tri.layer_state(snapshot, features, pid) for pid in tri.LAYERS}
    if not at or any(s in ("stale", "missing") for s in states.values()):
        return None  # a day with a layer down measures nothing: leave it out, and say so
    levels, alert_dates = (hist or {}).get("advisory_levels"), (hist or {}).get("alert_dates")
    volcanoes = (hist or {}).get("volcano_levels")
    # Only items that could change a reading: this keeps the distance work small.
    haz = [f for f in _with_ended(snapshot, features) if f["properties"].get("cop_program") == "elements"
           and f["properties"].get("tier") in ("anomaly", "warning")
           and tri._kind(f["properties"].get("id")) not in tri.BACKGROUND]
    other = [f for f in features if f["properties"].get("cop_program") in ("cordon", "watchfloor")]
    hazard, standing, notice, raised, unusual, notice_kinds = set(), set(), set(), set(), set(), Counter()
    events = {}   # what made each country's signal: the distinct items behind it, so one notice is one event
    for cc in countries:
        h = tri.hazards(haz, cc, at, volcanoes)
        if h["significant"]:
            hazard.add(cc)
            events[cc] = {"hazards": {r["id"] for r in h["significant"]}}
        elif h["standing"]:
            standing.add(cc)
        recent = tri.notices(other, cc, at, levels, alert_dates)["recent"]
        if recent:
            notice.add(cc)
            notice_kinds.update({r["kind"] for r in recent})
        a = tri.attention(other, cc, at)
        if a["raised"]:
            raised.add(cc)
        if a["unusual"]:
            unusual.add(cc)
        if cc not in hazard and (recent or a["unusual"]):
            events[cc] = {"notices": {r["id"] for r in recent}, "unusual": a["unusual"]}
    n = len(countries)
    day = snapshot["generated_at"][:10]
    natural = hazard
    tension = (notice | unusual) - hazard
    return {"day": day, "snapshot": snapshot["generated_at"], "countries": n,
            "readings_counted": day >= NOTICE_VALID_FROM, "ended_list": ended_list_kind(snapshot),
            "hazard": len(hazard), "standing_hazard_only": len(standing), "recent_notice": len(notice),
            "raised_attention": len(raised), "unusual_attention": len(unusual),
            "would_read": {"natural": len(natural), tri.TENSION: len(tension),
                           "open": n - len(natural) - len(tension)},
            "recent_notice_kinds": dict(sorted(notice_kinds.items())),
            "_natural": natural, "_tension": tension, "_events": events}


def state_background_day(snapshot, hist=None):
    """For one snapshot: which US states would a state power outage at the snapshot's own time read natural in.
    Only Elements decides that reading, so only a day with Elements down is left out."""
    at = tri.utc(snapshot.get("generated_at"))
    features = _features(snapshot)
    if not at or tri.layer_state(snapshot, features, "elements") in ("stale", "missing"):
        return None
    # Only items that could make a natural reading: significant, or a warning for a hazard that takes lines down.
    haz = [f for f in _with_ended(snapshot, features) if f["properties"].get("cop_program") == "elements"
           and tri._kind(f["properties"].get("id")) not in tri.BACKGROUND
           and (f["properties"].get("tier") in ("anomaly", "warning") or tri._power_warning(f["properties"]))]
    volcanoes = (hist or {}).get("volcano_levels")
    states = sorted(tri.us_states())
    natural, events = set(), {}
    for st in states:
        sig = tri.state_hazards(haz, st, at, None, volcanoes)["significant"]
        if sig:
            natural.add(st)
            events[st] = {r["id"] for r in sig}
    return {"day": snapshot["generated_at"][:10], "snapshot": snapshot["generated_at"], "states": len(states),
            "ended_list": ended_list_kind(snapshot), "natural": len(natural), "natural_states": sorted(natural),
            "_events": events}


def _share(part, whole):
    return round(part / whole, 4) if whole else None


def _country_events(days, counted):
    """Per country, over the counted days: how many days read natural or tension context, and the distinct events
    behind them (Elements hazards; Cordon notices; runs of consecutive days with unusual attention). A notice that
    counts for three days is one notice; a regional alert is one notice in each country it names."""
    by_day = {id(d): d.pop("_events") for d in days}
    out = {}
    for i, d in enumerate(counted):
        for cc, e in by_day[id(d)].items():
            c = out.setdefault(cc, {"natural": {"days": 0, "hazards": set()},
                                    tri.TENSION: {"days": 0, "notices": set(), "attention_runs": 0, "_last": None}})
            if "hazards" in e:
                c["natural"]["days"] += 1
                c["natural"]["hazards"] |= e["hazards"]
                continue
            t = c[tri.TENSION]
            t["days"] += 1
            t["notices"] |= e["notices"]
            if e["unusual"]:
                if t["_last"] != i - 1:
                    t["attention_runs"] += 1
                t["_last"] = i
    return {cc: {"natural": {"days": c["natural"]["days"], "hazards": len(c["natural"]["hazards"])},
                 tri.TENSION: {"days": c[tri.TENSION]["days"], "notices": len(c[tri.TENSION]["notices"]),
                               "attention_runs": c[tri.TENSION]["attention_runs"]}}
            for cc, c in sorted(out.items())}


def rates(snapshots, today=None):
    """snapshots: iterable of snapshot dicts, any order. today: the build's UTC date (YYYY-MM-DD); a snapshot taken
    on it is today's partial day so far, so it is left out of the background (it still feeds the history and the
    replay). None leaves nothing out."""
    snapshots = sorted(snapshots, key=lambda s: s.get("generated_at") or "")
    countries = all_countries()
    hist = history(snapshots)
    outages, days, skipped, sdays, sskipped, partial = {}, [], [], [], [], []
    for snap in snapshots:
        for r in tri.triage(snap, hist, snap.get("cop_ended"))["records"]:
            outages[(r["outage"]["id"], r["country"])] = {
                "outage": r["outage"]["id"], "country": r["country"], "us_state": r.get("us_state"),
                "source": r["outage"].get("source"), "started_at": r["outage"].get("started_at"),
                "reading": r["reading"], "seen_in": snap["generated_at"]}
        if today and (snap.get("generated_at") or "")[:10] >= today:
            partial.append(snap["generated_at"][:10])
            continue
        row = background_day(snap, countries, hist)
        (days.append(row) if row else skipped.append((snap.get("generated_at") or "?")[:10]))
        srow = state_background_day(snap, hist)
        (sdays.append(srow) if srow else sskipped.append((snap.get("generated_at") or "?")[:10]))
    counted = [d for d in days if d["readings_counted"]]
    # A world rate hides where the signals live: a country under standing alerts reads "tension context" every day.
    per = {}
    for d in days:
        for key, label in (("natural", "natural"), ("tension", tri.TENSION)):
            for cc in d.pop("_" + key):
                if d["readings_counted"]:
                    per.setdefault(cc, {"natural": 0, tri.TENSION: 0})[label] += 1
    by_country = {cc: {k: _share(v, len(counted)) for k, v in sorted(c.items())} |
                      {"open": _share(len(counted) - c["natural"] - c[tri.TENSION], len(counted))}
                  for cc, c in sorted(per.items())}
    events = _country_events(days, counted)
    # The pooled rate mostly counts countries that never show a signal; the countries that did are the population
    # a reading should be held against.
    sig_days = len(events) * len(counted)
    sig_nat = sum(e["natural"]["days"] for e in events.values())
    sig_ten = sum(e[tri.TENSION]["days"] for e in events.values())
    cd = sum(d["countries"] for d in days)
    rcd = sum(d["countries"] for d in counted)
    total = {k: sum(d["would_read"][k] for d in counted) for k in ("natural", tri.TENSION, "open")}
    by_reading = Counter(o["reading"] for o in outages.values())
    cautions = []
    if len(counted) < THIN_DAYS:
        cautions.append(f"Only {len(counted)} archived days count toward the readings (from {NOTICE_VALID_FROM}, when "
                        "Cordon's notices took their present form): a first look, not a settled rate.")
    if len(outages) < THIN_OUTAGES:
        cautions.append(f"Only {len(outages)} real outages in the archive: the replay counts are examples, not rates.")
    no_list = sum(1 for d in counted if d["ended_list"] == "none")
    if no_list:
        cautions.append(f"{no_list} of the {len(counted)} counted days have no list of Elements' ended hazards (archived "
                        "from 14 Nov 2026), so they miss a hazard that ended before that day's one snapshot: the natural "
                        "rate is low.")
    log_from = min((r.get("log_from") for snap in snapshots
                    for r in [(snap.get("cop_ended") or {}).get("rebuilt")] if isinstance(r, dict) and r.get("log_from")),
                   default=None)
    cautions += _list_cautions(counted, "counted days", log_from)
    cautions.append(HIT_RATE_CAUTION)
    # A notice kind Cordon added after NOTICE_VALID_FROM could not count on the days before it existed.
    present = {}
    for snap in snapshots:
        for f in _features(snap):
            p = f["properties"]
            k = tri._kind(p.get("id"))
            if p.get("cop_program") == "cordon" and k in tri.NOTICE_KINDS and k not in present:
                present[k] = (snap.get("generated_at") or "?")[:10]
    start = max(NOTICE_VALID_FROM, (snapshots[0].get("generated_at") or "")[:10]) if snapshots else NOTICE_VALID_FROM
    late = sorted((d, k) for k, d in present.items() if d > start)
    if late:
        cautions.append("Cordon kinds added after " + NOTICE_VALID_FROM + ": "
                        + ", ".join(f"{k} from {d}" for d, k in late)
                        + ". Days before those could not count them, so the notice rate rests on fewer days for "
                          "them.")
    kinds_first = {k: next(d["day"] for d in days if k in d["recent_notice_kinds"])
                   for k in sorted({k for d in days for k in d["recent_notice_kinds"]})}
    # US states: how often a state power outage would have read natural whatever its cause.
    sd = sum(d["states"] for d in sdays)
    snat = sum(d["natural"] for d in sdays)
    per_state = Counter(st for d in sdays for st in d["natural_states"])
    state_events = {}
    for d in sdays:
        for st, ids in d.pop("_events").items():
            state_events.setdefault(st, set()).update(ids)
    state_events = {st: {"days": per_state[st], "hazards": len(ids)} for st, ids in sorted(state_events.items())}
    s_no_list = sum(1 for d in sdays if d["ended_list"] == "none")
    scautions = [f"{s_no_list} of {len(sdays)} days have no list of Elements' ended hazards (archived from 14 Nov 2026): "
                 "on those days a warning that ran out before the day's one snapshot is missed, so most thunderstorm "
                 "and tornado warnings (about an hour long) are, and the real rate is higher. Measured 1-14 Nov 2026: "
                 "a power-line warning had begun in the 72-hour window in 12.6% of state-times, against 3.7% read "
                 "from the live feed alone."] if s_no_list else []
    scautions += _list_cautions(sdays, "days", log_from)
    if len(sdays) < THIN_DAYS:
        scautions.insert(0, f"Only {len(sdays)} archived days: a first look, not a settled rate.")
    scautions.append(HIT_RATE_CAUTION)
    return {
        "join": tri.JOIN,
        "archive": {"days": len(snapshots), "first": snapshots[0]["generated_at"] if snapshots else None,
                    "last": snapshots[-1]["generated_at"] if snapshots else None,
                    "days_left_out_layer_down": skipped,
                    "days_left_out_partial": partial,
                    "readings_counted_from": NOTICE_VALID_FROM, "days_counted_for_readings": len(counted)},
        "cautions": cautions,
        "history": hist,
        "replay": {"outages": len(outages), "by_reading": dict(sorted(by_reading.items())),
                   "rows": sorted(outages.values(), key=lambda o: o["outage"])},
        "background": {
            "what": "Share of country-days on which an outage would have met each signal anyway. Hazards and "
                    f"attention over every day; notices and the readings over days from {NOTICE_VALID_FROM}.",
            "country_days": cd,
            "would_read_country_days": rcd,
            "hazard": _share(sum(d["hazard"] for d in days), cd),
            "standing_hazard_only": _share(sum(d["standing_hazard_only"] for d in days), cd),
            "raised_attention": _share(sum(d["raised_attention"] for d in days), cd),
            "unusual_attention": _share(sum(d["unusual_attention"] for d in days), cd),
            "recent_notice": _share(sum(d["recent_notice"] for d in counted), rcd),
            "would_read": {k: _share(v, rcd) for k, v in total.items()},
            "notice_kinds_first_seen": kinds_first,
            "notice_kinds_in_archive_from": dict(sorted(present.items())),
            "by_country_days": len(counted),
            "by_country": by_country,
            "by_country_note": "Share of counted days each country would have read natural or tension context. A "
                               "country not listed read open on every day.",
            "countries": len(countries),
            "countries_with_signal": len(events),
            "signal_countries_would_read": {"natural": _share(sig_nat, sig_days), tri.TENSION: _share(sig_ten, sig_days),
                                            "open": _share(sig_days - sig_nat - sig_ten, sig_days)},
            "by_country_events": events,
            "by_country_events_note": "Per country, over the counted days: the days that read natural or tension "
                                      "context and the distinct events behind them (Elements hazards; Cordon "
                                      "notices; runs of consecutive days with unusual attention).",
            "days": days,
        },
        "state_background": {
            "what": "Share of US state-days on which a state power outage would have met a hazard that takes power "
                    "lines down, in or near the state, anyway: the natural reading. Cordon and Watchfloor don't "
                    "cover US states, so the rest read open.",
            "state_days": sd,
            "days_left_out_elements_down": sskipped,
            "would_read": {"natural": _share(snat, sd), "open": _share(sd - snat, sd)},
            "by_state_days": len(sdays),
            "by_state": {st: {"natural": _share(n, len(sdays)), "open": _share(len(sdays) - n, len(sdays))}
                         for st, n in sorted(per_state.items())},
            "by_state_note": "Share of archived days each state would have read natural. A state not listed read "
                             "open on every day.",
            "states": sdays[0]["states"] if sdays else len(tri.us_states()),
            "states_with_signal": len(state_events),
            "by_state_events": state_events,
            "cautions": scautions,
            "days": sdays,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=str(tri.ROOT / "out" / "archive"), help="folder of YYYY-MM-DD.geojson.gz")
    ap.add_argument("--out", default=str(tri.ROOT / "out" / "triage"))
    ap.add_argument("--today", default=None,
                    help="the build's UTC date, YYYY-MM-DD (default: now); its partial day is left out")
    a = ap.parse_args(argv)
    today = a.today or datetime.now(timezone.utc).date().isoformat()
    out = tri.safe_out(a.out)
    files = sorted(f for f in Path(a.archive).glob("*.geojson.gz") if f.name.count(".") == 2)
    if not files:
        raise SystemExit(f"no *.geojson.gz in {a.archive}")
    doc = rates((attach_ended(tri.load(f), f) for f in files), today)
    tri.gate.write(tri.JOIN, doc, out, ("base-rates.json",))
    b, arc = doc["background"], doc["archive"]
    print(f"{arc['days']} days, {arc['first']} to {arc['last']}; left out (a layer down): "
          f"{arc['days_left_out_layer_down'] or 'none'}; left out (today's partial day): "
          f"{arc['days_left_out_partial'] or 'none'}; readings counted on {arc['days_counted_for_readings']} days "
          f"from {arc['readings_counted_from']}")
    print(f"replay: {doc['replay']['outages']} outages {doc['replay']['by_reading']}")
    print(f"background over {b['country_days']} country-days: new hazard {b['hazard']:.1%} (standing hazard only "
          f"{b['standing_hazard_only']:.1%}), raised attention {b['raised_attention']:.1%} (unusual "
          f"{b['unusual_attention']:.1%})")
    if b["would_read_country_days"]:
        print(f"over {b['would_read_country_days']} counted country-days: fresh notice {b['recent_notice']:.1%}; "
              "an outage would read: " + ", ".join(f"{k} {v:.1%}" for k, v in b["would_read"].items()))
    for d in b["days"]:
        print(f"  {d['day']}{' ' if d['readings_counted'] else '*'} hazard {d['hazard']:>3} (standing "
              f"{d['standing_hazard_only']:>3})  notice {d['recent_notice']:>3}  attention {d['raised_attention']:>3} "
              f"(unusual {d['unusual_attention']:>3})  kinds {d['recent_notice_kinds']}")
    print("  * not counted toward notices or readings")
    often = sorted(cc for cc, c in b["by_country"].items() if c[tri.TENSION] >= 0.75)
    nat = sorted(cc for cc, c in b["by_country"].items() if c["natural"] >= 0.75)
    print(f"  {len(b['by_country'])} countries ever read natural or tension context; tension context on 3 days in 4 "
          "or more: "
          f"{', '.join(often) or 'none'}; natural on 3 days in 4 or more: {', '.join(nat) or 'none'}")
    s = doc["state_background"]
    if s["state_days"]:
        print(f"US states over {s['state_days']} state-days ({s['by_state_days']} days): a state power outage would "
              f"read natural {s['would_read']['natural']:.1%}, open {s['would_read']['open']:.1%}")
        for d in s["days"]:
            print(f"  {d['day']}  natural in {d['natural']:>2}: {', '.join(d['natural_states']) or '-'}")
        for c in s["cautions"]:
            print("state caution: " + c)
    for c in doc["cautions"]:
        print("caution: " + c)
    print(f"wrote {out / 'base-rates.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
