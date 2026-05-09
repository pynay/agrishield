"""Stage 6 — invariant checks per raster + cross-raster manifest checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKey, LayerKind
from wildfire_preproc.utils.raster import CATEGORICAL_NODATA, CONTINUOUS_NODATA
from wildfire_preproc.validation.fbfm40_codes import VALID_FBFM40_CODES


class ValidationError(Exception):
    pass


@dataclass
class ValidationResult:
    layer: str
    path: Path
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


_TRANSFORM_EPS = 1e-9


def _check_grid(ds: rasterio.io.DatasetReader, grid: GridSpec, errors: list[str]) -> None:
    if ds.crs != grid.crs:
        errors.append(f"crs mismatch: got {ds.crs}, expected {grid.crs}")
    if ds.width != grid.width or ds.height != grid.height:
        errors.append(
            f"dimensions mismatch: got ({ds.width}, {ds.height}),"
            f" expected ({grid.width}, {grid.height})"
        )
    for a, b in zip(tuple(ds.transform)[:6], tuple(grid.transform)[:6], strict=False):
        if abs(a - b) > _TRANSFORM_EPS:
            errors.append(
                f"transform mismatch: got {tuple(ds.transform)},"
                f" expected {tuple(grid.transform)}"
            )
            break
    if abs(abs(ds.transform.a) - grid.cell_size) > _TRANSFORM_EPS:
        errors.append(f"cell size x mismatch: got {abs(ds.transform.a)}, expected {grid.cell_size}")
    if abs(abs(ds.transform.e) - grid.cell_size) > _TRANSFORM_EPS:
        errors.append(f"cell size y mismatch: got {abs(ds.transform.e)}, expected {grid.cell_size}")


def _check_nodata(ds: rasterio.io.DatasetReader, kind: LayerKind, errors: list[str]) -> None:
    if kind == LayerKind.CONTINUOUS:
        if ds.nodata is None or abs(ds.nodata - CONTINUOUS_NODATA) > 1e-6:
            errors.append(f"continuous raster nodata must be {CONTINUOUS_NODATA}, got {ds.nodata}")
    elif kind == LayerKind.CATEGORICAL:
        if ds.nodata is None or int(ds.nodata) != CATEGORICAL_NODATA:
            errors.append(
                f"categorical raster nodata must be {CATEGORICAL_NODATA}, got {ds.nodata}"
            )
    elif kind == LayerKind.MASK:
        if ds.nodata is not None:
            errors.append(f"mask raster must have nodata=None, got {ds.nodata}")


def _check_data(
    arr: np.ndarray,
    layer: LayerKey,
    kind: LayerKind,
    nodata: float | int | None,
    errors: list[str],
) -> None:
    # All-nodata check
    if nodata is not None:
        if np.all(arr == nodata):
            errors.append("raster is entirely nodata")
            return
    # Mask values
    if kind == LayerKind.MASK:
        unique = set(np.unique(arr).tolist())
        if not unique.issubset({0, 1}):
            errors.append(f"mask values must be in {{0, 1}}, got {sorted(unique)}")
    # Categorical FBFM40 codes
    if layer == LayerKey.FBFM40:
        valid = arr[arr != nodata] if nodata is not None else arr
        invalid = set(np.unique(valid).tolist()) - set(VALID_FBFM40_CODES)
        if invalid:
            errors.append(f"fbfm40 invalid codes present: {sorted(invalid)[:10]}")
    # DEM sanity bounds
    if layer == LayerKey.DEM:
        valid_arr = arr[arr != nodata] if nodata is not None else arr
        if valid_arr.size > 0:
            if valid_arr.min() < -500 or valid_arr.max() > 9000:
                errors.append(
                    f"DEM out of plausible range:"
                    f" min={float(valid_arr.min())}, max={float(valid_arr.max())}"
                )


def validate_raster(
    path: Path, grid: GridSpec, layer: LayerKey, kind: LayerKind
) -> ValidationResult:
    res = ValidationResult(layer=layer.value, path=path)
    if not path.exists():
        res.errors.append(f"file does not exist: {path}")
        return res
    try:
        with rasterio.open(path) as ds:
            _check_grid(ds, grid, res.errors)
            _check_nodata(ds, kind, res.errors)
            arr = ds.read(1)
            _check_data(arr, layer, kind, ds.nodata, res.errors)
    except rasterio.errors.RasterioIOError as e:
        res.errors.append(f"cannot open raster: {e}")
    return res
