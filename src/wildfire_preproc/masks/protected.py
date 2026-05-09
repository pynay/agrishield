"""Stage 5 — rasterize a protected polygon into a 0/1 mask aligned to the canonical grid."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from shapely.geometry.base import BaseGeometry

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import write_array


def _rasterize_polygon(polygon: BaseGeometry, polygon_crs: str, grid: GridSpec) -> np.ndarray:
    gdf = gpd.GeoDataFrame(geometry=[polygon], crs=polygon_crs).to_crs(grid.crs)
    geom = gdf.geometry.iloc[0]
    raw = rasterize(
        shapes=[(geom, 1)],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    return np.asarray(raw, dtype=np.uint8)


def build_protected_mask(
    protected_polygon: BaseGeometry,
    polygon_crs: str,
    grid: GridSpec,
    out_path: Path,
) -> None:
    arr = _rasterize_polygon(protected_polygon, polygon_crs, grid)
    write_array(out_path, arr, grid, LayerKind.MASK)
