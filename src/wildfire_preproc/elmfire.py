"""Run no-firebreak ELMFIRE simulations from preprocessed job inputs."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import Point, shape

from wildfire_preproc.config import JobConfig
from wildfire_preproc.utils.geometry import bearing_between_points

MPH_PER_MPS = 2.2369362920544


@dataclass(frozen=True)
class ElmfireRunSpec:
    """All inputs needed for one no-firebreak ELMFIRE run."""

    run_id: str
    ignition_bearing_deg: float
    ignition_x: float
    ignition_y: float
    protected_center_x: float
    protected_center_y: float
    wind_to_direction_deg: float
    wind_from_direction_deg: float
    wind_speed_mps: float
    inputs_dir: Path
    output_dir: Path
    config_path: Path
    ignition_point_path: Path
    raster_paths: dict[str, Path]
    firebreak_mask_path: Path | None = None


@dataclass(frozen=True)
class ElmfireRunResult:
    """Result data for one ELMFIRE run."""

    spec: ElmfireRunSpec
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float
    output_files: list[Path]

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class ElmfireEnsembleResult:
    """All eight no-firebreak simulation results."""

    job_dir: Path
    protected_center: Point
    runs: list[ElmfireRunResult]


class ElmfireRunner(Protocol):
    """Executes one prepared ELMFIRE run spec."""

    def run(self, spec: ElmfireRunSpec) -> ElmfireRunResult: ...


class SubprocessElmfireRunner:
    """Run ELMFIRE through a local command.

    Command arguments may include these placeholders:
    - `{config}`: per-run `elmfire.data` path
    - `{out_dir}`: per-run output directory
    - `{inputs_dir}`: per-run ELMFIRE `inputs/` directory

    If no `{config}` placeholder is present, the config path is appended.
    """

    def __init__(
        self,
        command: Sequence[str],
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ):
        if not command:
            raise ValueError("ELMFIRE command must not be empty")
        self._command = tuple(command)
        self._timeout_s = timeout_s
        self._env = dict(env) if env is not None else None

    def run(self, spec: ElmfireRunSpec) -> ElmfireRunResult:
        cmd = [
            part.format(
                config=str(spec.config_path),
                out_dir=str(spec.output_dir),
                inputs_dir=str(spec.inputs_dir),
            )
            for part in self._command
        ]
        if not any("{config}" in part for part in self._command):
            cmd.append(str(spec.config_path))

        env = os.environ.copy()
        if self._env:
            env.update(self._env)

        started = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=spec.output_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=self._timeout_s,
            check=False,
        )
        elapsed = time.monotonic() - started
        return ElmfireRunResult(
            spec=spec,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_s=elapsed,
            output_files=_list_output_files(spec.output_dir),
        )


class WslElmfireRunner:
    """Run a Linux ELMFIRE executable through WSL from the Windows workspace."""

    def __init__(
        self,
        executable: Path,
        distro: str = "Ubuntu",
        timeout_s: float | None = None,
    ):
        self._executable = Path(executable).resolve()
        self._distro = distro
        self._timeout_s = timeout_s

    def run(self, spec: ElmfireRunSpec) -> ElmfireRunResult:
        started = time.monotonic()
        proc = subprocess.run(
            [
                "wsl",
                "-d",
                self._distro,
                "--",
                "bash",
                "-lc",
                (
                    f"cd {_sh_quote(_wsl_path(spec.output_dir))} && "
                    f"{_sh_quote(_wsl_path(self._executable))} ./inputs/elmfire.data"
                ),
            ],
            text=True,
            capture_output=True,
            timeout=self._timeout_s,
            check=False,
        )
        elapsed = time.monotonic() - started
        return ElmfireRunResult(
            spec=spec,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_s=elapsed,
            output_files=_list_output_files(spec.output_dir),
        )


def run_no_firebreak_elmfire_ensemble(
    cfg: JobConfig,
    job_dir: Path,
    runner: ElmfireRunner,
    protected_polygon_crs: str = "EPSG:4326",
    out_dir: Path | None = None,
    wind_speed_mps: float = 6.7,
    simulation_tstop_s: float = 21_600.0,
    simulation_dt_s: float = 30.0,
    dump_interval_s: float = 3_600.0,
    fail_fast: bool = True,
) -> ElmfireEnsembleResult:
    """Run 8 no-firebreak ELMFIRE simulations around the protected polygon.

    The existing preprocessing pipeline must already have produced `job_dir/inputs`.
    Each simulation gets an ELMFIRE-ready `inputs/elmfire.data` deck plus the
    required raster inputs. Wind direction is set as wind-from, so the resulting
    spread direction points from the ignition toward the protected polygon
    centroid. No fuel or canopy rasters are modified for firebreaks.
    """
    job_dir = Path(job_dir).resolve()
    inputs_dir = job_dir / "inputs"
    out_dir = (
        Path(out_dir).resolve()
        if out_dir is not None
        else job_dir / "elmfire_no_firebreak"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    raster_paths = _required_rasters(inputs_dir)
    ignition_points = _load_ignition_points(inputs_dir / "ignition_points.geojson", cfg.crs)
    protected_center = _protected_center(cfg, protected_polygon_crs)

    results: list[ElmfireRunResult] = []
    for row in ignition_points.itertuples(index=False):
        ignition = row.geometry
        if not isinstance(ignition, Point):
            raise ValueError("ignition_points.geojson must contain point geometries")

        ignition_bearing = float(row.bearing_deg)
        run_id = f"bearing_{round(ignition_bearing):03d}"
        run_dir = out_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run_inputs_dir = run_dir / "inputs"
        run_outputs_dir = run_dir / "outputs"
        scratch_dir = run_dir / "scratch"
        run_inputs_dir.mkdir(parents=True, exist_ok=True)
        run_outputs_dir.mkdir(parents=True, exist_ok=True)
        scratch_dir.mkdir(parents=True, exist_ok=True)

        wind_to = bearing_between_points(ignition, protected_center)
        wind_from = (wind_to + 180.0) % 360.0
        ignition_path = run_dir / "ignition_point.geojson"
        config_path = run_inputs_dir / "elmfire.data"

        _write_ignition_point(ignition_path, ignition, cfg.crs, ignition_bearing)
        spec = ElmfireRunSpec(
            run_id=run_id,
            ignition_bearing_deg=ignition_bearing,
            ignition_x=float(ignition.x),
            ignition_y=float(ignition.y),
            protected_center_x=float(protected_center.x),
            protected_center_y=float(protected_center.y),
            wind_to_direction_deg=wind_to,
            wind_from_direction_deg=wind_from,
            wind_speed_mps=wind_speed_mps,
            inputs_dir=run_inputs_dir,
            output_dir=run_dir,
            config_path=config_path,
            ignition_point_path=ignition_path,
            raster_paths=raster_paths,
            firebreak_mask_path=None,
        )
        _prepare_elmfire_inputs(
            spec=spec,
            source_rasters=raster_paths,
            crs=cfg.crs,
            simulation_tstop_s=simulation_tstop_s,
            simulation_dt_s=simulation_dt_s,
            dump_interval_s=dump_interval_s,
        )
        result = runner.run(spec)
        results.append(result)
        if fail_fast and not result.ok:
            raise RuntimeError(
                f"ELMFIRE run {run_id} failed with exit code {result.returncode}:\n"
                f"{result.stderr}"
            )

    return ElmfireEnsembleResult(job_dir=job_dir, protected_center=protected_center, runs=results)


def _protected_center(cfg: JobConfig, protected_polygon_crs: str) -> Point:
    polygon = shape(cfg.protected_polygon)
    gdf = gpd.GeoDataFrame(geometry=[polygon], crs=protected_polygon_crs).to_crs(cfg.crs)
    centroid = gdf.geometry.iloc[0].centroid
    return Point(float(centroid.x), float(centroid.y))


def _load_ignition_points(path: Path, target_crs: str) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    gdf = gpd.read_file(path)
    if "bearing_deg" not in gdf.columns:
        raise ValueError(f"{path} is missing required bearing_deg column")
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS")
    if len(gdf) != 8:
        raise ValueError(f"{path} must contain exactly 8 ignition points, got {len(gdf)}")
    return gdf.to_crs(target_crs).sort_values("bearing_deg").reset_index(drop=True)


def _required_rasters(inputs_dir: Path) -> dict[str, Path]:
    names = [
        "fbfm40",
        "dem",
        "slp",
        "asp",
        "cc",
        "ch",
        "cbh",
        "cbd",
        "protected_mask",
        "candidate_zone",
        "non_burnable_mask",
    ]
    paths = {name: inputs_dir / f"{name}.tif" for name in names}
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("missing preprocessed raster(s): " + ", ".join(missing))
    return paths


def _write_ignition_point(path: Path, point: Point, crs: str, bearing_deg: float) -> None:
    gdf = gpd.GeoDataFrame(
        [{"bearing_deg": bearing_deg, "geometry": point}],
        crs=crs,
    )
    gdf.to_file(path, driver="GeoJSON")


def _prepare_elmfire_inputs(
    spec: ElmfireRunSpec,
    source_rasters: dict[str, Path],
    crs: str,
    simulation_tstop_s: float,
    simulation_dt_s: float,
    dump_interval_s: float,
) -> None:
    _write_elmfire_rasters(
        source_rasters=source_rasters,
        inputs_dir=spec.inputs_dir,
        wind_speed_mps=spec.wind_speed_mps,
        wind_from_direction_deg=spec.wind_from_direction_deg,
    )
    _write_elmfire_data(
        path=spec.config_path,
        spec=spec,
        crs=crs,
        simulation_tstop_s=simulation_tstop_s,
        simulation_dt_s=simulation_dt_s,
        dump_interval_s=dump_interval_s,
    )
    _write_run_manifest(spec, crs)


def _write_elmfire_rasters(
    source_rasters: dict[str, Path],
    inputs_dir: Path,
    wind_speed_mps: float,
    wind_from_direction_deg: float,
) -> None:
    ref_path = source_rasters["dem"]
    _write_constant_like(ref_path, inputs_dir / "ws.tif", wind_speed_mps * MPH_PER_MPS)
    _write_constant_like(ref_path, inputs_dir / "wd.tif", wind_from_direction_deg)
    _write_constant_like(ref_path, inputs_dir / "m1.tif", 6.0)
    _write_constant_like(ref_path, inputs_dir / "m10.tif", 7.0)
    _write_constant_like(ref_path, inputs_dir / "m100.tif", 8.0)
    _write_constant_like(ref_path, inputs_dir / "adj.tif", 1.0)
    _write_constant_like(ref_path, inputs_dir / "phi.tif", 1.0)

    _copy_as_int16(source_rasters["slp"], inputs_dir / "slp.tif")
    _copy_as_int16(source_rasters["asp"], inputs_dir / "asp.tif", flat_value=0)
    _copy_as_int16(source_rasters["dem"], inputs_dir / "dem.tif")
    _copy_as_int16(source_rasters["fbfm40"], inputs_dir / "fbfm40.tif", nodata_value=99)
    _copy_as_int16(source_rasters["cc"], inputs_dir / "cc.tif")
    _copy_as_int16(source_rasters["ch"], inputs_dir / "ch.tif", scale=10.0)
    _copy_as_int16(source_rasters["cbh"], inputs_dir / "cbh.tif", scale=10.0)
    _copy_as_int16(source_rasters["cbd"], inputs_dir / "cbd.tif", scale=100.0)


def _write_constant_like(ref_path: Path, out_path: Path, value: float) -> None:
    with rasterio.open(ref_path) as src:
        profile = src.profile.copy()
        data = np.full((src.height, src.width), value, dtype="float32")
    profile.update(dtype="float32", nodata=-9999.0, count=1, compress="LZW")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data, 1)


def _copy_as_int16(
    src_path: Path,
    out_path: Path,
    scale: float = 1.0,
    flat_value: int | None = None,
    nodata_value: int = -9999,
) -> None:
    with rasterio.open(src_path) as src:
        arr = src.read(1).astype("float32")
        src_nodata = src.nodata
        profile = src.profile.copy()

    valid = np.ones(arr.shape, dtype=bool)
    if src_nodata is not None:
        valid = arr != src_nodata
    if flat_value is not None:
        arr = np.where((arr < 0) & valid, flat_value, arr)
    arr = np.where(valid, np.rint(arr * scale), nodata_value)
    arr = np.clip(arr, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype("int16")

    profile.update(dtype="int16", nodata=nodata_value, count=1, compress="LZW")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr, 1)


def _write_elmfire_data(
    path: Path,
    spec: ElmfireRunSpec,
    crs: str,
    simulation_tstop_s: float,
    simulation_dt_s: float,
    dump_interval_s: float,
) -> None:
    with rasterio.open(spec.raster_paths["dem"]) as src:
        bounds = src.bounds
        cell_size = abs(src.transform.a)

    content = f"""&INPUTS
FUELS_AND_TOPOGRAPHY_DIRECTORY = './inputs'
ASP_FILENAME                   = 'asp'
CBD_FILENAME                   = 'cbd'
CBH_FILENAME                   = 'cbh'
CC_FILENAME                    = 'cc'
CH_FILENAME                    = 'ch'
DEM_FILENAME                   = 'dem'
FBFM_FILENAME                  = 'fbfm40'
SLP_FILENAME                   = 'slp'
ADJ_FILENAME                   = 'adj'
PHI_FILENAME                   = 'phi'
DT_METEOROLOGY                 = 3600.0
WEATHER_DIRECTORY              = './inputs'
WS_FILENAME                    = 'ws'
WD_FILENAME                    = 'wd'
M1_FILENAME                    = 'm1'
M10_FILENAME                   = 'm10'
M100_FILENAME                  = 'm100'
LH_MOISTURE_CONTENT            = 30.0
LW_MOISTURE_CONTENT            = 60.0
/

&OUTPUTS
OUTPUTS_DIRECTORY              = './outputs'
DTDUMP                         = {dump_interval_s:.1f}
DUMP_FLIN                      = .TRUE.
DUMP_SPREAD_RATE               = .TRUE.
DUMP_TIME_OF_ARRIVAL           = .TRUE.
CONVERT_TO_GEOTIFF             = .TRUE.
/

&COMPUTATIONAL_DOMAIN
A_SRS                          = '{crs}'
COMPUTATIONAL_DOMAIN_CELLSIZE  = {cell_size:.6f}
COMPUTATIONAL_DOMAIN_XLLCORNER = {bounds.left:.6f}
COMPUTATIONAL_DOMAIN_YLLCORNER = {bounds.bottom:.6f}
/

&TIME_CONTROL
SIMULATION_DT                  = {simulation_dt_s:.1f}
SIMULATION_TSTOP               = {simulation_tstop_s:.1f}
/

&SIMULATOR
NUM_IGNITIONS                  = 1
X_IGN(1)                       = {spec.ignition_x:.6f}
Y_IGN(1)                       = {spec.ignition_y:.6f}
T_IGN(1)                       = 0.0
WX_BILINEAR_INTERPOLATION      = .TRUE.
WSMFEFF_LOW_MULT               = 0.011364
/

&MISCELLANEOUS
PATH_TO_GDAL                   = '/usr/bin'
SCRATCH                        = './scratch'
/
"""
    path.write_text(content)


def _write_run_manifest(spec: ElmfireRunSpec, crs: str) -> None:
    payload = {
        "run_id": spec.run_id,
        "crs": crs,
        "no_firebreaks": True,
        "elmfire_data": str(spec.config_path),
        "ignition": {
            "path": str(spec.ignition_point_path),
            "bearing_deg": spec.ignition_bearing_deg,
            "x": spec.ignition_x,
            "y": spec.ignition_y,
        },
        "protected_center": {
            "x": spec.protected_center_x,
            "y": spec.protected_center_y,
        },
        "wind": {
            "to_direction_deg": spec.wind_to_direction_deg,
            "from_direction_deg": spec.wind_from_direction_deg,
            "speed_mps": spec.wind_speed_mps,
        },
        "inputs_dir": str(spec.inputs_dir),
        "rasters": {key: str(path) for key, path in spec.raster_paths.items()},
        "firebreak_mask": None,
        "output_dir": str(spec.output_dir),
    }
    (spec.output_dir / "run_manifest.json").write_text(json.dumps(payload, indent=2))


def _wsl_path(path: Path) -> str:
    resolved = Path(path).resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _list_output_files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*") if p.is_file())
