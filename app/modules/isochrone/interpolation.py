"""Linear interpolation inside one measured radial bracket."""

from app.modules.isochrone.refinement import BoundaryBracket


def interpolate_radius(bracket: BoundaryBracket, time_limit_s: int) -> float:
    inner_radius = bracket.inner.sample.radial_distance_m
    outer_radius = bracket.outer.sample.radial_distance_m
    inner_time = bracket.inner.duration_s
    outer_time = bracket.outer.duration_s
    if outer_time <= inner_time:
        return (inner_radius + outer_radius) / 2.0
    ratio = (time_limit_s - inner_time) / (outer_time - inner_time)
    ratio = min(1.0, max(0.0, ratio))
    return inner_radius + ratio * (outer_radius - inner_radius)
