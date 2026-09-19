"""Mock-only checks for the Baidu POI adapter."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.providers.baidu.poi import BaiduPOIProvider, POIProviderError
from app.schemas.common import CenterPoint


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def test_search_builds_bd09ll_request_and_maps_pages() -> None:
    calls: list[str] = []

    def http_get(url: str, *, timeout: float) -> _Response:
        calls.append(url)
        page = int(parse_qs(urlsplit(url).query)["page_num"][0])
        if page == 0:
            return _Response(
                {
                    "status": 0,
                    "message": "ok",
                    "total": 2,
                    "results": [
                        {
                            "uid": "u1",
                            "name": "Market",
                            "location": {"lng": 121.5, "lat": 31.2},
                            "address": "Road 1",
                            "detail_info": {"raw": "ignored"},
                        }
                    ],
                }
            )
        if page == 1:
            return _Response(
                {
                    "status": 0,
                    "message": "ok",
                    "total": 2,
                    "results": [
                        {
                            "uid": None,
                            "name": "School",
                            "location": {"lng": 121.6, "lat": 31.3},
                        }
                    ],
                }
            )
        return _Response({"status": 0, "total": 2, "results": []})

    result = BaiduPOIProvider(ak="test-ak", http_get=http_get).search(
        "小学", CenterPoint(lng=121.5, lat=31.2, crs="BD09LL"), 1500
    )

    assert [poi.name for poi in result] == ["Market", "School"]
    assert result[1].address is None
    query = parse_qs(urlsplit(calls[0]).query)
    assert query["location"] == ["31.2,121.5"]
    assert query["radius"] == ["1500"]
    assert query["radius_limit"] == ["true"]
    assert query["page_size"] == ["20"]
    assert query["page_num"] == ["0"]
    assert len(calls) == 3


def test_inaccurate_total_and_short_page_do_not_stop_search() -> None:
    calls: list[int] = []

    def http_get(url: str, *, timeout: float) -> _Response:
        page = int(parse_qs(urlsplit(url).query)["page_num"][0])
        calls.append(page)
        results = (
            [{"name": f"POI {page}", "location": {"lng": 1, "lat": 2}}]
            if page < 2 else []
        )
        return _Response({"status": 0, "total": 1, "results": results})

    result = BaiduPOIProvider(ak="test-ak", http_get=http_get).search(
        "菜市场", CenterPoint(lng=1, lat=2, crs="BD09LL"), 100
    )

    assert len(result) == 2
    assert calls == [0, 1, 2]


def test_api_error_does_not_echo_ak() -> None:
    provider = BaiduPOIProvider(
        ak="secret-ak",
        http_get=lambda url, *, timeout: _Response(
            {"status": 5, "message": "bad secret-ak"}
        ),
    )

    with pytest.raises(POIProviderError) as caught:
        provider.search("market", CenterPoint(lng=1, lat=2, crs="BD09LL"), 100)

    assert caught.value.code == "BAIDU_STATUS_5"
    assert caught.value.retryable is False
    assert "secret-ak" not in str(caught.value)


def test_timeout_is_retryable() -> None:
    def http_get(url: str, *, timeout: float) -> _Response:
        raise TimeoutError

    provider = BaiduPOIProvider(ak="test-ak", http_get=http_get)

    with pytest.raises(POIProviderError) as caught:
        provider.search("market", CenterPoint(lng=1, lat=2, crs="BD09LL"), 100)

    assert caught.value.code == "TIMEOUT"
    assert caught.value.retryable is True


def test_missing_ak_fails_before_http() -> None:
    called = False

    def http_get(url: str, *, timeout: float) -> _Response:
        nonlocal called
        called = True
        raise AssertionError("HTTP must not be called without an AK")

    provider = BaiduPOIProvider(ak="", http_get=http_get)
    with pytest.raises(POIProviderError) as caught:
        provider.search("market", CenterPoint(lng=1, lat=2, crs="BD09LL"), 100)

    assert caught.value.code == "MISSING_AK"
    assert called is False


def test_truncated_pagination_is_an_error() -> None:
    provider = BaiduPOIProvider(
        ak="test-ak",
        http_get=lambda url, *, timeout: _Response(
            {
                "status": 0,
                "total": 151,
                "results": [
                    {"name": "Market", "location": {"lng": 1, "lat": 2}}
                ],
            }
        ),
    )

    with pytest.raises(POIProviderError) as caught:
        provider.search("market", CenterPoint(lng=1, lat=2, crs="BD09LL"), 100)

    assert caught.value.code == "INCOMPLETE_RESULTS"
