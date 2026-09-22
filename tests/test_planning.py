"""Planning Agent behavior with a controlled structured model."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.planning import PlanningAgent, PlanningOutputError
from app.schemas.agent import EvidenceBundle, LifeCircleBrief, PlanningProposal


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def diagnosis_evidence():
    return EvidenceBundle.model_validate({
        "schema_version": "1.0",
        "bundle_id": "evidence:diagnosis-example",
        "refs": [{
            "evidence_id": "diagnosis:market-coverage",
            "kind": "diagnosis",
            "json_pointer": "/diagnosis/data/metrics/0/blind_ratio",
            "summary": "菜市场覆盖比例来自 Diagnosis 指标",
        }],
        "diagnosis": load("diagnosis-result.example.json"),
    })


class RecordingLLM:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def generate_object(self, *, system_prompt, user_message, response_schema):
        self.calls.append((system_prompt, user_message, response_schema))
        if self.error is not None:
            raise self.error
        return self.output


@pytest.mark.parametrize("round_value", [1, 2])
def test_planning_returns_public_proposal_from_supplied_evidence(round_value):
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    evidence = diagnosis_evidence()
    model_output = load("agent-planning-proposal.example.json")
    model_output["planning_round"] = round_value
    llm = RecordingLLM(model_output)

    proposal = PlanningAgent(llm).create_proposal(brief, evidence, round_value)

    assert isinstance(proposal, PlanningProposal)
    assert proposal.evidence_bundle_id == "evidence:diagnosis-example"
    assert proposal.planning_round == round_value
    assert proposal.issues[0].evidence_refs == ["diagnosis:market-coverage"]
    system_prompt, message, schema = llm.calls[0]
    assert system_prompt
    assert json.loads(message)["evidence"]["bundle_id"] == "evidence:diagnosis-example"
    assert json.loads(message)["planning_round"] == round_value
    assert schema == PlanningProposal.model_json_schema()


@pytest.mark.parametrize("change", [
    {"intent": "community_diagnosis", "needs_planning": False, "needs_review": False},
    {"location": None, "missing_information": [{"field": "location", "message": "缺少地点"}]},
])
def test_ineligible_brief_is_rejected_before_model_call(change):
    payload = load("agent-brief.example.json")
    payload.update(change)
    llm = RecordingLLM(load("agent-planning-proposal.example.json"))
    with pytest.raises(ValueError):
        PlanningAgent(llm).create_proposal(LifeCircleBrief.model_validate(payload), diagnosis_evidence())
    assert llm.calls == []


@pytest.mark.parametrize("round_value", [0, -1, True, 1.5])
def test_invalid_planning_round_is_rejected_before_model_call(round_value):
    llm = RecordingLLM(load("agent-planning-proposal.example.json"))
    with pytest.raises(ValueError):
        PlanningAgent(llm).create_proposal(
            LifeCircleBrief.model_validate(load("agent-brief.example.json")), diagnosis_evidence(), round_value
        )
    assert llm.calls == []


def test_missing_or_failed_diagnosis_is_rejected_before_model_call():
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    llm = RecordingLLM(load("agent-planning-proposal.example.json"))
    location_only = EvidenceBundle.model_validate(load("agent-evidence.example.json"))
    with pytest.raises(ValueError):
        PlanningAgent(llm).create_proposal(brief, location_only)
    failed_payload = diagnosis_evidence().model_dump(mode="json")
    failed_payload["diagnosis"] = load("diagnosis-failure.example.json")
    with pytest.raises(ValueError):
        PlanningAgent(llm).create_proposal(brief, EvidenceBundle.model_validate(failed_payload))
    assert llm.calls == []


def test_diagnosis_must_cover_requested_facility_types():
    payload = diagnosis_evidence().model_dump(mode="json")
    payload["diagnosis"]["data"]["metrics"] = payload["diagnosis"]["data"]["metrics"][:1]
    llm = RecordingLLM(load("agent-planning-proposal.example.json"))
    with pytest.raises(ValueError):
        PlanningAgent(llm).create_proposal(
            LifeCircleBrief.model_validate(load("agent-brief.example.json")), EvidenceBundle.model_validate(payload)
        )
    assert llm.calls == []


@pytest.mark.parametrize("change", [
    {"evidence_bundle_id": "evidence:other"},
    {"planning_round": 2},
    {"issues": [{**load("agent-planning-proposal.example.json")["issues"][0], "facility_type": "pharmacy"}]},
])
def test_model_cannot_change_request_identity_or_issue_scope(change):
    proposal = load("agent-planning-proposal.example.json")
    proposal.update(change)
    brief_payload = load("agent-brief.example.json")
    if "issues" in change:
        brief_payload["facility_types"] = ["market"]
    with pytest.raises(PlanningOutputError):
        PlanningAgent(RecordingLLM(proposal)).create_proposal(
            LifeCircleBrief.model_validate(brief_payload), diagnosis_evidence()
        )


@pytest.mark.parametrize("section", ["issues", "recommendations"])
def test_model_cannot_cite_evidence_outside_bundle(section):
    proposal = deepcopy(load("agent-planning-proposal.example.json"))
    proposal[section][0]["evidence_refs"] = ["diagnosis:unknown"]
    with pytest.raises(PlanningOutputError):
        PlanningAgent(RecordingLLM(proposal)).create_proposal(
            LifeCircleBrief.model_validate(load("agent-brief.example.json")), diagnosis_evidence()
        )


def test_bad_model_output_does_not_echo_private_content():
    with pytest.raises(PlanningOutputError) as caught:
        PlanningAgent(RecordingLLM({"summary": "私人地址123"})).create_proposal(
            LifeCircleBrief.model_validate(load("agent-brief.example.json")), diagnosis_evidence()
        )
    assert "私人地址123" not in str(caught.value)


def test_provider_error_is_preserved_for_runtime():
    error = TimeoutError("model timeout")
    with pytest.raises(TimeoutError) as caught:
        PlanningAgent(RecordingLLM(error=error)).create_proposal(
            LifeCircleBrief.model_validate(load("agent-brief.example.json")), diagnosis_evidence()
        )
    assert caught.value is error
