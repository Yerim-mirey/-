"""Lightweight contract validation that does not require pytest."""

import json
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.schemas.common import CenterPoint
from app.schemas.blindspot import (
    BlindspotRequest,
    BlindspotResult,
    validate_blindspot_exchange,
)
from app.schemas.agent import (
    AgentRunState,
    EvidenceBundle,
    LifeCircleBrief,
    PlanningProposal,
    ReviewResult,
)
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult, validate_diagnosis_exchange
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult


CONTRACTS = ROOT / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def must_reject(model: type, payload: dict) -> None:
    try:
        model.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError(f"{model.__name__} unexpectedly accepted invalid data")


def main() -> None:
    CenterPoint.model_validate(load("center-point.example.json"))
    LocationRequest.model_validate(load("location-request.example.json"))
    LocationResult.model_validate(load("location-result.example.json"))
    LocationResult.model_validate(load("location-failure.example.json"))
    POISearchRequest.model_validate(load("poi-search-request.example.json"))
    POISearchResult.model_validate(load("poi-search-result.example.json"))
    POISearchResult.model_validate(load("poi-search-empty.example.json"))
    POISearchResult.model_validate(load("poi-search-partial.example.json"))
    POISearchResult.model_validate(load("poi-search-failure.example.json"))
    RoutingRequest.model_validate(load("routing-request.example.json"))
    RoutingResult.model_validate(load("routing-result.example.json"))
    RoutingResult.model_validate(load("routing-partial.example.json"))
    RoutingResult.model_validate(load("routing-failure.example.json"))
    IsochroneRequest.model_validate(load("isochrone-request.example.json"))
    IsochroneResult.model_validate(load("isochrone-result.example.json"))
    IsochroneResult.model_validate(load("isochrone-partial.example.json"))
    IsochroneResult.model_validate(load("isochrone-failure.example.json"))
    blindspot_request = BlindspotRequest.model_validate(load("blindspot-request.example.json"))
    blindspot_partial_request = BlindspotRequest.model_validate(
        load("blindspot-partial-request.example.json")
    )
    blindspot_result = BlindspotResult.model_validate(load("blindspot-result.example.json"))
    BlindspotResult.model_validate(load("blindspot-empty.example.json"))
    blindspot_partial = BlindspotResult.model_validate(load("blindspot-partial.example.json"))
    BlindspotResult.model_validate(load("blindspot-failure.example.json"))
    validate_blindspot_exchange(blindspot_request, blindspot_result)
    validate_blindspot_exchange(blindspot_partial_request, blindspot_partial)
    diagnosis_request = DiagnosisRequest.model_validate(load("diagnosis-request.example.json"))
    diagnosis_result = DiagnosisResult.model_validate(load("diagnosis-result.example.json"))
    diagnosis_partial = DiagnosisResult.model_validate(load("diagnosis-partial.example.json"))
    DiagnosisResult.model_validate(load("diagnosis-failure.example.json"))
    validate_diagnosis_exchange(diagnosis_request, diagnosis_result)
    validate_diagnosis_exchange(diagnosis_request, diagnosis_partial)
    LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    EvidenceBundle.model_validate(load("agent-evidence.example.json"))
    PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
    ReviewResult.model_validate(load("agent-review-result.example.json"))
    AgentRunState.model_validate(load("agent-run-state.example.json"))

    must_reject(
        CenterPoint,
        {"lng": 181, "lat": 31.2, "crs": "BD09LL"},
    )
    must_reject(
        CenterPoint,
        {"lng": 121.5, "lat": 31.2, "crs": "WGS84"},
    )
    must_reject(
        CenterPoint,
        {"lng": "121.5", "lat": 31.2, "crs": "BD09LL"},
    )
    must_reject(
        CenterPoint,
        {"lng": True, "lat": 31.2, "crs": "BD09LL"},
    )

    mixed_result = load("location-result.example.json")
    mixed_result["error"] = {
        "code": "PROVIDER_ERROR",
        "message": "成功结果不应包含 error。",
        "retryable": True,
    }
    must_reject(LocationResult, mixed_result)

    missing_warnings = load("location-result.example.json")
    missing_warnings.pop("warnings")
    must_reject(LocationResult, missing_warnings)

    mixed_blindspot = load("blindspot-result.example.json")
    mixed_blindspot["error"] = load("blindspot-failure.example.json")["error"]
    must_reject(BlindspotResult, mixed_blindspot)

    unclosed_blindspot = load("blindspot-result.example.json")
    unclosed_blindspot["data"]["blind_spots"]["features"][0]["geometry"]["coordinates"][0].pop()
    must_reject(BlindspotResult, unclosed_blindspot)

    mixed_diagnosis = load("diagnosis-result.example.json")
    mixed_diagnosis["error"] = load("diagnosis-failure.example.json")["error"]
    must_reject(DiagnosisResult, mixed_diagnosis)

    print("Core MVP Tool 1-6 and Agent v1 contracts validated successfully.")


if __name__ == "__main__":
    main()
