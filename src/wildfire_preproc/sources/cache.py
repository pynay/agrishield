"""Disk-backed cache key for fetched rasters."""

from __future__ import annotations

import hashlib
from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox


def cache_key_path(
    cache_dir: Path,
    layer: LayerKey,
    bbox: BBox,
    crs: str,
    version: str | None = None,
) -> Path:
    """Stable cache path for a fetched raster.

    `version` distinguishes otherwise-identical fetches that come from
    different LANDFIRE releases (e.g. LF2022 vs LF2023). Pass `None` for
    sources that don't have a version (3DEP, local files).
    """
    h = hashlib.sha256()
    h.update(layer.value.encode())
    h.update(b"|")
    h.update(",".join(f"{x:.6f}" for x in bbox).encode())
    h.update(b"|")
    h.update(crs.encode())
    if version is not None:
        h.update(b"|")
        h.update(version.encode())
    return cache_dir / f"{layer.value}_{h.hexdigest()[:16]}.tif"
