"""Offline tests for COP's grid cell (tools/cells.py, `cop_cells` on every bus item). Run: python tests/test_cells.py"""

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import cells  # noqa: E402

POINTS = ROOT / "tests" / "fixtures" / "h3-res2-points.json"
DOHA = "82536ffffffffff"   # the resolution 2 cell Doha lies in (h3-py latlng_to_cell)
H3_RING = [[52.6956, 27.2771], [53.3536, 25.8025], [55.11, 25.4308], [56.2814, 26.53], [55.6709, 28.0459],
           [53.839, 28.4214], [52.6956, 27.2771]]   # cell 824317fffffffff's outline to 4 places, as Noisefloor sends it


def feature(geometry, precision_class):
    return {"type": "Feature", "geometry": geometry, "properties": {"id": "t", "precision_class": precision_class}}


class Points(unittest.TestCase):
    def test_matches_h3_on_3000_points(self):
        """Every stored point (random, by area and by latitude, the poles, the 180th meridian, the 12 pentagons and
        points beside them) gets the cell h3-py's latlng_to_cell gave it."""
        rows = json.loads(POINTS.read_text(encoding="utf-8"))["points"]
        self.assertGreater(len(rows), 3000)
        wrong = [(lat, lng, want, cells.cell_at(lat, lng)) for lat, lng, want in rows if cells.cell_at(lat, lng) != want]
        self.assertEqual(wrong, [])

    def test_matches_h3_when_it_is_installed(self):
        try:
            import h3
        except ImportError:
            self.skipTest("h3 is not installed (the build does not need it)")
        import random
        rng = random.Random(7)
        for _ in range(500):
            lat, lng = rng.uniform(-90, 90), rng.uniform(-180, 180)
            self.assertEqual(cells.cell_at(lat, lng), h3.latlng_to_cell(lat, lng, 2), (lat, lng))

    def test_a_point_gets_its_one_cell(self):
        self.assertEqual(cells.cells_for({"type": "Point", "coordinates": [51.531, 25.2854]}, "point"), [DOHA])
        self.assertEqual(cells.cells_for({"type": "Point", "coordinates": [51.531, 25.2854]}, "approximate"), [DOHA])
        self.assertEqual(cells.cells_for({"type": "Point", "coordinates": [51.531, 25.2854]}, None), [DOHA])

    def test_a_multipoint_gets_the_cell_of_each_point(self):
        got = cells.cells_for({"type": "MultiPoint", "coordinates": [[51.531, 25.2854], [51.6, 25.3], [30.52, 50.45]]},
                              "approximate")
        self.assertEqual(got, sorted({DOHA, cells.cell_at(50.45, 30.52)}))

    def test_a_point_off_the_globe_gets_none(self):
        self.assertIsNone(cells.cell_at(91, 0))
        self.assertIsNone(cells.cell_at("x", 0))
        self.assertEqual(cells.cells_for({"type": "Point", "coordinates": [0, 95]}, "point"), [])


class StandIns(unittest.TestCase):
    def test_a_country_point_gets_no_cell(self):
        """A country's label point is not a place: a join on cells must never meet it there."""
        for pc in ("country", "admin1", "area", "none"):
            self.assertEqual(cells.cells_for({"type": "Point", "coordinates": [51.18, 25.32]}, pc), [], pc)
            self.assertEqual(cells.cells_for({"type": "MultiPoint", "coordinates": [[51.18, 25.32]]}, pc), [], pc)

    def test_no_geometry_no_cell(self):
        self.assertEqual(cells.cells_for(None, "none"), [])
        self.assertEqual(cells.cells_for(None, None), [])
        self.assertEqual(cells.cells_for({"type": "Polygon", "coordinates": []}, "area"), [])


class Areas(unittest.TestCase):
    def test_an_h3_cell_outline_gets_that_cell_and_not_its_neighbours(self):
        """Another program's H3 cell, rounded to 4 places, shares its edges with six neighbours; it touches them only,
        so it takes its own cell alone."""
        self.assertEqual(cells.cells_for({"type": "Polygon", "coordinates": [H3_RING]}, "area"), ["824317fffffffff"])

    def test_an_area_gets_every_cell_it_overlaps(self):
        """A box over the Gulf covers cells none of its corners lies in; every cell its corners lie in is there too."""
        box = [[50.0, 24.0], [57.0, 24.0], [57.0, 30.0], [50.0, 30.0], [50.0, 24.0]]
        got = cells.cells_for({"type": "Polygon", "coordinates": [box]}, "area")
        corners = {cells.cell_at(lat, lng) for lng, lat in box}
        self.assertTrue(corners <= set(got), "the corners' cells")
        self.assertIn(DOHA, got, "Doha, inside the box")
        self.assertGreater(len(got), len(corners))
        self.assertEqual(got, sorted(got))
        # and nothing far outside it: every cell found lies within one cell of the box
        for c in got:
            lng, lat = cells_centre(c)
            self.assertTrue(48 < lng < 59 and 22 < lat < 32, c)

    def test_a_small_area_inside_one_cell(self):
        ring = [[51.5, 25.27], [51.56, 25.27], [51.56, 25.3], [51.5, 25.3], [51.5, 25.27]]
        self.assertEqual(cells.cells_for({"type": "Polygon", "coordinates": [ring]}, "area"), [DOHA])

    def test_a_line_gets_the_cells_it_crosses(self):
        line = {"type": "LineString", "coordinates": [[50.0, 26.0], [57.0, 26.0]]}
        got = cells.cells_for(line, "point")
        self.assertIn(cells.cell_at(26.0, 50.0), got)
        self.assertIn(cells.cell_at(26.0, 57.0), got)
        self.assertGreaterEqual(len(got), 3)

    def test_a_label_point_beside_an_outline_adds_nothing(self):
        ring = [[51.5, 25.27], [51.56, 25.27], [51.56, 25.3], [51.5, 25.3], [51.5, 25.27]]
        geom = {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [30.52, 50.45]},
                                                             {"type": "Polygon", "coordinates": [ring]}]}
        self.assertEqual(cells.cells_for(geom, "area"), [DOHA])

    def test_areas_on_both_sides_of_the_180th_meridian(self):
        """A cell across the meridian is stored whole (one side moved by 360); areas on either side still meet it."""
        for lo, hi in ((179.0, 179.95), (-179.95, -179.0)):
            ring = [[lo, -17.0], [hi, -17.0], [hi, -16.0], [lo, -16.0], [lo, -17.0]]
            got = cells.cells_for({"type": "Polygon", "coordinates": [ring]}, "area")
            for lng in (lo, hi):
                for lat in (-17.0, -16.0):
                    self.assertIn(cells.cell_at(lat, lng), got, (lat, lng))

    def test_an_area_round_a_pole(self):
        ring = [[0, 89.5], [90, 89.5], [180, 89.5], [-90, 89.5], [0, 89.5]]
        got = cells.cells_for({"type": "Polygon", "coordinates": [ring]}, "area")
        self.assertIn(cells.cell_at(90, 0), got)


def cells_centre(cid):
    flat = json.loads(cells.TABLE.read_text(encoding="utf-8"))["cells"][cid]
    return sum(flat[0::2]) / (len(flat) / 2), sum(flat[1::2]) / (len(flat) / 2)


class Snapshot(unittest.TestCase):
    def test_every_bus_item_carries_cells(self):
        spec = importlib.util.spec_from_file_location("snapshot", ROOT / "tools" / "snapshot.py")
        snap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(snap)
        fix = ROOT / "tests" / "fixtures"
        feeds, reports = {}, {}
        for p in snap.CONFIG["programs"]:
            feeds[p["id"]], reports[p["id"]] = snap.read_local(fix / f"{p['id']}.geojson")
        today, *_ = snap.build(feeds, reports, None, datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc))
        self.assertTrue(today["features"])
        for f in today["features"]:
            p = f["properties"]
            self.assertIsInstance(p.get(cells.FIELD), list, p["id"])
            self.assertEqual(p[cells.FIELD], cells.cells_for(f.get("geometry"), p.get("precision_class")), p["id"])
            if not f.get("geometry"):
                self.assertEqual(p[cells.FIELD], [], p["id"])
        self.assertTrue(any(f["properties"][cells.FIELD] for f in today["features"]), "some item is placed")

    def test_the_method_page_states_the_rule(self):
        html = (ROOT / "site" / "scoring.html").read_text(encoding="utf-8")
        self.assertIn('id="cells"', html)
        self.assertIn("<code>cop_cells</code>", html)
        self.assertIn(f"resolution {json.loads(cells.TABLE.read_text(encoding='utf-8'))['resolution']}", html)
        self.assertIn(f"{cells.SHRINK * 100:g}%", html)


if __name__ == "__main__":
    unittest.main()
