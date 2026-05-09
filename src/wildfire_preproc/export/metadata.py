"""Stage 7 — build metadata.json describing the job's outputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import JobConfig


def build_metadata(
    job_id: str,
    cfg: JobConfig,
    grid: GridSpec,
    layer_sources: dict[str, str],
    validation_status: str,
    pipeline_version: str = "0.1.0",
) -> dict[str, Any]:
    minx, miny, maxx, maxy = grid.bounds
    return {
        "job_id": job_id,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "simulation_radius_m": cfg.simulation_radius_m,
            "ignition_distance_m": cfg.ignition_distance_m,
            "cell_size_m": cfg.cell_size_m,
            "crs": cfg.crs,
            "safety_buffer_m": cfg.safety_buffer_m,
            "non_burnable_sources": cfg.non_burnable_sources,
        },
        "grid": {
            "crs": cfg.crs,
            "transform": list(grid.transform)[:6],
            "width": grid.width,
            "height": grid.height,
            "bounds": [minx, miny, maxx, maxy],
        },
        "layers": {
            "fbfm40": {"path": "fbfm40.tif", "kind": "categorical", "nodata": 255,
                       "source": layer_sources.get("fbfm40", "unknown")},
            "dem":    {"path": "dem.tif",    "kind": "continuous",  "nodata": -9999,
                       "source": layer_sources.get("dem", "unknown")},
            "slp":    {"path": "slp.tif",    "kind": "continuous",  "nodata": -9999,
                       "source": "derived:dem", "units": "degrees"},
            "asp":    {"path": "asp.tif",    "kind": "continuous",  "nodata": -9999,
                       "source": "derived:dem", "units": "degrees_cw_from_N", "flat_value": -1},
            "cc":     {"path": "cc.tif",     "kind": "continuous",  "nodata": -9999,
                       "source": layer_sources.get("cc", "unknown")},
            "ch":     {"path": "ch.tif",     "kind": "continuous",  "nodata": -9999,
                       "source": layer_sources.get("ch", "unknown")},
            "cbh":    {"path": "cbh.tif",    "kind": "continuous",  "nodata": -9999,
                       "source": layer_sources.get("cbh", "unknown")},
            "cbd":    {"path": "cbd.tif",    "kind": "continuous",  "nodata": -9999,
                       "source": layer_sources.get("cbd", "unknown")},
            "protected_mask":    {"path": "protected_mask.tif",    "kind": "mask", "nodata": None},
            "candidate_zone":    {"path": "candidate_zone.tif",    "kind": "mask", "nodata": None},
            "non_burnable_mask": {"path": "non_burnable_mask.tif", "kind": "mask", "nodata": None,
                                  "sources": cfg.non_burnable_sources},
        },
        "ignition_points": "ignition_points.geojson",
        "validation": validation_status,
        "pipeline_version": pipeline_version,
    }
