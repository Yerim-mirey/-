from urllib.parse import parse_qs, urlsplit

import pytest

from app.providers.baidu.routing import (
    BaiduRoutingError,
    BaiduRoutingProvider,
)
from app.schemas.common import CenterPoint


ORIGIN = CenterPoint(lng=121.506123, lat=31.282456, crs="BD09LL")
DESTINATIONS = [
    CenterPoint(lng=121.5081, lat=31.2809, crs="BD09LL"),
    CenterPoint(lng=121.5092, lat=31.2921, crs="BD09LL"),
]


def test_provider_builds_lat_lng_request_and_maps_results() -> None:
    seen = {}

    def transport(url: str, timeout: float):
        seen["url"] = url
        seen["timeout"] = timeout
        return {
            "status": 0,
            "result": [
                {"distance": {"text": "1公里", "value": 842}, "duration": {"text": "12分钟", "value": 735}},
                {"distance": {"value": 1280.4}, "duration": {"value": 1012.2}},
            ],
        }

    routes = BaiduRoutingProvider("secret-ak", transport=transport).route_matrix(
        ORIGIN, DESTINATIONS
    )
    query = parse_qs(urlsplit(seen["url"]).query)
    assert query["origins"] == ["31.282456,121.506123"]
    assert query["destinations"] == [
        "31.280900,121.508100|31.292100,121.509200"
    ]
    assert query["coord_type"] == ["bd09ll"]
    assert routes[0].distance_m == 842
    assert routes[1].duration_s == 1012


def test_provider_rejects_more_than_fifty_destinations() -> None:
    provider = BaiduRoutingProvider("secret", transport=lambda *_: {})
    with pytest.raises(BaiduRoutingError) as caught:
        provider.route_matrix(ORIGIN, [DESTINATIONS[0]] * 51)
    assert caught.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(1, "PROVIDER_ERROR", True), (2, "INVALID_REQUEST", False), (4, "RATE_LIMITED", True), (210, "PROVIDER_ERROR", False)],
)
def test_provider_maps_baidu_status(status: int, code: str, retryable: bool) -> None:
    provider = BaiduRoutingProvider(
        "secret", transport=lambda *_: {"status": status, "message": "raw secret"}
    )
    with pytest.raises(BaiduRoutingError) as caught:
        provider.route_matrix(ORIGIN, DESTINATIONS[:1])
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "secret" not in str(caught.value)


def test_provider_rejects_wrong_result_count() -> None:
    provider = BaiduRoutingProvider(
        "secret", transport=lambda *_: {"status": 0, "result": []}
    )
    with pytest.raises(BaiduRoutingError) as caught:
        provider.route_matrix(ORIGIN, DESTINATIONS[:1])
    assert caught.value.code == "INVALID_RESPONSE"


def test_provider_rejects_inconsistent_zero_values() -> None:
    provider = BaiduRoutingProvider(
        "secret",
        transport=lambda *_: {
            "status": 0,
            "result": [{"distance": {"value": 0}, "duration": {"value": 10}}],
        },
    )
    with pytest.raises(BaiduRoutingError):
        provider.route_matrix(ORIGIN, DESTINATIONS[:1])
