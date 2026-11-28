"""The write path for COP's joins: what a join must state before it is written, and when it may publish.

A join places several programs' public items around one place and time. Until 15 Nov 2026 this module also held
back any join that read layers it listed as military context (the mosaic gate). That rule was retired on 15 Nov 2026:
"We operate exclusively with publicly available information and our objective is to show what can be done with it.
That doesn't mean we have to spell it out, though." Every layer is now usable in a join. What replaces the lists is a
rule of voice, kept in each join's own wording: a join shows each layer's measurement side by side, with a base rate
and a descriptive reading, and never states the inference (intent, a strike, the timing of forces) in COP's voice.

What this module still checks, so no join decides it for itself:

  inputs     the join declares the layers it reads (JOIN["inputs"]), each one a COP program or Verdict.
  citations  every item id the join's document cites belongs to a declared input. A join that cites a layer it never
             declared is refused, so its stated inputs and its credits can be trusted.
  publish    a join reaches site/, functions/ or mcp/ only when its own "published" switch is on (the suite's rule, per
             join).

    python tools/gate.py --check        # fail if anything under site/ is a refused or unpublished join (run by the build)

Standard-library Python only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOYED = ("site", "functions", "mcp")    # folders the build ships: site/ as pages, the other two as the MCP server

_ITEM_ID = re.compile(r"^([a-z]+)(?:[:\-]([^:]*))?")
# Programs whose item ids start with an upper-case prefix instead of the program's name. Undertow's are
# "UT-<LANE>-<key>" ("UT-GOOGLE-fe9985db2a"), Relief's "RL-<LANE>-<key>" ("RL-IFRC-d855019585"); the lane is
# read as the kind, in lower case. An upper-case prefix not
# listed here names no program, so a join citing it is refused.
ID_PREFIXES = {"UT": "undertow", "RL": "relief"}
_PREFIXED_ID = re.compile(r"^([A-Z]+)-([A-Z0-9]+)-")


# Suite programs COP reads but does not draw as a layer. Verdict's calendar and ledger reach COP through
# agent_index.py (verdict.json) and, from 18 Nov 2026, the Undertow election join, which cites calendar events by
# their feed ids ("verdict:calendar:<id>"). A join may declare one as an input like any layer.
READ_NOT_DRAWN = {"verdict"}


def programs():
    """The program ids a join may declare: those COP draws (config/programs.json) and READ_NOT_DRAWN."""
    cfg = json.loads((ROOT / "config" / "programs.json").read_text(encoding="utf-8"))
    return {p["id"] for p in cfg.get("programs", [])} | READ_NOT_DRAWN


def layer_of(item_id):
    """(program, kind) of a COP item id. Noisefloor's ids use hyphens and have no kind; Undertow's use its prefix."""
    p = _PREFIXED_ID.match(str(item_id))
    if p and p.group(1) in ID_PREFIXES:
        return ID_PREFIXES[p.group(1)], p.group(2).lower()
    m = _ITEM_ID.match(str(item_id))
    if not m:
        return "", ""
    program = m.group(1)
    kind = (m.group(2) or "") if ":" in str(item_id) else ""
    return program, kind


COVERAGE_GROUP_KEYS = ("groups", "set_aside", "not_counted")


def coverage_row(node):
    """A row of the kit's coverage table (format tic-coverage/1): its groups, set_aside and not_counted (kit 0.6.17)
    list source groups (a publisher or an independence group, with the programs that carry it), not items."""
    return (isinstance(node, dict) and isinstance(node.get("place"), str) and isinstance(node.get("independent"), int)
            and isinstance(node.get("groups"), list) and isinstance(node.get("set_aside"), list))


def cited_ids(doc):
    """Every item id a join's document cites: any string under a key named "id", outside the join and gate blocks and
    outside a coverage row's source groups (whose ids name publishers, never items)."""
    found = set()

    def walk(node, top=False):
        if isinstance(node, dict):
            groups = coverage_row(node)
            for k, v in node.items():
                if top and k in ("join", "gate"):
                    continue
                if groups and k in COVERAGE_GROUP_KEYS:
                    continue
                if k == "id" and isinstance(v, str):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc, top=True)
    return found


def review(join, doc=None):
    """The verdict on a join and, when given, the document it produced: "open" when its inputs and citations are in
    order, "refused" otherwise. may_publish also needs the join's own switch."""
    known = programs()
    reasons = []
    inputs = join.get("inputs")
    if not inputs:
        reasons.append("the join declares no inputs")
    for entry in inputs or []:
        program = str(entry).partition(":")[0]
        if program not in known:
            reasons.append(f"declared input: {program or 'an unnamed layer'} is not a COP program")
    declared = {str(e) for e in inputs or []}
    for item_id in sorted(cited_ids(doc) if doc is not None else ()):
        program, kind = layer_of(item_id)
        if program not in known:
            reasons.append(f"cites {item_id}: it names no COP program")
        elif program not in declared and f"{program}:{kind}" not in declared:
            reasons.append(f"cites {item_id}: {program}{':' + kind if kind else ''} is not among the join's "
                           "declared inputs")
    reasons = sorted(set(reasons))
    cls = "refused" if reasons else "open"
    return {"review_class": cls, "reasons": reasons,
            "may_publish": cls == "open" and join.get("published") is True,
            "rule": "a join publishes when its inputs and citations are in order and its own switch is on"}


def _inside(path, folder):
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents


def allowed(verdict, folder):
    """None if a document with this verdict may be written in folder, or the reason it may not."""
    if verdict["review_class"] != "open":
        return "the join's inputs or citations are not in order"
    folder = Path(folder).resolve()
    deployed = any(_inside(folder, ROOT / d) for d in DEPLOYED)
    if deployed and not verdict["may_publish"]:
        return f"a join that is not switched on to publish is not written inside {', '.join(d + '/' for d in DEPLOYED)}"
    return None


def write(join, doc, folder, names):
    """Stamp doc with the verdict and write it as each of names in folder, or refuse (SystemExit)."""
    verdict = review(join, doc)
    refusal = allowed(verdict, folder)
    if refusal:
        raise SystemExit(f"{join.get('id', 'join')}: {refusal}. " + "; ".join(verdict["reasons"]))
    doc["gate"] = verdict
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    for name in names:
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
    return verdict


def check(folders=None):
    """Offences among the deployed folders: any JSON there that is a join that may not publish."""
    offences = []
    for folder in folders or [ROOT / d for d in DEPLOYED]:
        for path in sorted(Path(folder).rglob("*")):
            if path.suffix not in (".json", ".geojson") or "node_modules" in path.parts or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if '"review_class"' not in text and '"join"' not in text:
                continue
            try:
                doc = json.loads(text)
            except ValueError:
                continue
            join = doc.get("join") if isinstance(doc, dict) else None
            if not isinstance(join, dict):
                continue
            verdict = review(join, doc)    # judged afresh: a stamp in the file is not trusted
            if not verdict["may_publish"]:
                offences.append((str(path), verdict["review_class"], verdict["reasons"]))
    return offences


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if a refused or unpublished join sits in a deployed folder")
    a = ap.parse_args(argv)
    if not a.check:
        ap.print_help()
        return 0
    offences = check()
    for path, cls, reasons in offences:
        why = "; ".join(reasons) or "its publish switch is off"
        print(f"GATE: {path} is a join that may not be deployed ({cls}). {why}")
    if not offences:
        print("gate: no refused or unpublished join in " + ", ".join(d + "/" for d in DEPLOYED))
    return 1 if offences else 0


if __name__ == "__main__":
    sys.exit(main())
