"""Offline checks for the Phase 10 acceptance script's verifiers.

The live script itself is gated and never runs here. What is tested is the
logic that decides whether a real run passed: JSON Pointer resolution,
evidence-reference validity, proposal/review binding, and the terminal-state
expectations. A silent bug in these would invalidate an entire Phase 10
report, so they are covered with synthetic but schema-valid state.
"""

import json
from pathlib import Path

import pytest

from app.schemas.agent import (
    AgentRunState,
    EvidenceBundle,
    PlanningProposal,
    ReviewResult,
    RunStatus,
)
from scripts.verify_agent_live_e2e import (
    CASES,
    Case,
    Recorder,
    check_evidence,
    check_proposal_and_review,
    check_semantics,
    observe,
    resolve_pointer,
    sanitize,
)


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def example(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


# --- JSON Pointer -----------------------------------------------------------


def test_pointer_resolves_nested_object():
    ok, value = resolve_pointer({"a": {"b": [1, 2, {"c": "found"}]}}, "/a/b/2/c")

    assert ok is True
    assert value == "found"


def test_empty_pointer_returns_the_document():
    document = {"a": 1}

    assert resolve_pointer(document, "") == (True, document)


@pytest.mark.parametrize(
    "pointer",
    ["a/b", "/a/missing", "/a/9", "/a/-", "/a/b/c"],
)
def test_bad_pointers_are_rejected(pointer):
    ok, _ = resolve_pointer({"a": [1, 2]}, pointer)

    assert ok is False


def test_pointer_unescapes_the_rfc6901_tokens():
    ok, value = resolve_pointer({"a/b": {"m~n": 7}}, "/a~1b/m~0n")

    assert ok is True
    assert value == 7


# --- evidence references ----------------------------------------------------


def test_contract_evidence_pointer_resolves_against_its_own_bundle():
    bundle = EvidenceBundle.model_validate(example("agent-evidence.example.json"))
    recorder = Recorder("case", "message")

    check_evidence(_state(evidence=bundle), recorder)

    assert recorder.passed, recorder.problems
    assert recorder.evidence_ref_count == 1


def test_dangling_evidence_pointer_is_reported():
    payload = example("agent-evidence.example.json")
    payload["refs"][0]["json_pointer"] = "/location/data/not_a_field"
    bundle = EvidenceBundle.model_validate(payload)
    recorder = Recorder("case", "message")

    check_evidence(_state(evidence=bundle), recorder)

    assert not recorder.passed
    assert any("evidence-pointer" in problem for problem in recorder.problems)


# --- proposal and review binding -------------------------------------------


def _query_brief() -> dict:
    """A facility-query brief: no planning, so `completed` needs only Evidence."""
    brief = example("agent-brief.example.json")
    brief["intent"] = "facility_query"
    brief["needs_full_diagnosis"] = False
    brief["needs_planning"] = False
    brief["needs_review"] = False
    return brief


def _location_input() -> dict:
    return {"type": "address", "query": "上海市虹口区四川北路街道", "city": "上海市"}


def _state(*, evidence=None, proposal=None, review=None, brief=None) -> AgentRunState:
    """A schema-valid Run State; `completed` requires both Brief and Evidence."""
    return AgentRunState.model_validate(
        {
            "schema_version": "1.0",
            "run_id": "run:offline-check",
            "status": "completed",
            "completion_mode": "normal",
            "user_message": "offline check",
            "brief": brief or _query_brief(),
            "evidence": evidence.model_dump(mode="json") if evidence else None,
            "planning_proposal": proposal.model_dump(mode="json") if proposal else None,
            "review_result": review.model_dump(mode="json") if review else None,
            "planning_round": 1 if proposal else 0,
            "tool_retry_count": 0,
            "warnings": [],
            "errors": [],
        }
    )


def _waiting_state(message: str) -> AgentRunState:
    """A Run that stopped for a missing location, with no evidence fetched."""
    brief = example("agent-brief.example.json")
    brief["intent"] = "facility_query"
    brief["location"] = None
    brief["facility_types"] = []
    brief["needs_full_diagnosis"] = False
    brief["needs_planning"] = False
    brief["needs_review"] = False
    brief["missing_information"] = [
        {"field": "location", "message": "需要地点或坐标"},
        {"field": "facility_types", "message": "需要至少一类设施"},
    ]
    return AgentRunState.model_validate(
        {
            "schema_version": "1.0",
            "run_id": "run:waiting",
            "status": "waiting_for_input",
            "completion_mode": None,
            "user_message": message,
            "brief": brief,
            "evidence": None,
            "planning_proposal": None,
            "review_result": None,
            "planning_round": 0,
            "tool_retry_count": 0,
            "warnings": [],
            "errors": [],
        }
    )


def _planning_brief() -> dict:
    """A planning brief; keeps the state schema's planning requirements visible."""
    return example("agent-brief.example.json")


def _aligned_proposal(bundle_id: str, **overrides) -> PlanningProposal:
    """A proposal bound to `bundle_id`, citing the bundle's own evidence id."""
    payload = example("agent-planning-proposal.example.json")
    payload["evidence_bundle_id"] = bundle_id
    payload["issues"][0]["evidence_refs"] = ["location:center"]
    payload["recommendations"][0]["evidence_refs"] = ["location:center"]
    payload.update(overrides)
    return PlanningProposal.model_validate(payload)


def _aligned_review(proposal: PlanningProposal, bundle_id: str) -> ReviewResult:
    payload = example("agent-review-result.example.json")
    payload["reviewed_proposal_id"] = proposal.proposal_id
    payload["reviewed_planning_round"] = proposal.planning_round
    payload["reviewed_evidence_bundle_id"] = bundle_id
    return ReviewResult.model_validate(payload)


def _planning_state() -> AgentRunState:
    """A schema-valid completed planning Run, ready to be mutated by a test."""
    evidence = EvidenceBundle.model_validate(example("agent-evidence.example.json"))
    proposal = _aligned_proposal(evidence.bundle_id)
    review = _aligned_review(proposal, evidence.bundle_id)
    return _state(
        evidence=evidence, proposal=proposal, review=review, brief=_planning_brief()
    )


def test_matching_proposal_and_review_pass():
    state = _planning_state()
    recorder = Recorder("case", "message")

    check_proposal_and_review(state, recorder)

    assert recorder.passed, recorder.problems


def test_proposal_citing_unknown_evidence_is_reported():
    state = _planning_state()
    state.planning_proposal.issues[0].evidence_refs = ["diagnosis:does-not-exist"]
    recorder = Recorder("case", "message")

    check_proposal_and_review(state, recorder)

    assert not recorder.passed
    assert any("proposal-evidence-refs" in problem for problem in recorder.problems)


def test_proposal_bound_to_a_stale_bundle_is_reported():
    state = _planning_state()
    state.planning_proposal.evidence_bundle_id = "evidence:some-older-bundle"
    recorder = Recorder("case", "message")

    check_proposal_and_review(state, recorder)

    assert not recorder.passed
    assert any("proposal-bundle-binding" in problem for problem in recorder.problems)


def test_review_of_a_different_round_is_reported():
    state = _planning_state()
    state.review_result.reviewed_planning_round = 2
    recorder = Recorder("case", "message")

    check_proposal_and_review(state, recorder)

    assert not recorder.passed
    assert any("review-identity" in problem for problem in recorder.problems)


def test_review_bound_to_a_stale_bundle_is_reported():
    state = _planning_state()
    state.review_result.reviewed_evidence_bundle_id = "evidence:some-older-bundle"
    recorder = Recorder("case", "message")

    check_proposal_and_review(state, recorder)

    assert not recorder.passed
    assert any("review-bundle-binding" in problem for problem in recorder.problems)


# --- terminal-state expectations -------------------------------------------


def test_missing_location_case_requires_missing_fields_and_no_evidence():
    case = next(item for item in CASES if item.case_id == "missing-location")
    recorder = Recorder(case.case_id, case.message)

    check_semantics(case, _waiting_state(case.message), recorder)

    assert recorder.passed, recorder.problems


def test_waiting_without_missing_information_is_reported():
    """The schema forbids a bare waiting_for_input, so build the brief loosely."""
    recorder = Recorder("x", "m")
    state = _waiting_state("m")
    # `missing_information` is re-emptied after validation to model a future
    # regression where the Runtime stops for no stated reason.
    object.__setattr__(state.brief, "missing_information", [])

    check_semantics(CASES[-1], state, recorder)

    assert not recorder.passed
    assert any("waiting-has-missing-fields" in problem for problem in recorder.problems)


def test_completed_run_missing_evidence_is_reported_by_the_checker():
    """Guard the checker itself: the schema may tighten, the check must hold."""
    case = Case("x", "m", RunStatus.COMPLETED, False, False)
    recorder = Recorder("x", "m")
    state = _waiting_state("m")
    object.__setattr__(state, "status", RunStatus.COMPLETED)
    object.__setattr__(state, "evidence", None)

    check_semantics(case, state, recorder)

    assert not recorder.passed
    assert any("completed-has-evidence" in problem for problem in recorder.problems)


def test_observe_records_public_fields_only():
    evidence = EvidenceBundle.model_validate(example("agent-evidence.example.json"))
    state = _state(evidence=evidence)
    recorder = Recorder("x", "m")

    observe(state, recorder)

    assert recorder.status == "completed"
    assert recorder.completion_mode == "normal"
    assert recorder.error_codes == []


# --- secret scrubbing -------------------------------------------------------


def test_sanitize_removes_both_keys_from_report_text():
    text = '{"ak": "baidu-secret", "llm": "sk-secret-value"}'

    cleaned = sanitize(text, "baidu-secret", "sk-secret-value")

    assert "baidu-secret" not in cleaned
    assert "sk-secret-value" not in cleaned
    assert cleaned.count("<redacted>") == 2


def test_sanitize_tolerates_a_missing_model_key():
    cleaned = sanitize("ak=abc", "abc", None)

    assert "abc" not in cleaned
