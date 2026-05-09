"""Stage 5 — non-burnable mask via union of pluggable sources."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import rasterio

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import write_array
from wildfire_preproc.validation.fbfm40_codes import NON_BURNABLE_CODES


def _fbfm40_reclass(grid: GridSpec, fbfm40_path: Path) -> np.ndarray:
    with rasterio.open(fbfm40_path) as ds:
        arr = ds.read(1)
    if arr.shape != (grid.height, grid.width):
        raise ValueError(
            f"fbfm40 shape {arr.shape} does not match grid {(grid.height, grid.width)}"
        )
    out = np.zeros_like(arr, dtype="uint8")
    for code in NON_BURNABLE_CODES:
        out[arr == code] = 1
    return out


_SOURCE_REGISTRY: dict[str, Callable[[GridSpec, Path], np.ndarray]] = {
    "fbfm40": _fbfm40_reclass,
}


def build_non_burnable_mask(
    grid: GridSpec,
    fbfm40_path: Path,
    sources: list[str],
    out_path: Path,
) -> None:
    if not sources:
        raise ValueError("non_burnable_mask requires at least one source")
    union = np.zeros((grid.height, grid.width), dtype="uint8")
    for src in sources:
        fn = _SOURCE_REGISTRY.get(src)
        if fn is None:
            raise ValueError(f"unknown non_burnable source: {src!r}")
        layer = fn(grid, fbfm40_path)
        union |= layer
    write_array(out_path, union, grid, LayerKind.MASK)
