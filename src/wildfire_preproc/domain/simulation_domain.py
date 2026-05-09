"""Stage 1 — build simulation domain geometry from a protected polygon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from wildfire_preproc.utils.geometry import nearest_point_on, point_at_bearing

COMPASS_BEARINGS: tuple[int, ...] = (0, 45, 90, 135, 180, 225, 270, 315)


@dataclass(frozen=True)
class DomainArtifacts:
    simulation_domain_path: Path
    ignition_ring_path: Path
    ignition_points_path: Path  # the deliverable
    candidate_zone_polygon_path: Path
    simulation_polygon: BaseGeometry
    protected_polygon_projected: BaseGeometry


def build_domain(
    protected_polygon: BaseGeometry,
    protected_polygon_crs: str,
    target_crs: str,
    simulation_radius_m: float,
    ignition_distance_m: float,
    safety_buffer_m: float,
    out_dir: Path,
) -> DomainArtifacts:
    """Generate simulation domain, ignition ring/points, candidate zone polygon.

    All geometry operations occur in `target_crs` (a projected CRS in meters).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Reproject the protected polygon into the target CRS.
    gdf_in = gpd.GeoDataFrame(geometry=[protected_polygon], crs=protected_polygon_crs)
    gdf_proj = gdf_in.to_crs(target_crs)
    poly_proj: Polygon = gdf_proj.geometry.iloc[0]

    # 2. Buffers in projected meters.
    sim_polygon = poly_proj.buffer(simulation_radius_m)
    ignition_polygon = poly_proj.buffer(ignition_distance_m)
    ignition_ring = ignition_polygon.boundary
    candidate_zone = ignition_polygon.difference(poly_proj.buffer(safety_buffer_m))

    # 3. Eight ignition points: bearing-from-centroid candidates snapped to ring.
    centroid = poly_proj.centroid
    ignition_points = []
    for bearing in COMPASS_BEARINGS:
        candidate = point_at_bearing(
            centroid, bearing_deg=bearing, distance=ignition_distance_m * 2
        )
        snapped = nearest_point_on(ignition_ring, candidate)
        ignition_points.append({"geometry": snapped, "bearing_deg": bearing})

    # 4. Write artifacts.
    sim_path = out_dir / "simulation_domain.geojson"
    ring_path = out_dir / "ignition_ring.geojson"
    pts_path = out_dir / "ignition_points.geojson"
    candidate_path = out_dir / "candidate_zone_polygon.geojson"

    gpd.GeoDataFrame(geometry=[sim_polygon], crs=target_crs).to_file(
        sim_path, driver="GeoJSON"
    )
    gpd.GeoDataFrame(geometry=[ignition_ring], crs=target_crs).to_file(
        ring_path, driver="GeoJSON"
    )
    gpd.GeoDataFrame(ignition_points, crs=target_crs).to_file(pts_path, driver="GeoJSON")
    gpd.GeoDataFrame(geometry=[candidate_zone], crs=target_crs).to_file(
        candidate_path, driver="GeoJSON"
    )

    return DomainArtifacts(
        simulation_domain_path=sim_path,
        ignition_ring_path=ring_path,
        ignition_points_path=pts_path,
        candidate_zone_polygon_path=candidate_path,
        simulation_polygon=sim_polygon,
        protected_polygon_projected=poly_proj,
    )
