import dataclasses

import pytest
from rasterio.crs import CRS
from shapely.geometry import box

from wildfire_preproc.align.grid import gridspec_from_polygon


def test_gridspec_snaps_to_global_origin():
    poly = box(100.5, 200.5, 250.5, 350.5)  # arbitrary projected bounds
    grid = gridspec_from_polygon(poly, crs=CRS.from_epsg(5070), cell_size=30.0)
    minx = grid.transform.c
    maxy = grid.transform.f
    # Snap outward to multiples of 30, anchored at (0, 0):
    # minx_snapped = floor(100.5 / 30) * 30 = 90
    # maxy_snapped = ceil(350.5 / 30) * 30 = 360
    assert minx == pytest.approx(90.0)
    assert maxy == pytest.approx(360.0)


def test_gridspec_dimensions_cover_bounds():
    poly = box(0.0, 0.0, 90.0, 60.0)
    grid = gridspec_from_polygon(poly, crs=CRS.from_epsg(5070), cell_size=30.0)
    assert grid.width == 3
    assert grid.height == 2
    assert grid.cell_size == 30.0


def test_gridspec_two_overlapping_jobs_share_pixel_grid():
    poly_a = box(105.0, 205.0, 245.0, 345.0)
    poly_b = box(125.0, 225.0, 265.0, 365.0)
    grid_a = gridspec_from_polygon(poly_a, crs=CRS.from_epsg(5070), cell_size=30.0)
    grid_b = gridspec_from_polygon(poly_b, crs=CRS.from_epsg(5070), cell_size=30.0)
    # Both should be aligned to the same global grid: (minx % 30) == 0.
    assert grid_a.transform.c % 30.0 == pytest.approx(0.0)
    assert grid_b.transform.c % 30.0 == pytest.approx(0.0)
    assert grid_a.transform.f % 30.0 == pytest.approx(0.0)
    assert grid_b.transform.f % 30.0 == pytest.approx(0.0)


def test_gridspec_is_frozen():
    poly = box(0, 0, 90, 60)
    grid = gridspec_from_polygon(poly, crs=CRS.from_epsg(5070), cell_size=30.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        grid.width = 99  # type: ignore[misc]
