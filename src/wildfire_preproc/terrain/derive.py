"""Stage 4 — slope and aspect from DEM via the Horn (1981) 3x3 kernel."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import correlate

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import write_array

# Horn weights matching ESRI convention. dz/dx positive when east is higher;
# dz/dy positive when south is higher (rows are top-down in array indexing,
# so row r+1 is at lower y / south).
_KX = np.array(
    [
        [-1, 0, 1],
        [-2, 0, 2],
        [-1, 0, 1],
    ],
    dtype="float32",
)
_KY = np.array(
    [
        [-1, -2, -1],
        [ 0,  0,  0],
        [ 1,  2,  1],
    ],
    dtype="float32",
)

NODATA = -9999.0
ASPECT_FLAT = -1.0


def derive_slope_aspect(
    dem_path: Path,
    grid: GridSpec,
    out_slope_path: Path,
    out_aspect_path: Path,
) -> None:
    """Compute slope (degrees) and aspect (0-360 cw from N, flat=-1) from DEM."""
    with rasterio.open(dem_path) as ds:
        dem = ds.read(1).astype("float32")
        src_nodata = ds.nodata if ds.nodata is not None else NODATA
        cell = grid.cell_size

    if dem.shape != (grid.height, grid.width):
        raise ValueError(
            f"DEM shape {dem.shape} does not match grid {(grid.height, grid.width)}; "
            "DEM must already be aligned (Stage 3)."
        )

    valid = dem != src_nodata

    # correlate (not convolve) so kernel weights are applied as-written.
    dem_filled = np.where(valid, dem, 0.0)
    dz_dx = correlate(dem_filled, _KX, mode="constant", cval=0.0) / (8.0 * cell)
    dz_dy = correlate(dem_filled, _KY, mode="constant", cval=0.0) / (8.0 * cell)

    # Any cell whose 3x3 window touches nodata is nodata in the output.
    invalid_window = (
        correlate(
            (~valid).astype("uint8"),
            np.ones((3, 3), dtype="uint8"),
            mode="constant",
        )
        > 0
    )

    slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
    slope_deg = np.degrees(slope_rad).astype("float32")

    aspect_rad = np.arctan2(dz_dy, -dz_dx)
    aspect_deg = np.degrees(aspect_rad).astype("float32")
    # Convert math angle (0=+x ccw) to compass bearing (0=N cw).
    aspect_compass = (90.0 - aspect_deg) % 360.0

    flat = np.hypot(dz_dx, dz_dy) < 1e-7
    aspect_compass = np.where(flat, ASPECT_FLAT, aspect_compass).astype("float32")

    # Edge ring is unreliable: mark as nodata.
    edge = np.ones_like(slope_deg, dtype=bool)
    edge[1:-1, 1:-1] = False

    bad = invalid_window | edge

    slope_out = np.where(bad, NODATA, slope_deg).astype("float32")
    aspect_out = np.where(bad, NODATA, aspect_compass).astype("float32")

    write_array(out_slope_path, slope_out, grid, LayerKind.CONTINUOUS)
    write_array(out_aspect_path, aspect_out, grid, LayerKind.CONTINUOUS)
