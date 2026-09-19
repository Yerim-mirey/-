import pytest

from app.modules.isochrone.interpolation import interpolate_radius
from app.modules.isochrone.refinement import (
    BoundaryBracket,
    MeasuredSample,
    find_bracket,
    midpoint_sample,
)
from app.modules.isochrone.sampling import (
    bearings,
    destination_point,
    initial_samples,
    sample_point,
)
from app.schemas.common import CenterPoint


CENTER = CenterPoint(lng=121.5, lat=31.2, crs="BD09LL")


def test_twenty_four_bearings_and_multi_radius_samples_are_stable() -> None:
    values = bearings(24)
    samples = initial_samples(CENTER)
    assert values == [index * 15.0 for index in range(24)]
    assert len(samples) == 144
    assert len({sample.sample_id for sample in samples}) == 144
    assert samples[0].bearing_deg == 0
    assert samples[-1].bearing_deg == 345


def test_sample_converts_to_route_target() -> None:
    sample = sample_point(CENTER, 90.0, 1000.0)
    target = sample.as_route_target()
    assert target.target_id == sample.sample_id
    assert target.location == sample.location


def test_zero_distance_returns_center() -> None:
    point = destination_point(CENTER, 123.0, 0.0)
    assert point.lng == pytest.approx(CENTER.lng)
    assert point.lat == pytest.approx(CENTER.lat)


def test_bracket_midpoint_and_interpolation() -> None:
    measured = [
        MeasuredSample(sample_point(CENTER, 0.0, 600.0), 500),
        MeasuredSample(sample_point(CENTER, 0.0, 900.0), 760),
        MeasuredSample(sample_point(CENTER, 0.0, 1200.0), 1030),
    ]
    bracket = find_bracket(CENTER, 0.0, measured, 900)
    assert bracket is not None
    assert midpoint_sample(CENTER, bracket, 0).radial_distance_m == 1050
    assert interpolate_radius(bracket, 900) == pytest.approx(1055.5555556)


def test_interpolation_falls_back_when_times_are_not_monotonic() -> None:
    bracket = BoundaryBracket(
        inner=MeasuredSample(sample_point(CENTER, 0.0, 900.0), 800),
        outer=MeasuredSample(sample_point(CENTER, 0.0, 1200.0), 800),
    )
    assert interpolate_radius(bracket, 900) == 1050
