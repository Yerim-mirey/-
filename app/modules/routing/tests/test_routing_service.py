from app.modules.routing.service import RoutingService
from app.providers.baidu.routing import BaiduRoutingError, ProviderRoute
from app.schemas.routing import RouteStatus, RoutingRequest


def request_with(count: int, *, same_first: bool = False) -> RoutingRequest:
    origin = {"lng": 121.5, "lat": 31.2, "crs": "BD09LL"}
    targets = []
    for index in range(count):
        location = origin if same_first and index == 0 else {
            "lng": 121.501 + index * 0.00001,
            "lat": 31.201,
            "crs": "BD09LL",
        }
        targets.append({"target_id": f"target:{index}", "location": location})
    return RoutingRequest.model_validate(
        {
            "schema_version": "1.0",
            "origin": origin,
            "targets": targets,
            "travel_mode": "walking",
        }
    )


class SuccessProvider:
    def __init__(self) -> None:
        self.batch_sizes = []

    def route_matrix(self, origin, destinations):
        self.batch_sizes.append(len(destinations))
        return [ProviderRoute(100 + index, 80 + index) for index in range(len(destinations))]


def test_fifty_one_targets_split_into_fifty_plus_one() -> None:
    provider = SuccessProvider()
    result = RoutingService(provider).route(request_with(51)).root
    assert result.ok is True
    assert provider.batch_sizes == [50, 1]
    assert [route.target_id for route in result.data.routes] == [
        f"target:{index}" for index in range(51)
    ]


def test_one_hundred_twenty_targets_split_correctly() -> None:
    provider = SuccessProvider()
    result = RoutingService(provider).route(request_with(120)).root
    assert result.ok is True
    assert provider.batch_sizes == [50, 50, 20]
    assert result.data.summary.success == 120


class SecondBatchFails(SuccessProvider):
    def route_matrix(self, origin, destinations):
        self.batch_sizes.append(len(destinations))
        if len(self.batch_sizes) == 2:
            raise BaiduRoutingError("TIMEOUT", "timeout", retryable=True)
        return [ProviderRoute(100, 80) for _ in destinations]


def test_failed_batch_becomes_unavailable_without_losing_targets() -> None:
    provider = SecondBatchFails()
    result = RoutingService(provider, max_retries=0).route(request_with(70)).root
    assert result.ok is True
    assert len(result.data.routes) == 70
    assert result.data.summary.success == 50
    assert result.data.summary.unavailable == 20
    assert result.data.routes[50].status is RouteStatus.UNAVAILABLE
    assert [warning.code for warning in result.warnings] == ["PARTIAL_ROUTING_RESULTS"]


class AlwaysFails:
    def route_matrix(self, origin, destinations):
        raise BaiduRoutingError("RATE_LIMITED", "limited", retryable=True)


def test_all_batches_failing_returns_failure() -> None:
    result = RoutingService(AlwaysFails(), max_retries=0).route(request_with(2)).root
    assert result.ok is False
    assert result.error.code.value == "RATE_LIMITED"
    assert result.error.retryable is True


class ZeroProvider:
    def route_matrix(self, origin, destinations):
        return [ProviderRoute(0, 0) for _ in destinations]


def test_zero_values_distinguish_same_point_from_no_route() -> None:
    result = RoutingService(ZeroProvider()).route(request_with(2, same_first=True)).root
    assert result.data.routes[0].status is RouteStatus.SUCCESS
    assert result.data.routes[0].distance_m == 0
    assert result.data.routes[1].status is RouteStatus.NO_ROUTE
    assert result.data.routes[1].distance_m is None
    assert result.data.summary.no_route == 1
    assert [warning.code for warning in result.warnings] == ["NO_ROUTE_FOR_TARGETS"]


class RetryOnce:
    def __init__(self) -> None:
        self.calls = 0

    def route_matrix(self, origin, destinations):
        self.calls += 1
        if self.calls == 1:
            raise BaiduRoutingError("TIMEOUT", "timeout", retryable=True)
        return [ProviderRoute(10, 10) for _ in destinations]


def test_retryable_error_is_retried_once() -> None:
    provider = RetryOnce()
    slept = []
    result = RoutingService(
        provider, max_retries=1, retry_delay_seconds=0.5, sleep=slept.append
    ).route(request_with(1)).root
    assert result.ok is True
    assert provider.calls == 2
    assert slept == [0.5]
