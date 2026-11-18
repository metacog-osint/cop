"""The STOP replay's figures, ready to sit beside triage's base rates (cop-purpose sequence step 4, second half).

tools/stop_replay.py replayed triage on Access Now's documented 2025 shutdowns and on the same countries' ordinary
days (history/replays/stop-2025-two-layer-* and stop-2025-three-layer-*). This module reads those two manifests and
nothing else. It keeps counts and rates only: STOP carries no data licence, so no row, date or event leaves the
manifests, and the credit is "Access Now, #KeepItOn STOP".

What it says, and what it never says: how often a documented shutdown would have read each way, and how often the
same countries' ordinary days would have. It does not say that triage detects shutdowns, and a reading stays not a
warning, not a forecast and not a finding of cause.

Standard-library Python only.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLAYS = ROOT / "history" / "replays"
CONTRACT = "cop-layer-history/1"
ATTRIBUTION = "Access Now, #KeepItOn STOP"
SOURCE = "Access Now's #KeepItOn STOP dataset, 2025"
LICENCE = "No data licence stated; counts and rates only"
READINGS = ("natural", "tension context", "open", "incomplete")
WHAT = ("How often a documented shutdown would have read each way, beside how often the same countries' ordinary "
        "days would have, from replaying triage on Access Now's documented 2025 shutdowns. It does not say that "
        "triage detects shutdowns.")
NO_CORDON = ("Cordon's notices were not replayed for this country, so its tension context here rests on press "
             "attention alone and reads lower than triage would read it.")
STATE_CAUTION = ("No hit rate yet for US state power outages: the STOP replay covers internet shutdowns, and no "
                 "power outage of known cause has been replayed. These rates say only how often each reading "
                 "happens anyway.")
COUNTRY_MIN = 1   # a country row is shown when STOP documented at least this many shutdowns there


def _latest(kind, folder):
    """The newest run of this kind, by its folder name (they end in the run date), or None."""
    runs = sorted(Path(folder).glob(f"stop-2025-{kind}-layer-*/manifest.json"))
    return runs[-1] if runs else None


def _shares(d):
    return {k: d[k] for k in READINGS} if d and all(isinstance(d.get(k), (int, float)) for k in READINGS) else None


def _pool(shutdowns, matched, places=None, layers=None):
    """One layer count's pooled figures: all shutdowns, and the matched comparison (each shutdown against its own
    country's ordinary days)."""
    m = matched
    return {"layers": layers, "shutdowns": shutdowns["shutdowns"], "all_shutdowns_shares": _shares(shutdowns["shutdowns_shares"]),
            "matched_shutdowns": m["shutdowns"], "matched_left_out": list(m.get("left_out") or []),
            "shutdown_shares": _shares(m["shutdowns_shares"]), "ordinary_day_shares": _shares(m["control_shares"]),
            "ordinary_days": shutdowns["control_days"], "places": places}


def _country_rows(results):
    return {c["country"]: c for c in results["by_country"]}


def load(folder=REPLAYS):
    """The two newest replay manifests boiled down to counts and rates, or None when either is missing or not what
    this reads (the page and the server then say no replay is on file; nothing else changes)."""
    two_p, three_p = _latest("two", folder), _latest("three", folder)
    if not two_p or not three_p:
        return None
    try:
        two = json.loads(two_p.read_text(encoding="utf-8"))
        three = json.loads(three_p.read_text(encoding="utf-8"))
        for m in (two, three):
            if m.get("contract") != CONTRACT or m.get("redistribution") != "aggregates":
                return None
        r2, r3 = two["results"], three["results"]
        places = sorted(three["places"])
        two_layer = _pool(r2, r2["matched"], layers=list(two["layers_replayed"]))
        three_layer = _pool(r3["three_layer"], r3["three_layer"]["matched"], places=places,
                            layers=list(three["layers_replayed"]))
        same_shutdowns_two = _pool(r3["two_layer"], r3["two_layer"]["matched"], places=places,
                                   layers=list(two["layers_replayed"]))
        three_rows, two_rows = _country_rows(r3["three_layer"]), _country_rows(r2)
        by_country = {}
        for cc in sorted(set(two_rows) | set(three_rows)):
            # Triage reads three layers; use the three-layer row where Cordon was replayed, the two-layer one elsewhere.
            row, kind = (three_rows[cc], "three_layer") if cc in three_rows else (two_rows[cc], "two_layer")
            if row["shutdowns"] < COUNTRY_MIN:
                continue
            by_country[cc] = {"name": row["name"], "layers": kind, "shutdowns": row["shutdowns"],
                              "read": {k: int(row["shutdowns_read"].get(k, 0)) for k in READINGS},
                              "ordinary_days": row["control_days"],
                              "ordinary_day_shares": _shares({k: (row["control_shares"] or {}).get(k) for k in READINGS})}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # Caveats per run (fixed 18 Nov 2026): merging both runs' lists put the two-layer run's "Cordon's notices are not
    # replayed yet" beside the three-layer run's caveats on Cordon's history. `caveats` keeps only what both runs say;
    # each pool carries what its own run adds, and each record's replay_rate carries its own pool's.
    two_c, three_c = list(two.get("caveats") or []), list(three.get("caveats") or [])
    caveats = [c for c in two_c if c in three_c]
    two_layer["caveats"] = [c for c in two_c if c not in caveats]
    three_layer["caveats"] = [c for c in three_c if c not in caveats]
    rep = {
        "source": SOURCE, "attribution": ATTRIBUTION, "licence": LICENCE, "what": WHAT,
        "runs": {"two_layer": two["run_id"], "three_layer": three["run_id"]},
        "built_at": {"two_layer": two.get("built_at"), "three_layer": three.get("built_at")},
        "outage_hour_utc": (two.get("method") or {}).get("outage_hour_utc"),
        "readings": list(READINGS),
        "two_layer": two_layer, "three_layer": three_layer, "same_shutdowns_two_layer": same_shutdowns_two,
        "countries_with_shutdowns": len(two_rows),
        "by_country": by_country,
        "caveats": caveats,
    }
    rep["caution"] = caution(rep)
    return rep


def _pct(x):
    return f"{x * 100:.1f}%"


def _more_less(a, b):
    return "more often" if a > b + 0.005 else "less often" if a < b - 0.005 else "about as often"


def caution(rep):
    """The sentence that replaces HIT_RATE_CAUTION on the internet-outage base rates once the replay is on file. Its
    directions are read from the figures, never written in."""
    p = rep["two_layer"]
    s, o = p["shutdown_shares"], p["ordinary_day_shares"]
    nat, ten = _more_less(s["natural"], o["natural"]), _more_less(s["tension context"], o["tension context"])
    first = (f"natural and tension context showed {nat}" if nat == ten
             else f"natural showed {nat} and tension context {ten}")
    return (f"Access Now's documented 2025 shutdowns have been replayed (figures beside this): {first} on shutdown "
            f"days than on ordinary days, open {_more_less(s['open'], o['open'])}. That is a difference "
            f"in how often, over one year in {rep['countries_with_shutdowns']} countries. It does not show that a "
            f"reading identifies a shutdown or its cause"
            + (f", and most documented shutdowns still read open." if s["open"] > 0.5 else "."))


def record_rate(rep, country, reading, name=None):
    """The replay's line for one internet outage record, or None when the replay has no figures for that reading
    (unconfirmed, power) or no replay is on file."""
    if not rep or reading not in READINGS:
        return None
    cordon = country in (rep["three_layer"].get("places") or [])
    pool = rep["three_layer"] if cordon else rep["two_layer"]
    s, o = pool["shutdown_shares"][reading], pool["ordinary_day_shares"][reading]
    which = "three_layer" if cordon else "two_layer"
    row = rep["by_country"].get(country)
    name = name or (row or {}).get("name") or country
    text = (f"In Access Now's documented 2025 shutdowns ({pool['matched_shutdowns']} compared, each against its own "
            f"country's ordinary days), {reading} was the reading on {_pct(s)} of shutdowns and on {_pct(o)} of those "
            f"countries' ordinary days.")
    own = None
    if row:
        k = row["read"][reading]
        own = {"shutdowns": row["shutdowns"], "read": k, "ordinary_days": row["ordinary_days"],
               "ordinary_day_share": (row["ordinary_day_shares"] or {}).get(reading)}
        if own["ordinary_days"] and own["ordinary_day_share"] is not None:
            text += (f" In {name}: {k} of {row['shutdowns']} documented shutdowns read {reading}, against "
                     f"{_pct(own['ordinary_day_share'])} of its {row['ordinary_days']} ordinary days.")
        else:
            text += (f" In {name}: {k} of {row['shutdowns']} documented shutdowns read {reading}; it has no "
                     "ordinary days to compare them with.")
    else:
        text += f" STOP documented no 2025 shutdown in {name}, so there is no country figure."
    # The two-layer run's own caveat is about the run ("not replayed yet"); for a country outside Cordon's history
    # NO_CORDON says the same of this country, so it stands in for it. Cordon's countries get the three-layer run's own.
    notes = list(pool.get("caveats") or []) if cordon else [NO_CORDON]
    return {"reading": reading, "layers_replayed": pool["layers"], "pool": which,
            "matched_shutdowns": pool["matched_shutdowns"], "shutdown_share": s, "ordinary_day_share": o,
            "country": own, "text": text, "caveats": notes, "attribution": ATTRIBUTION}
