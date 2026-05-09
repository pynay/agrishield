"""Local AgriShield web server.

Serves the no-build UI and accepts backend-ready job JSON from the browser.
"""

from __future__ import annotations

import json
import sys
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

from wildfire_preproc.config import JobConfig  # noqa: E402


class AgriShieldHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_POST(self) -> None:
        if self.path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body)
            cfg = JobConfig.model_validate(payload)
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

        relative_job = job_json.relative_to(PROJECT_ROOT)
        relative_location = location_geojson.relative_to(PROJECT_ROOT)
        preprocessed_dir = job_dir / "preprocessed"
        response = {
            "job_json": str(relative_job),
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
        self._send_json(HTTPStatus.CREATED, response)

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


def main() -> None:
    host = "127.0.0.1"
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
    server = ThreadingHTTPServer((host, port), AgriShieldHandler)
    print(f"Serving AgriShield at http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
