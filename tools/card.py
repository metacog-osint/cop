"""COP share card: a 1200x627 PNG of today's picks, used as the link-preview image (og:image).

Reads site/data/picks.json, today.geojson, health.json and world.geojson (written by snapshot.py), builds
out/card.html (self-contained: fonts embedded, map drawn as SVG) and screenshots it with headless Chrome, Chromium or
Edge into site/data/card.png. If no browser is found or the screenshot is the wrong size, it copies
site/card-fallback.png instead, so a link preview never breaks and the build never fails. Standard library only.

  python tools/card.py              today's card
  python tools/card.py --generic    the fallback card (no picks, no time), written to site/card-fallback.png
  python tools/card.py --stamp-page also add ?v=<assessment time> to the page's preview-image address (the build
                                    does this to its own copy), because social sites cache previews by address
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DATA = SITE / "data"
OUT = ROOT / "out"
FALLBACK = SITE / "card-fallback.png"
W, H = 1200, 627
MAP_W = 680                      # map on the left, picks on the right
INK, MUT, RULE, PANEL = "#13202E", "#5A6472", "#DCDED9", "#FFFFFF"
LAT_MAX = 80.0


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def when(s):
    """'2026-10-31T22:08:05Z' -> '31 Oct 2026, 22:08 UTC' (a picture can't know the viewer's zone, so UTC, named)."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    return f"{dt.day} {dt:%b %Y, %H:%M} UTC"


def day(s):
    """'2026-10-31T22:08:05Z' -> '31 Oct', or None when a pick's own time cannot be read (the card then leaves it
    out; one odd item must not stop the build)."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None
    return f"{dt.day} {dt:%b}"


def _positions_of(geom):
    """The positions placeOf boxes: a polygon's outer ring, the first polygon of a multipolygon, every position of a
    line or multipoint, the first part of a collection."""
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        return [c]
    if t in ("MultiPoint", "LineString"):
        return c or []
    if t == "Polygon":
        return c[0] if c else []
    if t == "MultiPolygon":
        return c[0][0] if c and c[0] else []
    if t == "MultiLineString":
        return [p for line in c or [] for p in line]
    if t == "GeometryCollection":
        parts = geom.get("geometries") or []
        return _positions_of(parts[0]) if parts else []
    return []


def place_of(geom):
    """Same rule as cop.js placeOf: a point, or the middle of the box around the shape's positions."""
    if not geom:
        return None
    if geom["type"] == "Point":
        return geom["coordinates"][:2]
    ring = _positions_of(geom)
    if not ring:
        return None
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]


# ---------------------------------------------------------------- Web Mercator, fitted to the picks
def merc(lon, lat):
    lat = max(-LAT_MAX, min(LAT_MAX, lat))
    return math.radians(lon), math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


class View:
    """Fits the pick points into a w x h box with padding, never narrower than 70 degrees of longitude."""

    def __init__(self, points, w, h, min_span=math.radians(70), pad=0.22):
        self.w, self.h = w, h
        ylim = merc(0, LAT_MAX)[1]
        if points:
            xy = [merc(*p) for p in points]
            x0, x1 = min(p[0] for p in xy), max(p[0] for p in xy)
            y0, y1 = min(p[1] for p in xy), max(p[1] for p in xy)
        else:                                    # no picks: Europe, Africa and Asia
            x0, y0 = merc(-20, -36)
            x1, y1 = merc(145, 70)
            pad = 0.0
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sx, sy = max(x1 - x0, 1e-6) * (1 + 2 * pad), max(y1 - y0, 1e-6) * (1 + 2 * pad)
        sx = max(sx, min_span)
        if sx / sy < w / h:
            sx = sy * w / h
        else:
            sy = sx * h / w
        if sx > 2 * math.pi:                     # never wider than the world
            sx, sy = 2 * math.pi, 2 * math.pi * h / w
        if sy > 2 * ylim:
            sy, sx = 2 * ylim, 2 * ylim * w / h
        cy = max(-ylim + sy / 2, min(ylim - sy / 2, cy))
        cx = max(-math.pi + sx / 2, min(math.pi - sx / 2, cx))
        self.x0, self.y1, self.s = cx - sx / 2, cy + sy / 2, w / sx

    def px(self, lon, lat):
        x, y = merc(lon, lat)
        return (x - self.x0) * self.s, (self.y1 - y) * self.s


def land_paths(view, world):
    """SVG path data for every land ring that touches the view (rings are clipped by the SVG viewport)."""
    parts = []
    for f in world["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
        for poly in polys:
            for ring in poly:
                pts = [view.px(lon, lat) for lon, lat in ring]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                if max(xs) < 0 or min(xs) > view.w or max(ys) < 0 or min(ys) > view.h:
                    continue
                parts.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
    return "".join(parts)


def spread(points, gap=40.0, rounds=40):
    """Nudges pins apart so numbers never overlap; returns new positions (the true spot keeps a small dot)."""
    pts = [list(p) for p in points]
    for _ in range(rounds):
        moved = False
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
                d = math.hypot(dx, dy)
                if d < gap:
                    if d < 1e-6:
                        dx, dy, d = 1.0, 0.0, 1.0
                    push = (gap - d) / 2 + 0.5
                    ux, uy = dx / d, dy / d
                    pts[i][0] -= ux * push; pts[i][1] -= uy * push
                    pts[j][0] += ux * push; pts[j][1] += uy * push
                    moved = True
        if not moved:
            break
    return pts


# ---------------------------------------------------------------- the card page
def font_face(family, weight, file):
    data = base64.b64encode((SITE / "fonts" / file).read_bytes()).decode("ascii")
    return (f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:normal;'
            f'src:url(data:font/woff2;base64,{data}) format("woff2");}}')


def build_html(picks_doc, today, health, world, generic=False):
    cfg = health["config"]
    progs = {p["id"]: p for p in cfg["programs"]}
    base = cfg["basemaps"]["light"]
    by_id = {f["properties"]["id"]: f for f in today["features"]}
    rows = []
    for p in ([] if generic else picks_doc.get("picks", [])):
        f = by_id.get(p["id"])
        at = place_of(f and f.get("geometry"))
        if f and at:
            rows.append((p, f, at))
    view = View([r[2] for r in rows], MAP_W, H)
    true_xy = [view.px(*r[2]) for r in rows]
    pin_xy = spread(true_xy)

    svg = [f'<svg class="map" viewBox="0 0 {MAP_W} {H}" width="{MAP_W}" height="{H}" xmlns="http://www.w3.org/2000/svg">',
           f'<rect width="{MAP_W}" height="{H}" fill="{base["water"]}"/>',
           f'<path d="{land_paths(view, world)}" fill="{base["land"]}" stroke="#AEB6BA" stroke-width="0.6"/>']
    for (p, f, at), (tx, ty), (x, y) in zip(rows, true_xy, pin_xy):
        col = progs[p["program"]]["color"]
        if math.hypot(x - tx, y - ty) > 1:
            svg.append(f'<line x1="{tx:.1f}" y1="{ty:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{INK}" stroke-width="1.5"/>')
        svg.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="{col["fill"]}" stroke="{col["outline_light"]}" stroke-width="1.5"/>')
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="17" fill="{INK}" stroke="{col["fill"]}" stroke-width="4"/>'
                   f'<text x="{x:.1f}" y="{y + 6.5:.1f}" text-anchor="middle" class="pin-n">{p["n"]}</text>')
    svg.append("</svg>")

    items = []
    for p, f, at in rows:
        pr, prog = f["properties"], progs[p["program"]]
        col = prog["color"]
        data_day = day(pr.get("data_as_of"))
        meta = f'{html.escape(prog["name"])} · data {data_day}' if data_day else html.escape(prog["name"])
        if pr.get("status_class") == "claimed":
            meta += " · claimed"
        if pr.get("cop_stale"):
            meta += " · no current data"
        title = pr.get("title")
        items.append(
            f'<li><span class="num">{p["n"]}</span><span class="txt"><span class="t">{html.escape("" if title is None else str(title))}</span>'
            f'<span class="m"><i class="dot" style="background:{col["fill"]};border-color:{col["outline_light"]}"></i>{meta}</span></span></li>')

    if generic:
        body = ('<h1>Every program, one map</h1>'
                '<p class="sub">The latest findings from every Intelligence Company program, each source credited and '
                'every measurement kept apart from judgment.</p>')
    else:
        stamp = f'Assessed {when(picks_doc["generated_at"])}'
        if items:
            body = (f'<h1>Today’s picks</h1><p class="when">{stamp}</p><ol>{"".join(items)}</ol>')
        else:
            body = (f'<h1>Today’s picks</h1><p class="when">{stamp}</p>'
                    '<p class="sub">No program has a placed anomaly right now.</p>')

    fonts = "".join([font_face("IBM Plex Sans", "400 600", "ibm-plex-sans-latin.woff2"),
                     font_face("IBM Plex Sans", "400 600", "ibm-plex-sans-latin-ext.woff2"),
                     font_face("IBM Plex Mono", "400", "ibm-plex-mono-400-latin.woff2"),
                     font_face("IBM Plex Mono", "500", "ibm-plex-mono-500-latin.woff2")])
    css = f"""{fonts}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:{PANEL};color:{INK};font:400 16px/1.4 "IBM Plex Sans",sans-serif}}
.card{{display:flex;width:{W}px;height:{H}px}}
.map{{display:block;flex:none}}
.pin-n{{font:600 19px "IBM Plex Sans",sans-serif;fill:#fff}}
.side{{flex:1;display:flex;flex-direction:column;padding:30px 34px 24px 34px;border-left:1px solid {RULE};min-width:0}}
.brand{{display:flex;align-items:center;gap:10px}}
.brand b{{font-size:24px;font-weight:600;letter-spacing:.02em}}
.brand span{{font-size:15px;color:{MUT}}}
h1{{font-size:30px;line-height:1.15;font-weight:600;margin:22px 0 4px}}
.when{{font:500 15px/1.3 "IBM Plex Mono",monospace;color:{MUT}}}
.sub{{font-size:19px;line-height:1.4;color:#34404F;margin-top:12px}}
ol{{list-style:none;margin-top:16px;display:flex;flex-direction:column;gap:11px}}
li{{display:flex;gap:12px;align-items:flex-start}}
.num{{flex:none;width:28px;height:28px;border-radius:50%;background:{INK};color:#fff;font-weight:600;font-size:15px;
  display:flex;align-items:center;justify-content:center;margin-top:1px}}
.txt{{display:flex;flex-direction:column;min-width:0}}
.t{{font-size:16.5px;line-height:1.3;font-weight:500;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.m{{font:400 13px/1.4 "IBM Plex Mono",monospace;color:{MUT};margin-top:2px;display:flex;align-items:center;gap:6px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;border:1.5px solid}}
.foot{{margin-top:auto;padding-top:12px;font:400 13px/1.35 "IBM Plex Mono",monospace;color:{MUT};display:flex;justify-content:space-between;gap:12px}}
.foot b{{color:{INK};font-weight:500}}
"""
    mark = ('<svg width="30" height="30" viewBox="0 0 32 32"><circle cx="16" cy="16" r="10.5" fill="none" '
            'stroke="#E3A83B" stroke-width="3"/><circle cx="16" cy="16" r="3.5" fill="#E3A83B"/></svg>')
    foot = ('<span>Measured or reported, not warnings</span>' if not generic else '<span>Free to read</span>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>COP card</title><style>{css}</style>'
            f'</head><body><div class="card">{"".join(svg)}<div class="side">'
            f'<div class="brand">{mark}<b>COP</b><span>The Intelligence Company</span></div>{body}'
            f'<div class="foot">{foot}<b>cop.theintelligence.company</b></div></div></div></body></html>')


# ---------------------------------------------------------------- screenshot
def find_browser():
    env = os.environ.get("COP_BROWSER")
    if env:
        return env
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        path = shutil.which(name)
        if path:
            return path
    for path in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(path).exists():
            return path
    return None


def png_size(path):
    head = Path(path).read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def screenshot(page, png):
    browser = find_browser()
    if not browser:
        return "no Chrome, Chromium or Edge found"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        cmd = [browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
               "--no-default-browser-check", "--force-device-scale-factor=1", f"--user-data-dir={profile}",
               f"--window-size={W},{H}", "--virtual-time-budget=5000", f"--screenshot={png}", page.as_uri()]
        if sys.platform.startswith("linux") and os.environ.get("CI"):
            cmd.insert(1, "--no-sandbox")    # our own local page; Ubuntu 24.04 runners block Chrome's sandbox
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError) as e:
            return f"browser failed: {e}"
        # Edge on Windows hands the work to a child process and returns early: wait for the file to settle
        last = -1
        for _ in range(60):
            size = Path(png).stat().st_size if Path(png).exists() else -1
            if size > 0 and size == last:
                break
            last = size
            time.sleep(0.5)
    if not Path(png).exists():
        return "browser wrote no file"
    size = png_size(png)
    if size != (W, H):
        return f"screenshot is {size}, expected {(W, H)}"
    return None


def stamp_page(page_html, generated_at):
    """Adds ?v=YYYYMMDDHHMM to the og:image and twitter:image addresses, so each new card is a new address."""
    v = re.sub(r"\D", "", generated_at)[:12]
    out, n = re.subn(r'(content="https://cop\.theintelligence\.company/data/card\.png)(\?v=\d+)?"',
                     lambda m: f'{m.group(1)}?v={v}"', page_html)
    return out, n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generic", action="store_true", help="write the fallback card to site/card-fallback.png")
    ap.add_argument("--html-only", action="store_true", help="write out/card.html and stop")
    ap.add_argument("--stamp-page", action="store_true", help="version the preview-image address in site/index.html")
    args = ap.parse_args(argv)
    OUT.mkdir(exist_ok=True)
    page = OUT / ("card-generic.html" if args.generic else "card.html")
    world = load("world.geojson")
    target = FALLBACK if args.generic else DATA / "card.png"
    if args.generic:
        health = load("health.json")
        page.write_text(build_html({}, {"features": []}, health, world, generic=True), encoding="utf-8")
        problem = None
    else:
        # The card is a nicety: a pick it cannot draw costs today's card, never the build (15 Nov 2026).
        try:
            page.write_text(build_html(load("picks.json"), load("today.geojson"), load("health.json"), world),
                            encoding="utf-8")
            problem = None
        except Exception as e:  # noqa: BLE001
            problem = f"the card page could not be built ({type(e).__name__}: {e})"
    if args.html_only:
        print(f"wrote {page}" if problem is None else f"card: {problem}")
        return 0 if problem is None else 1
    tmp = OUT / (target.stem + ".tmp.png")
    tmp.unlink(missing_ok=True)
    if problem is None:
        problem = screenshot(page, tmp)
    if problem is None:
        shutil.move(str(tmp), target)
        print(f"wrote {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KB)")
        if args.stamp_page:
            index = SITE / "index.html"
            stamped, n = stamp_page(index.read_text(encoding="utf-8"), load("picks.json")["generated_at"])
            index.write_text(stamped, encoding="utf-8")
            print(f"stamped {n} preview-image addresses in site/index.html")
        return 0
    if args.generic:
        print(f"card: {problem}", file=sys.stderr)
        return 1
    print(f"card: {problem}; using the fallback card", file=sys.stderr)
    if FALLBACK.exists():
        shutil.copyfile(FALLBACK, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
