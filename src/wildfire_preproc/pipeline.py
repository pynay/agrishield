"""Pipeline orchestrator - runs Stages 1-7 in order against a `JobConfig`."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
from rasterio.crs import CRS
from shapely.geometry import shape

from wildfire_preproc.align.align import reproject_match
from wildfire_preproc.align.grid import gridspec_from_polygon
from wildfire_preproc.config import (
    FETCHED_LAYERS,
    JobConfig,
    LayerKey,
    layer_kind,
)
from wildfire_preproc.domain.simulation_domain import build_domain
from wildfire_preproc.export.metadata import build_metadata
from wildfire_preproc.masks.candidate import build_candidate_zone_mask
from wildfire_preproc.masks.non_burnable import build_non_burnable_mask
from wildfire_preproc.masks.protected import build_protected_mask
from wildfire_preproc.sources.base import RasterSource
from wildfire_preproc.terrain.derive import derive_slope_aspect
from wildfire_preproc.utils.raster import nodata_for_kind
from wildfire_preproc.validation.checks import validate_raster
from wildfire_preproc.validation.report import format_report


def run_pipeline(
    cfg: JobConfig,
    out_dir: Path,
    source: RasterSource,
    protected_polygon_crs: str = "EPSG:4326",
    keep_intermediate: bool = False,
) -> None:
    """Run all 7 stages and write outputs to `out_dir`."""
    inputs = out_dir / "inputs"
    intermediate = out_dir / "_intermediate"
    inputs.mkdir(parents=True, exist_ok=True)
    intermediate.mkdir(parents=True, exist_ok=True)

    # Stage 1 - domain
    polygon = shape(cfg.protected_polygon)
    art = build_domain(
        protected_polygon=polygon,
        protected_polygon_crs=protected_polygon_crs,
        target_crs=cfg.crs,
        simulation_radius_m=cfg.simulation_radius_m,
        ignition_distance_m=cfg.ignition_distance_m,
        safety_buffer_m=cfg.safety_buffer_m,
        out_dir=intermediate,
    )
    # Move the deliverable copy of ignition_points into inputs/
    deliverable_pts = inputs / "ignition_points.geojson"
    shutil.copy2(art.ignition_points_path, deliverable_pts)

    # Stage 3 - grid (built before fetching, since fetch bbox uses domain bounds in cfg.crs).
    grid = gridspec_from_polygon(
        art.simulation_polygon, crs=CRS.from_string(cfg.crs), cell_size=cfg.cell_size_m
    )

    # Stage 2 - fetch
    bbox = grid.bounds  # already in cfg.crs and snapped to grid
    raw_paths: dict[LayerKey, Path] = {}
    for layer in FETCHED_LAYERS:
        raw_paths[layer] = source.fetch(layer, bbox=bbox, dst_crs=cfg.crs)

    # Stage 3 - align each fetched raster onto the canonical grid
    aligned: dict[LayerKey, Path] = {}
    for layer, src_path in raw_paths.items():
        kind = layer_kind(layer)
        dst = inputs / f"{layer.value}.tif"
        reproject_match(
            src=src_path, dst=dst, grid=grid, kind=kind,
            dst_nodata=nodata_for_kind(kind),
        )
        aligned[layer] = dst

    # Stage 4 - terrain
    derive_slope_aspect(
        dem_path=aligned[LayerKey.DEM],
        grid=grid,
        out_slope_path=inputs / "slp.tif",
        out_aspect_path=inputs / "asp.tif",
    )

    # Stage 5 - masks
    build_protected_mask(
        protected_polygon=polygon,
        polygon_crs=protected_polygon_crs,
        grid=grid,
        out_path=inputs / "protected_mask.tif",
    )
    candidate_poly = gpd.read_file(art.candidate_zone_polygon_path).geometry.iloc[0]
    build_candidate_zone_mask(
        candidate_polygon=candidate_poly,
        polygon_crs=cfg.crs,
        grid=grid,
        out_path=inputs / "candidate_zone.tif",
    )
    build_non_burnable_mask(
        grid=grid,
        fbfm40_path=aligned[LayerKey.FBFM40],
        sources=cfg.non_burnable_sources,
        out_path=inputs / "non_burnable_mask.tif",
    )

    # Stage 6 - validate
    layers_to_validate: list[tuple[Path, LayerKey]] = [
        (inputs / "fbfm40.tif", LayerKey.FBFM40),
        (inputs / "dem.tif", LayerKey.DEM),
        (inputs / "slp.tif", LayerKey.SLP),
        (inputs / "asp.tif", LayerKey.ASP),
        (inputs / "cc.tif", LayerKey.CC),
        (inputs / "ch.tif", LayerKey.CH),
        (inputs / "cbh.tif", LayerKey.CBH),
        (inputs / "cbd.tif", LayerKey.CBD),
        (inputs / "protected_mask.tif", LayerKey.PROTECTED_MASK),
        (inputs / "candidate_zone.tif", LayerKey.CANDIDATE_ZONE),
        (inputs / "non_burnable_mask.tif", LayerKey.NON_BURNABLE_MASK),
    ]
    results = [validate_raster(p, grid, lk, layer_kind(lk)) for p, lk in layers_to_validate]
    report = format_report(results, manifest_ok=all(p.exists() for p, _ in layers_to_validate))
    (inputs / "validation_report.txt").write_text(report)

    # Stage 7 — metadata + cleanup (always write metadata, even on validation failure)
    job_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:4]
    layer_sources = {layer.value: source.provenance(layer) for layer in FETCHED_LAYERS}
    validation_status = "passed" if all(r.ok for r in results) else "failed"
    md = build_metadata(
        job_id=job_id, cfg=cfg, grid=grid,
        layer_sources=layer_sources, validation_status=validation_status,
    )
    (inputs / "metadata.json").write_text(json.dumps(md, indent=2))

    if not keep_intermediate:
        shutil.rmtree(intermediate, ignore_errors=True)

    if not all(r.ok for r in results):
        raise RuntimeError("Validation failed:\n" + report)
