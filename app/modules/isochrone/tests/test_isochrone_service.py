from app.modules.isochrone.service import IsochroneService
from app.schemas.isochrone import IsochroneRequest
from app.schemas.routing import RoutingResult


def request() -> IsochroneRequest:
    return IsochroneRequest.model_validate(
        {
            "schema_version": "1.0",
            "center": {"lng": 121.5, "lat": 31.2, "crs": "BD09LL"},
            "time_limit_s": 900,
        }
    )


def radius_from_id(target_id: str) -> float:
    return float(target_id.rsplit(":r", 1)[1].replace("p", "."))


class RadialRouter:
    def __init__(self, seconds_per_meter: float = 0.8, missing_bearings=()) -> None:
        self.seconds_per_meter = seconds_per_meter
        self.missing_bearings = tuple(missing_bearings)
        self.calls = []

    def __call__(self, routing_request):
        self.calls.append(len(routing_request.targets))
        routes = []
        for target in routing_request.targets:
            missing = any(f":b{bearing}:" in target.target_id for bearing in self.missing_bearings)
            radius = radius_from_id(target.target_id)
            routes.append(
                {
                    "target_id": target.target_id,
                    "location": target.location.model_dump(),
                    "status": "unavailable" if missing else "success",
                    "distance_m": None if missing else round(radius),
                    "duration_s": None if missing else round(radius * self.seconds_per_meter),
                }
            )
        success = sum(route["status"] == "success" for route in routes)
        unavailable = len(routes) - success
        return RoutingResult.model_validate(
            {
                "ok": True,
                "data": {
                    "origin": routing_request.origin.model_dump(),
                    "travel_mode": "walking",
                    "routes": routes,
                    "summary": {
                        "requested": len(routes),
                        "success": success,
                        "no_route": 0,
                        "unavailable": unavailable,
                    },
                },
                "warnings": [],
                "meta": {"schema_version": "1.0", "provider": "mock"},
            }
        )


def test_nominal_isochrone_has_closed_polygon_and_high_confidence() -> None:
    router = RadialRouter()
    result = IsochroneService(router).generate(request()).root
    assert result.ok is True
    assert len(result.data.boundary_points) == 24
    assert result.data.geometry.coordinates[0][0] == result.data.geometry.coordinates[0][-1]
    assert result.data.quality.routing_sample_count == 192
    assert result.data.quality.confidence.value == "high"
    assert result.warnings == []
    assert all(
        abs(point.radial_distance_m - 1125.0) < 0.01
        for point in result.data.boundary_points
    )


def test_one_missing_direction_produces_gaps_without_failure() -> None:
    router = RadialRouter(missing_bearings=("090p00",))
    result = IsochroneService(router).generate(request()).root
    assert result.ok is True
    assert len(result.data.boundary_points) == 23
    assert [warning.code for warning in result.warnings] == [
        "PARTIAL_ROUTING_RESULTS",
        "BOUNDARY_GAPS",
        "BOUNDARY_NOT_BRACKETED",
    ]


def test_fewer_than_three_boundaries_returns_failure() -> None:
    missing = tuple(
        f"{bearing:06.2f}".replace(".", "p")
        for bearing in [index * 15.0 for index in range(24)]
        if bearing not in {0.0, 15.0}
    )
    result = IsochroneService(RadialRouter(missing_bearings=missing)).generate(request()).root
    assert result.ok is False
    assert result.error.code.value == "INSUFFICIENT_ROUTING_DATA"


def test_finite_outward_extension_finds_larger_boundary() -> None:
    router = RadialRouter(seconds_per_meter=0.4)
    result = IsochroneService(router, direction_count=4).generate(request()).root
    assert result.ok is True
    assert router.calls[:2] == [24, 4]
    assert all(point.radial_distance_m > 1800 for point in result.data.boundary_points)
