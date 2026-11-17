"""The Undertow sanctions join: a Headwind list change that names an organisation Undertow's publishers name.

Headwind (`headwind:sanctions`) reads five official lists and, since headwind-1.9, names the people and organisations
each change adds, removes or changes (data/list-changes.json, kept 30 days). Undertow names five organisations as
operators, each only where a publisher names it (Social Design Agency, ANO Dialog, Struktura, Haimai, China Global
Television Network). This join puts the two side by side, in COP's voice:

  "Headwind saw <name as the list prints it> added to <list> (<programmes>) on <date>. <Publisher> names
   <organisation> in its disclosure of <operation>."

Nothing is inferred. Rules (Tom's yes, 16 Nov 2026, to the cross-suite joins plan):
  - Organisations only: a list entry is matched only when Headwind types it "entity". A person is never matched,
    even on an exact name.
  - Exact match against a hand-kept table (config/undertow-names.json), one row per organisation and list, giving
    the name as that list prints it. No free text, no fuzzy match. A table row whose entry id Headwind shows under
    another name is reported as stale and matches nothing.
  - A listing corroborates an operator, never an origin. Undertow's origin and confidence are not touched.
  - Declared inputs: undertow and headwind:sanctions. `published` is off until Tom turns it on.
  - Base rate: list changes naming any of the five, per month, from the listings Undertow records from the
    publishers' notices (each item's operation.listed_by), with Headwind's own window beside it.

    python tools/undertow_sanctions.py --snapshot site/data/today.geojson --list-changes out/headwind-list-changes.json \
        --out out/undertow-sanctions [--publish site/data]

Standard-library Python only. A failure never fails the build.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gate  # noqa: E402

JOIN = {"id": "undertow-sanctions", "version": "0.1", "published": False,
        "inputs": ["undertow", "headwind:sanctions"],
        "rule": "exact name match, organisations only, against a hand-kept table; nothing inferred"}
TABLE = ROOT / "config" / "undertow-names.json"
HEADWIND_PAGE = "https://headwind.theintelligence.company/#sanctions"
LISTS = {"ofac-sdn": "the OFAC SDN list", "eu-fsf": "the EU's sanctions list", "uk-sanctions": "the UK Sanctions List",
         "france-gels": "France's asset-freeze register", "canada-sema": "Canada's consolidated autonomous sanctions list"}
ACTION = {"named_added": "added to", "named_removed": "removed from", "named_changed": "changed on"}
NOT_A_FINDING = ("A listing says a government acted against the organisation. It corroborates the operator that "
                 "Undertow's publisher names; it is not a finding of origin, and it changes neither Undertow's origin "
                 "nor its confidence. COP shows what Headwind saw beside what the publisher names, nothing more.")
HEADWIND_NAMES_SINCE = "2026-11-16"   # headwind-1.9, the first method that names organisations on list changes


def load_table(path=TABLE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_table(table):
    """Refuse a row that is not an organisation Undertow keeps, a list Headwind does not name, or a duplicate."""
    errs, seen = [], set()
    orgs = set(table.get("organisations") or [])
    for r in table.get("rows") or []:
        key = (r.get("organisation"), r.get("list"))
        if r.get("organisation") not in orgs:
            errs.append(f"{key}: not one of the table's organisations")
        if r.get("list") not in LISTS:
            errs.append(f"{key}: unknown list")
        if not r.get("name") or not r.get("entry_id"):
            errs.append(f"{key}: name and entry_id are required")
        if key in seen:
            errs.append(f"{key}: one row per organisation and list")
        seen.add(key)
    return errs


def _features(snapshot):
    return snapshot.get("features") if isinstance(snapshot, dict) else snapshot or []


def undertow_items(snapshot):
    """{organisation: [Undertow items whose publisher names it]}, from each item's operation.sponsor."""
    out = {}
    for f in _features(snapshot):
        p = f.get("properties") or {}
        if p.get("program") != "undertow":
            continue
        op = p.get("operation") or {}
        for org in op.get("sponsor") or []:
            out.setdefault(org, []).append({
                "id": p["id"], "title": p.get("title"), "publisher": (p.get("sources") or [{}])[0].get("publisher"),
                "operation_name": op.get("operation_name"), "disclosure": (op.get("disclosure") or {}).get("title"),
                "url": (p.get("visual") or {}).get("url")})
    return out


def match(changes, table):
    """Every (change, entry, row) where Headwind names a table organisation exactly, and every stale row."""
    rows = {(r["list"], r["name"]): r for r in table.get("rows") or []}
    by_id = {(r["list"], r["entry_id"]): r for r in table.get("rows") or []}
    hits, stale = [], []
    for ch in changes or []:
        if ch.get("kind", "sanctions") != "sanctions" or ch.get("list") not in LISTS:
            continue
        for field, action in ACTION.items():
            for e in ch.get(field) or []:
                if e.get("type") != "entity":
                    continue          # organisations only: a person is never matched, even on an exact name
                row = rows.get((ch["list"], e.get("name")))
                if row:
                    hits.append({"change": ch, "entry": e, "row": row, "action": action})
                else:
                    other = by_id.get((ch["list"], str(e.get("list_id"))))
                    if other:
                        stale.append({"organisation": other["organisation"], "list": ch["list"],
                                      "entry_id": other["entry_id"], "table_name": other["name"],
                                      "headwind_name": e.get("name"), "change_id": ch.get("id")})
    return hits, stale


def sentence(hit, item):
    ch, e = hit["change"], hit["entry"]
    progs = ", ".join(e.get("programs") or []) or "no programme given"
    extra = ""
    if hit["action"] == "changed on":
        moved = [f"gained {', '.join(e['gained'])}" if e.get("gained") else "",
                 f"lost {', '.join(e['lost'])}" if e.get("lost") else "",
                 f"renamed from {e['name_before']}" if e.get("name_before") else ""]
        extra = " (" + "; ".join(m for m in moved if m) + ")" if any(moved) else ""
    seen = str(ch.get("detected_at") or "")[:10]
    return (f"Headwind saw {e.get('name')} {hit['action']} {LISTS[ch['list']]} ({progs}){extra} on {seen}. "
            f"{item['publisher']} names {hit['row']['organisation']} in its disclosure of "
            f"{item['operation_name'] or item['title']}.")


def base_rate(snapshot, changes, hits, today=None):
    """List changes naming any of the five, per month: the publishers' listing actions Undertow records, from the
    first to this month, beside the changes Headwind itself has seen since it began naming organisations."""
    today = today or datetime.now(timezone.utc).date()
    actions = set()
    for f in _features(snapshot):
        p = f.get("properties") or {}
        if p.get("program") != "undertow":
            continue
        for row in (p.get("operation") or {}).get("listed_by") or []:
            actions.add((row["list"], row["listed"]))
    seen_ids = {h["change"].get("id") for h in hits}
    out = {"headwind_names_since": HEADWIND_NAMES_SINCE, "headwind_changes_naming_any": len(seen_ids),
           "headwind_changes_read": len(changes or []),
           "caveat": ("Counts listing actions only, one per list and date. Amendments that change an entry's details "
                      "(the EU's 2024/2455 of 12 Sep 2024 for Struktura and ANO Dialog) are not counted, and Headwind "
                      "compares type and programmes only, so it does not see them either.")}
    if not actions:
        out.update({"listing_actions": 0, "per_month": None,
                    "sentence": "No listing of these organisations is on file in Undertow's feed, so no base rate."})
        return out
    first = min(d for _, d in actions)
    y, m = int(first[:4]), int(first[5:7])
    months = (today.year - y) * 12 + today.month - m + 1
    last = max(d for _, d in actions)
    per = round(len(actions) / months, 2)
    out.update({"listing_actions": len(actions), "months": months, "first": first, "last": last, "per_month": per,
                "sentence": (f"{len(actions)} listing actions named any of the five from {first} to {today.isoformat()} "
                             f"({months} months): {per} a month. The last was on {last}. Headwind has seen "
                             f"{len(seen_ids)} list change{'s' if len(seen_ids) != 1 else ''} naming any of them since "
                             f"it began naming organisations on {HEADWIND_NAMES_SINCE}.")})
    return out


def run(snapshot, list_changes, table=None, today=None):
    table = table or load_table()
    errs = check_table(table)
    if errs:
        raise SystemExit("undertow-names.json: " + "; ".join(errs))
    changes = (list_changes or {}).get("changes") or []
    hits, stale = match(changes, table)
    items = undertow_items(snapshot)
    out = []
    for h in hits:
        org = h["row"]["organisation"]
        ch, e = h["change"], h["entry"]
        beside = items.get(org) or []
        out.append({
            "organisation": org, "list": ch["list"], "list_name": LISTS[ch["list"]], "action": h["action"],
            "name_as_printed": e.get("name"), "programmes": e.get("programs") or [], "seen_on": ch.get("detected_at"),
            "headwind": {"id": f"headwind:sanctions:{ch['id']}", "url": HEADWIND_PAGE},
            "undertow": [{"id": i["id"], "publisher": i["publisher"], "operation_name": i["operation_name"],
                          "url": i["url"]} for i in beside],
            "sentences": [sentence(h, i) for i in beside] or [
                f"Headwind saw {e.get('name')} {h['action']} {LISTS[ch['list']]} on {str(ch.get('detected_at'))[:10]}. "
                f"No Undertow item in this snapshot names {org}."]})
    return {"join": dict(JOIN), "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "list_changes_generated_at": (list_changes or {}).get("generated_at"),
            "matches": out, "stale_rows": stale, "base_rate": base_rate(snapshot, changes, hits, today),
            "table": {"read_on": table.get("read_on"), "checked_by": table.get("checked_by") or None,
                      "organisations": table.get("organisations"), "rows": len(table.get("rows") or [])},
            "not_a_finding": NOT_A_FINDING}


def _read(path_or_url):
    if str(path_or_url).startswith("https://"):
        import urllib.request
        req = urllib.request.Request(path_or_url, headers={"User-Agent": "TIC-COP/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(path_or_url).read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--list-changes", required=True, help="Headwind's data/list-changes.json, a path or an https URL")
    ap.add_argument("--out", default=str(ROOT / "out" / "undertow-sanctions"))
    ap.add_argument("--publish", help="site/data: written only when the join's own published switch is on")
    a = ap.parse_args(argv)
    doc = run(_read(a.snapshot), _read(a.list_changes))
    v = gate.write(JOIN, doc, a.out, ["undertow-sanctions.json"])
    print(f"undertow-sanctions: {len(doc['matches'])} matches, {len(doc['stale_rows'])} stale rows; "
          f"{doc['base_rate']['sentence']} ({v['review_class']})")
    if a.publish:
        if JOIN["published"]:
            gate.write(JOIN, doc, a.publish, ["undertow-sanctions.json"])
        else:
            print("undertow-sanctions: published is off (Tom's call); nothing written to " + a.publish)
    return 0


if __name__ == "__main__":
    sys.exit(main())
