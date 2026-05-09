"""End-to-end entry point for location-based no-firebreak wildfire simulation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import rasterio.crs
from pyproj import CRS, Transformer
from rasterio.transform import Affine
from shapely.geometry import MultiPolygon, Polygon, mapping, shape

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import JobConfig
from wildfire_preproc.domain.simulation_domain import build_domain
from wildfire_preproc.elmfire import (
    ElmfireEnsembleResult,
    resolve_elmfire_runner,
    run_no_firebreak_elmfire_ensemble,
)
from wildfire_preproc.masks.candidate import build_candidate_zone_mask
from wildfire_preproc.masks.non_burnable import build_non_burnable_mask
from wildfire_preproc.masks.protected import build_protected_mask
from wildfire_preproc.pipeline import run_pipeline
from wildfire_preproc.sources.base import RasterSource
from wildfire_preproc.sources.registry import DefaultSourceRegistry
from wildfire_preproc.utils.env import load_env_file as _shared_load_env_file

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ELMFIRE = PROJECT_ROOT / "elmfire" / "build" / "linux" / "bin" / "elmfire_2025.0212"


SAN_DIEGO_EXAMPLE = {
    "name": "san-diego-ramona-demo-farm",
    "center_lon": -116.945,
    "center_lat": 33.035,
    "width_m": 800.0,
    "height_m": 800.0,
}


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Backward-compat wrapper around `wildfire_preproc.utils.env.load_env_file`.

    Kept so `from main import load_env_file` continues to work for callers that
    relied on it before the helper moved to the shared utils module.
    """
    _shared_load_env_file(path)


def rectangle_location(
    center_lon: float,
    center_lat: float,
    width_m: float,
    height_m: float,
) -> dict[str, Any]:
    """Build a WGS84 GeoJSON polygon for a rectangular protected location."""
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_lat} +lon_0={center_lon} +datum=WGS84 +units=m"
    )
    to_wgs84 = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
    half_w = width_m / 2.0
    half_h = height_m / 2.0
    local_corners = [
        (-half_w, -half_h),
        (half_w, -half_h),
        (half_w, half_h),
        (-half_w, half_h),
        (-half_w, -half_h),
    ]
    lonlat_corners = [to_wgs84.transform(x, y) for x, y in local_corners]
    return mapping(Polygon(lonlat_corners))


def run_location_fire_simulations(
    protected_polygon: dict[str, Any],
    out_dir: Path,
    protected_polygon_crs: str = "EPSG:4326",
    target_crs: str = "EPSG:5070",
    simulation_radius_m: float = 5000.0,
    ignition_distance_m: float = 4500.0,
    safety_buffer_m: float = 100.0,
    cell_size_m: float = 30.0,
    landfire_version: str | None = None,
    wind_speed_mps: float = 6.7,
    simulation_tstop_s: float = 21_600.0,
    cache_dir: Path | None = None,
    source: RasterSource | None = None,
    elmfire_executable: Path = DEFAULT_ELMFIRE,
    elmfire_runner: str = "auto",
    wsl_distro: str = "Ubuntu",
    keep_intermediate: bool = True,
    preprocessed_dir: Path | None = None,
) -> ElmfireEnsembleResult:
    """Preprocess a protected location and run 8 no-firebreak ELMFIRE simulations.

    `protected_polygon` is the protected farm/field/asset boundary as a GeoJSON
    Polygon or MultiPolygon. The returned result contains one run for each
    compass ignition point around the protected polygon.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = JobConfig.model_validate(
        {
            "protected_polygon": protected_polygon,
            "simulation_radius_m": simulation_radius_m,
            "ignition_distance_m": ignition_distance_m,
            "cell_size_m": cell_size_m,
            "crs": target_crs,
            "safety_buffer_m": safety_buffer_m,
            "landfire_version": landfire_version or os.environ.get("LANDFIRE_VERSION", "LF2023"),
            "cache_dir": str(cache_dir) if cache_dir is not None else None,
        }
    )

    job_json = out_dir / "job.json"
    job_json.write_text(json.dumps(cfg.model_dump(), indent=2))

    if source is None:
        resolved_cache = cache_dir or (Path.home() / ".cache" / "wildfire-preproc")
        source = DefaultSourceRegistry(
            cache_dir=resolved_cache,
            landfire_version=cfg.landfire_version,
        )

    if preprocessed_dir is None:
        preprocessed_dir = out_dir / "preprocessed"
        run_pipeline(
            cfg=cfg,
            out_dir=preprocessed_dir,
            source=source,
            protected_polygon_crs=protected_polygon_crs,
            keep_intermediate=keep_intermediate,
        )
    else:
        preprocessed_dir = Path(preprocessed_dir)
        try:
            _refresh_polygon_dependent_inputs(
                cfg=cfg,
                preprocessed_dir=preprocessed_dir,
                protected_polygon_crs=protected_polygon_crs,
            )
        except PreprocessedReuseError as exc:
            # The cached grid does not cover the user's simulation domain.
            # Fall through to a fresh full preprocessing pass instead of
            # silently reusing stale rasters that don't span the AOI.
            print(
                f"[main] preprocessed-dir reuse rejected ({exc}); "
                "running full preprocessing instead."
            )
            preprocessed_dir = out_dir / "preprocessed"
            run_pipeline(
                cfg=cfg,
                out_dir=preprocessed_dir,
                source=source,
                protected_polygon_crs=protected_polygon_crs,
                keep_intermediate=keep_intermediate,
            )

    runner = resolve_elmfire_runner(
        choice=elmfire_runner,  # type: ignore[arg-type]
        executable=elmfire_executable,
        wsl_distro=wsl_distro,
    )
    result = run_no_firebreak_elmfire_ensemble(
        cfg=cfg,
        job_dir=preprocessed_dir,
        runner=runner,
        protected_polygon_crs=protected_polygon_crs,
        wind_speed_mps=wind_speed_mps,
        simulation_tstop_s=simulation_tstop_s,
    )
    _write_summary(out_dir / "simulation_summary.json", result)
    return result


class PreprocessedReuseError(RuntimeError):
    """Raised when a preprocessed directory does not cover the user's simulation domain."""


# Backward-compat alias for callers that imported the old name.
PreprocessedReuseMismatch = PreprocessedReuseError


def _refresh_polygon_dependent_inputs(
    cfg: JobConfig,
    preprocessed_dir: Path,
    protected_polygon_crs: str,
) -> None:
    """Reuse fetched vegetation rasters while updating user-polygon domain inputs.

    Raises `PreprocessedReuseMismatch` if the cached grid does not cover the
    user's simulation domain, the user's CRS, or the user's cell size — so
    that callers can fall back to a fresh full pipeline instead of producing
    silently-incorrect outputs.
    """
    inputs = preprocessed_dir / "inputs"
    metadata_path = inputs / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"missing preprocessed metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text())
    grid_crs = rasterio.crs.CRS.from_string(metadata["grid"]["crs"])
    grid = GridSpec(
        crs=grid_crs,
        transform=Affine(*metadata["grid"]["transform"]),
        width=int(metadata["grid"]["width"]),
        height=int(metadata["grid"]["height"]),
        cell_size=float(metadata["config"]["cell_size_m"]),
    )

    # Reuse safety: the cached grid must match the requested CRS + cell size,
    # and must geographically cover the user's simulation domain (the buffer
    # around the protected polygon).
    if grid_crs != rasterio.crs.CRS.from_string(cfg.crs):
        raise PreprocessedReuseError(
            f"cached grid CRS {grid_crs.to_string()!r} does not match "
            f"requested CRS {cfg.crs!r}"
        )
    if abs(grid.cell_size - cfg.cell_size_m) > 1e-9:
        raise PreprocessedReuseError(
            f"cached cell size {grid.cell_size} does not match "
            f"requested {cfg.cell_size_m}"
        )

    polygon = shape(cfg.protected_polygon)
    polygon_in_target = (
        gpd.GeoDataFrame(geometry=[polygon], crs=protected_polygon_crs)
        .to_crs(cfg.crs)
        .geometry.iloc[0]
    )
    sim_domain = polygon_in_target.buffer(cfg.simulation_radius_m)
    sim_minx, sim_miny, sim_maxx, sim_maxy = sim_domain.bounds
    grid_minx, grid_miny, grid_maxx, grid_maxy = grid.bounds
    if (
        sim_minx < grid_minx - 1e-6
        or sim_miny < grid_miny - 1e-6
        or sim_maxx > grid_maxx + 1e-6
        or sim_maxy > grid_maxy + 1e-6
    ):
        raise PreprocessedReuseError(
            f"simulation domain bounds {(sim_minx, sim_miny, sim_maxx, sim_maxy)} "
            f"are not contained in cached grid bounds "
            f"{(grid_minx, grid_miny, grid_maxx, grid_maxy)}"
        )

    with tempfile.TemporaryDirectory(prefix="agrishield_domain_") as tmp:
        tmp_dir = Path(tmp)
        art = build_domain(
            protected_polygon=polygon,
            protected_polygon_crs=protected_polygon_crs,
            target_crs=cfg.crs,
            simulation_radius_m=cfg.simulation_radius_m,
            ignition_distance_m=cfg.ignition_distance_m,
            safety_buffer_m=cfg.safety_buffer_m,
            out_dir=tmp_dir,
        )
        shutil.copy2(art.ignition_points_path, inputs / "ignition_points.geojson")
        candidate_poly = gpd.read_file(art.candidate_zone_polygon_path).geometry.iloc[0]

    build_protected_mask(
        protected_polygon=polygon,
        polygon_crs=protected_polygon_crs,
        grid=grid,
        out_path=inputs / "protected_mask.tif",
    )
    build_candidate_zone_mask(
        candidate_polygon=candidate_poly,
        polygon_crs=cfg.crs,
        grid=grid,
        out_path=inputs / "candidate_zone.tif",
    )
    build_non_burnable_mask(
        grid=grid,
        fbfm40_path=inputs / "fbfm40.tif",
        sources=cfg.non_burnable_sources,
        out_path=inputs / "non_burnable_mask.tif",
    )


def san_diego_example_polygon() -> dict[str, Any]:
    """Example farm-sized rectangle near Ramona / San Pasqual Valley, San Diego County."""
    return rectangle_location(
        center_lon=SAN_DIEGO_EXAMPLE["center_lon"],
        center_lat=SAN_DIEGO_EXAMPLE["center_lat"],
        width_m=SAN_DIEGO_EXAMPLE["width_m"],
        height_m=SAN_DIEGO_EXAMPLE["height_m"],
    )


def run_san_diego_example(
    out_dir: Path = Path("jobs/san-diego-farm-example"),
) -> ElmfireEnsembleResult:
    """Run the end-to-end workflow for the bundled San Diego-area example location."""
    return run_location_fire_simulations(
        protected_polygon=san_diego_example_polygon(),
        out_dir=out_dir,
        protected_polygon_crs="EPSG:4326",
        target_crs="EPSG:5070",
        landfire_version=os.environ.get("LANDFIRE_VERSION", "LF2023"),
    )


def _load_geojson_geometry(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if data.get("type") == "FeatureCollection":
        if not data.get("features"):
            raise ValueError(f"{path} contains no features")
        geom = data["features"][0]["geometry"]
    elif data.get("type") == "Feature":
        geom = data["geometry"]
    else:
        geom = data
    parsed = shape(geom)
    if not isinstance(parsed, (Polygon, MultiPolygon)):
        raise ValueError("location geometry must be a Polygon or MultiPolygon")
    return mapping(parsed)


def _write_summary(path: Path, result: ElmfireEnsembleResult) -> None:
    runs = []
    for run in result.runs:
        scenario_summary_path = run.spec.output_dir / "summary.json"
        scenario_summary = (
            json.loads(scenario_summary_path.read_text())
            if scenario_summary_path.exists()
            else {}
        )
        run_payload = {
            "run_id": run.spec.run_id,
            "ok": run.ok,
            "returncode": run.returncode,
            "wind_to_direction_deg": run.spec.wind_to_direction_deg,
            "wind_from_direction_deg": run.spec.wind_from_direction_deg,
            "output_dir": str(run.spec.output_dir),
            "output_files": [str(path) for path in run.output_files],
        }
        run_payload.update(scenario_summary)
        runs.append(run_payload)

    payload = {
        "job_dir": str(result.job_dir),
        "protected_center": {
            "x": result.protected_center.x,
            "y": result.protected_center.y,
        },
        "runs": runs,
    }
    path.write_text(json.dumps(payload, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess a protected location and run 8 no-firebreak ELMFIRE simulations."
    )
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument(
        "--location-geojson",
        type=Path,
        help="GeoJSON Polygon/MultiPolygon file.",
    )
    location.add_argument(
        "--example-san-diego",
        action="store_true",
        help="Use an example farm-sized polygon near Ramona / San Pasqual Valley.",
    )
    location.add_argument(
        "--center-lon",
        type=float,
        help="Center longitude for a rectangular location.",
    )
    parser.add_argument("--center-lat", type=float, help="Center latitude for --center-lon.")
    parser.add_argument("--width-m", type=float, default=800.0, help="Rectangle width in meters.")
    parser.add_argument("--height-m", type=float, default=800.0, help="Rectangle height in meters.")
    parser.add_argument("--out", type=Path, default=Path("jobs/location-run"))
    parser.add_argument("--protected-polygon-crs", default="EPSG:4326")
    parser.add_argument("--target-crs", default="EPSG:5070")
    parser.add_argument("--simulation-radius-m", type=float, default=5000.0)
    parser.add_argument("--ignition-distance-m", type=float, default=4500.0)
    parser.add_argument("--safety-buffer-m", type=float, default=100.0)
    parser.add_argument("--cell-size-m", type=float, default=30.0)
    parser.add_argument("--landfire-version", default=None)
    parser.add_argument("--wind-speed-mps", type=float, default=6.7)
    parser.add_argument("--simulation-tstop-s", type=float, default=21_600.0)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--preprocessed-dir",
        type=Path,
        default=None,
        help="Reuse an existing preprocessing directory and refresh polygon-dependent inputs.",
    )
    parser.add_argument("--elmfire-executable", type=Path, default=DEFAULT_ELMFIRE)
    parser.add_argument(
        "--elmfire-runner",
        choices=("auto", "wsl", "native"),
        default="auto",
        help="How to run ELMFIRE. auto uses WSL on Windows and native subprocess elsewhere.",
    )
    parser.add_argument("--wsl-distro", default="Ubuntu")
    return parser.parse_args()


def main() -> None:
    load_env_file()
    args = _parse_args()
    if args.location_geojson is not None:
        polygon = _load_geojson_geometry(args.location_geojson)
        polygon_crs = args.protected_polygon_crs
    elif args.example_san_diego:
        polygon = san_diego_example_polygon()
        polygon_crs = "EPSG:4326"
    else:
        if args.center_lat is None:
            raise ValueError("--center-lat is required with --center-lon")
        polygon = rectangle_location(args.center_lon, args.center_lat, args.width_m, args.height_m)
        polygon_crs = "EPSG:4326"

    result = run_location_fire_simulations(
        protected_polygon=polygon,
        out_dir=args.out,
        protected_polygon_crs=polygon_crs,
        target_crs=args.target_crs,
        simulation_radius_m=args.simulation_radius_m,
        ignition_distance_m=args.ignition_distance_m,
        safety_buffer_m=args.safety_buffer_m,
        cell_size_m=args.cell_size_m,
        landfire_version=args.landfire_version,
        wind_speed_mps=args.wind_speed_mps,
        simulation_tstop_s=args.simulation_tstop_s,
        cache_dir=args.cache_dir,
        elmfire_executable=args.elmfire_executable,
        elmfire_runner=args.elmfire_runner,
        wsl_distro=args.wsl_distro,
        preprocessed_dir=args.preprocessed_dir,
    )
    print(f"Completed {sum(run.ok for run in result.runs)}/{len(result.runs)} ELMFIRE runs")
    print(f"Summary: {args.out / 'simulation_summary.json'}")


if __name__ == "__main__":
    main()
