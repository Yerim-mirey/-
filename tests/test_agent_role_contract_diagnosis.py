"""Offline tests for the Agent role contract diagnostic harness.

The harness decides how the Phase 10 report describes real-model reliability.
If it misattributes a cause or miscounts an outcome, the report would be wrong
while looking authoritative, so every branch is covered here with a stub
StructuredLLM: no model call, no network.

The runners deliberately call the real Agent classes, so these tests also pin
that the harness measures production validation rather than a reimplementation.
"""

import json
from pathlib import Path

import pytest

from scripts.diagnose_agent_contracts import (
    BUNDLES,
    ORCHESTRATOR_MESSAGES,
    ROLES,
    Attempt,
    cause_of,
    proposal_for,
    realistic_evidence,
    run_orchestrator,
    run_planning,
    run_reviewer,
    shape,
    smoke_brief,
    sparse_evidence,
)
from scripts.verify_agent_llm_live import smoke_evidence


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def example(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


class StubLLM:
    """A StructuredLLM returning one prepared object (or raising)."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def generate_object(self, *, system_prompt, user_message, response_schema):
        self.calls += 1
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


# --- shape / cause helpers --------------------------------------------------


def test_shape_reduces_an_id_to_its_kind():
    assert shape("diagnosis:facility-market") == "diagnosis:<other>"
    assert shape("poi:facility-0") == "poi:<other>"


def test_shape_never_leaks_the_original_identifier():
    assert "facility-market" not in shape("diagnosis:facility-market")


def test_cause_of_uses_the_exception_type():
    assert cause_of(ValueError("secret prose")) == "ValueError"


# --- evidence bundles -------------------------------------------------------


def test_sparse_bundle_is_the_single_ref_smoke_fixture():
    assert [ref.evidence_id for ref in sparse_evidence().refs] == [
        "diagnosis:facility-market"
    ]


def test_realistic_bundle_has_one_ref_per_facility_metric():
    evidence = realistic_evidence()
    metrics = evidence.diagnosis.root.data.metrics

    assert {ref.evidence_id for ref in evidence.refs} == {
        f"diagnosis:facility-{metric.facility_type.value}" for metric in metrics
    }
    assert len(evidence.refs) == 3


def test_realistic_bundle_pointers_all_resolve():
    from app.schemas.agent import _decoded_json_pointer_tokens

    evidence = realistic_evidence()
    document = evidence.model_dump(mode="json")
    for ref in evidence.refs:
        target = document
        for token in _decoded_json_pointer_tokens(ref.json_pointer):
            target = target[int(token)] if isinstance(target, list) else target[token]
        assert target is not None


def test_both_bundles_are_registered():
    assert set(BUNDLES) == {"smoke", "realistic"}


# --- proposal fixture -------------------------------------------------------


def test_proposal_for_cites_only_available_evidence():
    evidence = realistic_evidence()
    proposal = proposal_for(evidence)
    available = {ref.evidence_id for ref in evidence.refs}
    cited = {ref for item in (*proposal.issues, *proposal.recommendations)
             for ref in item.evidence_refs}

    assert cited <= available
    assert proposal.evidence_bundle_id == evidence.bundle_id
    assert proposal.planning_round == 1


def test_grounded_proposal_cites_matching_evidence_for_each_issue():
    """Regression: an earlier fixture mis-cited the recommendation, which made
    every Reviewer verdict `revision_required` and measured nothing."""
    evidence = realistic_evidence()
    proposal = proposal_for(evidence)

    for issue in proposal.issues:
        if issue.facility_type is None:
            continue
        assert issue.evidence_refs == [f"diagnosis:facility-{issue.facility_type.value}"]
    for recommendation in proposal.recommendations:
        assert recommendation.evidence_refs == ["diagnosis:facility-market"]


def test_mismatch_variant_deliberately_mis_cites_one_recommendation():
    proposal = proposal_for(realistic_evidence(), mismatch=True)

    assert proposal.recommendations[0].evidence_refs == ["diagnosis:facility-pharmacy"]
    # The issue stays grounded, so only the recommendation is defective.
    assert proposal.issues[0].evidence_refs == ["diagnosis:facility-market"]


def test_grounded_and_mismatch_variants_differ():
    evidence = realistic_evidence()

    grounded = proposal_for(evidence)
    mismatched = proposal_for(evidence, mismatch=True)

    assert grounded.recommendations[0].evidence_refs != mismatched.recommendations[0].evidence_refs


def test_proposal_for_stays_valid_under_the_sparse_bundle():
    proposal = proposal_for(sparse_evidence())

    assert proposal.evidence_bundle_id == sparse_evidence().bundle_id


def test_proposal_for_uses_only_in_scope_facility_types():
    brief_facilities = {item.value for item in smoke_brief().facility_types}
    proposal = proposal_for(realistic_evidence())

    assert all(
        issue.facility_type is None or issue.facility_type.value in brief_facilities
        for issue in proposal.issues
    )


# --- orchestrator runner ----------------------------------------------------


def test_orchestrator_records_intent_and_missing_fields():
    """The Orchestrator owns only the extraction; Python derives the Brief."""
    payload = {
        "intent": "facility_query",
        "user_goal": "查看附近设施",
        "location": None,
        "facility_types": ["market"],
        "use_standard_facilities": False,
    }
    stub = StubLLM(payload)

    attempt = run_orchestrator(stub, 1, "帮我看看附近的菜市场")

    assert attempt.outcome == "ok"
    assert attempt.role == "orchestrator"
    assert attempt.intent == "facility_query"
    assert attempt.missing_fields == ["location"]
    assert attempt.facility_count == 1
    assert stub.calls == 1


def test_orchestrator_applies_the_standard_three_facilities_in_python():
    payload = {
        "intent": "community_diagnosis",
        "user_goal": "体检这个社区",
        "location": {
            "type": "address",
            "query": "上海市虹口区四川北路街道",
            "city": "上海市",
        },
        "facility_types": [],
        "use_standard_facilities": True,
    }

    attempt = run_orchestrator(StubLLM(payload), 1, "做一次完整体检")

    assert attempt.outcome == "ok"
    assert attempt.facility_count == 3
    assert attempt.missing_fields == []


def test_orchestrator_reports_a_contract_rejection():
    attempt = run_orchestrator(StubLLM({"intent": "not_an_intent"}), 1, "hi")

    assert attempt.outcome == "contract_rejected"
    assert attempt.error == "OrchestratorOutputError"


def test_orchestrator_reports_a_provider_error_by_type():
    from app.providers.deepseek import LLMTransportError

    attempt = run_orchestrator(StubLLM(LLMTransportError("boom")), 1, "hi")

    assert attempt.outcome == "provider_error"
    assert attempt.error == "LLMTransportError"


def test_orchestrator_messages_cover_planning_and_missing_location():
    joined = " ".join(ORCHESTRATOR_MESSAGES)

    assert "规划建议" in joined, "需要一条应触发 planning 的消息"
    assert "附近" in joined, "需要一条缺地点的消息"


# --- planning runner --------------------------------------------------------


def planning_payload(**overrides) -> dict:
    evidence = realistic_evidence()
    payload = proposal_for(evidence).model_dump(mode="json")
    payload.update(overrides)
    return payload


def test_planning_records_counts_and_binding():
    stub = StubLLM(planning_payload())

    attempt = run_planning(stub, 1, realistic_evidence(), smoke_brief())

    assert attempt.outcome == "ok"
    assert attempt.role == "planning"
    assert attempt.issue_count == 1
    assert attempt.recommendation_count == 1
    assert attempt.bundle_id_matches is True
    assert attempt.round_matches is True
    assert attempt.unavailable_evidence_shapes == []


def test_planning_reports_a_stale_bundle_as_deterministic_rejection():
    """The real PlanningAgent rejects a proposal bound to another bundle."""
    payload = planning_payload(evidence_bundle_id="evidence:some-other-bundle")
    attempt = run_planning(StubLLM(payload), 1, realistic_evidence(), smoke_brief())

    assert attempt.outcome == "deterministic_rejected"
    assert attempt.causes == ["PlanningOutputError"]


def test_planning_reports_an_unknown_evidence_citation():
    payload = planning_payload()
    payload["issues"][0]["evidence_refs"] = ["diagnosis:not-in-bundle"]
    attempt = run_planning(StubLLM(payload), 1, realistic_evidence(), smoke_brief())

    assert attempt.outcome == "deterministic_rejected"


def test_pharmacy_citation_is_accepted_only_under_the_realistic_bundle():
    """Pins the fixture artefact behind the previously unexplained rejection."""
    payload = planning_payload()
    for item in (*payload["issues"], *payload["recommendations"]):
        item["evidence_refs"] = ["diagnosis:facility-pharmacy"]

    ok_attempt = run_planning(
        StubLLM(payload), 1, realistic_evidence(), smoke_brief()
    )
    sparse_attempt = run_planning(
        StubLLM(payload), 1, sparse_evidence(), smoke_brief()
    )

    assert ok_attempt.outcome == "ok"
    assert sparse_attempt.outcome == "deterministic_rejected"


def test_planning_reports_a_provider_error_by_type():
    from app.providers.deepseek import LLMTransportError

    attempt = run_planning(
        StubLLM(LLMTransportError("boom")), 1, realistic_evidence(), smoke_brief()
    )

    assert attempt.outcome == "provider_error"
    assert attempt.error == "LLMTransportError"


def test_planning_output_error_is_not_confused_with_a_contract_breach():
    """A schema breach raises ValidationError; a semantic conflict does not."""
    attempt = run_planning(StubLLM({"summary": "nothing"}), 1, realistic_evidence(), smoke_brief())

    assert attempt.outcome == "deterministic_rejected"
    assert attempt.causes == ["PlanningOutputError"]


# --- reviewer runner --------------------------------------------------------


def test_reviewer_records_status_and_issue_counts():
    evidence = realistic_evidence()
    payload = example("agent-review-result.example.json")
    payload["reviewed_proposal_id"] = proposal_for(evidence).proposal_id
    payload["reviewed_planning_round"] = 1
    payload["reviewed_evidence_bundle_id"] = evidence.bundle_id
    stub = StubLLM(payload)

    attempt = run_reviewer(
        stub, 1, evidence, proposal_for(evidence), expect_revision=False
    )

    assert attempt.outcome == "ok"
    assert attempt.role == "reviewer"
    assert attempt.review_status == "approved"
    assert attempt.review_issue_count == 0
    assert attempt.missing_evidence_count == 0
    assert attempt.proposal_is_grounded is True
    assert attempt.discriminates is True


def test_reviewer_revision_required_is_recorded_as_a_status_not_a_failure():
    """`revision_required` is a legitimate Reviewer verdict, not an error."""
    evidence = realistic_evidence()
    payload = example("agent-review-result.example.json")
    payload["status"] = "revision_required"
    payload["reviewed_proposal_id"] = proposal_for(evidence).proposal_id
    payload["reviewed_planning_round"] = 1
    payload["reviewed_evidence_bundle_id"] = evidence.bundle_id
    payload["revision_instructions"] = ["收窄建议范围"]
    attempt = run_reviewer(
        StubLLM(payload), 1, evidence, proposal_for(evidence, mismatch=True),
        expect_revision=True,
    )

    assert attempt.outcome == "ok"
    assert attempt.review_status == "revision_required"
    assert attempt.proposal_is_grounded is False
    assert attempt.discriminates is True


def test_reviewer_mismatched_identity_is_a_contract_rejection():
    evidence = realistic_evidence()
    payload = example("agent-review-result.example.json")
    payload["reviewed_evidence_bundle_id"] = "evidence:some-other-bundle"
    attempt = run_reviewer(
        StubLLM(payload), 1, evidence, proposal_for(evidence), expect_revision=False
    )

    assert attempt.outcome == "contract_rejected"


# --- attempt payload --------------------------------------------------------


def test_attempt_payload_is_json_serializable():
    attempt = run_planning(StubLLM(planning_payload()), 1, realistic_evidence(), smoke_brief())

    assert json.loads(json.dumps(attempt.to_payload(), ensure_ascii=False))


def test_attempt_payload_never_contains_model_prose():
    from app.providers.deepseek import LLMTransportError

    attempt = run_planning(
        StubLLM(LLMTransportError("LEAK-ME")), 1, realistic_evidence(), smoke_brief()
    )

    assert "LEAK-ME" not in json.dumps(attempt.to_payload(), ensure_ascii=False)


def test_attempt_defaults_are_independent_between_instances():
    first = Attempt(index=1)
    second = Attempt(index=2)
    first.causes.append("x")

    assert second.causes == []


def test_roles_tuple_is_the_three_agents():
    assert ROLES == ("orchestrator", "planning", "reviewer")
