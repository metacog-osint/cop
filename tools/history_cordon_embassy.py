"""Rebuild US embassy alerts by date, for the STOP replay (cop-layer-history/1).

Every US embassy site is a WordPress site whose alert category has an RSS feed (<host>/category/alert/feed/) listing
its ten newest alerts with exact publication times. The embassy sites refuse plain scripts (HTTP 403), so the live
feeds are out of reach; the Internet Archive holds captures of the same feeds. Each distinct capture is read, the
alerts of all captures are merged by link, and a row is one alert (country, published time, title, type).

A capture of ten items shows the feed from its oldest item to the capture time, so the union of those spans says
which stretches of the window were seen. A stretch no capture's span covers is a gap: alerts posted in it, and then
pushed off the feed before the next capture, are missing. `to_items` turns rows back into the Cordon items triage
reads.

    python tools/history_cordon_embassy.py --start 2024-12-01 --end 2025-12-31 [--countries IN,PK]
Writes history/cordon/<dataset_id>/manifest.json and rows.jsonl. Standard-library Python only.
"""
import argparse
import hashlib
import html
import re
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_cordon_common as hc  # noqa: E402

FEED = "https://{host}/category/alert/feed/"
FEED_PREFIX = "{host}/category/alert/feed"
PAGE_ITEMS = 10                 # WordPress's default feed length
TYPE_RE = re.compile(r"^\s*([A-Za-z ]{3,40}?)\s+(?:Alert|Message)\s*[:\-–—]", re.I)   # Cordon's embassy.py
SOURCES = [{"id": "usembassy-alert-feed-wayback",
            "name": "US embassy and consulate alert category RSS feeds, as captured by the Internet Archive",
            "url": "https://<cc>.usembassy.gov/category/alert/feed/",
            "licence": "US Government work, public domain (embassy websites, state.gov)",
            "attribution": "U.S. Department of State, U.S. embassies and consulates (usembassy.gov); captures by the "
                           "Internet Archive Wayback Machine", "how": "wayback"}]
PS_WORDS = re.compile(r"(?i)west bank|gaza|palestin")


def parse_feed(xml, iso):
    """[{title, link, at (ISO UTC), type, summary}] from one capture of a feed, or raises ValueError (not RSS, or the
    embassy's "Technical Difficulties" page)."""
    if "<rss" not in xml or "<channel" not in xml:
        raise ValueError("not RSS" + (" (Technical Difficulties page)" if "Technical Difficulties" in xml else ""))
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name):
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
            if not m:
                return ""
            v = m.group(1).strip()
            if v.startswith("<![CDATA["):
                v = v[9:-3]
            return html.unescape(re.sub(r"<[^>]+>", " ", v)).strip()
        title, link, pub = re.sub(r"\s+", " ", tag("title")).strip(), tag("link"), tag("pubDate")
        try:
            at = parsedate_to_datetime(pub).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            continue
        if not title or not link.startswith(("http://", "https://")):
            continue
        m = TYPE_RE.match(title)
        out.append({"title": title, "link": link, "at": at, "type": m.group(1).strip().lower() if m else "alert",
                    "summary": re.sub(r"\s+", " ", tag("description"))[:240]})
    return out


def item_key(iso, link):
    return f"cordon:alert:{iso}:{hashlib.sha256(link.encode('utf-8')).hexdigest()[:12]}"   # Cordon: digest(link)[7:19]


def spans(captures, start, end):
    """(gaps, covered_from): the stretches of [start, end] no capture's span covers.

    captures: [(capture time ISO, [items] or None, is_first_page)]. A capture that holds PAGE_ITEMS items shows the
    feed from its oldest item to its own time; one with fewer shows everything the category had."""
    iv = []
    for t, items, first_page in captures:
        if not items or not first_page:
            continue
        cap = _d(t)
        lo = date.min if len(items) < PAGE_ITEMS else _d(min(i["at"] for i in items))
        iv.append((lo, cap))
    iv.sort()
    gaps, cursor = [], start
    for lo, hi in iv:
        if hi < cursor:
            continue
        if lo > cursor:
            gaps.append((cursor, min(lo, end)))
        cursor = max(cursor, hi + timedelta(days=0))
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return [(a, b) for a, b in gaps if a < b]


def _d(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


def build_rows(iso, captures, start, end, now, host):
    """rows for one embassy from [(stamp, [items], is_first_page)] sorted by stamp, items in the window only."""
    first = {}
    for stamp, items, _fp in captures:
        for it in items or []:
            first.setdefault(it["link"], (stamp, it))
    lo = start.isoformat()
    hi = (end + timedelta(days=1)).isoformat()
    rows = []
    for link, (stamp, it) in first.items():
        if not (lo <= it["at"][:10] < hi):
            continue
        for cc in [iso] + (["PS"] if iso == "IL" and PS_WORDS.search(it["title"] + " " + it["summary"]) else []):
            rows.append({
                "place": cc, "at": it["at"], "precision": "minute", "source_id": "usembassy-alert-feed-wayback",
                "source_ref": hc.wayback_url(stamp, FEED.format(host=host)), "retrieved_at": now, "quality": [],
                "item_id": item_key(iso, link), "kind": "alert", "title": it["title"], "alert_type_raw": it["type"],
                "link": link, "summary": it["summary"], "embassy_host": host, "first_seen": hc.stamp_to_iso(stamp),
                "countries": [cc],
                "place_note": ("US Embassy Jerusalem alert whose title or summary names the West Bank, Gaza or "
                               "Palestinians" if cc == "PS" else None)})
    rows.sort(key=lambda r: (r["at"], r["item_id"]))
    return rows


def to_items(rows, at=None, keep=10):
    """The embassy-alert items Cordon would have carried at `at` (a datetime; None: all): per post the newest `keep`
    alerts published on or before `at` (Cordon keeps ten), id, title ("US embassy (IN): ..."), tier routine,
    data_as_of the publication time, measures.type the type the title gives."""
    by = {}
    for r in rows:
        if r.get("kind") != "alert":
            continue
        t = datetime.fromisoformat(r["at"].replace("Z", "+00:00"))
        if at is not None and t > at:
            continue
        by.setdefault(r["place"], []).append((r["at"], r))
    items = []
    for cc, seq in by.items():
        for _, r in sorted(seq, key=lambda x: x[0], reverse=True)[:keep]:
            items.append({"type": "Feature", "geometry": None, "properties": {
                "id": r["item_id"], "title": f"US embassy ({cc}): {r['title']}", "tier": "routine",
                "data_as_of": r["at"], "countries": [cc], "cop_program": "cordon", "cop_stale": False,
                "measures": {"type": r["alert_type_raw"]}}})
    return items


def collect_host(client, host, start, end):
    """[(stamp, items or None, is_first_page)] and [failures] for the host's alert feed captures."""
    lo = (start - timedelta(days=45)).strftime("%Y%m%d")
    hi = (end + timedelta(days=120)).strftime("%Y%m%d")
    rows = hc.cdx(client, FEED_PREFIX.format(host=host), **{"from": lo, "to": hi}, matchType="prefix",
                  filter="statuscode:200", collapse="digest")
    out, failed = [], []
    for r in rows:
        first_page = "paged=" not in r["original"] and "page/" not in r["original"]
        body, st = hc.wayback_raw(client, r["timestamp"], r["original"])
        try:
            if body is None or st != 200:
                raise ValueError(st)
            items = parse_feed(body.decode("utf-8", "replace"), host)
            out.append((r["timestamp"], items, first_page))
        except ValueError as e:
            failed.append({"stamp": r["timestamp"], "why": str(e)})
    out.sort()
    return out, failed


def manifest(rows, start, end, now, code_sha, per_host, no_host):
    gaps = []
    for cc, v in per_host.items():
        for a, b in v["gaps"]:
            gaps.append({"from": str(a), "to": str(b), "places": cc,
                         "reason": ("no readable capture of the embassy's alert feed" if v["captures"] == 0 else
                                    "no capture's ten newest alerts span this stretch; alerts posted in it and "
                                    "pushed off the feed before the next capture are missing")})
    for cc, why in no_host.items():
        gaps.append({"from": str(start), "to": str(end), "places": cc, "reason": why})
    return {
        "contract": "cop-layer-history/1",
        "dataset_id": f"cordon-embassy-alerts-{start}-{end}",
        "layer": "cordon",
        "title": "US embassy alerts by publication time, from Wayback captures of each embassy's alert feed",
        "description": "One row per alert an embassy or consulate published (country of the post, exact UTC time, "
                       "title, type from the title). For replaying triage's embassy alert notices (kind alert; "
                       "security and demonstration alerts count, weather, health, event and routine do not).",
        "sources": SOURCES,
        "derived_from": [],
        "method": {"code": "tools/history_cordon_embassy.py", "commit": code_sha,
                   "params": {"feed": FEED, "feed_items": PAGE_ITEMS,
                              "hosts": {cc: v["host"] for cc, v in per_host.items()},
                              "captures": {cc: v["captures"] for cc, v in per_host.items()},
                              "unreadable": {cc: v["unreadable"] for cc, v in per_host.items()}},
                   "matches_live": False,
                   "differences": [
                       "Live Cordon reads the feeds directly (or a Google News search when they 403) for posts of "
                       "countries at advisory level 3-4 plus a fixed list, and keeps ten per post; here every "
                       "embassy in the list is read, from archived feeds.",
                       "An alert published and pushed off the ten-item feed between two captures is missing.",
                       "An edited alert keeps its first publication time here; Cordon's Google News fallback can "
                       "re-date an edited alert.",
                       "Palestine has no embassy of its own: alerts of US Embassy Jerusalem (il.usembassy.gov) are "
                       "rows for place IL, and copied to PS when the title or summary names the West Bank, Gaza or "
                       "Palestinians."]},
        "coverage": {"start": str(start), "end": str(end), "time_precision": "minute",
                     "places": sorted({r["place"] for r in rows}), "place_code": "ISO 3166-1 alpha-2"},
        "gaps": gaps,
        "biases": ["Under-counts alerts in busy periods (many alerts between captures) and for embassies with few "
                   "archived captures.",
                   "Over-represents embassies whose feeds the Archive captured often, which are the large and the "
                   "newsworthy ones.",
                   "Countries with no operating embassy (Iran, and Afghanistan, Syria, Yemen where their sites were "
                   "not archived) have no rows, which is not the same as no alerts."],
        "rows": hc.row_stats(rows),
        "row_schema": {
            "place": "ISO alpha-2 of the post's country", "at": "publication time, UTC", "precision": "minute",
            "source_id": "usembassy-alert-feed-wayback",
            "source_ref": "the Wayback capture of the feed that first showed the alert", "retrieved_at": "UTC",
            "quality": "empty unless noted", "item_id": "cordon:alert:<ISO>:<12 hex of sha256(link)>",
            "kind": "alert", "title": "the alert title as posted", "alert_type_raw": "type word from the title",
            "link": "the alert's URL", "summary": "first 240 characters of the feed's excerpt",
            "embassy_host": "usembassy.gov host", "first_seen": "capture time that first showed it, UTC",
            "countries": "[place]", "place_note": "set for Palestine copies"},
        "files": [],
        "redistribution": "rows",
        "built_at": now,
        "built_by": "tools/history_cordon_embassy.py, COP Cordon-history session, 16 Nov 2026",
        "uses": ["replaying triage's embassy alert notices for 2025",
                 "base rates of security and demonstration alerts per country-day"],
        "not_for": ["a complete record of embassy alerts", "countries without a captured embassy feed"],
        "supersedes": None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2024-12-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--countries", default="")
    ap.add_argument("--extra", action="store_true")
    ap.add_argument("--id-suffix", default="")
    ap.add_argument("--out", default=str(hc.ROOT / "history" / "cordon"))
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    todo = hc.COUNTRIES + (hc.COUNTRIES_EXTRA if a.extra else [])
    if a.countries:
        want = set(a.countries.split(","))
        todo = [c for c in hc.COUNTRIES + hc.COUNTRIES_EXTRA if c[0] in want]
    now = hc.utc_now()
    client = hc.Client()
    rows, per, no_host = [], {}, {}
    for cc, name, _slug, host in todo:
        if not host:
            no_host[cc] = "no US embassy website (protecting-power or virtual presence only)"
            continue
        iso = "IL" if cc == "PS" else cc
        if cc == "PS" and any(r["place"] == "PS" for r in rows):
            continue
        caps, failed = collect_host(client, host, start, end)
        if not any(c[1] for c in caps):
            no_host[iso if cc != "PS" else "PS"] = (
                f"{host}: no readable capture of the alert feed in the Archive "
                f"({len(failed)} unreadable)" if failed or not caps else f"{host}: captures hold no alerts")
            per[iso] = {"host": host, "captures": 0, "unreadable": len(failed), "gaps": []}
            continue
        crow = build_rows(iso, caps, start, end, now, host)
        rows += crow
        gaps = spans([(hc.stamp_to_iso(s), it, fp) for s, it, fp in caps], start, end)
        per[iso] = {"host": host, "captures": len(caps), "unreadable": len(failed), "gaps": gaps}
        if cc == "PS":
            per["PS"] = per[iso]
        print(iso, host, f"{len(caps)} captures, {len(crow)} rows, {len(gaps)} gaps, {len(failed)} unreadable",
              file=sys.stderr)
    rows.sort(key=lambda r: (r["place"], r["at"], r["item_id"]))
    man = manifest(rows, start, end, now, hc.code_ref(__file__), per, no_host)
    if a.id_suffix:
        man["dataset_id"] += a.id_suffix
    hc.write_dataset(Path(a.out) / man["dataset_id"], man, rows)
    print(f"{len(rows)} rows -> {man['dataset_id']}; requests {client.requests}, cached {client.hits}")


if __name__ == "__main__":
    sys.exit(main())
