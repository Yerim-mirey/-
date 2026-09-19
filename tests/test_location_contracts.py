"""Contract-to-schema verification for Location v1."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.common import CenterPoint
from app.schemas.location import LocationRequest, LocationResult


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "v1"


def load_contract(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_center_point_example_matches_schema() -> None:
    center = CenterPoint.model_validate(load_contract("center-point.example.json"))
    assert center.crs == "BD09LL"


def test_location_request_example_matches_schema() -> None:
    request = LocationRequest.model_validate(
        load_contract("location-request.example.json")
    )
    assert request.input.type == "address"


def test_location_result_example_matches_schema() -> None:
    result = LocationResult.model_validate(
        load_contract("location-result.example.json")
    )
    assert result.root.ok is True
    assert result.root.data.center.crs == "BD09LL"


def test_location_failure_example_matches_schema() -> None:
    result = LocationResult.model_validate(
        load_contract("location-failure.example.json")
    )
    assert result.root.ok is False
    assert result.root.error.code == "LOCATION_NOT_FOUND"


def test_coordinate_request_is_supported() -> None:
    request = LocationRequest.model_validate(
        {
            "schema_version": "1.0",
            "input": {
                "type": "coordinate",
                "lng": 121.506123,
                "lat": 31.282456,
                "crs": "BD09LL",
            },
        }
    )
    assert request.input.type == "coordinate"


@pytest.mark.parametrize(
    ("lng", "lat"),
    [(181, 31), (-181, 31), (121, 91), (121, -91), (float("inf"), 31)],
)
def test_invalid_coordinates_are_rejected(lng: float, lat: float) -> None:
    with pytest.raises(ValidationError):
        CenterPoint.model_validate({"lng": lng, "lat": lat, "crs": "BD09LL"})


def test_unsupported_crs_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CenterPoint.model_validate({"lng": 121.5, "lat": 31.2, "crs": "WGS84"})


@pytest.mark.parametrize("lng", ["121.5", True])
def test_coordinate_type_coercion_is_rejected(lng: object) -> None:
    with pytest.raises(ValidationError):
        CenterPoint.model_validate({"lng": lng, "lat": 31.2, "crs": "BD09LL"})


@pytest.mark.parametrize(
    ("lng", "lat"),
    [(180, 90), (-180, -90)],
)
def test_coordinate_boundaries_are_accepted(lng: float, lat: float) -> None:
    request = LocationRequest.model_validate(
        {
            "schema_version": "1.0",
            "input": {
                "type": "coordinate",
                "lng": lng,
                "lat": lat,
                "crs": "BD09LL",
            },
        }
    )
    assert request.input.type == "coordinate"


@pytest.mark.parametrize(
    ("lng", "lat", "crs"),
    [
        (181, 31, "BD09LL"),
        (121, 91, "BD09LL"),
        (float("nan"), 31, "BD09LL"),
        (float("-inf"), 31, "BD09LL"),
        (121, 31, "WGS84"),
    ],
)
def test_invalid_coordinate_requests_are_rejected(
    lng: float, lat: float, crs: str
) -> None:
    with pytest.raises(ValidationError):
        LocationRequest.model_validate(
            {
                "schema_version": "1.0",
                "input": {
                    "type": "coordinate",
                    "lng": lng,
                    "lat": lat,
                    "crs": crs,
                },
            }
        )


def test_success_cannot_contain_error() -> None:
    payload = load_contract("location-result.example.json")
    payload["error"] = {
        "code": "PROVIDER_ERROR",
        "message": "不应同时出现。",
        "retryable": True,
    }
    with pytest.raises(ValidationError):
        LocationResult.model_validate(payload)


def test_failure_cannot_contain_data() -> None:
    payload = {
        "ok": False,
        "error": {
            "code": "LOCATION_NOT_FOUND",
            "message": "未找到匹配的位置。",
            "retryable": False,
        },
        "warnings": [],
        "meta": {"schema_version": "1.0", "provider": "baidu"},
        "data": {},
    }
    with pytest.raises(ValidationError):
        LocationResult.model_validate(payload)
