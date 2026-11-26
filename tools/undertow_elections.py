"""The Undertow election join: reported influence activity against a country in the 90 days before its national vote.

Verdict's event calendar (`verdict:calendar`) holds national elections, kind `election`, hand-entered from each
electoral body's own page for the 40 countries Undertow's items name as stated targets (18 Nov 2026).
Undertow (`undertow`) records operations that platforms, the EEAS and named reporters disclosed or reported. This join
puts the two side by side, in COP's voice:

  "<Country> voted on <date>; <publisher> reported <operation> targeting <country> from <a> to <b> days before."

("votes on" for a vote still to come.) Nothing is inferred: no intent, and no claim that the operation aimed at the
vote. Rules (16 Nov 2026; extended 18 Nov 2026):
  - A match is an Undertow item whose stated target (operation.targets) is a country with a national vote on the
    calendar, whose period overlaps the window: 90 days before the vote through election day (provisional).
  - The item's time is when the activity ran, never when it was reported: a watch row's activity.activity_period,
    or a disclosure's own period (operation.period: "2026-03", "2026-01 to 2026-06", "H2 2025", "2025"), cut at the
    disclosure's publication date. A reporter's item that carries only report dates is left out and counted.
  - Each match keeps the period's length and whether it lies inside the window or only overlaps it: a half-year
    disclosure period overlaps a window far more easily than a month does, so a long period is weaker evidence of
    timing. Nothing is dropped for being long.
  - Declared inputs: undertow and verdict:calendar. `published` is off until the owner turns it on.
  - Base rate: the share of item-country pairs whose period overlaps a window in that country, against the share of
    days that fall in a window, and against the chance that a period of the same length placed at random overlaps
    one. A described measure, never a rule: each country has few votes.

    python tools/undertow_elections.py --snapshot site/data/today.geojson \
        --calendar https://metacog-osint.github.io/verdict/data/calendar.geojson --out out/undertow-elections \
        [--publish site/data]

Standard-library Python only. A failure never fails the build.
"""

import argparse
import calendar as cal
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
import gate  # noqa: E402
from tic_doctrine import contract  # noqa: E402

JOIN = {"id": "undertow-elections", "version": "0.1", "published": False,
        "inputs": ["undertow", "verdict:calendar"],
        "rule": ("an Undertow item's activity or disclosure period overlaps the 90 days before a national vote in a "
                 "country it states as a target; nothing inferred")}
SCOPE = ROOT / "config" / "undertow-elections.json"
VERDICT_CALENDAR = "https://metacog-osint.github.io/verdict/data/calendar.geojson"
WINDOW_DAYS = 90          # provisional (16 Nov 2026)
NOT_A_FINDING = ("This join shows that a publisher reported activity against a country in the 90 days before a "
                 "national vote there. It does not say the activity aimed at the vote, who directed it, or that it "
                 "changed anything. Undertow's origin and confidence are not touched. Dates of votes come from each "
                 "electoral body's own page through Verdict's calendar; Undertow's periods are its publishers' own.")
VOTE_WORDS = {"presidential": "a presidential election", "legislative": "a parliamentary election",
              "referendum": "a national referendum"}
VOTE_WORDS_BY_COUNTRY = {("US", "legislative"): "an election to the House of Representatives"}


def load_scope(path=SCOPE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _features(doc):
    return doc.get("features") if isinstance(doc, dict) else doc or []


def _day(s):
    return date.fromisoformat(str(s)[:10])


def _month_end(y, m):
    return date(y, m, cal.monthrange(y, m)[1])


def _bound(text, end=False):
    """One end of a period: 'YYYY-MM-DD', 'YYYY-MM' or 'YYYY'. A month or year opens at its first day and closes
    at its last."""
    t = str(text).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return date.fromisoformat(t)
    if re.fullmatch(r"\d{4}-\d{2}", t):
        y, m = int(t[:4]), int(t[5:7])
        return _month_end(y, m) if end else date(y, m, 1)
    if re.fullmatch(r"\d{4}", t):
        return date(int(t), 12, 31) if end else date(int(t), 1, 1)
    raise ValueError(t)


def parse_period(text):
    """(from, to) of a period as Undertow writes it, or None if it cannot be read."""
    t = str(text or "").strip()
    try:
        m = re.fullmatch(r"H([12]) (\d{4})", t)
        if m:
            y = int(m.group(2))
            return (date(y, 1, 1), date(y, 6, 30)) if m.group(1) == "1" else (date(y, 7, 1), date(y, 12, 31))
        m = re.fullmatch(r"Q([1-4]) (\d{4})", t)
        if m:
            q, y = int(m.group(1)), int(m.group(2))
            return date(y, 3 * q - 2, 1), _month_end(y, 3 * q)
        if " to " in t:
            a, b = t.split(" to ", 1)
            return _bound(a), _bound(b, end=True)
        return _bound(t), _bound(t, end=True)
    except ValueError:
        return None


def item_period(p):
    """(from, to, source, as written) for an Undertow item, or (None, reason) when it has no period this join may use.

    A watch row's activity_period is when the activity ran. A disclosure's operation.period is its own period, cut at
    the date it was published (a disclosure cannot describe days after it). A reporter's item with an activity block
    and no activity_period has only report dates, which this join never uses. An item that states event_start
    (feed contract revision 2.1: when what it describes ran in the world) is read from that first, by the kit's
    contract.event_interval, cut at its data_as_of like a disclosure."""
    act = p.get("activity") or {}
    published = _day(p["data_as_of"]) if p.get("data_as_of") else None
    span = contract.event_interval(p)
    if span is not None and span[0] is not None:
        start, end = span
        exclusive = p.get("event_time_precision") in ("hour", "day", "month") and end > start
        a, b = start.date(), (end - timedelta(microseconds=1)).date() if exclusive else end.date()
        if published and b > published:
            b = max(a, published)
        return (a, b, "event time", f"{p.get('event_start')} to {p.get('event_end') or 'data_as_of'}"), None
    if act.get("activity_period"):
        ap = act["activity_period"]
        try:
            a, b = _bound(ap["from"]), _bound(ap["to"], end=True)
        except (KeyError, ValueError):
            return None, "unreadable activity period"
        return (a, b, "activity period", f"{ap['from']} to {ap['to']}"), None
    if act:
        return None, "report dates only"
    per = (p.get("operation") or {}).get("period")
    span = parse_period(per)
    if not span:
        return None, "no readable period"
    a, b = span
    if published and b > published:
        b = max(a, published)
    return (a, b, "disclosure period", per), None


def elections(calendar_doc):
    """Verdict calendar items of kind election, one per country it names: {country: [election, ...]}, oldest first."""
    out = {}
    for f in _features(calendar_doc):
        p = f.get("properties") or {}
        ev = p.get("event") or {}
        if ev.get("kind") != "election" or not ev.get("date"):
            continue
        ref = str(ev.get("ref") or "")
        parts = ref.split(":")           # election:<CC>:<vote>:<round>
        vote = parts[2] if len(parts) > 2 else ""
        rnd = parts[3] if len(parts) > 3 else ""
        src = (p.get("sources") or [{}])[0]
        for cc in p.get("countries") or []:
            out.setdefault(cc, []).append({
                "id": p["id"], "country": cc, "date": ev["date"], "vote": vote, "round": rnd,
                "title": p.get("title"), "occurred": bool(ev.get("occurred")), "verified": ev.get("verified", True),
                "note": ev.get("note"), "publisher": src.get("publisher"), "url": src.get("url")})
    for cc in out:
        out[cc].sort(key=lambda e: e["date"])
    return out


def _days(n):
    return f"{n} day{'' if n == 1 else 's'}"


def _span_words(a_days, b_days):
    """Where the period sits against the vote: a_days from its start, b_days from its end (negative: after)."""
    if a_days == 0 and b_days == 0:
        return "on election day"
    if b_days == 0:
        return f"from {_days(a_days)} before to election day"
    if b_days > 0:
        return f"{_days(a_days)} before" if a_days == b_days else f"from {a_days} to {_days(b_days)} before"
    return f"from {_days(a_days)} before to {_days(-b_days)} after"


def sentence(el, item, names):
    country = names.get(el["country"], el["country"])
    verb = "voted" if el["occurred"] else "votes"
    when = _day(el["date"])
    vote = VOTE_WORDS_BY_COUNTRY.get((el["country"], el["vote"])) or VOTE_WORDS.get(el["vote"])
    rnd = {"first": " (first round)", "second": " (second round)"}.get(el["round"], "")
    what = f" in {vote}{rnd}" if vote else ""
    span = _span_words((when - item["period_from"]).days, (when - item["period_to"]).days)
    tail = "" if el["verified"] else " The vote's date is not yet read on an official page."
    publisher = item["publisher"] + ("," if "," in str(item["publisher"]) else "")
    text = (f"{country} {verb} on {el['date']}{what}; {publisher} reported {item['operation']} targeting "
            f"{country} {span} ({item['period_source']} {item['period_as_written']}).{tail}")
    return text[0].upper() + text[1:]


def undertow_items(snapshot, scope, today):
    """Undertow items with a stated target, each with its usable period; and what was left out, and why."""
    start = _day(scope["calendar_from"])
    countries = set(scope["countries"])
    kept, left = [], {"report dates only": 0, "no readable period": 0, "unreadable activity period": 0,
                      "period begins before the calendar": 0, "target not on the calendar": {}}
    for f in _features(snapshot):
        p = f.get("properties") or {}
        if p.get("program") != "undertow":
            continue
        op = p.get("operation") or {}
        targets = [t for t in op.get("targets") or [] if isinstance(t, str)]
        if not targets:
            continue
        per, why = item_period(p)
        if why:
            left[why] += 1
            continue
        a, b, source, written = per
        b = min(b, today)
        if a < start:
            left["period begins before the calendar"] += 1
            continue
        on = [t for t in targets if t in countries]
        for t in targets:
            if t not in countries:
                left["target not on the calendar"][t] = left["target not on the calendar"].get(t, 0) + 1
        if not on:
            continue
        name = op.get("operation_name") or (p.get("activity") or {}).get("operation_name")
        kept.append({"id": p["id"], "publisher": (p.get("sources") or [{}])[0].get("publisher"),
                     "operation": name or f"the operation in \"{p.get('title')}\"", "operation_name": name,
                     "title": p.get("title"), "url": (p.get("visual") or {}).get("url"), "targets": on,
                     "period_from": a, "period_to": b, "period_days": (b - a).days + 1,
                     "period_source": source, "period_as_written": written})
    return kept, left


def windows(els):
    return [(_day(e["date"]) - timedelta(days=WINDOW_DAYS), _day(e["date"])) for e in els]


def _overlaps(a, b, wins):
    return any(a <= we and b >= ws for ws, we in wins)


def base_rate(items, by_country, scope, today):
    """Share of item-country pairs in a window, against the share of days in a window and the chance that a period of
    the same length, placed at random in the calendar's span, overlaps one."""
    start = _day(scope["calendar_from"])
    span_days = (today - start).days + 1
    pairs = in_win = 0
    day_share, random_share = [], []
    for it in items:
        for cc in it["targets"]:
            wins = windows(by_country.get(cc, []))
            pairs += 1
            if _overlaps(it["period_from"], it["period_to"], wins):
                in_win += 1
            in_days = sum(1 for k in range(span_days) if _overlaps(start + timedelta(k), start + timedelta(k), wins))
            day_share.append(in_days / span_days)
            length = it["period_days"]
            slots = max(1, span_days - length + 1)
            hits = sum(1 for k in range(slots)
                       if _overlaps(start + timedelta(k), start + timedelta(k + length - 1), wins))
            random_share.append(hits / slots)
    if not pairs:
        return {"pairs": 0, "sentence": "No Undertow item with a usable period names a country on the calendar, so no "
                                         "base rate."}
    share = in_win / pairs
    days = sum(day_share) / pairs
    rnd = sum(random_share) / pairs
    return {"pairs": pairs, "in_window": in_win, "share_in_window": round(share, 3),
            "share_of_days_in_window": round(days, 3), "share_if_placed_at_random": round(rnd, 3),
            "from": start.isoformat(), "to": today.isoformat(), "window_days": WINDOW_DAYS,
            "sentence": (f"{in_win} of {pairs} item-country pairs ({share:.0%}) have a period that overlaps the "
                         f"{WINDOW_DAYS} days before a national vote in that country. Over {start.isoformat()} to "
                         f"{today.isoformat()}, {days:.0%} of those countries' days fell in such a window, and a period "
                         f"of the same length placed at random would overlap one {rnd:.0%} of the time. A described "
                         "measure, not a rule: each country holds few votes, so the figure moves with every item."),
            "caveat": ("Pairs, not operations: an item naming four target countries counts four times. Long periods "
                       "(a half-year disclosure) overlap windows easily, which the third figure allows for. Countries "
                       "outside the calendar's scope and items whose period begins before it are left out.")}


def run(snapshot, calendar_doc, scope=None, today=None):
    scope = scope or load_scope()
    today = today or datetime.now(timezone.utc).date()
    by_country = elections(calendar_doc)
    items, left = undertow_items(snapshot, scope, today)
    names = scope.get("names") or {}
    matches = {}
    for it in items:
        for cc in it["targets"]:
            for el in by_country.get(cc, []):
                ws, we = _day(el["date"]) - timedelta(days=WINDOW_DAYS), _day(el["date"])
                if not (it["period_from"] <= we and it["period_to"] >= ws):
                    continue
                fit = "inside" if it["period_from"] >= ws and it["period_to"] <= we else "overlaps"
                m = matches.setdefault(el["id"], {"election": {k: el[k] for k in (
                    "id", "country", "date", "vote", "round", "title", "occurred", "verified", "note", "publisher",
                    "url")}, "window": {"from": ws.isoformat(), "to": we.isoformat()}, "undertow": []})
                m["undertow"].append({
                    "id": it["id"], "publisher": it["publisher"], "operation_name": it["operation_name"],
                    "title": it["title"], "url": it["url"], "period_source": it["period_source"],
                    "period": it["period_as_written"], "period_from": it["period_from"].isoformat(),
                    "period_to": it["period_to"].isoformat(), "period_days": it["period_days"], "fit": fit,
                    "sentence": sentence(el, it, names)})
    out = sorted(matches.values(), key=lambda m: (m["election"]["date"], m["election"]["country"]))
    n_el = sum(len(v) for k, v in by_country.items() if k in set(scope["countries"]))
    return {"join": dict(JOIN), "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calendar_generated_at": (calendar_doc or {}).get("generated_at") if isinstance(calendar_doc, dict) else None,
            "scope": {"calendar_from": scope["calendar_from"], "calendar_to": scope["calendar_to"],
                      "countries": len(scope["countries"]), "read_on": scope.get("read_on"),
                      "elections_on_calendar": n_el,
                      "countries_with_no_vote": sorted(set(scope["countries"]) - set(by_country))},
            "matches": out, "items_considered": len(items),
            "left_out": left, "base_rate": base_rate(items, by_country, scope, today), "not_a_finding": NOT_A_FINDING}


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
    ap.add_argument("--calendar", default=VERDICT_CALENDAR, help="Verdict's calendar.geojson, a path or an https URL")
    ap.add_argument("--out", default=str(ROOT / "out" / "undertow-elections"))
    ap.add_argument("--publish", help="site/data: written only when the join's own published switch is on")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today, UTC)")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else None
    doc = run(_read(a.snapshot), _read(a.calendar), today=today)
    v = gate.write(JOIN, doc, a.out, ["undertow-elections.json"])
    n = sum(len(m["undertow"]) for m in doc["matches"])
    print(f"undertow-elections: {len(doc['matches'])} votes with {n} matches, "
          f"{doc['scope']['elections_on_calendar']} votes on the calendar; {doc['base_rate']['sentence'][:160]} "
          f"({v['review_class']})")
    if a.publish:
        if JOIN["published"]:
            gate.write(JOIN, doc, a.publish, ["undertow-elections.json"])
        else:
            print("undertow-elections: published is off; nothing written to " + a.publish)
    return 0


if __name__ == "__main__":
    sys.exit(main())
