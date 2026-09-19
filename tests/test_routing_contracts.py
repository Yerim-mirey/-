import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.poi import POISearchResult
from app.schemas.routing import RouteTarget, RoutingRequest, RoutingResult


CONTRACTS = Path(__file__).parents[1] / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "routing-result.example.json",
        "routing-partial.example.json",
        "routing-failure.example.json",
    ],
)
def test_result_examples_match_schema(name: str) -> None:
    RoutingResult.model_validate(load(name))


def test_request_example_matches_schema() -> None:
    RoutingRequest.model_validate(load("routing-request.example.json"))


def test_duplicate_target_ids_are_rejected() -> None:
    payload = load("routing-request.example.json")
    payload["targets"][1]["target_id"] = payload["targets"][0]["target_id"]
    with pytest.raises(ValidationError):
        RoutingRequest.model_validate(payload)


def test_route_values_must_match_status() -> None:
    payload = load("routing-result.example.json")
    payload["data"]["routes"][0]["status"] = "no_route"
    with pytest.raises(ValidationError):
        RoutingResult.model_validate(payload)


def test_summary_must_match_routes() -> None:
    payload = load("routing-result.example.json")
    payload["data"]["summary"]["success"] = 1
    payload["data"]["summary"]["no_route"] = 1
    with pytest.raises(ValidationError):
        RoutingResult.model_validate(payload)


def test_extra_fields_are_rejected() -> None:
    payload = load("routing-request.example.json")
    payload["targets"][0]["poi_name"] = "不应进入 Routing"
    with pytest.raises(ValidationError):
        RoutingRequest.model_validate(payload)


def test_poi_maps_directly_to_route_target() -> None:
    poi_result = POISearchResult.model_validate(load("poi-search-result.example.json"))
    poi = poi_result.root.data.pois[0]
    target = RouteTarget(target_id=poi.poi_id, location=poi.location)
    assert target.target_id == poi.poi_id
    assert target.location == poi.location
