"""Routing service with batching, limited retry, and partial-result handling."""

import time
from collections.abc import Callable

from app.modules.routing.batching import iter_batches
from app.providers.baidu.routing import (
    BaiduRoutingError,
    BaiduRoutingProvider,
    ProviderRoute,
)
from app.schemas.common import Meta, WarningItem
from app.schemas.routing import (
    RouteResult,
    RouteStatus,
    RouteTarget,
    RoutingData,
    RoutingErrorCode,
    RoutingFailure,
    RoutingRequest,
    RoutingResult,
    RoutingSuccess,
    RoutingSummary,
)


_PROVIDER_MESSAGE = "百度地图步行批量算路服务请求失败。"


class RoutingService:
    def __init__(
        self,
        provider: BaiduRoutingProvider,
        *,
        max_retries: int = 1,
        retry_delay_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0 or retry_delay_seconds < 0:
            raise ValueError("重试次数和延迟不能为负数。")
        self.provider = provider
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.sleep = sleep

    def route(self, request: RoutingRequest) -> RoutingResult:
        routes: list[RouteResult] = []
        first_error: BaiduRoutingError | None = None
        successful_batches = 0

        for batch in iter_batches(request.targets):
            try:
                provider_routes = self._route_batch(request, batch)
            except BaiduRoutingError as exc:
                first_error = first_error or exc
                routes.extend(_unavailable(target) for target in batch)
                continue
            successful_batches += 1
            routes.extend(
                _public_route(request, target, provider_route)
                for target, provider_route in zip(batch, provider_routes, strict=True)
            )

        meta = Meta(schema_version="1.0", provider="baidu")
        if successful_batches == 0:
            error = first_error or BaiduRoutingError(
                "PROVIDER_ERROR", _PROVIDER_MESSAGE, retryable=True
            )
            return RoutingResult(
                root=RoutingFailure(
                    ok=False,
                    error={
                        "code": _public_error_code(error.code),
                        "message": _PROVIDER_MESSAGE,
                        "retryable": error.retryable,
                    },
                    warnings=[],
                    meta=meta,
                )
            )

        summary = _summary(routes)
        warnings: list[WarningItem] = []
        if summary.unavailable:
            warnings.append(
                WarningItem(
                    code="PARTIAL_ROUTING_RESULTS",
                    message="部分目标点的步行数据暂时不可用。",
                )
            )
        if summary.no_route:
            warnings.append(
                WarningItem(
                    code="NO_ROUTE_FOR_TARGETS",
                    message="部分目标点没有可用步行路线。",
                )
            )
        return RoutingResult(
            root=RoutingSuccess(
                ok=True,
                data=RoutingData(
                    origin=request.origin,
                    travel_mode=request.travel_mode,
                    routes=routes,
                    summary=summary,
                ),
                warnings=warnings,
                meta=meta,
            )
        )

    def _route_batch(
        self, request: RoutingRequest, batch: list[RouteTarget]
    ) -> list[ProviderRoute]:
        for attempt in range(self.max_retries + 1):
            try:
                return self.provider.route_matrix(
                    request.origin, [target.location for target in batch]
                )
            except BaiduRoutingError as exc:
                if not exc.retryable or attempt == self.max_retries:
                    raise
                self.sleep(self.retry_delay_seconds * (attempt + 1))
        raise AssertionError("unreachable")


def route_targets(
    request: RoutingRequest, provider: BaiduRoutingProvider
) -> RoutingResult:
    return RoutingService(provider).route(request)


def _public_route(
    request: RoutingRequest, target: RouteTarget, route: ProviderRoute
) -> RouteResult:
    same_point = target.location == request.origin
    no_route = route.distance_m == 0 and route.duration_s == 0 and not same_point
    return RouteResult(
        target_id=target.target_id,
        location=target.location,
        status=RouteStatus.NO_ROUTE if no_route else RouteStatus.SUCCESS,
        distance_m=None if no_route else route.distance_m,
        duration_s=None if no_route else route.duration_s,
    )


def _unavailable(target: RouteTarget) -> RouteResult:
    return RouteResult(
        target_id=target.target_id,
        location=target.location,
        status=RouteStatus.UNAVAILABLE,
        distance_m=None,
        duration_s=None,
    )


def _summary(routes: list[RouteResult]) -> RoutingSummary:
    return RoutingSummary(
        requested=len(routes),
        success=sum(route.status is RouteStatus.SUCCESS for route in routes),
        no_route=sum(route.status is RouteStatus.NO_ROUTE for route in routes),
        unavailable=sum(route.status is RouteStatus.UNAVAILABLE for route in routes),
    )


def _public_error_code(code: str) -> RoutingErrorCode:
    try:
        return RoutingErrorCode(code)
    except ValueError:
        return RoutingErrorCode.PROVIDER_ERROR
