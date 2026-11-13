"""The review gate for COP's joins: which may publish, and which are held.

A join places several programs' public items around one place and time. Most of that is harmless. It stops being
harmless when the join places public signals around military activity, because the picture then says more than any
one source did (the mosaic effect). This module decides that in code, so no join decides it for itself:

  open   every layer the join reads, and every item it cites, is on the OPEN list. It may publish once its own
         "published" switch is on (Tom's call, per join).
  held   anything else: a layer on the MILITARY list, or a layer or kind this file has never heard of. A held join is
         written only under out/held/, which git ignores and the build never deploys. There is no switch in code that
         publishes a held join; releasing one is a person's act, outside this repo's build.

A join's class is the stricter of two readings: the layers it declares in JOIN["inputs"], and the item ids found in
the document it produced. Declaring "open" and then citing a Noisefloor item is caught by the second reading.

    python tools/gate.py --check        # fail if anything under site/ is a held or unpublished join (run by the build)

Standard-library Python only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOYED = ("site", "functions", "mcp")    # folders the build ships: site/ as pages, the other two as the MCP server
HELD_DIR = ROOT / "out" / "held"

# Layers by program, then by item kind (the second part of an item id). None means every kind of that program.
# A program or kind on neither list is held: a new layer is reviewed before any join may publish with it.
OPEN = {
    "elements": None,                              # natural hazards
    "blackout": None,                              # internet outages, censorship measurements, provider status
    "watchfloor": None,                            # press attention
    "context": None,                               # press framing
    "vigil": None,                                 # ransomware claims, exploited vulnerabilities
    "cordon": {"advisory", "alert", "posture"},    # travel advisories, embassy alerts, embassy operating status
}
MILITARY = {
    "noisefloor": (None, "GPS interference: most of it comes from military jamming and spoofing"),
    "vis": (None, "operational weather and night light in conflict zones: when forces can fly or move"),
    "cordon": ({"navwarn", "czib"}, "navigation warnings and conflict-zone airspace bulletins: firing areas, "
                                    "exercises and threats to aircraft"),
}

_ITEM_ID = re.compile(r"^([a-z]+)(?:[:\-]([^:]*))?")


def layer_of(item_id):
    """(program, kind) of a COP item id. Noisefloor's ids use hyphens and have no kind."""
    m = _ITEM_ID.match(str(item_id))
    if not m:
        return "", ""
    program = m.group(1)
    kind = (m.group(2) or "") if ":" in str(item_id) else ""
    return program, kind


def layer_class(program, kind=None):
    """("open" | "held", reason). kind None asks about the whole program."""
    if program in MILITARY:
        kinds, why = MILITARY[program]
        if kinds is None or kind in kinds:
            return "held", f"{program}{':' + kind if kind else ''} is military context ({why})"
    if program in OPEN:
        kinds = OPEN[program]
        if kinds is None or kind in kinds:
            return "open", ""
        if not kind:
            return "held", f"{program} has kinds that are military context: name the kinds this join reads"
        return "held", f"{program}:{kind} is not on the open list (unreviewed kind)"
    return "held", f"{program or 'an unnamed layer'} is not on the open list (unreviewed layer)"


def cited_ids(doc):
    """Every item id a join's document cites: any string under a key named "id", outside the join and gate blocks."""
    found = set()

    def walk(node, top=False):
        if isinstance(node, dict):
            for k, v in node.items():
                if top and k in ("join", "gate"):
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
    """The gate's verdict on a join and, when given, the document it produced."""
    reasons = []
    inputs = join.get("inputs")
    if not inputs:
        reasons.append("the join declares no inputs")
    for entry in inputs or []:
        program, _, kind = str(entry).partition(":")
        cls, why = layer_class(program, kind or None)
        if cls == "held":
            reasons.append("declared input: " + why)
    declared = {str(e) for e in inputs or []}
    for item_id in sorted(cited_ids(doc) if doc is not None else ()):
        program, kind = layer_of(item_id)
        cls, why = layer_class(program, kind or None)
        if cls == "held":
            reasons.append(f"cites {item_id}: {why}")
        elif program not in declared and f"{program}:{kind}" not in declared:
            reasons.append(f"cites {item_id}: {program}{':' + kind if kind else ''} is not among the join's "
                           "declared inputs")
    reasons = sorted(set(reasons))
    cls = "held" if reasons else "open"
    return {"review_class": cls, "reasons": reasons,
            "may_publish": cls == "open" and join.get("published") is True,
            "rule": "open joins publish when switched on; held joins stay under out/held/ and never reach the "
                    "site or the MCP server"}


def _inside(path, folder):
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents


def allowed(verdict, folder):
    """None if a document with this verdict may be written in folder, or the reason it may not."""
    folder = Path(folder).resolve()
    deployed = any(_inside(folder, ROOT / d) for d in DEPLOYED)
    if deployed and not verdict["may_publish"]:
        what = "a held join" if verdict["review_class"] == "held" else "a join that is not switched on to publish"
        return f"the gate does not write {what} inside {', '.join(d + '/' for d in DEPLOYED)}"
    if verdict["review_class"] == "held" and _inside(folder, ROOT) and not _inside(folder, HELD_DIR):
        return "the gate writes a held join only under out/held/"
    return None


def write(join, doc, folder, names):
    """Stamp doc with the gate's verdict and write it as each of names in folder, or refuse (SystemExit)."""
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
    """Offences among the deployed folders: any JSON there that is a join the gate would not publish."""
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
    ap.add_argument("--check", action="store_true", help="fail if a held or unpublished join sits in a deployed folder")
    a = ap.parse_args(argv)
    if not a.check:
        ap.print_help()
        return 0
    offences = check()
    for path, cls, reasons in offences:
        print(f"GATE: {path} is a {cls} join and must not be deployed. " + "; ".join(reasons))
    if not offences:
        print("gate: no held or unpublished join in " + ", ".join(d + "/" for d in DEPLOYED))
    return 1 if offences else 0


if __name__ == "__main__":
    sys.exit(main())
