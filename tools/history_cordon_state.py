"""Rebuild the State Department's travel advisory levels by date, for the STOP replay (cop-layer-history/1).

State's advisories RSS (travel.state.gov/_res/rss/TAsTWs.xml) carries every advisory with its level in the title and
the date it was last updated; the Internet Archive holds dozens of captures of it for 2024-2025. Each capture is read
for the countries below, and a row is one advisory update (country, update date, level), first seen in a named
capture. A level change is therefore dated exactly (State's own "date updated"), but an update that came and went
between two captures is missed, and the row that follows says so: `capture-gap` when the capture that first showed it
came more than 7 days after the one before. `to_items` turns rows back into the Cordon items triage reads.

    python tools/history_cordon_state.py --start 2024-12-01 --end 2025-12-31
Writes history/cordon/<dataset_id>/manifest.json and rows.jsonl. Standard-library Python only.
"""
import argparse
import html
import re
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_cordon_common as hc  # noqa: E402

RSS_URL = "https://travel.state.gov/_res/rss/TAsTWs.xml"
PAGE_URL = "https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories/{slug}-travel-advisory.html"
GAP_DAYS = 7
TITLE_RE = re.compile(r"^(.*?)\s*-\s*Level\s+([1-4])\s*:\s*(.+)$", re.S)
SOURCES = [{"id": "state-advisories-rss",
            "name": "US State Department travel advisories RSS, as captured by the Internet Archive",
            "url": RSS_URL, "licence": "US Government work, public domain",
            "attribution": "U.S. Department of State, Bureau of Consular Affairs (travel.state.gov); captures by "
                           "the Internet Archive Wayback Machine", "how": "wayback"},
           {"id": "state-advisory-pages",
            "name": "US State Department travel advisory pages, as captured by the Internet Archive",
            "url": PAGE_URL, "licence": "US Government work, public domain",
            "attribution": "U.S. Department of State, Bureau of Consular Affairs (travel.state.gov); captures by "
                           "the Internet Archive Wayback Machine", "how": "wayback"}]
# Israel, the West Bank and Gaza is not in the RSS in any capture (its page says "See Individual Summaries", no
# single level): its dates come from page captures, thinned to one every PAGE_THIN_DAYS.
PAGES = {"israel-west-bank-and-gaza": ["IL", "PS"]}
PAGE_THIN_DAYS = 5
PAGE_RE = re.compile(r"typebox[^>]*>\s*Travel Advisory\s*<br>\s*([A-Z][a-z]+ \d{1,2}, \d{4})", re.S)
TITLE_H3 = re.compile(r'alertheader-title[^>]*>(.*?)</h3>', re.S)
# State's link slug (and any earlier spelling) -> the places the advisory covers. The combined Israel, West Bank and
# Gaza advisory is Palestine's too (15 Nov 2026): rows are made for both.
SLUGS = {
    "burma": ["MM"], "india": ["IN"], "pakistan": ["PK"], "russia": ["RU"], "iraq": ["IQ"],
    "israel-west-bank-and-gaza": ["IL", "PS"], "israel-the-west-bank-and-gaza": ["IL", "PS"], "iran": ["IR"],
    "tanzania": ["TZ"], "afghanistan": ["AF"], "ukraine": ["UA"], "syria": ["SY"], "yemen": ["YE"],
    "turkey": ["TR"], "turkiye": ["TR"], "turkey-turkiye": ["TR"], "kenya": ["KE"], "sudan": ["SD"],
    "cameroon": ["CM"], "venezuela": ["VE"], "belarus": ["BY"], "democratic-republic-of-the-congo": ["CD"],
    "cambodia": ["KH"], "panama": ["PA"], "jordan": ["JO"], "nepal": ["NP"], "guinea": ["GN"],
    "chad": ["TD"], "south-sudan": ["SS"], "albania": ["AL"], "papua-new-guinea": ["PG"], "lithuania": ["LT"],
    "libya": ["LY"], "vietnam": ["VN"], "nigeria": ["NG"], "algeria": ["DZ"], "togo": ["TG"], "angola": ["AO"],
    "indonesia": ["ID"], "central-african-republic": ["CF"], "guinea-bissau": ["GW"],
}
# Since State's site change of Nov 2025 the RSS links read .../destination.<ISO alpha-3>.html.
ISO3 = {"mmr": "MM", "ind": "IN", "pak": "PK", "rus": "RU", "irq": "IQ", "isr": "IL", "irn": "IR", "tza": "TZ",
        "afg": "AF", "ukr": "UA", "syr": "SY", "yem": "YE", "tur": "TR", "ken": "KE", "sdn": "SD", "cmr": "CM",
        "ven": "VE", "blr": "BY", "cod": "CD", "khm": "KH", "pan": "PA", "jor": "JO", "npl": "NP", "gin": "GN",
        "tcd": "TD", "ssd": "SS", "alb": "AL", "png": "PG", "ltu": "LT", "lby": "LY", "vnm": "VN", "nga": "NG",
        "dza": "DZ", "tgo": "TG", "ago": "AO", "idn": "ID", "caf": "CF", "gnb": "GW"}
for _code, _cc in ISO3.items():
    SLUGS[f"destination.{_code}"] = ["IL", "PS"] if _cc == "IL" else [_cc]
KEY_OF = {"PS": "IL"}      # the item id key State's live page would carry (Cordon keys the combined page IL)


def _pub_date(text):
    """'Wed, 26 Jun 2024' (no time of day) or a full RFC 822 date, as YYYY-MM-DD, or None."""
    t = text.strip()
    for fmt in ("%a, %d %b %Y", "%d %b %Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(t).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_rss(text):
    """[{slug, name, level, level_text, date (YYYY-MM-DD), link, tag, note}] from one capture of the RSS."""
    if "<rss" not in text or "<item>" not in text:
        raise ValueError("not the advisories RSS")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", text, re.S):
        def tag(name):
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
            return html.unescape(m.group(1)).strip() if m else ""
        m = TITLE_RE.match(re.sub(r"\s+", " ", tag("title")))
        link = tag("link")
        if not m or not re.search(r"/travel-?advisories/", link):
            continue
        slug = link.rsplit("/", 1)[-1].removesuffix(".html").lower()
        slug = re.sub(r"-?(travel-)?advisory$", "", slug)
        d = _pub_date(tag("pubDate"))
        if not d:
            continue
        cat = re.search(r"<category domain='Country-Tag'>(.*?)</category>", item)
        note = re.search(r"<description>\s*<!\[CDATA\[\s*<p><b><i>(.*?)</i></b></p>", item, re.S)
        out.append({"slug": slug, "name": m.group(1).strip(), "level": int(m.group(2)),
                    "level_text": m.group(3).strip().rstrip("."), "date": d, "link": link,
                    "tag": cat.group(1) if cat else None,
                    "note": hc.strip_tags(note.group(1))[:200] if note else None})
    return out


def parse_page(text):
    """{date, level, level_text, note} from one capture of a country's advisory page, or raises ValueError. level is
    None where the page has no single level (Israel, the West Bank and Gaza: "See Individual Summaries")."""
    m = PAGE_RE.search(text)
    if not m:
        raise ValueError("no advisory date on the page")
    d = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    t = TITLE_H3.search(text)
    head = hc.strip_tags(t.group(1)) if t else ""
    lv = re.match(r"Level\s+([1-4])\s*:\s*(.+)$", head)
    note = re.search(r'tsg-rwd-emergency-alert-text"><p><b>(.*?)</b>', text, re.S)
    return {"date": d, "level": int(lv.group(1)) if lv else None, "level_text": lv.group(2).strip() if lv else head,
            "note": hc.strip_tags(note.group(1))[:200] if note else None}


def page_rows(slug, captures, start, end, now):
    """rows from [(stamp, parsed page)] of one advisory page, one per update date, for each place in PAGES[slug]."""
    seen, prev = {}, None
    for stamp, pg in captures:
        gap = None if prev is None else (_dt(stamp) - _dt(prev)).days
        prev = stamp
        key = (pg["date"], pg["level"])
        if key not in seen:
            seen[key] = (stamp, gap, pg)
    lst = sorted(seen.items(), key=lambda kv: kv[0][0])
    before = [kv for kv in lst if kv[0][0] < start.isoformat()]
    keep = ([before[-1]] if before else []) + [kv for kv in lst if start.isoformat() <= kv[0][0] <= end.isoformat()]
    rows = []
    for (d, lv), (stamp, gap, pg) in keep:
        for cc in PAGES[slug]:
            quality = ["capture-gap"] if gap is None or gap > GAP_DAYS else []
            if lv is None:
                quality.append("level-unknown")
            if d < start.isoformat():
                quality.append("lead-in")
            rows.append({
                "place": cc, "at": d, "precision": "day", "source_id": "state-advisory-pages",
                "source_ref": hc.wayback_url(stamp, PAGE_URL.format(slug=slug)), "retrieved_at": now,
                "quality": quality, "item_id": f"cordon:advisory:{KEY_OF.get(cc, cc)}", "kind": "advisory",
                "level": lv, "level_text": pg["level_text"], "name": "Israel, the West Bank and Gaza",
                "state_slug": slug, "link": PAGE_URL.format(slug=slug), "change_note": pg["note"],
                "first_seen": hc.stamp_to_iso(stamp), "capture_gap_days": gap, "countries": [cc],
                "place_note": ("State's combined Israel, West Bank and Gaza advisory, used for Palestine"
                               if cc == "PS" else None)})
    return rows


def build_rows(captures, start, end, now):
    """rows from [(stamp, [parsed items])] sorted by stamp; items of the countries in SLUGS only.

    A row per (slug, update date, level). `first_seen` is the capture that first showed it; `capture_gap_days` the
    days between that capture and the one before, which is how long another update could have gone unseen."""
    first = {}
    prev_stamp = {}
    for stamp, items in captures:
        gap = None
        if prev_stamp:
            gap = (_dt(stamp) - _dt(prev_stamp["s"])).days
        for it in items:
            if it["slug"] not in SLUGS:
                continue
            key = (SLUGS[it["slug"]][0], it["date"], it["level"])   # one advisory, whichever link spelling
            if key not in first:
                first[key] = (stamp, gap, it)
        prev_stamp["s"] = stamp
    by_slug = {}
    for (cc0, d, lv), v in first.items():
        by_slug.setdefault(cc0, []).append((d, lv, v))
    rows = []
    for cc0, lst in by_slug.items():
        lst.sort()
        before = [x for x in lst if x[0] < start.isoformat()]
        keep = ([before[-1]] if before else []) + [x for x in lst if start.isoformat() <= x[0] <= end.isoformat()]
        for i, (d, lv, (stamp, gap, it)) in enumerate(keep):
            for cc in SLUGS[it["slug"]]:
                quality = []
                if gap is None or gap > GAP_DAYS:
                    quality.append("capture-gap")
                if it["date"] < start.isoformat():
                    quality.append("lead-in")
                rows.append({
                    "place": cc, "at": d, "precision": "day", "source_id": "state-advisories-rss",
                    "source_ref": hc.wayback_url(stamp, RSS_URL), "retrieved_at": now, "quality": quality,
                    "item_id": f"cordon:advisory:{KEY_OF.get(cc, cc)}", "kind": "advisory", "level": lv,
                    "level_text": it["level_text"], "name": it["name"], "state_slug": it["slug"], "link": it["link"],
                    "change_note": it["note"], "first_seen": hc.stamp_to_iso(stamp),
                    "capture_gap_days": gap, "countries": [cc],
                    "place_note": ("State's combined Israel, West Bank and Gaza advisory, used for Palestine"
                                   if cc == "PS" else None)})
    rows.sort(key=lambda r: (r["place"], r["at"], r["level"]))
    return rows


def _dt(stamp):
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def to_items(rows, at=None):
    """The advisory items Cordon would have carried, from rows: one per advisory id and place, the latest update on or
    before `at` (a datetime; None: the latest row). tier is anomaly when the level changed in the 30 days before
    `at`, as Cordon marks it. data_as_of is the update date (midnight UTC), precision day."""
    by = {}
    for r in sorted((r for r in rows if r.get("kind") == "advisory"), key=lambda r: r["at"]):
        d = datetime.fromisoformat(r["at"]).replace(tzinfo=timezone.utc)
        if at is not None and d > at:
            continue
        by.setdefault((r["item_id"], r["place"]), []).append((d, r))
    items = []
    for (item_id, place), seq in by.items():
        d, r = seq[-1]
        prev = seq[-2][1]["level"] if len(seq) > 1 else None
        changed = prev is not None and prev != r["level"]
        horizon = at or d
        recent = changed and (horizon - d) <= timedelta(days=30)
        p = {"id": item_id, "title": f"{r['name']}: Level {r['level']}, {r['level_text']}",
             "tier": "anomaly" if recent else "routine", "data_as_of": d.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "data_as_of_precision": "day", "countries": [place], "cop_program": "cordon", "cop_stale": False,
             "response_stage": f"state_travel_advisory:level_{r['level']}",
             "measures": {"level": r["level"]}}
        if changed:
            p["measures"]["from_level"] = prev
        items.append({"type": "Feature", "geometry": None, "properties": p})
    return items


def levels_from_rows(rows):
    """{advisory id: [[date, level], ...]}: what triage_rates.py keeps from the archive, for `_direction`. A
    Palestine row shares its id with Israel's, so the lists are the same advisory's."""
    out = {}
    for r in rows:
        if r.get("kind") == "advisory" and r["place"] == KEY_OF.get(r["place"], r["place"]):
            out.setdefault(r["item_id"], []).append([r["at"] + "T00:00:00Z", r["level"]])
    return out


def manifest(rows, start, end, now, code_sha, captures_read, captures_failed, gaps):
    return {
        "contract": "cop-layer-history/1",
        "dataset_id": f"cordon-state-advisories-{start}-{end}",
        "layer": "cordon",
        "title": "State Department travel advisory levels by update date, from Wayback captures of its RSS",
        "description": "One row per advisory update (country, date updated, level) as State's advisories RSS showed "
                       "it in an Internet Archive capture. For replaying triage's advisory notices (kind advisory) "
                       "against Access Now's 2025 shutdowns.",
        "sources": SOURCES,
        "derived_from": [],
        "method": {"code": "tools/history_cordon_state.py", "commit": code_sha,
                   "params": {"rss": RSS_URL, "gap_days": GAP_DAYS, "slugs": sorted(SLUGS),
                              "captures_read": captures_read, "captures_failed": captures_failed},
                   "matches_live": False,
                   "differences": [
                       "Live Cordon reads State's JSON (cadataapi.state.gov), which carries the update time; the RSS "
                       "carries the date only, so every row is day precision.",
                       "An update that appeared and was replaced between two captures is missed.",
                       "Tier is derived the way Cordon marks it (level changed in the 30 days before), not read "
                       "from a stored change log.",
                       "Departure posture (kind posture, read from the advisory text) is not rebuilt.",
                       "Gaza and the West Bank are State's combined Israel, West Bank and Gaza advisory, copied to "
                       "place PS."]},
        "coverage": {"start": str(start), "end": str(end), "time_precision": "day",
                     "places": sorted({r["place"] for r in rows}), "place_code": "ISO 3166-1 alpha-2"},
        "gaps": gaps,
        "biases": ["Levels are exact on the day of each update; the risk is a missed update between captures, "
                   "flagged capture-gap on the row that followed.",
                   "A 'reissued after periodic review without changes' notice carries a new date at the same level; "
                   "triage reads it as not raised.",
                   "The Archive's captures are irregular and thin in autumn 2025."],
        "rows": hc.row_stats(rows),
        "row_schema": {
            "place": "ISO alpha-2", "at": "date the advisory was updated (UTC day)", "precision": "day",
            "source_id": "state-advisories-rss", "source_ref": "the Wayback capture that first showed the row",
            "retrieved_at": "UTC", "quality": "capture-gap, lead-in", "item_id": "cordon:advisory:<key>",
            "kind": "advisory", "level": "1-4", "level_text": "State's words", "name": "State's name for the place",
            "state_slug": "State's page slug", "link": "State's advisory page", "change_note": "State's reissue note",
            "first_seen": "capture time that first showed the row, UTC", "capture_gap_days":
            "days since the previous capture", "countries": "[place]", "place_note": "set for Palestine"},
        "files": [],
        "redistribution": "rows",
        "built_at": now,
        "built_by": "tools/history_cordon_state.py, COP Cordon-history session, 16 Nov 2026",
        "uses": ["replaying triage's State advisory notices for 2025", "base rates of advisory updates per country-day"],
        "not_for": ["a complete record of advisory updates (captures are irregular)", "posture or departure status"],
        "supersedes": None,
    }


def capture_gaps(stamps, start, end):
    out = []
    pts = [_dt(s).date() for s in stamps]
    pts = [p for p in pts if start - timedelta(days=1) <= p <= end + timedelta(days=60)]
    for a, b in zip(pts, pts[1:]):
        if (b - a).days > GAP_DAYS and a < end and b > start:
            out.append({"from": str(max(a, start)), "to": str(min(b, end)), "places": "all",
                        "reason": f"no capture of the advisories RSS for {(b - a).days} days; a level update "
                                  "inside the gap would show only in the next capture's dates"})
    return out


def collect(client, start, end):
    rows_cdx = hc.cdx(client, "travel.state.gov/_res/rss/TAsTWs.xml",
                      **{"from": (start - timedelta(days=90)).strftime("%Y%m%d"),
                         "to": (end + timedelta(days=60)).strftime("%Y%m%d")}, filter="statuscode:200")
    caps, failed = [], []
    seen_days = set()
    for r in rows_cdx:
        body, st = hc.wayback_raw(client, r["timestamp"], RSS_URL)
        try:
            if body is None or st != 200:
                raise ValueError(st)
            caps.append((r["timestamp"], parse_rss(body.decode("utf-8", "replace"))))
        except ValueError as e:
            failed.append({"stamp": r["timestamp"], "why": str(e)})
    caps.sort()
    return caps, failed


def collect_pages(client, start, end):
    """{slug: [(stamp, parsed page)]} for the PAGES, captures thinned to one every PAGE_THIN_DAYS, and failures."""
    out, failed = {}, []
    for slug in PAGES:
        url = PAGE_URL.format(slug=slug)
        rows_cdx = hc.cdx(client, url[8:], **{"from": (start - timedelta(days=120)).strftime("%Y%m%d"),
                                              "to": (end + timedelta(days=30)).strftime("%Y%m%d")},
                          filter="statuscode:200", collapse="timestamp:8")
        last, got = None, []
        for r in rows_cdx:
            t = _dt(r["timestamp"])
            if last is not None and (t - last).days < PAGE_THIN_DAYS:
                continue
            last = t
            body, st = hc.wayback_raw(client, r["timestamp"], url)
            try:
                if body is None or st != 200:
                    raise ValueError(st)
                got.append((r["timestamp"], parse_page(body.decode("utf-8", "replace"))))
            except ValueError as e:
                failed.append({"stamp": r["timestamp"], "slug": slug, "why": str(e)})
        out[slug] = got
    return out, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2024-12-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default=str(hc.ROOT / "history" / "cordon"))
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    now = hc.utc_now()
    client = hc.Client()
    caps, failed = collect(client, start, end)
    rows = build_rows(caps, start, end, now)
    pages, pfailed = collect_pages(client, start, end)
    for slug, got in pages.items():
        rows += page_rows(slug, got, start, end, now)
    rows.sort(key=lambda r: (r["place"], r["at"], r["level"] or 0))
    failed += pfailed
    man = manifest(rows, start, end, now, hc.code_ref(__file__), len(caps), failed,
                   capture_gaps([s for s, _ in caps], start, end))
    hc.write_dataset(Path(a.out) / man["dataset_id"], man, rows)
    print(f"{len(rows)} rows from {len(caps)} captures ({len(failed)} unreadable) -> {man['dataset_id']}; "
          f"requests {client.requests}, cached {client.hits}")


def row_stats(rows):
    return hc.row_stats(rows)


if __name__ == "__main__":
    sys.exit(main())
