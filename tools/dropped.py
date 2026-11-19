"""The dropped-items log: what snapshot.sift() leaves out of the picture, kept with the reason. Standard library only.

    python tools/dropped.py --rows out/dropped.jsonl --assets out/ledger --day 2026-11-18

`tools/snapshot.py --dropped-out out/dropped.jsonl` writes this build's rows: one per feature COP left out as
malformed (in a program's feed, or in its ended list), each with the program, the id when it could be read, the item's
place in the feed, the reason and the item itself exactly as the program published it. Until now those items were
only counted in health.json: they reached neither the daily archive nor the ledger, so a program that fixed or
retired one left no copy of it here (retention audit C2, 18 Nov 2026; the program's own feed or log is the other copy).

This tool merges the build's rows into the UTC day's file, `<day>.dropped.jsonl.gz`, beside the ledger's day file
(`<day>.jsonl.gz`, same release) and in the same plain-JSON-lines form. It is a separate asset rather than lines in the
ledger's own file so that the ledger's lines stay exactly the kit's tic-history/1 and its readers are untouched.
An item still malformed on the next build (the usual case: a feed keeps a bad item for days) is the same row, so it is
written once a day: a row is the same when program, id, place, reason and the item's hash are the same. Earlier lines
are never dropped or rewritten, only added to; the file is the old lines plus the new ones.

Not published, like the ledger: this tool refuses the folders that are deployed.
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ledger  # noqa: E402  (its _folder() refuses the deployed folders)


def digest(item) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, default=repr).encode("utf-8")).hexdigest()


def row_for(d: dict, item, at: str, source: str = "feed") -> dict:
    """One dropped feature as a log row. `d` is sift()'s row (id, program, index, reason)."""
    row = {"at": at, "source": source, "program": d.get("program"), "id": d.get("id"), "index": d.get("index"),
           "reason": d.get("reason"), "sha256": digest(item)}
    try:
        json.dumps(item, allow_nan=False)
        row["item"] = item
    except (ValueError, TypeError):   # NaN or Infinity in the item: JSON cannot hold it, so keep it as text
        row["item_text"] = json.dumps(item, default=repr, ensure_ascii=False)
    return row


def key(row: dict) -> tuple:
    return (row.get("source"), row.get("program"), row.get("id"), row.get("index"), row.get("reason"), row.get("sha256"))


def read_lines(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(s) for s in fh if s.strip()]


def merge(rows_path: Path, folder: Path, day: str) -> tuple[Path | None, int]:
    """Add the build's rows to <folder>/<day>.dropped.jsonl.gz. Returns (the file, rows added); (None, 0) when the
    build dropped nothing and the day has no file."""
    new = read_lines(rows_path)
    dest = Path(folder) / f"{day}.dropped.jsonl.gz"
    old = read_lines(dest)                    # a line that is not JSON raises: the old lines are never skipped
    held = {key(r) for r in old}
    added = []
    for r in new:
        if key(r) not in held:
            held.add(key(r))
            added.append(r)
    if not added:
        return (dest if dest.exists() else None), 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in old + added)
    with open(dest, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
        fh.write(text.encode("utf-8"))
    return dest, len(added)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, help="this build's rows (snapshot.py --dropped-out)")
    ap.add_argument("--assets", required=True, help="folder of the ledger's files (the day's existing file is read here)")
    ap.add_argument("--day", required=True, help="the snapshot's UTC day, YYYY-MM-DD")
    a = ap.parse_args(argv)
    folder = ledger._folder(a.assets)
    dest, n = merge(Path(a.rows), folder, a.day)
    print(f"dropped items {a.day}: {n} new row(s)" + (f", file {dest.name}" if dest else ", nothing to keep"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
