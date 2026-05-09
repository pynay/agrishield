import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from wildfire_preproc.elmfire import (
    ElmfireRunResult,
    ElmfireRunSpec,
    _windows_path_to_wsl,
    _write_scenario_summary,
    _wsl_path,
)


def test_windows_path_to_wsl_converts_drive_path() -> None:
    assert _windows_path_to_wsl(r"C:\Users\laksh\agrishield") == "/mnt/c/Users/laksh/agrishield"


def test_wsl_path_rejects_non_windows_path() -> None:
    with pytest.raises(ValueError, match="native ELMFIRE runner"):
        _wsl_path(Path("/Users/lakshgoyal/agrishield"))


def test_write_scenario_summary_reads_elmfire_outputs(tmp_path: Path) -> None:
    transform = from_origin(0, 30, 10, 10)
    protected_mask = np.array(
        [
            [0, 0, 0],
            [0, 1, 1],
            [0, 0, 0],
        ],
        dtype="uint8",
    )
    arrival = np.array(
        [
            [0, 0, 0],
            [0, 120, 0],
            [0, 0, 0],
        ],
        dtype="float32",
    )
    intensity = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
        ],
        dtype="float32",
    )

    mask_path = tmp_path / "protected_mask.tif"
    output_dir = tmp_path / "run"
    outputs_dir = output_dir / "outputs"
    outputs_dir.mkdir(parents=True)
    _write_test_raster(mask_path, protected_mask, transform, "uint8", nodata=0)
    _write_test_raster(outputs_dir / "time_of_arrival.tif", arrival, transform, "float32")
    _write_test_raster(outputs_dir / "flin.tif", intensity, transform, "float32")

    spec = ElmfireRunSpec(
        run_id="bearing_090",
        ignition_bearing_deg=90,
        ignition_x=0,
        ignition_y=0,
        protected_center_x=0,
        protected_center_y=0,
        wind_to_direction_deg=270,
        wind_from_direction_deg=90,
        wind_speed_mps=6.7,
        inputs_dir=output_dir / "inputs",
        output_dir=output_dir,
        config_path=output_dir / "inputs" / "elmfire.data",
        ignition_point_path=output_dir / "ignition_point.geojson",
        raster_paths={},
    )
    result = ElmfireRunResult(
        spec=spec,
        returncode=0,
        stdout="",
        stderr="",
        elapsed_s=1.0,
        output_files=[],
    )

    _write_scenario_summary(result, mask_path)

    payload = json.loads((output_dir / "summary.json").read_text())
    assert payload["scenario_id"] == 3
    assert payload["ignition_direction"] == "east"
    assert payload["patch_burned"] is True
    assert payload["burned_area_inside_patch_m2"] == 100
    assert payload["first_arrival_to_patch_minutes"] == 2
    assert payload["max_flame_length_near_patch_m"] == 9


def _write_test_raster(
    path: Path,
    data: np.ndarray,
    transform: rasterio.Affine,
    dtype: str,
    nodata: float | int | None = None,
) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=dtype,
        crs="EPSG:5070",
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def test_subprocess_runner_validates_executable_exists(tmp_path: Path) -> None:
    """SubprocessElmfireRunner must fail at construction with a clear message
    when the path-style executable does not exist."""
    from wildfire_preproc.elmfire import (
        ElmfireExecutableMissing,
        SubprocessElmfireRunner,
    )

    missing = tmp_path / "no-such-elmfire"
    with pytest.raises(ElmfireExecutableMissing, match="not found"):
        SubprocessElmfireRunner([str(missing)])


def test_subprocess_runner_skips_check_for_bare_command_names(tmp_path: Path) -> None:
    """A bare command name (no path separator) is assumed to be on PATH and
    should not trigger an existence check at construction."""
    from wildfire_preproc.elmfire import SubprocessElmfireRunner

    # Should not raise even though "no-such-binary-on-path" doesn't exist.
    runner = SubprocessElmfireRunner(["no-such-binary-on-path"])
    assert runner is not None


def test_resolve_elmfire_runner_native_on_unix(tmp_path: Path) -> None:
    """resolve_elmfire_runner('native') returns SubprocessElmfireRunner."""
    from wildfire_preproc.elmfire import (
        SubprocessElmfireRunner,
        resolve_elmfire_runner,
    )

    fake = tmp_path / "fake_elmfire"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    runner = resolve_elmfire_runner("native", fake)
    assert isinstance(runner, SubprocessElmfireRunner)


def test_resolve_elmfire_runner_auto_falls_through(tmp_path: Path) -> None:
    """resolve_elmfire_runner('auto') picks native on macOS/Linux."""
    import sys

    from wildfire_preproc.elmfire import (
        SubprocessElmfireRunner,
        WslElmfireRunner,
        resolve_elmfire_runner,
    )

    fake = tmp_path / "fake_elmfire"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    # On macOS/Linux this should be SubprocessElmfireRunner; on Windows WslElmfireRunner.
    runner = resolve_elmfire_runner("auto", fake)
    if sys.platform.startswith("win"):
        assert isinstance(runner, WslElmfireRunner)
    else:
        assert isinstance(runner, SubprocessElmfireRunner)


def test_resolve_elmfire_runner_rejects_missing_executable(tmp_path: Path) -> None:
    from wildfire_preproc.elmfire import ElmfireExecutableMissing, resolve_elmfire_runner

    with pytest.raises(ElmfireExecutableMissing):
        resolve_elmfire_runner("native", tmp_path / "definitely-missing")


def test_resolve_elmfire_runner_can_skip_check(tmp_path: Path) -> None:
    """Tests that need to inject a fake binary should be able to skip the check."""
    from wildfire_preproc.elmfire import resolve_elmfire_runner

    runner = resolve_elmfire_runner(
        "native", tmp_path / "missing", check_executable=False
    )
    assert runner is not None
