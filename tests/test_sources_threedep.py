from pathlib import Path

import numpy as np
import rasterio
import responses
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.threedep import ThreeDepSource


def _make_tif_bytes(tmp_path: Path) -> bytes:
    arr = np.full((4, 4), 100.0, dtype="float32")
    p = tmp_path / "out.tif"
    with rasterio.open(
        p, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
        crs=CRS.from_epsg(5070), transform=from_origin(0, 120, 30, 30), nodata=-9999.0,
    ) as ds:
        ds.write(arr, 1)
    return p.read_bytes()


@responses.activate
def test_threedep_fetches_dem(tmp_path: Path):
    responses.add(
        responses.GET,
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
        body=_make_tif_bytes(tmp_path),
        content_type="image/tiff",
    )
    src = ThreeDepSource(cache_dir=tmp_path / "cache")
    out = src.fetch(LayerKey.DEM, bbox=(0.0, 0.0, 100.0, 100.0), dst_crs="EPSG:5070")
    assert out.exists()
    with rasterio.open(out) as ds:
        assert ds.read(1)[0, 0] == 100.0


@responses.activate
def test_threedep_caches(tmp_path: Path):
    responses.add(
        responses.GET,
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
        body=_make_tif_bytes(tmp_path),
        content_type="image/tiff",
    )
    src = ThreeDepSource(cache_dir=tmp_path / "cache")
    a = src.fetch(LayerKey.DEM, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    b = src.fetch(LayerKey.DEM, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    assert a == b
    assert len(responses.calls) == 1
