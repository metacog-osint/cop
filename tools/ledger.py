"""The suite ledger: every item COP's picture has carried, kept after it leaves. Standard library only.

    python tools/ledger.py --snapshot site/data/today.geojson --assets out/ledger            one build
    python tools/ledger.py --snapshot site/data/today.geojson --assets out/ledger --first    the first build ever
    python tools/ledger.py --replay out/archive --assets out/ledger-replay                   archived days, in order
    python tools/ledger.py --day site/data/today.geojson                                     print the snapshot's UTC day

The snapshot carries what every program shows now. The ledger keeps what was: each build compares the snapshot just
built with the items open after the last build and writes one line per item that is new, changed (its tier or response
stage moved) or ended, with the full item, in the doctrine kit's history format (tic-history/1, doctrine/kit/docs/
history-log.md). It is the suite's own historical record, for comparing now with the past (13 Nov 2026: an item
no longer active is not displayed, but every item is kept).

A lane here is a program, or a program's source where its feed says which sources were read. A program counts as read
on a build when its state in the snapshot is fresh or late; when a program's feed failed, or its data are stale, its
missing items stay open and nothing of its ends. Since 14 Nov 2026, a program whose feed carries a per-source `health`
block (Cordon, Blackout; the snapshot keeps it as `lanes` and `lanes_read` in cop_programs) has one lane per source,
"<program>/<source id>" (the item's first source id, through the program's `lane_map` in config/programs.json): its
item ends only when that source was read too, so a source that failed while the rest of the feed came ends nothing.
An item whose source the block does not name falls back to the program's lane. Lanes are worked out again from each
open item on every build, so items logged before the change move to their source lane. So an end in the ledger means
the program's own feed stopped carrying the item while its source was read, seen at COP's two-hour step. A program's own log
(Elements keeps one, the others as they adopt the kit's history) is finer and is the record of that program; the
ledger is the one place with all of them, from the day it starts.

A sighting needs a read too (15 Nov 2026). When a program's feed fails, the snapshot still carries its last good copies
(cop_stale), drawn grey. They are not sightings: step() leaves the items of a program that was not read out of what it
hands the kit, so their last_seen stays at the last build that really read them and nothing of theirs is logged. Before
this, a dead feed's copies pushed last_seen forward every two hours, and an end, when it came, was bracketed too
tightly. And an item the snapshot left out as malformed (its `dropped` list) has not left its program's feed: one named
there by id stays open, and a program with a malformed item that had no readable id ends nothing on that build. The
same holds one level down: a program read on this build may still keep one failed source's last good items, and an
item whose own source lane its health block names as not read is left out the same way (Orbit, Elements, Headwind).

Files, in --assets (the build keeps them as release assets, not in the repository and not on the site):
    state.json.gz           the items open after the last build
    YYYY-MM-DD.jsonl.gz     that UTC day's lines; today's file is rewritten by each build of the day

The workflow keeps state.json.gz in the release `ledger` for good; the day files stay there through 2026 and from 2027 go
to one release a year, `ledger-YYYY`. tools/dropped.py keeps the malformed items beside each day file.

Not served from the site. The ledger holds every layer side by side, the military-context ones included, so it stays out of
site/, functions/ and mcp/ (this tool refuses those folders), like the daily snapshot copies it sits beside. Anything
drawn from it for the page or the MCP server is a join and goes through tools/gate.py. Its release assets are public,
as every release of this public repository is.

Without the last state, every item would be logged as new again. So a run with no state.json.gz stops unless --first
says this is the first run.

Keyed on subject_id (kit 0.6.0, feed contract revision 2.1, 18 Nov 2026). The kit's history keeps an item under its
subject_id when it states one and under its id otherwise (history.key), so a daily reading of one subject under a new
dated id is an update, not a new item and an end; on the first build after a feed adds subject_id, an open entry kept
under the item's id moves to its subject and logs nothing. The state says so in `keyed_on`. The first build that
reads a state without it copies that state, byte for byte, to state-before-subject-id.json.gz beside it, and never
writes that copy again: the record of the ledger as it was before the re-keying.
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
KEYED_ON = "subject_id, else id"              # kit history.key(), from kit 0.6.0
BEFORE_KEYING = "state-before-subject-id.json.gz"
# COP's own marks on an item change with the rest of the picture, not with the item: no change at all.
VOLATILE = ("published_at", "cop_pick", "cop_stale", "cop_strength", "cop_strength_text", "cop_cells")


def program_of(feature: dict) -> str | None:
    p = feature.get("properties") or {}
    return p.get("cop_program") or p.get("program")


def programs_read(snapshot: dict) -> set:
    """Programs whose feed came and was current enough on this build."""
    return {pid for pid, v in (snapshot.get("cop_programs") or {}).items()
            if isinstance(v, dict) and v.get("state") in READ_STATES}


def _lane_maps() -> dict:
    cfg = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
    return {p["id"]: p["lane_map"] for p in cfg.get("programs") or [] if p.get("lane_map")}


def lane_namer(snapshot: dict, lane_maps: dict | None = None):
    """lane_of(feature) for this snapshot: "<program>/<source>" where the program's feed named its sources' health,
    otherwise the program."""
    lane_maps = _lane_maps() if lane_maps is None else lane_maps
    known = {pid: set(v.get("lanes") or []) for pid, v in (snapshot.get("cop_programs") or {}).items()
             if isinstance(v, dict) and v.get("lanes")}
    source_of = {pid: history.mapped_lane(lane_maps.get(pid) or {}) for pid in known}

    def lane_of(feature: dict):
        pid = program_of(feature)
        if pid not in known:
            return pid
        src = source_of[pid](feature)
        return f"{pid}/{src}" if src in known[pid] else pid
    return lane_of


def unnamed_drops(snapshot: dict) -> set:
    """Programs with an item the snapshot left out as malformed and could not name (no readable id). Which open item
    that was cannot be known, so nothing of such a program ends on this build."""
    return {x.get("program") for x in snapshot.get("dropped") or [] if isinstance(x, dict) and not x.get("id")}


def lanes_read(snapshot: dict) -> set:
    """Every lane whose missing items may end on this build: each program read, and each of its sources its own feed
    says was read, except a program with a malformed item the snapshot could not name."""
    ok = programs_read(snapshot) - unnamed_drops(snapshot)
    for pid, v in (snapshot.get("cop_programs") or {}).items():
        if pid in ok and isinstance(v, dict):
            ok |= {f"{pid}/{s}" for s in v.get("lanes_read") or []}
    return ok


def when(snapshot: dict) -> datetime:
    at = history._t(snapshot.get("generated_at"))
    if at is None:
        raise SystemExit("ledger: the snapshot has no generated_at")
    return at


def step(state: dict, snapshot: dict, **extra) -> tuple[dict, list]:
    """(new state, lines) for one snapshot. extra members go on every line (a replay marks its lines)."""
    lane_of = lane_namer(snapshot)
    state = dict(state or {})
    if state.get("open"):
        # Lanes follow this build's health blocks, so an item logged under its program moves to its source lane.
        state["open"] = {iid: dict(e, lane=lane_of(e["item"])) if e.get("item") else e
                         for iid, e in state["open"].items()}
    ok = lanes_read(snapshot)
    # Only what was read on this build counts as seen. A program that was not read shows its last good copies
    # (cop_stale): left out here, they neither move last_seen nor log anything, and since their lanes are not in
    # `ok`, they stay open. The same one level down (15 Nov 2026): an item whose own source lane the program's health
    # block says was not read is that source's last good copy, so it is left out too and stays open.
    read = programs_read(snapshot)

    def sighted(f):
        pid, lane = program_of(f), lane_of(f)
        return pid in read and (lane == pid or lane in ok)
    seen = dict(snapshot, features=[f for f in snapshot.get("features") or [] if sighted(f)])
    new_state, lines = history.update(state, seen, None, when(snapshot), ok=ok,
                                      lane_of=lane_of, volatile=VOLATILE)
    for x in lines:
        # the snapshot's own `program` member is the item's too; the lane may name a source after the program
        x["program"] = program_of(x.get("item") or {}) or (x.get("lane") or "").split("/")[0] or x.get("program")
        x.update(extra)
    new_state["programs_read"] = sorted(programs_read(snapshot))
    new_state["lanes_read"] = sorted(ok)
    new_state["keyed_on"] = KEYED_ON
    return new_state, lines


# --- files ---------------------------------------------------------------------------------------------------------

def _folder(assets) -> Path:
    folder = Path(assets).resolve()
    for d in gate.DEPLOYED:
        if folder == (ROOT / d).resolve() or (ROOT / d).resolve() in folder.parents:
            raise SystemExit(f"ledger: {folder} is deployed; the ledger is not served from the site")
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
    read = set(state.get("lanes_read") or state.get("programs_read") or [])
    not_read = sorted(set(e.get("lane") for e in state["open"].values()) - read)
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
    if state is not None and state.get("keyed_on") != KEYED_ON:
        keep_before_keying(folder)
    state, lines = step(state or {}, snapshot, **extra)
    save(folder, state, lines)
    return state, lines


def keep_before_keying(folder: Path) -> Path | None:
    """Copy state.json.gz to BEFORE_KEYING, byte for byte, unless the copy is there already (it is never
    overwritten). Returns the copy written, or None."""
    src, dest = folder / "state.json.gz", folder / BEFORE_KEYING
    if dest.exists() or not src.exists():
        return None
    dest.write_bytes(src.read_bytes())
    return dest


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
