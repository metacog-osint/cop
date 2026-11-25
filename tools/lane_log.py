"""COP's lane records, COP side: the locked log of what each lane flags, and one record line per lane. Stdlib only.

    python tools/lane_log.py --log out/lane-log.jsonl --triage site/data/triage.json \
        --three-signal site/data/three-signal.json --direction-log out/direction-log.jsonl --publish site/data

Design adopted by Tom, 24 Nov 2026 (ideas#88; built under ideas#330, steps 1 and 2). A lane is one kind of item from one
program (config/lanes.json). Each build appends a "flag" line for every item a logged lane flags for the first time,
before its outcome can be known: the lane, its method version, the item's key and reading, and when the lane's
horizon ends. The file is append-only; each line carries the SHA-256 of the line before it, and a broken chain stops
the tool. A method change starts a new segment, and segments are never pooled.

The public record (site/data/lanes.json) holds counts only. Verdict scores the outcomes (ideas#331); until a lane has
min_scored_for_rate independently scored items it shows no rate or skill figure, and the pick order does not change.
A record describes how a measurement has behaved. It is not a warning and gives no likelihood to any single item.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import triage_replay as replay_mod  # noqa: E402

SCHEMA = "tic-lane-log/1"
PUBLIC_SCHEMA = "cop-lane-records/1"
PUBLIC_NAME = "lanes.json"
CONFIG = ROOT / "config" / "lanes.json"
GENESIS = "0" * 64
NOT_A_WARNING = ("A lane record counts how one kind of item has behaved: how many it flagged and, once Verdict scores "
                 "them, how many a second source confirmed. It is not a warning and gives no likelihood to any item.")
NO_CHECK = "No outside check yet. COP's ledger keeps every item; this lane gets a record once a second source is named."
REPLAY_ORDER = ("natural", "tension context", "open")


def line_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_log(path):
    """The log's lines as (text, record). Stops on a broken chain: a changed record is never built upon."""
    path = Path(path)
    if not path.exists():
        return []
    out, prev = [], GENESIS
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        rec = json.loads(text)
        if rec.get("prev") != prev:
            raise SystemExit(f"lane log: the chain breaks at line {n}")
        out.append((text, rec))
        prev = line_hash(text)
    return out


def append(path, lines, records):
    prev = line_hash(lines[-1][0]) if lines else GENESIS
    new = []
    for rec in records:
        text = json.dumps(rec | {"prev": prev}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        new.append(text)
        prev = line_hash(text)
    if new:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(x + "\n" for x in new))
    return len(new)


def load_config(path=CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _version(doc):
    j = (doc or {}).get("join") or {}
    return f"{j.get('id', '?')} {j.get('version', '?')}"


def triage_flags(lane, doc, now):
    """One candidate line per internet outage triage read. US state power outages are a later lane of their own."""
    out = []
    for r in (doc or {}).get("records") or []:
        if r.get("outage_kind") != "internet":
            continue
        o = r.get("outage") or {}
        if not o.get("id"):
            continue
        out.append({"key": o["id"], "subject": {"country": r.get("country"), "title": o.get("title"),
                                                "data_as_of": o.get("data_as_of"), "confirmed": o.get("confirmed")},
                    "reading": r.get("reading")})
    return out


def three_signal_flags(lane, doc, now):
    """One candidate line per area, reading and UTC day, for the readings the lane flags."""
    keep = set(lane.get("flag_readings") or [])
    day = now.date().isoformat()
    return [{"key": f"{r['area']}:{r['reading']}:{day}", "subject": {"area": r["area"], "name": r.get("area_name"),
                                                                   "signals_present": r.get("signals_present") or []},
             "reading": r["reading"]}
            for r in (doc or {}).get("records") or [] if r.get("area") and r.get("reading") in keep]


FLAGGERS = {"triage": triage_flags, "three-signal": three_signal_flags}


def update(path, cfg, docs, now):
    """Append a flag line for every item a logged lane flags that this lane's segment has not logged. Returns the
    number written. docs: {"triage": doc or None, "three-signal": doc or None}. A missing document logs nothing."""
    lines = read_log(path)
    seen = {(r["lane"], r["method_version"], r["key"]) for _, r in lines if r.get("type") == "flag"}
    new = []
    for lane in cfg["lanes"]:
        src = lane.get("logged_from")
        if src not in FLAGGERS or not docs.get(src):
            continue
        version = _version(docs[src])
        horizon = timedelta(days=lane.get("horizon_days") or 0)
        for f in FLAGGERS[src](lane, docs[src], now):
            if (lane["id"], version, f["key"]) in seen:
                continue
            seen.add((lane["id"], version, f["key"]))
            new.append({"type": "flag", "schema": SCHEMA, "lane": lane["id"], "program": lane["program"],
                        "method_version": version, "key": f["key"], "reading": f["reading"], "subject": f["subject"],
                        "logged_at": _iso(now), "horizon_ends": _iso(now + horizon)})
    return append(path, lines, new)


def _day(iso):
    return (iso or "")[:10] or None


def _counts(records):
    by = {}
    for r in records:
        by[r.get("reading") or "unread"] = by.get(r.get("reading") or "unread", 0) + 1
    return dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))


def _pct(x):
    return f"{x * 100:.1f}%"


def replay_block(rep):
    """The STOP replay's matched three-layer figures per reading, labelled replay. None without a replay on file."""
    if not rep:
        return None
    pool = rep.get("three_layer") or {}
    s, o = pool.get("shutdown_shares"), pool.get("ordinary_day_shares")
    if not s or not o:
        return None
    parts = [f"{k} on {_pct(s[k])} of shutdowns and {_pct(o[k])} of ordinary days" for k in REPLAY_ORDER]
    return {"label": "replay", "source": rep.get("source"), "attribution": rep.get("attribution"),
            "licence": rep.get("licence"), "run": (rep.get("runs") or {}).get("three_layer"),
            "matched_shutdowns": pool.get("matched_shutdowns"), "left_out": pool.get("matched_left_out") or [],
            "shutdown_shares": s, "ordinary_day_shares": o,
            "text": (f"Replay, not live: in Access Now's documented 2025 shutdowns ({pool.get('matched_shutdowns')} "
                     f"compared, each against its own country's ordinary days), triage read " + "; ".join(parts) + ".")}


def _segment(records):
    """The current segment: the lines under the newest method version. Older segments are counted apart."""
    if not records:
        return None, [], []
    version = records[-1]["method_version"]
    cur = [r for r in records if r["method_version"] == version]
    older = sorted({r["method_version"] for r in records} - {version})
    return version, cur, older


def _flag_text(n, since, counts, scored):
    if not n:
        return "Nothing flagged yet in this segment."
    by = ", ".join(f"{v} {k}" for k, v in counts.items())
    tail = "none scored yet" if not scored else f"{scored} scored"
    return f"This lane: {n} flagged since {since} ({by}); {tail}."


def _direction(lane, log_records):
    calls = [r for r in log_records if r.get("type") == "call" and r.get("scored")]
    if not calls:
        return {"flagged": 0, "since": None, "by_reading": {}, "method_version": None, "older_segments": [],
                "text": "No direction call locked yet."}
    rule = calls[-1]["rule"]
    cur = [r for r in calls if r["rule"] == rule]
    weeks = sorted({r["week"] for r in cur})
    counts = {}
    for r in cur:
        counts[r["word"]] = counts.get(r["word"], 0) + 1
    counts = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    done = {r["week"] for r in log_records if r.get("type") == "followup"}
    by = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items())
    return {"flagged": len(cur), "since": weeks[0], "by_reading": counts, "method_version": f"direction rule {rule}",
            "older_segments": sorted({f"direction rule {r['rule']}" for r in calls} - {f"direction rule {rule}"}),
            "weeks": len(weeks), "weeks_followed_up": len(set(weeks) & done),
            "text": (f"This lane: {len(cur)} place calls locked over {len(weeks)} week{'s' if len(weeks) != 1 else ''} "
                     f"since {weeks[0]} ({by}); none scored yet.")}


def records(cfg, log_records, direction_records, rep, now):
    """The public document: one record per lane, counts only."""
    flags = [r for r in log_records if r.get("type") == "flag"]
    minimum = cfg.get("min_scored_for_rate", 30)
    replay = replay_block(rep)
    out = []
    for lane in cfg["lanes"]:
        src = lane.get("logged_from")
        row = {k: lane.get(k) for k in ("id", "name", "program", "match", "horizon_days", "second_source", "tests",
                                        "ids_stable")}
        row["logged"] = bool(src)
        row["scored"] = 0
        row["rate_shown"] = False
        row["rate_note"] = f"No rate or skill figure before {minimum} independently scored items."
        if src == "direction-log":
            row |= _direction(lane, direction_records)
        elif src:
            mine = [r for r in flags if r["lane"] == lane["id"]]
            version, cur, older = _segment(mine)
            since = _day(cur[0]["logged_at"]) if cur else None
            counts = _counts(cur)
            row |= {"flagged": len(cur), "since": since, "by_reading": counts, "method_version": version,
                    "older_segments": older, "text": _flag_text(len(cur), since, counts, 0)}
        else:
            row |= {"flagged": None, "since": None, "by_reading": {}, "method_version": None, "older_segments": [],
                    "text": NO_CHECK}
        row["replay"] = replay if lane["id"] == "blackout-outage-triage" else None
        out.append(row)
    return {"schema": PUBLIC_SCHEMA, "generated_at": _iso(now), "what": cfg.get("about"), "not_a_warning": NOT_A_WARNING,
            "min_scored_for_rate": minimum, "pick_order": "unchanged: one seat per program in config order",
            "lanes": out}


def lane_for(cfg_or_doc, program, item_id):
    """The lane an item belongs to: the first lane whose match fits (prefixes before a program's catch-all)."""
    for lane in cfg_or_doc["lanes"]:
        m = lane.get("match")
        if not m or m.get("program") != program:
            continue
        prefixes = m.get("id_prefixes")
        if prefixes and not any(str(item_id).startswith(p) for p in prefixes):
            continue
        return lane
    return None


def _read(path):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_lines(path):
    p = Path(path) if path else None
    if not p or not p.exists():
        return []
    return [json.loads(t) for t in p.read_text(encoding="utf-8").splitlines() if t.strip()]


def publish_folder(path):
    out = Path(path).resolve()
    site = (ROOT / "site").resolve()
    if (out == site or site in out.parents) and out != site / "data":
        raise SystemExit("lane_log.py publishes only to site/data")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True, help="the lane log (lane-log.jsonl from the branch lane-log)")
    ap.add_argument("--triage", help="site/data/triage.json")
    ap.add_argument("--three-signal", help="site/data/three-signal.json")
    ap.add_argument("--direction-log", help="direction-log.jsonl from the branch direction-log, read only")
    ap.add_argument("--replays", default=str(replay_mod.REPLAYS))
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--publish", metavar="FOLDER", help=f"write {PUBLIC_NAME} here (the build passes site/data)")
    ap.add_argument("--no-append", action="store_true", help="write the record from the log as it is")
    ap.add_argument("--now", help="ISO time, for tests")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(timezone.utc)
    docs = {"triage": _read(a.triage), "three-signal": _read(a.three_signal)}
    if not Path(a.log).exists():
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        Path(a.log).touch()
    n = 0 if a.no_append else update(a.log, cfg, docs, now)
    print(f"lane log: {n} flag lines written"
          + "".join(f"; no {k} document" for k, v in docs.items() if v is None))
    if a.publish:
        doc = records(cfg, [r for _, r in read_log(a.log)], _read_lines(a.direction_log), replay_mod.load(a.replays), now)
        folder = publish_folder(a.publish)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / PUBLIC_NAME).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        print(f"published {folder / PUBLIC_NAME}: " + "; ".join(f"{x['id']} {x['flagged']}" for x in doc["lanes"]
                                                                 if x["logged"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
