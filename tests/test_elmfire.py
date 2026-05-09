from pathlib import Path

import pytest

from wildfire_preproc.elmfire import _windows_path_to_wsl, _wsl_path


def test_windows_path_to_wsl_converts_drive_path() -> None:
    assert _windows_path_to_wsl(r"C:\Users\laksh\agrishield") == "/mnt/c/Users/laksh/agrishield"


def test_wsl_path_rejects_non_windows_path() -> None:
    with pytest.raises(ValueError, match="native ELMFIRE runner"):
        _wsl_path(Path("/Users/lakshgoyal/agrishield"))
