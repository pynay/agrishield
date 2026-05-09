from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

from wildfire_preproc.domain.simulation_domain import build_domain


def _square_polygon_meters(side: float = 200.0) -> Polygon:
    """A 200m square centered at (0, 0) in a projected CRS."""
    h = side / 2
    return Polygon([(-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)])


def test_build_domain_creates_8_ignition_points(tmp_path: Path):
    poly = _square_polygon_meters()
    art = build_domain(
        protected_polygon=poly,
        protected_polygon_crs="EPSG:5070",
        target_crs="EPSG:5070",
        simulation_radius_m=5000.0,
        ignition_distance_m=4500.0,
        safety_buffer_m=100.0,
        out_dir=tmp_path,
    )
    pts = gpd.read_file(art.ignition_points_path)
    assert len(pts) == 8
    bearings = sorted(pts["bearing_deg"].tolist())
    assert bearings == [0, 45, 90, 135, 180, 225, 270, 315]


def test_build_domain_simulation_polygon_contains_protected(tmp_path: Path):
    poly = _square_polygon_meters()
    art = build_domain(
        protected_polygon=poly,
        protected_polygon_crs="EPSG:5070",
        target_crs="EPSG:5070",
        simulation_radius_m=5000.0,
        ignition_distance_m=4500.0,
        safety_buffer_m=100.0,
        out_dir=tmp_path,
    )
    sim = gpd.read_file(art.simulation_domain_path).geometry.iloc[0]
    assert sim.contains(poly)


def test_build_domain_candidate_zone_excludes_safety_buffer(tmp_path: Path):
    poly = _square_polygon_meters()
    art = build_domain(
        protected_polygon=poly,
        protected_polygon_crs="EPSG:5070",
        target_crs="EPSG:5070",
        simulation_radius_m=5000.0,
        ignition_distance_m=4500.0,
        safety_buffer_m=100.0,
        out_dir=tmp_path,
    )
    candidate = gpd.read_file(art.candidate_zone_polygon_path).geometry.iloc[0]
    safety = poly.buffer(100.0)
    # candidate is outside the buffered protected area
    assert not candidate.intersects(safety.buffer(-1.0))


def test_build_domain_writes_all_artifacts(tmp_path: Path):
    poly = _square_polygon_meters()
    art = build_domain(
        protected_polygon=poly,
        protected_polygon_crs="EPSG:5070",
        target_crs="EPSG:5070",
        simulation_radius_m=5000.0,
        ignition_distance_m=4500.0,
        safety_buffer_m=100.0,
        out_dir=tmp_path,
    )
    assert art.simulation_domain_path.exists()
    assert art.ignition_ring_path.exists()
    assert art.ignition_points_path.exists()
    assert art.candidate_zone_polygon_path.exists()


def test_build_domain_reprojects_input_when_crs_differs(tmp_path: Path):
    # Polygon defined in EPSG:4326 (lon/lat) — should be reprojected to EPSG:5070.
    poly_wgs84 = Polygon([
        (-118.7, 34.1), (-118.6, 34.1), (-118.6, 34.2), (-118.7, 34.2), (-118.7, 34.1)
    ])
    art = build_domain(
        protected_polygon=poly_wgs84,
        protected_polygon_crs="EPSG:4326",
        target_crs="EPSG:5070",
        simulation_radius_m=5000.0,
        ignition_distance_m=4500.0,
        safety_buffer_m=100.0,
        out_dir=tmp_path,
    )
    sim = gpd.read_file(art.simulation_domain_path)
    assert sim.crs is not None and sim.crs.to_epsg() == 5070
