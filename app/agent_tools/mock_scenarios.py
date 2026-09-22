"""Load existing Tool v1 examples as bundled Mock exchanges."""

import json
from pathlib import Path
from typing import Literal

from app.schemas.blindspot import BlindspotRequest, BlindspotResult
from app.schemas.diagnosis import DiagnosisRequest, DiagnosisResult
from app.schemas.isochrone import IsochroneRequest, IsochroneResult
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult

_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "v1"


def _load(name: str) -> dict:
    return json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))


def load_contract_exchanges(diagnosis_outcome: Literal["success", "partial", "failure"] = "success"):
    """Return six complete v1 exchanges; select the Diagnosis outcome."""
    if diagnosis_outcome not in {"success", "partial", "failure"}:
        raise ValueError(f"Unsupported diagnosis outcome: {diagnosis_outcome}")
    from app.agent_tools.mock_gateway import MockExchange

    examples = [
        ("resolve_location", "location", LocationRequest, LocationResult),
        ("search_pois", "poi-search", POISearchRequest, POISearchResult),
        ("calculate_walking_times", "routing", RoutingRequest, RoutingResult),
        ("generate_isochrone", "isochrone", IsochroneRequest, IsochroneResult),
        ("detect_blindspots", "blindspot", BlindspotRequest, BlindspotResult),
        ("diagnose_community", "diagnosis", DiagnosisRequest, DiagnosisResult),
    ]
    exchanges = []
    for method, prefix, request_type, result_type in examples:
        result_name = "result" if prefix != "diagnosis" or diagnosis_outcome == "success" else diagnosis_outcome
        exchanges.append(MockExchange(
            method,
            request_type.model_validate(_load(f"{prefix}-request.example.json")),
            result_type.model_validate(_load(f"{prefix}-{result_name}.example.json")),
        ))
    return exchanges
