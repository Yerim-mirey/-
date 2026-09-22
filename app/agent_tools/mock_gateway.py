"""Deterministic, validated Tool exchanges for offline Agent development."""

import json
import math
from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel

from app.schemas.blindspot import BlindspotRequest, BlindspotResult, validate_blindspot_exchange
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult, validate_diagnosis_exchange
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.location import AddressInput, CoordinateInput, LocationRequest, LocationResult, LocationSource
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult


@dataclass(frozen=True)
class MockExchange:
    method: str
    request: BaseModel
    result: BaseModel


@dataclass(frozen=True)
class MockCall:
    method: str
    request_json: str


class MockScenarioError(ValueError):
    """No configured exchange matches a Mock call."""


_METHOD_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "resolve_location": (LocationRequest, LocationResult),
    "search_pois": (POISearchRequest, POISearchResult),
    "calculate_walking_times": (RoutingRequest, RoutingResult),
    "generate_isochrone": (IsochroneRequest, IsochroneResult),
    "detect_blindspots": (BlindspotRequest, BlindspotResult),
    "diagnose_community": (DiagnosisRequest, DiagnosisResult),
}


def _canonical_request(request: BaseModel) -> str:
    return json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _distance_m(first, second) -> float:
    """Great-circle distance for the BD09LL point coordinates in v1 samples."""
    first_lat, second_lat = math.radians(first.lat), math.radians(second.lat)
    delta_lat = second_lat - first_lat
    delta_lng = math.radians(second.lng - first.lng)
    arc = math.sin(delta_lat / 2) ** 2 + math.cos(first_lat) * math.cos(second_lat) * math.sin(delta_lng / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(min(1.0, math.sqrt(arc)))


def _validate_exchange(method: str, request: BaseModel, result: BaseModel) -> None:
    if method not in _METHOD_MODELS:
        raise ValueError(f"Unknown Mock Tool method: {method}")
    request_type, result_type = _METHOD_MODELS[method]
    if type(request) is not request_type or type(result) is not result_type:
        raise TypeError(f"{method} requires {request_type.__name__} and {result_type.__name__}")
    if request.schema_version != result.root.meta.schema_version:
        raise ValueError(f"{method} request/result schema versions differ")
    if method == "detect_blindspots":
        validate_blindspot_exchange(request, result)
    elif method == "diagnose_community":
        validate_diagnosis_exchange(request, result)
    if not result.root.ok:
        return

    data = result.root.data
    if method == "resolve_location":
        if isinstance(request.input, CoordinateInput) and (data.center.lng, data.center.lat, data.center.crs) != (
            request.input.lng, request.input.lat, request.input.crs
        ):
            raise ValueError("Location result center differs from coordinate request")
        if isinstance(request.input, CoordinateInput) and (
            data.source is not LocationSource.INPUT_COORDINATE or result.root.meta.provider is not None
        ):
            raise ValueError("Coordinate Location result source/provider differs from request")
        if isinstance(request.input, AddressInput) and data.source is not LocationSource.BAIDU_GEOCODING:
            raise ValueError("Address Location result source differs from request")
    elif method == "search_pois":
        if data.center != request.center:
            raise ValueError("POI result center differs from request")
        requested = set(request.facility_types)
        if any(poi.facility_type not in requested for poi in data.pois):
            raise ValueError("POI result contains an unrequested facility type")
        if any(_distance_m(request.center, poi.location) > request.search_radius_m for poi in data.pois):
            raise ValueError("POI result contains a facility outside request radius")
        requested_names = {item.value for item in requested}
        counts = data.counts.model_dump()
        for facility, count in counts.items():
            observed = sum(poi.facility_type.value == facility for poi in data.pois)
            if (facility not in requested_names and count is not None) or (
                count is not None and count != observed
            ):
                raise ValueError("POI counts differ from request or returned facilities")
        if any(counts[name] is None for name in requested_names) and not any(
            warning.code == "PARTIAL_POI_RESULTS" for warning in result.root.warnings
        ):
            raise ValueError("Unknown POI count requires PARTIAL_POI_RESULTS warning")
    elif method == "calculate_walking_times":
        targets = {target.target_id: target.location for target in request.targets}
        if data.origin != request.origin or data.travel_mode != request.travel_mode or len(data.routes) != len(targets):
            raise ValueError("Routing result differs from request")
        if {route.target_id: route.location for route in data.routes} != targets:
            raise ValueError("Routing targets differ from request")
    elif method == "generate_isochrone":
        if data.center != request.center or data.time_limit_s != request.time_limit_s:
            raise ValueError("Isochrone result differs from request")
    elif method == "diagnose_community" and isinstance(request.location, CoordinateInput):
        location = request.location
        if (data.center.lng, data.center.lat, data.center.crs) != (location.lng, location.lat, location.crs):
            raise ValueError("Diagnosis result center differs from coordinate request")


class MockToolGateway:
    """Exact-match Tool Gateway; accepts only configured v1 exchanges."""

    def __init__(self, exchanges: Iterable[MockExchange] | None = None):
        if exchanges is None:
            from app.agent_tools.mock_scenarios import load_contract_exchanges
            exchanges = load_contract_exchanges()
        self._exchanges: dict[tuple[str, str], BaseModel] = {}
        self.calls: list[MockCall] = []
        for exchange in exchanges:
            _validate_exchange(exchange.method, exchange.request, exchange.result)
            key = (exchange.method, _canonical_request(exchange.request))
            if key in self._exchanges:
                raise ValueError(f"Duplicate Mock exchange for {exchange.method}")
            self._exchanges[key] = type(exchange.result).model_validate_json(exchange.result.model_dump_json())

    def _lookup(self, method: str, request: BaseModel) -> BaseModel:
        request_type, _ = _METHOD_MODELS[method]
        if type(request) is not request_type:
            raise TypeError(f"{method} requires {request_type.__name__}")
        request_json = _canonical_request(request)
        self.calls.append(MockCall(method, request_json))
        result = self._exchanges.get((method, request_json))
        if result is None:
            raise MockScenarioError(f"No Mock exchange configured for {method}: {request_json}")
        return type(result).model_validate_json(result.model_dump_json())

    def resolve_location(self, request: LocationRequest) -> LocationResult:
        return self._lookup("resolve_location", request)

    def search_pois(self, request: POISearchRequest) -> POISearchResult:
        return self._lookup("search_pois", request)

    def calculate_walking_times(self, request: RoutingRequest) -> RoutingResult:
        return self._lookup("calculate_walking_times", request)

    def generate_isochrone(self, request: IsochroneRequest) -> IsochroneResult:
        return self._lookup("generate_isochrone", request)

    def detect_blindspots(self, request: BlindspotRequest) -> BlindspotResult:
        return self._lookup("detect_blindspots", request)

    def diagnose_community(self, request: DiagnosisRequest) -> DiagnosisResult:
        return self._lookup("diagnose_community", request)
