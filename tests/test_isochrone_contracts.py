import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.isochrone import IsochroneRequest, IsochroneResult


CONTRACTS = Path(__file__).parents[1] / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_request_example_matches_schema() -> None:
    IsochroneRequest.model_validate(load("isochrone-request.example.json"))


@pytest.mark.parametrize(
    "name",
    [
        "isochrone-result.example.json",
        "isochrone-partial.example.json",
        "isochrone-failure.example.json",
    ],
)
def test_result_examples_match_schema(name: str) -> None:
    IsochroneResult.model_validate(load(name))


def test_polygon_must_close() -> None:
    payload = load("isochrone-result.example.json")
    payload["data"]["geometry"]["coordinates"][0][-1] = [0.0, 0.0]
    with pytest.raises(ValidationError):
        IsochroneResult.model_validate(payload)


def test_polygon_must_match_boundary_points() -> None:
    payload = load("isochrone-result.example.json")
    payload["data"]["geometry"]["coordinates"][0][1][0] += 0.001
    with pytest.raises(ValidationError):
        IsochroneResult.model_validate(payload)


def test_quality_must_match_boundary_and_samples() -> None:
    payload = load("isochrone-result.example.json")
    payload["data"]["quality"]["valid_boundary_directions"] = 3
    with pytest.raises(ValidationError):
        IsochroneResult.model_validate(payload)


def test_time_limit_must_be_positive() -> None:
    payload = load("isochrone-request.example.json")
    payload["time_limit_s"] = 0
    with pytest.raises(ValidationError):
        IsochroneRequest.model_validate(payload)
