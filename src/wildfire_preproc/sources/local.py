"""Local-file RasterSource: returns a configured local path for a layer."""

from __future__ import annotations

from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox


class LocalRasterSource:
    """Configured with a fixed path per LayerKey. Ignores bbox/dst_crs at fetch time."""

    def __init__(self, paths: dict[LayerKey, Path]):
        self._paths = paths

    def provenance(self, layer: LayerKey) -> str:
        if layer not in self._paths:
            return f"local:unknown({layer.value})"
        return f"local:{self._paths[layer]}"

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer not in self._paths:
            raise KeyError(f"LocalRasterSource has no path configured for {layer}")
        p = self._paths[layer]
        if not p.exists():
            raise FileNotFoundError(p)
        return p
