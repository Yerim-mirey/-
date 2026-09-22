"""Deterministic routing decisions for the future Agent graph."""

import json
from pathlib import Path

import pytest

from app.agent_runtime.conditions import after_brief, after_evidence, after_review, can_retry_tool
from app.agent_runtime.state import new_run, update_run
from app.schemas.agent import AgentRunState, EvidenceBundle, LifeCircleBrief


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
            "summary": "菜市场覆盖比例",
        }],
        "diagnosis": load("diagnosis-result.example.json"),
    })


def reviewed_state(review_status="approved", planning_round=1):
    proposal = load("agent-planning-proposal.example.json")
    proposal["planning_round"] = planning_round
    review = load("agent-review-result.example.json")
    review["status"] = review_status
    review["reviewed_planning_round"] = planning_round
    if review_status == "revision_required":
        review["revision_instructions"] = ["收窄建议范围"]
    if review_status == "insufficient_evidence":
        review["missing_evidence"] = [{
            "request_id": "request:diagnosis-refresh",
            "kind": "diagnosis",
            "reason": "需要更新诊断指标",
            "required_json_pointers": ["/diagnosis/data/metrics"],
            "facility_types": ["market"],
        }]
    payload = load("agent-run-state.example.json")
    payload.update({
        "status": "running",
        "brief": load("agent-brief.example.json"),
        "evidence": diagnosis_evidence().model_dump(mode="json"),
        "planning_proposal": proposal,
        "review_result": review,
        "planning_round": planning_round,
    })
    return AgentRunState.model_validate(payload)


def test_after_brief_waits_only_when_information_is_missing():
    pending = new_run("run:conditions", "体检社区")
    complete = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    running = update_run(pending, status="running", brief=complete)
    missing_payload = load("agent-brief.example.json")
    missing_payload["location"] = None
    missing_payload["missing_information"] = [{"field": "location", "message": "缺少地点"}]
    waiting = update_run(pending, status="waiting_for_input", brief=missing_payload)

    assert after_brief(running) == "fetch_evidence"
    assert after_brief(waiting) == "wait_for_input"
    with pytest.raises(ValueError):
        after_brief(pending)


def test_after_evidence_runs_planning_only_when_requested():
    pending = new_run("run:conditions", "体检社区")
    evidence = diagnosis_evidence()
    planning = update_run(pending, status="running", brief=load("agent-brief.example.json"), evidence=evidence)
    diagnosis_only = load("agent-brief.example.json")
    diagnosis_only.update({"intent": "community_diagnosis", "needs_planning": False, "needs_review": False})
    nonplanning = update_run(pending, status="running", brief=diagnosis_only, evidence=evidence)

    assert after_evidence(planning) == "plan"
    assert after_evidence(nonplanning) == "finalize"
    with pytest.raises(ValueError):
        after_evidence(update_run(pending, status="running", brief=load("agent-brief.example.json")))


@pytest.mark.parametrize(("review_status", "round_value", "expected"), [
    ("approved", 1, "finalize"),
    ("revision_required", 1, "plan"),
    ("insufficient_evidence", 1, "fetch_evidence"),
    ("revision_required", 3, "finalize_with_limitations"),
    ("insufficient_evidence", 3, "finalize_with_limitations"),
    ("approved", 3, "finalize"),
])
def test_after_review_uses_status_and_planning_round_limit(review_status, round_value, expected):
    assert after_review(reviewed_state(review_status, round_value)) == expected


def test_after_review_requires_a_review():
    state = update_run(reviewed_state(), review_result=None)
    with pytest.raises(ValueError):
        after_review(state)


@pytest.mark.parametrize(("count", "expected"), [(0, True), (1, True), (2, False)])
def test_tool_retry_count_is_bounded(count, expected):
    state = update_run(new_run("run:conditions", "体检社区"), status="running", tool_retry_count=count)
    assert can_retry_tool(state) is expected
