"""Live end-to-end integration test against LFPS + 3DEP.

Marked @pytest.mark.live; runs the bundled Santa Monica sample AOI fully against
real LANDFIRE Product Service and USGS 3DEP. Validates that all 14 expected
output files are produced and that all rasters share the same canonical grid.
"""

import json
from pathlib import Path

import pytest
import rasterio
from click.testing import CliRunner

from wildfire_preproc.cli import main

pytestmark = pytest.mark.live


def test_sample_runs_end_to_end_against_live_apis(tmp_path: Path):
    """Run `wildfire-preproc sample` against LIVE LFPS + 3DEP."""
    out_dir = tmp_path / "sample_job"
    runner = CliRunner()
    result = runner.invoke(main, ["sample", "--out", str(out_dir)], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    inputs = out_dir / "inputs"
    expected = [
        "fbfm40.tif",
        "dem.tif",
        "slp.tif",
        "asp.tif",
        "cc.tif",
        "ch.tif",
        "cbh.tif",
        "cbd.tif",
        "protected_mask.tif",
        "candidate_zone.tif",
        "non_burnable_mask.tif",
        "ignition_points.geojson",
        "metadata.json",
        "validation_report.txt",
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
