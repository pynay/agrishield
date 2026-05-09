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
