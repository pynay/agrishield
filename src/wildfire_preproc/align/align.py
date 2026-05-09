"""reproject_match — the only function that produces an aligned raster."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import dtype_for_kind


def _resampling_for(kind: LayerKind) -> Resampling:
    if kind == LayerKind.CATEGORICAL:
        return Resampling.nearest
    if kind == LayerKind.CONTINUOUS:
        return Resampling.bilinear
    if kind == LayerKind.MASK:
        return Resampling.nearest
    raise ValueError(f"unknown LayerKind: {kind}")


def reproject_match(
    src: Path,
    dst: Path,
    grid: GridSpec,
    kind: LayerKind,
    dst_nodata: float | int | None,
) -> None:
    """Reproject `src` raster onto the canonical `grid`, writing `dst`."""
    dtype = dtype_for_kind(kind)
    resampling = _resampling_for(kind)

    with rasterio.open(src) as s:
        src_band = s.read(1)
        src_crs = s.crs
        src_transform = s.transform
        src_nodata = s.nodata

    fill_val = dst_nodata if dst_nodata is not None else 0
    out = np.full((grid.height, grid.width), fill_val, dtype=dtype)

    reproject(
        source=src_band,
        destination=out,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        dst_nodata=dst_nodata,
        resampling=resampling,
    )

    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": dst_nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "LZW",
        "predictor": 2 if dtype.startswith("float") else 1,
    }
    with rasterio.open(dst, "w", **profile) as d:
        d.write(out, 1)
