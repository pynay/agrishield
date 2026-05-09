import json
from pathlib import Path

import numpy as np
import rasterio
from click.testing import CliRunner
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.cli import main
from wildfire_preproc.optimization import FirebreakOptimizationConfig, optimize_firebreaks


def _write_tif(path: Path, arr: np.ndarray, transform, crs, nodata=None) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=arr.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as ds:
        ds.write(arr, 1)


def _build_inputs(tmp_path: Path) -> Path:
    inputs = tmp_path / "job" / "inputs"
    inputs.mkdir(parents=True)
    crs = CRS.from_epsg(5070)
    transform = from_origin(0.0, 6000.0, 30.0, 30.0)
    shape = (200, 200)

    fbfm40 = np.full(shape, 101, dtype="uint8")
    protected = np.zeros(shape, dtype="uint8")
    protected[85:115, 85:115] = 1
    candidate = np.zeros(shape, dtype="uint8")
    candidate[50:150, 50:150] = 1
    candidate[80:120, 80:120] = 0
    non_burnable = np.zeros(shape, dtype="uint8")
    non_burnable[40:160, 130:135] = 1

    _write_tif(inputs / "fbfm40.tif", fbfm40, transform, crs, nodata=255)
    _write_tif(inputs / "protected_mask.tif", protected, transform, crs)
    _write_tif(inputs / "candidate_zone.tif", candidate, transform, crs)
    _write_tif(inputs / "non_burnable_mask.tif", non_burnable, transform, crs)
    return inputs


def _build_baseline(tmp_path: Path) -> Path:
    baseline = tmp_path / "baseline"
    for idx, direction, bearing, burned in [
        (1, "north", 0, 18_000),
        (2, "northeast", 45, 7_500),
        (3, "east", 90, 0),
    ]:
        scenario = baseline / f"scenario_{idx:02d}"
        scenario.mkdir(parents=True)
        (scenario / "summary.json").write_text(
            json.dumps(
                {
                    "scenario_id": idx,
                    "ignition_direction": direction,
                    "ignition_bearing_deg": bearing,
                    "patch_burned": burned > 0,
                    "burned_area_inside_patch_m2": burned,
                    "first_arrival_to_patch_minutes": 42,
                    "max_flame_length_near_patch_m": 3.4,
                }
            )
        )
    return baseline


def test_optimize_firebreaks_writes_ranked_layout_artifacts(tmp_path: Path):
    inputs = _build_inputs(tmp_path)
    baseline = _build_baseline(tmp_path)
    out = tmp_path / "optimization"

    result = optimize_firebreaks(
        inputs,
        baseline_dir=baseline,
        out_dir=out,
        config=FirebreakOptimizationConfig(max_layouts=3, offsets_m=(250.0, 500.0)),
    )

    assert result.recommended_layout_id == result.layouts[0].layout_id
    assert result.baseline_result.scenarios_failed == 2
    assert result.baseline_result.burned_area_inside_patch_m2 == 25_500
    assert (out / "firebreak_optimization.json").exists()

    payload = json.loads((out / "firebreak_optimization.json").read_text())
    assert payload["recommended_layout_id"] == result.recommended_layout_id
    assert len(payload["ranked_layouts"]) == 3
    assert payload["firebreak_segments"]
    lon, lat = payload["firebreak_segments"][0]["geometry"][0]
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90

    layout_dir = out / "layouts" / result.recommended_layout_id
    assert (layout_dir / "firebreak_mask.tif").exists()
    assert (layout_dir / "fbfm40.tif").exists()
    assert (layout_dir / "segments.geojson").exists()

    with rasterio.open(layout_dir / "firebreak_mask.tif") as mask_ds:
        mask = mask_ds.read(1)
        assert mask.sum() > 0
    with rasterio.open(layout_dir / "fbfm40.tif") as fbfm_ds:
        modified = fbfm_ds.read(1)
        assert 99 in np.unique(modified)


def test_optimize_firebreaks_uses_job_dir_inputs_convention(tmp_path: Path):
    inputs = _build_inputs(tmp_path)
    job_dir = inputs.parent

    result = optimize_firebreaks(
        job_dir,
        config=FirebreakOptimizationConfig(max_layouts=1, offsets_m=(250.0,)),
    )

    assert result.output_path == job_dir / "firebreak_optimization" / "firebreak_optimization.json"
    assert result.output_path.exists()


def test_cli_optimize_firebreaks(tmp_path: Path):
    inputs = _build_inputs(tmp_path)
    job_dir = inputs.parent
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["optimize-firebreaks", str(job_dir), "--max-layouts", "1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Recommended layout:" in result.output
    assert (job_dir / "firebreak_optimization" / "firebreak_optimization.json").exists()


def test_optimize_firebreaks_with_runner_populates_optimized_result(tmp_path: Path):
    """When a runner is supplied, the recommended layout is rerun and
    `optimized_result` is populated from the per-scenario summary.json files.
    """
    from shapely.geometry import Polygon, mapping

    from wildfire_preproc.config import JobConfig
    from wildfire_preproc.elmfire import ElmfireRunResult, ElmfireRunSpec

    inputs = _build_inputs(tmp_path)
    job_dir = inputs.parent

    # Stage the 8-bearing ignition_points GeoJSON the runner expects to find.
    crs = "EPSG:5070"
    polygon_proj = Polygon(
        [(2400, 2400), (2400, 3600), (3600, 3600), (3600, 2400), (2400, 2400)]
    )
    cfg = JobConfig.model_validate(
        {
            "protected_polygon": mapping(polygon_proj),
            "simulation_radius_m": 800.0,
            "ignition_distance_m": 500.0,
            "cell_size_m": 30.0,
            "crs": crs,
        }
    )
    # Build ignition_points.geojson from the canonical 8 compass bearings.
    from wildfire_preproc.utils.geometry import point_at_bearing

    centroid = polygon_proj.centroid
    pts_payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs}},
        "features": [],
    }
    for bearing in (0, 45, 90, 135, 180, 225, 270, 315):
        candidate = point_at_bearing(centroid, bearing_deg=bearing, distance=400.0)
        pts_payload["features"].append(
            {
                "type": "Feature",
                "properties": {"bearing_deg": bearing},
                "geometry": {
                    "type": "Point",
                    "coordinates": [candidate.x, candidate.y],
                },
            }
        )
    (inputs / "ignition_points.geojson").write_text(json.dumps(pts_payload))

    # Stage the canopy/terrain rasters the elmfire runner expects.
    crs_obj = CRS.from_epsg(5070)
    transform = from_origin(0.0, 6000.0, 30.0, 30.0)
    shape = (200, 200)
    for name, fill, dtype, nodata in [
        ("dem", 100.0, "float32", -9999.0),
        ("slp", 0.0, "float32", -9999.0),
        ("asp", -1.0, "float32", -9999.0),
        ("cc", 50.0, "float32", -9999.0),
        ("ch", 10.0, "float32", -9999.0),
        ("cbh", 2.0, "float32", -9999.0),
        ("cbd", 0.1, "float32", -9999.0),
    ]:
        _write_tif(
            inputs / f"{name}.tif",
            np.full(shape, fill, dtype=dtype),
            transform,
            crs_obj,
            nodata=nodata,
        )

    # Fake runner: write fake ELMFIRE-style time_of_arrival.tif files so the
    # production summary writer (`_write_scenario_summary`) computes real
    # patch_burned / burned_area metrics from those rasters.
    class FakeRunner:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, spec: ElmfireRunSpec) -> ElmfireRunResult:
            self.calls += 1
            outputs_dir = spec.output_dir / "outputs"
            outputs_dir.mkdir(parents=True, exist_ok=True)
            arrival = np.zeros(shape, dtype="float32")
            # Half the scenarios "burn" the patch (any cell in protected_mask
            # gets a positive arrival time -> _write_scenario_summary marks
            # patch_burned=True with burned_area > 0).
            if spec.ignition_bearing_deg in (0.0, 45.0, 90.0, 135.0):
                arrival[85:115, 85:115] = 120.0
            with rasterio.open(
                outputs_dir / "time_of_arrival.tif",
                "w",
                driver="GTiff",
                height=shape[0],
                width=shape[1],
                count=1,
                dtype="float32",
                crs=crs_obj,
                transform=transform,
                nodata=0.0,
            ) as ds:
                ds.write(arrival, 1)
            return ElmfireRunResult(
                spec=spec,
                returncode=0,
                stdout="",
                stderr="",
                elapsed_s=0.0,
                output_files=[outputs_dir / "time_of_arrival.tif"],
            )

    fake = FakeRunner()
    result = optimize_firebreaks(
        job_dir=job_dir,
        config=FirebreakOptimizationConfig(max_layouts=1, offsets_m=(250.0,)),
        runner=fake,
        job_config=cfg,
        protected_polygon_crs=crs,
    )
    assert fake.calls == 8
    assert result.optimized_result is not None
    assert result.optimized_result.scenarios_run == 8
    # Four "burned" scenarios = four arrival rasters with values inside protected_mask.
    assert result.optimized_result.scenarios_failed == 4
    assert result.optimized_result.burned_area_inside_patch_m2 > 0.0
    assert result.optimized_result.layout_id == result.recommended_layout_id

    # The serialized firebreak_optimization.json must contain the populated optimized_result.
    payload = json.loads(result.output_path.read_text())
    assert payload["optimized_result"] is not None
    assert payload["optimized_result"]["scenarios_failed"] == 4
    assert payload["optimized_result"]["scenarios_run"] == 8
    # Without a runner the same call must produce optimized_result=None.
    no_runner = optimize_firebreaks(
        job_dir=job_dir,
        config=FirebreakOptimizationConfig(max_layouts=1, offsets_m=(250.0,)),
    )
    assert no_runner.optimized_result is None
