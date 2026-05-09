"""Default source registry: LFPS for fuels/canopy, 3DEP for DEM."""

from __future__ import annotations

from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox, RasterSource
from wildfire_preproc.sources.lfps import LfpsSource
from wildfire_preproc.sources.threedep import ThreeDepSource


class DefaultSourceRegistry:
    def __init__(self, cache_dir: Path, landfire_version: str = "LF2022"):
        self._lfps = LfpsSource(cache_dir=cache_dir / "lfps", landfire_version=landfire_version)
        self._threedep = ThreeDepSource(cache_dir=cache_dir / "threedep")

    def for_layer(self, layer: LayerKey) -> RasterSource:
        if layer == LayerKey.DEM:
            return self._threedep
        return self._lfps

    def provenance(self, layer: LayerKey) -> str:
        return self.for_layer(layer).provenance(layer)

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        return self.for_layer(layer).fetch(layer, bbox, dst_crs)
