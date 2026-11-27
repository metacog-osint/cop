"""The triage log: every reading outage triage has given, kept after the outage ends (cop-purpose sequence step 3).

    python tools/triage_log.py --triage site/data/triage.json --assets out/triage-log            one build
    python tools/triage_log.py --triage site/data/triage.json --assets out/triage-log --first    the first build ever
    python tools/triage_log.py --replay out/rates-archive --assets out/triage-log                archived days, in order

Each build reruns triage, and a reading can change over an outage's life: Watchfloor lags a day, a notice can come
after the outage. The page shows only the newest reading. This log keeps one item per outage and place, with what each
layer showed, the base rate and the replay figure, and a `history` of every reading it has had, so that a reading can
be scored later against what happened (Verdict, the 12 Dec test) and the replay can be rerun on COP's own outages.

Shape A (18 Nov 2026): the doctrine kit's history log, unchanged (tic-history/1, `history.update()`, as
tools/ledger.py uses it). Lane `cop/triage`. The kit writes `new` when an outage is first read and `ended` when it
leaves triage, and a `changed` line only when tier or response stage moves, which a reading never does: a reading
change is kept in the item's own `triage.history` (one entry when the reading or a layer's `found` changes, not on
every build) and stated as `last_change: changed` with `last_change_at`, in the feed contract's revision 2.1 words
(the reading is COP's own level, `doctrine/kit/docs/feed-contract-v2.md`). `cop_last_read_at` moves on every build
and is no change at all. Kit 0.5.13 added `reading_key` to `history.update()`: where the vendored kit has it, the log
passes `cop_reading`, so a reading change is also its own `changed` line with the reading before in `was` (shape B,
with no kit release of its own). Both kits give the same items.

Rules for the record (the private planning notes):
- No probability and no cause: what each layer showed, the base rate, the replay figure. Tier stays routine; a
  reading is not a warning, not a forecast and not a finding of cause.
- `labels` stays empty until forward labels (option C): Cloudflare's stated cause, or STOP's next release counted.
- An outage ends only on a build that read Blackout (fresh or late); otherwise it stays open and nothing ends.
- Every id the log cites must belong to one of triage's declared inputs (tools/gate.py), or nothing is written.

Files, in --assets (the build keeps them as assets of the release "triage-log", never in site/, functions/ or mcp/):
    state.json.gz           the readings open after the last build
    YYYY-MM-DD.jsonl.gz     that UTC day's lines

A replay rebuilds the log from archived daily snapshots (one reading a day, the day's last snapshot), each line marked
`backfill`. It reruns triage on each day with the history the archive held by that day, without base rates (they need
the whole archive) and with the STOP replay on file now.

Standard-library Python only.
"""

import argparse
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
sys.path.insert(0, str(ROOT / "tools"))
from tic_doctrine import history  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402  (its file handling: state, day files, deployed-folder refusal)
import triage as tri  # noqa: E402

FORMAT_NOTE = "cop-triage-log/1"
LANE = "cop/triage"
READING_KEY = "cop_reading"     # the reading at top level, for the kit's reading_key (kit 0.5.13 and later)
READ_STATES = ("fresh", "late")
VOLATILE = history.VOLATILE + ("cop_last_read_at",)
# What a layer counted toward the reading; every other list of items it holds was listed, not counted.
COUNTED = {"elements": ("significant", "minor"), "cordon": ("recent",), "watchfloor": ("unusual_items",)}


def _ids(rows):
    return [r["id"] for r in rows or [] if isinstance(r, dict) and r.get("id")]


def layer_summary(name, layer):
    counted = [i for k in COUNTED.get(name, ()) for i in _ids(layer.get(k))]
    listed = []
    for k, v in layer.items():
        if isinstance(v, list) and k not in COUNTED.get(name, ()):
            listed += [i for i in _ids(v) if i not in counted and i not in listed]
    return {"state": layer.get("state"), "found": layer.get("found"), "counted": counted, "listed": listed,
            "showed": layer.get("showed")}


def place_of(rec):
    return rec.get("us_state") or rec.get("country")


def log_id(rec):
    return f"cop:triage:{rec['outage']['id']}:{place_of(rec)}"


def _found(layers):
    return {k: v.get("found") for k, v in sorted(layers.items())}


def _verdict_id(draft):
    return (draft or {}).get("verdict_id")


def reading_item(rec, doc, at, before=None):
    """One record of triage's public document as a contract-v2 item. before: this outage's item as the log last held
    it, whose history continues here."""
    o = rec["outage"]
    layers = {k: layer_summary(k, v) for k, v in sorted((rec.get("layers") or {}).items())}
    entry = {"at": at, "reading": rec["reading"], "found": _found(layers)}
    old = ((before or {}).get("properties") or {}).get("triage") or {}
    hist = list(old.get("history") or [])
    last = hist[-1] if hist else None
    if last is None or last["reading"] != entry["reading"] or last["found"] != entry["found"]:
        hist.append(entry)
    br, rr = rec.get("base_rate") or {}, rec.get("replay_rate") or {}
    if rec.get("us_state"):
        place = rec.get("us_state_name") or rec["us_state"]
    else:
        place = tri.country_name(rec["country"])
    sources = [o["id"]] + sorted({i for v in layers.values() for i in v["counted"] + v["listed"]} - {o["id"]})
    changed_at = hist[-1]["at"]
    return {"type": "Feature", "geometry": None, "properties": {
        "id": log_id(rec), "program": "cop", "kind": "observation", "status_class": "reported", "tier": "routine",
        "title": f"{place}: outage triage reading {rec['reading']}",
        "summary": rec.get("reading_text"),
        "data_as_of": changed_at, "published_at": at, "cop_last_read_at": at, READING_KEY: rec["reading"],
        "last_change": "new" if len(hist) == 1 else "changed", "last_change_at": changed_at,
        "countries": [rec["country"]] if rec.get("country") else [],
        "sources": [{"id": i} for i in sources],
        "triage": {
            "format": FORMAT_NOTE, "join": doc["join"]["id"], "join_version": doc["join"].get("version"),
            "outage": {"id": o["id"], "kind": rec.get("outage_kind"), "source": o.get("source"),
                       "indicator_id": o.get("indicator_id"), "confirmed": o.get("confirmed"),
                       "started_at": o.get("started_at"), "ended_at": o.get("ended_at"),
                       "network": o.get("network"), "stated_cause": o.get("stated_cause"),
                       "us_state": rec.get("us_state")},
            "reading": rec["reading"], "layers": layers, "no_current_data": rec.get("no_current_data") or [],
            "base_rate": {k: br.get(k) for k in ("share_of_country_days", "share_of_this_countrys_days",
                                                 "counted_days")} if br else None,
            "replay_rate": {k: rr.get(k) for k in ("pool", "shutdown_share", "ordinary_day_share")} if rr else None,
            "questions": {"ooni": _verdict_id(rec.get("question_draft")),
                          "cause": _verdict_id(rec.get("cause_question_draft")), "registered": []},
            "history": hist, "labels": list(old.get("labels") or []),
        }}}


def when(doc):
    at = history._t(doc.get("snapshot_generated_at"))
    if at is None:
        raise SystemExit("triage log: the triage document has no snapshot_generated_at")
    return at


def blackout_read(doc):
    return (doc.get("layer_states") or {}).get("blackout") in READ_STATES


def review(doc):
    """tools/gate.py's verdict on what the log will cite: triage's records, under triage's own declared inputs."""
    return gate.review(doc["join"], {"records": doc.get("records") or []})


def kit_tracks_readings():
    """Kit 0.5.13 added history.update(reading_key=...): a move in that property writes its own "changed" line, with
    the reading before in `was`. COP vendored 0.5.12 when this was written; from 0.5.13 the log uses it, and every
    reading change is also a kit line (shape B at no extra cost), while triage.history stays as it is."""
    return "reading_key" in inspect.signature(history.update).parameters


def _reading_kw():
    return {"reading_key": READING_KEY} if kit_tracks_readings() else {}


def step(state, doc, **extra):
    """(new state, lines) for one triage document. extra members go on every line (a replay marks its lines)."""
    verdict = review(doc)
    if verdict["review_class"] != "open":
        raise SystemExit("triage log: refused by the gate: " + "; ".join(verdict["reasons"]))
    now = when(doc)
    at = history._stamp(now)
    open_ = (state or {}).get("open") or {}
    feats, seen = [], set()
    for rec in doc.get("records") or []:
        if not place_of(rec) or not (rec.get("outage") or {}).get("id"):
            continue
        iid = log_id(rec)
        if iid in seen:
            continue
        seen.add(iid)
        feats.append(reading_item(rec, doc, at, (open_.get(iid) or {}).get("item")))
    ok = {LANE} if blackout_read(doc) else set()
    new_state, lines = history.update(state or {}, {"features": feats}, None, now, ok=ok,
                                      lane_of=lambda f: LANE, volatile=VOLATILE, **_reading_kw())
    for x in lines:
        x.update(extra)
    new_state["blackout_read"] = bool(ok)
    return new_state, lines


def run(doc, folder, first=False, **extra):
    state = ledger.load_state(folder)
    if state is None and not first:
        raise SystemExit(f"triage log: no state.json.gz in {folder}. Without the last state every reading would be "
                         "logged as new again. Pass --first only if the log has never run.")
    if state is not None and (state.get("updated_at") or "") >= history._stamp(when(doc)):
        raise SystemExit(f"triage log: the state is from {state.get('updated_at')}, not older than this triage "
                         f"({history._stamp(when(doc))}); nothing written")
    state, lines = step(state or {}, doc, **extra)
    ledger.save(folder, state, lines)
    return state, lines


def replay_docs(archive):
    """Triage's public document for each archived day, oldest first, with the history the archive held by then."""
    import triage_rates  # noqa: E402
    import triage_replay  # noqa: E402
    files = sorted(f for f in Path(archive).glob("????-??-??.geojson.gz"))
    if not files:
        raise SystemExit(f"triage log: no archived snapshots in {archive}")
    stop = triage_replay.load(triage_replay.REPLAYS)
    snaps = []
    for f in files:
        snaps.append(triage_rates.attach_ended(tri.load(f), f))
        snap = snaps[-1]
        doc = tri.triage(snap, triage_rates.history(snaps), snap.get("cop_ended"))
        yield tri.public(tri.attach_replay(tri.attach_rates(doc, None), stop))


def replay(archive, folder):
    if ledger.load_state(folder) is not None:
        raise SystemExit(f"triage log: {folder} already holds a log; a replay starts from an empty folder")
    first = True
    for doc in replay_docs(archive):
        if not doc.get("snapshot_generated_at"):
            continue
        state, lines = run(doc, folder, first=first, backfill="daily archive")
        first = False
        print(report(when(doc), state, lines))
    if first:
        raise SystemExit(f"triage log: no archived snapshot in {archive} had a time")
    return 0


def report(at, state, lines):
    n = {c: sum(1 for x in lines if x["change"] == c) for c in ("new", "changed", "ended")}
    moved = sum(1 for e in state["open"].values()
                if (((e.get("item") or {}).get("properties") or {}).get("last_change_at") == history._stamp(at)
                    and e.get("first_seen") != history._stamp(at)))
    text = (f"triage log {at:%Y-%m-%dT%H:%MZ}: {n['new']} new, {n['ended']} ended, {moved} reading changed; "
            f"{len(state['open'])} open")
    return text + ("" if state.get("blackout_read") else "; Blackout not read, so nothing ended")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--triage", help="triage's public document just built (site/data/triage.json)")
    ap.add_argument("--assets", default=str(ROOT / "out" / "triage-log"), help="folder of the log's files")
    ap.add_argument("--first", action="store_true", help="the log has never run: start with no state")
    ap.add_argument("--replay", help="build a log from this folder of archived daily snapshots instead")
    a = ap.parse_args(argv)
    folder = ledger._folder(a.assets)
    if a.replay:
        return replay(Path(a.replay), folder)
    if not a.triage:
        ap.error("--triage or --replay")
    doc = json.loads(Path(a.triage).read_text(encoding="utf-8"))
    state, lines = run(doc, folder, a.first)
    print(report(when(doc), state, lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
