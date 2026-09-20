"""Offline HTTP acceptance of the real Tool 1–6 chain with fake Baidu I/O."""

import math
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.providers.baidu import geocoding, routing
from app.providers.baidu.geocoding import GeocodingMatch
from app.providers.baidu.poi import POIProviderError, ProviderPOI
from app.providers.baidu.routing import BaiduRoutingError, ProviderRoute
from app.schemas.common import CenterPoint
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult, validate_diagnosis_exchange
from app.schemas.location import LocationResult


CENTER = {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}
COORDINATE = {"type": "coordinate", **CENTER}
ADDRESS = {"type": "address", "query": "同济大学", "city": "上海市"}
FACILITIES = ["market", "pharmacy", "primary_school"]


def diagnosis_payload(location=COORDINATE):
    return {"schema_version": "1.0", "location": location, "facility_types": FACILITIES}


@pytest.fixture
def mock_http(monkeypatch):
    """Keep real API/service wiring; replace only external map-provider I/O."""
    monkeypatch.setenv("BAIDU_MAP_AK", "mock-only-never-return")

    def no_network(*_args, **_kwargs):
        raise AssertionError("Mock E2E must never contact the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(geocoding, "urlopen", no_network)
    monkeypatch.setattr(routing, "urlopen", no_network)

    class FakeGeocoder:
        def __init__(self, ak):
            assert ak == "mock-only-never-return"

        def geocode(self, query, city):
            assert (query, city) == ("同济大学", "上海市")
            return GeocodingMatch(center=CenterPoint(**CENTER), label=query)

    class FakePOI:
        fail_school = False
        radii = []

        def __init__(self, *, ak):
            assert ak == "mock-only-never-return"

        def search(self, keyword, center, radius_m):
            assert center == CenterPoint(**CENTER)
            self.radii.append(radius_m)
            if keyword == "小学" and self.fail_school:
                raise POIProviderError("PROVIDER_ERROR", "mock school failure", False)
            if keyword == "菜市场":
                return [ProviderPOI("market-1", "模拟菜市场", center.lng + 0.003, center.lat, None)]
            if keyword == "药店":
                return [ProviderPOI("pharmacy-1", "模拟药店", center.lng - 0.003, center.lat, "模拟地址")]
            return []

    class FakeRouting:
        fail = False

        def __init__(self, ak):
            assert ak == "mock-only-never-return"

        def route_matrix(self, origin, destinations):
            if self.fail:
                raise BaiduRoutingError("PROVIDER_ERROR", "mock routing failure", retryable=False)
            result = []
            for target in destinations:
                dx = (target.lng - origin.lng) * 111_320 * math.cos(math.radians(origin.lat))
                dy = (target.lat - origin.lat) * 111_320
                distance = round(math.hypot(dx, dy))
                result.append(ProviderRoute(distance_m=distance, duration_s=round(distance / 1.2)))
            return result

    monkeypatch.setattr(api, "BaiduGeocodingProvider", FakeGeocoder)
    monkeypatch.setattr(api, "BaiduPOIProvider", FakePOI)
    monkeypatch.setattr(api, "BaiduRoutingProvider", FakeRouting)
    return TestClient(api.app), FakePOI, FakeRouting


def post_diagnosis(client, location=COORDINATE):
    payload = diagnosis_payload(location)
    response = client.post("/api/v1/diagnosis", json=payload)
    assert response.status_code == 200
    assert "mock-only-never-return" not in response.text
    result = DiagnosisResult.model_validate(response.json())
    validate_diagnosis_exchange(DiagnosisRequest.model_validate(payload), result)
    return result.root


def test_coordinate_to_complete_diagnosis(mock_http):
    client, fake_poi, _ = mock_http
    result = post_diagnosis(client)

    assert result.ok is True
    data = result.data
    assert data.center.model_dump() == CENTER
    assert data.stages.model_dump(mode="json") == {
        "location": "complete", "poi": "complete", "routing": "complete",
        "isochrone": "complete", "blindspot": "complete",
    }
    assert data.poi.counts.model_dump() == {"market": 1, "pharmacy": 1, "primary_school": 0}
    assert all(radius == data.poi_search_radius_m for radius in fake_poi.radii)
    assert data.isochrone.geometry.coordinates[0][0] == data.isochrone.geometry.coordinates[0][-1]
    assert {item.facility_type.value: item.poi_count for item in data.metrics} == {
        "market": 1, "pharmacy": 1, "primary_school": 0,
    }
    school = next(item for item in data.metrics if item.facility_type.value == "primary_school")
    assert school.blind_ratio == pytest.approx(1)
    assert school.unknown_ratio == 0


def test_address_resolution_then_diagnosis_uses_same_center(mock_http):
    client, _, _ = mock_http
    location_response = client.post("/api/v1/location/resolve", json={
        "schema_version": "1.0", "input": ADDRESS,
    })
    assert location_response.status_code == 200
    location = LocationResult.model_validate(location_response.json()).root
    assert location.ok is True

    result = post_diagnosis(client, {"type": "coordinate", **location.data.center.model_dump()})
    assert result.ok is True
    assert result.data.center == location.data.center
    assert result.data.poi.center == location.data.center
    assert result.data.isochrone.center == location.data.center

    direct_result = post_diagnosis(client, ADDRESS)
    assert direct_result.ok is True
    assert direct_result.data.center == location.data.center
    assert direct_result.data.location_label == "同济大学"


def test_partial_school_search_is_unknown_not_zero(mock_http):
    client, fake_poi, _ = mock_http
    fake_poi.fail_school = True
    result = post_diagnosis(client)

    assert result.ok is True
    assert result.data.poi.counts.model_dump() == {
        "market": 1, "pharmacy": 1, "primary_school": None,
    }
    assert result.data.stages.poi.value == "partial"
    school = next(item for item in result.data.metrics if item.facility_type.value == "primary_school")
    assert school.poi_count is None
    assert school.blind_ratio == 0
    assert school.unknown_ratio == pytest.approx(1)
    assert {item.code for item in result.warnings} >= {"PARTIAL_POI_RESULTS", "PARTIAL_DIAGNOSIS"}


def test_routing_outage_returns_isochrone_failure_envelope(mock_http):
    client, _, fake_routing = mock_http
    fake_routing.fail = True
    result = post_diagnosis(client)

    assert result.ok is False
    assert result.error.code.value == "ISOCHRONE_FAILED"
    assert not hasattr(result, "data")
