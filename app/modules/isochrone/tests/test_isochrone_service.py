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


def test_a_routing_refusal_reason_is_surfaced_in_the_failure_message():
    """An AK/whitelist refusal must not read as a mere shortage of walking data.

    Routing and a sparse street network both yield zero measurements, so without
    the reason an operator cannot tell a misconfigured deployment from real map
    data gaps.
    """
    from app.schemas.routing import (
        RoutingFailure,
        RoutingResult,
        RoutingErrorCode,
    )

    refusal = "百度步行批量算路拒绝了服务端 AK：出口 IP 不在白名单，或 AK 未获授权。"

    def refusing_route(_request):
        return RoutingResult(root=RoutingFailure(
            ok=False,
            error={"code": RoutingErrorCode.INVALID_REQUEST, "message": refusal,
                   "retryable": False},
            warnings=[],
            meta={"schema_version": "1.0", "provider": "baidu"},
        ))

    result = IsochroneService(refusing_route).generate(request()).root

    assert result.ok is False
    assert "白名单" in result.error.message


def test_a_failure_without_a_refusal_keeps_the_plain_message():
    """A genuine data shortage must not gain a spurious Routing reason."""
    from app.schemas.routing import RoutingResult, RoutingSuccess, RoutingData, RoutingSummary

    def unavailable_route(req):
        # A successful envelope whose targets are all unavailable: routing did
        # answer, so there is no refusal to report, only missing measurements.
        routes = [
            {
                "target_id": target.target_id,
                "location": target.location.model_dump(),
                "status": "unavailable",
                "distance_m": None,
                "duration_s": None,
            }
            for target in req.targets
        ]
        return RoutingResult(root=RoutingSuccess(
            ok=True,
            data=RoutingData(
                origin=req.origin.model_dump(),
                travel_mode="walking",
                routes=routes,
                summary=RoutingSummary(
                    requested=len(routes), success=0, no_route=0,
                    unavailable=len(routes),
                ),
            ),
            warnings=[],
            meta={"schema_version": "1.0", "provider": "baidu"},
        ))

    result = IsochroneService(unavailable_route).generate(request()).root

    assert result.ok is False
    assert "Routing 返回" not in result.error.message
