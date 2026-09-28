"""Offline tests for the shared per-AK Baidu quota guard.

Every test here uses a fake clock, so the suite never sleeps and never opens a
socket. The real acceptance run is the only place that spends quota.
"""

import threading

import pytest

from app.agent_tools.mvp_gateway import MVPToolGateway
from app.modules.routing.service import RoutingService
from app.providers.baidu import quota as quota_module
from app.providers.baidu.geocoding import BaiduGeocodingError, BaiduGeocodingProvider
from app.providers.baidu.live import build_live_providers
from app.providers.baidu.poi import BaiduPOIProvider, POIProviderError
from app.providers.baidu.quota import (
    BaiduQuotaGuard,
    QuotaExceededError,
    guard_for_ak,
    reset_guards,
    snapshot,
)
from app.providers.baidu.routing import BaiduRoutingError, BaiduRoutingProvider
from app.schemas.common import CenterPoint
from app.schemas.routing import RouteTarget, RoutingRequest


def point(lng: float, lat: float = 31.282456) -> CenterPoint:
    return CenterPoint(lng=lng, lat=lat, crs="BD09LL")


class FakeClock:
    """A monotonic clock that only advances when someone sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, "guard must never ask for a negative sleep"
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clean_registry():
    reset_guards()
    yield
    reset_guards()


def test_three_route_requests_fit_in_one_window():
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)

    guard.acquire_routes(3)

    assert clock.sleeps == []
    assert snapshot(guard).granted_routes == 3


def test_small_requests_are_not_throttled_to_one_per_second():
    """A paid quota must buy throughput, not one request per second.

    Regression: an earlier revision also forced a minimum gap between calls,
    which capped throughput at ~1 request/second whatever the rate was set to
    and made concurrent callers queue-jump into multi-second delays.
    """
    clock = FakeClock()
    guard = BaiduQuotaGuard(
        routes_per_second=100.0, window_seconds=1.0, clock=clock.monotonic, sleep=clock.sleep
    )

    for _ in range(100):
        guard.acquire_routes(1)

    # 100 single-target calls exactly fill one window and need no waiting.
    assert clock.sleeps == []
    assert snapshot(guard).granted_routes == 100


def test_second_batch_waits_for_the_window_to_clear():
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)

    guard.acquire_routes(3)
    guard.acquire_routes(3)

    # The previous grant must age out of the one-second window first.
    assert clock.sleeps == [pytest.approx(1.0)]
    assert snapshot(guard).granted_routes == 6


def test_pacing_keeps_long_run_within_budget():
    """A 30-target run paced at 3/s must take at least (30/3 - 1) seconds."""
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)

    for _ in range(10):
        guard.acquire_routes(3)

    elapsed = clock.now - 1_000.0
    assert elapsed >= 9.0
    assert snapshot(guard).granted_routes == 30


def test_threads_are_serialized_and_fifo():
    """Concurrent callers share one budget instead of each getting their own."""
    clock = FakeClock()
    lock = threading.Lock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)
    start = threading.Barrier(3)

    def worker(index: int) -> None:
        start.wait()
        guard.acquire_routes(3)
        with lock:
            order.append(index)

    order: list[int] = []
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Three grants of 3 targets each: the first is immediate; the other two
    # each wait one window for the previous grant to age out, so the total
    # advance is 2.0s regardless of thread scheduling.
    assert len(order) == 3
    assert clock.now - 1_000.0 == pytest.approx(2.0)
    assert all(seconds > 0 for seconds in clock.sleeps)
    assert snapshot(guard).granted_routes == 9


def test_refuses_when_wait_exceeds_budget():
    clock = FakeClock()
    guard = BaiduQuotaGuard(
        clock=clock.monotonic, sleep=clock.sleep, max_wait_seconds=0.5
    )
    guard.acquire_routes(3)

    with pytest.raises(QuotaExceededError) as excinfo:
        guard.acquire_routes(3)

    assert excinfo.value.waited_seconds >= 0


def test_quota_wait_is_capped_by_run_deadline():
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)
    guard.acquire_routes(3)

    with pytest.raises(QuotaExceededError, match="时间预算"):
        guard.acquire_routes(3, deadline_monotonic=clock.now + 0.25)

    assert clock.now <= 1_000.25
    assert snapshot(guard).granted_routes == 3


def test_provider_adapters_preserve_deadline_exhaustion_as_non_retryable():
    guard = BaiduQuotaGuard()
    center = point(121.5)
    providers = [
        (
            BaiduGeocodingProvider("ak", quota_guard=guard),
            lambda provider: provider.geocode("上海市人民广场", deadline_monotonic=0),
            BaiduGeocodingError,
            "TIMEOUT",
        ),
        (
            BaiduPOIProvider(ak="ak", quota_guard=guard),
            lambda provider: provider.search(
                "药店", center, 500, deadline_monotonic=0
            ),
            POIProviderError,
            "RUN_DEADLINE_EXCEEDED",
        ),
        (
            BaiduRoutingProvider("ak", quota_guard=guard),
            lambda provider: provider.route_matrix(
                center, [point(121.501)], deadline_monotonic=0
            ),
            BaiduRoutingError,
            "TIMEOUT",
        ),
    ]

    for provider, invoke, error_type, expected_code in providers:
        with pytest.raises(error_type) as caught:
            invoke(provider)
        assert caught.value.code == expected_code
        assert caught.value.retryable is False


def test_over_window_batch_is_rejected_before_any_call():
    guard = BaiduQuotaGuard(routes_per_second=3)

    with pytest.raises(ValueError, match="拆批"):
        guard.acquire_routes(4)


@pytest.mark.parametrize("targets", [0, -1, True, "3"])
def test_invalid_target_counts_are_rejected(targets):
    guard = BaiduQuotaGuard()

    with pytest.raises(ValueError):
        guard.acquire_routes(targets)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"routes_per_second": 0},
        {"routes_per_second": -1},
        {"window_seconds": 0},
        {"max_wait_seconds": 0},
        {"routes_per_second": float("inf")},
    ],
)
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        BaiduQuotaGuard(**kwargs)


def test_window_can_be_widened_but_batch_ceiling_tracks_the_quota():
    guard = BaiduQuotaGuard(routes_per_second=3, window_seconds=2)

    assert guard.max_batch_routes == 6
    guard.acquire_routes(6)


def test_guard_registry_is_shared_per_ak_and_isolated_between_aks():
    first = guard_for_ak("ak-one")
    again = guard_for_ak("ak-one")
    other = guard_for_ak("ak-two")

    assert first is again
    assert first is not other


def test_registry_rejects_conflicting_reconfiguration():
    guard_for_ak("ak-one", routes_per_second=3)

    with pytest.raises(ValueError, match="配额参数"):
        guard_for_ak("ak-one", routes_per_second=50)


def test_registry_applies_the_free_tier_default():
    guard = guard_for_ak("ak-default")

    assert guard.routes_per_second == 3.0
    assert guard.max_batch_routes == 3


def test_registry_rejects_blank_ak():
    with pytest.raises(ValueError):
        guard_for_ak("   ")


def test_live_providers_share_one_guard_per_ak():
    reset_guards()

    first = build_live_providers("ak-shared")
    second = build_live_providers("ak-shared")

    assert first.guard is second.guard
    assert first.routing.quota_guard is first.guard
    assert first.poi.quota_guard is first.guard
    assert first.geocoding.quota_guard is first.guard


def test_live_providers_read_the_quota_override(monkeypatch):
    reset_guards()
    monkeypatch.setenv("BAIDU_ROUTES_PER_SECOND", "20")
    monkeypatch.setenv("BAIDU_QUOTA_MAX_WAIT_SECONDS", "30")

    providers = build_live_providers("ak-override")

    assert providers.guard.routes_per_second == 20.0
    assert providers.guard.max_wait_seconds == 30.0
    assert providers.routing.max_batch_routes == 20


def test_live_providers_reject_a_bad_override(monkeypatch):
    reset_guards()
    monkeypatch.setenv("BAIDU_ROUTES_PER_SECOND", "fast")

    with pytest.raises(ValueError, match="BAIDU_ROUTES_PER_SECOND"):
        build_live_providers("ak-bad")


def test_routing_provider_asks_the_guard_before_the_transport():
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)
    calls: list[str] = []

    def transport(url: str, _timeout: float):
        calls.append(url)
        return {
            "status": 0,
            "result": [{"distance": {"value": 100}, "duration": {"value": 80}}],
        }

    provider = BaiduRoutingProvider(
        "ak-guarded", transport=transport, quota_guard=guard
    )
    provider.route_matrix(point(121.5), [point(121.51)])

    assert len(calls) == 1
    assert snapshot(guard).granted_routes == 1


def test_routing_provider_refusal_is_a_retryable_rate_limit():
    clock = FakeClock()
    guard = BaiduQuotaGuard(
        clock=clock.monotonic, sleep=clock.sleep, max_wait_seconds=0.5
    )
    calls: list[str] = []

    def transport(url: str, _timeout: float):
        calls.append(url)
        return {
            "status": 0,
            "result": [
                {"distance": {"value": 100}, "duration": {"value": 80}}
                for _ in range(3)
            ],
        }

    provider = BaiduRoutingProvider(
        "ak-guarded", transport=transport, quota_guard=guard
    )
    provider.route_matrix(
        point(121.5), [point(121.51), point(121.52), point(121.53)]
    )
    assert len(calls) == 1

    # The window is full, so the next batch cannot be admitted inside the
    # 0.5s wait budget and must surface as a retryable provider error.
    with pytest.raises(BaiduRoutingError) as excinfo:
        provider.route_matrix(point(121.5), [point(121.54)])

    assert excinfo.value.code == "RATE_LIMITED"
    assert excinfo.value.retryable is True
    assert len(calls) == 1, "refused call must not reach the transport"


def test_routing_provider_without_guard_keeps_the_50_route_ceiling():
    provider = BaiduRoutingProvider("ak-unguarded", transport=lambda *_: {})

    assert provider.max_batch_routes == 50
    assert provider.quota_guard is None


def test_routing_service_batches_within_the_quota_window():
    """The service must split by the guard's window, not the hardcoded 50."""
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)
    batch_sizes: list[int] = []

    def transport(_url: str, _timeout: float):
        raise AssertionError("unused")

    provider = BaiduRoutingProvider(
        "ak-guarded", transport=transport, quota_guard=guard
    )
    service = RoutingService(provider)

    def fake_route_batch(_request, batch):
        batch_sizes.append(len(batch))
        return [_route_for(target) for target in batch]

    service._route_batch = fake_route_batch
    targets = [
        RouteTarget(target_id=f"t{index}", location=point(121.5 + index / 1000))
        for index in range(7)
    ]
    result = service.route(
        RoutingRequest(
            schema_version="1.0",
            origin=point(121.5),
            travel_mode="walking",
            targets=targets,
        )
    )

    assert service.batch_size == 3
    assert batch_sizes == [3, 3, 1]
    assert result.root.data.summary.success == 7


def _route_for(target: RouteTarget):
    from app.providers.baidu.routing import ProviderRoute

    return ProviderRoute(distance_m=100, duration_s=80)


def test_explicit_batch_size_is_still_capped_by_baidu_limit():
    provider = BaiduRoutingProvider("ak-plain", transport=lambda *_: {})

    assert RoutingService(provider, batch_size=999).batch_size == 50


@pytest.mark.parametrize("batch_size", [0, -1, "3", True])
def test_invalid_batch_size_is_rejected(batch_size):
    provider = BaiduRoutingProvider("ak-plain", transport=lambda *_: {})

    with pytest.raises(ValueError):
        RoutingService(provider, batch_size=batch_size)


def test_provider_batch_hint_of_zero_is_rejected_not_silently_widened():
    """A non-positive hint must fail loudly instead of falling back to 50."""

    class ZeroHintProvider:
        max_batch_routes = 0

        def route_matrix(self, origin, destinations):
            raise AssertionError("unused")

    with pytest.raises(ValueError):
        RoutingService(ZeroHintProvider())


def test_gateway_from_ak_exposes_the_shared_guard_and_batch_size(monkeypatch):
    reset_guards()
    monkeypatch.setenv("BAIDU_ROUTES_PER_SECOND", "4")

    gateway = MVPToolGateway.from_baidu_ak("ak-gateway")

    assert gateway.quota_guard is not None
    assert gateway.quota_guard.routes_per_second == 4.0
    assert gateway.routing_batch_size == 4


def test_gateway_from_ak_does_not_issue_network_calls(monkeypatch):
    reset_guards()
    sentinel = AssertionError("construction must not open a socket")

    monkeypatch.setattr("socket.socket.connect", lambda *_: (_ for _ in ()).throw(sentinel))

    gateway = MVPToolGateway.from_baidu_ak("ak-quiet")

    assert gateway.quota_guard is not None


def test_route_target_counter_excludes_geocoding_and_poi_call_units():
    clock = FakeClock()
    guard = BaiduQuotaGuard(clock=clock.monotonic, sleep=clock.sleep)

    guard.acquire_routes(2)
    guard.acquire_call()

    current = snapshot(guard)
    assert current.granted_routes == 2
    assert current.granted_calls == 1
    assert current.granted_quota_units == 3
