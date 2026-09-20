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


@pytest.mark.parametrize("json_pointer", ["/location/root/data/center", "/location/root"])
def test_evidence_pointer_rejects_root_model_token(json_pointer):
    payload = load("agent-evidence.example.json")
    payload["refs"][0]["json_pointer"] = json_pointer
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


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


def diagnosis_evidence_payload() -> dict:
    return {
        "schema_version": "1.0",
        "bundle_id": "evidence:diagnosis-example",
        "refs": [{
            "evidence_id": "diagnosis:market-coverage",
            "kind": "diagnosis",
            "json_pointer": "/diagnosis/data/metrics/0/blind_ratio",
            "summary": "菜市场覆盖比例来自 Diagnosis 指标"
        }],
        "location": None,
        "poi": None,
        "routing": None,
        "isochrone": None,
        "blindspot": None,
        "diagnosis": load("diagnosis-result.example.json"),
        "warnings": []
    }


def test_planning_and_evidence_examples_form_a_closed_fixture():
    from app.schemas.agent import PlanningProposal

    evidence = EvidenceBundle.model_validate(diagnosis_evidence_payload())
    proposal = PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
    assert proposal.evidence_bundle_id == evidence.bundle_id
    refs = {ref for item in proposal.issues + proposal.recommendations for ref in item.evidence_refs}
    assert refs <= {ref.evidence_id for ref in evidence.refs}


def test_issue_and_recommendation_require_unique_evidence_refs():
    from app.schemas.agent import PlanningProposal

    payload = load("agent-planning-proposal.example.json")
    payload["issues"][0]["evidence_refs"] = []
    with pytest.raises(ValidationError):
        PlanningProposal.model_validate(payload)
    payload = load("agent-planning-proposal.example.json")
    payload["recommendations"][0]["evidence_refs"] *= 2
    with pytest.raises(ValidationError):
        PlanningProposal.model_validate(payload)


def test_planning_ids_are_unique():
    from app.schemas.agent import PlanningProposal

    payload = load("agent-planning-proposal.example.json")
    payload["recommendations"].append(payload["recommendations"][0])
    with pytest.raises(ValidationError):
        PlanningProposal.model_validate(payload)


def test_approved_review_example_validates():
    from app.schemas.agent import ReviewResult

    review = ReviewResult.model_validate(load("agent-review-result.example.json"))
    assert review.status.value == "approved"


def test_revision_requires_issue_or_instruction_and_no_evidence_request():
    from app.schemas.agent import ReviewResult

    payload = load("agent-review-result.example.json")
    payload["status"] = "revision_required"
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
    payload["revision_instructions"] = ["收窄建议范围"]
    payload["missing_evidence"] = [{
        "request_id": "request:diagnosis-refresh",
        "kind": "diagnosis",
        "reason": "需要新的诊断结果",
        "required_json_pointers": ["/diagnosis/data/metrics"],
        "facility_types": ["market"]
    }]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)


def test_insufficient_evidence_requires_structured_request():
    from app.schemas.agent import ReviewResult

    payload = load("agent-review-result.example.json")
    payload["status"] = "insufficient_evidence"
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
    payload["missing_evidence"] = [{
        "request_id": "request:routing-refresh",
        "kind": "routing",
        "reason": "需要新的路径结果",
        "required_json_pointers": ["/routing/data/routes"],
        "facility_types": ["market"]
    }]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
    payload["missing_evidence"] = [{
        "request_id": "request:diagnosis-refresh",
        "kind": "diagnosis",
        "reason": "需要新的诊断结果",
        "required_json_pointers": ["/location/data/center"],
        "facility_types": ["market"]
    }]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
