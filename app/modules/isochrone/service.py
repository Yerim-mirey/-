"""Approximate a walking isochrone from real point-to-many Routing results."""

from collections.abc import Callable

from app.modules.isochrone.interpolation import interpolate_radius
from app.modules.isochrone.refinement import (
    BoundaryBracket,
    MeasuredSample,
    find_bracket,
    midpoint_sample,
    update_bracket,
)
from app.modules.isochrone.sampling import (
    DEFAULT_RADII_M,
    SamplePoint,
    bearings,
    destination_point,
    initial_samples,
    sample_point,
)
from app.schemas.common import Meta, WarningItem
from app.schemas.isochrone import (
    BoundaryPoint,
    GeoJSONPolygon,
    IsochroneConfidence,
    IsochroneData,
    IsochroneErrorCode,
    IsochroneFailure,
    IsochroneQuality,
    IsochroneRequest,
    IsochroneResult,
    IsochroneSuccess,
)
from app.schemas.routing import RouteStatus, RoutingRequest, RoutingResult


Route = Callable[[RoutingRequest], RoutingResult]


class IsochroneService:
    def __init__(
        self,
        route: Route,
        *,
        direction_count: int = 24,
        initial_radii_m: tuple[float, ...] = DEFAULT_RADII_M,
        refinement_rounds: int = 2,
        extension_step_m: float = 600.0,
        max_radius_m: float = 3000.0,
    ) -> None:
        if direction_count < 3 or refinement_rounds < 0:
            raise ValueError("方向数至少为 3，refinement_rounds 不能为负数。")
        if not initial_radii_m or any(radius <= 0 for radius in initial_radii_m):
            raise ValueError("初始采样半径必须全部大于 0。")
        if extension_step_m <= 0 or max_radius_m <= max(initial_radii_m):
            raise ValueError("外扩采样参数无效。")
        self.route = route
        self.direction_count = direction_count
        self.initial_radii_m = initial_radii_m
        self.refinement_rounds = refinement_rounds
        self.extension_step_m = extension_step_m
        self.max_radius_m = max_radius_m

    def generate(self, request: IsochroneRequest) -> IsochroneResult:
        bearing_values = bearings(self.direction_count)
        measurements: dict[float, list[MeasuredSample]] = {
            bearing: [] for bearing in bearing_values
        }
        sample_count = 0
        success_count = 0
        had_missing_routes = False

        initial = initial_samples(
            request.center,
            direction_count=self.direction_count,
            radii_m=self.initial_radii_m,
        )
        measured, attempted, succeeded, missing = self._measure(request, initial)
        sample_count += attempted
        success_count += succeeded
        had_missing_routes |= missing
        _append_measurements(measurements, measured)

        next_radius = max(self.initial_radii_m) + self.extension_step_m
        while next_radius <= self.max_radius_m:
            missing_bearings = [
                bearing
                for bearing in bearing_values
                if find_bracket(
                    request.center,
                    bearing,
                    measurements[bearing],
                    request.time_limit_s,
                )
                is None
                and _needs_extension(measurements[bearing], request.time_limit_s)
            ]
            if not missing_bearings:
                break
            extension = [
                sample_point(
                    request.center,
                    bearing,
                    next_radius,
                    phase="extend",
                )
                for bearing in missing_bearings
            ]
            measured, attempted, succeeded, missing = self._measure(request, extension)
            sample_count += attempted
            success_count += succeeded
            had_missing_routes |= missing
            _append_measurements(measurements, measured)
            next_radius += self.extension_step_m

        brackets = _find_brackets(request, bearing_values, measurements)
        for round_index in range(self.refinement_rounds):
            candidates = [
                midpoint_sample(request.center, bracket, round_index)
                for bracket in brackets.values()
            ]
            if not candidates:
                break
            measured, attempted, succeeded, missing = self._measure(request, candidates)
            sample_count += attempted
            success_count += succeeded
            had_missing_routes |= missing
            for item in measured:
                bearing = item.sample.bearing_deg
                brackets[bearing] = update_bracket(
                    brackets[bearing], item, request.time_limit_s
                )

        boundary_points = _boundary_points(request, brackets)
        warnings = _warnings(
            had_missing_routes,
            len(boundary_points),
            self.direction_count,
            _confidence(
                len(boundary_points), self.direction_count, success_count, sample_count
            ),
        )
        if len(boundary_points) < 3:
            code = (
                IsochroneErrorCode.ROUTING_FAILED
                if success_count == 0
                else IsochroneErrorCode.INSUFFICIENT_ROUTING_DATA
            )
            return IsochroneResult(
                root=IsochroneFailure(
                    ok=False,
                    error={
                        "code": code,
                        "message": "有效步行测时数据不足，无法生成可靠等时圈。",
                        "retryable": True,
                    },
                    warnings=warnings,
                    meta=Meta(schema_version="1.0", provider="baidu"),
                )
            )

        confidence = _confidence(
            len(boundary_points), self.direction_count, success_count, sample_count
        )
        ring = [[point.location.lng, point.location.lat] for point in boundary_points]
        ring.append(ring[0])
        quality = IsochroneQuality(
            requested_directions=self.direction_count,
            valid_boundary_directions=len(boundary_points),
            routing_sample_count=sample_count,
            routing_success_count=success_count,
            sample_success_rate=success_count / sample_count,
            confidence=confidence,
        )
        return IsochroneResult(
            root=IsochroneSuccess(
                ok=True,
                data=IsochroneData(
                    center=request.center,
                    time_limit_s=request.time_limit_s,
                    geometry=GeoJSONPolygon(type="Polygon", coordinates=[ring]),
                    boundary_points=boundary_points,
                    quality=quality,
                ),
                warnings=warnings,
                meta=Meta(schema_version="1.0", provider="baidu"),
            )
        )

    def _measure(
        self, request: IsochroneRequest, samples: list[SamplePoint]
    ) -> tuple[list[MeasuredSample], int, int, bool]:
        if not samples:
            return [], 0, 0, False
        routing_request = RoutingRequest(
            schema_version="1.0",
            origin=request.center,
            targets=[sample.as_route_target() for sample in samples],
            travel_mode="walking",
        )
        result = self.route(routing_request).root
        if not result.ok:
            return [], len(samples), 0, True
        by_id = {sample.sample_id: sample for sample in samples}
        measured = [
            MeasuredSample(by_id[route.target_id], route.duration_s)
            for route in result.data.routes
            if route.status is RouteStatus.SUCCESS and route.duration_s is not None
        ]
        return measured, len(samples), len(measured), len(measured) != len(samples)


def generate_isochrone(request: IsochroneRequest, route: Route) -> IsochroneResult:
    return IsochroneService(route).generate(request)


def _append_measurements(
    target: dict[float, list[MeasuredSample]], measured: list[MeasuredSample]
) -> None:
    for item in measured:
        target[item.sample.bearing_deg].append(item)


def _needs_extension(measured: list[MeasuredSample], time_limit_s: int) -> bool:
    return bool(measured) and max(item.duration_s for item in measured) <= time_limit_s


def _find_brackets(
    request: IsochroneRequest,
    bearing_values: list[float],
    measurements: dict[float, list[MeasuredSample]],
) -> dict[float, BoundaryBracket]:
    found = {}
    for bearing in bearing_values:
        bracket = find_bracket(
            request.center,
            bearing,
            measurements[bearing],
            request.time_limit_s,
        )
        if bracket is not None:
            found[bearing] = bracket
    return found


def _boundary_points(
    request: IsochroneRequest, brackets: dict[float, BoundaryBracket]
) -> list[BoundaryPoint]:
    points = []
    for bearing, bracket in sorted(brackets.items()):
        radius = interpolate_radius(bracket, request.time_limit_s)
        points.append(
            BoundaryPoint(
                boundary_id=f"boundary:b{bearing:06.2f}".replace(".", "p"),
                bearing_deg=bearing,
                location=destination_point(request.center, bearing, radius),
                radial_distance_m=radius,
            )
        )
    return points


def _confidence(
    valid: int, requested: int, routing_success: int, routing_total: int
) -> IsochroneConfidence:
    valid_rate = valid / requested
    sample_rate = routing_success / routing_total if routing_total else 0.0
    if valid_rate >= 0.8 and sample_rate >= 0.9:
        return IsochroneConfidence.HIGH
    if valid_rate >= 0.6 and sample_rate >= 0.75:
        return IsochroneConfidence.MEDIUM
    return IsochroneConfidence.LOW


def _warnings(
    had_missing_routes: bool,
    valid: int,
    requested: int,
    confidence: IsochroneConfidence,
) -> list[WarningItem]:
    warnings = []
    if had_missing_routes:
        warnings.append(
            WarningItem(
                code="PARTIAL_ROUTING_RESULTS",
                message="部分采样点的步行数据不可用。",
            )
        )
    if valid < requested:
        warnings.extend(
            [
                WarningItem(
                    code="BOUNDARY_GAPS",
                    message="部分方向未能稳定确定目标时间边界。",
                ),
                WarningItem(
                    code="BOUNDARY_NOT_BRACKETED",
                    message="部分方向未形成有效的内外时间夹逼。",
                ),
            ]
        )
    if confidence is IsochroneConfidence.LOW:
        warnings.append(
            WarningItem(code="LOW_CONFIDENCE", message="等时圈结果置信度较低。")
        )
    return warnings
