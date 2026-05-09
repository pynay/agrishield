"""Command-line interface for wildfire-preproc."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
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
    if cfg.cache_dir:
        cache_dir = Path(cfg.cache_dir).expanduser()
    else:
        cache_dir = Path.home() / ".cache" / "wildfire-preproc"
    source = _resolve_source(cache_dir, cfg.landfire_version)
    run_pipeline(
        cfg=cfg, out_dir=out_dir, source=source,
        protected_polygon_crs=protected_polygon_crs, keep_intermediate=keep_intermediate,
    )
    click.echo(f"Pipeline complete: {out_dir}")


@main.command("validate")
@click.argument("job_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def validate_cmd(job_dir: Path) -> None:
    """Re-run Stage 6 validation against an existing job directory's rasters."""
    import json as _json

    from rasterio.crs import CRS
    from rasterio.transform import Affine

    from wildfire_preproc.align.grid import GridSpec
    from wildfire_preproc.config import LayerKey, layer_kind
    from wildfire_preproc.validation.checks import validate_raster
    from wildfire_preproc.validation.report import format_report

    inputs = job_dir / "inputs"
    metadata_path = inputs / "metadata.json"
    if not metadata_path.exists():
        raise click.ClickException(f"no metadata.json at {metadata_path}")

    md = _json.loads(metadata_path.read_text())
    grid = GridSpec(
        crs=CRS.from_string(md["grid"]["crs"]),
        transform=Affine(*md["grid"]["transform"]),
        width=md["grid"]["width"],
        height=md["grid"]["height"],
        cell_size=md["config"]["cell_size_m"],
    )
    layers = [
        LayerKey.FBFM40, LayerKey.DEM, LayerKey.SLP, LayerKey.ASP,
        LayerKey.CC, LayerKey.CH, LayerKey.CBH, LayerKey.CBD,
        LayerKey.PROTECTED_MASK, LayerKey.CANDIDATE_ZONE, LayerKey.NON_BURNABLE_MASK,
    ]
    layer_paths = [(inputs / f"{lk.value}.tif", lk) for lk in layers]
    results = [validate_raster(p, grid, lk, layer_kind(lk)) for p, lk in layer_paths]
    report = format_report(results, manifest_ok=all(p.exists() for p, _ in layer_paths))
    click.echo(report)
    if not all(r.ok for r in results):
        raise click.ClickException("Validation failed")


@main.command("sample")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def sample_cmd(out_dir: Path | None, verbose: bool) -> None:
    """Run the bundled sample AOI end-to-end against live LFPS+3DEP."""
    _setup_logging(verbose)
    sample_geojson = (
        Path(__file__).parent.parent.parent / "data" / "sample" / "santa_monica_demo.geojson"
    )
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
