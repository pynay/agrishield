"""LANDFIRE Product Service (LFPS) RasterSource."""

from __future__ import annotations

import io
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from rasterio.warp import transform_bounds
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox
from wildfire_preproc.sources.cache import cache_key_path

LFPS_BASE = "https://lfps.usgs.gov/api/job"
SUBMIT_URL = f"{LFPS_BASE}/submit"
STATUS_URL = f"{LFPS_BASE}/status"

_LFPS_LAYER_SUFFIX: dict[LayerKey, str] = {
    LayerKey.FBFM40: "FBFM40",
    LayerKey.CC: "CC",
    LayerKey.CH: "CH",
    LayerKey.CBH: "CBH",
    LayerKey.CBD: "CBD",
}

# Backward-compatible constant for older tests/callers.
LFPS_LAYER_CODE: dict[LayerKey, str] = {
    layer: f"LF2022_{suffix}" for layer, suffix in _LFPS_LAYER_SUFFIX.items()
}

_ZIP_URL_RE = re.compile(r"https://[^\s\"'<>]+\.zip", re.IGNORECASE)


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
        email: str | None = None,
        poll_interval_s: float = 5.0,
        poll_timeout_s: float = 600.0,
    ):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._landfire_version = landfire_version
        self._email = email or os.environ.get("LANDFIRE_EMAIL")
        self._poll_interval = poll_interval_s
        self._poll_timeout = poll_timeout_s

    def provenance(self, layer: LayerKey) -> str:
        if layer not in _LFPS_LAYER_SUFFIX:
            return f"lfps:unknown({layer.value})"
        return f"lfps:{self._layer_code(layer)}"

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer not in _LFPS_LAYER_SUFFIX:
            raise KeyError(f"LFPS does not provide layer {layer}")
        if not self._email:
            raise LfpsHttpError(
                "LFPS requires an email address. Set LANDFIRE_EMAIL or pass "
                "email=... when constructing LfpsSource."
            )
        cache_path = cache_key_path(
            self._cache_dir, layer, bbox, dst_crs, version=self._landfire_version
        )
        if cache_path.exists():
            return cache_path

        layer_code = self._layer_code(layer)
        job_id = self._submit(layer_code, bbox, dst_crs)
        output_url = self._await_complete(job_id)
        zip_bytes = self._download(output_url)
        self._extract_to(zip_bytes, cache_path)
        return cache_path

    def _layer_code(self, layer: LayerKey) -> str:
        return f"{self._landfire_version}_{_LFPS_LAYER_SUFFIX[layer]}"

    @_retry
    def _submit(self, layer_code: str, bbox: BBox, dst_crs: str) -> str:
        west, south, east, north = transform_bounds(dst_crs, "EPSG:4326", *bbox)
        params = {
            "Email": self._email,
            "Layer_List": layer_code,
            "Include_Layer_List_XML_File": "false",
            "Area_of_Interest": f"{west:.6f} {south:.6f} {east:.6f} {north:.6f}",
            "Output_Projection": dst_crs.replace("EPSG:", ""),
        }
        r = requests.get(SUBMIT_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("jobId"):
            raise LfpsHttpError(f"LFPS submit did not return jobId: {data}")
        return str(data["jobId"])

    def _await_complete(self, job_id: str) -> str:
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            status = self._poll(job_id)
            state = str(status.get("status") or status.get("Status") or "").lower()
            if state == "succeeded":
                output = (
                    status.get("OutputFile")
                    or status.get("outputFile")
                    or status.get("downloadUrl")
                    or status.get("downloadURL")
                    or _find_zip_url(status)
                )
                if not output:
                    raise LfpsHttpError(f"LFPS Succeeded but no OutputFile: {status}")
                return str(output)
            if state in {"failed", "canceled", "cancelled"}:
                raise LfpsHttpError(f"LFPS job failed: {status}")
            time.sleep(self._poll_interval)
        raise LfpsHttpError(f"LFPS job {job_id} timed out after {self._poll_timeout}s")

    @_retry
    def _poll(self, job_id: str) -> dict[str, Any]:
        r = requests.get(STATUS_URL, params={"JobId": job_id}, timeout=30)
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


def _find_zip_url(value: Any) -> str | None:
    if isinstance(value, str):
        match = _ZIP_URL_RE.search(value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for nested in value.values():
            found = _find_zip_url(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_zip_url(nested)
            if found:
                return found
    return None
