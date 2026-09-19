"""Executable Tool 5 contract rules; no live Baidu requests or spatial service."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.blindspot import (
    BlindspotRequest,
    BlindspotResult,
    FacilityCoverage,
    validate_blindspot_exchange,
)
from app.schemas.poi import FacilityType


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("blindspot-request.example.json", BlindspotRequest),
        ("blindspot-partial-request.example.json", BlindspotRequest),
        ("blindspot-result.example.json", BlindspotResult),
        ("blindspot-empty.example.json", BlindspotResult),
        ("blindspot-partial.example.json", BlindspotResult),
        ("blindspot-failure.example.json", BlindspotResult),
    ],
)
def test_examples_validate(name, model):
    model.model_validate(load(name))


def test_embedded_root_models_serialize_without_a_root_wrapper():
    request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    payload = request.model_dump(mode="json")
    assert set(payload["poi_result"]) == {"ok", "data", "warnings", "meta"}
    assert set(payload["isochrone_result"]) == {"ok", "data", "warnings", "meta"}


def test_zero_is_complete_only_when_the_radius_covers_the_polygon_buffer():
    complete = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    assert 1100 < complete.minimum_poi_search_radius_m() <= 1200
    assert complete.facility_search_complete(FacilityType.MARKET)  # counts=0

    partial = BlindspotRequest.model_validate(load("blindspot-partial-request.example.json"))
    assert not partial.facility_search_complete(FacilityType.MARKET)  # counts=0, radius short
    assert not partial.facility_search_complete(FacilityType.PHARMACY)  # counts=null

    payload = load("blindspot-partial-request.example.json")
    payload["poi_request"]["search_radius_m"] = complete.minimum_poi_search_radius_m()
    enough_radius = BlindspotRequest.model_validate(payload)
    assert enough_radius.facility_search_complete(FacilityType.MARKET)
    assert not enough_radius.facility_search_complete(FacilityType.PHARMACY)


def test_failed_poi_cannot_supply_blind_evidence():
    payload = load("blindspot-request.example.json")
    payload["poi_result"] = load("poi-search-failure.example.json")
    request = BlindspotRequest.model_validate(payload)
    assert not request.facility_search_complete(FacilityType.MARKET)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["poi_result"]["data"]["counts"].__setitem__("market", None),
        lambda p: p["poi_result"]["data"]["counts"].__setitem__("market", 1),
        lambda p: p["poi_result"]["data"]["center"].__setitem__("lng", 121.0),
        lambda p: p["poi_request"]["facility_types"].append("market"),
        lambda p: p["isochrone_result"]["data"].__setitem__("time_limit_s", 600),
        lambda p: p.__setitem__("max_route_pairs", 5001),
    ],
)
def test_inconsistent_upstream_inputs_are_rejected(mutate):
    payload = load("blindspot-request.example.json")
    mutate(payload)
    with pytest.raises(ValidationError):
        BlindspotRequest.model_validate(payload)


def test_unknown_is_not_an_empty_blindspot_result():
    partial = BlindspotResult.model_validate(load("blindspot-partial.example.json")).root.data
    assert not partial.blind_spots.features
    assert {item.properties.facility_type.value for item in partial.unknown_areas.features} == {
        "market", "pharmacy"
    }
    assert all(item.blind_cells == 0 for item in partial.coverage)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["data"]["coverage"][0].__setitem__("blind_area_m2", 9400.0),
        lambda p: p["data"]["coverage"][0].__setitem__("blind_cells", 0),
        lambda p: p["data"]["blind_spots"]["features"][0]["properties"].__setitem__("classification", "unknown"),
        lambda p: p["data"]["blind_spots"]["features"][0]["geometry"]["coordinates"][0].pop(),
        lambda p: p["data"]["coverage"][0].__setitem__("unexpected", True),
        lambda p: p["warnings"].extend([{"code": "LOW_CONFIDENCE", "message": "重复"}] * 2),
        lambda p: p.__setitem__("error", load("blindspot-failure.example.json")["error"]),
    ],
)
def test_invalid_results_are_rejected(mutate):
    payload = load("blindspot-result.example.json")
    mutate(payload)
    with pytest.raises(ValidationError):
        BlindspotResult.model_validate(payload)


def test_partial_result_requires_unknown_warning_and_low_confidence():
    payload = load("blindspot-partial.example.json")
    payload["warnings"] = [
        item for item in payload["warnings"] if item["code"] != "UNKNOWN_BLINDSPOT_AREA"
    ]
    with pytest.raises(ValidationError):
        BlindspotResult.model_validate(payload)


def test_area_ratio_and_cell_counts_must_agree():
    source = load("blindspot-result.example.json")["data"]["coverage"][0]
    source["covered_cells"] = 1  # No covered area is present.
    with pytest.raises(ValidationError):
        FacilityCoverage.model_validate(source)

    source = {
        "facility_type": "market", "total_area_m2": 100.0,
        "covered_area_m2": 34.0, "blind_area_m2": 33.0, "unknown_area_m2": 33.0,
        "covered_ratio": 0.34008, "blind_ratio": 0.33008, "unknown_ratio": 0.33008,
        "covered_cells": 1, "blind_cells": 1, "unknown_cells": 1,
        "confidence": "low",
    }
    with pytest.raises(ValidationError):
        FacilityCoverage.model_validate(source)

    payload = load("blindspot-partial.example.json")
    payload["data"]["coverage"][0]["confidence"] = "high"
    with pytest.raises(ValidationError):
        BlindspotResult.model_validate(payload)


def test_multi_polygon_clips_are_supported():
    payload = load("blindspot-result.example.json")
    geometry = payload["data"]["blind_spots"]["features"][0]["geometry"]
    geometry["type"] = "MultiPolygon"
    geometry["coordinates"] = [deepcopy(geometry["coordinates"])]
    BlindspotResult.model_validate(payload)


def test_request_result_pairs_validate():
    validate_blindspot_exchange(
        BlindspotRequest.model_validate(load("blindspot-request.example.json")),
        BlindspotResult.model_validate(load("blindspot-result.example.json")),
    )
    validate_blindspot_exchange(
        BlindspotRequest.model_validate(load("blindspot-partial-request.example.json")),
        BlindspotResult.model_validate(load("blindspot-partial.example.json")),
    )


def test_null_count_or_short_radius_cannot_be_published_as_blind():
    blind = BlindspotResult.model_validate(load("blindspot-result.example.json"))
    too_short = BlindspotRequest.model_validate(load("blindspot-partial-request.example.json"))
    with pytest.raises(ValueError):
        validate_blindspot_exchange(too_short, blind)

    payload = load("blindspot-request.example.json")
    payload["poi_result"]["data"]["counts"]["market"] = None
    payload["poi_result"]["warnings"] = [
        {"code": "PARTIAL_POI_RESULTS", "message": "市场设施数据可能不完整。"}
    ]
    incomplete = BlindspotRequest.model_validate(payload)
    with pytest.raises(ValueError):
        validate_blindspot_exchange(incomplete, blind)


def test_exchange_enforces_route_budget_and_category_order():
    request_payload = load("blindspot-request.example.json")
    request_payload["max_route_pairs"] = 1
    request = BlindspotRequest.model_validate(request_payload)
    result = BlindspotResult.model_validate(load("blindspot-result.example.json"))
    with pytest.raises(ValueError):
        validate_blindspot_exchange(request, result)

    request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    payload = load("blindspot-result.example.json")
    payload["data"]["coverage"].reverse()
    with pytest.raises(ValueError):
        validate_blindspot_exchange(request, BlindspotResult.model_validate(payload))


def test_failed_isochrone_requires_matching_blindspot_failure():
    payload = load("blindspot-request.example.json")
    payload["isochrone_result"] = load("isochrone-failure.example.json")
    request = BlindspotRequest.model_validate(payload)
    failure = BlindspotResult.model_validate(load("blindspot-failure.example.json"))
    validate_blindspot_exchange(request, failure)
    with pytest.raises(ValueError):
        validate_blindspot_exchange(
            request, BlindspotResult.model_validate(load("blindspot-result.example.json"))
        )

    good_isochrone = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    with pytest.raises(ValueError):
        validate_blindspot_exchange(good_isochrone, failure)


def test_failed_isochrone_propagates_retryability_and_warnings():
    payload = load("blindspot-request.example.json")
    payload["isochrone_result"] = load("isochrone-failure.example.json")
    payload["isochrone_result"]["warnings"] = [
        {"code": "PARTIAL_ROUTING_RESULTS", "message": "采样路线部分不可用。"}
    ]
    request = BlindspotRequest.model_validate(payload)
    failure_payload = load("blindspot-failure.example.json")
    with pytest.raises(ValueError):
        validate_blindspot_exchange(request, BlindspotResult.model_validate(failure_payload))

    failure_payload["warnings"] = payload["isochrone_result"]["warnings"]
    validate_blindspot_exchange(request, BlindspotResult.model_validate(failure_payload))
    failure_payload["error"]["retryable"] = False
    with pytest.raises(ValueError):
        validate_blindspot_exchange(request, BlindspotResult.model_validate(failure_payload))


def test_isochrone_quality_caps_each_facility_confidence():
    payload = load("blindspot-request.example.json")
    payload["isochrone_result"]["data"]["quality"]["confidence"] = "medium"
    request = BlindspotRequest.model_validate(payload)
    result = BlindspotResult.model_validate(load("blindspot-result.example.json"))
    with pytest.raises(ValueError):
        validate_blindspot_exchange(request, result)
