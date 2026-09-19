"""Find and refine radial travel-time brackets."""

from dataclasses import dataclass

from app.modules.isochrone.sampling import SamplePoint, sample_point
from app.schemas.common import CenterPoint


@dataclass(frozen=True, slots=True)
class MeasuredSample:
    sample: SamplePoint
    duration_s: int


@dataclass(frozen=True, slots=True)
class BoundaryBracket:
    inner: MeasuredSample
    outer: MeasuredSample


def find_bracket(
    center: CenterPoint,
    bearing_deg: float,
    measurements: list[MeasuredSample],
    time_limit_s: int,
) -> BoundaryBracket | None:
    center_sample = sample_point(center, bearing_deg, 0.0, phase="center")
    inner = MeasuredSample(center_sample, 0)
    for measured in sorted(
        measurements, key=lambda item: item.sample.radial_distance_m
    ):
        if measured.duration_s <= time_limit_s:
            inner = measured
        elif measured.sample.radial_distance_m > inner.sample.radial_distance_m:
            return BoundaryBracket(inner=inner, outer=measured)
    return None


def midpoint_sample(
    center: CenterPoint, bracket: BoundaryBracket, round_index: int
) -> SamplePoint:
    radius = (
        bracket.inner.sample.radial_distance_m
        + bracket.outer.sample.radial_distance_m
    ) / 2.0
    return sample_point(
        center,
        bracket.inner.sample.bearing_deg,
        radius,
        phase=f"refine{round_index}",
    )


def update_bracket(
    bracket: BoundaryBracket, measured: MeasuredSample, time_limit_s: int
) -> BoundaryBracket:
    if measured.duration_s <= time_limit_s:
        return BoundaryBracket(inner=measured, outer=bracket.outer)
    return BoundaryBracket(inner=bracket.inner, outer=measured)
