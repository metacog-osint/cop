"""Write COP's grid table, tools/data/h3-res2.json, and the test points that check tools/cells.py against H3 itself.

The build never runs this: it needs the `h3` package (h3-py, Apache License 2.0), which the build does not install.
tools/cells.py reads the table with the standard library alone. Re-run it only to change the grid or its resolution,
then run tests/test_cells.py.

    pip install h3==4.5.0
    python tools/make_cells.py
"""

import json
import math
import random
from pathlib import Path

import h3

ROOT = Path(__file__).resolve().parents[1]
RES = 2
TABLE = ROOT / "tools" / "data" / f"h3-res{RES}.json"
POINTS = ROOT / "tests" / "fixtures" / f"h3-res{RES}-points.json"
PLACES = 6  # decimal places, about 0.1 m


def main():
    cells = sorted(c for base in h3.get_res0_cells() for c in h3.cell_to_children(base, RES))
    table = {
        "grid": "H3", "resolution": RES,
        "basis": f"Every H3 cell at resolution {RES} and its outline, as h3-py {h3.__version__} gives it "
                 f"(cell_to_boundary), latitude and longitude rounded to {PLACES} decimal places. Written by "
                 "tools/make_cells.py; read by tools/cells.py.",
        "cells": {c: [v for lat, lng in h3.cell_to_boundary(c) for v in (round(lng, PLACES), round(lat, PLACES))]
                  for c in cells},
    }
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    TABLE.write_text(json.dumps(table, separators=(",", ":")) + "\n", encoding="utf-8")

    rng = random.Random(20261127)
    pts = [(rng.uniform(-90, 90), rng.uniform(-180, 180)) for _ in range(1500)]
    # Points spread by area, not by latitude, so the high latitudes are not over-sampled.
    pts += [(math.degrees(math.asin(rng.uniform(-1, 1))), rng.uniform(-180, 180)) for _ in range(1500)]
    # The awkward places: the poles, the 180th meridian, the 12 pentagons and points just off their centres.
    pts += [(90, 0), (-90, 0), (89.99, 45), (-89.99, -135), (0, 180), (0, -180), (45, 179.999), (-45, -179.999),
            (25.2854, 51.531), (26.376, 56.341), (50.45, 30.52)]
    for p in h3.get_pentagons(RES):
        lat, lng = h3.cell_to_latlng(p)
        pts += [(lat, lng), (lat + 0.3, lng), (lat - 0.3, lng + 0.3)]
    rows = [[round(lat, 6), round(lng, 6), h3.latlng_to_cell(round(lat, 6), round(lng, 6), RES)] for lat, lng in pts]
    POINTS.write_text(json.dumps({"resolution": RES, "basis": f"h3-py {h3.__version__} latlng_to_cell",
                                  "points": rows}, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(cells)} cells to {TABLE.relative_to(ROOT)}; {len(rows)} test points to {POINTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
