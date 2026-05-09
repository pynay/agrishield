"""Generate, score, and export firebreak layout candidates."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import features
from rasterio.transform import xy
from shapely import ops
from shapely.geometry import LineString, MultiPoint, Point, mapping, shape
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.geometry import point_at_bearing
from wildfire_preproc.utils.raster import write_array
from wildfire_preproc.validation.fbfm40_codes import NON_BURNABLE_CODES

DEFAULT_BEARINGS: tuple[int, ...] = (0, 45, 90, 135, 180, 225, 270, 315)
DEFAULT_FIREBREAK_FUEL_CODE = 99


@dataclass(frozen=True)
class FirebreakOptimizationConfig:
    """Configuration for deterministic candidate layout generation and scoring."""

    max_layouts: int = 6
    firebreak_width_m: float = 60.0
    offsets_m: tuple[float, ...] = (250.0, 500.0, 750.0)
    sector_half_angle_deg: float = 32.0
    firebreak_cost_per_m: float = 12.0
    non_burnable_cost_discount: float = 0.25
    firebreak_fuel_code: int = DEFAULT_FIREBREAK_FUEL_CODE
    protected_weight: float = 10_000.0
    failed_scenario_weight: float = 1_000.0
    flame_length_weight: float = 100.0
    cost_weight: float = 10.0
    length_weight: float = 1.0
    risk_reduction_weight: float = 500.0

    def __post_init__(self) -> None:
        if self.max_layouts <= 0:
            raise ValueError("max_layouts must be positive")
        if self.firebreak_width_m <= 0:
            raise ValueError("firebreak_width_m must be positive")
        if not self.offsets_m or any(offset <= 0 for offset in self.offsets_m):
            raise ValueError("offsets_m must contain positive offsets")
        if self.firebreak_fuel_code not in NON_BURNABLE_CODES:
            raise ValueError("firebreak_fuel_code must be a valid non-burnable FBFM40 code")


@dataclass(frozen=True)
class ScenarioSummary:
    """Scenario-level fire outcome metrics from baseline or optimized runs."""

    scenario_id: int
    ignition_direction: str
    ignition_bearing_deg: float
    patch_burned: bool
    burned_area_inside_patch_m2: float
    first_arrival_to_patch_minutes: float | None
    max_flame_length_near_patch_m: float


@dataclass(frozen=True)
class BaselineResult:
    """Aggregated fire outcome metrics."""

    scenarios_failed: int
    burned_area_inside_patch_m2: float
    max_flame_length_near_patch_m: float


@dataclass(frozen=True)
class FirebreakSegment:
    """One exported firebreak segment."""

    segment_id: str
    geometry: LineString
    ui_geometry: LineString
    length_m: float
    estimated_cost: float


@dataclass(frozen=True)
class FirebreakLayout:
    """A candidate firebreak layout and its derived artifacts."""

    layout_id: str
    bearing_deg: float
    offset_m: float
    segments: tuple[FirebreakSegment, ...]
    mask: np.ndarray
    risk_reduction: float
    firebreak_length_m: float
    estimated_cost: float
    score: float


@dataclass(frozen=True)
class OptimizationResult:
    """Ranked firebreak optimization result."""

    recommended_layout_id: str
    layouts: tuple[FirebreakLayout, ...]
    baseline_result: BaselineResult
    output_path: Path

    def to_ui_payload(self) -> dict[str, Any]:
        recommended = self.layouts[0]
        return {
            "recommended_layout_id": self.recommended_layout_id,
            "firebreak_segments": [
                {
                    "segment_id": segment.segment_id,
                    "geometry": [[x, y] for x, y in segment.ui_geometry.coords],
                    "length_m": round(segment.length_m, 3),
                    "estimated_cost": round(segment.estimated_cost, 2),
                }
                for segment in recommended.segments
            ],
            "baseline_result": {
                "scenarios_failed": self.baseline_result.scenarios_failed,
                "burned_area_inside_patch_m2": round(
                    self.baseline_result.burned_area_inside_patch_m2, 3
                ),
            },
            "optimized_result": None,
            "ranked_layouts": [
                {
                    "layout_id": layout.layout_id,
                    "bearing_deg": layout.bearing_deg,
                    "offset_m": layout.offset_m,
                    "score": round(layout.score, 6),
                    "risk_reduction": round(layout.risk_reduction, 6),
                    "firebreak_length_m": round(layout.firebreak_length_m, 3),
                    "estimated_cost": round(layout.estimated_cost, 2),
                    "artifacts": {
                        "firebreak_mask": f"layouts/{layout.layout_id}/firebreak_mask.tif",
                        "modified_fbfm40": f"layouts/{layout.layout_id}/fbfm40.tif",
                        "segments": f"layouts/{layout.layout_id}/segments.geojson",
                    },
                }
                for layout in self.layouts
            ],
        }


def optimize_firebreaks(
    job_dir: Path,
    baseline_dir: Path | None = None,
    out_dir: Path | None = None,
    config: FirebreakOptimizationConfig | None = None,
) -> OptimizationResult:
    """Generate and score firebreak layouts for a preprocessed simulation job.

    `job_dir` may be either the preprocessing directory containing `inputs/`, or
    the `inputs/` directory itself. The function writes ranked layouts, each with
    a firebreak mask, modified `fbfm40.tif`, and `segments.geojson`.
    """
    cfg = config or FirebreakOptimizationConfig()
    inputs_dir = _resolve_inputs_dir(job_dir)
    out_dir = Path(out_dir) if out_dir is not None else inputs_dir.parent / "firebreak_optimization"
    out_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(inputs_dir / "fbfm40.tif") as fbfm_src:
        fbfm40 = fbfm_src.read(1)
        profile = fbfm_src.profile.copy()
        transform = fbfm_src.transform
        crs = fbfm_src.crs
        height = fbfm_src.height
        width = fbfm_src.width
        cell_area_m2 = abs(fbfm_src.transform.a * fbfm_src.transform.e)

    protected_mask = _read_bool_raster(inputs_dir / "protected_mask.tif")
    candidate_zone = _read_bool_raster(inputs_dir / "candidate_zone.tif")
    non_burnable = _read_bool_raster(inputs_dir / "non_burnable_mask.tif")
    protected_geom = _mask_to_geometry(protected_mask, transform)
    candidate_geom = _mask_to_geometry(candidate_zone, transform)

    summaries = load_scenario_summaries(baseline_dir) if baseline_dir is not None else []
    baseline_result = summarize_baseline(summaries, protected_mask, cell_area_m2)
    risk_surface = build_risk_surface(
        shape=(height, width),
        transform=transform,
        protected_geom=protected_geom,
        summaries=summaries,
        candidate_zone=candidate_zone,
    )

    layouts = generate_firebreak_layouts(
        protected_geom=protected_geom,
        candidate_geom=candidate_geom,
        candidate_zone=candidate_zone,
        non_burnable_mask=non_burnable,
        risk_surface=risk_surface,
        transform=transform,
        crs=str(crs),
        config=cfg,
        baseline=baseline_result,
    )
    if not layouts:
        raise RuntimeError("no viable firebreak layouts could be generated")

    ranked = tuple(sorted(layouts, key=lambda item: item.score)[: cfg.max_layouts])
    for layout in ranked:
        layout_dir = out_dir / "layouts" / layout.layout_id
        layout_dir.mkdir(parents=True, exist_ok=True)
        _write_layout_artifacts(
            layout=layout,
            layout_dir=layout_dir,
            fbfm40=fbfm40,
            fbfm_profile=profile,
            firebreak_fuel_code=cfg.firebreak_fuel_code,
            crs=str(crs),
        )

    result = OptimizationResult(
        recommended_layout_id=ranked[0].layout_id,
        layouts=ranked,
        baseline_result=baseline_result,
        output_path=out_dir / "firebreak_optimization.json",
    )
    result.output_path.write_text(json.dumps(result.to_ui_payload(), indent=2))
    return result


def load_scenario_summaries(baseline_dir: Path) -> list[ScenarioSummary]:
    """Load scenario summary JSON files from an ELMFIRE baseline output directory."""
    summaries: list[ScenarioSummary] = []
    for path in sorted(Path(baseline_dir).rglob("summary.json")):
        payload = json.loads(path.read_text())
        summaries.append(_scenario_from_payload(payload, fallback_id=len(summaries) + 1))
    summary_json = Path(baseline_dir) / "simulation_summary.json"
    if summary_json.exists() and not summaries:
        payload = json.loads(summary_json.read_text())
        for idx, run in enumerate(payload.get("runs", []), start=1):
            summaries.append(_scenario_from_payload(run, fallback_id=idx))
    return summaries


def summarize_baseline(
    summaries: Iterable[ScenarioSummary],
    protected_mask: np.ndarray,
    cell_area_m2: float,
) -> BaselineResult:
    """Aggregate scenario summaries, falling back to protected area if summaries are absent."""
    loaded = list(summaries)
    if not loaded:
        return BaselineResult(
            scenarios_failed=0,
            burned_area_inside_patch_m2=0.0,
            max_flame_length_near_patch_m=0.0,
        )
    return BaselineResult(
        scenarios_failed=sum(1 for item in loaded if item.patch_burned),
        burned_area_inside_patch_m2=sum(item.burned_area_inside_patch_m2 for item in loaded),
        max_flame_length_near_patch_m=max(
            (item.max_flame_length_near_patch_m for item in loaded),
            default=float(protected_mask.sum() * cell_area_m2),
        ),
    )


def build_risk_surface(
    shape: tuple[int, int],
    transform: rasterio.Affine,
    protected_geom: BaseGeometry,
    summaries: Iterable[ScenarioSummary],
    candidate_zone: np.ndarray,
) -> np.ndarray:
    """Build a normalized raster risk surface from scenario bearings and outcomes."""
    risk = np.zeros(shape, dtype="float32")
    center = protected_geom.centroid
    cell_bearing = _cell_bearings(shape, transform, center)

    loaded = list(summaries)
    if not loaded:
        risk = np.where(candidate_zone, 1.0, 0.0).astype("float32")
        return risk

    for summary in loaded:
        outcome_weight = 1.0
        if summary.patch_burned:
            outcome_weight += 4.0
        outcome_weight += min(summary.burned_area_inside_patch_m2 / 10_000.0, 10.0)
        angular_delta = _angular_delta(cell_bearing, summary.ignition_bearing_deg)
        directional = np.clip(1.0 - (angular_delta / 75.0), 0.0, 1.0)
        risk += (directional * outcome_weight).astype("float32")

    risk = np.where(candidate_zone, risk, 0.0)
    max_value = float(risk.max())
    if max_value > 0:
        risk /= max_value
    return risk.astype("float32")


def generate_firebreak_layouts(
    protected_geom: BaseGeometry,
    candidate_geom: BaseGeometry,
    candidate_zone: np.ndarray,
    non_burnable_mask: np.ndarray,
    risk_surface: np.ndarray,
    transform: rasterio.Affine,
    crs: str,
    config: FirebreakOptimizationConfig,
    baseline: BaselineResult,
) -> tuple[FirebreakLayout, ...]:
    """Generate deterministic arc and directional firebreak candidates."""
    center = protected_geom.centroid
    bearings = _ranked_bearings_from_risk(risk_surface, transform, center)
    candidates: list[FirebreakLayout] = []
    for bearing in bearings:
        for offset in config.offsets_m:
            layout_id = f"layout_{len(candidates) + 1:02d}"
            segment_id = f"bearing_{round(bearing):03d}_arc_{round(offset)}m"
            arc = _arc_segment(
                center=center,
                radius=_distance_to_boundary(protected_geom, center, bearing) + offset,
                bearing_deg=bearing,
                half_angle_deg=config.sector_half_angle_deg,
            )
            if arc.is_empty or arc.length == 0:
                continue
            buffered = arc.buffer(config.firebreak_width_m / 2.0, cap_style="flat")
            clipped = buffered.intersection(candidate_geom)
            if clipped.is_empty:
                continue
            mask = _rasterize_geometry(clipped, candidate_zone.shape, transform)
            mask = mask & candidate_zone
            if not mask.any():
                continue
            effective_line = arc.intersection(candidate_geom.buffer(config.firebreak_width_m / 2.0))
            if effective_line.is_empty:
                effective_line = arc
            length_m = float(effective_line.length)
            if length_m <= 0:
                continue
            non_burnable_overlap = _mask_overlap_fraction(mask, non_burnable_mask)
            cost = length_m * config.firebreak_cost_per_m * (
                1.0 - non_burnable_overlap * (1.0 - config.non_burnable_cost_discount)
            )
            risk_reduction = float(risk_surface[mask].sum())
            score = _score_layout(
                baseline=baseline,
                risk_reduction=risk_reduction,
                firebreak_length_m=length_m,
                estimated_cost=cost,
                config=config,
            )
            segment = FirebreakSegment(
                segment_id=segment_id,
                geometry=_line_to_crs_line(effective_line, crs),
                ui_geometry=_line_to_wgs84(_line_to_crs_line(effective_line, crs), crs),
                length_m=length_m,
                estimated_cost=cost,
            )
            candidates.append(
                FirebreakLayout(
                    layout_id=layout_id,
                    bearing_deg=float(bearing),
                    offset_m=float(offset),
                    segments=(segment,),
                    mask=mask,
                    risk_reduction=risk_reduction,
                    firebreak_length_m=length_m,
                    estimated_cost=cost,
                    score=score,
                )
            )
    return tuple(candidates)


def _resolve_inputs_dir(job_dir: Path) -> Path:
    path = Path(job_dir)
    if (path / "fbfm40.tif").exists():
        return path
    inputs = path / "inputs"
    if inputs.exists():
        return inputs
    raise FileNotFoundError(f"could not find inputs directory under {path}")


def _read_bool_raster(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = cast(np.ndarray, src.read(1))
    return cast(np.ndarray, arr == 1)


def _mask_to_geometry(mask: np.ndarray, transform: rasterio.Affine) -> BaseGeometry:
    geoms = [
        shape(geom)
        for geom, value in features.shapes(mask.astype("uint8"), mask=mask, transform=transform)
        if int(value) == 1
    ]
    if not geoms:
        raise ValueError("mask does not contain any true pixels")
    return ops.unary_union(geoms)


def _scenario_from_payload(payload: dict[str, Any], fallback_id: int) -> ScenarioSummary:
    scenario_id = int(payload.get("scenario_id") or fallback_id)
    bearing = _bearing_from_payload(payload, scenario_id)
    burned_area = float(payload.get("burned_area_inside_patch_m2", 0.0) or 0.0)
    patch_burned = bool(payload.get("patch_burned", burned_area > 0.0))
    return ScenarioSummary(
        scenario_id=scenario_id,
        ignition_direction=str(payload.get("ignition_direction", _direction_name(bearing))),
        ignition_bearing_deg=bearing,
        patch_burned=patch_burned,
        burned_area_inside_patch_m2=burned_area,
        first_arrival_to_patch_minutes=_optional_float(
            payload.get("first_arrival_to_patch_minutes")
        ),
        max_flame_length_near_patch_m=float(
            payload.get("max_flame_length_near_patch_m", 0.0) or 0.0
        ),
    )


def _bearing_from_payload(payload: dict[str, Any], scenario_id: int) -> float:
    raw = payload.get("ignition_bearing_deg")
    if raw is not None:
        return float(raw) % 360.0
    direction = str(payload.get("ignition_direction", "")).lower()
    direction_bearings = {
        "north": 0.0,
        "northeast": 45.0,
        "east": 90.0,
        "southeast": 135.0,
        "south": 180.0,
        "southwest": 225.0,
        "west": 270.0,
        "northwest": 315.0,
    }
    if direction in direction_bearings:
        return direction_bearings[direction]
    if isinstance(payload.get("run_id"), str) and "bearing_" in payload["run_id"]:
        return float(payload["run_id"].rsplit("_", 1)[-1]) % 360.0
    return float(DEFAULT_BEARINGS[(scenario_id - 1) % len(DEFAULT_BEARINGS)])


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _direction_name(bearing: float) -> str:
    names = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")
    idx = round((bearing % 360.0) / 45.0) % len(names)
    return names[idx]


def _angular_delta(a: np.ndarray, b: float) -> np.ndarray:
    return np.abs(((a - b + 180.0) % 360.0) - 180.0)


def _ranked_bearings_from_risk(
    risk_surface: np.ndarray,
    transform: rasterio.Affine,
    center: Point,
) -> tuple[float, ...]:
    cell_bearing = _cell_bearings(risk_surface.shape, transform, center)
    scores = []
    for bearing in DEFAULT_BEARINGS:
        weights = np.clip(1.0 - (_angular_delta(cell_bearing, float(bearing)) / 67.5), 0.0, 1.0)
        scores.append((float((risk_surface * weights).sum()), float(bearing)))
    return tuple(bearing for _, bearing in sorted(scores, reverse=True))


def _cell_bearings(
    shape: tuple[int, int],
    transform: rasterio.Affine,
    center: Point,
) -> np.ndarray:
    ys, xs = np.indices(shape)
    cell_x, cell_y = xy(transform, ys, xs)
    x_arr = np.asarray(cell_x, dtype="float32").reshape(shape)
    y_arr = np.asarray(cell_y, dtype="float32").reshape(shape)
    dx = x_arr - float(center.x)
    dy = y_arr - float(center.y)
    return (90.0 - np.degrees(np.arctan2(dy, dx))) % 360.0


def _grid_diagonal_m(transform: rasterio.Affine, shape: tuple[int, int]) -> float:
    height, width = shape
    return math.hypot(abs(transform.a) * width, abs(transform.e) * height)


def _distance_to_boundary(geom: BaseGeometry, center: Point, bearing: float) -> float:
    far = point_at_bearing(center, bearing, 1_000_000.0)
    ray = LineString([center, far])
    intersection = geom.boundary.intersection(ray)
    points = _extract_points(intersection)
    if not points:
        return 0.0
    return min(center.distance(point) for point in points)


def _extract_points(geom: BaseGeometry) -> list[Point]:
    if geom.is_empty:
        return []
    if isinstance(geom, Point):
        return [geom]
    if isinstance(geom, MultiPoint):
        return [Point(item.x, item.y) for item in geom.geoms]
    if isinstance(geom, LineString):
        return [Point(coord) for coord in geom.coords]
    if isinstance(geom, BaseMultipartGeometry):
        points: list[Point] = []
        for item in geom.geoms:
            points.extend(_extract_points(item))
        return points
    return []


def _arc_segment(
    center: Point,
    radius: float,
    bearing_deg: float,
    half_angle_deg: float,
    vertices: int = 24,
) -> LineString:
    start = bearing_deg - half_angle_deg
    stop = bearing_deg + half_angle_deg
    coords = [
        point_at_bearing(center, start + (stop - start) * i / vertices, radius).coords[0]
        for i in range(vertices + 1)
    ]
    return LineString(coords)


def _rasterize_geometry(
    geom: BaseGeometry,
    shape: tuple[int, int],
    transform: rasterio.Affine,
) -> np.ndarray:
    arr = cast(np.ndarray, features.rasterize(
        [(mapping(geom), 1)],
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    ))
    return arr.astype(bool)


def _mask_overlap_fraction(mask: np.ndarray, other: np.ndarray) -> float:
    count = int(mask.sum())
    if count == 0:
        return 0.0
    return float((mask & other).sum() / count)


def _score_layout(
    baseline: BaselineResult,
    risk_reduction: float,
    firebreak_length_m: float,
    estimated_cost: float,
    config: FirebreakOptimizationConfig,
) -> float:
    return (
        config.protected_weight * baseline.burned_area_inside_patch_m2
        + config.failed_scenario_weight * baseline.scenarios_failed
        + config.flame_length_weight * baseline.max_flame_length_near_patch_m
        + config.cost_weight * estimated_cost
        + config.length_weight * firebreak_length_m
        - config.risk_reduction_weight * risk_reduction
    )


def _line_to_crs_line(geom: BaseGeometry, crs: str) -> LineString:
    del crs
    if isinstance(geom, LineString):
        return geom
    if hasattr(geom, "geoms"):
        lines = [item for item in geom.geoms if isinstance(item, LineString) and item.length > 0]
        if lines:
            return max(lines, key=lambda item: item.length)
    raise ValueError("firebreak geometry did not produce a line segment")


def _line_to_wgs84(line: LineString, src_crs: str) -> LineString:
    if src_crs.upper() in {"EPSG:4326", "OGC:CRS84"}:
        return line
    transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    transformed = ops.transform(transformer.transform, line)
    if not isinstance(transformed, LineString):
        raise ValueError("transformed firebreak geometry is not a line")
    return transformed


def _write_layout_artifacts(
    layout: FirebreakLayout,
    layout_dir: Path,
    fbfm40: np.ndarray,
    fbfm_profile: dict[str, Any],
    firebreak_fuel_code: int,
    crs: str,
) -> None:
    modified = np.where(layout.mask, firebreak_fuel_code, fbfm40).astype(fbfm40.dtype)
    profile = fbfm_profile.copy()
    profile.update(compress="LZW")
    with rasterio.open(layout_dir / "fbfm40.tif", "w", **profile) as dst:
        dst.write(modified, 1)

    grid = _grid_from_profile(profile)
    write_array(
        layout_dir / "firebreak_mask.tif",
        layout.mask.astype("uint8"),
        grid,
        LayerKind.MASK,
    )

    gdf = gpd.GeoDataFrame(
        [
            {
                "segment_id": segment.segment_id,
                "length_m": segment.length_m,
                "estimated_cost": segment.estimated_cost,
                "geometry": segment.geometry,
            }
            for segment in layout.segments
        ],
        crs=crs,
    )
    gdf.to_file(layout_dir / "segments.geojson", driver="GeoJSON")


def _grid_from_profile(profile: dict[str, Any]) -> Any:
    from wildfire_preproc.align.grid import GridSpec

    return GridSpec(
        crs=profile["crs"],
        transform=profile["transform"],
        width=int(profile["width"]),
        height=int(profile["height"]),
        cell_size=float(abs(profile["transform"].a)),
    )
