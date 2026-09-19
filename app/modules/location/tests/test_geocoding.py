"""Baidu geocoding provider mapping checks."""

import socket
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from app.modules.location.service import resolve_location
from app.providers.baidu import geocoding as geocoding_module
from app.providers.baidu.geocoding import (
    BaiduGeocodingError,
    BaiduGeocodingProvider,
)
from app.schemas.location import LocationErrorCode, LocationRequest


def test_geocode_builds_request_and_maps_public_fields() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, timeout: float) -> dict:
        captured["query"] = parse_qs(urlparse(url).query)
        captured["timeout"] = timeout
        return {
            "status": 0,
            "result": {
                "location": {"lng": 121.506123, "lat": 31.282456},
                "confidence": 90,
            },
            "poi_infos": [
                {
                    "name": "同济大学四平路校区",
                    "formatted_address": "上海市杨浦区四平路1239号",
                    "uid": "raw-baidu-id",
                }
            ],
        }

    provider = BaiduGeocodingProvider(
        "test-ak",
        timeout_seconds=3.0,
        transport=transport,
    )
    match = provider.geocode("同济大学四平路校区", "上海市")

    query = captured["query"]
    assert query["address"] == ["同济大学四平路校区"]
    assert query["city"] == ["上海市"]
    assert query["output"] == ["json"]
    assert "ret_coordtype" not in query
    assert query["ak"] == ["test-ak"]
    assert captured["timeout"] == 3.0
    assert match.center.crs == "BD09LL"
    assert match.label == "同济大学四平路校区"
    assert match.formatted_address == "上海市杨浦区四平路1239号"
    assert not hasattr(match, "uid")


def test_object_poi_info_shape_is_supported() -> None:
    provider = BaiduGeocodingProvider(
        "test-ak",
        transport=lambda url, timeout: {
            "status": 0,
            "result": {"location": {"lng": 121.5, "lat": 31.2}},
            "poi_infos": {
                "name": "示例地点",
                "formatted_address": "上海市示例路1号",
            },
        },
    )
    match = provider.geocode("示例地点")
    assert match.formatted_address == "上海市示例路1号"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (2, LocationErrorCode.INVALID_REQUEST, False),
        (4, LocationErrorCode.RATE_LIMITED, True),
        (302, LocationErrorCode.RATE_LIMITED, True),
        (1, LocationErrorCode.PROVIDER_ERROR, True),
        (5, LocationErrorCode.PROVIDER_ERROR, False),
    ],
)
def test_baidu_status_is_mapped(
    status: int,
    code: LocationErrorCode,
    retryable: bool,
) -> None:
    provider = BaiduGeocodingProvider(
        "test-ak",
        transport=lambda url, timeout: {"status": status},
    )
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("测试地址")
    assert caught.value.code == code
    assert caught.value.retryable is retryable


def test_missing_location_is_not_a_success() -> None:
    provider = BaiduGeocodingProvider(
        "test-ak",
        transport=lambda url, timeout: {"status": 0, "result": {}},
    )
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("不存在的地址")
    assert caught.value.code == LocationErrorCode.LOCATION_NOT_FOUND


def test_malformed_location_is_provider_error() -> None:
    provider = BaiduGeocodingProvider(
        "test-ak",
        transport=lambda url, timeout: {
            "status": 0,
            "result": {"location": {"lng": "bad", "lat": 31.2}},
        },
    )
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("测试地址")
    assert caught.value.code == LocationErrorCode.PROVIDER_ERROR


def test_public_result_does_not_leak_baidu_fields() -> None:
    provider = BaiduGeocodingProvider(
        "test-ak",
        transport=lambda url, timeout: {
            "status": 0,
            "result": {
                "location": {"lng": 121.506123, "lat": 31.282456},
                "confidence": 90,
            },
            "poi_infos": [
                {
                    "name": "同济大学四平路校区",
                    "formatted_address": "上海市杨浦区四平路1239号",
                    "uid": "raw-baidu-id",
                }
            ],
        },
    )
    request = LocationRequest.model_validate(
        {
            "schema_version": "1.0",
            "input": {
                "type": "address",
                "query": "同济大学四平路校区",
                "city": "上海市",
            },
        }
    )
    result = resolve_location(request, provider.geocode).model_dump()
    assert set(result["data"]) == {
        "center",
        "label",
        "formatted_address",
        "source",
    }
    assert result["data"]["formatted_address"] == "上海市杨浦区四平路1239号"


def test_default_transport_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(url: str, timeout: float) -> None:
        raise socket.timeout

    monkeypatch.setattr(geocoding_module, "urlopen", timeout)
    provider = BaiduGeocodingProvider("test-ak")
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("测试地址")
    assert caught.value.code == LocationErrorCode.TIMEOUT
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    ("http_status", "code", "retryable"),
    [
        (429, LocationErrorCode.RATE_LIMITED, True),
        (500, LocationErrorCode.PROVIDER_ERROR, True),
        (400, LocationErrorCode.PROVIDER_ERROR, False),
    ],
)
def test_default_transport_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
    code: LocationErrorCode,
    retryable: bool,
) -> None:
    def fail(url: str, timeout: float) -> None:
        raise HTTPError(url, http_status, "test", None, None)

    monkeypatch.setattr(geocoding_module, "urlopen", fail)
    provider = BaiduGeocodingProvider("test-ak")
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("测试地址")
    assert caught.value.code == code
    assert caught.value.retryable is retryable


def test_default_transport_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(url: str, timeout: float) -> None:
        raise URLError("offline")

    monkeypatch.setattr(geocoding_module, "urlopen", fail)
    provider = BaiduGeocodingProvider("test-ak")
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("测试地址")
    assert caught.value.code == LocationErrorCode.PROVIDER_ERROR
    assert caught.value.retryable is True


class _FakeResponse:
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"not-json"


def test_default_transport_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        geocoding_module,
        "urlopen",
        lambda url, timeout: _FakeResponse(),
    )
    provider = BaiduGeocodingProvider("test-ak")
    with pytest.raises(BaiduGeocodingError) as caught:
        provider.geocode("测试地址")
    assert caught.value.code == LocationErrorCode.PROVIDER_ERROR
    assert caught.value.retryable is False
