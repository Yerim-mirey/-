"""Isochrone uses the public Routing service rather than a provider directly."""

import math

from app.modules.isochrone.service import IsochroneService
from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import ProviderRoute
from app.schemas.isochrone import IsochroneRequest


class _DistanceBasedProvider:
    def route_matrix(self, origin, destinations):
        return [
            ProviderRoute(
                distance_m=round(_distance_m(origin, destination)),
                duration_s=round(_distance_m(origin, destination) * 0.8),
            )
            for destination in destinations
        ]


def _distance_m(first, second) -> float:
    earth_radius_m = 6_371_008.8
    lat1 = math.radians(first.lat)
    lat2 = math.radians(second.lat)
    delta_lat = lat2 - lat1
    delta_lng = math.radians(second.lng - first.lng)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(value))


def test_routing_service_drives_isochrone_generation() -> None:
    routing = RoutingService(_DistanceBasedProvider())
    request = IsochroneRequest.model_validate(
        {
            "schema_version": "1.0",
            "center": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
            "time_limit_s": 900,
        }
    )

    result = IsochroneService(routing.route).generate(request).root

    assert result.ok is True
    assert result.data.quality.valid_boundary_directions == 24
    assert result.data.quality.confidence.value == "high"
    assert result.data.geometry.coordinates[0][0] == result.data.geometry.coordinates[0][-1]
    radii = [point.radial_distance_m for point in result.data.boundary_points]
    assert min(radii) > 1100
    assert max(radii) < 1150
