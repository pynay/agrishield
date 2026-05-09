"""Stage 5 — rasterize the candidate firebreak zone polygon into a 0/1 mask."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry.base import BaseGeometry

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.masks.protected import _rasterize_polygon
from wildfire_preproc.utils.raster import write_array


def build_candidate_zone_mask(
    candidate_polygon: BaseGeometry,
    polygon_crs: str,
    grid: GridSpec,
    out_path: Path,
) -> None:
    arr = _rasterize_polygon(candidate_polygon, polygon_crs, grid)
    write_array(out_path, arr, grid, LayerKind.MASK)
