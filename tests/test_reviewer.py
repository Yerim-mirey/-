"""Reviewer Agent behavior with a controlled structured model."""

import json
from pathlib import Path

import pytest

from app.agents.reviewer import ReviewerAgent, ReviewerOutputError
from app.schemas.agent import EvidenceBundle, PlanningProposal, ReviewResult


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


def test_approved_review_uses_supplied_evidence_and_proposal():
    evidence = diagnosis_evidence()
    proposal = PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
    llm = RecordingLLM(load("agent-review-result.example.json"))

    review = ReviewerAgent(llm).review_proposal(evidence, proposal)

    assert isinstance(review, ReviewResult)
    assert review.status.value == "approved"
    assert review.reviewed_proposal_id == "proposal:market-gap-r1"
    assert review.reviewed_evidence_bundle_id == "evidence:diagnosis-example"
    system_prompt, message, schema = llm.calls[0]
    assert system_prompt
    assert json.loads(message)["evidence"]["bundle_id"] == "evidence:diagnosis-example"
    assert json.loads(message)["proposal"]["proposal_id"] == "proposal:market-gap-r1"
    assert schema == ReviewResult.model_json_schema()


@pytest.mark.parametrize("status", ["revision_required", "insufficient_evidence"])
def test_review_returns_structured_nonapproved_status(status):
    output = load("agent-review-result.example.json")
    output["status"] = status
    if status == "revision_required":
        output["issues"] = [{
            "issue_id": "review:unsupported-claim",
            "category": "unsupported_fact",
            "message": "建议的建设条件缺少依据",
            "recommendation_ids": ["recommendation:market-study"],
            "evidence_refs": ["diagnosis:market-coverage"],
        }]
        output["revision_instructions"] = ["将建设结论改为待核查方向"]
    else:
        output["missing_evidence"] = [{
            "request_id": "request:diagnosis-refresh",
            "kind": "diagnosis",
            "reason": "当前证据不足以判断设施覆盖",
            "required_json_pointers": ["/diagnosis/data/metrics"],
            "facility_types": ["market"],
        }]

    review = ReviewerAgent(RecordingLLM(output)).review_proposal(
        diagnosis_evidence(), PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
    )

    assert review.status.value == status
    assert bool(review.issues) is (status == "revision_required")
    assert bool(review.missing_evidence) is (status == "insufficient_evidence")


@pytest.mark.parametrize("change", [
    {"evidence_bundle_id": "evidence:other"},
    {"issues": [{**load("agent-planning-proposal.example.json")["issues"][0],
                 "evidence_refs": ["diagnosis:unknown"]}]},
    {"recommendations": [{**load("agent-planning-proposal.example.json")["recommendations"][0],
                          "evidence_refs": ["diagnosis:unknown"]}]},
])
def test_unrelated_proposal_is_rejected_before_model_call(change):
    payload = load("agent-planning-proposal.example.json")
    payload.update(change)
    llm = RecordingLLM(load("agent-review-result.example.json"))
    with pytest.raises(ValueError):
        ReviewerAgent(llm).review_proposal(diagnosis_evidence(), PlanningProposal.model_validate(payload))
    assert llm.calls == []


@pytest.mark.parametrize("field,value", [
    ("reviewed_proposal_id", "proposal:other"),
    ("reviewed_planning_round", 2),
    ("reviewed_evidence_bundle_id", "evidence:other"),
])
def test_model_cannot_change_review_identity(field, value):
    output = load("agent-review-result.example.json")
    output[field] = value
    with pytest.raises(ReviewerOutputError):
        ReviewerAgent(RecordingLLM(output)).review_proposal(
            diagnosis_evidence(), PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
        )


@pytest.mark.parametrize("field,value", [
    ("recommendation_ids", ["recommendation:unknown"]),
    ("evidence_refs", ["diagnosis:unknown"]),
])
def test_review_issue_must_cite_this_proposal_and_evidence(field, value):
    output = load("agent-review-result.example.json")
    output["status"] = "revision_required"
    output["issues"] = [{
        "issue_id": "review:unsupported-claim",
        "category": "unsupported_fact",
        "message": "需要修改建议",
        "recommendation_ids": ["recommendation:market-study"],
        "evidence_refs": ["diagnosis:market-coverage"],
    }]
    output["issues"][0][field] = value
    with pytest.raises(ReviewerOutputError):
        ReviewerAgent(RecordingLLM(output)).review_proposal(
            diagnosis_evidence(), PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
        )


def test_invalid_model_output_does_not_echo_private_content():
    with pytest.raises(ReviewerOutputError) as caught:
        ReviewerAgent(RecordingLLM({"message": "私人地址123"})).review_proposal(
            diagnosis_evidence(), PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
        )
    assert "私人地址123" not in str(caught.value)


def test_provider_error_is_preserved_for_runtime():
    error = TimeoutError("model timeout")
    with pytest.raises(TimeoutError) as caught:
        ReviewerAgent(RecordingLLM(error=error)).review_proposal(
            diagnosis_evidence(), PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
        )
    assert caught.value is error
