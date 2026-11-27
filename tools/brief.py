"""COP's daily brief: what changed across the suite in five sentences, on one page and in an Atom feed.

Once a UTC day, at the first build at or after BRIEF_HOUR (12:00 UTC, 6 AM CST), COP writes that day's brief from
the snapshot it has just built: up to four sentences on what changed, each the strongest new or changed measurement
of one program, read against that program's own baseline, and one sentence on a dated event ahead. Every sentence
links to the item behind it and names its source publisher. A brief is never replaced: a day that already has one
keeps it, and every past brief stays on the page and in the feed (Tom, 23 Nov 2026, ideas#259).

What a sentence may say (the voice rule, as for COP's joins): the item's own title, the baseline its program measured
it against, and who published the data. Never a cause, an intent, a likelihood or a warning in COP's voice. Vis
forecasts are never in the brief (D12), nor anything stale, expired, ended, unplaced or unchanged.

    python tools/brief.py --snapshot site/data/today.geojson --events site/data/agent/events.json \
        --verdict site/data/agent/verdict.json --store out/briefs --publish site/brief [--new-out out/brief-new.txt]
    python tools/brief.py --store out/briefs --publish site/brief --render-only

The store (out/briefs/<day>.json) is the release "briefs-YYYY"; build.yml downloads it first, uploads a new day's
file without --clobber, and publishes a new brief only once its upload succeeded. Standard-library Python only.
"""

import argparse
import html
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)
sys.path.insert(0, str(ROOT / "tools"))
from country_names import with_country_names  # noqa: E402

CONTRACT = "cop-brief/1"
SITE = "https://cop.theintelligence.company"
BRIEF_HOUR = 12          # UTC: the first build at or after 12:00 UTC writes the day's brief (6 AM CST)
CHANGED_MAX = 4          # sentences on what changed; the fifth is what to watch
RECENT_HOURS = 48        # an item's data must reach into the last 48 h (programs that post a whole day lag a day)
WATCH_DAYS = 7           # Verdict calendar events (elections and the like) within a week
WATCH_LAUNCH_HOURS = 48  # Orbit's scheduled launches within two days, only when no calendar event qualifies
TITLE_MAX = 160
FEED_ENTRIES = 30
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
PROGRAMS = {p["id"]: p for p in snap.CONFIG["programs"]}


# ---------------------------------------------------------------- choosing what goes in

def _short(text, limit=TITLE_MAX):
    text = " ".join(str(text or "").split()).rstrip(" .")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    while cut.count("(") > cut.count(")"):   # never leave a bracket open when the cut falls inside one
        cut = cut[:cut.rindex("(")]
    cut = cut.rstrip(" ,;:(–-")
    return cut + "…"


def publishers(props):
    out = []
    for s in props.get("sources") or []:
        name = s.get("publisher") if isinstance(s, dict) else None
        if isinstance(name, str) and name and name not in out:
            out.append(name)
    return out


def item_link(props):
    """The item's own page: its link, else its visual, else its program's site."""
    for url in (props.get("link"), (props.get("visual") or {}).get("url"),
                (PROGRAMS.get(props.get("cop_program")) or {}).get("site")):
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return SITE + "/"


def against(props):
    """The baseline clause, in the program's own words: why the item stands out against its own normal."""
    window = " ".join(str((props.get("baseline_ref") or {}).get("window") or "").split())
    window = window.split("; ")[0].rstrip(". ")     # the measure itself; the fine print stays on the item
    if window:
        if window.lower().startswith("fixed:"):
            return "by a fixed rule: " + window.split(":", 1)[1].strip()
        if re.search(r"alert (scale|levels)", window, re.I):
            return "on the " + re.sub(r"^the ", "", window)
        if not re.match(r"(the|a|an|its|their|every|each|all) |[A-Z]", window):
            window = "the " + window
        return "compared with " + window
    if props.get("cop_strength_text"):
        return props["cop_strength_text"]
    return None


def eligible(props, geometry, now):
    """A changed, current, placed anomaly whose program and source were read: what may be news today."""
    if props.get("tier") != "anomaly" or not geometry or props.get("kind") == "forecast":
        return False
    if props.get("cop_stale") or props.get("cop_source_stale") or snap.no_longer_current(props):
        return False
    if snap.standing(props) or props.get("cop_program") not in PROGRAMS:
        return False
    until = snap.utc(props.get("valid_until"))
    if until and until <= now:
        return False
    if props.get("data_as_of_precision") == "month":
        return False
    covered = snap.covered_until(props, now)
    return bool(covered and covered >= now - timedelta(hours=RECENT_HOURS))


def changed(today, now, previous=None):
    """Up to CHANGED_MAX items, each program's strongest new or changed measurement, one program at a time
    (snapshot.pick, so a shared voice is said once). Programs go in config order, except that those that spoke in the
    last brief go to the back, so every program is heard over a few days; strength is never compared across
    programs. An item already in the last brief with the same title is left out: it has not changed since."""
    last = previous or {}
    said = {(s.get("item_id"), s.get("title")) for s in last.get("sentences") or []}
    spoke = {s.get("program") for s in last.get("sentences") or [] if s.get("kind") == "changed"}
    queue = sorted(snap.CONFIG["programs"], key=lambda prog: prog["id"] in spoke)
    ranked = {}
    for order, f in enumerate(today.get("features") or []):
        p = f.get("properties") or {}
        if not eligible(p, f.get("geometry"), now) or (p.get("id"), _short(p.get("title"))) in said:
            continue
        pid = p["cop_program"]
        _, _, rule, rank = snap.strength(p, PROGRAMS[pid]["strength"])
        ranked.setdefault(pid, []).append((99 if rule is None else rule, -(rank or 0), order, f))
    candidates = {pid: [f for *_, f in sorted(rows, key=lambda r: r[:3])] for pid, rows in ranked.items()}
    return [f for f, _, _ in snap.pick(candidates, CHANGED_MAX, queue)]


def changed_sentence(f):
    p = f["properties"]
    prog = PROGRAMS[p["cop_program"]]
    clause = against(p)
    pubs = publishers(p)
    return {"kind": "changed", "item_id": p["id"], "program": prog["id"], "program_name": prog["name"],
            "title": _short(p.get("title")), "against": clause, "publishers": pubs, "link": item_link(p),
            "map": SITE + "/#i=" + p["id"], "data_as_of": p.get("data_as_of"),
            "data_as_of_precision": p.get("data_as_of_precision"), "countries": p.get("countries") or []}


def _calendar(verdict):
    cal = (verdict or {}).get("calendar") or {}
    if cal.get("stale"):
        return []
    return ((cal.get("data") or {}).get("events")) or []


def watch(events, verdict, today, now, countries, watched=frozenset()):
    """One dated event ahead: a Verdict calendar event within WATCH_DAYS (one in a country the brief names first), else
    an Orbit scheduled launch within WATCH_LAUNCH_HOURS (likewise). None when nothing qualifies."""
    by_id = {(f.get("properties") or {}).get("id"): f.get("properties") or {} for f in today.get("features") or []}
    start = now.date()

    def when(e):
        at = snap.utc(e.get("at"))
        if at:
            return at
        try:
            d = datetime.strptime(str(e.get("date")), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return d + timedelta(days=1) if d.date() >= start else None   # a dated day counts to its end

    def ahead(rows, limit):
        """Upcoming rows, in the brief's countries first, then one not named in the last brief, then soonest."""
        out = []
        for e in rows:
            t = when(e)
            if t is None or e.get("occurred") or e.get("stale") or not (now < t <= now + limit):
                continue
            out.append((not (set(e.get("countries") or []) & countries), e.get("id") in watched, t,
                        e.get("id") or "", e))
        return [e for *_, e in sorted(out, key=lambda r: r[:4])]

    cal = ahead(_calendar(verdict), timedelta(days=WATCH_DAYS))
    if cal:
        e = cal[0]
        host = urlparse(e.get("link") or "").hostname or ""
        return {"kind": "watch", "item_id": e["id"], "program": "verdict", "program_name": "Verdict",
                "title": _short(re.sub(r"^(On|Scheduled) \d{4}-\d{2}-\d{2}: ", "", e.get("title") or "")), "date": e.get("date"),
                "at": e.get("at"), "publishers": [host.removeprefix("www.")] if host else [],
                "link": e.get("link") or "https://metacog-osint.github.io/verdict/", "note": e.get("note"),
                "countries": e.get("countries") or []}
    # Listed launches only: a closure with no listed launch (Launch, kind "closure") is not a launch to watch for.
    launches = ahead([e for e in (events or {}).get("events") or [] if (e.get("kind") or "launch") == "launch"],
                     timedelta(hours=WATCH_LAUNCH_HOURS))
    if launches:
        e = launches[0]
        props = by_id.get(e["id"]) or {}
        return {"kind": "watch", "item_id": e["id"], "program": e.get("origin") or "orbit",
                "program_name": (PROGRAMS.get(e.get("origin")) or {}).get("name", "Orbit"),
                "title": _short(re.sub(r", \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$", "", e.get("title") or "")),
                "date": e.get("date"), "at": e.get("at"),
                "publishers": publishers(props) or ["Launch Library 2 (The Space Devs)"],
                "link": e.get("link") or item_link(props), "note": e.get("note"),
                "countries": e.get("countries") or []}
    return None


def compose(today, events, verdict, now, previous=None):
    items = changed(today, now, previous)
    sentences = [changed_sentence(f) for f in items]
    countries = {c for s in sentences for c in s["countries"]}
    watched = {s.get("item_id") for s in (previous or {}).get("sentences") or [] if s.get("kind") == "watch"}
    w = watch(events, verdict, today, now, countries, watched)
    if w:
        sentences.append(w)
    for n, s in enumerate(sentences, 1):
        s["n"] = n
        s["text"] = plain(s)
    return {"contract": CONTRACT, "day": f"{now:%Y-%m-%d}", "issued_at": snap.iso(now),
            "snapshot_generated_at": today.get("generated_at"),
            "rule": ("Each program's strongest new or changed measurement with a place, one program at a time (those heard "
                     "in the last brief last), against its own baseline; then one dated event ahead. Measurements and scheduled events, never warnings."),
            "sentences": sentences}


# ---------------------------------------------------------------- words

def _source(s):
    pubs = s.get("publishers") or []
    names = " and ".join(pubs) if len(pubs) <= 2 else ", ".join(pubs[:2]) + " and others"
    if s["kind"] == "watch" and s["program"] == "verdict":
        return f"source: {names}; via Verdict's calendar" if names else "via Verdict's calendar"
    return f"source: {names}; via {s['program_name']}" if names else f"via {s['program_name']}"


def _title(s):
    """The sentence's title as shown: a bare code of one of the item's countries reads as its name. The stored
    title stays the program's own, so a later brief still knows the item was said."""
    return with_country_names(s["title"], s.get("countries"))


def plain(s):
    """The sentence as text (feed summaries, tests). The page renders the same parts with links and times."""
    if s["kind"] == "changed":
        body = _title(s) + (f", {s['against']}" if s.get("against") else "")
        return f"{body} ({_source(s)})."
    when = f"{s['at'][:16].replace('T', ' ')} UTC" if s.get("at") else s.get("date")
    note = f" {s['note'].rstrip('.')}." if s.get("note") else ""
    return f"Ahead: {_title(s)}, {when} ({_source(s)}).{note}"


def _esc(v):
    return html.escape(str(v), quote=True)


def _time_html(s):
    if s.get("at"):
        return (f'<time datetime="{_esc(s["at"])}" data-tic="datetime">'
                f'{_esc(s["at"][:16].replace("T", " "))} UTC</time>')
    return f'<time datetime="{_esc(s.get("date"))}">{_esc(s.get("date"))} (UTC)</time>'


def sentence_html(s):
    src = _esc(_source(s))
    title = f'<a href="{_esc(s["link"])}">{_esc(_title(s))}</a>'
    if s["kind"] == "changed":
        body = title + (f", {_esc(s['against'])}" if s.get("against") else "")
        return f'{body} <span class="brief-src">({src})</span>.'
    note = f' <span class="brief-note">{_esc(s["note"].rstrip("."))}.</span>' if s.get("note") else ""
    return f'<strong>Ahead:</strong> {title}, {_time_html(s)} <span class="brief-src">({src})</span>.{note}'


def brief_html(b, heading="h2", link_day=False):
    day = _esc(b["day"])
    head = f'<a href="{day}.html">{day}</a>' if link_day else day
    issued = f'<time datetime="{_esc(b["issued_at"])}" data-tic="datetime">{_esc(b["issued_at"][:16].replace("T", " "))} UTC</time>'
    if b["sentences"]:
        body = "\n".join(f"    <li>{sentence_html(s)}</li>" for s in b["sentences"])
        lst = f'  <ol class="brief-list">\n{body}\n  </ol>'
    else:
        lst = '  <p class="note">Nothing new or changed with a place stood out in the programs read for this day.</p>'
    short = ""
    if 0 < sum(1 for s in b["sentences"] if s["kind"] == "changed") < CHANGED_MAX:
        short = ('\n  <p class="note">Fewer than four programs had a new or changed measurement with a place; the brief '
                 'does not fill the gap with older items.</p>')
    return (f'<article class="brief doc-box" id="d{day}">\n  <{heading}>Brief for {head}</{heading}>\n'
            f'  <p class="note">Written {issued} from COP\'s picture of that time.</p>\n{lst}{short}\n</article>')


# ---------------------------------------------------------------- pages and feed

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title} · COP · The Intelligence Company</title>
<link rel="canonical" href="{canonical}">
<meta name="description" content="{description}">
<meta name="color-scheme" content="light dark">
<meta property="og:type" content="article">
<meta property="og:site_name" content="The Intelligence Company">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<link rel="alternate" type="application/atom+xml" title="COP daily brief" href="{feed}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23142235'/%3E%3Ccircle cx='16' cy='16' r='9' fill='none' stroke='%23e3a83b' stroke-width='3'/%3E%3Ccircle cx='16' cy='16' r='3' fill='%23e3a83b'/%3E%3C/svg%3E">
<link rel="preload" href="../fonts/ibm-plex-sans-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="../shared/tic-time.css">
<link rel="stylesheet" href="../cop.css">
<link rel="stylesheet" href="../scoring.css">
</head>
<body class="doc">
<a class="skip" href="#main">Skip to the brief</a>
<header class="bar">
  <a class="brand" href="../" aria-label="COP, the map">
    <svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="10.5" class="mark-ring"/><circle cx="16" cy="16" r="3.5" class="mark-dot"/></svg>
    <span class="brand-name">COP</span>
    <span class="brand-sub">The Intelligence Company</span>
  </a>
  <nav class="bar-nav" aria-label="Site">
    <a href="../">Map</a>
    <a href="./">Brief</a>
    <a href="../scoring.html">How COP scores</a>
    <span id="time-settings"></span>
  </nav>
</header>
<main class="doc-main" id="main">
"""

FOOT = """  <p class="credits">Each sentence is one program's measurement, in that program's words, with its source publisher;
    follow the link for the item and its licence. Measurements and scheduled events only: never a warning, a cause or a
    forecast in COP's voice. Times show in your time zone (change it with the Time button); dates of whole UTC days
    stay in UTC. The Intelligence Company, publicly available information only.</p>
</main>
<script src="../shared/tic-time.js"></script>
<script src="../brief.js"></script>
</body>
</html>
"""

LEDE = ("""  <p class="doc-lede">What changed across The Intelligence Company's monitoring programs, once a day in five
    sentences: up to four measurements that are new or changed, each read against its own program's baseline, and one
    dated event ahead. One measurement per program, never ranked across programs; programs heard in the last brief
    wait their turn. Written at the first COP build after 12:00 UTC and never rewritten.
    <a href="feed.xml">Atom feed</a>.</p>
""")


def page(title, canonical, description, body):
    return HEAD.format(title=_esc(title), canonical=_esc(canonical), description=_esc(description),
                       feed=SITE + "/brief/feed.xml") + body + FOOT


def index_page(briefs):
    if not briefs:
        body = ("  <h1>COP daily brief</h1>\n" + LEDE +
                '  <p class="note">The first brief is written at the first build after 12:00 UTC.</p>\n')
    else:
        latest, past = briefs[0], briefs[1:]
        body = "  <h1>COP daily brief</h1>\n" + LEDE + brief_html(latest, link_day=True) + "\n"
        if past:
            rows = "\n".join(f'    <li><a href="{_esc(b["day"])}.html">{_esc(b["day"])}</a>: '
                             f'{_esc(_first_line(b))}</li>' for b in past)
            body += f'  <h2>Past briefs</h2>\n  <ul class="brief-past">\n{rows}\n  </ul>\n'
    return page("COP daily brief", SITE + "/brief/",
                "What changed across The Intelligence Company's monitoring programs today, in five sentences, each "
                "linked to its item and source.", body)


def _first_line(b):
    s = b["sentences"][0]["text"] if b["sentences"] else "nothing new stood out"
    return _short(s, 110)


def day_page(b, newer, older):
    nav = []
    if older:
        nav.append(f'<a href="{_esc(older)}.html">Earlier: {_esc(older)}</a>')
    nav.append('<a href="./">Latest and all briefs</a>')
    if newer:
        nav.append(f'<a href="{_esc(newer)}.html">Later: {_esc(newer)}</a>')
    body = (f'  <h1>COP daily brief, {_esc(b["day"])}</h1>\n' + brief_html(b) +
            '\n  <p class="brief-nav">' + " · ".join(nav) + "</p>\n")
    return page(f"COP daily brief, {b['day']}", f"{SITE}/brief/{b['day']}.html",
                f"What changed across The Intelligence Company's monitoring programs on {b['day']}.", body)


def feed(briefs):
    def x(v):
        return html.escape(str(v), quote=False)
    updated = briefs[0]["issued_at"] if briefs else "2026-11-24T12:00:00Z"
    entries = []
    for b in briefs[:FEED_ENTRIES]:
        url = f"{SITE}/brief/{b['day']}.html"
        items = "".join(f"<li>{sentence_html(s)}</li>" for s in b["sentences"]) or "<li>Nothing new stood out.</li>"
        content = (f"<p>Written {x(b['issued_at'][:16].replace('T', ' '))} UTC.</p><ol>{items}</ol>")
        entries.append(
            "  <entry>\n"
            f"    <id>tag:cop.theintelligence.company,2026:brief:{b['day']}</id>\n"
            f"    <title>COP daily brief, {b['day']}</title>\n"
            f'    <link rel="alternate" type="text/html" href="{url}"/>\n'
            f"    <published>{b['issued_at']}</published>\n    <updated>{b['issued_at']}</updated>\n"
            f"    <summary>{x(' '.join(s['text'] for s in b['sentences']) or 'Nothing new stood out.')}</summary>\n"
            f'    <content type="html">{x(content)}</content>\n'
            "  </entry>\n")
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n'
            "  <id>tag:cop.theintelligence.company,2026:brief</id>\n  <title>COP daily brief</title>\n"
            "  <subtitle>What changed across The Intelligence Company's monitoring programs, in five sentences a day."
            "</subtitle>\n"
            f'  <link rel="self" type="application/atom+xml" href="{SITE}/brief/feed.xml"/>\n'
            f'  <link rel="alternate" type="text/html" href="{SITE}/brief/"/>\n'
            f"  <updated>{updated}</updated>\n  <author><name>COP, The Intelligence Company</name></author>\n"
            + "".join(entries) + "</feed>\n")


# ---------------------------------------------------------------- store

def load_store(store):
    out = {}
    for path in sorted(Path(store).glob("*.json")) if Path(store).is_dir() else []:
        m = DAY_FILE.match(path.name)
        if not m:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"brief: {path.name} is unreadable; left out of the page", file=sys.stderr)
            continue
        if doc.get("contract") == CONTRACT and doc.get("day") == m.group(1):
            out[m.group(1)] = doc
    return out


def due(now, store_days):
    day = f"{now:%Y-%m-%d}"
    return now.hour >= BRIEF_HOUR and day not in store_days


def render(briefs_by_day, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.html"):      # drawn output only (the store is elsewhere): a page for a brief not kept goes
        old.unlink()
    days = sorted(briefs_by_day, reverse=True)
    briefs = [briefs_by_day[d] for d in days]
    (out / "index.html").write_text(index_page(briefs), encoding="utf-8", newline="\n")
    for i, d in enumerate(days):
        newer = days[i - 1] if i > 0 else None
        older = days[i + 1] if i + 1 < len(days) else None
        (out / f"{d}.html").write_text(day_page(briefs_by_day[d], newer, older), encoding="utf-8", newline="\n")
    (out / "feed.xml").write_text(feed(briefs), encoding="utf-8", newline="\n")
    snap.write_json(out / "briefs.json", {"contract": CONTRACT, "briefs": briefs})


def _read(path):
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot")
    ap.add_argument("--events")
    ap.add_argument("--verdict")
    ap.add_argument("--store", required=True, help="folder of <day>.json briefs (the briefs-YYYY releases)")
    ap.add_argument("--publish", required=True, help="where the page, day pages and feed go (site/brief)")
    ap.add_argument("--new-out", help="write the path of a newly written brief here (empty when none)")
    ap.add_argument("--render-only", action="store_true", help="draw the stored briefs; write no new one")
    ap.add_argument("--now", help="override the brief time (default: the snapshot's generated_at)")
    ap.add_argument("--seed", help="a published briefs.json to draw from when the releases could not be read "
                                   "(with --render-only: the page keeps its past briefs, and nothing new is written)")
    a = ap.parse_args(argv)
    if a.seed and not a.render_only:
        ap.error("--seed is for --render-only: a new brief is written only against the full store")
    store = Path(a.store)
    briefs = load_store(store)
    if a.seed:
        for b in (_read(a.seed) or {}).get("briefs") or []:
            if isinstance(b, dict) and b.get("contract") == CONTRACT and isinstance(b.get("day"), str):
                briefs.setdefault(b["day"], b)
    new = None
    if not a.render_only:
        today = _read(a.snapshot)
        if not today:
            print("brief: no snapshot; nothing new written", file=sys.stderr)
        else:
            now = snap.utc(a.now) if a.now else snap.utc(today.get("generated_at"))
            if now and due(now, briefs):
                previous = briefs[max(briefs)] if briefs else None
                b = compose(today, _read(a.events), _read(a.verdict), now, previous)
                store.mkdir(parents=True, exist_ok=True)
                new = store / f"{b['day']}.json"
                if new.exists():                  # never replace a stored brief
                    raise SystemExit(f"brief: {new} exists; refusing to replace it")
                new.write_text(json.dumps(b, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
                briefs[b["day"]] = b
                print(f"brief: wrote {b['day']} with {len(b['sentences'])} sentences")
            else:
                print("brief: not due (before 12:00 UTC, or today's brief exists)")
    if a.new_out:
        Path(a.new_out).write_text(str(new) if new else "", encoding="utf-8")
    render(briefs, a.publish)
    print(f"brief: page with {len(briefs)} briefs")


if __name__ == "__main__":
    main()
