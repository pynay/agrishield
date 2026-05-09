from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.cache import cache_key_path


def test_cache_key_is_deterministic(tmp_path: Path):
    a = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    b = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    assert a == b


def test_cache_key_differs_by_bbox(tmp_path: Path):
    a = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    b = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 5), crs="EPSG:5070")
    assert a != b


def test_cache_key_differs_by_layer(tmp_path: Path):
    a = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    b = cache_key_path(tmp_path, layer=LayerKey.DEM, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    assert a != b
