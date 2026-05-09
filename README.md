# wildfire-preproc

LANDFIRE/3DEP preprocessing pipeline producing ELMFIRE-ready raster outputs.

Given a protected-land polygon and a simulation config, the pipeline produces an aligned, validated raster directory that ELMFIRE can consume directly.

## Status

The pipeline (Stages 1-7) is fully implemented and tested against synthetic data:

- 64 unit + integration tests pass
- mypy strict + ruff strict are clean across `src/` and `tests/`
- The orchestrator (`run_pipeline`) wires every stage in order and produces the full output manifest
- All rasters share an identical canonical grid, enforced by `reproject_match` as the single chokepoint

**Live-API status:**

- 3DEP DEM fetcher: endpoint verified live; should work end-to-end
- LFPS fetcher: job endpoint surface verified as `/api/job/submit` and `/api/job/status`; live runs still require a real `LANDFIRE_EMAIL`

## Quick start

```bash
# Install
uv sync --all-groups

# Run the offline test suite (62 tests, all passing)
uv run pytest -m "not live"

# Type-check and lint
uv run mypy src
uv run ruff check src tests
```

## UI workspace

The project includes a no-build browser UI in `web/` for project setup, polygon drawing,
scenario settings, baseline/firebreak visualization, comparison, and JSON handoff.

Run the browser UI with the local backend bridge:

```bash
uv run python web/server.py
```

Open:

```text
http://127.0.0.1:4173/
```

The local bridge serves the UI and lets the **Save backend job** button write
validated backend job files under `jobs/ui/<timestamp>/`.

For a static UI only:

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory web
```

Open:

```text
http://127.0.0.1:4173/
```

The UI can export the backend job payload and import `firebreak_optimization.json`
from the optimization step.

Generate firebreak layouts for an existing preprocessed job:

```bash
uv run wildfire-preproc optimize-firebreaks jobs/YOUR_RUN/preprocessed \
  --baseline-dir jobs/YOUR_RUN/preprocessed/elmfire_no_firebreak
```

The bundled sample AOI and the `wildfire-preproc sample` command are wired up for live LFPS+3DEP runs. Set `LANDFIRE_EMAIL` before running against LFPS.

## End-to-end fire simulation app

`main.py` connects the full workflow:

1. Accept a protected location (farm/field boundary).
2. Run LANDFIRE/3DEP preprocessing for that location.
3. Prepare ELMFIRE input decks for 8 ignition points around the protected polygon.
4. Run 8 no-firebreak ELMFIRE simulations with wind directed toward the polygon center.
5. Write `simulation_summary.json` plus per-run fire outputs.

The function entry point is:

```python
from main import run_location_fire_simulations

result = run_location_fire_simulations(
    protected_polygon={...},  # GeoJSON Polygon or MultiPolygon
    out_dir="jobs/my-farm-run",
)
```

The command-line entry point is:

```powershell
.\.venv\Scripts\python.exe main.py --location-geojson .\data\my_farm.geojson --out .\jobs\my-farm-run
```

### Example: farm near San Diego

The repo includes a built-in example protected location: an 800 m x 800 m farm-sized rectangle near Ramona / San Pasqual Valley in San Diego County, centered at approximately:

```text
longitude: -116.945
latitude:   33.035
```

Run it with:

```powershell
.\.venv\Scripts\python.exe main.py --example-san-diego --out .\jobs\san-diego-farm-example
```

Outputs:

```text
jobs/san-diego-farm-example/
  job.json
  simulation_summary.json
  preprocessed/
    inputs/
    elmfire_no_firebreak/
      bearing_000/
      bearing_045/
      bearing_090/
      bearing_135/
      bearing_180/
      bearing_225/
      bearing_270/
      bearing_315/
```

Each `bearing_*` folder contains the ELMFIRE `inputs/elmfire.data`, per-run rasters, `run_manifest.json`, and ELMFIRE output rasters such as time of arrival, fireline intensity, and spread rate.

You can also create a rectangular location directly:

```powershell
.\.venv\Scripts\python.exe main.py --center-lon -116.945 --center-lat 33.035 --width-m 800 --height-m 800 --out .\jobs\custom-location
```

Or use your own farm boundary GeoJSON:

```powershell
.\.venv\Scripts\python.exe main.py --location-geojson .\data\my_farm.geojson --out .\jobs\my-farm-run
```

Prerequisites:

- ELMFIRE must be built at `elmfire/build/linux/bin/elmfire_2025.0212`. The setup in this workspace uses WSL Ubuntu and the official `elmfire/build/linux/make_gnu.sh` build.
- Live preprocessing still depends on the LFPS fetch endpoint. See "Known integration gap" if LFPS layer fetching fails before the ELMFIRE stage.

Before live LFPS preprocessing, set your LANDFIRE email in `.env`:

```text
LANDFIRE_EMAIL=your.email@example.com
LANDFIRE_VERSION=LF2023
```

`main.py` loads `.env` automatically. Replace the placeholder email with your real email before running live LFPS requests. `LANDFIRE_VERSION` controls the requested layer prefix, so the default end-to-end app requests layers such as `LF2023_FBFM40`, `LF2023_CC`, and `LF2023_CBD`.

You can also override the version per run:

```powershell
.\.venv\Scripts\python.exe main.py --example-san-diego --landfire-version LF2023 --out .\jobs\san-diego-farm-example
```

## Outputs

Per-job, `inputs/` contains:

| File                                     | Description                                                                                            |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `fbfm40.tif`                             | LANDFIRE Scott & Burgan 40 fuel models, `uint8`, nodata=255                                            |
| `dem.tif`                                | DEM resampled to target cell size, `float32`, nodata=-9999                                             |
| `slp.tif`                                | Slope (degrees, 0-90), Horn 3x3 derived, `float32`, nodata=-9999                                       |
| `asp.tif`                                | Aspect (compass degrees from N, flat=-1), `float32`, nodata=-9999                                      |
| `cc.tif`, `ch.tif`, `cbh.tif`, `cbd.tif` | LANDFIRE canopy cover, height, base height, bulk density (`float32`)                                   |
| `protected_mask.tif`                     | 1 inside protected polygon, 0 outside                                                                  |
| `candidate_zone.tif`                     | 1 where firebreaks may be placed (annulus between safety-buffered protected polygon and ignition ring) |
| `non_burnable_mask.tif`                  | 1 where fire cannot spread (FBFM40 codes 91/92/93/98/99 by default)                                    |
| `ignition_points.geojson`                | 8 ignition points on N/NE/E/SE/S/SW/W/NW bearings                                                      |
| `metadata.json`                          | Grid, CRS, bounds, layer source provenance                                                             |
| `validation_report.txt`                  | Stage 6 invariant check report                                                                         |

All rasters share identical CRS, extent, transform, width, and height. ELMFIRE will reject misaligned grids.

## Config (job.json)

```json
{
  "protected_polygon": { "type": "Polygon", "coordinates": [[...]] },
  "simulation_radius_m": 5000,
  "ignition_distance_m": 4500,
  "cell_size_m": 30,
  "crs": "EPSG:5070"
}
```

Optional fields: `safety_buffer_m` (default 100), `non_burnable_sources` (default `["fbfm40"]`), `landfire_version` (default `"LF2022"`), `cache_dir`.

```bash
uv run wildfire-preproc run path/to/job.json --out ./jobs/my-run
```

By default the input polygon is interpreted as `EPSG:4326` (lon/lat). Override with `--protected-polygon-crs`.

## Architecture

7 stages, run in order:

1. **Domain** — buffer the protected polygon to produce simulation domain, ignition ring, candidate zone, and 8 ignition points (all in the projected target CRS, polygon-boundary based).
2. **Acquire** — fetch each layer from its configured source (LFPS for fuels/canopy, 3DEP for DEM). Cached on disk by sha256(bbox+layer+crs). Atomic writes (.tmp + rename), smart retry (Connection/Timeout/5xx/429 only).
3. **Align** — build a single canonical `GridSpec` snapped to the global cell grid, then reproject every raster onto it (nearest for FBFM40, bilinear for continuous). `reproject_match` is the only path that produces an aligned raster.
4. **Terrain** — derive slope and aspect from the aligned DEM via Horn 3x3 (slope in degrees, aspect compass-cw-from-N with flat=-1).
5. **Masks** — rasterize `protected_mask`, `candidate_zone`, build `non_burnable_mask` via pluggable source registry (FBFM40 reclass default).
6. **Validate** — verify CRS, transform, dimensions, nodata, value ranges per layer; build a human-readable report. Fails the run on the first violation.
7. **Export** — write `metadata.json`; clean up `_intermediate/` (preserved with `--keep-intermediate`).

### Module layout

```
src/wildfire_preproc/
  cli.py                  # `wildfire-preproc run|validate|sample`
  config.py               # LayerKey, LayerKind, JobConfig (pydantic v2)
  pipeline.py             # 7-stage orchestrator
  domain/                 # Stage 1
  sources/                # Stage 2: base Protocol + LFPS + 3DEP + local + registry
  align/                  # Stage 3: GridSpec + reproject_match
  terrain/                # Stage 4: Horn 3x3 slope/aspect
  masks/                  # Stage 5: protected, candidate, non-burnable
  validation/             # Stage 6: per-raster checks + report formatter + FBFM40 codes
  export/                 # Stage 7: metadata.json
  utils/                  # geometry, raster I/O helpers
```

## Live integration notes

The LFPS fetcher (`src/wildfire_preproc/sources/lfps.py`) uses the LANDFIRE Product Service REST workflow:

1. Submit a job at `https://lfps.usgs.gov/api/job/submit`
2. Poll `https://lfps.usgs.gov/api/job/status?JobId=<job id>`
3. Download the output zip and extract the GeoTIFF

The submit endpoint was verified live on 2026-05-09: a request without required
parameters returns `400 Bad Request`, confirming the endpoint exists. The code
still requires a real `LANDFIRE_EMAIL` for live job submission.

## Development

```bash
uv run pytest -m "not live"                    # 62 tests, ~5s
uv run pytest -m live                          # requires live LFPS/3DEP access and LANDFIRE_EMAIL
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
```

Useful ad-hoc:

```bash
# Probe LFPS products endpoint (this works)
curl https://lfps.usgs.gov/api/products | jq '.products[] | select(.version=="LF2022")'

# Probe LFPS submit endpoint shape; 400 means the endpoint exists but needs parameters
curl -I https://lfps.usgs.gov/api/job/submit

# Probe 3DEP (this also works)
curl 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer?f=json'
```
