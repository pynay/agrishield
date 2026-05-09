"""Canonical grid specification — frozen after Stage 3."""

from __future__ import annotations

import math
from dataclasses import dataclass

from rasterio.crs import CRS
from rasterio.transform import Affine, from_origin
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class GridSpec:
    crs: CRS
    transform: Affine
    width: int
    height: int
    cell_size: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        minx = self.transform.c
        maxy = self.transform.f
        maxx = minx + self.width * self.cell_size
        miny = maxy - self.height * self.cell_size
        return (minx, miny, maxx, maxy)


def gridspec_from_polygon(polygon: BaseGeometry, crs: CRS, cell_size: float) -> GridSpec:
    """Snap polygon bounds outward to a global cell grid anchored at the CRS origin (0, 0)."""
    minx, miny, maxx, maxy = polygon.bounds
    minx_s = math.floor(minx / cell_size) * cell_size
    miny_s = math.floor(miny / cell_size) * cell_size
    maxx_s = math.ceil(maxx / cell_size) * cell_size
    maxy_s = math.ceil(maxy / cell_size) * cell_size
    width = round((maxx_s - minx_s) / cell_size)
    height = round((maxy_s - miny_s) / cell_size)
    return GridSpec(
        crs=crs,
        transform=from_origin(minx_s, maxy_s, cell_size, cell_size),
        width=width,
        height=height,
        cell_size=cell_size,
    )
