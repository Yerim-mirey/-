"""Mock-only spatial and evidence checks for Blindspot Service."""

import json
from pathlib import Path

from shapely.geometry import shape

from app.modules.blindspot.service import analyze_blindspots
from app.schemas.blindspot import BlindspotRequest, minimum_poi_search_radius_m
from app.schemas.common import Meta
from app.schemas.isochrone import IsochroneResult
from app.schemas.routing import (
    RouteResult,
    RoutingData,
    RoutingFailure,
    RoutingResult,
    RoutingSuccess,
    RoutingSummary,
)


CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "v1"


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def mock_route(request, distances=None):
    distances = distances or {"baidu:mock-pharmacy": 800, "baidu:mock-school": 1300}
    routes = [
        RouteResult(
            target_id=target.target_id, location=target.location,
            status="no_route" if distances[target.target_id] is None else "unavailable" if distances[target.target_id] == "unavailable" else "success",
            distance_m=distances[target.target_id] if isinstance(distances[target.target_id], int) else None,
            duration_s=600 if isinstance(distances[target.target_id], int) else None,
        )
        for target in request.targets
    ]
    return RoutingResult(root=RoutingSuccess(
        ok=True,
        data=RoutingData(
            origin=request.origin, travel_mode="walking", routes=routes,
            summary=RoutingSummary(
                requested=len(routes),
                success=sum(item.status.value == "success" for item in routes),
                no_route=sum(item.status.value == "no_route" for item in routes),
                unavailable=sum(item.status.value == "unavailable" for item in routes),
            ),
        ),
        warnings=[], meta=Meta(schema_version="1.0", provider=None),
    ))


def by_type(result):
    return {item.facility_type.value: item for item in result.root.data.coverage}


def test_complete_zero_near_and_long_routes_are_distinct():
    request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    calls = []

    def route(payload):
        calls.append(payload)
        return mock_route(payload)

    result = analyze_blindspots(request, route)
    assert result.root.ok
    assert result.root.data.quality.grid_cell_count == 1
    assert result.root.data.quality.route_pairs_requested == 2
    assert len(calls) == 1
    assert {target.target_id for target in calls[0].targets} == {
        "baidu:mock-pharmacy", "baidu:mock-school"
    }
    coverage = by_type(result)
    assert coverage["market"].blind_cells == 1  # counts=0, radius complete
    assert coverage["pharmacy"].covered_cells == 1  # walking 800 m
    assert coverage["primary_school"].blind_cells == 1  # walking 1300 m
    domain = shape(result.root.data.analysis_geometry.model_dump())
    for feature in result.root.data.blind_spots.features:
        assert shape(feature.geometry.model_dump()).difference(domain).area < 1e-12
    assert abs(coverage["market"].total_area_m2 - 9510.11) < 0.1


def test_short_radius_and_null_count_are_unknown_but_known_route_covers():
    request = BlindspotRequest.model_validate(load("blindspot-partial-request.example.json"))
    result = analyze_blindspots(
        request, lambda payload: mock_route(payload, {"baidu:mock-school": 800})
    )
    assert result.root.ok
    coverage = by_type(result)
    assert coverage["market"].unknown_cells == 1
    assert coverage["pharmacy"].unknown_cells == 1
    assert coverage["primary_school"].covered_cells == 1  # known 800 m route survives short radius
    assert not result.root.data.blind_spots.features
    assert {item.code for item in result.root.warnings} >= {
        "PARTIAL_POI_RESULTS", "POI_RADIUS_INSUFFICIENT", "UNKNOWN_BLINDSPOT_AREA"
    }


def test_budget_exhaustion_never_turns_untested_poi_into_blind():
    payload = load("blindspot-request.example.json")
    payload["max_route_pairs"] = 1
    result = analyze_blindspots(BlindspotRequest.model_validate(payload), mock_route)
    assert result.root.ok
    assert result.root.data.quality.route_budget_exhausted
    assert result.root.data.quality.route_pairs_requested == 1
    assert by_type(result)["primary_school"].unknown_cells == 1
    assert any(item.code == "ROUTING_BUDGET_EXCEEDED" for item in result.root.warnings)


def test_route_failure_is_unknown_not_blind():
    request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))

    def failed_route(_):
        return RoutingResult(root=RoutingFailure(
            ok=False,
            error={"code": "PROVIDER_ERROR", "message": "Unavailable", "retryable": True},
            warnings=[], meta=Meta(schema_version="1.0", provider=None),
        ))

    result = analyze_blindspots(request, failed_route)
    assert result.root.ok
    coverage = by_type(result)
    assert coverage["market"].blind_cells == 1  # no candidates, no Routing needed
    assert coverage["pharmacy"].unknown_cells == 1
    assert coverage["primary_school"].unknown_cells == 1
    assert result.root.data.quality.route_pairs_unavailable == 2


def test_poi_failure_makes_all_categories_unknown_without_routing():
    payload = load("blindspot-request.example.json")
    payload["poi_result"] = load("poi-search-failure.example.json")
    request = BlindspotRequest.model_validate(payload)

    def route(_):
        raise AssertionError("No POI target should be routed")

    result = analyze_blindspots(request, route)
    assert result.root.ok
    assert all(item.unknown_cells == 1 for item in result.root.data.coverage)
    assert not result.root.data.blind_spots.features


def test_self_intersecting_isochrone_fails_without_repair():
    payload = load("blindspot-request.example.json")
    ring = payload["isochrone_result"]["data"]["geometry"]["coordinates"][0]
    ring[1], ring[2] = ring[2], ring[1]
    points = payload["isochrone_result"]["data"]["boundary_points"]
    points[1]["location"], points[2]["location"] = points[2]["location"], points[1]["location"]
    request = BlindspotRequest.model_validate(payload)
    result = analyze_blindspots(request, mock_route)
    assert not result.root.ok
    assert result.root.error.code.value == "INVALID_GEOMETRY"


def test_exactly_one_kilometre_is_covered_and_no_route_is_blind():
    request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    result = analyze_blindspots(
        request,
        lambda payload: mock_route(payload, {
            "baidu:mock-pharmacy": 1000,
            "baidu:mock-school": None,
        }),
    )
    assert result.root.ok
    assert by_type(result)["pharmacy"].covered_cells == 1
    assert by_type(result)["primary_school"].blind_cells == 1


def test_unavailable_nearby_route_is_unknown():
    request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    result = analyze_blindspots(
        request,
        lambda payload: mock_route(payload, {
            "baidu:mock-pharmacy": "unavailable",
            "baidu:mock-school": 1300,
        }),
    )
    assert result.root.ok
    assert by_type(result)["pharmacy"].unknown_cells == 1
    assert by_type(result)["primary_school"].blind_cells == 1


def test_same_poi_id_in_two_categories_routes_once():
    payload = load("blindspot-request.example.json")
    school = payload["poi_result"]["data"]["pois"][1]
    pharmacy = payload["poi_result"]["data"]["pois"][0]
    school["poi_id"] = pharmacy["poi_id"]
    school["location"] = pharmacy["location"]
    request = BlindspotRequest.model_validate(payload)
    result = analyze_blindspots(request, mock_route)
    assert result.root.ok
    assert result.root.data.quality.route_pairs_requested == 1
    assert by_type(result)["pharmacy"].covered_cells == 1
    assert by_type(result)["primary_school"].covered_cells == 1


def test_straight_line_over_one_kilometre_skips_routing():
    payload = load("blindspot-request.example.json")
    payload["poi_request"]["search_radius_m"] = 3000
    payload["poi_result"]["data"]["pois"][1]["location"]["lng"] += 0.015
    request = BlindspotRequest.model_validate(payload)
    calls = []

    def route(req):
        calls.append(req)
        return mock_route(req)

    result = analyze_blindspots(request, route)
    assert result.root.ok
    assert result.root.data.quality.route_pairs_requested == 1
    assert len(calls[0].targets) == 1
    assert by_type(result)["primary_school"].blind_cells == 1


def test_stored_real_isochrone_is_processed_offline():
    sample = json.loads((CONTRACTS.parents[1] / "data" / "samples" / "isochrone-live.json").read_text(encoding="utf-8"))
    isochrone = IsochroneResult.model_validate(sample).root.data
    payload = load("blindspot-request.example.json")
    payload["poi_request"]["center"] = isochrone.center.model_dump()
    payload["poi_request"]["facility_types"] = ["market"]
    payload["poi_request"]["search_radius_m"] = minimum_poi_search_radius_m(
        isochrone.center, isochrone.geometry
    )
    payload["poi_result"] = load("poi-search-failure.example.json")
    payload["isochrone_result"] = sample
    request = BlindspotRequest.model_validate(payload)

    def no_route(_):
        raise AssertionError("No POI targets exist")

    result = analyze_blindspots(request, no_route)
    assert result.root.ok
    assert result.root.data.quality.grid_cell_count > 1
    assert abs(by_type(result)["market"].unknown_ratio - 1) < 1e-12
