"""Deterministic radial sampling around a BD09LL center."""

import math
from dataclasses import dataclass

from app.schemas.common import CenterPoint
from app.schemas.routing import RouteTarget


EARTH_RADIUS_M = 6_371_008.8
DEFAULT_RADII_M = (300.0, 600.0, 900.0, 1200.0, 1500.0, 1800.0)


@dataclass(frozen=True, slots=True)
class SamplePoint:
    sample_id: str
    bearing_deg: float
    radial_distance_m: float
    location: CenterPoint

    def as_route_target(self) -> RouteTarget:
        return RouteTarget(target_id=self.sample_id, location=self.location)


def bearings(direction_count: int) -> list[float]:
    if direction_count < 3:
        raise ValueError("direction_count 至少为 3。")
    step = 360.0 / direction_count
    return [index * step for index in range(direction_count)]


def destination_point(
    center: CenterPoint, bearing_deg: float, distance_m: float
) -> CenterPoint:
    if distance_m < 0 or not math.isfinite(distance_m):
        raise ValueError("采样半径必须是非负有限数。")
    angular = distance_m / EARTH_RADIUS_M
    bearing_rad = math.radians(bearing_deg)
    lat1 = math.radians(center.lat)
    lng1 = math.radians(center.lng)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing_rad)
    )
    lng2 = lng1 + math.atan2(
        math.sin(bearing_rad) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    lng = (math.degrees(lng2) + 540.0) % 360.0 - 180.0
    return CenterPoint(lng=lng, lat=math.degrees(lat2), crs="BD09LL")


def sample_point(
    center: CenterPoint,
    bearing_deg: float,
    radius_m: float,
    *,
    phase: str = "sample",
) -> SamplePoint:
    bearing_token = f"{bearing_deg:06.2f}".replace(".", "p")
    radius_token = f"{radius_m:07.2f}".replace(".", "p")
    return SamplePoint(
        sample_id=f"{phase}:b{bearing_token}:r{radius_token}",
        bearing_deg=bearing_deg,
        radial_distance_m=radius_m,
        location=destination_point(center, bearing_deg, radius_m),
    )


def initial_samples(
    center: CenterPoint,
    *,
    direction_count: int = 24,
    radii_m: tuple[float, ...] = DEFAULT_RADII_M,
) -> list[SamplePoint]:
    if not radii_m or any(radius <= 0 for radius in radii_m):
        raise ValueError("初始采样半径必须全部大于 0。")
    return [
        sample_point(center, bearing, radius)
        for bearing in bearings(direction_count)
        for radius in radii_m
    ]
