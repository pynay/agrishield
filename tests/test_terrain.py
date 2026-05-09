from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.terrain.derive import derive_slope_aspect


def _write_dem(path: Path, data: np.ndarray, cell: float) -> None:
    with rasterio.open(
        path, "w",
        driver="GTiff", height=data.shape[0], width=data.shape[1],
        count=1, dtype="float32", crs=CRS.from_epsg(5070),
        transform=from_origin(0, data.shape[0] * cell, cell, cell),
        nodata=-9999.0,
    ) as ds:
        ds.write(data, 1)


def _grid(shape: tuple[int, int], cell: float) -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, shape[0] * cell, cell, cell),
        width=shape[1],
        height=shape[0],
        cell_size=cell,
    )


def test_flat_dem_yields_zero_slope_and_flat_aspect(tmp_path: Path):
    dem = tmp_path / "dem.tif"
    arr = np.full((20, 20), 100.0, dtype="float32")
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s, rasterio.open(out_asp) as a:
        slp = s.read(1)
        asp = a.read(1)
    interior_slp = slp[1:-1, 1:-1]
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_slp, 0.0, atol=1e-5)
    assert np.allclose(interior_asp, -1.0)  # flat = -1


def test_east_facing_slope(tmp_path: Path):
    # DEM increasing west-to-east: 1 m/cell over 30 m cells => ~1.91 deg slope, downslope west (270)
    dem = tmp_path / "dem.tif"
    cols = np.arange(20)
    arr = np.tile(cols, (20, 1)).astype("float32")  # z grows with x
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s, rasterio.open(out_asp) as a:
        slp = s.read(1)
        asp = a.read(1)
    expected_slope_deg = np.degrees(np.arctan(1.0 / 30.0))
    interior_slp = slp[1:-1, 1:-1]
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_slp, expected_slope_deg, atol=1e-3)
    # For a surface where elevation increases eastward, the downhill direction is west (270).
    assert np.allclose(interior_asp, 270.0, atol=1.0)


def test_edges_are_nodata(tmp_path: Path):
    dem = tmp_path / "dem.tif"
    arr = np.full((10, 10), 100.0, dtype="float32")
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s, rasterio.open(out_asp) as a:
        slp = s.read(1)
        asp = a.read(1)
    assert np.all(slp[0, :] == -9999.0)
    assert np.all(slp[-1, :] == -9999.0)
    assert np.all(slp[:, 0] == -9999.0)
    assert np.all(slp[:, -1] == -9999.0)
    assert np.all(asp[0, :] == -9999.0)


def test_slope_aspect_inputs_dem_nodata_propagates(tmp_path: Path):
    dem = tmp_path / "dem.tif"
    arr = np.full((10, 10), 100.0, dtype="float32")
    arr[5, 5] = -9999.0  # nodata in middle
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s:
        slp = s.read(1)
    # Cells whose 3x3 window touches nodata should be nodata.
    assert slp[5, 5] == -9999.0
    assert slp[4, 4] == -9999.0
    assert slp[6, 6] == -9999.0


def test_north_facing_slope(tmp_path: Path):
    # DEM increasing south-to-north (z grows as row index decreases —
    # elevation higher at top of array). Expected: downslope = south → aspect = 180.
    dem = tmp_path / "dem.tif"
    rows = np.arange(20, 0, -1, dtype="float32").reshape(20, 1)  # row 0 = 20 (highest), row 19 = 1
    arr = np.tile(rows, (1, 20))
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_asp) as a:
        asp = a.read(1)
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_asp, 180.0, atol=1.0)


def test_south_facing_slope(tmp_path: Path):
    # DEM increasing north-to-south (z grows as row index increases)
    # Expected: downslope direction is north → aspect = 0 (or 360).
    dem = tmp_path / "dem.tif"
    rows = np.arange(20, dtype="float32").reshape(20, 1)
    arr = np.tile(rows, (1, 20))
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_asp) as a:
        asp = a.read(1)
    interior_asp = asp[1:-1, 1:-1]
    # Aspect 0 and 360 are the same direction; check both.
    assert np.all(
        np.isclose(interior_asp, 0.0, atol=1.0) | np.isclose(interior_asp, 360.0, atol=1.0)
    )


def test_northeast_facing_slope(tmp_path: Path):
    # DEM where elevation grows toward NE (i.e., +x and -row); downslope = SW = aspect 225.
    dem = tmp_path / "dem.tif"
    cols = np.arange(20, dtype="float32").reshape(1, 20)  # increases east
    rows = np.arange(20, 0, -1, dtype="float32").reshape(20, 1)  # increases north
    arr = (cols + rows).astype("float32")
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_asp) as a:
        asp = a.read(1)
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_asp, 225.0, atol=1.5)


def test_southwest_facing_slope(tmp_path: Path):
    # DEM where elevation grows toward SW (-x and +row); downslope = NE = aspect 45.
    dem = tmp_path / "dem.tif"
    cols = np.arange(20, 0, -1, dtype="float32").reshape(1, 20)  # decreases east (grows west)
    rows = np.arange(20, dtype="float32").reshape(20, 1)  # grows south
    arr = (cols + rows).astype("float32")
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_asp) as a:
        asp = a.read(1)
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_asp, 45.0, atol=1.5)
