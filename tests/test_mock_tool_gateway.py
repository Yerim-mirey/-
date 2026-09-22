"""Offline contract tests for the future Agent-to-Tool boundary."""

import json
import math
import socket
from pathlib import Path

import pytest

from app.agent_tools.gateway import ToolGateway
from app.agent_tools.mock_gateway import MockExchange, MockScenarioError, MockToolGateway
from app.agent_tools.mock_scenarios import load_contract_exchanges
from app.schemas.blindspot import BlindspotRequest, BlindspotResult, validate_blindspot_exchange
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult, validate_diagnosis_exchange
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.location import CoordinateInput, LocationRequest, LocationResult, LocationSource
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def fixture(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


# This helper intentionally accepts any ToolGateway. Reuse it for MVPToolGateway.
def assert_gateway_contract(gateway: ToolGateway, method: str, request, result_type):
    result = getattr(gateway, method)(request)
    assert type(result) is result_type
    assert type(result).model_validate_json(result.model_dump_json()) == result
    assert result.root.meta.schema_version == request.schema_version
    if not result.root.ok:
        return result
    data = result.root.data
    if method == "resolve_location":
        if isinstance(request.input, CoordinateInput):
            assert (data.center.lng, data.center.lat, data.center.crs) == (
                request.input.lng, request.input.lat, request.input.crs
            )
            assert data.source is LocationSource.INPUT_COORDINATE
        else:
            assert data.source is LocationSource.BAIDU_GEOCODING
    elif method == "search_pois":
        assert data.center == request.center
        assert {poi.facility_type for poi in data.pois} <= set(request.facility_types)
        for poi in data.pois:
            lat1, lat2 = math.radians(request.center.lat), math.radians(poi.location.lat)
            delta_lat = lat2 - lat1
            delta_lng = math.radians(poi.location.lng - request.center.lng)
            haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
            assert 2 * 6_371_008.8 * math.asin(min(1.0, math.sqrt(haversine))) <= request.search_radius_m, "POI outside radius"
        counts = data.counts.model_dump()
        if any(counts[facility.value] is None for facility in request.facility_types):
            assert any(warning.code == "PARTIAL_POI_RESULTS" for warning in result.root.warnings), "PARTIAL_POI_RESULTS warning required"
    elif method == "calculate_walking_times":
        assert data.origin == request.origin
        assert data.travel_mode == request.travel_mode
        assert {route.target_id: route.location for route in data.routes} == {
            target.target_id: target.location for target in request.targets
        }
    elif method == "generate_isochrone":
        assert data.center == request.center
        assert data.time_limit_s == request.time_limit_s
    elif method == "detect_blindspots":
        validate_blindspot_exchange(request, result)
    elif method == "diagnose_community":
        validate_diagnosis_exchange(request, result)
        if isinstance(request.location, CoordinateInput):
            assert (data.center.lng, data.center.lat, data.center.crs) == (
                request.location.lng, request.location.lat, request.location.crs
            )
    return result


@pytest.mark.parametrize(
    ("method", "request_type", "result_type", "fixture_prefix"),
    [
        ("resolve_location", LocationRequest, LocationResult, "location"),
        ("search_pois", POISearchRequest, POISearchResult, "poi-search"),
        ("calculate_walking_times", RoutingRequest, RoutingResult, "routing"),
        ("generate_isochrone", IsochroneRequest, IsochroneResult, "isochrone"),
        ("detect_blindspots", BlindspotRequest, BlindspotResult, "blindspot"),
        ("diagnose_community", DiagnosisRequest, DiagnosisResult, "diagnosis"),
    ],
)
def test_default_gateway_returns_six_typed_contract_exchanges(method, request_type, result_type, fixture_prefix):
    gateway = MockToolGateway()
    assert isinstance(gateway, ToolGateway)
    request = request_type.model_validate(fixture(f"{fixture_prefix}-request.example.json"))
    result = assert_gateway_contract(gateway, method, request, result_type)
    assert result.root.ok is True
    assert len(gateway.calls) == 1
    assert gateway.calls[0].method == method
    assert json.loads(gateway.calls[0].request_json) == request.model_dump(mode="json")


def test_unconfigured_request_fails_explicitly_and_records_attempt():
    gateway = MockToolGateway()
    request = LocationRequest.model_validate(fixture("location-request.example.json"))
    changed = request.model_copy(deep=True)
    changed.input.query = "另一处地址"
    with pytest.raises(MockScenarioError, match="resolve_location"):
        gateway.resolve_location(changed)
    assert len(gateway.calls) == 1
    assert gateway.calls[0].method == "resolve_location"


def test_registration_canonicalizes_request_and_returns_defensive_copies():
    request = POISearchRequest.model_validate(fixture("poi-search-request.example.json"))
    result = POISearchResult.model_validate(fixture("poi-search-result.example.json"))
    gateway = MockToolGateway([MockExchange("search_pois", request, result)])
    request_from_reordered_json = POISearchRequest.model_validate(
        {key: value for key, value in reversed(list(request.model_dump(mode="json").items()))}
    )
    result.root.data.pois[0].name = "outside mutation"
    first = gateway.search_pois(request_from_reordered_json)
    first.root.data.pois[0].name = "caller mutation"
    second = gateway.search_pois(request)
    assert second.root.data.pois[0].name == "示例大药房"
    assert first is not second
    assert [call.method for call in gateway.calls] == ["search_pois", "search_pois"]


def test_business_failure_is_returned_not_raised():
    request = LocationRequest.model_validate(fixture("location-request.example.json"))
    failure = LocationResult.model_validate(fixture("location-failure.example.json"))
    gateway = MockToolGateway([MockExchange("resolve_location", request, failure)])
    actual = gateway.resolve_location(request)
    assert actual.root.ok is False
    assert actual.root.error.code.value == "LOCATION_NOT_FOUND"


@pytest.mark.parametrize("outcome", ["success", "partial", "failure"])
def test_diagnosis_outcomes_use_existing_envelopes(outcome):
    gateway = MockToolGateway(load_contract_exchanges(outcome))
    request = DiagnosisRequest.model_validate(fixture("diagnosis-request.example.json"))
    actual = assert_gateway_contract(gateway, "diagnose_community", request, DiagnosisResult)
    expected_name = "result" if outcome == "success" else outcome
    expected = DiagnosisResult.model_validate(fixture(f"diagnosis-{expected_name}.example.json"))
    assert actual == expected


def test_wrong_model_for_method_is_rejected():
    request = LocationRequest.model_validate(fixture("location-request.example.json"))
    result = POISearchResult.model_validate(fixture("poi-search-result.example.json"))
    with pytest.raises(TypeError, match="search_pois"):
        MockToolGateway([MockExchange("search_pois", request, result)])


@pytest.mark.parametrize(
    ("method", "request_name", "result_name", "request_type", "result_type", "change"),
    [
        ("search_pois", "poi-search-request", "poi-search-result", POISearchRequest, POISearchResult,
         lambda value: value["data"]["center"].update(lng=120.0)),
        ("calculate_walking_times", "routing-request", "routing-result", RoutingRequest, RoutingResult,
         lambda value: value["data"]["routes"][0].update(target_id="wrong-id")),
        ("generate_isochrone", "isochrone-request", "isochrone-result", IsochroneRequest, IsochroneResult,
         lambda value: value["data"].update(time_limit_s=600)),
        ("detect_blindspots", "blindspot-request", "blindspot-result", BlindspotRequest, BlindspotResult,
         lambda value: value["data"]["center"].update(lng=120.0)),
        ("diagnose_community", "diagnosis-request", "diagnosis-result", DiagnosisRequest, DiagnosisResult,
         lambda value: value["data"]["metrics"].reverse()),
    ],
)
def test_mismatched_exchange_is_rejected(method, request_name, result_name, request_type, result_type, change):
    request = request_type.model_validate(fixture(f"{request_name}.example.json"))
    result_data = fixture(f"{result_name}.example.json")
    change(result_data)
    result = result_type.model_validate(result_data)
    with pytest.raises(ValueError):
        MockToolGateway([MockExchange(method, request, result)])


def test_mock_never_opens_network_connection(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network attempted")
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    monkeypatch.delenv("RUN_BAIDU_SMOKE", raising=False)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    gateway = MockToolGateway()
    for method, request_type, prefix in [
        ("resolve_location", LocationRequest, "location"),
        ("search_pois", POISearchRequest, "poi-search"),
        ("calculate_walking_times", RoutingRequest, "routing"),
        ("generate_isochrone", IsochroneRequest, "isochrone"),
        ("detect_blindspots", BlindspotRequest, "blindspot"),
        ("diagnose_community", DiagnosisRequest, "diagnosis"),
    ]:
        getattr(gateway, method)(request_type.model_validate(fixture(f"{prefix}-request.example.json")))
    assert len(gateway.calls) == 6


def test_coordinate_location_exchange_accepts_matching_center():
    payload = fixture("location-request.example.json")
    payload["input"] = {"type": "coordinate", "lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}
    request = LocationRequest.model_validate(payload)
    result_data = fixture("location-result.example.json")
    result_data["data"]["source"] = "input_coordinate"
    result_data["meta"]["provider"] = None
    result = LocationResult.model_validate(result_data)
    gateway = MockToolGateway([MockExchange("resolve_location", request, result)])
    assert gateway.resolve_location(request).root.data.center.lng == 121.506123


def test_blindspot_failure_must_match_upstream_isochrone_state():
    request = BlindspotRequest.model_validate(fixture("blindspot-request.example.json"))
    failure = BlindspotResult.model_validate(fixture("blindspot-failure.example.json"))
    with pytest.raises(ValueError, match="ISOCHRONE_UNAVAILABLE"):
        MockToolGateway([MockExchange("detect_blindspots", request, failure)])


def test_coordinate_location_rejects_geocoding_source():
    payload = fixture("location-request.example.json")
    payload["input"] = {"type": "coordinate", "lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}
    request = LocationRequest.model_validate(payload)
    result = LocationResult.model_validate(fixture("location-result.example.json"))
    with pytest.raises(ValueError, match="source"):
        MockToolGateway([MockExchange("resolve_location", request, result)])


def test_diagnosis_rejects_result_from_another_coordinate():
    request_data = fixture("diagnosis-request.example.json")
    request_data["location"].update(lng=120.0)
    request = DiagnosisRequest.model_validate(request_data)
    result = DiagnosisResult.model_validate(fixture("diagnosis-result.example.json"))
    with pytest.raises(ValueError, match="Diagnosis.*center"):
        MockToolGateway([MockExchange("diagnose_community", request, result)])


def test_address_location_rejects_coordinate_source():
    request = LocationRequest.model_validate(fixture("location-request.example.json"))
    result_data = fixture("location-result.example.json")
    result_data["data"]["source"] = "input_coordinate"
    result_data["meta"]["provider"] = None
    result = LocationResult.model_validate(result_data)
    with pytest.raises(ValueError, match="Address Location.*source"):
        MockToolGateway([MockExchange("resolve_location", request, result)])


def test_poi_unknown_count_requires_partial_warning():
    request = POISearchRequest.model_validate(fixture("poi-search-request.example.json"))
    result_data = fixture("poi-search-result.example.json")
    result_data["data"]["counts"]["pharmacy"] = None
    result = POISearchResult.model_validate(result_data)
    with pytest.raises(ValueError, match="PARTIAL_POI_RESULTS"):
        MockToolGateway([MockExchange("search_pois", request, result)])


def test_poi_result_must_fit_requested_search_radius():
    request_data = fixture("poi-search-request.example.json")
    request_data["search_radius_m"] = 1
    request = POISearchRequest.model_validate(request_data)
    result = POISearchResult.model_validate(fixture("poi-search-result.example.json"))
    with pytest.raises(ValueError, match="radius"):
        MockToolGateway([MockExchange("search_pois", request, result)])


def test_reusable_gateway_contract_rejects_wrong_poi_center():
    request = POISearchRequest.model_validate(fixture("poi-search-request.example.json"))
    payload = fixture("poi-search-result.example.json")
    payload["data"]["center"].update(lng=120.0)
    wrong_result = POISearchResult.model_validate(payload)

    class WrongPOIGateway:
        def search_pois(self, request):
            return wrong_result

    with pytest.raises(AssertionError):
        assert_gateway_contract(WrongPOIGateway(), "search_pois", request, POISearchResult)


def test_reusable_gateway_contract_rejects_out_of_radius_poi():
    request_data = fixture("poi-search-request.example.json")
    request_data["search_radius_m"] = 1
    request = POISearchRequest.model_validate(request_data)
    result = POISearchResult.model_validate(fixture("poi-search-result.example.json"))

    class WrongPOIGateway:
        def search_pois(self, request):
            return result

    with pytest.raises(AssertionError, match="radius"):
        assert_gateway_contract(WrongPOIGateway(), "search_pois", request, POISearchResult)


def test_reusable_gateway_contract_rejects_unmarked_partial_poi():
    request = POISearchRequest.model_validate(fixture("poi-search-request.example.json"))
    payload = fixture("poi-search-result.example.json")
    payload["data"]["counts"]["pharmacy"] = None
    result = POISearchResult.model_validate(payload)

    class WrongPOIGateway:
        def search_pois(self, request):
            return result

    with pytest.raises(AssertionError, match="PARTIAL_POI_RESULTS"):
        assert_gateway_contract(WrongPOIGateway(), "search_pois", request, POISearchResult)
