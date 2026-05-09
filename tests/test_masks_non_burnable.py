from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.masks.non_burnable import build_non_burnable_mask


def _grid(w: int, h: int, cell: float = 30.0) -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, h * cell, cell, cell),
        width=w,
        height=h,
        cell_size=cell,
    )


def _write_fbfm40(path: Path, arr: np.ndarray, grid: GridSpec) -> None:
    with rasterio.open(
        path, "w",
        driver="GTiff", height=grid.height, width=grid.width,
        count=1, dtype="uint8", crs=grid.crs, transform=grid.transform, nodata=255,
    ) as ds:
        ds.write(arr.astype("uint8"), 1)


def test_fbfm40_reclass_marks_non_burnable_codes(tmp_path: Path):
    grid = _grid(10, 10)
    arr = np.full((10, 10), 101, dtype="uint8")  # all burnable grass
    arr[0, 0] = 91  # urban
    arr[1, 1] = 92  # snow
    arr[2, 2] = 93  # ag
    arr[3, 3] = 98  # water
    arr[4, 4] = 99  # barren
    fbfm = tmp_path / "fbfm40.tif"
    _write_fbfm40(fbfm, arr, grid)
    out = tmp_path / "non_burnable_mask.tif"
    build_non_burnable_mask(grid=grid, fbfm40_path=fbfm, sources=["fbfm40"], out_path=out)
    with rasterio.open(out) as ds:
        result = ds.read(1)
    assert result[0, 0] == 1
    assert result[1, 1] == 1
    assert result[2, 2] == 1
    assert result[3, 3] == 1
    assert result[4, 4] == 1
    # Burnable cells stay 0
    assert result[5, 5] == 0


def test_unknown_source_raises(tmp_path: Path):
    grid = _grid(5, 5)
    fbfm = tmp_path / "fbfm40.tif"
    _write_fbfm40(fbfm, np.full((5, 5), 101, dtype="uint8"), grid)
    out = tmp_path / "out.tif"
    with pytest.raises(ValueError, match="unknown non_burnable source"):
        build_non_burnable_mask(grid=grid, fbfm40_path=fbfm, sources=["osm"], out_path=out)


def test_empty_sources_raises(tmp_path: Path):
    grid = _grid(5, 5)
    fbfm = tmp_path / "fbfm40.tif"
    _write_fbfm40(fbfm, np.full((5, 5), 101, dtype="uint8"), grid)
    out = tmp_path / "out.tif"
    with pytest.raises(ValueError, match="at least one"):
        build_non_burnable_mask(grid=grid, fbfm40_path=fbfm, sources=[], out_path=out)
