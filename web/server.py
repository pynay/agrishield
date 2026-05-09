"""Local AgriShield web server.

Serves the no-build UI and accepts backend-ready job JSON from the browser.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
WEB_ROOT = PROJECT_ROOT / "web"
JOBS_ROOT = PROJECT_ROOT / "jobs" / "ui"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wildfire_preproc.config import JobConfig  # noqa: E402

RUNS: dict[str, dict[str, Any]] = {}
LOCATION_RUNS: dict[str, dict[str, Any]] = {}
RUNS_LOCK = threading.Lock()


class AgriShieldHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_POST(self) -> None:
        if self.path == "/api/jobs":
            self._create_job(save_only=True)
            return
        if self.path == "/api/simulations":
            self._create_job(save_only=False)
            return
        if self.path == "/api/location-preprocess":
            self._create_location_preprocess()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_GET(self) -> None:
        if self.path.startswith("/api/location-preprocess/"):
            run_id = self.path.rsplit("/", 1)[-1]
            with RUNS_LOCK:
                run = LOCATION_RUNS.get(run_id)
                payload = dict(run) if run is not None else None
            if payload is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown location preprocessing run"})
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        if self.path.startswith("/api/simulations/"):
            run_id = self.path.rsplit("/", 1)[-1]
            with RUNS_LOCK:
                run = RUNS.get(run_id)
                payload = dict(run) if run is not None else None
            if payload is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown simulation"})
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        super().do_GET()

    def _create_job(self, save_only: bool) -> None:
        try:
            request = self._read_json_body()
            cfg = JobConfig.model_validate(request.get("job", request))
            options = request.get("options", {}) if isinstance(request, dict) else {}
            if not isinstance(options, dict):
                options = {}
            job_dir = _next_job_dir()
            job_dir.mkdir(parents=True, exist_ok=False)
            job_json = job_dir / "job.json"
            location_geojson = job_dir / "location.geojson"
            job_json.write_text(json.dumps(cfg.model_dump(), indent=2))
            location_geojson.write_text(json.dumps(cfg.protected_polygon, indent=2))
        except json.JSONDecodeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON: {exc.msg}"})
            return
        except ValidationError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Invalid job", "details": exc.errors()},
            )
            return

        if save_only:
            self._send_json(HTTPStatus.CREATED, _job_response(job_dir, job_json, location_geojson, cfg))
            return

        run_id = uuid.uuid4().hex[:12]
        run_record = _initial_run_record(run_id, job_dir, job_json, location_geojson, cfg)
        with RUNS_LOCK:
            RUNS[run_id] = run_record
        worker = threading.Thread(
            target=_run_simulation_job,
            args=(run_id, job_dir, job_json, location_geojson, cfg, options),
            daemon=True,
        )
        worker.start()
        self._send_json(HTTPStatus.ACCEPTED, run_record)

    def _create_location_preprocess(self) -> None:
        try:
            request = self._read_json_body()
            lat = float(request["lat"])
            lon = float(request["lon"])
            label = str(request.get("label") or "Selected location")
            simulation_radius_m = float(request.get("simulation_radius_m", 5000.0))
            ignition_distance_m = float(request.get("ignition_distance_m", 4500.0))
            cell_size_m = float(request.get("cell_size_m", 30.0))
            target_crs = str(request.get("crs", "EPSG:5070"))
            landfire_version = str(request.get("landfire_version", "LF2023"))
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid location payload: {exc}"})
            return

        run_id = uuid.uuid4().hex[:12]
        job_dir = JOBS_ROOT / f"location_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{run_id}"
        job_dir.mkdir(parents=True, exist_ok=False)
        run_record = {
            "run_id": run_id,
            "status": "queued",
            "stage": "queued",
            "label": label,
            "lat": lat,
            "lon": lon,
            "job_dir": str(job_dir.relative_to(PROJECT_ROOT)),
            "preprocessed_dir": str((job_dir / "preprocessed").relative_to(PROJECT_ROOT)),
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "returncode": None,
            "error": None,
            "log_tail": "",
        }
        with RUNS_LOCK:
            LOCATION_RUNS[run_id] = run_record
        worker = threading.Thread(
            target=_run_location_preprocess,
            args=(
                run_id,
                job_dir,
                lon,
                lat,
                simulation_radius_m,
                ignition_distance_m,
                cell_size_m,
                target_crs,
                landfire_version,
            ),
            daemon=True,
        )
        worker.start()
        self._send_json(HTTPStatus.ACCEPTED, run_record)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("Expected JSON object", raw_body.decode("utf-8"), 0)
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _next_job_dir() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = JOBS_ROOT / timestamp
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = JOBS_ROOT / f"{timestamp}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def _job_response(
    job_dir: Path,
    job_json: Path,
    location_geojson: Path,
    cfg: JobConfig,
) -> dict[str, Any]:
    relative_job = job_json.relative_to(PROJECT_ROOT)
    relative_location = location_geojson.relative_to(PROJECT_ROOT)
    preprocessed_dir = job_dir / "preprocessed"
    return {
        "job_json": str(relative_job),
        "job_dir": str(job_dir.relative_to(PROJECT_ROOT)),
        "run_preprocess_command": (
            f"uv run wildfire-preproc run {relative_job} --out {preprocessed_dir}"
        ),
        "run_full_command": (
            f"uv run python main.py --location-geojson {relative_location} --out {job_dir} "
            f"--simulation-radius-m {cfg.simulation_radius_m:g} "
            f"--ignition-distance-m {cfg.ignition_distance_m:g} "
            f"--cell-size-m {cfg.cell_size_m:g} --target-crs {cfg.crs} "
            f"--landfire-version {cfg.landfire_version}"
        ),
    }


def _initial_run_record(
    run_id: str,
    job_dir: Path,
    job_json: Path,
    location_geojson: Path,
    cfg: JobConfig,
) -> dict[str, Any]:
    payload = _job_response(job_dir, job_json, location_geojson, cfg)
    payload.update(
        {
            "run_id": run_id,
            "status": "queued",
            "stage": "queued",
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "returncode": None,
            "error": None,
            "summary": None,
            "optimization": None,
            "log_tail": "",
        }
    )
    return payload


def _update_run(run_id: str, **updates: Any) -> None:
    with RUNS_LOCK:
        if run_id in RUNS:
            RUNS[run_id].update(updates)


def _update_location_run(run_id: str, **updates: Any) -> None:
    with RUNS_LOCK:
        if run_id in LOCATION_RUNS:
            LOCATION_RUNS[run_id].update(updates)


def _completed_location_preprocess(run_id: str | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    with RUNS_LOCK:
        run = LOCATION_RUNS.get(run_id)
        if run is None or run.get("status") != "completed":
            return None
        return dict(run)


def _run_location_preprocess(
    run_id: str,
    job_dir: Path,
    lon: float,
    lat: float,
    simulation_radius_m: float,
    ignition_distance_m: float,
    cell_size_m: float,
    target_crs: str,
    landfire_version: str,
) -> None:
    try:
        from main import rectangle_location

        from wildfire_preproc.pipeline import run_pipeline
        from wildfire_preproc.sources.registry import DefaultSourceRegistry

        env_landfire_version = os.environ.get("LANDFIRE_VERSION", landfire_version)
        cfg = JobConfig.model_validate(
            {
                "protected_polygon": rectangle_location(lon, lat, 250.0, 250.0),
                "simulation_radius_m": simulation_radius_m,
                "ignition_distance_m": ignition_distance_m,
                "cell_size_m": cell_size_m,
                "crs": target_crs,
                "landfire_version": env_landfire_version,
            }
        )
        job_json = job_dir / "location_vegetation_job.json"
        job_json.write_text(json.dumps(cfg.model_dump(), indent=2))
        _update_location_run(run_id, status="running", stage="fetching_vegetation")
        source = DefaultSourceRegistry(
            cache_dir=Path.home() / ".cache" / "wildfire-preproc",
            landfire_version=cfg.landfire_version,
        )
        run_pipeline(
            cfg=cfg,
            out_dir=job_dir / "preprocessed",
            source=source,
            protected_polygon_crs="EPSG:4326",
            keep_intermediate=False,
        )
        _update_location_run(
            run_id,
            status="completed",
            stage="completed",
            returncode=0,
            finished_at=datetime.now(UTC).isoformat(),
            log_tail=f"Vegetation and terrain preprocessing complete for {lat}, {lon}",
        )
    except Exception as exc:  # pragma: no cover - UI bridge safety net
        _update_location_run(
            run_id,
            status="failed",
            stage="error",
            error=str(exc),
            finished_at=datetime.now(UTC).isoformat(),
            log_tail=traceback.format_exc(),
        )


def _run_simulation_job(
    run_id: str,
    job_dir: Path,
    job_json: Path,
    location_geojson: Path,
    cfg: JobConfig,
    options: dict[str, Any],
) -> None:
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(SRC_ROOT)
            if not env.get("PYTHONPATH")
            else str(SRC_ROOT) + os.pathsep + env["PYTHONPATH"]
        )
        wind_speed = float(options.get("wind_speed_mps", 6.7))
        timeout = options.get("timeout_s")
        full_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--location-geojson",
            str(location_geojson),
            "--out",
            str(job_dir),
            "--simulation-radius-m",
            f"{cfg.simulation_radius_m:g}",
            "--ignition-distance-m",
            f"{cfg.ignition_distance_m:g}",
            "--cell-size-m",
            f"{cfg.cell_size_m:g}",
            "--target-crs",
            cfg.crs,
            "--landfire-version",
            cfg.landfire_version,
            "--wind-speed-mps",
            f"{wind_speed:g}",
        ]
        location_preprocess_id = options.get("location_preprocess_id")
        location_preprocess = _completed_location_preprocess(str(location_preprocess_id))
        if location_preprocess is not None:
            source_preprocessed = PROJECT_ROOT / location_preprocess["preprocessed_dir"]
            run_preprocessed = job_dir / "preprocessed"
            shutil.copytree(source_preprocessed, run_preprocessed)
            full_cmd.extend(
                [
                    "--preprocessed-dir",
                    str(run_preprocessed),
                ]
            )
        _update_run(run_id, status="running", stage="preprocess_and_elmfire")
        proc = subprocess.run(
            full_cmd,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=float(timeout) if timeout is not None else None,
            check=False,
        )
        log_tail = _tail(proc.stdout, proc.stderr)
        if proc.returncode != 0:
            _update_run(
                run_id,
                status="failed",
                stage="preprocess_and_elmfire",
                returncode=proc.returncode,
                error="ELMFIRE workflow failed",
                finished_at=datetime.now(UTC).isoformat(),
                log_tail=log_tail,
            )
            return

        summary_path = job_dir / "simulation_summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else None

        _update_run(
            run_id,
            stage="optimize_firebreaks",
            returncode=proc.returncode,
            summary=summary,
            log_tail=log_tail,
        )
        optimize_cmd = [
            sys.executable,
            "-m",
            "wildfire_preproc.cli",
            "optimize-firebreaks",
            str(job_dir / "preprocessed"),
            "--baseline-dir",
            str(job_dir / "preprocessed" / "elmfire_no_firebreak"),
        ]
        opt_proc = subprocess.run(
            optimize_cmd,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        combined_tail = _tail(log_tail, opt_proc.stdout, opt_proc.stderr)
        if opt_proc.returncode != 0:
            _update_run(
                run_id,
                status="failed",
                stage="optimize_firebreaks",
                returncode=opt_proc.returncode,
                error="Firebreak optimization failed",
                finished_at=datetime.now(UTC).isoformat(),
                log_tail=combined_tail,
            )
            return

        optimization_path = (
            job_dir / "preprocessed" / "firebreak_optimization" / "firebreak_optimization.json"
        )
        optimization = (
            json.loads(optimization_path.read_text()) if optimization_path.exists() else None
        )
        _update_run(
            run_id,
            status="completed",
            stage="completed",
            returncode=0,
            finished_at=datetime.now(UTC).isoformat(),
            summary=summary,
            optimization=optimization,
            log_tail=combined_tail,
        )
    except Exception as exc:  # pragma: no cover - UI bridge safety net
        _update_run(
            run_id,
            status="failed",
            stage="error",
            error=str(exc),
            finished_at=datetime.now(UTC).isoformat(),
            log_tail=traceback.format_exc(),
        )


def _tail(*parts: str, max_chars: int = 6000) -> str:
    text = "\n".join(part for part in parts if part)
    return text[-max_chars:]


def main() -> None:
    host = "127.0.0.1"
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
    server = ThreadingHTTPServer((host, port), AgriShieldHandler)
    print(f"Serving AgriShield at http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
