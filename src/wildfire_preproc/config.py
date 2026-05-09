"""Job configuration: layer enumeration, kinds, and the JSON-payload schema."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LayerKey(StrEnum):
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


class LayerKind(StrEnum):
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
