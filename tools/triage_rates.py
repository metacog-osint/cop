"""Base rates for outage triage, from COP's archived daily snapshots.

Two questions, answered by replaying tools/triage.py over the archive (GitHub releases snapshots-YYYY-MM, the last
snapshot of each UTC day):

1. Replay: what reading did each real outage get? One row per outage (the newest day that saw it), counted by reading.
2. Background: on an ordinary day, in how many countries would an outage have found a significant hazard, a recent
   notice or raised attention anyway? A reading that fires for a third of the world says little; this is the number
   to hold each reading against. Every country in the kit's list, every archived day, at that snapshot's own time.

    gh release download snapshots-2026-11 --repo metacog-osint/cop --dir out/archive --pattern "*.geojson.gz"
    python tools/triage_rates.py --archive out/archive      # writes out/triage/base-rates.json

NOT PUBLISHED: same rule as triage.py (out/ only, never site/). Standard-library Python only.
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


def all_countries():
    data = json.loads(tri.COUNTRIES.read_text(encoding="utf-8"))
    return sorted(c["cc"] for c in data["countries"])


def background_day(snapshot, countries):
    """For one snapshot: which countries would show each signal to an outage at the snapshot's own time."""
    at = tri.utc(snapshot.get("generated_at"))
    features = [f for f in snapshot.get("features", []) if isinstance(f.get("properties"), dict)]
    states = {pid: tri.layer_state(snapshot, features, pid) for pid in tri.LAYERS}
    if not at or any(s in ("stale", "missing") for s in states.values()):
        return None  # a day with a layer down measures nothing: leave it out, and say so
    # Only items that could change a reading: this keeps the distance work small.
    haz = [f for f in features if f["properties"].get("cop_program") == "elements"
           and f["properties"].get("tier") in ("anomaly", "warning")
           and tri._kind(f["properties"].get("id")) not in tri.BACKGROUND]
    other = [f for f in features if f["properties"].get("cop_program") in ("cordon", "watchfloor")]
    hazard, notice, raised, notice_kinds = set(), set(), set(), Counter()
    for cc in countries:
        if tri.hazards(haz, cc, at)["significant"]:
            hazard.add(cc)
        recent = tri.notices(other, cc, at)["recent"]
        if recent:
            notice.add(cc)
            notice_kinds.update({r["kind"] for r in recent})
        if tri.attention(other, cc, at)["raised"]:
            raised.add(cc)
    n = len(countries)
    natural = hazard
    deliberate = (notice | raised) - hazard
    return {"day": snapshot["generated_at"][:10], "snapshot": snapshot["generated_at"], "countries": n,
            "hazard": len(hazard), "recent_notice": len(notice), "raised_attention": len(raised),
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
    outages, days, skipped = {}, [], []
    for snap in snapshots:
        for r in tri.triage(snap)["records"]:
            outages[r["outage"]["id"]] = {"outage": r["outage"]["id"], "country": r["country"],
                                          "reading": r["reading"], "seen_in": snap["generated_at"]}
        row = background_day(snap, countries)
        (days.append(row) if row else skipped.append((snap.get("generated_at") or "?")[:10]))
    # A world rate hides where the signals live: a country under standing alerts reads "deliberate" every day.
    per = {}
    for d in days:
        for key in ("natural", "deliberate"):
            for cc in d.pop("_" + key):
                per.setdefault(cc, {"natural": 0, "deliberate": 0})[key] += 1
    by_country = {cc: {k: _share(v, len(days)) for k, v in sorted(c.items())} |
                      {"open": _share(len(days) - c["natural"] - c["deliberate"], len(days))}
                  for cc, c in sorted(per.items())}
    cd = sum(d["countries"] for d in days)
    total = {k: sum(d["would_read"][k] for d in days) for k in ("natural", "deliberate", "open")}
    by_reading = Counter(o["reading"] for o in outages.values())
    cautions = []
    if len(days) < THIN_DAYS:
        cautions.append(f"Only {len(days)} archived days: the background rates are a first look, not a settled rate.")
    if len(outages) < THIN_OUTAGES:
        cautions.append(f"Only {len(outages)} real outages in the archive: the replay counts are examples, not rates.")
    kinds_first = {k: next(d["day"] for d in days if k in d["recent_notice_kinds"])
                   for k in sorted({k for d in days for k in d["recent_notice_kinds"]})}
    return {
        "join": tri.JOIN,
        "archive": {"days": len(snapshots), "first": snapshots[0]["generated_at"] if snapshots else None,
                    "last": snapshots[-1]["generated_at"] if snapshots else None,
                    "days_left_out_layer_down": skipped},
        "cautions": cautions,
        "replay": {"outages": len(outages), "by_reading": dict(sorted(by_reading.items())),
                   "rows": sorted(outages.values(), key=lambda o: o["outage"])},
        "background": {
            "what": "Share of country-days on which an outage would have met each signal anyway.",
            "country_days": cd,
            "hazard": _share(sum(d["hazard"] for d in days), cd),
            "recent_notice": _share(sum(d["recent_notice"] for d in days), cd),
            "raised_attention": _share(sum(d["raised_attention"] for d in days), cd),
            "would_read": {k: _share(v, cd) for k, v in total.items()},
            "notice_kinds_first_seen": kinds_first,
            "by_country_days": len(days),
            "by_country": by_country,
            "by_country_note": "Share of archived days each country would have read natural or deliberate. A "
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
    out.mkdir(parents=True, exist_ok=True)
    (out / "base-rates.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    b = doc["background"]
    print(f"{doc['archive']['days']} days, {doc['archive']['first']} to {doc['archive']['last']}; "
          f"left out (a layer down): {doc['archive']['days_left_out_layer_down'] or 'none'}")
    print(f"replay: {doc['replay']['outages']} outages {doc['replay']['by_reading']}")
    print(f"background over {b['country_days']} country-days: hazard {b['hazard']:.1%}, recent notice "
          f"{b['recent_notice']:.1%}, raised attention {b['raised_attention']:.1%}")
    print("  an outage on a random country-day would read: " +
          ", ".join(f"{k} {v:.1%}" for k, v in b["would_read"].items()))
    for d in b["days"]:
        print(f"  {d['day']}  hazard {d['hazard']:>3}  notice {d['recent_notice']:>3}  attention "
              f"{d['raised_attention']:>3}  kinds {d['recent_notice_kinds']}")
    always = sorted(cc for cc, c in b["by_country"].items() if c["deliberate"] >= 0.75)
    print(f"  {len(b['by_country'])} countries ever read natural or deliberate; deliberate on 3 days in 4 or more: "
          f"{', '.join(always) or 'none'}")
    for c in doc["cautions"]:
        print("caution: " + c)
    print(f"wrote {out / 'base-rates.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
