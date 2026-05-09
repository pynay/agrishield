import json
from pathlib import Path

from rasterio.crs import CRS
from rasterio.transform import from_origin

from wildfire_preproc.align.grid import GridSpec
from wildfire_preproc.config import JobConfig
from wildfire_preproc.export.metadata import build_metadata


def _cfg() -> JobConfig:
    return JobConfig.model_validate({
        "protected_polygon": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
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
