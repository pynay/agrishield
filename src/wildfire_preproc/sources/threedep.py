"""USGS 3DEP DEM fetcher via the National Map exportImage endpoint."""

from __future__ import annotations

from pathlib import Path

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox
from wildfire_preproc.sources.cache import cache_key_path

THREEDEP_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
)


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transient failures only: connection errors, timeouts, 5xx, 429."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status >= 500 or status == 429
    return False


class ThreeDepSource:
    def __init__(self, cache_dir: Path, default_pixel_size_m: float = 10.0):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._pixel_size = default_pixel_size_m

    def provenance(self, layer: LayerKey) -> str:
        return f"3dep:{int(self._pixel_size)}m"

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer != LayerKey.DEM:
            raise KeyError(f"3DEP only provides DEM, got {layer}")
        cache_path = cache_key_path(self._cache_dir, layer, bbox, dst_crs)
        if cache_path.exists():
            return cache_path
        content = self._download(bbox, dst_crs)
        # Atomic write
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(cache_path)
        return cache_path

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1, max=60),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    def _download(self, bbox: BBox, dst_crs: str) -> bytes:
        minx, miny, maxx, maxy = bbox
        width = max(1, int((maxx - minx) / self._pixel_size))
        height = max(1, int((maxy - miny) / self._pixel_size))
        params = {
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "bboxSR": dst_crs.replace("EPSG:", ""),
            "imageSR": dst_crs.replace("EPSG:", ""),
            "size": f"{width},{height}",
            "format": "tiff",
            "pixelType": "F32",
            "noData": "-9999",
            "f": "image",
        }
        r = requests.get(THREEDEP_URL, params=params, timeout=120)
        r.raise_for_status()
        return r.content
