"""COP's grid cell: the H3 cells (resolution 2) each placed item's footprint covers, added at ingest as `cop_cells`.

The grid is COP's, not the feed contract's: COP computes the cells for every item it reads, so items from different
layers can be joined on a common cell as well as on `countries`, and the grid can change without touching any feed.
Resolution 2 cells are about 87,000 km² (some 158 km a side), the grid Noisefloor's regions already use.

- A point gets the one cell it lies in, the same cell H3's own latlng_to_cell gives (tests/test_cells.py checks
  3,000 points against it). A multipoint gets the cell of each point.
- An area or a line gets every cell it overlaps, by its outline as GeoJSON draws it (straight lines in longitude and
  latitude, as the kit's countries_overlapping() reads them). An outline that only touches a cell along its edge
  (an H3 cell from another program, for example) does not take the neighbours: each cell is shrunk by 0.2% (about
  300 m) for the test.
- A point that stands for a wider place gets no cell, so a join never takes a country's or a zone's label point for
  a place: precision_class "country", "admin1", "area" or "none" on a point or multipoint. Beside an outline (a
  GeometryCollection), points are label points and add nothing.
- No geometry, no cell. `cop_cells` is then [].

Standard-library Python: the cell outlines come from tools/data/h3-res2.json, which tools/make_cells.py writes with
the h3 package (h3-py) outside the build.
"""

import json
import math
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))
from tic_doctrine import geo as kitgeo  # noqa: E402

TABLE = ROOT / "tools" / "data" / "h3-res2.json"
FIELD = "cop_cells"
STAND_IN = frozenset({"country", "admin1", "area", "none"})  # a point of these classes is not a place of its own
SHRINK = 0.002  # each cell shrunk by this share for the overlap test, so an outline along a cell's edge stays out
BUCKET = 4.0    # degrees: the lookup's buckets of cell centres


def _vec(lng, lat):
    la, lo = math.radians(lat), math.radians(lng)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _in_ring2(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class _Cell:
    """One cell: its centre, its outline on the plane tangent at the centre (gnomonic, so H3's edges, which are
    great-circle arcs, stay straight), and its outline in longitude and latitude for the overlap test."""
    __slots__ = ("id", "center", "e1", "e2", "flat", "ring", "box", "polar")

    def __init__(self, cid, flat_lnglat):
        verts = [(flat_lnglat[i], flat_lnglat[i + 1]) for i in range(0, len(flat_lnglat), 2)]
        vs = [_vec(lng, lat) for lng, lat in verts]
        c = _unit((sum(v[0] for v in vs), sum(v[1] for v in vs), sum(v[2] for v in vs)))
        e1 = _unit(_cross3((0.0, 0.0, 1.0), c)) if abs(c[2]) < 0.999 else _unit(_cross3((1.0, 0.0, 0.0), c))
        self.id, self.center, self.e1, self.e2 = cid, c, e1, _cross3(c, e1)
        self.flat = [self._project(v) for v in vs]
        self.polar = self.contains(_vec(0, 90)) or self.contains(_vec(0, -90))
        lngs = [lng for lng, _ in verts]
        if max(lngs) - min(lngs) > 180:  # across the 180th meridian: one side moved by 360 so the outline is whole
            verts = [(lng + 360 if lng < 0 else lng, lat) for lng, lat in verts]
        mx, my = sum(v[0] for v in verts) / len(verts), sum(v[1] for v in verts) / len(verts)
        ring = [(mx + (x - mx) * (1 - SHRINK), my + (y - my) * (1 - SHRINK)) for x, y in verts]
        self.ring = ring + [ring[0]]
        self.box = [min(x for x, _ in ring), min(y for _, y in ring), max(x for x, _ in ring), max(y for _, y in ring)]

    def _project(self, v):
        d = _dot(v, self.center)
        return (_dot(v, self.e1) / d, _dot(v, self.e2) / d) if d > 0 else None

    def contains(self, v):
        p = self._project(v)
        return p is not None and _in_ring2(p[0], p[1], self.flat)


@cache
def _grid():
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    cells = [_Cell(cid, flat) for cid, flat in sorted(table["cells"].items())]
    reach = 0.0  # the widest angle from a cell's centre to one of its corners, in degrees
    buckets = {}
    for cell in cells:
        lat = math.degrees(math.asin(max(-1.0, min(1.0, cell.center[2]))))
        lng = math.degrees(math.atan2(cell.center[1], cell.center[0]))
        buckets.setdefault(_bucket(lat, lng), []).append(cell)
    for cell, flat in zip(cells, (table["cells"][c.id] for c in cells)):
        for i in range(0, len(flat), 2):
            reach = max(reach, math.degrees(math.acos(max(-1.0, min(1.0, _dot(cell.center, _vec(flat[i], flat[i + 1])))))))
    return {"resolution": table["resolution"], "cells": cells, "buckets": buckets, "reach": reach}


def _bucket(lat, lng):
    rows, cols = int(180 / BUCKET), int(360 / BUCKET)
    return min(rows - 1, int((lat + 90) // BUCKET)), int(((lng + 180) % 360) // BUCKET) % cols


def _near(lat, lng):
    """The cells whose centres lie within reach of a point, from the buckets around it."""
    g = _grid()
    rows, cols = int(180 / BUCKET), int(360 / BUCKET)
    r0, c0 = _bucket(lat, lng)
    span = math.ceil(g["reach"] / BUCKET) + 1
    row_range = range(max(0, r0 - span), min(rows, r0 + span + 1))
    top = max(max(abs(-90 + r * BUCKET), abs(-90 + (r + 1) * BUCKET)) for r in row_range)
    cos = math.cos(math.radians(min(90.0, top)))
    k = math.ceil(g["reach"] / cos / BUCKET) + 1 if cos > 0.05 else cols
    col_range = range(cols) if 2 * k + 1 >= cols else [(c0 + d) % cols for d in range(-k, k + 1)]
    out = []
    for r in row_range:
        for c in col_range:
            out += g["buckets"].get((r, c), [])
    return out


def cell_at(lat, lng):
    """The H3 cell (resolution 2) a point lies in, as H3's latlng_to_cell gives it, or None for a point off the globe."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)) or abs(lat) > 90:
        return None
    v = _vec(lng, lat)
    near = sorted(_near(lat, lng), key=lambda cell: -_dot(v, cell.center))
    for cell in near:
        if cell.contains(v):
            return cell.id
    return near[0].id if near else None  # on an edge to within rounding: the nearest centre


def _overlapping(lines, polys, vertex_cells):
    """Every cell an outline or a line overlaps (planar, in longitude and latitude, each cell shrunk by SHRINK).
    vertex_cells: the cells the shape's own vertices lie in, which decide the two cells round the poles."""
    g = _grid()
    shape = ([], lines, polys)
    pts = [p for ln in lines for p in ln] + [p for poly in polys for r in poly for p in r]
    box = [min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts), max(y for _, y in pts)]
    found = set()
    for cell in g["cells"]:
        if cell.polar:  # a cell round a pole has no outline on the plane; it counts when a vertex lies in it
            if cell.id in vertex_cells:
                found.add(cell.id)
            continue
        for shift in (0, -360):
            b = cell.box
            if not kitgeo.boxes_intersect(box, [b[0] + shift, b[1], b[2] + shift, b[3]]):
                continue
            ring = cell.ring if not shift else [(x + shift, y) for x, y in cell.ring]
            if kitgeo._overlaps(shape, [[ring]]):
                found.add(cell.id)
                break
    return found


def cells_for(geometry, precision_class=None):
    """Sorted H3 cells (resolution 2) of an item's footprint, by the rules in this module's docstring."""
    pts, lines, polys = kitgeo._parts(geometry)
    lines = [ln for ln in lines if ln]
    polys = [poly for poly in polys if poly and poly[0]]
    if lines or polys:
        vertex_cells = {cell_at(y, x) for x, y in [p for ln in lines for p in ln]
                        + [p for poly in polys for r in poly for p in r] if abs(y) > 80}  # only the poles' cells
        return sorted(_overlapping(lines, polys, vertex_cells))
    if not pts or precision_class in STAND_IN:
        return []
    return sorted({c for c in (cell_at(y, x) for x, y in pts) if c})


def add(features):
    """Set `cop_cells` on every feature. Returns counts for the build log: items with cells, stand-ins left without,
    and items with no geometry."""
    counts = {"placed": 0, "stand_in": 0, "unplaced": 0}
    for f in features:
        p = f.get("properties")
        if not isinstance(p, dict):
            continue
        p[FIELD] = cells_for(f.get("geometry"), p.get("precision_class"))
        counts["placed" if p[FIELD] else "stand_in" if f.get("geometry") else "unplaced"] += 1
    return counts
