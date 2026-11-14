"""The suite ledger: every item COP's picture has carried, kept after it leaves. Standard library only.

    python tools/ledger.py --snapshot site/data/today.geojson --assets out/ledger            one build
    python tools/ledger.py --snapshot site/data/today.geojson --assets out/ledger --first    the first build ever
    python tools/ledger.py --replay out/archive --assets out/ledger-replay                   archived days, in order
    python tools/ledger.py --day site/data/today.geojson                                     print the snapshot's UTC day

The snapshot carries what every program shows now. The ledger keeps what was: each build compares the snapshot just
built with the items open after the last build and writes one line per item that is new, changed (its tier or response
stage moved) or ended, with the full item, in the doctrine kit's history format (tic-history/1, doctrine/kit/docs/
history-log.md). It is the suite's own historical record, for comparing now with the past (Tom, 13 Nov 2026: an item
no longer active is not displayed, but every item is kept).

A lane here is a program. A program counts as read on a build when its state in the snapshot is fresh or late; when a
program's feed failed, or its data are stale, its missing items stay open and nothing of its ends. So an end in the
ledger means the program's own feed stopped carrying the item, seen at COP's two-hour step. A program's own log
(Elements keeps one, the others as they adopt the kit's history) is finer and is the record of that program; the
ledger is the one place with all of them, from the day it starts.

Files, in --assets (the build keeps them as release assets, not in the repository and not on the site):
    state.json.gz           the items open after the last build
    YYYY-MM-DD.jsonl.gz     that UTC day's lines; today's file is rewritten by each build of the day

Not published. The ledger holds every layer side by side, the military-context ones included, so it stays out of
site/, functions/ and mcp/ (this tool refuses those folders), like the daily snapshot copies it sits beside. Anything
drawn from it for the page or the MCP server is a join and goes through tools/gate.py.

Without the last state, every item would be logged as new again. So a run with no state.json.gz stops unless --first
says this is the first run.
"""

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
sys.path.insert(0, str(ROOT / "tools"))
from tic_doctrine import history  # noqa: E402
import gate  # noqa: E402

READ_STATES = ("fresh", "late")
# COP's own marks on an item change with the rest of the picture, not with the item: no change at all.
VOLATILE = ("published_at", "cop_pick", "cop_stale", "cop_strength", "cop_strength_text")


def program_of(feature: dict) -> str | None:
    p = feature.get("properties") or {}
    return p.get("cop_program") or p.get("program")


def programs_read(snapshot: dict) -> set:
    """Programs whose feed came and was current enough on this build."""
    return {pid for pid, v in (snapshot.get("cop_programs") or {}).items()
            if isinstance(v, dict) and v.get("state") in READ_STATES}


def when(snapshot: dict) -> datetime:
    at = history._t(snapshot.get("generated_at"))
    if at is None:
        raise SystemExit("ledger: the snapshot has no generated_at")
    return at


def step(state: dict, snapshot: dict, **extra) -> tuple[dict, list]:
    """(new state, lines) for one snapshot. extra members go on every line (a replay marks its lines)."""
    new_state, lines = history.update(state, snapshot, None, when(snapshot), ok=programs_read(snapshot),
                                      lane_of=program_of, volatile=VOLATILE)
    for x in lines:
        x["program"] = x["lane"] or x.get("program")       # the snapshot's own `program` member is the item's too
        x.update(extra)
    new_state["programs_read"] = sorted(programs_read(snapshot))
    return new_state, lines


# --- files ---------------------------------------------------------------------------------------------------------

def _folder(assets) -> Path:
    folder = Path(assets).resolve()
    for d in gate.DEPLOYED:
        if folder == (ROOT / d).resolve() or (ROOT / d).resolve() in folder.parents:
            raise SystemExit(f"ledger: {folder} is deployed; the ledger is not published")
    return folder


def _write_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
        fh.write(text.encode("utf-8"))


def load_state(folder: Path) -> dict | None:
    p = folder / "state.json.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def save(folder: Path, state: dict, lines: list) -> list:
    """Write the state and add the lines to their days' files. Returns the day files written."""
    by_day = {}
    for x in lines:
        by_day.setdefault(x["at"][:10], []).append(x)
    written = []
    for day, rows in sorted(by_day.items()):
        p = folder / f"{day}.jsonl.gz"
        old = ""
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                old = fh.read()
        _write_gz(p, old + "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in rows))
        written.append(p)
    _write_gz(folder / "state.json.gz", json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return written


def read_day(folder: Path, day: str) -> list:
    p = Path(folder) / f"{day}.jsonl.gz"
    if not p.exists():
        return []
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return [json.loads(s) for s in fh if s.strip()]


def counts(lines: list) -> dict:
    out = {}
    for x in lines:
        row = out.setdefault(x.get("program") or "?", {"new": 0, "changed": 0, "ended": 0})
        row[x["change"]] += 1
    return out


def _report(at: datetime, state: dict, lines: list) -> str:
    n = {c: sum(1 for x in lines if x["change"] == c) for c in ("new", "changed", "ended")}
    not_read = sorted(set(e.get("lane") for e in state["open"].values()) - set(state.get("programs_read") or []))
    text = (f"ledger {at:%Y-%m-%dT%H:%MZ}: {n['new']} new, {n['changed']} changed, {n['ended']} ended; "
            f"{len(state['open'])} open")
    return text + (f"; not read, so nothing of theirs ended: {', '.join(str(x) for x in not_read)}" if not_read else "")


def _load(path) -> dict:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def run(snapshot: dict, folder: Path, first: bool = False, **extra) -> tuple[dict, list]:
    state = load_state(folder)
    if state is None and not first:
        raise SystemExit(f"ledger: no state.json.gz in {folder}. Without the last state every item would be logged "
                         "as new again. Pass --first only if the ledger has never run.")
    if state is not None and (state.get("updated_at") or "") >= history._stamp(when(snapshot)):
        raise SystemExit(f"ledger: the state is from {state.get('updated_at')}, not older than this snapshot "
                         f"({history._stamp(when(snapshot))}); nothing written")
    state, lines = step(state or {}, snapshot, **extra)
    save(folder, state, lines)
    return state, lines


def replay(archive: Path, folder: Path) -> int:
    """Build a ledger from archived daily snapshots (<day>.geojson.gz), oldest first, into an empty folder. One step a
    day is all the archive holds, so every line is marked: an item that came and went within a day is missing, and a
    time is the time of that day's last snapshot."""
    if load_state(folder) is not None:
        raise SystemExit(f"ledger: {folder} already holds a ledger; a replay starts from an empty folder")
    files = sorted(p for p in Path(archive).glob("????-??-??.geojson.gz"))
    if not files:
        raise SystemExit(f"ledger: no archived snapshots in {archive}")
    for i, p in enumerate(files):
        snap = _load(p)
        state, lines = run(snap, folder, first=(i == 0), backfill="daily archive")
        print(_report(when(snap), state, lines))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", help="the snapshot just built (site/data/today.geojson, or a .gz copy)")
    ap.add_argument("--assets", default=str(ROOT / "out" / "ledger"), help="folder of the ledger's files")
    ap.add_argument("--first", action="store_true", help="the ledger has never run: start with no state")
    ap.add_argument("--replay", help="build a ledger from this folder of archived daily snapshots instead")
    ap.add_argument("--day", help="print the UTC day of this snapshot and stop (the build names release assets by it)")
    a = ap.parse_args(argv)
    if a.day:
        print(f"{when(_load(a.day)):%Y-%m-%d}")
        return 0
    folder = _folder(a.assets)
    if a.replay:
        return replay(Path(a.replay), folder)
    if not a.snapshot:
        ap.error("--snapshot, --replay or --day")
    snap = _load(a.snapshot)
    state, lines = run(snap, folder, a.first)
    print(_report(when(snap), state, lines))
    for pid, n in sorted(counts(lines).items()):
        print(f"  {pid:<11} new {n['new']:<4} changed {n['changed']:<3} ended {n['ended']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
