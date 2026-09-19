"""Tool 6 JSON examples and null/partial-result guardrails."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult, validate_diagnosis_exchange


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    ["diagnosis-result.example.json", "diagnosis-partial.example.json", "diagnosis-failure.example.json"],
)
def test_result_examples_validate(name):
    DiagnosisResult.model_validate(load(name))


def test_request_dedupes_facilities_in_stable_order():
    payload = load("diagnosis-request.example.json")
    payload["facility_types"] = ["pharmacy", "market", "pharmacy"]
    request = DiagnosisRequest.model_validate(payload)
    assert [item.value for item in request.facility_types] == ["pharmacy", "market"]


def test_full_and_partial_examples_match_their_request():
    request = DiagnosisRequest.model_validate(load("diagnosis-request.example.json"))
    validate_diagnosis_exchange(request, DiagnosisResult.model_validate(load("diagnosis-result.example.json")))
    validate_diagnosis_exchange(request, DiagnosisResult.model_validate(load("diagnosis-partial.example.json")))


def test_null_poi_count_and_unknown_ratio_survive_partial_result():
    data = DiagnosisResult.model_validate(load("diagnosis-partial.example.json")).root.data
    pharmacy = next(item for item in data.metrics if item.facility_type.value == "pharmacy")
    assert pharmacy.poi_count is None
    assert pharmacy.unknown_ratio == 1
    assert data.stages.poi.value == "partial"
    assert data.quality.confidence.value == "low"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.__setitem__("error", load("diagnosis-failure.example.json")["error"]),
        lambda p: p["data"]["metrics"][0].__setitem__("poi_count", 1),
        lambda p: p["data"]["metrics"][0].__setitem__("covered_ratio", None),
        lambda p: p["data"]["stages"].__setitem__("location", "unavailable"),
        lambda p: p["data"]["poi"].__setitem__("unexpected", True),
    ],
)
def test_invalid_result_is_rejected_by_schema_or_pair_check(mutate):
    payload = load("diagnosis-result.example.json")
    mutate(payload)
    request = DiagnosisRequest.model_validate(load("diagnosis-request.example.json"))
    with pytest.raises((ValidationError, ValueError)):
        validate_diagnosis_exchange(request, DiagnosisResult.model_validate(payload))


def test_short_poi_radius_is_rejected_by_pair_check():
    payload = load("diagnosis-result.example.json")
    payload["data"]["poi_search_radius_m"] = 1100
    request = DiagnosisRequest.model_validate(load("diagnosis-request.example.json"))
    with pytest.raises(ValueError):
        validate_diagnosis_exchange(request, DiagnosisResult.model_validate(payload))


def test_missing_or_wrong_threshold_is_rejected():
    payload = load("diagnosis-request.example.json")
    payload["service_distance_m"] = 1500
    with pytest.raises(ValidationError):
        DiagnosisRequest.model_validate(payload)
