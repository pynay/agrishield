"""Tests for `_refresh_polygon_dependent_inputs` reuse vs. fallback paths.

These cover the bug where the UI prefetched a fixed 250 m square and then
silently reused that grid for arbitrary user polygons without checking
whether the simulation domain actually fit.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon, mapping

from main import (
    PreprocessedReuseMismatch,
    _refresh_polygon_dependent_inputs,
)
from wildfire_preproc.config import JobConfig


def _write_uint8(path: Path, data: np.ndarray, transform, crs, nodata=None) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="uint8",
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as ds:
        ds.write(data, 1)


def _build_cached_inputs(
    tmp_path: Path,
    *,
    crs: str = "EPSG:5070",
    cell_size: float = 30.0,
    grid_width: int = 1200,
    grid_height: int = 1200,
    origin_x: float = -100_000.0,
    origin_y: float = 1_000_000.0,
) -> Path:
    """Create a fake preprocessed directory with metadata + fbfm40.tif covering
    a 30 km x 30 km area at 30 m resolution centered around (-100km, 1000km)."""
    inputs = tmp_path / "preprocessed" / "inputs"
    inputs.mkdir(parents=True)
    transform = from_origin(origin_x, origin_y, cell_size, cell_size)
    crs_obj = CRS.from_string(crs)
    # FBFM40 raster covering the full grid (so non_burnable_mask can be rebuilt).
    fbfm40 = np.full((grid_height, grid_width), 101, dtype="uint8")  # GR1
    _write_uint8(inputs / "fbfm40.tif", fbfm40, transform, crs_obj, nodata=255)

    metadata = {
        "grid": {
            "crs": crs,
            "transform": [
                transform.a,
                transform.b,
                transform.c,
                transform.d,
                transform.e,
                transform.f,
            ],
            "width": grid_width,
            "height": grid_height,
        },
        "config": {"cell_size_m": cell_size},
    }
    (inputs / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return inputs.parent


def _make_cfg(polygon: Polygon, *, simulation_radius_m: float = 3000.0) -> JobConfig:
    return JobConfig.model_validate(
        {
            "protected_polygon": mapping(polygon),
            "simulation_radius_m": simulation_radius_m,
            "ignition_distance_m": simulation_radius_m * 0.9,
            "cell_size_m": 30.0,
            "crs": "EPSG:5070",
        }
    )


def test_reuse_succeeds_when_domain_fits(tmp_path: Path) -> None:
    """Polygon centered well inside the cached grid -> reuse succeeds."""
    preprocessed = _build_cached_inputs(tmp_path)
    # Polygon at the center of the cached grid (-85_000, 985_000) — sim buffer 3km fits.
    polygon = Polygon(
        [
            (-85_500, 984_500),
            (-85_500, 985_500),
            (-84_500, 985_500),
            (-84_500, 984_500),
            (-85_500, 984_500),
        ]
    )
    cfg = _make_cfg(polygon)
    _refresh_polygon_dependent_inputs(
        cfg=cfg, preprocessed_dir=preprocessed, protected_polygon_crs="EPSG:5070"
    )
    inputs = preprocessed / "inputs"
    assert (inputs / "protected_mask.tif").exists()
    assert (inputs / "candidate_zone.tif").exists()
    assert (inputs / "non_burnable_mask.tif").exists()
    assert (inputs / "ignition_points.geojson").exists()


def test_reuse_rejected_when_polygon_outside_grid(tmp_path: Path) -> None:
    """Polygon outside the cached grid -> raises PreprocessedReuseMismatch."""
    preprocessed = _build_cached_inputs(tmp_path)
    # Polygon far outside the cached grid bounds (which span -100k..-64k in x).
    polygon = Polygon(
        [
            (200_000, 985_000),
            (200_000, 986_000),
            (201_000, 986_000),
            (201_000, 985_000),
            (200_000, 985_000),
        ]
    )
    cfg = _make_cfg(polygon)
    with pytest.raises(PreprocessedReuseMismatch, match="simulation domain bounds"):
        _refresh_polygon_dependent_inputs(
            cfg=cfg, preprocessed_dir=preprocessed, protected_polygon_crs="EPSG:5070"
        )


def test_reuse_rejected_when_simulation_radius_overflows_grid(tmp_path: Path) -> None:
    """Polygon center is inside the grid but the buffer overflows the edge."""
    preprocessed = _build_cached_inputs(tmp_path)
    # Polygon near the right edge of the grid; with simulation_radius=5000m it overflows.
    polygon = Polygon(
        [
            (-66_000, 985_000),
            (-66_000, 985_500),
            (-65_500, 985_500),
            (-65_500, 985_000),
            (-66_000, 985_000),
        ]
    )
    cfg = _make_cfg(polygon, simulation_radius_m=5000.0)
    with pytest.raises(PreprocessedReuseMismatch, match="not contained"):
        _refresh_polygon_dependent_inputs(
            cfg=cfg, preprocessed_dir=preprocessed, protected_polygon_crs="EPSG:5070"
        )


def test_reuse_rejected_on_crs_mismatch(tmp_path: Path) -> None:
    """If the cached grid CRS differs from cfg.crs, reuse must be rejected."""
    preprocessed = _build_cached_inputs(tmp_path, crs="EPSG:5070")
    polygon = Polygon(
        [
            (-85_500, 984_500),
            (-85_500, 985_500),
            (-84_500, 985_500),
            (-84_500, 984_500),
            (-85_500, 984_500),
        ]
    )
    cfg = JobConfig.model_validate(
        {
            "protected_polygon": mapping(polygon),
            "simulation_radius_m": 3000.0,
            "ignition_distance_m": 2500.0,
            "cell_size_m": 30.0,
            "crs": "EPSG:3857",  # different CRS
        }
    )
    with pytest.raises(PreprocessedReuseMismatch, match="CRS"):
        _refresh_polygon_dependent_inputs(
            cfg=cfg, preprocessed_dir=preprocessed, protected_polygon_crs="EPSG:3857"
        )


def test_reuse_rejected_on_cell_size_mismatch(tmp_path: Path) -> None:
    preprocessed = _build_cached_inputs(tmp_path, cell_size=30.0)
    polygon = Polygon(
        [
            (-85_500, 984_500),
            (-85_500, 985_500),
            (-84_500, 985_500),
            (-84_500, 984_500),
            (-85_500, 984_500),
        ]
    )
    cfg = JobConfig.model_validate(
        {
            "protected_polygon": mapping(polygon),
            "simulation_radius_m": 3000.0,
            "ignition_distance_m": 2500.0,
            "cell_size_m": 10.0,  # different cell size
            "crs": "EPSG:5070",
        }
    )
    with pytest.raises(PreprocessedReuseMismatch, match="cell size"):
        _refresh_polygon_dependent_inputs(
            cfg=cfg, preprocessed_dir=preprocessed, protected_polygon_crs="EPSG:5070"
        )
