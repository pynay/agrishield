"""LANDFIRE Product Service (LFPS) RasterSource — ArcGIS REST job workflow."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox
from wildfire_preproc.sources.cache import cache_key_path

LFPS_BASE = "https://lfps.usgs.gov/api/job"
SUBMIT_URL = f"{LFPS_BASE}/submitJob"
STATUS_URL = f"{LFPS_BASE}/status"

# LF2022 layer codes per LANDFIRE Product Service (verified against
# https://lfps.usgs.gov/api/products on 2026-05-08).
LFPS_LAYER_CODE: dict[LayerKey, str] = {
    LayerKey.FBFM40: "LF2022_FBFM40",
    LayerKey.CC: "LF2022_CC",
    LayerKey.CH: "LF2022_CH",
    LayerKey.CBH: "LF2022_CBH",
    LayerKey.CBD: "LF2022_CBD",
}


class LfpsHttpError(RuntimeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transient failures only: connection errors, timeouts, 5xx, 429."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status >= 500 or status == 429
    return False


_retry = retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, max=60),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)


class LfpsSource:
    def __init__(
        self,
        cache_dir: Path,
        landfire_version: str = "LF2022",
        poll_interval_s: float = 5.0,
        poll_timeout_s: float = 600.0,
    ):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._landfire_version = landfire_version
        self._poll_interval = poll_interval_s
        self._poll_timeout = poll_timeout_s

    def provenance(self, layer: LayerKey) -> str:
        if layer not in LFPS_LAYER_CODE:
            return f"lfps:unknown({layer.value})"
        return f"lfps:{LFPS_LAYER_CODE[layer]}"

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer not in LFPS_LAYER_CODE:
            raise KeyError(f"LFPS does not provide layer {layer}")
        cache_path = cache_key_path(self._cache_dir, layer, bbox, dst_crs)
        if cache_path.exists():
            return cache_path

        layer_code = LFPS_LAYER_CODE[layer]
        job_id = self._submit(layer_code, bbox, dst_crs)
        output_url = self._await_complete(job_id)
        zip_bytes = self._download(output_url)
        self._extract_to(zip_bytes, cache_path)
        return cache_path

    @_retry
    def _submit(self, layer_code: str, bbox: BBox, dst_crs: str) -> str:
        payload = {
            "Layer_List": layer_code,
            "Area_Of_Interest": ",".join(f"{x:.6f}" for x in bbox),
            "Output_Projection": dst_crs.replace("EPSG:", ""),
        }
        r = requests.post(SUBMIT_URL, json=payload, timeout=30)
        r.raise_for_status()
        return str(r.json()["jobId"])

    def _await_complete(self, job_id: str) -> str:
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            status = self._poll(job_id)
            state = status.get("Status", "").lower()
            if state == "succeeded":
                output = status.get("OutputFile")
                if not output:
                    raise LfpsHttpError(f"LFPS Succeeded but no OutputFile: {status}")
                return str(output)
            if state == "failed":
                raise LfpsHttpError(f"LFPS job failed: {status}")
            time.sleep(self._poll_interval)
        raise LfpsHttpError(f"LFPS job {job_id} timed out after {self._poll_timeout}s")

    @_retry
    def _poll(self, job_id: str) -> dict[str, Any]:
        r = requests.get(STATUS_URL, params={"jobId": job_id}, timeout=30)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    @_retry
    def _download(self, url: str) -> bytes:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        return r.content

    def _extract_to(self, zip_bytes: bytes, dst: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            tif_names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
            if not tif_names:
                raise LfpsHttpError("LFPS zip contained no .tif")
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            with zf.open(tif_names[0]) as src, open(tmp, "wb") as out:
                out.write(src.read())
            tmp.replace(dst)
