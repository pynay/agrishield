import io
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import responses
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.lfps import LFPS_LAYER_CODE, LfpsSource


def _zip_with_tif(tmp_path: Path) -> bytes:
    arr = np.full((4, 4), 101, dtype="uint8")
    tif = tmp_path / "x.tif"
    with rasterio.open(
        tif, "w",
        driver="GTiff", height=4, width=4, count=1, dtype="uint8",
        crs=CRS.from_epsg(5070), transform=from_origin(0, 120, 30, 30), nodata=255,
    ) as ds:
        ds.write(arr, 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(tif, arcname="x.tif")
    return buf.getvalue()


@responses.activate
def test_lfps_fetches_layer_via_arcgis_job_workflow(tmp_path: Path):
    # Job submission
    responses.add(responses.GET, "https://lfps.usgs.gov/api/job/submit", json={"jobId": "abc123"})
    # Status poll
    responses.add(
        responses.GET,
        "https://lfps.usgs.gov/api/job/status",
        json={"Status": "Succeeded", "OutputFile": "https://example.test/result.zip"},
    )
    # Output zip
    responses.add(
        responses.GET,
        "https://example.test/result.zip",
        body=_zip_with_tif(tmp_path),
        content_type="application/zip",
    )

    src = LfpsSource(
        cache_dir=tmp_path / "cache",
        landfire_version="LF2022",
        email="test@example.com",
        poll_interval_s=0.0,
    )
    out = src.fetch(LayerKey.FBFM40, bbox=(0.0, 0.0, 100.0, 100.0), dst_crs="EPSG:5070")
    assert out.exists()
    with rasterio.open(out) as ds:
        assert ds.read(1)[0, 0] == 101


@responses.activate
def test_lfps_uses_cache_on_second_call(tmp_path: Path):
    responses.add(responses.GET, "https://lfps.usgs.gov/api/job/submit", json={"jobId": "abc123"})
    responses.add(
        responses.GET, "https://lfps.usgs.gov/api/job/status",
        json={"Status": "Succeeded", "OutputFile": "https://example.test/result.zip"},
    )
    responses.add(
        responses.GET, "https://example.test/result.zip",
        body=_zip_with_tif(tmp_path),
        content_type="application/zip",
    )

    src = LfpsSource(
        cache_dir=tmp_path / "cache",
        landfire_version="LF2022",
        email="test@example.com",
        poll_interval_s=0.0,
    )
    p1 = src.fetch(LayerKey.FBFM40, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    p2 = src.fetch(LayerKey.FBFM40, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    assert p1 == p2
    # Only the first call hits LFPS (1 POST, 1 status, 1 download = 3 calls); second is cache hit.
    assert len(responses.calls) == 3


def test_lfps_layer_codes_cover_all_fetched_layers():
    for layer in [LayerKey.FBFM40, LayerKey.CC, LayerKey.CH, LayerKey.CBH, LayerKey.CBD]:
        assert layer in LFPS_LAYER_CODE
