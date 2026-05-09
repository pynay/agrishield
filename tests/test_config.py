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
            "coordinates": [
                [[-118.7, 34.1], [-118.6, 34.1], [-118.6, 34.2], [-118.7, 34.2], [-118.7, 34.1]]
            ],
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
        "protected_polygon": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
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
            "coordinates": [
                [[-118.7, 34.1], [-118.6, 34.1], [-118.6, 34.2], [-118.7, 34.2], [-118.7, 34.1]]
            ],
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
