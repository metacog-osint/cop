"""Register one of outage triage's drafted questions in Verdict, with Tom's own probability (D6, Tom, 15 Nov 2026).

Every confirmed outage in triage.json carries two drafted questions: the OONI line (question_draft) and a second
resolver on cause (cause_question_draft: whether Cloudflare Radar, Access Now or two wire services name a government
order). Each has a verdict_id. One command registers one of them:

    python tools/register_question.py <verdict_id> <probability>             # e.g. ... 0.15
    python tools/register_question.py <verdict_id> <probability> --commit    # also commit it in Verdict (not push)
    python tools/register_question.py <verdict_id> <probability> --dry-run   # show the forecast, register nothing

It reads the published triage.json (or --triage <file>), builds a tic-feed/2 forecast with the probability given on
the command line, its ICD 203 term and the kit's rule-based confidence, checks it with Verdict's own rules, adds it
to <verdict>/drafts/cop-triage.json approved by --approved-by, and runs Verdict's register on that file. Verdict's CI
holds a new line's published_at to within an hour of the commit that adds it, so commit and push soon after; the
command prints both. It refuses a question that is not from a confirmed outage, one whose window has closed, and a
Verdict checkout that is behind origin/main or not on main.

Nothing here sets a probability: the number is the person's who runs it. COP's build never runs this.
Standard-library Python only.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage as tri  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from tic_doctrine import confidence, contract, likelihood  # noqa: E402

PUBLISHED = "https://cop.theintelligence.company/data/triage.json"
VERDICT = Path(r"[local path]")
DRAFTS = Path("drafts") / "cop-triage.json"
DRAFTS_NOTE = ("Questions drafted by COP's outage triage (D6). Each was added by tools/register_question.py in COP, "
               "with the probability and approval of the person who ran it, then registered.")
ALTERNATIVES = {
    "cause": [
        {"hypothesis": "A fault, a power cut, a cable break or an attack, with no government order",
         "tell_apart": "the cause Cloudflare's Outage Center states, and operator or ministry statements"},
        {"hypothesis": "A government order that none of the named sources reports inside the window",
         "tell_apart": "Access Now's later STOP data for the year"},
    ],
    "ooni": [
        {"hypothesis": "A network cut without new content blocking: OONI's probes go dark and the rate need not rise",
         "tell_apart": "OONI's measurement counts for the country falling in the window"},
        {"hypothesis": "Blocking that was already in place before the outage",
         "tell_apart": "OONI's share over the 120 hours before the window, which the rule compares against"},
    ],
}


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_triage(where):
    return tri.load(where)


def find(doc, verdict_id):
    """(record, public draft, what) for the drafted question with this verdict_id, or SystemExit."""
    for r in doc.get("records") or []:
        for key, what in (("question_draft", "ooni"), ("cause_question_draft", "cause")):
            q = r.get(key)
            if isinstance(q, dict) and q.get("verdict_id") == verdict_id:
                return r, q, what
    ids = [q["verdict_id"] for r in doc.get("records") or [] for q in (r.get("question_draft"),
                                                                       r.get("cause_question_draft"))
           if isinstance(q, dict) and q.get("verdict_id")]
    raise SystemExit(f"{verdict_id}: no drafted question with this id in triage.json (its outage may have left "
                     f"Blackout's feed). Ids there now: {', '.join(ids) or 'none'}")


def build_item(doc, record, q, what, probability, approved_by, when):
    """The tic-feed/2 forecast for one drafted question, with the given probability. Raises SystemExit on a question
    that should not be registered."""
    if not isinstance(probability, (int, float)) or not 0 < probability < 1:
        raise SystemExit("the probability must be a number between 0 and 1, set by the person registering it")
    if not record.get("registerable"):
        raise SystemExit(f"{q['verdict_id']}: its outage is not confirmed ({record.get('reading')}); D6 registers a "
                         "question on confirmed outages only")
    question, rule = q["question"], q["rule_draft"]
    res = q.get("resolution") or {}
    known = res.get("known_at")
    if not known or tri.utc(when) >= tri.utc(known):
        raise SystemExit(f"{q['verdict_id']}: its window has closed ({known}); a forecast is registered before its "
                         "outcome can be known")
    cc, o = record["country"], record["outage"]
    term = likelihood.term_for(probability)
    horizon = (tri.utc(known) - tri.utc(when)).total_seconds() / 86400
    conf = confidence.fields(confidence.Evidence(
        independent_lines=1, contradicted=False, alternatives_rejected=0, method_scored=False,
        ease_of_faking="low", horizon_days=round(horizon, 1)))
    vid = q["verdict_id"]
    item = {
        "contract": "tic-feed/2", "program": "verdict", "kind": "forecast", "tier": "routine", "status": "draft",
        "msg_type": "new", "human_review": "full", "us_person_data": "none", "status_class": "assessed",
        "product_category": "estimative", "data_as_of": doc.get("snapshot_generated_at") or when,
        "published_at": when, "change_from_prior": "new", "requirement_ids": ["VD-PIR-2"],
        "method_version": f"cop-outage-triage-{doc.get('join', {}).get('version', '?')} (outage question)",
        "geometry": None, "precision_class": "country", "ie_aspect": ["informational"],
        "pmesii_pt": ["infrastructure", "information"], "ncf": ["provide_internet_routing"],
        "probability": probability, "likelihood_term": term["id"], **conf,
        "id": vid, "title": question,
        "summary": (f"{probability:.0%} chance ({term['id'].replace('_', ' ')}): {question} Drafted by COP's outage "
                    f"triage from Blackout's {o.get('source') or 'outage'} item {o['id']}, which COP read "
                    f"\"{record.get('reading')}\"; the probability is {approved_by}'s."),
        "countries": [cc],
        "alternatives": ALTERNATIVES[what],
        "resolution": {"rule": rule, "source": res.get("source"), "resolve_by": res.get("resolve_by"),
                       "known_at": known, "check_after": known,
                       **({"void_if": res["void_if"]} if res.get("void_if") else {})},
        "valid_until": known,
        "sources": [{
            "id": "cop-outage-triage", "publisher": "The Intelligence Company (COP)", "url": PUBLISHED,
            "licence": "COP's own reading; each layer's items keep their programs' licences",
            "descriptor": f"triage record for {o['id']} ({cc}), snapshot {doc.get('snapshot_generated_at')}",
            "grade": "B2", "independence_group": "tic-cop", "accessed_at": when,
            "source_type": "derived analysis"}],
        "visual": {"type": "page", "url": "https://cop.theintelligence.company/"},
    }
    return item


def problems(item, verdict_root):
    """The kit contract's errors, and Verdict's own rules when its checkout can be loaded."""
    errs = [f"{std}: {m}" for std, sev, m in contract.check({**item, "status": "actual"}) if sev == "error"]
    tools = Path(verdict_root) / "tools"
    if (tools / "verdict.py").exists():
        sys.path.insert(0, str(tools))
        import verdict as vd  # noqa: E402
        regs = vd.registers.ids(Path(verdict_root) / "doctrine")
        errs += vd.forecast_problems({**item, "status": "actual", "approved_by": "check"}, regs)
    return sorted(set(errs))


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def checkout_ready(root):
    """None, or why this Verdict checkout must not take a new line (behind origin, or not on main)."""
    code, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if code:
        return f"{root} is not a git checkout"
    if branch != "main":
        return f"{root} is on {branch}, not main"
    git(root, "fetch", "-q", "origin")
    code, behind = git(root, "rev-list", "--count", "HEAD..origin/main")
    if not code and behind != "0":
        return f"{root} is {behind} commits behind origin/main: pull first (git -C \"{root}\" pull --ff-only)"
    return None


def add_draft(verdict_root, item, approved_by):
    path = Path(verdict_root) / DRAFTS
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"batch": "cop-triage",
                                                                              "note": DRAFTS_NOTE, "drafts": []}
    if any(d.get("item", {}).get("id") == item["id"] for d in doc["drafts"]):
        raise SystemExit(f"{item['id']}: already in {path}")
    doc["drafts"].append({"approved": approved_by, "item": item})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verdict_id", help="the drafted question's verdict_id, as triage.json and the page show it")
    ap.add_argument("probability", type=float, help="your probability, between 0 and 1")
    ap.add_argument("--approved-by", default="Tom Brummer", help="who set the probability (Verdict's approved_by)")
    ap.add_argument("--triage", default=PUBLISHED, help="triage.json: the published one (default) or a file")
    ap.add_argument("--verdict", default=str(VERDICT), help="your Verdict checkout")
    ap.add_argument("--dry-run", action="store_true", help="show the forecast and its checks; register nothing")
    ap.add_argument("--commit", action="store_true", help="also commit the new ledger line in Verdict (never pushes)")
    a = ap.parse_args(argv)
    when = now_utc()
    doc = load_triage(a.triage)
    record, q, what = find(doc, a.verdict_id)
    item = build_item(doc, record, q, what, a.probability, a.approved_by, when)
    errs = problems(item, a.verdict)
    print(json.dumps(item, indent=2, ensure_ascii=False))
    if errs:
        raise SystemExit("not registered, the forecast fails these checks:\n  " + "\n  ".join(errs))
    print(f"\nconfidence {item['confidence']} (the kit's rubric); probability {a.probability} "
          f"({item['likelihood_term']}), set by {a.approved_by}")
    if a.dry_run:
        print("dry run: nothing registered")
        return 0
    why = checkout_ready(a.verdict)
    if why:
        raise SystemExit("not registered: " + why)
    path = add_draft(a.verdict, item, a.approved_by)
    r = subprocess.run([sys.executable, "tools/verdict.py", "register", str(DRAFTS)], cwd=a.verdict)
    if r.returncode:
        raise SystemExit(f"Verdict's register failed; the approved draft stays in {path}")
    print(f"\nregistered {item['id']} in {Path(a.verdict) / 'ledger' / 'forecasts.jsonl'}")
    files = ["ledger/forecasts.jsonl", str(DRAFTS).replace("\\", "/")]
    msg = f"Register {item['id']} (COP outage triage, {a.approved_by})"
    if a.commit:
        git(a.verdict, "add", *files)
        code, out = git(a.verdict, "commit", "-m", msg)
        print(out if not code else f"commit failed: {out}")
        print(f"Push within the hour (Verdict's CI checks it): git -C \"{a.verdict}\" push origin main")
    else:
        print("Commit and push within the hour (Verdict's CI holds published_at to the commit):")
        print(f"  git -C \"{a.verdict}\" add {' '.join(files)}")
        print(f"  git -C \"{a.verdict}\" commit -m \"{msg}\"")
        print(f"  git -C \"{a.verdict}\" push origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
