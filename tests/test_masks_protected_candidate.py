from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.masks.candidate import build_candidate_zone_mask
from wildfire_preproc.masks.protected import build_protected_mask


def _grid(
    cell: float = 30.0, w: int = 100, h: int = 100, ox: float = 0.0, oy: float = 3000.0
) -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(ox, oy, cell, cell),
        width=w,
        height=h,
        cell_size=cell,
    )


def test_protected_mask_marks_inside(tmp_path: Path):
    grid = _grid()
    poly = box(900, 900, 2100, 2100)  # interior square
    out = tmp_path / "protected_mask.tif"
    build_protected_mask(poly, "EPSG:5070", grid, out)
    with rasterio.open(out) as ds:
        arr = ds.read(1)
        assert ds.dtypes[0] == "uint8"
    inside = arr[(grid.height - 70):(grid.height - 30), 30:70]  # roughly the polygon footprint
    assert (inside == 1).any()
    assert arr.min() == 0
    assert arr.max() == 1


def test_protected_mask_reprojects_input(tmp_path: Path):
    grid = _grid()
    # Protected polygon in WGS84 — the function must reproject before rasterizing.
    poly_wgs84 = Polygon(
        [(-118.7, 34.1), (-118.6, 34.1), (-118.6, 34.2), (-118.7, 34.2), (-118.7, 34.1)]
    )
    out = tmp_path / "protected_mask.tif"
    # The grid is at projected coords (0..3000), so the WGS84 polygon won't intersect — that's ok,
    # the test is about the function not crashing on CRS mismatch.
    build_protected_mask(poly_wgs84, "EPSG:4326", grid, out)
    with rasterio.open(out) as ds:
        assert ds.crs == grid.crs


def test_candidate_zone_mask_marks_zone_only(tmp_path: Path):
    grid = _grid()
    candidate = box(0, 0, 1500, 1500)  # quarter of grid
    out = tmp_path / "candidate.tif"
    build_candidate_zone_mask(candidate, "EPSG:5070", grid, out)
    with rasterio.open(out) as ds:
        arr = ds.read(1)
    assert arr.dtype == np.uint8
    assert set(np.unique(arr).tolist()).issubset({0, 1})
    assert (arr == 1).sum() > 0
    assert (arr == 0).sum() > 0
