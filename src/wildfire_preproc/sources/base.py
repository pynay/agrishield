"""Abstract RasterSource protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from wildfire_preproc.config import LayerKey

BBox = tuple[float, float, float, float]


class RasterSource(Protocol):
    """A source that can produce a raster covering `bbox` for a given layer.

    Returns a path to a GeoTIFF on disk. The CRS/resolution of that file is whatever
    the source happens to produce — Stage 3 handles alignment.
    """

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path: ...

    def provenance(self, layer: LayerKey) -> str:
        """Human-readable label describing where this layer came from.
        Used by the pipeline to populate metadata.json."""
        ...
