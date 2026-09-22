"""Declarative graph edges between Agent nodes and Runtime conditions."""

import json
from pathlib import Path

import pytest

from app.agent_runtime import graph
from app.agent_runtime.state import new_run, update_run
from app.schemas.agent import AgentRunState, EvidenceBundle


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


def running_state(**changes):
    state = new_run("run:graph", "请体检该社区并提出规划建议")
    return update_run(state, status="running", **changes)


def reviewed_state(review_status, planning_round=1):
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
    return running_state(
        brief=load("agent-brief.example.json"),
        evidence=diagnosis_evidence(),
        planning_proposal=proposal,
        review_result=review,
        planning_round=planning_round,
    )


def test_graph_is_connected_and_declares_only_known_nodes():
    assert set(graph.NODE_TRANSITIONS) == {
        graph.ORCHESTRATE,
        graph.FETCH_EVIDENCE,
        graph.PLAN,
        graph.REVIEW,
    }
    for targets in graph.NODE_TRANSITIONS.values():
        assert set(targets.values()) <= {
            graph.FETCH_EVIDENCE,
            graph.PLAN,
            graph.REVIEW,
            graph.FINALIZE,
            graph.REQ_WAIT_FOR_INPUT,
        }


def test_brief_routes_to_evidence_or_waiting_for_input():
    complete = running_state(brief=load("agent-brief.example.json"))
    incomplete = update_run(
        running_state(),
        status="waiting_for_input",
        brief={
            **load("agent-brief.example.json"),
            "location": None,
            "missing_information": [
                {"field": "location", "message": "请提供社区地址、街道或明确的 BD09LL 中心点。"}
            ],
        },
    )
    assert graph.terminal_requirement(complete, graph.ORCHESTRATE) == graph.REQ_FETCH_EVIDENCE
    assert graph.next_node(graph.ORCHESTRATE, graph.REQ_FETCH_EVIDENCE) == graph.FETCH_EVIDENCE
    assert graph.terminal_requirement(incomplete, graph.ORCHESTRATE) == graph.REQ_WAIT_FOR_INPUT


def test_evidence_routes_to_plan_or_finalize():
    planning = running_state(
        brief=load("agent-brief.example.json"),
        evidence=diagnosis_evidence(),
        planning_round=1,
    )
    plain_brief = {**load("agent-brief.example.json")}
    plain_brief.update({
        "intent": "facility_query",
        "needs_full_diagnosis": False,
        "needs_planning": False,
        "needs_review": False,
    })
    plain = running_state(
        brief=plain_brief,
        evidence=EvidenceBundle.model_validate(load("agent-evidence.example.json")),
    )
    assert graph.terminal_requirement(planning, graph.FETCH_EVIDENCE) == graph.REQ_PLAN
    assert graph.next_node(graph.FETCH_EVIDENCE, graph.REQ_PLAN) == graph.PLAN
    assert graph.terminal_requirement(plain, graph.FETCH_EVIDENCE) == graph.REQ_FINALIZE


@pytest.mark.parametrize(
    ("review_status", "planning_round", "expected"),
    [
        ("approved", 1, graph.REQ_FINALIZE),
        ("revision_required", 1, graph.REQ_PLAN),
        ("insufficient_evidence", 1, graph.REQ_FETCH_EVIDENCE),
        ("revision_required", 3, graph.REQ_FINALIZE_WITH_LIMITATIONS),
        ("insufficient_evidence", 3, graph.REQ_FINALIZE_WITH_LIMITATIONS),
    ],
)
def test_review_follows_conditions_module(review_status, planning_round, expected):
    state = reviewed_state(review_status, planning_round)
    assert graph.terminal_requirement(state, graph.REVIEW) == expected


def test_every_review_requirement_has_an_edge():
    for requirement in (
        graph.REQ_FINALIZE,
        graph.REQ_PLAN,
        graph.REQ_FETCH_EVIDENCE,
        graph.REQ_FINALIZE_WITH_LIMITATIONS,
    ):
        assert graph.next_node(graph.REVIEW, requirement) in {
            graph.FINALIZE,
            graph.PLAN,
            graph.FETCH_EVIDENCE,
        }


def test_plan_has_one_unconditional_edge_to_review():
    state = running_state(
        brief=load("agent-brief.example.json"),
        evidence=diagnosis_evidence(),
        planning_round=1,
    )
    requirement = graph.terminal_requirement(state, graph.PLAN)
    assert requirement == "review"
    assert graph.next_node(graph.PLAN, requirement) == graph.REVIEW


def test_unknown_node_or_requirement_is_rejected():
    state = reviewed_state("approved")
    with pytest.raises(ValueError):
        graph.terminal_requirement(state, "unknown_node")
    with pytest.raises(ValueError):
        graph.next_node(graph.REVIEW, "unknown")
    with pytest.raises(ValueError):
        graph.next_node(graph.START, graph.REQ_FETCH_EVIDENCE)
