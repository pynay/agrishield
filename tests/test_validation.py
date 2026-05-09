from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKey, LayerKind
from wildfire_preproc.validation.checks import (
    ValidationError,  # noqa: F401 — exported for Task 17
    ValidationResult,
    validate_raster,
)
from wildfire_preproc.validation.report import format_report


def _grid() -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, 300, 30, 30),
        width=10,
        height=10,
        cell_size=30.0,
    )


def _write(path: Path, data: np.ndarray, grid: GridSpec, dtype: str, nodata):
    with rasterio.open(
        path, "w",
        driver="GTiff", height=grid.height, width=grid.width,
        count=1, dtype=dtype, crs=grid.crs, transform=grid.transform, nodata=nodata,
    ) as ds:
        ds.write(data.astype(dtype), 1)


def test_passing_continuous_raster(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    _write(p, np.full((10, 10), 100.0, dtype="float32"), grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert res.ok
    assert not res.errors


def test_failing_crs_mismatch(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    bad_grid = GridSpec(
        crs=CRS.from_epsg(4326),
        transform=grid.transform,
        width=grid.width, height=grid.height, cell_size=grid.cell_size,
    )
    _write(p, np.full((10, 10), 100.0, dtype="float32"), bad_grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert not res.ok
    assert any("crs" in e.lower() for e in res.errors)


def test_failing_dimensions(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    bad_grid = GridSpec(
        crs=grid.crs,
        transform=from_origin(0, 300, 30, 30),
        width=5, height=5, cell_size=30.0,
    )
    _write(p, np.full((5, 5), 100.0, dtype="float32"), bad_grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert not res.ok
    assert any(
        "dimensions" in e.lower() or "width" in e.lower() or "height" in e.lower()
        for e in res.errors
    )


def test_failing_all_nodata(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    _write(p, np.full((10, 10), -9999.0, dtype="float32"), grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert not res.ok
    assert any("nodata" in e.lower() for e in res.errors)


def test_failing_mask_with_invalid_values(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "m.tif"
    arr = np.zeros((10, 10), dtype="uint8")
    arr[0, 0] = 7
    _write(p, arr, grid, "uint8", None)
    res = validate_raster(p, grid, LayerKey.PROTECTED_MASK, LayerKind.MASK)
    assert not res.ok
    assert any("mask" in e.lower() or "values" in e.lower() for e in res.errors)


def test_failing_fbfm40_invalid_code(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "f.tif"
    arr = np.full((10, 10), 7, dtype="uint8")  # 7 is not a valid FBFM40 code
    _write(p, arr, grid, "uint8", 255)
    res = validate_raster(p, grid, LayerKey.FBFM40, LayerKind.CATEGORICAL)
    assert not res.ok
    assert any("fbfm40" in e.lower() or "code" in e.lower() for e in res.errors)


def test_format_report_passing():
    results = [
        ValidationResult(layer="dem", path=Path("/x/dem.tif")),
        ValidationResult(layer="fbfm40", path=Path("/x/fbfm40.tif")),
    ]
    report = format_report(results, manifest_ok=True)
    assert "PASS" in report
    assert "dem" in report
    assert "fbfm40" in report


def test_format_report_failing():
    bad = ValidationResult(layer="dem", path=Path("/x/dem.tif"))
    bad.errors.append("crs mismatch")
    report = format_report([bad], manifest_ok=True)
    assert "FAIL" in report
    assert "crs mismatch" in report


def test_format_report_manifest_fail_overrides_passing_results():
    results = [
        ValidationResult(layer="dem", path=Path("/x/dem.tif")),
        ValidationResult(layer="fbfm40", path=Path("/x/fbfm40.tif")),
    ]
    report = format_report(results, manifest_ok=False)
    # Manifest row should show FAIL even though both per-layer results are passing
    assert "manifest" in report
    # Overall must be FAIL
    assert "overall: FAIL" in report


def test_protected_mask_all_zero_fails(tmp_path: Path):
    """Regression: protected_mask must not silently pass when entirely zero."""
    grid = _grid()
    p = tmp_path / "protected_mask.tif"
    _write(p, np.zeros((10, 10), dtype="uint8"), grid, "uint8", None)
    res = validate_raster(p, grid, LayerKey.PROTECTED_MASK, LayerKind.MASK)
    assert not res.ok
    assert any("entirely zero" in e or "did not intersect" in e for e in res.errors)


def test_candidate_zone_all_zero_fails(tmp_path: Path):
    """Regression: candidate_zone must not silently pass when entirely zero."""
    grid = _grid()
    p = tmp_path / "candidate_zone.tif"
    _write(p, np.zeros((10, 10), dtype="uint8"), grid, "uint8", None)
    res = validate_raster(p, grid, LayerKey.CANDIDATE_ZONE, LayerKind.MASK)
    assert not res.ok
    assert any("entirely zero" in e or "did not intersect" in e for e in res.errors)


def test_non_burnable_mask_all_zero_passes(tmp_path: Path):
    """non_burnable_mask CAN legitimately be empty (pure forest AOI). Must not fail."""
    grid = _grid()
    p = tmp_path / "non_burnable_mask.tif"
    _write(p, np.zeros((10, 10), dtype="uint8"), grid, "uint8", None)
    res = validate_raster(p, grid, LayerKey.NON_BURNABLE_MASK, LayerKind.MASK)
    assert res.ok, res.errors


def test_protected_mask_non_zero_passes(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "protected_mask.tif"
    arr = np.zeros((10, 10), dtype="uint8")
    arr[3:7, 3:7] = 1
    _write(p, arr, grid, "uint8", None)
    res = validate_raster(p, grid, LayerKey.PROTECTED_MASK, LayerKind.MASK)
    assert res.ok, res.errors
