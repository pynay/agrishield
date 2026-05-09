"""Tiny rasterio helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind

CONTINUOUS_NODATA: float = -9999.0
CATEGORICAL_NODATA: int = 255


def dtype_for_kind(kind: LayerKind) -> str:
    if kind == LayerKind.CONTINUOUS:
        return "float32"
    if kind == LayerKind.CATEGORICAL:
        return "uint8"
    if kind == LayerKind.MASK:
        return "uint8"
    raise ValueError(f"unknown LayerKind: {kind}")


def nodata_for_kind(kind: LayerKind) -> float | int | None:
    if kind == LayerKind.CONTINUOUS:
        return CONTINUOUS_NODATA
    if kind == LayerKind.CATEGORICAL:
        return CATEGORICAL_NODATA
    if kind == LayerKind.MASK:
        return None
    raise ValueError(f"unknown LayerKind: {kind}")


def write_array(
    path: Path,
    data: np.ndarray,
    grid: GridSpec,
    kind: LayerKind,
) -> None:
    """Write a 2D array as a GeoTIFF matching the canonical grid + kind conventions."""
    if data.shape != (grid.height, grid.width):
        raise ValueError(
            f"array shape {data.shape} does not match grid {(grid.height, grid.width)}"
        )
    dtype = dtype_for_kind(kind)
    nodata = nodata_for_kind(kind)
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "LZW",
        "predictor": 2 if dtype.startswith("float") else 1,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)
