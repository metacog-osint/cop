"""Rebuild a program's list of recently ended items for archived COP days, from that program's history log.

    python tools/rebuild_ended.py --history PATH/TO/elements/history --archive out/archive

COP's archive keeps each UTC day's last snapshot (<day>.geojson.gz) and, from 14 Nov 2026, the ended list Elements
served beside it (<day>.elements-ended.json.gz). For a day without that list this rebuilds it as Elements' build
would have written it at the snapshot's time: every item whose latest log line by then is `ended`, with an end inside
the last 7 days. It reads the log format (tic-history/1: one JSON line per new, changed or ended item, under
log/YYYY/MM/DD.jsonl or .jsonl.gz) and nothing else of the program's code. A day that already has a list is left
alone. Each rebuilt file says it was rebuilt, from what, and how far back the log reached at that time (a log that
started less than 7 days earlier gives a shorter list). Standard library only.
"""

import argparse
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOW_DAYS = 7
FORMAT = "tic-history/1"


def utc(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc) if s else None
    except ValueError:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_log(history):
    """Every line of a history log, oldest first (file order kept within a time: a new before its ended)."""
    files = sorted(Path(history).glob("log/*/*/*.jsonl*"), key=lambda p: (p.parent.parent.name, p.parent.name, p.name))
    lines = []
    for p in files:
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8") as fh:
            lines += [json.loads(s) for s in fh if s.strip()]
    lines = [x for x in lines if x.get("format") == FORMAT]
    return sorted(lines, key=lambda x: x["at"])          # stable: same-time lines keep their file order


def ended_at(lines, when, program="elements", days=WINDOW_DAYS, source=""):
    """The program's ended list as its build would have served it at `when`: items whose latest line logged by then
    is `ended`, with an end in the last `days` days, newest end first, each with its `history` member."""
    stamp, since = iso(when), when - timedelta(days=days)
    seen = [x for x in lines if x["at"] <= stamp and x.get("program") == program]
    last = {}
    for x in seen:
        last[x["id"]] = x
    feats = []
    for x in sorted(last.values(), key=lambda x: x.get("ended_at") or "", reverse=True):
        if x["change"] != "ended" or (utc(x.get("ended_at")) or since) < since:
            continue
        f = dict(x["item"])
        f["history"] = {k: x.get(k) for k in ("first_seen", "last_seen", "ended_at", "end_reason", "peak_tier", "updates",
                                              "seen_live") if x.get(k) is not None}
        feats.append(f)
    log_from = lines[0]["at"] if lines else None
    short = bool(log_from and utc(log_from) > since)
    return {"type": "FeatureCollection", "format": FORMAT, "program": program,
            "generated_at": seen[-1]["at"] if seen else stamp, "window_days": days,
            "rebuilt": {"by": "COP tools/rebuild_ended.py", "on": iso(datetime.now(timezone.utc)),
                        "from": source or "the program's history log", "as_of": stamp, "log_from": log_from,
                        "note": ("Rebuilt from the history log, not the file the program served that day."
                                 + (f" The log starts at {log_from}, less than {days} days before this time, so the "
                                    "list covers less than its window." if short else ""))},
            "features": feats}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--history", required=True, help="the program's history/ folder (log/YYYY/MM/DD.jsonl[.gz])")
    ap.add_argument("--archive", required=True, help="folder of <day>.geojson.gz archive copies")
    ap.add_argument("--program", default="elements")
    ap.add_argument("--source", default="", help="where the log came from, for the files' provenance line")
    a = ap.parse_args(argv)
    lines = read_log(a.history)
    if not lines:
        raise SystemExit(f"no {FORMAT} lines under {a.history}/log")
    arch = Path(a.archive)
    for snap_path in sorted(f for f in arch.glob("*.geojson.gz") if f.name.count(".") == 2):
        day = snap_path.name[:10]
        out = arch / f"{day}.{a.program}-ended.json.gz"
        if out.exists():
            print(f"{day}: has its list, left alone")
            continue
        with gzip.open(snap_path, "rt", encoding="utf-8") as fh:
            when = utc(json.load(fh).get("generated_at"))
        if not when:
            print(f"{day}: snapshot has no generated_at, skipped")
            continue
        doc = ended_at(lines, when, a.program, source=a.source)
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"{day}: {len(doc['features'])} ended items as of {iso(when)}{' (short: log too young)' if 'starts' in doc['rebuilt']['note'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
