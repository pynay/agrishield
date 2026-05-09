import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from wildfire_preproc.align.align import reproject_match
from wildfire_preproc.align.grid import gridspec_from_polygon
from wildfire_preproc.config import LayerKind


def _write_test_raster(path, data, transform, crs, nodata=None, dtype=None):
    dtype = dtype or data.dtype
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def test_reproject_match_continuous_bilinear(tmp_path):
    # Source: 60x60 raster at 10m, all values=42
    src = tmp_path / "src.tif"
    data = np.full((60, 60), 42.0, dtype="float32")
    transform = from_origin(0, 600, 10, 10)
    _write_test_raster(src, data, transform, CRS.from_epsg(5070), nodata=-9999.0)

    grid = gridspec_from_polygon(box(0, 0, 600, 600), CRS.from_epsg(5070), cell_size=30.0)
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CONTINUOUS, dst_nodata=-9999.0)

    with rasterio.open(out) as ds:
        assert ds.crs == grid.crs
        assert ds.width == grid.width
        assert ds.height == grid.height
        assert tuple(ds.transform) == tuple(grid.transform)
        arr = ds.read(1)
        # Bilinear of a constant field is the constant.
        assert np.allclose(arr, 42.0)


def test_reproject_match_categorical_nearest(tmp_path):
    src = tmp_path / "src.tif"
    data = np.full((60, 60), 101, dtype="uint8")  # FBFM40 grass code
    transform = from_origin(0, 600, 10, 10)
    _write_test_raster(src, data, transform, CRS.from_epsg(5070), nodata=255, dtype="uint8")

    grid = gridspec_from_polygon(box(0, 0, 600, 600), CRS.from_epsg(5070), cell_size=30.0)
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CATEGORICAL, dst_nodata=255)

    with rasterio.open(out) as ds:
        arr = ds.read(1)
        # Nearest-neighbor must preserve the categorical code (no fractional values).
        assert set(np.unique(arr).tolist()) == {101}
        assert ds.dtypes[0] == "uint8"


def test_reproject_match_emits_correct_dtype_for_kind(tmp_path):
    src = tmp_path / "src.tif"
    data = np.full((60, 60), 42.0, dtype="float64")
    transform = from_origin(0, 600, 10, 10)
    _write_test_raster(src, data, transform, CRS.from_epsg(5070), nodata=-9999.0, dtype="float64")
    grid = gridspec_from_polygon(box(0, 0, 600, 600), CRS.from_epsg(5070), cell_size=30.0)
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CONTINUOUS, dst_nodata=-9999.0)
    with rasterio.open(out) as ds:
        assert ds.dtypes[0] == "float32"  # continuous always float32


def test_reproject_match_warps_across_crs(tmp_path):
    # Source in EPSG:4326 (lon/lat). Use a small AOI in CONUS where 5070 is well-defined.
    # Source raster: 100x100 cells of ~0.001 deg (~111m) each, with a north-south gradient.
    src = tmp_path / "src.tif"
    rows = np.arange(100, dtype="float32").reshape(100, 1)
    data = np.tile(rows, (1, 100))  # gradient: row 0 = 0, row 99 = 99
    transform = from_origin(-100.0, 40.0, 0.001, 0.001)  # west=-100, north=40, ~111m cells
    _write_test_raster(src, data, transform, CRS.from_epsg(4326), nodata=-9999.0)

    # Target grid in EPSG:5070, covering roughly the same AOI.
    # Bounds derived from projecting the 4326 source corners to EPSG:5070:
    #   lon/lat (-100, 39.9)..(-99.9, 40.0) -> x ~ -339k..-330k, y ~ 1883k..1894k
    grid = gridspec_from_polygon(
        box(-340_000.0, 1_882_000.0, -329_000.0, 1_895_000.0),
        CRS.from_epsg(5070),
        cell_size=300.0,  # 300m cell to keep test small
    )
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CONTINUOUS, dst_nodata=-9999.0)

    with rasterio.open(out) as ds:
        assert ds.crs == grid.crs
        assert ds.width == grid.width
        assert ds.height == grid.height
        arr = ds.read(1)
        valid = arr[arr != -9999.0]
        assert valid.size > 0  # warping produced some valid pixels
        # Bilinear of a north-south gradient yields values in the source's range (0..99).
        assert valid.min() >= -1.0 and valid.max() <= 100.0
