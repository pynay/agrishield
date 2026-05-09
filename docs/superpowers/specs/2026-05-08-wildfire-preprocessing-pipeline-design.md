# Wildfire Preprocessing Pipeline — Design

**Date:** 2026-05-08
**Status:** Approved (pending final user review)
**Owner:** agrishield

## 1. Goal

Given a protected-land polygon and a small JSON config, produce a directory of aligned, simulation-ready GeoTIFF rasters and metadata directly usable by ELMFIRE.

## 2. Inputs and Outputs

### Input (JSON payload)

```json
{
  "protected_polygon": { "type": "Polygon", "coordinates": [...] },
  "simulation_radius_m": 5000,
  "ignition_distance_m": 4500,
  "cell_size_m": 30,
  "crs": "EPSG:5070"
}
```

Optional fields with defaults:

- `safety_buffer_m` — `100`
- `non_burnable_sources` — `["fbfm40"]`
- `landfire_version` — `"LF2022"`
- `cache_dir` — `~/.cache/wildfire-preproc`

### Output directory

```
job_<id>/
  inputs/
    fbfm40.tif
    dem.tif
    slp.tif
    asp.tif
    cc.tif
    ch.tif
    cbh.tif
    cbd.tif
    protected_mask.tif
    candidate_zone.tif
    non_burnable_mask.tif
    ignition_points.geojson
    metadata.json
    validation_report.txt
```

## 3. Architecture

A synchronous, file-driven pipeline. Each stage is a function that reads from the `Job` directory and writes back to it. No DAG framework, no class-per-stage hierarchy. State is the files on disk plus a small in-memory `Job` (paths, config, frozen `GridSpec` after Stage 3).

### Module layout

```
agrishield/
  pyproject.toml
  README.md
  data/sample/santa_monica_demo.geojson
  src/wildfire_preproc/
    __init__.py
    cli.py                        # `wildfire-preproc run <job.json>`
    config.py                     # JobConfig (pydantic)
    pipeline.py                   # orchestrates Stages 1–7
    domain/                       # Stage 1
      simulation_domain.py
    sources/                      # Stage 2
      base.py                     # RasterSource protocol, LayerKey enum
      registry.py
      local.py
      lfps.py
      threedep.py
      cache.py
    align/                        # Stage 3
      grid.py                     # GridSpec
      align.py                    # reproject_match()
    terrain/                      # Stage 4
      derive.py                   # slope, aspect (Horn 3x3)
    masks/                        # Stage 5
      protected.py
      candidate.py
      non_burnable.py
    validation/                   # Stage 6
      checks.py
      report.py
      fbfm40_codes.py
    export/                       # Stage 7
      writer.py                   # COG writer
      metadata.py
    utils/
      logging.py
      geometry.py
      raster.py
  tests/
    test_align.py
    test_domain.py
    test_terrain.py
    test_masks.py
    test_validation.py
    test_pipeline_e2e.py          # @pytest.mark.live
  scripts/
    run_sample.sh
```

### Key invariants enforced by the architecture

1. After Stage 3, a single `GridSpec` (CRS, transform, width, height, nodata strategy) is frozen on the `Job`. Every subsequent raster is reprojected/snapped to it. There is no path through the pipeline that produces a raster *not* matched to that GridSpec — that is what prevents ELMFIRE alignment failures.
2. `sources/base.py` defines `RasterSource` with `fetch(layer: LayerKey, bbox, dst_crs) -> Path`. `local`, `lfps`, `threedep` all implement it. The rest of the pipeline never branches on which one is in use.
3. `non_burnable_mask` is built by `union_of(sources)` where each source is a binary-mask producer. FBFM40 reclass is the default; OSM/shapefile producers can be added later without touching the rest of the pipeline.

## 4. Data Flow

```
JSON payload
  ──▶ Stage 1: domain
        writes _intermediate/simulation_domain.geojson
               _intermediate/ignition_ring.geojson
               _intermediate/candidate_zone_polygon.geojson
               inputs/ignition_points.geojson         (final deliverable)

  ──▶ Stage 2: acquire
        for layer in {fbfm40, cc, ch, cbh, cbd, dem}:
          source.fetch(layer, bbox=domain.bounds, dst_crs=cfg.crs)
        writes raw tiles to _intermediate/<layer>_raw.tif (un-aligned)

  ──▶ Stage 3: align
        build GridSpec from cfg.crs, cfg.cell_size_m, domain.bounds
          (snap bounds outward to global cell grid)
        for each raw raster, reproject_match → inputs/<layer>.tif
          (nearest for fbfm40; bilinear otherwise)
        Job.grid_spec is set; never changes after this

  ──▶ Stage 4: terrain
        slope, aspect from inputs/dem.tif
        writes inputs/slp.tif, inputs/asp.tif

  ──▶ Stage 5: masks
        protected_mask.tif    = rasterize(protected_polygon, GridSpec)
        candidate_zone.tif    = rasterize(candidate_zone_polygon, GridSpec)
        non_burnable_mask.tif = union of configured sources
                                (default: fbfm40 in {91,92,93,98,99})

  ──▶ Stage 6: validate
        per-raster: CRS, transform, width, height, nodata, no all-nodata
        cross-raster: full manifest present, protected_mask intersects polygon
        raises ValidationError on first failure; writes validation_report.txt

  ──▶ Stage 7: export
        rewrite all .tif as deterministic COGs (tiled, LZW, predictor=2)
        writes inputs/metadata.json
        deletes _intermediate/ unless --keep-intermediate
```

### Contract details

- **Domain bounds → grid:** Stage 3 takes `simulation_domain.bounds`, snaps outward to a multiple of `cell_size_m` *anchored at the target CRS origin (0, 0)*. Two jobs in the same CRS that overlap therefore produce pixel-identical extents in the overlap region.
- **Nodata policy:** continuous → `-9999` (float32); categorical (fbfm40) → `255` (uint8); masks → no nodata, `uint8` 0/1 only. Enforced in `export/writer.py`; stages do not write GeoTIFFs by hand.
- **Ignition output:** GeoJSON only; no ignition raster generated.
- **Intermediate `_intermediate/`:** holds raw fetched tiles and the three Stage 1 vector files that aren't part of the deliverable (`simulation_domain.geojson`, `ignition_ring.geojson`, `candidate_zone_polygon.geojson`). Deleted by default; preserved with `--keep-intermediate`. `inputs/ignition_points.geojson` is the only Stage 1 vector kept by default — it's the deliverable.

## 5. The Hard Parts

### 5.1 Canonical GridSpec (alignment)

```python
@dataclass(frozen=True)
class GridSpec:
    crs: CRS
    transform: Affine
    width: int
    height: int
    cell_size: float
```

Construction:

1. Reproject `simulation_domain` to target CRS.
2. Get bounds `(minx, miny, maxx, maxy)`.
3. Snap outward to a global origin grid: `minx_snapped = floor(minx / cell) * cell`, etc., anchored at CRS origin `(0, 0)`.
4. `width = (maxx_snapped - minx_snapped) / cell`; `height = (maxy_snapped - miny_snapped) / cell`.
5. `transform = from_origin(minx_snapped, maxy_snapped, cell, cell)`.

`reproject_match(src, grid_spec, resampling)` is the only function that produces an aligned raster. Every output of stages 3–7 either flows through it or is rasterized directly into `grid_spec`'s frame.

Resampling rules (driven by a `LayerKind` enum):

| Layer | Kind | Resampling |
|---|---|---|
| `fbfm40` | categorical | `nearest` |
| `cc`, `ch`, `cbh`, `cbd` | continuous | `bilinear` |
| `dem` | continuous | `bilinear` |
| `slp`, `asp` | derived | computed after DEM is on grid |
| `protected_mask`, `candidate_zone`, `non_burnable_mask` | mask | rasterized directly to grid |

Slope and aspect are computed *after* the DEM is on the canonical grid, never before — reprojection-induced smoothing distorts the derivatives.

### 5.2 Slope and aspect (Horn 1981, 3×3)

```
For each cell, with 3x3 z window:
  dz/dx = ((z[0,2] + 2*z[1,2] + z[2,2]) - (z[0,0] + 2*z[1,0] + z[2,0])) / (8 * cell_size)
  dz/dy = ((z[2,0] + 2*z[2,1] + z[2,2]) - (z[0,0] + 2*z[0,1] + z[0,2])) / (8 * cell_size)
  slope_rad  = atan(sqrt(dz/dx**2 + dz/dy**2))
  aspect_rad = atan2(dz/dy, -dz/dx)
```

Outputs:

- `slp.tif` — float32, **degrees** (0–90), nodata `-9999`.
- `asp.tif` — float32, **degrees clockwise from north** (0–360), flat cells `-1`, nodata `-9999`.

Implemented with `scipy.ndimage.convolve` over the two Sobel-weighted kernels — vectorized, no Python-level loops.

Edges: outermost ring of pixels set to nodata. At 30 m on a 5 km radius this loses ~330 cells out of ~110 000.

### 5.3 LFPS and 3DEP fetchers

**LFPS (LANDFIRE Product Service)** — open ArcGIS REST endpoint at `https://lfps.usgs.gov/api/job/`.

1. POST job request with layers + bbox + output CRS → returns `jobId`.
2. Poll `/job/<jobId>` until `Status == "Succeeded"` (fail on `"Failed"` or timeout).
3. GET output zip URL, extract, return path to GeoTIFF.

LFPS layer codes (LF2022) live as a constant map `LayerKey → LFPS layer name` in `sources/lfps.py`. Codes will be verified against the live LFPS product table during implementation.

Retry policy via `tenacity`: exponential backoff `1s → 60s` cap, max 6 attempts, retry on `HTTPError(5xx)` and connection errors. Do not retry on 4xx.

Cache: `~/.cache/wildfire-preproc/lfps/<sha256(bbox+layer+crs)>.tif`. Same parameters → no-op after first run.

**3DEP DEM** — USGS dynamic clip endpoint at `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage`. Same retry and cache policy as LFPS. 3DEP returns ~10 m by default; resampled to 30 m via `bilinear` in Stage 3.

### 5.4 Stage 1 geometry (polygon-boundary based)

```python
domain_polygon       = protected_polygon.buffer(simulation_radius_m)  # default 5000
ignition_polygon     = protected_polygon.buffer(ignition_distance_m)  # default 4500
ignition_ring        = ignition_polygon.boundary
candidate_zone_poly  = ignition_polygon.difference(
                          protected_polygon.buffer(safety_buffer_m)   # default 100
                       )

# 8 ignition points: bearings from polygon centroid, snapped to ignition_ring.
centroid = protected_polygon.centroid
ignition_points = [
    nearest_point_on(
        ignition_ring,
        centroid_plus_bearing(centroid, bearing, ignition_distance_m)
    )
    for bearing in [0, 45, 90, 135, 180, 225, 270, 315]   # N, NE, E, SE, S, SW, W, NW
]
```

Correctness details:

1. All buffers happen in the projected target CRS (EPSG:5070), not WGS84. The pipeline reprojects input GeoJSON to target CRS *before* any geometry op.
2. Ignition points are placed *on* the ring, not at distance-from-centroid. For a non-convex protected polygon the candidate point at `centroid + bearing*distance` could land inside the polygon; snapping to the nearest point on the ring avoids that.

## 6. Validation (Stage 6)

Per-raster:

1. Exists and opens (`rasterio.open()`).
2. CRS identical to `Job.grid_spec.crs`.
3. Transform identical (component-wise, ε=1e-9).
4. Width × height identical.
5. Cell size identical in both axes.
6. Nodata is what was declared for that layer's kind.
7. Not all-nodata.
8. Masks: values exclusively `{0, 1}`.
9. `fbfm40`: values within published FBFM40 code set (catalogued in `validation/fbfm40_codes.py`).
10. DEM: finite range, sanity bounds (`-500 m ≤ z ≤ 9000 m`).

Cross-raster:

- Full manifest present.
- `protected_mask` overlaps protected polygon's footprint (catches CRS bugs in rasterizer).

Report is human-readable, also persisted to `inputs/validation_report.txt`.

## 7. CLI

```
wildfire-preproc run <job.json> [--out DIR] [--keep-intermediate] [-v]
wildfire-preproc validate <job_dir>
wildfire-preproc sample [--out DIR]
```

`--out DIR` defaults to `./jobs/<timestamp>_<short-hash>/`.

## 8. Dependencies and environment

Package manager: `uv`. Python pinned to `>=3.11,<3.13` in `pyproject.toml`.

Runtime:

- `rasterio>=1.3.10,<2`
- `geopandas>=0.14,<2`
- `shapely>=2.0,<3`
- `pyproj>=3.6`
- `numpy>=1.26,<3`
- `scipy>=1.11`
- `pydantic>=2.5,<3`
- `requests>=2.31`
- `tenacity>=8.2`
- `click>=8.1`
- `tqdm>=4.66`

Dev: `pytest`, `pytest-cov`, `responses`, `ruff`, `mypy`.

Deliberate non-choices: no `richdem` (Apple Silicon wheel pain; Horn 3×3 is ~30 LOC); no `pdal`/`whitebox` (out of scope).

Setup:

```bash
cd /Users/pynay/Documents/agrishield
uv init --python 3.11
uv add rasterio geopandas shapely pyproj numpy scipy pydantic requests tenacity click tqdm
uv add --dev pytest pytest-cov responses ruff mypy
```

## 9. Sample AOI

`data/sample/santa_monica_demo.geojson` — small (~few-km²) protected polygon in Santa Monica Mountains National Recreation Area. Chosen because it has full LANDFIRE/3DEP coverage, compact bounds (fast end-to-end), and mixed terrain that exercises every layer non-trivially including non-burnable codes.

`scripts/run_sample.sh` wraps `wildfire-preproc sample` for the README quick-start.

## 10. Example metadata.json

```json
{
  "job_id": "20260508T203015_a3f2",
  "created_at": "2026-05-08T20:30:15Z",
  "config": {
    "simulation_radius_m": 5000,
    "ignition_distance_m": 4500,
    "cell_size_m": 30,
    "crs": "EPSG:5070",
    "safety_buffer_m": 100,
    "non_burnable_sources": ["fbfm40"]
  },
  "grid": {
    "crs": "EPSG:5070",
    "transform": [30.0, 0.0, -2034780.0, 0.0, -30.0, 1745910.0],
    "width": 350,
    "height": 350,
    "bounds": [-2034780.0, 1735410.0, -2024280.0, 1745910.0]
  },
  "layers": {
    "fbfm40": { "path": "fbfm40.tif", "kind": "categorical", "nodata": 255, "source": "lfps:LC22_FBFM40", "fetched_at": "2026-05-08T20:30:18Z" },
    "dem":    { "path": "dem.tif",    "kind": "continuous",  "nodata": -9999, "source": "3dep:1m", "fetched_at": "2026-05-08T20:30:42Z" },
    "slp":    { "path": "slp.tif",    "kind": "continuous",  "nodata": -9999, "source": "derived:dem", "units": "degrees" },
    "asp":    { "path": "asp.tif",    "kind": "continuous",  "nodata": -9999, "source": "derived:dem", "units": "degrees_cw_from_N", "flat_value": -1 },
    "cc":     { "path": "cc.tif",     "kind": "continuous",  "nodata": -9999, "source": "lfps:LC22_CC" },
    "ch":     { "path": "ch.tif",     "kind": "continuous",  "nodata": -9999, "source": "lfps:LC22_CH" },
    "cbh":    { "path": "cbh.tif",    "kind": "continuous",  "nodata": -9999, "source": "lfps:LC22_CBH" },
    "cbd":    { "path": "cbd.tif",    "kind": "continuous",  "nodata": -9999, "source": "lfps:LC22_CBD" },
    "protected_mask":    { "path": "protected_mask.tif",    "kind": "mask", "nodata": null },
    "candidate_zone":    { "path": "candidate_zone.tif",    "kind": "mask", "nodata": null },
    "non_burnable_mask": { "path": "non_burnable_mask.tif", "kind": "mask", "nodata": null,
                           "sources": ["fbfm40_reclass:{91,92,93,98,99}"] }
  },
  "ignition_points": "ignition_points.geojson",
  "validation": "passed",
  "pipeline_version": "0.1.0"
}
```

## 11. Definition of Done

1. `uv sync` produces a working environment from a clean clone.
2. `wildfire-preproc sample` runs end-to-end on a developer machine — actually fetching from LFPS and 3DEP — and produces the full output directory.
3. `wildfire-preproc validate jobs/<sample>/` passes all checks.
4. `pytest` passes — unit tests for align, terrain, masks, validation, geometry; one integration test that runs the sample end-to-end and asserts on the manifest plus grid invariants. Marked `@pytest.mark.live` and run by default (per design Q4 = live data, real run).
5. `mypy src/` passes.
6. README has a quick-start and an "outputs explained" section.

## 12. Risks (acknowledged up front)

- **LFPS may rate-limit or be down** during live runs. Retry policy mitigates transient failures; persistent outage will surface as a clean error rather than a faked green run.
- **LFPS layer name codes may have shifted** (e.g. LF2023 release). Codes will be verified against live LFPS docs during implementation; source registry will be updated as needed.
- **3DEP returns ~10 m DEM by default**; resampled to 30 m via bilinear in Stage 3 — correct, but worth flagging.

## 13. Out of scope (v1)

- Firebreak placement or fuel modification. The pipeline produces inputs *for* an optimizer; it does not run optimization.
- OSM-based non-burnable mask layers. The plug-in point exists; the OSM source is not implemented in v1.
- Multi-CRS jobs. v1 assumes a single target CRS per job (default EPSG:5070).
- Async/parallel layer fetches. Layers are fetched serially; LFPS rate limits make parallel fetches risky and the time savings are small for an 8-layer fetch.
- Non-CONUS regions. Defaults assume CONUS coverage (LF2022, 3DEP, EPSG:5070).
