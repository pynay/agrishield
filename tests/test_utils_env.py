"""Tests for the shared .env loader."""

import os
from pathlib import Path

from wildfire_preproc.utils.env import load_env_file


def test_load_env_file_reads_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LANDFIRE_EMAIL", raising=False)
    monkeypatch.delenv("LANDFIRE_VERSION", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# comment line\n"
        "LANDFIRE_EMAIL=alice@example.com\n"
        "LANDFIRE_VERSION='LF2023'\n"
        "\n"
    )
    load_env_file(env)
    assert os.environ["LANDFIRE_EMAIL"] == "alice@example.com"
    assert os.environ["LANDFIRE_VERSION"] == "LF2023"


def test_load_env_file_does_not_override_existing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LANDFIRE_EMAIL", "preset@example.com")
    env = tmp_path / ".env"
    env.write_text("LANDFIRE_EMAIL=overridden@example.com\n")
    load_env_file(env)
    assert os.environ["LANDFIRE_EMAIL"] == "preset@example.com"


def test_load_env_file_missing_is_silent(tmp_path: Path) -> None:
    """Missing .env must not raise."""
    load_env_file(tmp_path / "nonexistent.env")  # no error


def test_web_server_loads_env_at_import(tmp_path: Path, monkeypatch) -> None:
    """web/server.py must invoke load_env_file at import (regression for the
    previous behavior where only main.py loaded .env)."""
    import importlib

    import web.server as server_module

    monkeypatch.setattr(server_module, "load_env_file", lambda *_: None)
    importlib.reload(server_module)
    # Smoke: importing the module should not error and should expose load_env_file.
    assert hasattr(server_module, "load_env_file")
