"""The V2 provider set is assembled in one place and shares the v1 quota guard.

Regression cover for the gap where the V2 API tool endpoints and
``MVPToolGatewayV2.from_baidu_ak`` each carried their own copy of the recipe
that builds the Baidu POI provider. Two copies meant two profiles that could
drift, and — worse for an AK-scoped console quota — two places that could stop
passing the shared guard. Assembly now lives only in
:func:`app.providers.baidu.live.build_live_providers_v2`.

Every test here blocks sockets, so the suite never reaches Baidu.
"""

import socket

import pytest

from app.agent_tools.mvp_gateway_v2 import MVPToolGatewayV2
from app.api import v2 as api_v2
from app.providers.baidu.live import (
    V2_POI_MAX_PAGES,
    V2_POI_TIMEOUT_SECONDS,
    build_live_providers,
    build_live_providers_v2,
)
from app.providers.baidu.poi import POIProviderError
from app.providers.baidu.quota import reset_guards
from app.providers.baidu.routing import BaiduRoutingError
from app.schemas.common import CenterPoint


@pytest.fixture
def no_socket(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("building the V2 tool set must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_v2_providers_reuse_the_v1_guard_and_transports(no_socket):
    reset_guards()

    v1 = build_live_providers("ak-v2-assembly")
    v2 = build_live_providers_v2("ak-v2-assembly")

    # One AK keeps one budget no matter which layer asked for the providers:
    # provider objects are rebuilt per call, the guard is what they share.
    assert v2.guard is v1.guard
    assert v2.poi.quota_guard is v1.guard
    assert v2.routing.quota_guard is v1.guard
    assert v2.geocoding.quota_guard is v1.guard


def test_v2_poi_profile_is_applied_in_one_place(no_socket):
    reset_guards()

    v2 = build_live_providers_v2("ak-v2-profile")
    v1 = build_live_providers("ak-v2-profile")

    assert v2.poi.quota_guard is v2.guard
    assert (v2.poi._timeout, v2.poi._max_pages) == (
        V2_POI_TIMEOUT_SECONDS,
        V2_POI_MAX_PAGES,
    )
    # The profile is deliberately tighter than the v1 provider's defaults.
    assert (v1.poi._timeout, v1.poi._max_pages) != (
        V2_POI_TIMEOUT_SECONDS,
        V2_POI_MAX_PAGES,
    )


def test_v2_gateway_and_api_endpoints_share_one_guard(monkeypatch, no_socket):
    reset_guards()
    monkeypatch.setenv("BAIDU_MAP_AK", "ak-v2-shared-entry-points")

    gateway = MVPToolGatewayV2.from_baidu_ak("ak-v2-shared-entry-points")
    service = api_v2.get_poi_service_v2()

    # ``_poi`` is the gateway's POIServiceV2; ``provider`` on it is public.
    gateway_guard = gateway._poi.provider.quota_guard
    assert gateway_guard is not None
    assert gateway_guard is service.provider.quota_guard
    assert gateway_guard is build_live_providers("ak-v2-shared-entry-points").guard


def test_v2_providers_without_an_ak_fail_explicitly_offline(no_socket):
    reset_guards()

    providers = build_live_providers_v2("   ")

    assert providers.guard is None
    assert providers.geocoding is None
    # No AK is an explicit refusal, never a silent attempt to reach the network.
    with pytest.raises(POIProviderError) as poi_error:
        providers.poi.search(
            "超市", CenterPoint(lng=116.35, lat=39.99, crs="BD09LL"), 1000
        )
    assert poi_error.value.code == "MISSING_AK"
    assert poi_error.value.retryable is False

    with pytest.raises(BaiduRoutingError) as routing_error:
        providers.routing.route_matrix(None, [None])
    assert routing_error.value.code == "PROVIDER_ERROR"
    assert routing_error.value.retryable is False


def test_v2_api_poi_service_without_an_ak_stays_offline(monkeypatch, no_socket):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    reset_guards()

    service = api_v2.get_poi_service_v2()

    assert service.provider.quota_guard is None
    with pytest.raises(POIProviderError) as poi_error:
        service.provider.search(
            "超市", CenterPoint(lng=116.35, lat=39.99, crs="BD09LL"), 1000
        )
    assert poi_error.value.code == "MISSING_AK"
