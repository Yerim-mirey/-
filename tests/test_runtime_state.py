"""Runtime state updates are atomic and respect AgentRunState."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_runtime.state import new_run, update_run
from app.schemas.agent import AgentRunState, EvidenceBundle, LifeCircleBrief, PlanningProposal


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


def reviewed_state():
    payload = load("agent-run-state.example.json")
    payload.update({
        "status": "running",
        "brief": load("agent-brief.example.json"),
        "evidence": diagnosis_evidence().model_dump(mode="json"),
        "planning_proposal": load("agent-planning-proposal.example.json"),
        "review_result": load("agent-review-result.example.json"),
        "planning_round": 1,
    })
    return AgentRunState.model_validate(payload)


def test_new_run_is_pending_and_atomic_brief_update_keeps_original():
    pending = new_run("run:phase6", "体检社区并提出建议")
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))

    running = update_run(pending, status="running", brief=brief)

    assert pending.status.value == "pending"
    assert pending.brief is None
    assert running.status.value == "running"
    assert running.brief == brief
    assert running.run_id == "run:phase6"


def test_invalid_update_does_not_mutate_original():
    pending = new_run("run:phase6", "体检社区")
    with pytest.raises(ValidationError):
        update_run(pending, status="completed")
    assert pending.status.value == "pending"
    assert pending.completion_mode is None


@pytest.mark.parametrize("field", ["run_id", "user_message", "schema_version"])
def test_run_identity_cannot_be_rewritten(field):
    pending = new_run("run:phase6", "体检社区")
    with pytest.raises(ValueError):
        update_run(pending, **{field: "changed"})


def test_new_evidence_requires_clearing_old_proposal_and_review_atomically():
    state = reviewed_state()
    replacement = diagnosis_evidence().model_dump(mode="json")
    replacement["bundle_id"] = "evidence:replacement"
    evidence = EvidenceBundle.model_validate(replacement)

    with pytest.raises(ValidationError):
        update_run(state, evidence=evidence)
    updated = update_run(state, evidence=evidence, planning_proposal=None, review_result=None)

    assert state.evidence.bundle_id == "evidence:diagnosis-example"
    assert state.review_result is not None
    assert updated.evidence.bundle_id == "evidence:replacement"
    assert updated.planning_proposal is None
    assert updated.review_result is None
    assert updated.planning_round == 1


def test_changed_evidence_with_same_id_requires_clearing_downstream_results():
    state = reviewed_state()
    replacement = state.evidence.model_dump(mode="json")
    replacement["warnings"] = [{"code": "LOW_CONFIDENCE", "message": "新增数据质量警告"}]
    evidence = EvidenceBundle.model_validate(replacement)

    with pytest.raises(ValueError):
        update_run(state, evidence=evidence)
    updated = update_run(state, evidence=evidence, planning_proposal=None, review_result=None)

    assert updated.evidence.warnings[0].code == "LOW_CONFIDENCE"
    assert updated.planning_proposal is None
    assert updated.review_result is None


def test_changed_proposal_with_same_id_requires_clearing_old_review():
    state = reviewed_state()
    replacement = state.planning_proposal.model_dump(mode="json")
    replacement["summary"] = "已修改的规划结论"
    proposal = PlanningProposal.model_validate(replacement)

    with pytest.raises(ValueError):
        update_run(state, planning_proposal=proposal)
    updated = update_run(state, planning_proposal=proposal, review_result=None)

    assert updated.planning_proposal.summary == "已修改的规划结论"
    assert updated.review_result is None


def test_changed_brief_requires_clearing_evidence_and_resetting_round():
    state = reviewed_state()
    replacement = state.brief.model_dump(mode="json")
    replacement["user_goal"] = "改为分析新社区"
    brief = LifeCircleBrief.model_validate(replacement)

    with pytest.raises(ValueError):
        update_run(state, brief=brief)
    updated = update_run(
        state, brief=brief, evidence=None, planning_proposal=None, review_result=None, planning_round=0
    )

    assert updated.brief.user_goal == "改为分析新社区"
    assert updated.evidence is None
    assert updated.planning_round == 0
