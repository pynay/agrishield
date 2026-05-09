import math

import pytest
from shapely.geometry import LineString, Point

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
