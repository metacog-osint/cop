"""Base rates for outage triage, from COP's archived daily snapshots.

Three things, from replaying tools/triage.py over the archive (GitHub releases snapshots-YYYY-MM, the last snapshot of
each UTC day):

1. History: each travel advisory's level on each date it was issued, and each country's share of days with raised
   press attention. triage.py needs both: to tell a raised advisory from a lowered one, and to count raised attention
   only where it is unusual for that country.
2. Replay: what reading did each real outage get? One row per outage (the newest day that saw it), counted by reading.
3. Background: on an ordinary day, in how many countries would an outage have found a new hazard, a fresh notice or
   unusual attention anyway? A reading that fires for a third of the world says little; this is the number to hold
   each reading against. Every country in the kit's list, every archived day, at that snapshot's own time.

    gh release download snapshots-2026-11 --repo metacog-osint/cop --dir out/archive --pattern "*.geojson.gz"
    python tools/triage_rates.py --archive out/archive      # writes out/triage/base-rates.json

RERUN about 28 Nov 2026, when the archive holds 28 days, and look at triage.py's provisional windows then.

NOT PUBLISHED: every write goes through tools/gate.py (out/ only while the join is not switched on). Standard-library
Python only.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage as tri  # noqa: E402

THIN_DAYS = 28        # fewer archived days than this: say the rates are thin
THIN_OUTAGES = 20     # fewer real outages than this: the replay counts are anecdotes, not rates
# Cordon began carrying embassy alerts on 8 Nov 2026. Days before that had a different notice signal, so the notice
# count and the readings are measured only from this day on. Hazards and attention use every day.
NOTICE_VALID_FROM = "2026-11-08"


def all_countries():
    data = json.loads(tri.COUNTRIES.read_text(encoding="utf-8"))
    return sorted(c["cc"] for c in data["countries"])


def _features(snapshot):
    return [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]


def history(snapshots):
    """What triage.py needs from the past: advisory levels by date, and how usual raised attention is per country."""
    levels, raised_days, days = {}, Counter(), 0
    for snap in snapshots:
        features = _features(snap)
        raised = set()
        for f in features:
            p = f["properties"]
            if p.get("cop_program") == "cordon" and tri._kind(p.get("id")) == "advisory" and p.get("data_as_of"):
                m = p.get("measures") if isinstance(p.get("measures"), dict) else {}
                levels.setdefault(p["id"], {})[p["data_as_of"]] = m.get("level")
            elif p.get("cop_program") == "watchfloor" and p.get("tier") in ("anomaly", "warning"):
                raised.update(p.get("countries") or [])
        if tri.layer_state(snap, features, "watchfloor") not in ("stale", "missing"):
            days += 1
            raised_days.update(raised)
    return {"advisory_levels": {k: [[d, lv] for d, lv in sorted(v.items())] for k, v in sorted(levels.items())},
            "attention_days": days,
            "attention_share": {cc: round(n / days, 4) for cc, n in sorted(raised_days.items())} if days else {}}


def background_day(snapshot, countries, hist=None):
    """For one snapshot: which countries would show each signal to an outage at the snapshot's own time."""
    at = tri.utc(snapshot.get("generated_at"))
    features = _features(snapshot)
    states = {pid: tri.layer_state(snapshot, features, pid) for pid in tri.LAYERS}
    if not at or any(s in ("stale", "missing") for s in states.values()):
        return None  # a day with a layer down measures nothing: leave it out, and say so
    levels, shares = (hist or {}).get("advisory_levels"), (hist or {}).get("attention_share")
    # Only items that could change a reading: this keeps the distance work small.
    haz = [f for f in features if f["properties"].get("cop_program") == "elements"
           and f["properties"].get("tier") in ("anomaly", "warning")
           and tri._kind(f["properties"].get("id")) not in tri.BACKGROUND]
    other = [f for f in features if f["properties"].get("cop_program") in ("cordon", "watchfloor")]
    hazard, standing, notice, raised, unusual, notice_kinds = set(), set(), set(), set(), set(), Counter()
    for cc in countries:
        h = tri.hazards(haz, cc, at)
        if h["significant"]:
            hazard.add(cc)
        elif h["standing"]:
            standing.add(cc)
        recent = tri.notices(other, cc, at, levels)["recent"]
        if recent:
            notice.add(cc)
            notice_kinds.update({r["kind"] for r in recent})
        a = tri.attention(other, cc, at, shares)
        if a["raised"]:
            raised.add(cc)
        if a["unusual"]:
            unusual.add(cc)
    n = len(countries)
    day = snapshot["generated_at"][:10]
    natural = hazard
    deliberate = (notice | unusual) - hazard
    return {"day": day, "snapshot": snapshot["generated_at"], "countries": n,
            "readings_counted": day >= NOTICE_VALID_FROM,
            "hazard": len(hazard), "standing_hazard_only": len(standing), "recent_notice": len(notice),
            "raised_attention": len(raised), "unusual_attention": len(unusual),
            "would_read": {"natural": len(natural), "deliberate": len(deliberate),
                           "open": n - len(natural) - len(deliberate)},
            "recent_notice_kinds": dict(sorted(notice_kinds.items())),
            "_natural": natural, "_deliberate": deliberate}


def _share(part, whole):
    return round(part / whole, 4) if whole else None


def rates(snapshots):
    """snapshots: iterable of snapshot dicts, any order."""
    snapshots = sorted(snapshots, key=lambda s: s.get("generated_at") or "")
    countries = all_countries()
    hist = history(snapshots)
    outages, days, skipped = {}, [], []
    for snap in snapshots:
        for r in tri.triage(snap, hist)["records"]:
            outages[r["outage"]["id"]] = {"outage": r["outage"]["id"], "country": r["country"],
                                          "reading": r["reading"], "seen_in": snap["generated_at"]}
        row = background_day(snap, countries, hist)
        (days.append(row) if row else skipped.append((snap.get("generated_at") or "?")[:10]))
    counted = [d for d in days if d["readings_counted"]]
    # A world rate hides where the signals live: a country under standing alerts reads "deliberate" every day.
    per = {}
    for d in days:
        for key in ("natural", "deliberate"):
            for cc in d.pop("_" + key):
                if d["readings_counted"]:
                    per.setdefault(cc, {"natural": 0, "deliberate": 0})[key] += 1
    by_country = {cc: {k: _share(v, len(counted)) for k, v in sorted(c.items())} |
                      {"open": _share(len(counted) - c["natural"] - c["deliberate"], len(counted))}
                  for cc, c in sorted(per.items())}
    cd = sum(d["countries"] for d in days)
    rcd = sum(d["countries"] for d in counted)
    total = {k: sum(d["would_read"][k] for d in counted) for k in ("natural", "deliberate", "open")}
    by_reading = Counter(o["reading"] for o in outages.values())
    cautions = []
    if len(counted) < THIN_DAYS:
        cautions.append(f"Only {len(counted)} archived days count toward the readings (from {NOTICE_VALID_FROM}, when "
                        "Cordon's notices took their present form): a first look, not a settled rate.")
    if len(outages) < THIN_OUTAGES:
        cautions.append(f"Only {len(outages)} real outages in the archive: the replay counts are examples, not rates.")
    kinds_first = {k: next(d["day"] for d in days if k in d["recent_notice_kinds"])
                   for k in sorted({k for d in days for k in d["recent_notice_kinds"]})}
    return {
        "join": tri.JOIN,
        "archive": {"days": len(snapshots), "first": snapshots[0]["generated_at"] if snapshots else None,
                    "last": snapshots[-1]["generated_at"] if snapshots else None,
                    "days_left_out_layer_down": skipped,
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
            "by_country_days": len(counted),
            "by_country": by_country,
            "by_country_note": "Share of counted days each country would have read natural or deliberate. A "
                               "country not listed read open on every day.",
            "days": days,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=str(tri.ROOT / "out" / "archive"), help="folder of YYYY-MM-DD.geojson.gz")
    ap.add_argument("--out", default=str(tri.ROOT / "out" / "triage"))
    a = ap.parse_args(argv)
    out = tri.safe_out(a.out)
    files = sorted(Path(a.archive).glob("*.geojson.gz"))
    if not files:
        raise SystemExit(f"no *.geojson.gz in {a.archive}")
    doc = rates(tri.load(f) for f in files)
    tri.gate.write(tri.JOIN, doc, out, ("base-rates.json",))
    b, arc = doc["background"], doc["archive"]
    print(f"{arc['days']} days, {arc['first']} to {arc['last']}; left out (a layer down): "
          f"{arc['days_left_out_layer_down'] or 'none'}; readings counted on {arc['days_counted_for_readings']} days "
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
    often = sorted(cc for cc, c in b["by_country"].items() if c["deliberate"] >= 0.75)
    nat = sorted(cc for cc, c in b["by_country"].items() if c["natural"] >= 0.75)
    print(f"  {len(b['by_country'])} countries ever read natural or deliberate; deliberate on 3 days in 4 or more: "
          f"{', '.join(often) or 'none'}; natural on 3 days in 4 or more: {', '.join(nat) or 'none'}")
    for c in doc["cautions"]:
        print("caution: " + c)
    print(f"wrote {out / 'base-rates.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
