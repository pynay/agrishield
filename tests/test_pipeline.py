import json as _json
from pathlib import Path

import numpy as np
import rasterio
from click.testing import CliRunner
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon, mapping

from wildfire_preproc.cli import main
from wildfire_preproc.config import JobConfig, LayerKey
from wildfire_preproc.pipeline import run_pipeline
from wildfire_preproc.sources.local import LocalRasterSource


def _write_synthetic_layer(path: Path, shape, value, dtype, nodata, transform, crs):
    arr = np.full(shape, value, dtype=dtype)
    with rasterio.open(
        path, "w", driver="GTiff", height=shape[0], width=shape[1],
        count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
    ) as ds:
        ds.write(arr, 1)


def _build_local_source(tmp_path: Path) -> LocalRasterSource:
    """Create synthetic rasters covering a 30km x 30km area in EPSG:5070."""
    crs = CRS.from_epsg(5070)
    cell = 30.0
    big_h = big_w = 1000  # 30km coverage
    transform = from_origin(-100_000.0, 1_000_000.0, cell, cell)
    fbfm = tmp_path / "fbfm.tif"
    dem = tmp_path / "dem.tif"
    cc = tmp_path / "cc.tif"
    ch = tmp_path / "ch.tif"
    cbh = tmp_path / "cbh.tif"
    cbd = tmp_path / "cbd.tif"
    _write_synthetic_layer(fbfm, (big_h, big_w), 101, "uint8", 255, transform, crs)
    _write_synthetic_layer(dem,  (big_h, big_w), 100.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cc,   (big_h, big_w), 50.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(ch,   (big_h, big_w), 10.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cbh,  (big_h, big_w), 2.0,  "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cbd,  (big_h, big_w), 0.1,  "float32", -9999.0, transform, crs)
    return LocalRasterSource({
        LayerKey.FBFM40: fbfm, LayerKey.DEM: dem, LayerKey.CC: cc,
        LayerKey.CH: ch, LayerKey.CBH: cbh, LayerKey.CBD: cbd,
    })


def test_pipeline_produces_full_manifest(tmp_path: Path):
    # Polygon centered well inside the synthetic raster footprint (-100km..-70km, 970km..1000km).
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")

    inputs = out_dir / "inputs"
    expected = [
        "fbfm40.tif", "dem.tif", "slp.tif", "asp.tif",
        "cc.tif", "ch.tif", "cbh.tif", "cbd.tif",
        "protected_mask.tif", "candidate_zone.tif", "non_burnable_mask.tif",
        "ignition_points.geojson", "metadata.json", "validation_report.txt",
    ]
    for name in expected:
        assert (inputs / name).exists(), f"missing: {name}"


def test_pipeline_outputs_share_grid(tmp_path: Path):
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")

    inputs = out_dir / "inputs"
    tifs = list(inputs.glob("*.tif"))
    assert len(tifs) >= 8
    ref = None
    for tif in tifs:
        with rasterio.open(tif) as ds:
            sig = (ds.crs, tuple(ds.transform)[:6], ds.width, ds.height)
        if ref is None:
            ref = sig
        else:
            assert sig == ref, f"{tif.name} has {sig}, expected {ref}"


def test_pipeline_intermediate_deleted_by_default(tmp_path: Path):
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")
    assert not (out_dir / "_intermediate").exists()


def test_pipeline_raises_runtime_error_on_validation_failure(tmp_path: Path):
    """If validation fails, RuntimeError is raised AND validation_report.txt is still written."""
    import pytest

    # Use the standard polygon that fits inside the synthetic raster footprint.
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"

    # Build a source that produces FBFM40 with INVALID codes (50 is not a valid FBFM40 code).
    crs = CRS.from_epsg(5070)
    cell = 30.0
    big_h = big_w = 1000
    transform = from_origin(-100_000.0, 1_000_000.0, cell, cell)
    fbfm = tmp_path / "fbfm.tif"
    dem = tmp_path / "dem.tif"
    cc = tmp_path / "cc.tif"
    ch = tmp_path / "ch.tif"
    cbh = tmp_path / "cbh.tif"
    cbd = tmp_path / "cbd.tif"
    _write_synthetic_layer(fbfm, (big_h, big_w), 50, "uint8", 255, transform, crs)  # invalid code
    _write_synthetic_layer(dem,  (big_h, big_w), 100.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cc,   (big_h, big_w), 50.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(ch,   (big_h, big_w), 10.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cbh,  (big_h, big_w), 2.0,  "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cbd,  (big_h, big_w), 0.1,  "float32", -9999.0, transform, crs)
    source = LocalRasterSource({
        LayerKey.FBFM40: fbfm, LayerKey.DEM: dem, LayerKey.CC: cc,
        LayerKey.CH: ch, LayerKey.CBH: cbh, LayerKey.CBD: cbd,
    })

    with pytest.raises(RuntimeError, match="Validation failed"):
        run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")

    # Validation report still written even on failure.
    report = out_dir / "inputs" / "validation_report.txt"
    assert report.exists()
    assert "FAIL" in report.read_text()


def test_pipeline_keep_intermediate_preserves_directory(tmp_path: Path):
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(
        cfg=cfg, out_dir=out_dir, source=source,
        protected_polygon_crs="EPSG:5070", keep_intermediate=True,
    )
    intermediate = out_dir / "_intermediate"
    assert intermediate.exists()
    assert (intermediate / "simulation_domain.geojson").exists()


def test_cli_run_executes_pipeline(tmp_path: Path):
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    payload = {
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    }
    job_json = tmp_path / "job.json"
    job_json.write_text(_json.dumps(payload))
    out_dir = tmp_path / "out"
    source = _build_local_source(tmp_path)

    # Inject the local source via a small helper hook the CLI exposes for testing.
    from wildfire_preproc import cli as cli_mod
    cli_mod._TEST_SOURCE_OVERRIDE = source  # type: ignore[attr-defined]
    try:
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(job_json), "--out", str(out_dir), "--protected-polygon-crs", "EPSG:5070"],
            catch_exceptions=False,
        )
    finally:
        cli_mod._TEST_SOURCE_OVERRIDE = None  # type: ignore[attr-defined]
    assert result.exit_code == 0, result.output
    assert (out_dir / "inputs" / "metadata.json").exists()
