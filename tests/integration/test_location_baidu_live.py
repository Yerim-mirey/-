"""Opt-in real Baidu smoke test. Never runs by default."""

import os

import pytest

from app.modules.location.service import resolve_location
from app.providers.baidu.geocoding import BaiduGeocodingProvider
from app.schemas.location import LocationRequest


@pytest.mark.skipif(
    os.getenv("RUN_BAIDU_SMOKE") != "1" or not os.getenv("BAIDU_MAP_AK"),
    reason="需要 RUN_BAIDU_SMOKE=1 和 BAIDU_MAP_AK",
)
def test_real_baidu_geocoding() -> None:
    provider = BaiduGeocodingProvider(
        os.environ["BAIDU_MAP_AK"],
        timeout_seconds=5.0,
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

    result = resolve_location(request, provider.geocode)
    assert result.root.ok is True
    assert result.root.data.center.crs == "BD09LL"
    assert result.root.data.source == "baidu_geocoding"
    assert result.root.meta.provider == "baidu"
    assert -180 <= result.root.data.center.lng <= 180
    assert -90 <= result.root.data.center.lat <= 90
