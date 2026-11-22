"""The Hormuz weekly stream: weekly bucketing, the 150 km share, severity coding, keeping every report, the voice rule."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402
import hormuz_stream as hs  # noqa: E402
import triage as tri  # noqa: E402

NOW = tri.utc("2026-11-21T12:00:00Z")


def report(n, incident, lat=26.55, lon=56.45, kind="Attack", **codes):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": f"wake:ukmto:ukmto-{n}", "data_as_of": incident,
                           "measures": {"ukmto_number": n, "report_type": kind, "incident_at": incident,
                                        "created_at": incident, **codes}}}


def transit(day, per_day=2.3):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [56.86, 26.3]},
            "properties": {"id": f"{hs.TRANSIT_PREFIX}{day}",
                           "measures": {"n_total_7d": per_day, "n_tanker_7d": 1.0, "baseline_90d_median": 3.8,
                                        "baseline_year_ago_median": 78.3, "change_year_ago_pct": -97.0}}}


def snap(*features, at="2026-11-21T12:00:00Z"):
    return {"generated_at": at, "features": list(features)}


def week(doc, key):
    return next(w for w in doc["weeks"] if w["week"] == key)


# Weekly bucketing: Monday to Sunday UTC, by incident time, and an empty week between two others is still a row.
st = hs.add(hs.new_state(), snap(report(1, "2026-11-01T23:59:00Z"), report(2, "2026-11-02T00:00:00Z"),
                                 report(3, "2026-11-16T08:00:00Z")))
doc = hs.build(st, NOW)
assert [w["week"] for w in doc["weeks"]] == ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]
assert [w["attacks"] for w in doc["weeks"]] == [1, 1, 0, 1]
assert week(doc, "2026-11-16")["complete"] is False and week(doc, "2026-11-16")["still_filling"] is True
assert week(doc, "2026-10-26")["week_to"] == "2026-11-01"

# A transit item dated Sunday lands in the week it closes.
st = hs.add(hs.new_state(), snap(transit("2026-11-15"), report(1, "2026-11-10T00:00:00Z")))
assert hs.build(st, NOW)["weeks"][0]["transits"]["per_day"] == 2.3

# The 150 km share: only Attack reports, inside the 900 km circle, measured from the strait's reference point.
assert hs.km_from_strait(*hs.STRAIT) == 0
st = hs.add(hs.new_state(), snap(report(1, "2026-11-10T00:00:00Z"),                       # at the strait
                                 report(2, "2026-11-10T01:00:00Z", lat=24.433, lon=58.433),  # about 300 km
                                 report(3, "2026-11-10T02:00:00Z", kind="Advisory"),      # not an Attack
                                 report(4, "2026-11-10T03:00:00Z", lat=12.0, lon=45.0)))  # outside the circle
w = hs.build(st, NOW)["weeks"][0]
assert (w["reports"], w["attacks"], w["attacks_within_150km"], w["share_within_150km"]) == (3, 2, 1, 0.5)
st = hs.add(hs.new_state(), snap(report(1, "2026-11-10T00:00:00Z", kind="Advisory")))
assert hs.build(st, NOW)["weeks"][0]["share_within_150km"] is None

# Severity: a stated code only, never guessed; casualties stay a separate flag; an outcome other than collide has none.
assert hs.code_severity({"severity": "L3 disabled or abandoned", "outcome": "collide"}) == "L3"
assert hs.code_severity({"severity": "L2"}) == "L2"
assert hs.code_severity({"severity": "L9"}) is None
assert hs.code_severity({}) is None
assert hs.code_severity({"severity": "L2", "outcome": "intersect"}) is None
assert hs.code_outcome({"outcome": "Collide"}) == "collide" and hs.code_outcome({"outcome": "destroy"}) is None
st = hs.add(hs.new_state(), snap(report(1, "2026-11-10T00:00:00Z", severity="L4", outcome="collide", casualties=True),
                                 report(2, "2026-11-10T01:00:00Z"),
                                 report(3, "2026-11-10T02:00:00Z", outcome="intersect")))
w = hs.build(st, NOW)["weeks"][0]
assert w["severity"]["L4"] == 1 and w["severity"]["not_coded"] == 1 and w["casualty_reports"] == 1

# Nothing is lost: a report that leaves the feed stays, and takes a code a later copy states.
st = hs.add(hs.new_state(), snap(report(1, "2026-10-01T00:00:00Z")))
hs.add(st, snap(report(2, "2026-11-10T00:00:00Z")))
assert set(st["reports"]) == {"wake:ukmto:ukmto-1", "wake:ukmto:ukmto-2"}
hs.add(st, snap(report(1, "2026-10-01T00:00:00Z", severity="L1", outcome="collide")))
assert st["reports"]["wake:ukmto:ukmto-1"]["severity"] == "L1"
hs.add(st, snap(report(1, "2026-10-01T00:00:00Z")))
assert st["reports"]["wake:ukmto:ukmto-1"]["severity"] == "L1"
with tempfile.TemporaryDirectory() as tmp:
    hs.save_state(st, tmp)
    assert hs.load_state(tmp) == st

# A base rate needs enough complete, settled weeks, and a week is never measured against itself.
feats = [report(i, f"2026-{m}T00:00:00Z") for i, m in enumerate(
    ["10-06", "10-13", "10-14", "10-20", "10-27", "11-03"], 1)]
st = hs.add(hs.new_state(), snap(*feats))
st["covered_from"] = "2026-10-01T00:00:00Z"
doc = hs.build(st, NOW)
assert week(doc, "2026-10-05")["attacks_base"]["enough"] is False
b = week(doc, "2026-11-02")["attacks_base"]
assert b["enough"] is True and b["weeks"] == 4 and b["median"] == 1

# The join goes through the gate, cites only its declared inputs, and a citation outside them is refused.
assert gate.review(hs.JOIN, doc)["may_publish"] is True
bad = json.loads(json.dumps(doc))
bad["weeks"][0]["items"].append({"id": "noisefloor-new-1"})
assert gate.review(hs.JOIN, bad)["review_class"] == "refused"

# The voice rule, in the join's own words: no probability, per-transit rate, attribution or forecast.
words = json.dumps({k: doc[k] for k in ("what", "not_a_warning", "limits", "credit")}).lower()
for banned in ("hit rate", "per transit", "per-transit", "likely", "irgc", "iran", "houthi", "strike window",
               "will ", "expect", "intent"):
    assert banned not in words, banned
for needed in ("45-77", "79-103", "7 single numbers", "baltic only", "not counted", "name no one", "never divided"):
    assert needed in words, needed
assert "probability" in doc["not_a_warning"] and "no probability" in doc["not_a_warning"]

print("test_hormuz_stream: ok")
