import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.agent import EvidenceBundle, LifeCircleBrief


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_brief_example_validates():
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    assert brief.intent.value == "planning_analysis"
    assert (brief.needs_full_diagnosis, brief.needs_planning, brief.needs_review) == (True, True, True)


def test_evidence_example_validates_without_root_in_public_pointer():
    evidence = EvidenceBundle.model_validate(load("agent-evidence.example.json"))
    assert evidence.refs[0].json_pointer == "/location/data/center"
    assert "/root/" not in evidence.refs[0].json_pointer
    assert evidence.location.root.ok is True


@pytest.mark.parametrize("field", ["needs_full_diagnosis", "needs_planning", "needs_review"])
def test_planning_intent_requires_all_planning_flags(field):
    payload = load("agent-brief.example.json")
    payload[field] = False
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(payload)


def test_review_cannot_run_without_planning():
    payload = load("agent-brief.example.json")
    payload.update({"intent": "facility_query", "needs_full_diagnosis": False, "needs_planning": False})
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(payload)


def test_missing_location_is_structured():
    payload = load("agent-brief.example.json")
    payload["location"] = None
    payload["missing_information"] = [{"field": "location", "message": "需要社区地址或中心点"}]
    LifeCircleBrief.model_validate(payload)


def test_location_can_coexist_with_other_missing_information():
    payload = load("agent-brief.example.json")
    payload["facility_types"] = []
    payload["missing_information"] = [{"field": "facility_types", "message": "需要设施类别"}]
    LifeCircleBrief.model_validate(payload)


def test_duplicate_facility_types_are_rejected():
    payload = load("agent-brief.example.json")
    payload["facility_types"] = ["market", "market"]
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(payload)


def test_evidence_id_kind_and_pointer_root_must_agree():
    payload = load("agent-evidence.example.json")
    payload["refs"][0]["kind"] = "diagnosis"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_evidence_pointer_requires_present_tool_result():
    payload = load("agent-evidence.example.json")
    payload["location"] = None
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_diagnosis_cannot_be_mixed_with_lower_level_results():
    payload = load("agent-evidence.example.json")
    payload["diagnosis"] = load("diagnosis-result.example.json")
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_unknown_fields_and_scalar_coercion_are_rejected():
    brief = load("agent-brief.example.json")
    brief["unexpected"] = True
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(brief)
    evidence = load("agent-evidence.example.json")
    evidence["refs"][0]["summary"] = 123
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(evidence)


def test_assignment_validation_is_inherited():
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    with pytest.raises(ValidationError):
        brief.user_goal = ""
