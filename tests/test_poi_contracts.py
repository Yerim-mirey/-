"""Contract-to-schema checks for POI v1, run after overlaying Location v1."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.common import CenterPoint
from app.schemas.location import LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "v1"


def load_contract(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "poi-search-result.example.json",
        "poi-search-empty.example.json",
        "poi-search-failure.example.json",
        "poi-search-partial.example.json",
    ],
)
def test_result_examples_match_schema(name: str) -> None:
    POISearchResult.model_validate(load_contract(name))


def test_request_example_matches_schema() -> None:
    request = POISearchRequest.model_validate(
        load_contract("poi-search-request.example.json")
    )
    assert request.center.crs == "BD09LL"
    assert [item.value for item in request.facility_types] == [
        "market",
        "pharmacy",
        "primary_school",
    ]


def test_location_center_is_directly_accepted_by_poi() -> None:
    location = LocationResult.model_validate(
        load_contract("location-result.example.json")
    )
    request = load_contract("poi-search-request.example.json")
    request["center"] = location.root.data.center.model_dump()

    poi_request = POISearchRequest.model_validate(request)
    assert isinstance(poi_request.center, CenterPoint)
    assert poi_request.center == location.root.data.center


def test_empty_result_is_not_a_provider_failure() -> None:
    empty = POISearchResult.model_validate(
        load_contract("poi-search-empty.example.json")
    )
    failed = POISearchResult.model_validate(
        load_contract("poi-search-failure.example.json")
    )
    assert empty.root.ok is True
    assert empty.root.data.pois == []
    assert empty.root.data.counts.primary_school == 0
    assert failed.root.ok is False
    assert failed.root.error.code == "PROVIDER_ERROR"


def test_partial_result_marks_failed_category_as_unknown() -> None:
    partial = POISearchResult.model_validate(
        load_contract("poi-search-partial.example.json")
    )
    assert partial.root.ok is True
    assert partial.root.data.counts.market == 0
    assert partial.root.data.counts.primary_school is None
    assert partial.root.warnings[0].code == "PARTIAL_POI_RESULTS"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0"),
        ("facility_types", []),
        ("facility_types", ["hospital"]),
        ("search_radius_m", 0),
        ("search_radius_m", "1500"),
        ("search_radius_m", True),
    ],
)
def test_invalid_request_fields_are_rejected(field: str, value: object) -> None:
    request = load_contract("poi-search-request.example.json")
    request[field] = value
    with pytest.raises(ValidationError):
        POISearchRequest.model_validate(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [("lng", "121.5"), ("lat", 91), ("crs", "WGS84")],
)
def test_invalid_shared_center_is_rejected(field: str, value: object) -> None:
    request = load_contract("poi-search-request.example.json")
    request["center"][field] = value
    with pytest.raises(ValidationError):
        POISearchRequest.model_validate(request)


def test_success_cannot_contain_error() -> None:
    result = load_contract("poi-search-result.example.json")
    result["error"] = load_contract("poi-search-failure.example.json")["error"]
    with pytest.raises(ValidationError):
        POISearchResult.model_validate(result)


def test_failure_cannot_contain_data() -> None:
    result = load_contract("poi-search-failure.example.json")
    result["data"] = load_contract("poi-search-result.example.json")["data"]
    with pytest.raises(ValidationError):
        POISearchResult.model_validate(result)


def test_warnings_and_meta_are_required() -> None:
    for field in ("warnings", "meta"):
        result = load_contract("poi-search-result.example.json")
        result.pop(field)
        with pytest.raises(ValidationError):
            POISearchResult.model_validate(result)


def test_raw_baidu_fields_cannot_leak_into_public_poi() -> None:
    result = load_contract("poi-search-result.example.json")
    result["data"]["pois"][0]["uid"] = "raw-baidu-uid"
    with pytest.raises(ValidationError):
        POISearchResult.model_validate(result)
