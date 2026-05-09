"""Disk-backed cache key for fetched rasters."""

from __future__ import annotations

import hashlib
from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox


def cache_key_path(cache_dir: Path, layer: LayerKey, bbox: BBox, crs: str) -> Path:
    h = hashlib.sha256()
    h.update(layer.value.encode())
    h.update(b"|")
    h.update(",".join(f"{x:.6f}" for x in bbox).encode())
    h.update(b"|")
    h.update(crs.encode())
    return cache_dir / f"{layer.value}_{h.hexdigest()[:16]}.tif"
