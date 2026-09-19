"""Offline end-to-end orchestration using real Tool 1/5 and mocked upstream data."""

import json
from pathlib import Path

from app.modules.blindspot.service import analyze_blindspots
from app.modules.diagnosis.service import diagnose
from app.modules.location.service import resolve_location
from app.providers.baidu.geocoding import GeocodingMatch
from app.schemas.blindspot import BlindspotFailure, BlindspotResult
from app.schemas.common import CenterPoint, Meta
from app.schemas.diagnosis import DiagnosisRequest
from app.schemas.isochrone import IsochroneResult
from app.schemas.location import LocationFailure, LocationResult
from app.schemas.poi import POISearchResult
from app.schemas.routing import (
    RouteResult, RoutingData, RoutingFailure, RoutingResult, RoutingSuccess, RoutingSummary,
)


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def request():
    return DiagnosisRequest.model_validate({
        "schema_version": "1.0",
        "location": {"type": "coordinate", "lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
        "facility_types": ["market", "pharmacy", "primary_school"],
        "time_limit_s": 900, "service_distance_m": 1000, "grid_size_m": 200,
    })


def poi(_):
    return POISearchResult.model_validate(load("blindspot-request.example.json")["poi_result"])


def iso(_):
    return IsochroneResult.model_validate(load("blindspot-request.example.json")["isochrone_result"])


def route(request):
    routes = [
        RouteResult(
            target_id=target.target_id, location=target.location,
            status="success", distance_m=800 if "pharmacy" in target.target_id else 1300,
            duration_s=600 if "pharmacy" in target.target_id else 1000,
        ) for target in request.targets
    ]
    return RoutingResult(root=RoutingSuccess(
        ok=True, data=RoutingData(
            origin=request.origin, travel_mode="walking", routes=routes,
            summary=RoutingSummary(requested=len(routes), success=len(routes), no_route=0, unavailable=0),
        ), warnings=[], meta=Meta(schema_version="1.0", provider=None),
    ))


def run(diagnosis_request=None, **changes):
    services = dict(
        resolve_location=resolve_location,
        search_pois=poi,
        route=route,
        generate_isochrone=iso,
        analyze_blindspots=analyze_blindspots,
    )
    services.update(changes)
    return diagnose(diagnosis_request or request(), **services)


def metrics(result):
    return {item.facility_type.value: item for item in result.root.data.metrics}


def test_full_chain_uses_isochrone_radius_and_route_durations():
    radii = []

    def search(payload):
        radii.append(payload.search_radius_m)
        return poi(payload)

    result = run(search_pois=search)
    assert result.root.ok
    assert radii == [1170]
    assert result.root.data.stages.model_dump(mode="json") == {
        "location": "complete", "poi": "complete", "routing": "complete",
        "isochrone": "complete", "blindspot": "complete",
    }
    assert result.root.data.quality.confidence.value == "high"
    assert metrics(result)["market"].poi_count == 0
    assert metrics(result)["market"].blind_ratio == 1
    assert metrics(result)["pharmacy"].confirmed_reachable_15m_count == 1
    assert metrics(result)["pharmacy"].covered_ratio == 1
    assert metrics(result)["primary_school"].confirmed_reachable_15m_count == 0
    assert metrics(result)["primary_school"].blind_ratio == 1


def test_location_or_isochrone_failure_returns_failure_envelope():
    def failed_location(_):
        return LocationResult(root=LocationFailure(
            ok=False, error={"code": "LOCATION_NOT_FOUND", "message": "Not found", "retryable": False},
            warnings=[], meta=Meta(schema_version="1.0", provider=None),
        ))

    location_result = run(resolve_location=failed_location)
    assert not location_result.root.ok
    assert location_result.root.error.code.value == "LOCATION_FAILED"

    iso_result = run(generate_isochrone=lambda _: IsochroneResult.model_validate(load("isochrone-failure.example.json")))
    assert not iso_result.root.ok
    assert iso_result.root.error.code.value == "ISOCHRONE_FAILED"


def test_poi_failure_keeps_unknown_not_zero():
    result = run(search_pois=lambda _: POISearchResult.model_validate(load("poi-search-failure.example.json")))
    assert result.root.ok
    assert result.root.data.poi is None
    assert result.root.data.stages.poi.value == "unavailable"
    assert all(item.poi_count is None for item in result.root.data.metrics)
    assert all(item.confirmed_reachable_15m_count is None for item in result.root.data.metrics)
    assert all(item.unknown_ratio == 1 for item in result.root.data.metrics)
    assert result.root.data.quality.confidence.value == "low"


def test_routing_failure_keeps_facility_metrics_unknown():
    def failed_route(_):
        return RoutingResult(root=RoutingFailure(
            ok=False, error={"code": "PROVIDER_ERROR", "message": "Unavailable", "retryable": True},
            warnings=[], meta=Meta(schema_version="1.0", provider=None),
        ))

    result = run(route=failed_route)
    assert result.root.ok
    assert result.root.data.facility_routes is None
    assert result.root.data.stages.routing.value == "unavailable"
    assert metrics(result)["pharmacy"].confirmed_reachable_15m_count is None
    assert metrics(result)["pharmacy"].unresolved_route_count == 1
    assert metrics(result)["market"].confirmed_reachable_15m_count == 0
    assert result.root.data.quality.confidence.value == "low"


def test_empty_complete_poi_skips_facility_routing():
    def empty(_):
        payload = load("blindspot-request.example.json")["poi_result"]
        payload["data"]["pois"] = []
        payload["data"]["counts"] = {"market": 0, "pharmacy": 0, "primary_school": 0}
        return POISearchResult.model_validate(payload)

    def no_route(_):
        raise AssertionError("No POI target needs routing")

    result = run(search_pois=empty, route=no_route)
    assert result.root.ok
    assert result.root.data.stages.routing.value == "skipped"
    assert all(item.poi_count == 0 for item in result.root.data.metrics)
    assert all(item.confirmed_reachable_15m_count == 0 for item in result.root.data.metrics)
    assert all(item.blind_ratio == 1 for item in result.root.data.metrics)


def test_blindspot_failure_preserves_other_successful_stages():
    def failed_blindspot(_, __):
        return BlindspotResult(root=BlindspotFailure(
            ok=False, error={"code": "COMPUTATION_ERROR", "message": "Unavailable", "retryable": False},
            warnings=[], meta=Meta(schema_version="1.0", provider=None),
        ))

    result = run(analyze_blindspots=failed_blindspot)
    assert result.root.ok
    assert result.root.data.blindspot is None
    assert result.root.data.stages.blindspot.value == "unavailable"
    assert metrics(result)["pharmacy"].covered_ratio is None
    assert metrics(result)["pharmacy"].confirmed_reachable_15m_count == 1
    assert result.root.data.quality.confidence.value == "low"


def test_address_input_reuses_location_geocoding_contract():
    payload = load("diagnosis-request.example.json")
    payload["location"] = {"type": "address", "query": "示例地址", "city": "上海市"}
    address_request = DiagnosisRequest.model_validate(payload)

    def location(request):
        return resolve_location(request, geocode=lambda query, city: GeocodingMatch(
            center=CenterPoint(lng=121.506123, lat=31.282456, crs="BD09LL"),
            label=query, formatted_address=f"{city}{query}",
        ))

    result = run(diagnosis_request=address_request, resolve_location=location)
    assert result.root.ok
    assert result.root.data.location_label == "示例地址"
    assert result.root.data.center.crs == "BD09LL"


def test_partial_facility_routing_does_not_become_zero_or_blind():
    def partial_route(request):
        routes = [
            RouteResult(
                target_id=item.target_id, location=item.location,
                status="unavailable" if "pharmacy" in item.target_id else "success",
                distance_m=None if "pharmacy" in item.target_id else 1300,
                duration_s=None if "pharmacy" in item.target_id else 1000,
            ) for item in request.targets
        ]
        return RoutingResult(root=RoutingSuccess(
            ok=True, data=RoutingData(
                origin=request.origin, travel_mode="walking", routes=routes,
                summary=RoutingSummary(
                    requested=len(routes), success=sum(item.status.value == "success" for item in routes),
                    no_route=0, unavailable=sum(item.status.value == "unavailable" for item in routes),
                ),
            ), warnings=[], meta=Meta(schema_version="1.0", provider=None),
        ))

    result = run(route=partial_route)
    assert result.root.ok
    assert result.root.data.stages.routing.value == "partial"
    assert metrics(result)["pharmacy"].unresolved_route_count == 1
    assert metrics(result)["pharmacy"].confirmed_reachable_15m_count == 0
    assert metrics(result)["pharmacy"].unknown_ratio == 1
    assert result.root.data.quality.confidence.value == "low"
