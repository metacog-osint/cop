"""Refresh times: when each program is expected to update, when it last did, and whether it is late on its own schedule.

A slow feed and a failed one look the same on the map; this tells them apart. The expected schedule is in
config/cadence.json (from each program's scheduler). The last update is read from the program's own feed:

- "lanes" (the default for a feed with a `health` block): the newest `checked_at` among its source lanes, the last
  time the program read any of its sources. Some feeds are served live, so their `generated_at` is the moment COP
  asked, not when the data changed.
- "generated": the feed's `generated_at`, for a feed with no health block.
- "data": the end of the newest data period (Watchfloor's whole UTC days), for a live feed with no health block.

Late when the last update is more than LATE_INTERVALS expected intervals plus LATE_GRACE_H hours old at the time of
COP's picture. Each source lane keeps its program's own state for it (the kit's health states), judged by that
program against the source's own window; COP restates it and judges nothing more. snapshot.py writes the result
into health.json, which site/refresh.html draws. Standard-library Python.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from tic_doctrine import vocab  # noqa: E402

CADENCE = json.loads((ROOT / "config" / "cadence.json").read_text(encoding="utf-8"))
LATE_INTERVALS = 2      # a run missed: the last update is older than two intervals ...
LATE_GRACE_H = 0.5      # ... plus the time a run takes
LANE_KEYS = ("state", "checked_at", "last_ok", "data_as_of", "failures", "detail")


def _utc(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def late_after_hours(every_hours):
    return LATE_INTERVALS * every_hours + LATE_GRACE_H


def lanes(feed):
    """The feed's own health block, one row per source lane, with only the fields the page shows."""
    h = feed.get("health") if isinstance(feed, dict) else None
    if not isinstance(h, dict):
        return {}
    return {k: {f: v.get(f) for f in LANE_KEYS if v.get(f) is not None} for k, v in sorted(h.items())
            if isinstance(v, dict)}


def last_update(feed, basis, data_end=None):
    """(time, basis actually used) of the program's last update, by the basis named in config/cadence.json."""
    if not isinstance(feed, dict):
        return None, None
    rows = lanes(feed)
    basis = basis or ("lanes" if rows else "generated")
    if basis == "lanes":
        times = [t for t in (_utc(r.get("checked_at")) for r in rows.values()) if t]
        if times:
            return max(times), "lanes"
        basis = "generated"
    if basis == "data":
        return data_end, "data"
    return _utc(feed.get("generated_at")), "generated"


def clock(pid, feed, now, data_end=None):
    # data_end: the end of the program's newest data period (snapshot.covered_until), for the "data" basis
    """One program's refresh entry for health.json. feed is None when COP could not read it this build."""
    c = CADENCE["programs"].get(pid)
    if not c:
        return None
    when, basis = last_update(feed, c.get("last_update"), data_end)
    if when and when > now:
        when = now
    age = round((now - when).total_seconds() / 3600, 2) if when else None
    late_h = late_after_hours(c["every_hours"])
    return {"every_hours": c["every_hours"], "runs": c["runs"], "scheduler": c["scheduler"],
            "last_update": _iso(when), "last_update_basis": basis, "age_hours": age, "late_after_hours": late_h,
            "status": "not_read" if feed is None else "unknown" if age is None else "late" if age > late_h else "on_time",
            "lanes": lanes(feed)}


def cop_clock(now):
    """COP's own schedule: its picture is this build, so it is on time by definition; the page compares it to now."""
    c = CADENCE["cop"]
    return {"every_hours": c["every_hours"], "runs": c["runs"], "scheduler": c["scheduler"], "last_update": _iso(now),
            "late_after_hours": late_after_hours(c["every_hours"])}


def method():
    """How the page's figures are made, and the kit's words for each source state (vocab health_state)."""
    return {"basis": CADENCE["basis"], "late_rule": CADENCE["late_rule"], "late_intervals": LATE_INTERVALS,
            "late_grace_hours": LATE_GRACE_H, "lane_states": vocab.load("enums")["health_state"]["values"]}
