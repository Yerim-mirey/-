"""Phase 9: one Gateway over the real Core services, with offline providers."""

import json
import math
import socket
from pathlib import Path

import pytest

from app.agent_runtime.runner import run_agent
from app.agent_tools.gateway import ToolGateway
from app.agent_tools.mvp_gateway import MVPToolGateway
from app.agents.orchestrator import OrchestratorAgent
from app.providers.baidu.geocoding import GeocodingMatch
from app.providers.baidu.poi import POIProviderError, ProviderPOI
from app.providers.baidu.routing import BaiduRoutingError, ProviderRoute
from app.schemas.blindspot import BlindspotRequest, BlindspotResult
from app.schemas.common import CenterPoint
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult
from tests.test_mock_tool_gateway import assert_gateway_contract


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
CENTER = CenterPoint(lng=121.506123, lat=31.282456, crs="BD09LL")


def example(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("Phase 9 tests must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


class FakePOIProvider:
    def search(self, keyword, center, _radius_m):
        if keyword == "菜市场":
            return [ProviderPOI(uid="market-1", name="示例菜场", lng=center.lng, lat=center.lat, address=None)]
        return []


class FakeRoutingProvider:
    def __init__(self):
        self.calls = 0

    def route_matrix(self, origin, destinations):
        self.calls += 1
        routes = []
        for point in destinations:
            meters = round(math.hypot((point.lng - origin.lng) * 95_000, (point.lat - origin.lat) * 111_000))
            routes.append(ProviderRoute(distance_m=meters, duration_s=meters))
        return routes


def gateway(*, poi_provider=None, routing_provider=None, geocode=None):
    return MVPToolGateway(
        geocode=geocode or (lambda query, city: GeocodingMatch(center=CENTER, label=query)),
        poi_provider=poi_provider or FakePOIProvider(),
        routing_provider=routing_provider or FakeRoutingProvider(),
    )


@pytest.mark.parametrize(
    ("method", "request_type", "result_type", "prefix"),
    [
        ("resolve_location", LocationRequest, LocationResult, "location"),
        ("search_pois", POISearchRequest, POISearchResult, "poi-search"),
        ("calculate_walking_times", RoutingRequest, RoutingResult, "routing"),
        ("generate_isochrone", IsochroneRequest, IsochroneResult, "isochrone"),
        ("detect_blindspots", BlindspotRequest, BlindspotResult, "blindspot"),
        ("diagnose_community", DiagnosisRequest, DiagnosisResult, "diagnosis"),
    ],
)
def test_six_core_methods_return_v1_exchanges(method, request_type, result_type, prefix):
    tool = gateway()
    assert isinstance(tool, ToolGateway)
    request = request_type.model_validate(example(f"{prefix}-request.example.json"))
    result = assert_gateway_contract(tool, method, request, result_type)
    assert result.root.ok is True
    if method == "diagnose_community":
        assert result.root.data.stages.location.value == "complete"
        assert result.root.data.stages.poi.value == "complete"
        assert result.root.data.stages.routing.value == "complete"
        assert result.root.data.stages.isochrone.value == "complete"
        assert result.root.data.stages.blindspot.value == "complete"
        assert [item.facility_type for item in result.root.data.metrics] == request.facility_types


def test_coordinate_location_does_not_call_geocoder():
    def forbidden(_query, _city):
        raise AssertionError("coordinate input must bypass geocoding")

    request = LocationRequest.model_validate({
        "schema_version": "1.0",
        "input": {"type": "coordinate", **CENTER.model_dump(mode="json")},
    })
    result = assert_gateway_contract(gateway(geocode=forbidden), "resolve_location", request, LocationResult)
    assert result.root.meta.provider is None


def test_provider_failure_and_unknown_counts_keep_core_semantics():
    class LimitedRouting:
        def route_matrix(self, _origin, _destinations):
            raise BaiduRoutingError("RATE_LIMITED", "limited", retryable=True)

    class PartialPOI(FakePOIProvider):
        def search(self, keyword, center, radius_m):
            if keyword == "药房":
                raise POIProviderError("RATE_LIMITED", "limited", True)
            return super().search(keyword, center, radius_m)

    route_request = RoutingRequest.model_validate(example("routing-request.example.json"))
    route_result = assert_gateway_contract(
        gateway(routing_provider=LimitedRouting()),
        "calculate_walking_times", route_request, RoutingResult,
    )
    assert route_result.root.ok is False
    assert route_result.root.error.code.value == "RATE_LIMITED"
    assert route_result.root.error.retryable is True

    poi_request = POISearchRequest.model_validate(example("poi-search-request.example.json"))
    poi_result = assert_gateway_contract(
        gateway(poi_provider=PartialPOI()), "search_pois", poi_request, POISearchResult,
    )
    assert poi_result.root.ok is True
    assert poi_result.root.data.counts.pharmacy is None
    assert any(item.code == "PARTIAL_POI_RESULTS" for item in poi_result.root.warnings)


def test_diagnosis_keeps_partial_and_failure_envelopes():
    class FacilityRouteLimited(FakeRoutingProvider):
        def route_matrix(self, origin, destinations):
            if all(point == origin for point in destinations):
                raise BaiduRoutingError("RATE_LIMITED", "limited", retryable=True)
            return super().route_matrix(origin, destinations)

    class AllRoutesLimited:
        def route_matrix(self, _origin, _destinations):
            raise BaiduRoutingError("RATE_LIMITED", "limited", retryable=True)

    request = DiagnosisRequest.model_validate(example("diagnosis-request.example.json"))
    partial = assert_gateway_contract(
        gateway(routing_provider=FacilityRouteLimited()),
        "diagnose_community", request, DiagnosisResult,
    )
    assert partial.root.ok is True
    assert partial.root.data.stages.routing.value == "unavailable"
    assert partial.root.data.metrics[0].confirmed_reachable_15m_count is None
    assert any(item.code == "PARTIAL_DIAGNOSIS" for item in partial.root.warnings)

    failure = assert_gateway_contract(
        gateway(routing_provider=AllRoutesLimited()),
        "diagnose_community", request, DiagnosisResult,
    )
    assert failure.root.ok is False
    assert failure.root.error.code.value == "ISOCHRONE_FAILED"
    assert failure.root.error.retryable is True


def test_runner_can_replace_mock_gateway_without_changing_agents():
    class ControlledLLM:
        def generate_object(self, *, system_prompt, user_message, response_schema):
            assert "Orchestrator" in system_prompt
            return {
                "intent": "facility_query",
                "user_goal": user_message,
                "location": {"type": "coordinate", **CENTER.model_dump(mode="json")},
                "facility_types": ["market"],
                "use_standard_facilities": False,
            }

    state = run_agent(
        run_id="run:phase9-gateway",
        user_message="查询周围菜场",
        orchestrator=OrchestratorAgent(ControlledLLM()),
        gateway=gateway(),
    )
    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert state.evidence.poi.root.data.counts.market == 1
    assert {item.kind.value for item in state.evidence.refs} == {"location", "poi"}


def test_baidu_factory_rejects_empty_key_without_network():
    with pytest.raises(ValueError):
        MVPToolGateway.from_baidu_ak(" ")
    assert isinstance(MVPToolGateway.from_baidu_ak("example-key"), ToolGateway)
