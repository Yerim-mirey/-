"""Location service behavior checks."""

import pytest

from app.modules.location.service import resolve_location
from app.providers.baidu.geocoding import BaiduGeocodingError, GeocodingMatch
from app.schemas.common import CenterPoint
from app.schemas.location import LocationErrorCode, LocationRequest


def test_coordinate_input_returns_without_calling_provider() -> None:
    request = LocationRequest.model_validate(
        {
            "schema_version": "1.0",
            "input": {
                "type": "coordinate",
                "lng": 121.506123,
                "lat": 31.282456,
                "crs": "BD09LL",
            },
        }
    )

    def must_not_run(query: str, city: str | None) -> GeocodingMatch:
        raise AssertionError("坐标输入不应调用 Provider")

    result = resolve_location(request, must_not_run)
    assert result.root.ok is True
    assert result.root.data.center == CenterPoint(
        lng=121.506123,
        lat=31.282456,
        crs="BD09LL",
    )
    assert result.root.data.source == "input_coordinate"
    assert result.root.meta.provider is None


def test_address_input_uses_geocoding_result() -> None:
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
    received: list[tuple[str, str | None]] = []

    def fake_geocode(query: str, city: str | None) -> GeocodingMatch:
        received.append((query, city))
        return GeocodingMatch(
            center=CenterPoint(lng=121.506123, lat=31.282456, crs="BD09LL"),
            label="同济大学四平路校区",
            formatted_address="上海市杨浦区四平路1239号",
        )

    result = resolve_location(request, fake_geocode)
    assert received == [("同济大学四平路校区", "上海市")]
    assert result.root.ok is True
    assert result.root.data.center.crs == "BD09LL"
    assert result.root.data.source == "baidu_geocoding"
    assert result.root.meta.provider == "baidu"


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (LocationErrorCode.LOCATION_NOT_FOUND, False),
        (LocationErrorCode.TIMEOUT, True),
        (LocationErrorCode.RATE_LIMITED, True),
        (LocationErrorCode.PROVIDER_ERROR, False),
    ],
)
def test_provider_errors_become_failure_envelopes(
    code: LocationErrorCode,
    retryable: bool,
) -> None:
    request = LocationRequest.model_validate(
        {
            "schema_version": "1.0",
            "input": {
                "type": "address",
                "query": "测试地址",
                "city": "上海市",
            },
        }
    )

    def failing_geocode(query: str, city: str | None) -> GeocodingMatch:
        raise BaiduGeocodingError(code, "测试错误", retryable=retryable)

    result = resolve_location(request, failing_geocode)
    assert result.root.ok is False
    assert result.root.error.code == code
    assert result.root.error.retryable is retryable
    assert result.root.meta.provider == "baidu"
