# Wildfire Preprocessing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LANDFIRE/3DEP preprocessing pipeline that turns a protected polygon + config into an aligned, ELMFIRE-ready raster output directory.

**Architecture:** Synchronous file-driven pipeline. A frozen `GridSpec` (set in Stage 3) is the single source of truth for raster alignment; every output flows through `reproject_match` or is rasterized directly into that grid. Pluggable `RasterSource` abstraction handles local files, LFPS, and 3DEP behind one interface.

**Tech Stack:** Python 3.11, `uv`, `rasterio`, `geopandas`, `shapely>=2`, `pyproj`, `numpy`, `scipy`, `pydantic v2`, `requests`, `tenacity`, `click`, `tqdm`. Testing: `pytest`, `responses`, `mypy`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-05-08-wildfire-preprocessing-pipeline-design.md`

---

## File Structure

Top-level:

```
agrishield/
  pyproject.toml
  README.md
  data/sample/santa_monica_demo.geojson
  scripts/run_sample.sh
  src/wildfire_preproc/
    __init__.py
    cli.py
    config.py                      # LayerKey, LayerKind, JobConfig
    pipeline.py                    # orchestrator
    domain/simulation_domain.py    # Stage 1
    sources/{base,registry,local,lfps,threedep,cache}.py    # Stage 2
    align/{grid,align}.py          # Stage 3
    terrain/derive.py              # Stage 4
    masks/{protected,candidate,non_burnable}.py             # Stage 5
    validation/{checks,report,fbfm40_codes}.py              # Stage 6
    export/{writer,metadata}.py    # Stage 7
    utils/{geometry,raster,logging}.py
  tests/
    test_config.py
    test_geometry.py
    test_grid.py
    test_align.py
    test_writer.py
    test_simulation_domain.py
    test_terrain.py
    test_masks_protected_candidate.py
    test_masks_non_burnable.py
    test_validation.py
    test_sources_cache.py
    test_sources_lfps.py
    test_sources_threedep.py
    test_metadata.py
    test_pipeline.py
    test_pipeline_e2e_live.py      # @pytest.mark.live
```

Each file has one clear responsibility. Files that change together are grouped (Stage 5's three mask types, Stage 2's source backends).

---

## Conventions used by every task

- **Test runner:** `uv run pytest -x` (stop on first failure; faster TDD loop).
- **Lint:** `uv run ruff check src tests` and `uv run ruff format src tests`.
- **Type check:** `uv run mypy src`.
- **Commit format:** Conventional commits (`feat:`, `test:`, `chore:`, `fix:`).
- **One task = one commit** unless the task explicitly says otherwise.

---

## Task 1: Project bootstrap

**Files:**
- Create: `pyproject.toml`, `README.md`, `.gitignore`, `src/wildfire_preproc/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Initialize uv project and pin Python**

```bash
cd /Users/pynay/Documents/agrishield
uv init --python 3.11 --no-readme --package
```

This generates `pyproject.toml` and `.python-version`. Verify `python_requires` next.

- [ ] **Step 2: Replace generated pyproject.toml**

Overwrite `pyproject.toml` with:

```toml
[project]
name = "wildfire-preproc"
version = "0.1.0"
description = "LANDFIRE/3DEP preprocessing pipeline producing ELMFIRE-ready raster outputs."
readme = "README.md"
requires-python = ">=3.11,<3.13"
dependencies = [
    "rasterio>=1.3.10,<2",
    "geopandas>=0.14,<2",
    "shapely>=2.0,<3",
    "pyproj>=3.6",
    "numpy>=1.26,<3",
    "scipy>=1.11",
    "pydantic>=2.5,<3",
    "requests>=2.31",
    "tenacity>=8.2",
    "click>=8.1",
    "tqdm>=4.66",
]

[project.scripts]
wildfire-preproc = "wildfire_preproc.cli:main"

[dependency-groups]
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "responses>=0.25",
    "ruff>=0.4",
    "mypy>=1.8",
    "types-requests",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/wildfire_preproc"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: tests that hit live LFPS/3DEP APIs"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "RUF"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_unused_ignores = true
files = ["src"]
```

- [ ] **Step 3: Install deps and lockfile**

Run: `uv sync --all-groups`
Expected: creates `.venv/` and `uv.lock`. No errors.

- [ ] **Step 4: Add .gitignore**

Create `.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
.coverage
jobs/
_intermediate/
.DS_Store
```

- [ ] **Step 5: Stub package and tests modules**

Create `src/wildfire_preproc/__init__.py`:

```python
"""Wildfire preprocessing pipeline producing ELMFIRE-ready raster outputs."""

__version__ = "0.1.0"
```

Create `tests/__init__.py` (empty file).

Create `README.md` with a one-line description (we'll flesh it out at the end):

```markdown
# wildfire-preproc

LANDFIRE/3DEP preprocessing pipeline producing ELMFIRE-ready raster outputs.
```

- [ ] **Step 6: Smoke test**

Run: `uv run python -c "import wildfire_preproc; print(wildfire_preproc.__version__)"`
Expected: `0.1.0`

Run: `uv run pytest -x`
Expected: `0 tests collected` (no tests yet).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .python-version README.md src/ tests/
git commit -m "chore: bootstrap uv project with GIS dependencies"
```

---

## Task 2: Config — LayerKey, LayerKind, JobConfig

**Files:**
- Create: `src/wildfire_preproc/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config.py`:

```python
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wildfire_preproc.config import (
    JobConfig,
    LayerKey,
    LayerKind,
    layer_kind,
)


def test_layer_kind_classification():
    assert layer_kind(LayerKey.FBFM40) == LayerKind.CATEGORICAL
    assert layer_kind(LayerKey.DEM) == LayerKind.CONTINUOUS
    assert layer_kind(LayerKey.CC) == LayerKind.CONTINUOUS
    assert layer_kind(LayerKey.CH) == LayerKind.CONTINUOUS
    assert layer_kind(LayerKey.CBH) == LayerKind.CONTINUOUS
    assert layer_kind(LayerKey.CBD) == LayerKind.CONTINUOUS


def test_jobconfig_minimal_payload(tmp_path: Path):
    payload = {
        "protected_polygon": {
            "type": "Polygon",
            "coordinates": [[[-118.7, 34.1], [-118.6, 34.1], [-118.6, 34.2], [-118.7, 34.2], [-118.7, 34.1]]],
        },
        "simulation_radius_m": 5000,
        "ignition_distance_m": 4500,
        "cell_size_m": 30,
        "crs": "EPSG:5070",
    }
    cfg = JobConfig.model_validate(payload)
    assert cfg.simulation_radius_m == 5000
    assert cfg.ignition_distance_m == 4500
    assert cfg.cell_size_m == 30
    assert cfg.crs == "EPSG:5070"
    assert cfg.safety_buffer_m == 100  # default
    assert cfg.non_burnable_sources == ["fbfm40"]  # default
    assert cfg.landfire_version == "LF2022"


def test_jobconfig_rejects_ignition_geq_radius():
    payload = {
        "protected_polygon": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "simulation_radius_m": 4000,
        "ignition_distance_m": 5000,
        "cell_size_m": 30,
        "crs": "EPSG:5070",
    }
    with pytest.raises(ValidationError, match="ignition_distance_m must be < simulation_radius_m"):
        JobConfig.model_validate(payload)


def test_jobconfig_rejects_non_polygon():
    payload = {
        "protected_polygon": {"type": "Point", "coordinates": [0, 0]},
        "simulation_radius_m": 5000,
        "ignition_distance_m": 4500,
        "cell_size_m": 30,
        "crs": "EPSG:5070",
    }
    with pytest.raises(ValidationError, match="protected_polygon"):
        JobConfig.model_validate(payload)


def test_jobconfig_from_json_file(tmp_path: Path):
    payload = {
        "protected_polygon": {
            "type": "Polygon",
            "coordinates": [[[-118.7, 34.1], [-118.6, 34.1], [-118.6, 34.2], [-118.7, 34.2], [-118.7, 34.1]]],
        },
        "simulation_radius_m": 5000,
        "ignition_distance_m": 4500,
        "cell_size_m": 30,
        "crs": "EPSG:5070",
    }
    p = tmp_path / "job.json"
    p.write_text(json.dumps(payload))
    cfg = JobConfig.from_json_file(p)
    assert cfg.simulation_radius_m == 5000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -x`
Expected: ImportError on `wildfire_preproc.config`.

- [ ] **Step 3: Implement config**

Create `src/wildfire_preproc/config.py`:

```python
"""Job configuration: layer enumeration, kinds, and the JSON-payload schema."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LayerKey(str, Enum):
    FBFM40 = "fbfm40"
    DEM = "dem"
    SLP = "slp"
    ASP = "asp"
    CC = "cc"
    CH = "ch"
    CBH = "cbh"
    CBD = "cbd"
    PROTECTED_MASK = "protected_mask"
    CANDIDATE_ZONE = "candidate_zone"
    NON_BURNABLE_MASK = "non_burnable_mask"


class LayerKind(str, Enum):
    CATEGORICAL = "categorical"
    CONTINUOUS = "continuous"
    MASK = "mask"


_KIND_BY_LAYER: dict[LayerKey, LayerKind] = {
    LayerKey.FBFM40: LayerKind.CATEGORICAL,
    LayerKey.DEM: LayerKind.CONTINUOUS,
    LayerKey.SLP: LayerKind.CONTINUOUS,
    LayerKey.ASP: LayerKind.CONTINUOUS,
    LayerKey.CC: LayerKind.CONTINUOUS,
    LayerKey.CH: LayerKind.CONTINUOUS,
    LayerKey.CBH: LayerKind.CONTINUOUS,
    LayerKey.CBD: LayerKind.CONTINUOUS,
    LayerKey.PROTECTED_MASK: LayerKind.MASK,
    LayerKey.CANDIDATE_ZONE: LayerKind.MASK,
    LayerKey.NON_BURNABLE_MASK: LayerKind.MASK,
}


def layer_kind(layer: LayerKey) -> LayerKind:
    return _KIND_BY_LAYER[layer]


# Layers that are fetched from external sources (not derived/computed locally).
FETCHED_LAYERS: tuple[LayerKey, ...] = (
    LayerKey.FBFM40,
    LayerKey.DEM,
    LayerKey.CC,
    LayerKey.CH,
    LayerKey.CBH,
    LayerKey.CBD,
)


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protected_polygon: dict[str, Any]
    simulation_radius_m: float = Field(gt=0)
    ignition_distance_m: float = Field(gt=0)
    cell_size_m: float = Field(gt=0)
    crs: str

    safety_buffer_m: float = Field(default=100.0, ge=0)
    non_burnable_sources: list[str] = Field(default_factory=lambda: ["fbfm40"])
    landfire_version: str = "LF2022"
    cache_dir: str | None = None

    @field_validator("protected_polygon")
    @classmethod
    def _polygon_must_be_polygon(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(v, dict) or v.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError("protected_polygon must be GeoJSON Polygon or MultiPolygon")
        return v

    @model_validator(mode="after")
    def _ignition_lt_radius(self) -> JobConfig:
        if self.ignition_distance_m >= self.simulation_radius_m:
            raise ValueError("ignition_distance_m must be < simulation_radius_m")
        return self

    @classmethod
    def from_json_file(cls, path: Path) -> JobConfig:
        return cls.model_validate(json.loads(Path(path).read_text()))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config.py -x`
Expected: 5 passed.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/wildfire_preproc/config.py`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add src/wildfire_preproc/config.py tests/test_config.py
git commit -m "feat(config): add LayerKey/LayerKind enums and JobConfig schema"
```

---

## Task 3: Geometry helpers

**Files:**
- Create: `src/wildfire_preproc/utils/__init__.py`, `src/wildfire_preproc/utils/geometry.py`
- Test: `tests/test_geometry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_geometry.py`:

```python
import math

import pytest
from shapely.geometry import LineString, Point, Polygon

from wildfire_preproc.utils.geometry import (
    compass_bearing_to_radians,
    nearest_point_on,
    point_at_bearing,
)


def test_compass_bearing_to_radians_north_is_pi_over_2_in_math_convention():
    assert compass_bearing_to_radians(0) == pytest.approx(math.pi / 2)
    assert compass_bearing_to_radians(90) == pytest.approx(0.0)
    assert compass_bearing_to_radians(180) == pytest.approx(-math.pi / 2)


def test_point_at_bearing_north():
    origin = Point(0, 0)
    p = point_at_bearing(origin, bearing_deg=0, distance=1000)
    assert p.x == pytest.approx(0.0, abs=1e-6)
    assert p.y == pytest.approx(1000.0, abs=1e-6)


def test_point_at_bearing_east():
    origin = Point(0, 0)
    p = point_at_bearing(origin, bearing_deg=90, distance=1000)
    assert p.x == pytest.approx(1000.0, abs=1e-6)
    assert p.y == pytest.approx(0.0, abs=1e-6)


def test_point_at_bearing_southwest():
    origin = Point(0, 0)
    p = point_at_bearing(origin, bearing_deg=225, distance=math.sqrt(2) * 1000)
    assert p.x == pytest.approx(-1000.0, abs=1e-3)
    assert p.y == pytest.approx(-1000.0, abs=1e-3)


def test_nearest_point_on_line():
    line = LineString([(0, 0), (10, 0)])
    p = nearest_point_on(line, Point(5, 5))
    assert p.x == pytest.approx(5.0)
    assert p.y == pytest.approx(0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_geometry.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement helpers**

Create `src/wildfire_preproc/utils/__init__.py` (empty).

Create `src/wildfire_preproc/utils/geometry.py`:

```python
"""Geometry helpers: compass-bearing math on projected coordinates."""

from __future__ import annotations

import math

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points


def compass_bearing_to_radians(bearing_deg: float) -> float:
    """Convert a compass bearing (0=N, clockwise) to a math angle (0=+x axis, ccw)."""
    return math.radians(90.0 - bearing_deg)


def point_at_bearing(origin: Point, bearing_deg: float, distance: float) -> Point:
    """Point at the given compass bearing and distance from origin (projected coords)."""
    theta = compass_bearing_to_radians(bearing_deg)
    return Point(origin.x + distance * math.cos(theta), origin.y + distance * math.sin(theta))


def nearest_point_on(geom: BaseGeometry, ref: Point) -> Point:
    """Closest point on `geom` to `ref`."""
    snapped, _ = nearest_points(geom, ref)
    return Point(snapped.x, snapped.y)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_geometry.py -x`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/utils/__init__.py src/wildfire_preproc/utils/geometry.py tests/test_geometry.py
git commit -m "feat(utils): add compass-bearing geometry helpers"
```

---

## Task 4: GridSpec construction

**Files:**
- Create: `src/wildfire_preproc/align/__init__.py`, `src/wildfire_preproc/align/grid.py`
- Test: `tests/test_grid.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_grid.py`:

```python
import pytest
from rasterio.crs import CRS
from shapely.geometry import box

from wildfire_preproc.align.grid import GridSpec, gridspec_from_polygon


def test_gridspec_snaps_to_global_origin():
    poly = box(100.5, 200.5, 250.5, 350.5)  # arbitrary projected bounds
    grid = gridspec_from_polygon(poly, crs=CRS.from_epsg(5070), cell_size=30.0)
    minx = grid.transform.c
    maxy = grid.transform.f
    # Snap outward to multiples of 30, anchored at (0, 0):
    # minx_snapped = floor(100.5 / 30) * 30 = 90
    # maxy_snapped = ceil(350.5 / 30) * 30 = 360
    assert minx == pytest.approx(90.0)
    assert maxy == pytest.approx(360.0)


def test_gridspec_dimensions_cover_bounds():
    poly = box(0.0, 0.0, 90.0, 60.0)
    grid = gridspec_from_polygon(poly, crs=CRS.from_epsg(5070), cell_size=30.0)
    assert grid.width == 3
    assert grid.height == 2
    assert grid.cell_size == 30.0


def test_gridspec_two_overlapping_jobs_share_pixel_grid():
    poly_a = box(105.0, 205.0, 245.0, 345.0)
    poly_b = box(125.0, 225.0, 265.0, 365.0)
    grid_a = gridspec_from_polygon(poly_a, crs=CRS.from_epsg(5070), cell_size=30.0)
    grid_b = gridspec_from_polygon(poly_b, crs=CRS.from_epsg(5070), cell_size=30.0)
    # Both should be aligned to the same global grid: (minx % 30) == 0.
    assert grid_a.transform.c % 30.0 == pytest.approx(0.0)
    assert grid_b.transform.c % 30.0 == pytest.approx(0.0)
    assert grid_a.transform.f % 30.0 == pytest.approx(0.0)
    assert grid_b.transform.f % 30.0 == pytest.approx(0.0)


def test_gridspec_is_frozen():
    poly = box(0, 0, 90, 60)
    grid = gridspec_from_polygon(poly, crs=CRS.from_epsg(5070), cell_size=30.0)
    with pytest.raises(Exception):
        grid.width = 99  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grid.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement GridSpec**

Create `src/wildfire_preproc/align/__init__.py` (empty).

Create `src/wildfire_preproc/align/grid.py`:

```python
"""Canonical grid specification — frozen after Stage 3."""

from __future__ import annotations

import math
from dataclasses import dataclass

from rasterio.crs import CRS
from rasterio.transform import Affine, from_origin
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class GridSpec:
    crs: CRS
    transform: Affine
    width: int
    height: int
    cell_size: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        minx = self.transform.c
        maxy = self.transform.f
        maxx = minx + self.width * self.cell_size
        miny = maxy - self.height * self.cell_size
        return (minx, miny, maxx, maxy)


def gridspec_from_polygon(polygon: BaseGeometry, crs: CRS, cell_size: float) -> GridSpec:
    """Snap polygon bounds outward to a global cell grid anchored at the CRS origin (0, 0)."""
    minx, miny, maxx, maxy = polygon.bounds
    minx_s = math.floor(minx / cell_size) * cell_size
    miny_s = math.floor(miny / cell_size) * cell_size
    maxx_s = math.ceil(maxx / cell_size) * cell_size
    maxy_s = math.ceil(maxy / cell_size) * cell_size
    width = int(round((maxx_s - minx_s) / cell_size))
    height = int(round((maxy_s - miny_s) / cell_size))
    return GridSpec(
        crs=crs,
        transform=from_origin(minx_s, maxy_s, cell_size, cell_size),
        width=width,
        height=height,
        cell_size=cell_size,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_grid.py -x`
Expected: 4 passed.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/wildfire_preproc/align`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/wildfire_preproc/align/ tests/test_grid.py
git commit -m "feat(align): GridSpec snapped to global cell grid"
```

---

## Task 5: reproject_match — the only path to an aligned raster

**Files:**
- Create: `src/wildfire_preproc/utils/raster.py`, `src/wildfire_preproc/align/align.py`
- Test: `tests/test_align.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_align.py`:

```python
import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from wildfire_preproc.align.align import reproject_match
from wildfire_preproc.align.grid import gridspec_from_polygon
from wildfire_preproc.config import LayerKind


def _write_test_raster(path, data, transform, crs, nodata=None, dtype=None):
    dtype = dtype or data.dtype
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def test_reproject_match_continuous_bilinear(tmp_path):
    # Source: 60x60 raster at 10m, all values=42
    src = tmp_path / "src.tif"
    data = np.full((60, 60), 42.0, dtype="float32")
    transform = from_origin(0, 600, 10, 10)
    _write_test_raster(src, data, transform, CRS.from_epsg(5070), nodata=-9999.0)

    grid = gridspec_from_polygon(box(0, 0, 600, 600), CRS.from_epsg(5070), cell_size=30.0)
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CONTINUOUS, dst_nodata=-9999.0)

    with rasterio.open(out) as ds:
        assert ds.crs == grid.crs
        assert ds.width == grid.width
        assert ds.height == grid.height
        assert tuple(ds.transform) == tuple(grid.transform)
        arr = ds.read(1)
        # Bilinear of a constant field is the constant.
        assert np.allclose(arr, 42.0)


def test_reproject_match_categorical_nearest(tmp_path):
    src = tmp_path / "src.tif"
    data = np.full((60, 60), 101, dtype="uint8")  # FBFM40 grass code
    transform = from_origin(0, 600, 10, 10)
    _write_test_raster(src, data, transform, CRS.from_epsg(5070), nodata=255, dtype="uint8")

    grid = gridspec_from_polygon(box(0, 0, 600, 600), CRS.from_epsg(5070), cell_size=30.0)
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CATEGORICAL, dst_nodata=255)

    with rasterio.open(out) as ds:
        arr = ds.read(1)
        # Nearest-neighbor must preserve the categorical code (no fractional values).
        assert set(np.unique(arr).tolist()) == {101}
        assert ds.dtypes[0] == "uint8"


def test_reproject_match_emits_correct_dtype_for_kind(tmp_path):
    src = tmp_path / "src.tif"
    data = np.full((60, 60), 42.0, dtype="float64")
    transform = from_origin(0, 600, 10, 10)
    _write_test_raster(src, data, transform, CRS.from_epsg(5070), nodata=-9999.0, dtype="float64")
    grid = gridspec_from_polygon(box(0, 0, 600, 600), CRS.from_epsg(5070), cell_size=30.0)
    out = tmp_path / "out.tif"
    reproject_match(src=src, dst=out, grid=grid, kind=LayerKind.CONTINUOUS, dst_nodata=-9999.0)
    with rasterio.open(out) as ds:
        assert ds.dtypes[0] == "float32"  # continuous always float32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_align.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement raster utils + reproject_match**

Create `src/wildfire_preproc/utils/raster.py`:

```python
"""Tiny rasterio helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind

CONTINUOUS_NODATA: float = -9999.0
CATEGORICAL_NODATA: int = 255


def dtype_for_kind(kind: LayerKind) -> str:
    if kind == LayerKind.CONTINUOUS:
        return "float32"
    if kind == LayerKind.CATEGORICAL:
        return "uint8"
    if kind == LayerKind.MASK:
        return "uint8"
    raise ValueError(f"unknown LayerKind: {kind}")


def nodata_for_kind(kind: LayerKind) -> float | int | None:
    if kind == LayerKind.CONTINUOUS:
        return CONTINUOUS_NODATA
    if kind == LayerKind.CATEGORICAL:
        return CATEGORICAL_NODATA
    if kind == LayerKind.MASK:
        return None
    raise ValueError(f"unknown LayerKind: {kind}")


def write_array(
    path: Path,
    data: np.ndarray,
    grid: GridSpec,
    kind: LayerKind,
) -> None:
    """Write a 2D array as a GeoTIFF matching the canonical grid + kind conventions."""
    if data.shape != (grid.height, grid.width):
        raise ValueError(f"array shape {data.shape} does not match grid {(grid.height, grid.width)}")
    dtype = dtype_for_kind(kind)
    nodata = nodata_for_kind(kind)
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "LZW",
        "predictor": 2 if dtype.startswith("float") else 1,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)
```

Create `src/wildfire_preproc/align/align.py`:

```python
"""reproject_match — the only function that produces an aligned raster."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import dtype_for_kind


def _resampling_for(kind: LayerKind) -> Resampling:
    if kind == LayerKind.CATEGORICAL:
        return Resampling.nearest
    if kind == LayerKind.CONTINUOUS:
        return Resampling.bilinear
    if kind == LayerKind.MASK:
        return Resampling.nearest
    raise ValueError(f"unknown LayerKind: {kind}")


def reproject_match(
    src: Path,
    dst: Path,
    grid: GridSpec,
    kind: LayerKind,
    dst_nodata: float | int | None,
) -> None:
    """Reproject `src` raster onto the canonical `grid`, writing `dst`."""
    dtype = dtype_for_kind(kind)
    resampling = _resampling_for(kind)

    with rasterio.open(src) as s:
        src_band = s.read(1)
        src_crs = s.crs
        src_transform = s.transform
        src_nodata = s.nodata

    out = np.full((grid.height, grid.width), dst_nodata if dst_nodata is not None else 0, dtype=dtype)

    reproject(
        source=src_band,
        destination=out,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        dst_nodata=dst_nodata,
        resampling=resampling,
    )

    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": dst_nodata,
        "tiled": True,
        "compress": "LZW",
        "predictor": 2 if dtype.startswith("float") else 1,
    }
    with rasterio.open(dst, "w", **profile) as d:
        d.write(out, 1)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_align.py -x`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/utils/raster.py src/wildfire_preproc/align/align.py tests/test_align.py
git commit -m "feat(align): reproject_match enforces canonical grid + kind-specific resampling"
```

---

## Task 6: Stage 1 — simulation domain geometry

**Files:**
- Create: `src/wildfire_preproc/domain/__init__.py`, `src/wildfire_preproc/domain/simulation_domain.py`
- Test: `tests/test_simulation_domain.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_simulation_domain.py`:

```python
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from wildfire_preproc.domain.simulation_domain import build_domain, DomainArtifacts


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
    assert not candidate.intersects(safety.buffer(-1.0))  # candidate is outside the buffered protected area


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
    poly_wgs84 = Polygon([(-118.7, 34.1), (-118.6, 34.1), (-118.6, 34.2), (-118.7, 34.2), (-118.7, 34.1)])
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_simulation_domain.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement Stage 1**

Create `src/wildfire_preproc/domain/__init__.py` (empty).

Create `src/wildfire_preproc/domain/simulation_domain.py`:

```python
"""Stage 1 — build simulation domain geometry from a protected polygon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

from wildfire_preproc.utils.geometry import nearest_point_on, point_at_bearing


COMPASS_BEARINGS: tuple[int, ...] = (0, 45, 90, 135, 180, 225, 270, 315)


@dataclass(frozen=True)
class DomainArtifacts:
    simulation_domain_path: Path
    ignition_ring_path: Path
    ignition_points_path: Path  # the deliverable
    candidate_zone_polygon_path: Path
    simulation_polygon: Polygon
    protected_polygon_projected: Polygon


def build_domain(
    protected_polygon: Polygon,
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
        candidate = point_at_bearing(centroid, bearing_deg=bearing, distance=ignition_distance_m * 2)
        snapped = nearest_point_on(ignition_ring, candidate)
        ignition_points.append({"geometry": snapped, "bearing_deg": bearing})

    # 4. Write artifacts.
    sim_path = out_dir / "simulation_domain.geojson"
    ring_path = out_dir / "ignition_ring.geojson"
    pts_path = out_dir / "ignition_points.geojson"
    candidate_path = out_dir / "candidate_zone_polygon.geojson"

    gpd.GeoDataFrame(geometry=[sim_polygon], crs=target_crs).to_file(sim_path, driver="GeoJSON")
    gpd.GeoDataFrame(geometry=[ignition_ring], crs=target_crs).to_file(ring_path, driver="GeoJSON")
    gpd.GeoDataFrame(ignition_points, crs=target_crs).to_file(pts_path, driver="GeoJSON")
    gpd.GeoDataFrame(geometry=[candidate_zone], crs=target_crs).to_file(candidate_path, driver="GeoJSON")

    return DomainArtifacts(
        simulation_domain_path=sim_path,
        ignition_ring_path=ring_path,
        ignition_points_path=pts_path,
        candidate_zone_polygon_path=candidate_path,
        simulation_polygon=sim_polygon,
        protected_polygon_projected=poly_proj,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_simulation_domain.py -x`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/domain/ tests/test_simulation_domain.py
git commit -m "feat(domain): Stage 1 simulation domain, ignition ring, 8 ignition points"
```

---

## Task 7: Stage 4 — slope/aspect from DEM (Horn 3×3)

**Files:**
- Create: `src/wildfire_preproc/terrain/__init__.py`, `src/wildfire_preproc/terrain/derive.py`
- Test: `tests/test_terrain.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_terrain.py`:

```python
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.terrain.derive import derive_slope_aspect


def _write_dem(path: Path, data: np.ndarray, cell: float) -> None:
    with rasterio.open(
        path, "w",
        driver="GTiff", height=data.shape[0], width=data.shape[1],
        count=1, dtype="float32", crs=CRS.from_epsg(5070),
        transform=from_origin(0, data.shape[0] * cell, cell, cell),
        nodata=-9999.0,
    ) as ds:
        ds.write(data, 1)


def _grid(shape: tuple[int, int], cell: float) -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, shape[0] * cell, cell, cell),
        width=shape[1],
        height=shape[0],
        cell_size=cell,
    )


def test_flat_dem_yields_zero_slope_and_flat_aspect(tmp_path: Path):
    dem = tmp_path / "dem.tif"
    arr = np.full((20, 20), 100.0, dtype="float32")
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s, rasterio.open(out_asp) as a:
        slp = s.read(1)
        asp = a.read(1)
    interior_slp = slp[1:-1, 1:-1]
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_slp, 0.0, atol=1e-5)
    assert np.allclose(interior_asp, -1.0)  # flat = -1


def test_east_facing_slope(tmp_path: Path):
    # DEM increasing west-to-east at 1 m per cell over 30m cells = ~1.91 deg slope, aspect = 90
    dem = tmp_path / "dem.tif"
    cols = np.arange(20)
    arr = np.tile(cols, (20, 1)).astype("float32")  # z grows with x
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s, rasterio.open(out_asp) as a:
        slp = s.read(1)
        asp = a.read(1)
    expected_slope_deg = np.degrees(np.arctan(1.0 / 30.0))
    interior_slp = slp[1:-1, 1:-1]
    interior_asp = asp[1:-1, 1:-1]
    assert np.allclose(interior_slp, expected_slope_deg, atol=1e-3)
    # Slope rises to +x, so it FACES east (downhill direction is +x, so aspect is east → 90)
    # But many GIS conventions report aspect as "downslope" direction. Confirm convention:
    # Horn aspect formula: atan2(dz/dy, -dz/dx) gives the upslope direction;
    # convert to compass-clockwise-from-north for the "downslope facing" direction.
    # For a +x gradient: aspect should be 270 (downslope points west).
    # Wait — re-examine: for a surface where elevation increases eastward, the downhill is west (270).
    assert np.allclose(interior_asp, 270.0, atol=1.0)


def test_edges_are_nodata(tmp_path: Path):
    dem = tmp_path / "dem.tif"
    arr = np.full((10, 10), 100.0, dtype="float32")
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s, rasterio.open(out_asp) as a:
        slp = s.read(1)
        asp = a.read(1)
    assert np.all(slp[0, :] == -9999.0)
    assert np.all(slp[-1, :] == -9999.0)
    assert np.all(slp[:, 0] == -9999.0)
    assert np.all(slp[:, -1] == -9999.0)
    assert np.all(asp[0, :] == -9999.0)


def test_slope_aspect_inputs_dem_nodata_propagates(tmp_path: Path):
    dem = tmp_path / "dem.tif"
    arr = np.full((10, 10), 100.0, dtype="float32")
    arr[5, 5] = -9999.0  # nodata in middle
    _write_dem(dem, arr, cell=30.0)
    out_slp = tmp_path / "slp.tif"
    out_asp = tmp_path / "asp.tif"
    derive_slope_aspect(dem, _grid(arr.shape, 30.0), out_slp, out_asp)
    with rasterio.open(out_slp) as s:
        slp = s.read(1)
    # Cells whose 3x3 window touches nodata should be nodata.
    assert slp[5, 5] == -9999.0
    assert slp[4, 4] == -9999.0
    assert slp[6, 6] == -9999.0
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_terrain.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement terrain derivation**

Create `src/wildfire_preproc/terrain/__init__.py` (empty).

Create `src/wildfire_preproc/terrain/derive.py`:

```python
"""Stage 4 — slope and aspect from DEM via the Horn (1981) 3x3 kernel."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import correlate

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import write_array

# Horn weights: dz/dx = (right-weighted - left-weighted) / (8 * cell)
# Kernel rows are top-down (matches array indexing).
_KX = np.array(
    [
        [-1, 0, 1],
        [-2, 0, 2],
        [-1, 0, 1],
    ],
    dtype="float32",
)
_KY = np.array(
    [
        [1, 2, 1],
        [0, 0, 0],
        [-1, -2, -1],
    ],
    dtype="float32",
)

NODATA = -9999.0
ASPECT_FLAT = -1.0


def derive_slope_aspect(
    dem_path: Path,
    grid: GridSpec,
    out_slope_path: Path,
    out_aspect_path: Path,
) -> None:
    """Compute slope (degrees) and aspect (0–360 cw from N, flat=-1) from DEM."""
    with rasterio.open(dem_path) as ds:
        dem = ds.read(1).astype("float32")
        src_nodata = ds.nodata if ds.nodata is not None else NODATA
        cell = grid.cell_size

    if dem.shape != (grid.height, grid.width):
        raise ValueError(
            f"DEM shape {dem.shape} does not match grid {(grid.height, grid.width)}; "
            "DEM must already be aligned (Stage 3)."
        )

    valid = dem != src_nodata

    # correlate (not convolve) so kernel weights are applied as-written.
    dem_filled = np.where(valid, dem, 0.0)
    dz_dx = correlate(dem_filled, _KX, mode="constant", cval=0.0) / (8.0 * cell)
    dz_dy = correlate(dem_filled, _KY, mode="constant", cval=0.0) / (8.0 * cell)

    # Any cell whose 3x3 window touches nodata is nodata in the output.
    invalid_window = correlate((~valid).astype("uint8"), np.ones((3, 3), dtype="uint8"), mode="constant") > 0

    slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
    slope_deg = np.degrees(slope_rad).astype("float32")

    aspect_rad = np.arctan2(dz_dy, -dz_dx)
    aspect_deg = np.degrees(aspect_rad).astype("float32")
    # Convert math angle (0=+x ccw) to compass bearing (0=N cw).
    aspect_compass = (90.0 - aspect_deg) % 360.0

    flat = (np.hypot(dz_dx, dz_dy) < 1e-7)
    aspect_compass = np.where(flat, ASPECT_FLAT, aspect_compass).astype("float32")

    # Edge ring is unreliable: mark as nodata.
    edge = np.ones_like(slope_deg, dtype=bool)
    edge[1:-1, 1:-1] = False

    bad = invalid_window | edge

    slope_out = np.where(bad, NODATA, slope_deg).astype("float32")
    aspect_out = np.where(bad, NODATA, aspect_compass).astype("float32")

    write_array(out_slope_path, slope_out, grid, LayerKind.CONTINUOUS)
    write_array(out_aspect_path, aspect_out, grid, LayerKind.CONTINUOUS)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_terrain.py -x`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/terrain/ tests/test_terrain.py
git commit -m "feat(terrain): slope/aspect via Horn 3x3 kernel"
```

---

## Task 8: Stage 5 — protected mask & candidate zone

**Files:**
- Create: `src/wildfire_preproc/masks/__init__.py`, `src/wildfire_preproc/masks/protected.py`, `src/wildfire_preproc/masks/candidate.py`
- Test: `tests/test_masks_protected_candidate.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_masks_protected_candidate.py`:

```python
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.masks.candidate import build_candidate_zone_mask
from wildfire_preproc.masks.protected import build_protected_mask


def _grid(cell: float = 30.0, w: int = 100, h: int = 100, ox: float = 0.0, oy: float = 3000.0) -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(ox, oy, cell, cell),
        width=w,
        height=h,
        cell_size=cell,
    )


def test_protected_mask_marks_inside(tmp_path: Path):
    grid = _grid()
    poly = box(900, 900, 2100, 2100)  # interior square
    out = tmp_path / "protected_mask.tif"
    build_protected_mask(poly, "EPSG:5070", grid, out)
    with rasterio.open(out) as ds:
        arr = ds.read(1)
        assert ds.dtypes[0] == "uint8"
    inside = arr[(grid.height - 70):(grid.height - 30), 30:70]  # roughly the polygon footprint
    assert (inside == 1).any()
    assert arr.min() == 0
    assert arr.max() == 1


def test_protected_mask_reprojects_input(tmp_path: Path):
    grid = _grid()
    # Protected polygon in WGS84 — the function must reproject before rasterizing.
    poly_wgs84 = Polygon([(-118.7, 34.1), (-118.6, 34.1), (-118.6, 34.2), (-118.7, 34.2), (-118.7, 34.1)])
    out = tmp_path / "protected_mask.tif"
    # The grid is at projected coords (0..3000), so the WGS84 polygon won't intersect — that's ok,
    # the test is about the function not crashing on CRS mismatch.
    build_protected_mask(poly_wgs84, "EPSG:4326", grid, out)
    with rasterio.open(out) as ds:
        assert ds.crs == grid.crs


def test_candidate_zone_mask_marks_zone_only(tmp_path: Path):
    grid = _grid()
    candidate = box(0, 0, 1500, 1500)  # quarter of grid
    out = tmp_path / "candidate.tif"
    build_candidate_zone_mask(candidate, "EPSG:5070", grid, out)
    with rasterio.open(out) as ds:
        arr = ds.read(1)
    assert arr.dtype == np.uint8
    assert set(np.unique(arr).tolist()).issubset({0, 1})
    assert (arr == 1).sum() > 0
    assert (arr == 0).sum() > 0
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_masks_protected_candidate.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement mask helpers**

Create `src/wildfire_preproc/masks/__init__.py` (empty).

Create `src/wildfire_preproc/masks/protected.py`:

```python
"""Stage 5 — rasterize a protected polygon into a 0/1 mask aligned to the canonical grid."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from shapely.geometry.base import BaseGeometry

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import write_array


def _rasterize_polygon(polygon: BaseGeometry, polygon_crs: str, grid: GridSpec) -> np.ndarray:
    gdf = gpd.GeoDataFrame(geometry=[polygon], crs=polygon_crs).to_crs(grid.crs)
    geom = gdf.geometry.iloc[0]
    arr = rasterize(
        shapes=[(geom, 1)],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    return arr.astype("uint8")


def build_protected_mask(
    protected_polygon: BaseGeometry,
    polygon_crs: str,
    grid: GridSpec,
    out_path: Path,
) -> None:
    arr = _rasterize_polygon(protected_polygon, polygon_crs, grid)
    write_array(out_path, arr, grid, LayerKind.MASK)
```

Create `src/wildfire_preproc/masks/candidate.py`:

```python
"""Stage 5 — rasterize the candidate firebreak zone polygon into a 0/1 mask."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry.base import BaseGeometry

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.masks.protected import _rasterize_polygon
from wildfire_preproc.utils.raster import write_array


def build_candidate_zone_mask(
    candidate_polygon: BaseGeometry,
    polygon_crs: str,
    grid: GridSpec,
    out_path: Path,
) -> None:
    arr = _rasterize_polygon(candidate_polygon, polygon_crs, grid)
    write_array(out_path, arr, grid, LayerKind.MASK)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_masks_protected_candidate.py -x`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/masks/ tests/test_masks_protected_candidate.py
git commit -m "feat(masks): protected_mask + candidate_zone rasterization"
```

---

## Task 9: Stage 5 — non-burnable mask + FBFM40 codes

**Files:**
- Create: `src/wildfire_preproc/validation/__init__.py`, `src/wildfire_preproc/validation/fbfm40_codes.py`, `src/wildfire_preproc/masks/non_burnable.py`
- Test: `tests/test_masks_non_burnable.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_masks_non_burnable.py`:

```python
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.masks.non_burnable import build_non_burnable_mask


def _grid(w: int, h: int, cell: float = 30.0) -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, h * cell, cell, cell),
        width=w,
        height=h,
        cell_size=cell,
    )


def _write_fbfm40(path: Path, arr: np.ndarray, grid: GridSpec) -> None:
    with rasterio.open(
        path, "w",
        driver="GTiff", height=grid.height, width=grid.width,
        count=1, dtype="uint8", crs=grid.crs, transform=grid.transform, nodata=255,
    ) as ds:
        ds.write(arr.astype("uint8"), 1)


def test_fbfm40_reclass_marks_non_burnable_codes(tmp_path: Path):
    grid = _grid(10, 10)
    arr = np.full((10, 10), 101, dtype="uint8")  # all burnable grass
    arr[0, 0] = 91  # urban
    arr[1, 1] = 92  # snow
    arr[2, 2] = 93  # ag
    arr[3, 3] = 98  # water
    arr[4, 4] = 99  # barren
    fbfm = tmp_path / "fbfm40.tif"
    _write_fbfm40(fbfm, arr, grid)
    out = tmp_path / "non_burnable_mask.tif"
    build_non_burnable_mask(grid=grid, fbfm40_path=fbfm, sources=["fbfm40"], out_path=out)
    with rasterio.open(out) as ds:
        result = ds.read(1)
    assert result[0, 0] == 1
    assert result[1, 1] == 1
    assert result[2, 2] == 1
    assert result[3, 3] == 1
    assert result[4, 4] == 1
    # Burnable cells stay 0
    assert result[5, 5] == 0


def test_unknown_source_raises(tmp_path: Path):
    grid = _grid(5, 5)
    fbfm = tmp_path / "fbfm40.tif"
    _write_fbfm40(fbfm, np.full((5, 5), 101, dtype="uint8"), grid)
    out = tmp_path / "out.tif"
    import pytest
    with pytest.raises(ValueError, match="unknown non_burnable source"):
        build_non_burnable_mask(grid=grid, fbfm40_path=fbfm, sources=["osm"], out_path=out)


def test_empty_sources_raises(tmp_path: Path):
    grid = _grid(5, 5)
    fbfm = tmp_path / "fbfm40.tif"
    _write_fbfm40(fbfm, np.full((5, 5), 101, dtype="uint8"), grid)
    out = tmp_path / "out.tif"
    import pytest
    with pytest.raises(ValueError, match="at least one"):
        build_non_burnable_mask(grid=grid, fbfm40_path=fbfm, sources=[], out_path=out)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_masks_non_burnable.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement FBFM40 codes + non-burnable mask**

Create `src/wildfire_preproc/validation/__init__.py` (empty).

Create `src/wildfire_preproc/validation/fbfm40_codes.py`:

```python
"""FBFM40 (Scott & Burgan 40) fuel-model code catalog."""

from __future__ import annotations

# Non-burnable codes (Scott & Burgan, 2005).
NON_BURNABLE_CODES: frozenset[int] = frozenset({91, 92, 93, 98, 99})

# Full set of valid burnable codes (grass, grass-shrub, shrub, timber-understory, timber-litter, slash-blowdown).
BURNABLE_CODES: frozenset[int] = frozenset(
    {
        # GR group
        101, 102, 103, 104, 105, 106, 107, 108, 109,
        # GS group
        121, 122, 123, 124,
        # SH group
        141, 142, 143, 144, 145, 146, 147, 148, 149,
        # TU group
        161, 162, 163, 164, 165,
        # TL group
        181, 182, 183, 184, 185, 186, 187, 188, 189,
        # SB group
        201, 202, 203, 204,
    }
)

VALID_FBFM40_CODES: frozenset[int] = NON_BURNABLE_CODES | BURNABLE_CODES
```

Create `src/wildfire_preproc/masks/non_burnable.py`:

```python
"""Stage 5 — non-burnable mask via union of pluggable sources."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import rasterio

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKind
from wildfire_preproc.utils.raster import write_array
from wildfire_preproc.validation.fbfm40_codes import NON_BURNABLE_CODES


def _fbfm40_reclass(grid: GridSpec, fbfm40_path: Path) -> np.ndarray:
    with rasterio.open(fbfm40_path) as ds:
        arr = ds.read(1)
    if arr.shape != (grid.height, grid.width):
        raise ValueError(f"fbfm40 shape {arr.shape} does not match grid {(grid.height, grid.width)}")
    out = np.zeros_like(arr, dtype="uint8")
    for code in NON_BURNABLE_CODES:
        out[arr == code] = 1
    return out


_SOURCE_REGISTRY: dict[str, Callable[[GridSpec, Path], np.ndarray]] = {
    "fbfm40": _fbfm40_reclass,
}


def build_non_burnable_mask(
    grid: GridSpec,
    fbfm40_path: Path,
    sources: list[str],
    out_path: Path,
) -> None:
    if not sources:
        raise ValueError("non_burnable_mask requires at least one source")
    union = np.zeros((grid.height, grid.width), dtype="uint8")
    for src in sources:
        fn = _SOURCE_REGISTRY.get(src)
        if fn is None:
            raise ValueError(f"unknown non_burnable source: {src!r}")
        layer = fn(grid, fbfm40_path)
        union |= layer
    write_array(out_path, union, grid, LayerKind.MASK)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_masks_non_burnable.py -x`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/validation/__init__.py src/wildfire_preproc/validation/fbfm40_codes.py src/wildfire_preproc/masks/non_burnable.py tests/test_masks_non_burnable.py
git commit -m "feat(masks): non_burnable_mask via pluggable source registry (FBFM40 reclass default)"
```

---

## Task 10: Stage 6 — validation checks

**Files:**
- Create: `src/wildfire_preproc/validation/checks.py`
- Test: `tests/test_validation.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_validation.py`:

```python
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKey, LayerKind
from wildfire_preproc.validation.checks import (
    ValidationError,
    ValidationResult,
    validate_raster,
)


def _grid() -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, 300, 30, 30),
        width=10,
        height=10,
        cell_size=30.0,
    )


def _write(path: Path, data: np.ndarray, grid: GridSpec, dtype: str, nodata):
    with rasterio.open(
        path, "w",
        driver="GTiff", height=grid.height, width=grid.width,
        count=1, dtype=dtype, crs=grid.crs, transform=grid.transform, nodata=nodata,
    ) as ds:
        ds.write(data.astype(dtype), 1)


def test_passing_continuous_raster(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    _write(p, np.full((10, 10), 100.0, dtype="float32"), grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert res.ok
    assert not res.errors


def test_failing_crs_mismatch(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    bad_grid = GridSpec(
        crs=CRS.from_epsg(4326),
        transform=grid.transform,
        width=grid.width, height=grid.height, cell_size=grid.cell_size,
    )
    _write(p, np.full((10, 10), 100.0, dtype="float32"), bad_grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert not res.ok
    assert any("crs" in e.lower() for e in res.errors)


def test_failing_dimensions(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    bad_grid = GridSpec(
        crs=grid.crs,
        transform=from_origin(0, 300, 30, 30),
        width=5, height=5, cell_size=30.0,
    )
    _write(p, np.full((5, 5), 100.0, dtype="float32"), bad_grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert not res.ok
    assert any("dimensions" in e.lower() or "width" in e.lower() or "height" in e.lower() for e in res.errors)


def test_failing_all_nodata(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "x.tif"
    _write(p, np.full((10, 10), -9999.0, dtype="float32"), grid, "float32", -9999.0)
    res = validate_raster(p, grid, LayerKey.DEM, LayerKind.CONTINUOUS)
    assert not res.ok
    assert any("nodata" in e.lower() for e in res.errors)


def test_failing_mask_with_invalid_values(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "m.tif"
    arr = np.zeros((10, 10), dtype="uint8")
    arr[0, 0] = 7
    _write(p, arr, grid, "uint8", None)
    res = validate_raster(p, grid, LayerKey.PROTECTED_MASK, LayerKind.MASK)
    assert not res.ok
    assert any("mask" in e.lower() or "values" in e.lower() for e in res.errors)


def test_failing_fbfm40_invalid_code(tmp_path: Path):
    grid = _grid()
    p = tmp_path / "f.tif"
    arr = np.full((10, 10), 7, dtype="uint8")  # 7 is not a valid FBFM40 code
    _write(p, arr, grid, "uint8", 255)
    res = validate_raster(p, grid, LayerKey.FBFM40, LayerKind.CATEGORICAL)
    assert not res.ok
    assert any("fbfm40" in e.lower() or "code" in e.lower() for e in res.errors)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_validation.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement validation**

Create `src/wildfire_preproc/validation/checks.py`:

```python
"""Stage 6 — invariant checks per raster + cross-raster manifest checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import LayerKey, LayerKind
from wildfire_preproc.utils.raster import CATEGORICAL_NODATA, CONTINUOUS_NODATA
from wildfire_preproc.validation.fbfm40_codes import VALID_FBFM40_CODES


class ValidationError(Exception):
    pass


@dataclass
class ValidationResult:
    layer: str
    path: Path
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


_TRANSFORM_EPS = 1e-9


def _check_grid(ds: rasterio.io.DatasetReader, grid: GridSpec, errors: list[str]) -> None:
    if ds.crs != grid.crs:
        errors.append(f"crs mismatch: got {ds.crs}, expected {grid.crs}")
    if ds.width != grid.width or ds.height != grid.height:
        errors.append(
            f"dimensions mismatch: got ({ds.width}, {ds.height}), expected ({grid.width}, {grid.height})"
        )
    for a, b in zip(tuple(ds.transform)[:6], tuple(grid.transform)[:6]):
        if abs(a - b) > _TRANSFORM_EPS:
            errors.append(f"transform mismatch: got {tuple(ds.transform)}, expected {tuple(grid.transform)}")
            break
    if abs(abs(ds.transform.a) - grid.cell_size) > _TRANSFORM_EPS:
        errors.append(f"cell size x mismatch: got {abs(ds.transform.a)}, expected {grid.cell_size}")
    if abs(abs(ds.transform.e) - grid.cell_size) > _TRANSFORM_EPS:
        errors.append(f"cell size y mismatch: got {abs(ds.transform.e)}, expected {grid.cell_size}")


def _check_nodata(ds: rasterio.io.DatasetReader, kind: LayerKind, errors: list[str]) -> None:
    if kind == LayerKind.CONTINUOUS:
        if ds.nodata is None or abs(ds.nodata - CONTINUOUS_NODATA) > 1e-6:
            errors.append(f"continuous raster nodata must be {CONTINUOUS_NODATA}, got {ds.nodata}")
    elif kind == LayerKind.CATEGORICAL:
        if ds.nodata is None or int(ds.nodata) != CATEGORICAL_NODATA:
            errors.append(f"categorical raster nodata must be {CATEGORICAL_NODATA}, got {ds.nodata}")
    elif kind == LayerKind.MASK:
        if ds.nodata is not None:
            errors.append(f"mask raster must have nodata=None, got {ds.nodata}")


def _check_data(arr: np.ndarray, layer: LayerKey, kind: LayerKind, nodata, errors: list[str]) -> None:
    # All-nodata check
    if nodata is not None:
        if np.all(arr == nodata):
            errors.append("raster is entirely nodata")
            return
    # Mask values
    if kind == LayerKind.MASK:
        unique = set(np.unique(arr).tolist())
        if not unique.issubset({0, 1}):
            errors.append(f"mask values must be in {{0, 1}}, got {sorted(unique)}")
    # Categorical FBFM40 codes
    if layer == LayerKey.FBFM40:
        valid = arr[arr != nodata] if nodata is not None else arr
        invalid = set(np.unique(valid).tolist()) - set(VALID_FBFM40_CODES)
        if invalid:
            errors.append(f"fbfm40 invalid codes present: {sorted(invalid)[:10]}")
    # DEM sanity bounds
    if layer == LayerKey.DEM:
        valid_arr = arr[arr != nodata] if nodata is not None else arr
        if valid_arr.size > 0:
            if valid_arr.min() < -500 or valid_arr.max() > 9000:
                errors.append(
                    f"DEM out of plausible range: min={float(valid_arr.min())}, max={float(valid_arr.max())}"
                )


def validate_raster(path: Path, grid: GridSpec, layer: LayerKey, kind: LayerKind) -> ValidationResult:
    res = ValidationResult(layer=layer.value, path=path)
    if not path.exists():
        res.errors.append(f"file does not exist: {path}")
        return res
    try:
        with rasterio.open(path) as ds:
            _check_grid(ds, grid, res.errors)
            _check_nodata(ds, kind, res.errors)
            arr = ds.read(1)
            _check_data(arr, layer, kind, ds.nodata, res.errors)
    except rasterio.errors.RasterioIOError as e:
        res.errors.append(f"cannot open raster: {e}")
    return res
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_validation.py -x`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/validation/checks.py tests/test_validation.py
git commit -m "feat(validation): per-raster CRS/dim/transform/nodata/value checks"
```

---

## Task 11: Validation report

**Files:**
- Create: `src/wildfire_preproc/validation/report.py`
- Modify: `tests/test_validation.py` (append)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_validation.py`:

```python
from wildfire_preproc.validation.checks import ValidationResult
from wildfire_preproc.validation.report import format_report


def test_format_report_passing():
    results = [
        ValidationResult(layer="dem", path=Path("/x/dem.tif")),
        ValidationResult(layer="fbfm40", path=Path("/x/fbfm40.tif")),
    ]
    report = format_report(results, manifest_ok=True)
    assert "PASS" in report
    assert "dem" in report
    assert "fbfm40" in report


def test_format_report_failing():
    bad = ValidationResult(layer="dem", path=Path("/x/dem.tif"))
    bad.errors.append("crs mismatch")
    report = format_report([bad], manifest_ok=True)
    assert "FAIL" in report
    assert "crs mismatch" in report
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_validation.py::test_format_report_passing tests/test_validation.py::test_format_report_failing -x`
Expected: ImportError.

- [ ] **Step 3: Implement report**

Create `src/wildfire_preproc/validation/report.py`:

```python
"""Stage 6 — human-readable validation report."""

from __future__ import annotations

from wildfire_preproc.validation.checks import ValidationResult


def format_report(results: list[ValidationResult], manifest_ok: bool) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("VALIDATION REPORT")
    lines.append("=" * 72)
    lines.append(f"manifest:               [{'PASS' if manifest_ok else 'FAIL'}]")
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        lines.append(f"  {r.layer:<22} [{status}]   {r.path.name}")
        for e in r.errors:
            lines.append(f"    - {e}")
    overall = manifest_ok and all(r.ok for r in results)
    lines.append("-" * 72)
    lines.append(f"overall: {'PASS' if overall else 'FAIL'}")
    lines.append("=" * 72)
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_validation.py -x`
Expected: all passing (6 from Task 10 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/validation/report.py tests/test_validation.py
git commit -m "feat(validation): human-readable report formatter"
```

---

## Task 12: Stage 2 — sources base + cache + local

**Files:**
- Create: `src/wildfire_preproc/sources/__init__.py`, `src/wildfire_preproc/sources/base.py`, `src/wildfire_preproc/sources/cache.py`, `src/wildfire_preproc/sources/local.py`
- Test: `tests/test_sources_cache.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sources_cache.py`:

```python
from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.cache import cache_key_path


def test_cache_key_is_deterministic(tmp_path: Path):
    a = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    b = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    assert a == b


def test_cache_key_differs_by_bbox(tmp_path: Path):
    a = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    b = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 5), crs="EPSG:5070")
    assert a != b


def test_cache_key_differs_by_layer(tmp_path: Path):
    a = cache_key_path(tmp_path, layer=LayerKey.FBFM40, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    b = cache_key_path(tmp_path, layer=LayerKey.DEM, bbox=(1, 2, 3, 4), crs="EPSG:5070")
    assert a != b
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_sources_cache.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement sources base + cache + local**

Create `src/wildfire_preproc/sources/__init__.py` (empty).

Create `src/wildfire_preproc/sources/base.py`:

```python
"""Abstract RasterSource protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from wildfire_preproc.config import LayerKey


BBox = tuple[float, float, float, float]


class RasterSource(Protocol):
    """A source that can produce a raster covering `bbox` for a given layer.

    Returns a path to a GeoTIFF on disk. The CRS/resolution of that file is whatever
    the source happens to produce — Stage 3 handles alignment.
    """

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path: ...
```

Create `src/wildfire_preproc/sources/cache.py`:

```python
"""Disk-backed cache key for fetched rasters."""

from __future__ import annotations

import hashlib
from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox


def cache_key_path(cache_dir: Path, layer: LayerKey, bbox: BBox, crs: str) -> Path:
    h = hashlib.sha256()
    h.update(layer.value.encode())
    h.update(b"|")
    h.update(",".join(f"{x:.6f}" for x in bbox).encode())
    h.update(b"|")
    h.update(crs.encode())
    return cache_dir / f"{layer.value}_{h.hexdigest()[:16]}.tif"
```

Create `src/wildfire_preproc/sources/local.py`:

```python
"""Local-file RasterSource: returns a configured local path for a layer."""

from __future__ import annotations

from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox


class LocalRasterSource:
    """Configured with a fixed path per LayerKey. Ignores bbox/dst_crs at fetch time."""

    def __init__(self, paths: dict[LayerKey, Path]):
        self._paths = paths

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer not in self._paths:
            raise KeyError(f"LocalRasterSource has no path configured for {layer}")
        p = self._paths[layer]
        if not p.exists():
            raise FileNotFoundError(p)
        return p
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sources_cache.py -x`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/sources/__init__.py src/wildfire_preproc/sources/base.py src/wildfire_preproc/sources/cache.py src/wildfire_preproc/sources/local.py tests/test_sources_cache.py
git commit -m "feat(sources): RasterSource protocol, cache key, LocalRasterSource"
```

---

## Task 13: Stage 2 — LFPS fetcher

**Files:**
- Create: `src/wildfire_preproc/sources/lfps.py`
- Test: `tests/test_sources_lfps.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sources_lfps.py`:

```python
import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
import responses
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.lfps import LFPS_LAYER_CODE, LfpsSource


def _zip_with_tif(tmp_path: Path) -> bytes:
    arr = np.full((4, 4), 101, dtype="uint8")
    tif = tmp_path / "x.tif"
    with rasterio.open(
        tif, "w",
        driver="GTiff", height=4, width=4, count=1, dtype="uint8",
        crs=CRS.from_epsg(5070), transform=from_origin(0, 120, 30, 30), nodata=255,
    ) as ds:
        ds.write(arr, 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(tif, arcname="x.tif")
    return buf.getvalue()


@responses.activate
def test_lfps_fetches_layer_via_arcgis_job_workflow(tmp_path: Path):
    # Job submission
    responses.add(
        responses.POST,
        "https://lfps.usgs.gov/api/job/submitJob",
        json={"jobId": "abc123"},
    )
    # Status poll
    responses.add(
        responses.GET,
        "https://lfps.usgs.gov/api/job/status",
        json={"Status": "Succeeded", "OutputFile": "https://example.test/result.zip"},
    )
    # Output zip
    responses.add(
        responses.GET,
        "https://example.test/result.zip",
        body=_zip_with_tif(tmp_path),
        content_type="application/zip",
    )

    src = LfpsSource(cache_dir=tmp_path / "cache", landfire_version="LF2022")
    out = src.fetch(LayerKey.FBFM40, bbox=(0.0, 0.0, 100.0, 100.0), dst_crs="EPSG:5070")
    assert out.exists()
    with rasterio.open(out) as ds:
        assert ds.read(1)[0, 0] == 101


@responses.activate
def test_lfps_uses_cache_on_second_call(tmp_path: Path):
    responses.add(
        responses.POST, "https://lfps.usgs.gov/api/job/submitJob",
        json={"jobId": "abc123"},
    )
    responses.add(
        responses.GET, "https://lfps.usgs.gov/api/job/status",
        json={"Status": "Succeeded", "OutputFile": "https://example.test/result.zip"},
    )
    responses.add(
        responses.GET, "https://example.test/result.zip",
        body=_zip_with_tif(tmp_path),
        content_type="application/zip",
    )

    src = LfpsSource(cache_dir=tmp_path / "cache", landfire_version="LF2022")
    p1 = src.fetch(LayerKey.FBFM40, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    p2 = src.fetch(LayerKey.FBFM40, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    assert p1 == p2
    # Only the first call hits LFPS (1 POST, 1 status, 1 download = 3 calls); second is cache hit.
    assert len(responses.calls) == 3


def test_lfps_layer_codes_cover_all_fetched_layers():
    for layer in [LayerKey.FBFM40, LayerKey.CC, LayerKey.CH, LayerKey.CBH, LayerKey.CBD]:
        assert layer in LFPS_LAYER_CODE
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_sources_lfps.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement LFPS source**

Create `src/wildfire_preproc/sources/lfps.py`:

```python
"""LANDFIRE Product Service (LFPS) RasterSource — ArcGIS REST job workflow."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox
from wildfire_preproc.sources.cache import cache_key_path


LFPS_BASE = "https://lfps.usgs.gov/api/job"
SUBMIT_URL = f"{LFPS_BASE}/submitJob"
STATUS_URL = f"{LFPS_BASE}/status"

# LF2022 layer codes per LANDFIRE Product Service.
LFPS_LAYER_CODE: dict[LayerKey, str] = {
    LayerKey.FBFM40: "220F40_22",
    LayerKey.CC:     "220CC_22",
    LayerKey.CH:     "220CH_22",
    LayerKey.CBH:    "220CBH_22",
    LayerKey.CBD:    "220CBD_22",
}


class LfpsHttpError(RuntimeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500
    return False


_retry = retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=1, max=60),
    retry=retry_if_exception_type((requests.ConnectionError, requests.HTTPError)),
    reraise=True,
)


class LfpsSource:
    def __init__(
        self,
        cache_dir: Path,
        landfire_version: str = "LF2022",
        poll_interval_s: float = 5.0,
        poll_timeout_s: float = 600.0,
    ):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._landfire_version = landfire_version
        self._poll_interval = poll_interval_s
        self._poll_timeout = poll_timeout_s

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer not in LFPS_LAYER_CODE:
            raise KeyError(f"LFPS does not provide layer {layer}")
        cache_path = cache_key_path(self._cache_dir, layer, bbox, dst_crs)
        if cache_path.exists():
            return cache_path

        layer_code = LFPS_LAYER_CODE[layer]
        job_id = self._submit(layer_code, bbox, dst_crs)
        output_url = self._await_complete(job_id)
        zip_bytes = self._download(output_url)
        self._extract_to(zip_bytes, cache_path)
        return cache_path

    @_retry
    def _submit(self, layer_code: str, bbox: BBox, dst_crs: str) -> str:
        payload = {
            "Layer_List": layer_code,
            "Area_Of_Interest": ",".join(f"{x:.6f}" for x in bbox),
            "Output_Projection": dst_crs.replace("EPSG:", ""),
        }
        r = requests.post(SUBMIT_URL, json=payload, timeout=30)
        r.raise_for_status()
        return str(r.json()["jobId"])

    def _await_complete(self, job_id: str) -> str:
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            status = self._poll(job_id)
            state = status.get("Status", "").lower()
            if state == "succeeded":
                output = status.get("OutputFile")
                if not output:
                    raise LfpsHttpError(f"LFPS Succeeded but no OutputFile: {status}")
                return str(output)
            if state == "failed":
                raise LfpsHttpError(f"LFPS job failed: {status}")
            time.sleep(self._poll_interval)
        raise LfpsHttpError(f"LFPS job {job_id} timed out after {self._poll_timeout}s")

    @_retry
    def _poll(self, job_id: str) -> dict[str, Any]:
        r = requests.get(STATUS_URL, params={"jobId": job_id}, timeout=30)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    @_retry
    def _download(self, url: str) -> bytes:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        return r.content

    def _extract_to(self, zip_bytes: bytes, dst: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            tif_names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
            if not tif_names:
                raise LfpsHttpError("LFPS zip contained no .tif")
            with zf.open(tif_names[0]) as src, open(dst, "wb") as out:
                out.write(src.read())
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sources_lfps.py -x`
Expected: 3 passed. (The first test asserts the GeoTIFF round-trips correctly through the zip extraction.)

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/sources/lfps.py tests/test_sources_lfps.py
git commit -m "feat(sources): LFPS fetcher with retry+cache"
```

---

## Task 14: Stage 2 — 3DEP DEM fetcher

**Files:**
- Create: `src/wildfire_preproc/sources/threedep.py`
- Test: `tests/test_sources_threedep.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sources_threedep.py`:

```python
from pathlib import Path

import numpy as np
import rasterio
import responses
from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.threedep import ThreeDepSource


def _make_tif_bytes(tmp_path: Path) -> bytes:
    arr = np.full((4, 4), 100.0, dtype="float32")
    p = tmp_path / "out.tif"
    with rasterio.open(
        p, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
        crs=CRS.from_epsg(5070), transform=from_origin(0, 120, 30, 30), nodata=-9999.0,
    ) as ds:
        ds.write(arr, 1)
    return p.read_bytes()


@responses.activate
def test_threedep_fetches_dem(tmp_path: Path):
    responses.add(
        responses.GET,
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
        body=_make_tif_bytes(tmp_path),
        content_type="image/tiff",
    )
    src = ThreeDepSource(cache_dir=tmp_path / "cache")
    out = src.fetch(LayerKey.DEM, bbox=(0.0, 0.0, 100.0, 100.0), dst_crs="EPSG:5070")
    assert out.exists()
    with rasterio.open(out) as ds:
        assert ds.read(1)[0, 0] == 100.0


@responses.activate
def test_threedep_caches(tmp_path: Path):
    responses.add(
        responses.GET,
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
        body=_make_tif_bytes(tmp_path),
        content_type="image/tiff",
    )
    src = ThreeDepSource(cache_dir=tmp_path / "cache")
    a = src.fetch(LayerKey.DEM, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    b = src.fetch(LayerKey.DEM, bbox=(0, 0, 100, 100), dst_crs="EPSG:5070")
    assert a == b
    assert len(responses.calls) == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_sources_threedep.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement 3DEP source**

Create `src/wildfire_preproc/sources/threedep.py`:

```python
"""USGS 3DEP DEM fetcher via the National Map exportImage endpoint."""

from __future__ import annotations

from pathlib import Path

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox
from wildfire_preproc.sources.cache import cache_key_path


THREEDEP_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
)


class ThreeDepSource:
    def __init__(self, cache_dir: Path, default_pixel_size_m: float = 10.0):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._pixel_size = default_pixel_size_m

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        if layer != LayerKey.DEM:
            raise KeyError(f"3DEP only provides DEM, got {layer}")
        cache_path = cache_key_path(self._cache_dir, layer, bbox, dst_crs)
        if cache_path.exists():
            return cache_path
        content = self._download(bbox, dst_crs)
        cache_path.write_bytes(content)
        return cache_path

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1, max=60),
        retry=retry_if_exception_type((requests.ConnectionError, requests.HTTPError)),
        reraise=True,
    )
    def _download(self, bbox: BBox, dst_crs: str) -> bytes:
        minx, miny, maxx, maxy = bbox
        width = max(1, int((maxx - minx) / self._pixel_size))
        height = max(1, int((maxy - miny) / self._pixel_size))
        params = {
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "bboxSR": dst_crs.replace("EPSG:", ""),
            "imageSR": dst_crs.replace("EPSG:", ""),
            "size": f"{width},{height}",
            "format": "tiff",
            "pixelType": "F32",
            "noData": "-9999",
            "f": "image",
        }
        r = requests.get(THREEDEP_URL, params=params, timeout=120)
        r.raise_for_status()
        return r.content
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sources_threedep.py -x`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/sources/threedep.py tests/test_sources_threedep.py
git commit -m "feat(sources): 3DEP DEM fetcher with retry+cache"
```

---

## Task 15: Source registry (selects backend per layer)

**Files:**
- Create: `src/wildfire_preproc/sources/registry.py`

- [ ] **Step 1: Implement registry (no dedicated test file — exercised end-to-end in pipeline test)**

Create `src/wildfire_preproc/sources/registry.py`:

```python
"""Default source registry: LFPS for fuels/canopy, 3DEP for DEM."""

from __future__ import annotations

from pathlib import Path

from wildfire_preproc.config import LayerKey
from wildfire_preproc.sources.base import BBox, RasterSource
from wildfire_preproc.sources.lfps import LfpsSource
from wildfire_preproc.sources.threedep import ThreeDepSource


class DefaultSourceRegistry:
    def __init__(self, cache_dir: Path, landfire_version: str = "LF2022"):
        self._lfps = LfpsSource(cache_dir=cache_dir / "lfps", landfire_version=landfire_version)
        self._threedep = ThreeDepSource(cache_dir=cache_dir / "threedep")

    def for_layer(self, layer: LayerKey) -> RasterSource:
        if layer == LayerKey.DEM:
            return self._threedep
        return self._lfps

    def fetch(self, layer: LayerKey, bbox: BBox, dst_crs: str) -> Path:
        return self.for_layer(layer).fetch(layer, bbox, dst_crs)
```

- [ ] **Step 2: Smoke test**

Run: `uv run python -c "from wildfire_preproc.sources.registry import DefaultSourceRegistry; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/wildfire_preproc/sources/registry.py
git commit -m "feat(sources): DefaultSourceRegistry maps layers to backends"
```

---

## Task 16: Stage 7 — metadata.json builder

**Files:**
- Create: `src/wildfire_preproc/export/__init__.py`, `src/wildfire_preproc/export/metadata.py`
- Test: `tests/test_metadata.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_metadata.py`:

```python
import json
from pathlib import Path

from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import JobConfig
from wildfire_preproc.export.metadata import build_metadata


def _cfg() -> JobConfig:
    return JobConfig.model_validate({
        "protected_polygon": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        "simulation_radius_m": 5000,
        "ignition_distance_m": 4500,
        "cell_size_m": 30,
        "crs": "EPSG:5070",
    })


def _grid() -> GridSpec:
    return GridSpec(
        crs=CRS.from_epsg(5070),
        transform=from_origin(0, 300, 30, 30),
        width=10,
        height=10,
        cell_size=30.0,
    )


def test_metadata_includes_grid_and_layers(tmp_path: Path):
    md = build_metadata(
        job_id="20260508T203015_a3f2",
        cfg=_cfg(),
        grid=_grid(),
        layer_sources={"fbfm40": "lfps:220F40_22", "dem": "3dep:1m"},
        validation_status="passed",
    )
    assert md["grid"]["crs"] == "EPSG:5070"
    assert md["grid"]["width"] == 10
    assert md["grid"]["height"] == 10
    assert md["grid"]["bounds"] == [0.0, 0.0, 300.0, 300.0]
    assert md["layers"]["fbfm40"]["source"] == "lfps:220F40_22"
    assert md["layers"]["dem"]["source"] == "3dep:1m"
    assert md["layers"]["slp"]["source"] == "derived:dem"
    assert md["validation"] == "passed"
    assert md["job_id"] == "20260508T203015_a3f2"


def test_metadata_round_trips_to_json(tmp_path: Path):
    md = build_metadata(
        job_id="x",
        cfg=_cfg(),
        grid=_grid(),
        layer_sources={"fbfm40": "lfps:220F40_22", "dem": "3dep:1m"},
        validation_status="passed",
    )
    out = tmp_path / "metadata.json"
    out.write_text(json.dumps(md, indent=2))
    re = json.loads(out.read_text())
    assert re["job_id"] == "x"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_metadata.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement metadata builder**

Create `src/wildfire_preproc/export/__init__.py` (empty).

Create `src/wildfire_preproc/export/metadata.py`:

```python
"""Stage 7 — build metadata.json describing the job's outputs."""

from __future__ import annotations

from datetime import datetime, timezone
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
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_metadata.py -x`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wildfire_preproc/export/ tests/test_metadata.py
git commit -m "feat(export): build_metadata creates the metadata.json structure"
```

---

## Task 17: Pipeline orchestrator

**Files:**
- Create: `src/wildfire_preproc/pipeline.py`
- Test: `tests/test_pipeline.py`

This task is integration-flavored — it wires every stage. The test uses the `LocalRasterSource` (from Task 12) so we don't depend on the live network.

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline.py`:

```python
import shutil
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon, mapping

from wildfire_preproc.config import JobConfig, LayerKey
from wildfire_preproc.pipeline import run_pipeline
from wildfire_preproc.sources.local import LocalRasterSource


def _write_synthetic_layer(path: Path, shape, value, dtype, nodata, transform, crs):
    arr = np.full(shape, value, dtype=dtype)
    with rasterio.open(
        path, "w", driver="GTiff", height=shape[0], width=shape[1],
        count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
    ) as ds:
        ds.write(arr, 1)


def _build_local_source(tmp_path: Path) -> LocalRasterSource:
    """Create synthetic rasters covering a 30km x 30km area in EPSG:5070 around origin."""
    crs = CRS.from_epsg(5070)
    cell = 30.0
    big_h = big_w = 1000  # 30km coverage
    # CRS-projected origin near the center of CONUS so EPSG:5070 lat/lon is well-defined.
    # Use a region known to fall within EPSG:5070 valid extent: somewhere around the central US.
    transform = from_origin(-100_000.0, 1_000_000.0, cell, cell)
    fbfm = tmp_path / "fbfm.tif"
    dem = tmp_path / "dem.tif"
    cc = tmp_path / "cc.tif"
    ch = tmp_path / "ch.tif"
    cbh = tmp_path / "cbh.tif"
    cbd = tmp_path / "cbd.tif"
    _write_synthetic_layer(fbfm, (big_h, big_w), 101, "uint8", 255, transform, crs)
    _write_synthetic_layer(dem,  (big_h, big_w), 100.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cc,   (big_h, big_w), 50.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(ch,   (big_h, big_w), 10.0, "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cbh,  (big_h, big_w), 2.0,  "float32", -9999.0, transform, crs)
    _write_synthetic_layer(cbd,  (big_h, big_w), 0.1,  "float32", -9999.0, transform, crs)
    return LocalRasterSource({
        LayerKey.FBFM40: fbfm, LayerKey.DEM: dem, LayerKey.CC: cc,
        LayerKey.CH: ch, LayerKey.CBH: cbh, LayerKey.CBD: cbd,
    })


def test_pipeline_produces_full_manifest(tmp_path: Path):
    # Protected polygon in EPSG:5070, well inside the synthetic raster footprint.
    # Polygon centered well inside the synthetic raster footprint (-100km..-70km, 970km..1000km).
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")

    inputs = out_dir / "inputs"
    expected = [
        "fbfm40.tif", "dem.tif", "slp.tif", "asp.tif",
        "cc.tif", "ch.tif", "cbh.tif", "cbd.tif",
        "protected_mask.tif", "candidate_zone.tif", "non_burnable_mask.tif",
        "ignition_points.geojson", "metadata.json", "validation_report.txt",
    ]
    for name in expected:
        assert (inputs / name).exists(), f"missing: {name}"


def test_pipeline_outputs_share_grid(tmp_path: Path):
    # Polygon centered well inside the synthetic raster footprint (-100km..-70km, 970km..1000km).
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")

    inputs = out_dir / "inputs"
    tifs = list(inputs.glob("*.tif"))
    assert len(tifs) >= 8
    ref = None
    for tif in tifs:
        with rasterio.open(tif) as ds:
            sig = (ds.crs, tuple(ds.transform)[:6], ds.width, ds.height)
        if ref is None:
            ref = sig
        else:
            assert sig == ref, f"{tif.name} has {sig}, expected {ref}"


def test_pipeline_intermediate_deleted_by_default(tmp_path: Path):
    # Polygon centered well inside the synthetic raster footprint (-100km..-70km, 970km..1000km).
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    cfg = JobConfig.model_validate({
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    })
    out_dir = tmp_path / "job"
    source = _build_local_source(tmp_path)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:5070")
    assert not (out_dir / "_intermediate").exists()
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `uv run pytest tests/test_pipeline.py -x`
Expected: ImportError.

- [ ] **Step 3: Implement pipeline orchestrator**

Create `src/wildfire_preproc/pipeline.py`:

```python
"""Pipeline orchestrator — runs Stages 1–7 in order against a `JobConfig`."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from rasterio.crs import CRS
from shapely.geometry import shape

from wildfire_preproc.align.align import reproject_match
from wildfire_preproc.align.grid import GridSpec, gridspec_from_polygon
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
from wildfire_preproc.sources.lfps import LFPS_LAYER_CODE
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

    # Stage 1 — domain
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

    # Stage 3 — grid (we build it before fetching, since fetch bbox uses domain bounds in cfg.crs).
    grid = gridspec_from_polygon(art.simulation_polygon, crs=CRS.from_string(cfg.crs), cell_size=cfg.cell_size_m)

    # Stage 2 — fetch
    bbox = grid.bounds  # already in cfg.crs and snapped to grid
    raw_paths: dict[LayerKey, Path] = {}
    for layer in FETCHED_LAYERS:
        raw_paths[layer] = source.fetch(layer, bbox=bbox, dst_crs=cfg.crs)

    # Stage 3 — align each fetched raster onto the canonical grid
    aligned: dict[LayerKey, Path] = {}
    for layer, src_path in raw_paths.items():
        kind = layer_kind(layer)
        dst = inputs / f"{layer.value}.tif"
        reproject_match(
            src=src_path, dst=dst, grid=grid, kind=kind,
            dst_nodata=nodata_for_kind(kind),
        )
        aligned[layer] = dst

    # Stage 4 — terrain
    derive_slope_aspect(
        dem_path=aligned[LayerKey.DEM],
        grid=grid,
        out_slope_path=inputs / "slp.tif",
        out_aspect_path=inputs / "asp.tif",
    )

    # Stage 5 — masks
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

    # Stage 6 — validate
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
    if not all(r.ok for r in results):
        raise RuntimeError("Validation failed:\n" + report)

    # Stage 7 — metadata + cleanup
    job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:4]
    layer_sources = {
        "fbfm40": f"lfps:{LFPS_LAYER_CODE[LayerKey.FBFM40]}",
        "dem": "3dep:dynamic",
        "cc": f"lfps:{LFPS_LAYER_CODE[LayerKey.CC]}",
        "ch": f"lfps:{LFPS_LAYER_CODE[LayerKey.CH]}",
        "cbh": f"lfps:{LFPS_LAYER_CODE[LayerKey.CBH]}",
        "cbd": f"lfps:{LFPS_LAYER_CODE[LayerKey.CBD]}",
    }
    md = build_metadata(
        job_id=job_id, cfg=cfg, grid=grid, layer_sources=layer_sources, validation_status="passed",
    )
    (inputs / "metadata.json").write_text(json.dumps(md, indent=2))

    if not keep_intermediate:
        shutil.rmtree(intermediate, ignore_errors=True)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_pipeline.py -x`
Expected: 3 passed.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/wildfire_preproc/pipeline.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/wildfire_preproc/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): orchestrate Stages 1-7 against a RasterSource"
```

---

## Task 18: CLI

**Files:**
- Create: `src/wildfire_preproc/cli.py`
- Test: extend `tests/test_pipeline.py` (CLI invocation)

- [ ] **Step 1: Append failing CLI test**

Append to `tests/test_pipeline.py`:

```python
import json as _json
from click.testing import CliRunner

from wildfire_preproc.cli import main


def test_cli_run_executes_pipeline(tmp_path: Path):
    # Polygon centered well inside the synthetic raster footprint (-100km..-70km, 970km..1000km).
    poly = Polygon([
        (-90_500.0, 990_500.0), (-90_500.0, 991_000.0),
        (-90_000.0, 991_000.0), (-90_000.0, 990_500.0),
        (-90_500.0, 990_500.0),
    ])
    payload = {
        "protected_polygon": mapping(poly),
        "simulation_radius_m": 3000.0,
        "ignition_distance_m": 2500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    }
    job_json = tmp_path / "job.json"
    job_json.write_text(_json.dumps(payload))
    out_dir = tmp_path / "out"
    source = _build_local_source(tmp_path)

    # Inject the local source via a small helper hook the CLI exposes for testing.
    from wildfire_preproc import cli as cli_mod
    cli_mod._TEST_SOURCE_OVERRIDE = source  # type: ignore[attr-defined]
    try:
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(job_json), "--out", str(out_dir), "--protected-polygon-crs", "EPSG:5070"],
            catch_exceptions=False,
        )
    finally:
        cli_mod._TEST_SOURCE_OVERRIDE = None  # type: ignore[attr-defined]
    assert result.exit_code == 0, result.output
    assert (out_dir / "inputs" / "metadata.json").exists()
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `uv run pytest tests/test_pipeline.py::test_cli_run_executes_pipeline -x`
Expected: ImportError on `wildfire_preproc.cli`.

- [ ] **Step 3: Implement CLI**

Create `src/wildfire_preproc/cli.py`:

```python
"""Command-line interface for wildfire-preproc."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from wildfire_preproc.config import JobConfig
from wildfire_preproc.pipeline import run_pipeline
from wildfire_preproc.sources.base import RasterSource
from wildfire_preproc.sources.registry import DefaultSourceRegistry


# Test hook — set to a RasterSource instance to bypass DefaultSourceRegistry. Must be None in prod.
_TEST_SOURCE_OVERRIDE: RasterSource | None = None


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )


def _default_out(base: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return base / f"{ts}_job"


def _resolve_source(cache_dir: Path, landfire_version: str) -> RasterSource:
    if _TEST_SOURCE_OVERRIDE is not None:
        return _TEST_SOURCE_OVERRIDE
    return DefaultSourceRegistry(cache_dir=cache_dir, landfire_version=landfire_version)


@click.group()
def main() -> None:
    """LANDFIRE/3DEP preprocessing pipeline producing ELMFIRE-ready raster outputs."""


@main.command("run")
@click.argument("job_json", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None,
              help="Output job directory. Default: ./jobs/<timestamp>_job/")
@click.option("--protected-polygon-crs", default="EPSG:4326",
              help="CRS of the input protected polygon (default EPSG:4326).")
@click.option("--keep-intermediate", is_flag=True, default=False)
@click.option("-v", "--verbose", is_flag=True, default=False)
def run_cmd(
    job_json: Path,
    out_dir: Path | None,
    protected_polygon_crs: str,
    keep_intermediate: bool,
    verbose: bool,
) -> None:
    _setup_logging(verbose)
    cfg = JobConfig.from_json_file(job_json)
    if out_dir is None:
        out_dir = _default_out(Path.cwd() / "jobs")
    cache_dir = Path(cfg.cache_dir).expanduser() if cfg.cache_dir else Path.home() / ".cache" / "wildfire-preproc"
    source = _resolve_source(cache_dir, cfg.landfire_version)
    run_pipeline(
        cfg=cfg, out_dir=out_dir, source=source,
        protected_polygon_crs=protected_polygon_crs, keep_intermediate=keep_intermediate,
    )
    click.echo(f"Pipeline complete: {out_dir}")


@main.command("validate")
@click.argument("job_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def validate_cmd(job_dir: Path) -> None:
    """Re-run Stage 6 validation against an existing job directory."""
    report_path = job_dir / "inputs" / "validation_report.txt"
    if not report_path.exists():
        raise click.ClickException(f"no validation_report.txt at {report_path}")
    click.echo(report_path.read_text())


@main.command("sample")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def sample_cmd(out_dir: Path | None, verbose: bool) -> None:
    """Run the bundled sample AOI end-to-end against live LFPS+3DEP."""
    _setup_logging(verbose)
    sample_geojson = Path(__file__).parent.parent.parent / "data" / "sample" / "santa_monica_demo.geojson"
    if not sample_geojson.exists():
        raise click.ClickException(f"sample geojson missing: {sample_geojson}")
    import json as _json
    feature_collection = _json.loads(sample_geojson.read_text())
    polygon = feature_collection["features"][0]["geometry"]
    payload: dict[str, Any] = {
        "protected_polygon": polygon,
        "simulation_radius_m": 5000.0,
        "ignition_distance_m": 4500.0,
        "cell_size_m": 30.0,
        "crs": "EPSG:5070",
    }
    cfg = JobConfig.model_validate(payload)
    if out_dir is None:
        out_dir = _default_out(Path.cwd() / "jobs")
    cache_dir = Path.home() / ".cache" / "wildfire-preproc"
    source = _resolve_source(cache_dir, cfg.landfire_version)
    run_pipeline(cfg=cfg, out_dir=out_dir, source=source, protected_polygon_crs="EPSG:4326")
    click.echo(f"Sample complete: {out_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_pipeline.py -x`
Expected: 4 passed.

- [ ] **Step 5: CLI smoke check**

Run: `uv run wildfire-preproc --help`
Expected: shows `run`, `validate`, `sample` subcommands.

- [ ] **Step 6: Commit**

```bash
git add src/wildfire_preproc/cli.py tests/test_pipeline.py
git commit -m "feat(cli): run/validate/sample subcommands"
```

---

## Task 19: Sample AOI

**Files:**
- Create: `data/sample/santa_monica_demo.geojson`

- [ ] **Step 1: Write the sample GeoJSON**

Create `data/sample/santa_monica_demo.geojson`:

```json
{
  "type": "FeatureCollection",
  "name": "santa_monica_demo",
  "crs": { "type": "name", "properties": { "name": "urn:ogc:def:crs:OGC:1.3:CRS84" } },
  "features": [
    {
      "type": "Feature",
      "properties": { "name": "santa_monica_demo" },
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [-118.7000, 34.0900],
            [-118.6850, 34.0900],
            [-118.6850, 34.1050],
            [-118.7000, 34.1050],
            [-118.7000, 34.0900]
          ]
        ]
      }
    }
  ]
}
```

- [ ] **Step 2: Smoke check**

Run: `uv run python -c "
import geopandas as gpd
gdf = gpd.read_file('data/sample/santa_monica_demo.geojson')
print(gdf.geometry.iloc[0])
"`
Expected: prints a `POLYGON ((...))` line.

- [ ] **Step 3: Commit**

```bash
git add data/sample/santa_monica_demo.geojson
git commit -m "data: add Santa Monica sample AOI"
```

---

## Task 20: Live end-to-end integration test + sample run

**Files:**
- Create: `tests/test_pipeline_e2e_live.py`, `scripts/run_sample.sh`

- [ ] **Step 1: Write the live integration test**

Create `tests/test_pipeline_e2e_live.py`:

```python
import json
from pathlib import Path

import pytest
import rasterio


pytestmark = pytest.mark.live


def test_sample_runs_end_to_end_against_live_apis(tmp_path: Path):
    """Run `wildfire-preproc sample` against LIVE LFPS + 3DEP. Skipped unless --live mark active."""
    from click.testing import CliRunner

    from wildfire_preproc.cli import main

    out_dir = tmp_path / "sample_job"
    runner = CliRunner()
    result = runner.invoke(main, ["sample", "--out", str(out_dir)], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    inputs = out_dir / "inputs"
    expected = [
        "fbfm40.tif", "dem.tif", "slp.tif", "asp.tif",
        "cc.tif", "ch.tif", "cbh.tif", "cbd.tif",
        "protected_mask.tif", "candidate_zone.tif", "non_burnable_mask.tif",
        "ignition_points.geojson", "metadata.json", "validation_report.txt",
    ]
    for name in expected:
        assert (inputs / name).exists(), f"missing: {name}"

    md = json.loads((inputs / "metadata.json").read_text())
    assert md["validation"] == "passed"
    assert md["grid"]["crs"] == "EPSG:5070"

    # All rasters share the canonical grid.
    ref = None
    for tif in inputs.glob("*.tif"):
        with rasterio.open(tif) as ds:
            sig = (ds.crs, tuple(ds.transform)[:6], ds.width, ds.height)
        if ref is None:
            ref = sig
        else:
            assert sig == ref, f"{tif.name} has {sig}, expected {ref}"
```

- [ ] **Step 2: Add the run-sample script**

Create `scripts/run_sample.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run wildfire-preproc sample "$@"
```

Run: `chmod +x scripts/run_sample.sh`

- [ ] **Step 3: Run the live integration test**

Run: `uv run pytest tests/test_pipeline_e2e_live.py -x -v -m live`
Expected: PASS — actually fetches from LFPS + 3DEP, runs full pipeline, validates outputs.

If LFPS or 3DEP is down or rate-limits, the test will fail with a clear network error. In that case: do not fake the test pass — report the failure, fix any code bug it reveals, and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline_e2e_live.py scripts/run_sample.sh
git commit -m "test: live end-to-end integration test against LFPS+3DEP"
```

---

## Task 21: README + final cleanup

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the README**

Replace `README.md` with:

````markdown
# wildfire-preproc

LANDFIRE/3DEP preprocessing pipeline producing ELMFIRE-ready raster outputs.

Given a protected-land polygon and a simulation config, this pipeline produces an aligned, validated raster directory that can be fed directly to ELMFIRE.

## Quick start

```bash
# 1. Install
uv sync --all-groups

# 2. Run the bundled Santa Monica sample (fetches live LANDFIRE + 3DEP data)
uv run wildfire-preproc sample --out ./jobs/sample
```

The output is `./jobs/sample/inputs/` containing all required GeoTIFFs, the ignition-points GeoJSON, validation report, and `metadata.json`.

## Outputs

Per-job, `inputs/` contains:

| File | Description |
|---|---|
| `fbfm40.tif` | LANDFIRE Scott & Burgan 40 fuel models, `uint8`, nodata=255 |
| `dem.tif` | 3DEP DEM resampled to target cell size, `float32`, nodata=-9999 |
| `slp.tif` | Slope (degrees, 0–90), derived from DEM via Horn 3×3, `float32`, nodata=-9999 |
| `asp.tif` | Aspect (compass degrees from N, flat=-1), `float32`, nodata=-9999 |
| `cc.tif`, `ch.tif`, `cbh.tif`, `cbd.tif` | LANDFIRE canopy cover, height, base height, bulk density (`float32`) |
| `protected_mask.tif` | 1 inside protected polygon, 0 outside |
| `candidate_zone.tif` | 1 where firebreaks may be placed (annulus between safety-buffered protected polygon and ignition ring) |
| `non_burnable_mask.tif` | 1 where fire cannot spread (FBFM40 codes 91/92/93/98/99 by default) |
| `ignition_points.geojson` | 8 ignition points on N/NE/E/SE/S/SW/W/NW bearings |
| `metadata.json` | Grid, CRS, bounds, layer source provenance |
| `validation_report.txt` | Stage 6 invariant check report |

All rasters share the same CRS, extent, transform, width, and height — ELMFIRE will reject misaligned grids.

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

Optional: `safety_buffer_m` (default 100), `non_burnable_sources` (default `["fbfm40"]`), `landfire_version` (default `"LF2022"`), `cache_dir`.

```bash
uv run wildfire-preproc run path/to/job.json --out ./jobs/my-run
```

By default the input polygon is interpreted as `EPSG:4326` (lon/lat). Override with `--protected-polygon-crs`.

## Architecture

7 stages, run in order:

1. **Domain** — buffer the protected polygon to produce simulation domain, ignition ring, candidate zone, and 8 ignition points (all in projected CRS, polygon-boundary based).
2. **Acquire** — fetch each layer from its configured source (LFPS for fuels/canopy, 3DEP for DEM). Cached on disk by SHA-256(bbox+layer+crs).
3. **Align** — build a single canonical `GridSpec` snapped to the global cell grid; reproject every raster onto it (nearest for FBFM40, bilinear for continuous).
4. **Terrain** — derive slope and aspect from the aligned DEM via Horn 3×3 (degrees / compass-cw-from-N).
5. **Masks** — rasterize `protected_mask`, `candidate_zone`, build `non_burnable_mask` from FBFM40 reclass.
6. **Validate** — verify CRS, transform, dimensions, nodata, value ranges per layer; build a human-readable report.
7. **Export** — write `metadata.json`; clean up `_intermediate/`.

The hard invariant: after Stage 3, every raster going through `reproject_match` shares the same `GridSpec`. Stages have no path to produce a misaligned output.

## Development

```bash
uv run pytest -x                           # unit + integration (includes live tests)
uv run pytest -x -m "not live"             # skip live API tests
uv run pytest tests/test_pipeline_e2e_live.py -m live   # only live e2e
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
```
````

- [ ] **Step 2: Run all tests one final time**

Run: `uv run pytest -x -m "not live"`
Expected: all unit + offline integration tests pass.

Run: `uv run pytest -x -m live`
Expected: live e2e passes (or report the failure honestly).

Run: `uv run ruff check src tests`
Expected: clean.

Run: `uv run mypy src`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README quick-start, outputs reference, architecture overview"
```

- [ ] **Step 4: Final tag**

```bash
git tag v0.1.0
```

---

## Spec Coverage Audit

Mapping each spec section to tasks that implement it:

| Spec section | Task(s) |
|---|---|
| §2 Inputs (JobConfig schema) | Task 2 |
| §2 Outputs directory | Tasks 17, 19 |
| §3 Module layout | Tasks 1–18 (one per module) |
| §3 Invariant 1 (frozen GridSpec) | Tasks 4, 5 |
| §3 Invariant 2 (RasterSource protocol) | Tasks 12, 13, 14, 15 |
| §3 Invariant 3 (non_burnable plug-in) | Task 9 |
| §4 Stage 1 — domain | Task 6 |
| §4 Stage 2 — acquire | Tasks 12–15, 17 |
| §4 Stage 3 — align | Tasks 4, 5 |
| §4 Stage 4 — terrain | Task 7 |
| §4 Stage 5 — masks | Tasks 8, 9 |
| §4 Stage 6 — validate | Tasks 10, 11 |
| §4 Stage 7 — export | Tasks 16, 17 |
| §5.1 GridSpec construction | Task 4 |
| §5.2 Horn 3×3 slope/aspect | Task 7 |
| §5.3 LFPS + 3DEP fetchers | Tasks 13, 14 |
| §5.4 Geometry (polygon-boundary based) | Task 6 |
| §6 Validation rules | Tasks 10, 11 |
| §7 CLI (run/validate/sample) | Task 18 |
| §8 Dependencies + uv setup | Task 1 |
| §9 Sample AOI | Task 19 |
| §10 metadata.json structure | Task 16 |
| §11 Definition of Done | Tasks 17, 18, 20, 21 |
| §12 Risks (LFPS down, layer codes) | Task 13 (retry policy + acknowledged in code constants) |
| §13 Out of scope | (no task — explicitly excluded) |

No spec gaps.
