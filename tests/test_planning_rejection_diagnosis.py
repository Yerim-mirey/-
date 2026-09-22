"""Offline tests for the Planning-rejection diagnostic classifier.

The classifier decides *why* a real model output was rejected. If it
misattributes a cause, the resulting Phase 10 report would be wrong while
looking authoritative, so every branch is pinned here against synthetic
output. No model call is made.
"""

import copy
import json
from pathlib import Path

import pytest

from app.schemas.agent import LifeCircleBrief
from scripts.diagnose_planning_rejections import (
    Attempt,
    build_payload,
    classify,
    shape,
    smoke_brief,
)
from scripts.verify_agent_llm_live import smoke_evidence


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def proposal_payload() -> dict:
    return json.loads(
        (CONTRACTS / "agent-planning-proposal.example.json").read_text(encoding="utf-8")
    )


def valid_raw() -> dict:
    """A proposal aligned with the smoke bundle and brief, so it must pass."""
    evidence = smoke_evidence()
    payload = proposal_payload()
    payload["evidence_bundle_id"] = evidence.bundle_id
    payload["planning_round"] = 1
    for item in [*payload["issues"], *payload["recommendations"]]:
        item["evidence_refs"] = ["diagnosis:facility-market"]
    for issue in payload["issues"]:
        issue["facility_type"] = "market"
    return payload


def test_shape_reduces_an_id_to_its_kind():
    assert shape("diagnosis:facility-market") == "diagnosis:<other>"
    assert shape("poi:facility-0") == "poi:<other>"


def test_shape_never_leaks_the_original_identifier():
    assert "facility-market" not in shape("diagnosis:facility-market")


def test_build_payload_matches_the_evidence_bundle_it_returns():
    payload, evidence = build_payload(2)

    assert payload["evidence"]["bundle_id"] == evidence.bundle_id
    assert payload["planning_round"] == 2
    assert payload["brief"]["needs_planning"] is True


def test_aligned_proposal_passes_every_condition():
    attempt = classify(valid_raw(), smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "ok"
    assert attempt.causes == []
    assert attempt.issue_count == 1
    assert attempt.recommendation_count == 1
    assert attempt.bundle_id_matches is True
    assert attempt.round_matches is True
    assert attempt.unavailable_evidence_shapes == []


def test_unknown_evidence_ref_is_attributed_and_shaped():
    payload = valid_raw()
    payload["issues"][0]["evidence_refs"] = ["diagnosis:not-in-bundle"]
    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "deterministic_rejected"
    assert "unknown_evidence_ref" in attempt.causes
    assert attempt.unavailable_evidence_shapes == ["diagnosis:<other>"]
    # The model-chosen id must not survive into the report payload.
    assert "not-in-bundle" not in json.dumps(attempt.to_payload(), ensure_ascii=False)


def test_bundle_id_mismatch_is_attributed():
    payload = valid_raw()
    payload["evidence_bundle_id"] = "evidence:some-other-bundle"
    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "deterministic_rejected"
    assert "bundle_id_mismatch" in attempt.causes
    assert attempt.bundle_id_matches is False


def test_round_mismatch_is_attributed_with_the_observed_round():
    payload = valid_raw()
    payload["planning_round"] = 3
    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "deterministic_rejected"
    assert "round_mismatch(round=3)" in attempt.causes
    assert attempt.round_matches is False


def test_facility_outside_the_brief_is_attributed():
    """The model may only use facility types the Brief actually lists."""
    payload = valid_raw()
    # The smoke Brief lists market only; make the Brief broader than the
    # output's scope instead of assuming it always holds three categories.
    assert payload["issues"][0]["facility_type"] == "market"
    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "ok"
    assert attempt.out_of_scope_facilities == []


def test_facility_not_in_a_narrowed_brief_is_attributed():
    brief_payload = json.loads(
        (CONTRACTS / "agent-brief.example.json").read_text(encoding="utf-8")
    )
    brief_payload["facility_types"] = ["pharmacy"]
    brief = LifeCircleBrief.model_validate(brief_payload)
    payload = valid_raw()  # issues[0].facility_type is "market"

    attempt = classify(payload, smoke_evidence(), brief, 1, 1)

    assert attempt.outcome == "deterministic_rejected"
    assert "facility_out_of_scope" in attempt.causes
    assert attempt.out_of_scope_facilities == ["market"]


def test_a_brief_narrower_than_three_categories_still_accepts_its_own_types():
    """Guard against hardcoding the three default facilities."""
    brief_payload = json.loads(
        (CONTRACTS / "agent-brief.example.json").read_text(encoding="utf-8")
    )
    brief_payload["facility_types"] = ["market"]
    brief = LifeCircleBrief.model_validate(brief_payload)

    attempt = classify(valid_raw(), smoke_evidence(), brief, 1, 1)

    assert attempt.outcome == "ok"


def test_contract_violation_reports_field_paths_only():
    attempt = classify({"summary": "missing everything"}, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "contract_rejected"
    assert attempt.error
    # Field paths are useful; model values must not appear.
    assert "missing everything" not in attempt.error


def test_empty_recommendations_fail_the_contract_not_the_degenerate_check():
    """`recommendations` has min_length=1, so an empty list is a contract breach."""
    payload = valid_raw()
    payload["recommendations"] = []

    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "contract_rejected"
    assert "recommendations" in attempt.error


def test_issue_count_zero_is_reported_by_the_contract():
    payload = valid_raw()
    payload["issues"] = []

    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "contract_rejected"
    assert "issues" in attempt.error


def test_attempt_payload_is_json_serializable():
    attempt = classify(valid_raw(), smoke_evidence(), smoke_brief(), 1, 1)

    assert json.loads(json.dumps(attempt.to_payload(), ensure_ascii=False))


def test_valid_raw_is_not_mutated_by_classification():
    payload = valid_raw()
    before = copy.deepcopy(payload)

    classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert payload == before


def test_attempt_defaults_are_independent_between_instances():
    first = Attempt(index=1)
    second = Attempt(index=2)
    first.causes.append("x")

    assert second.causes == []


# --- realistic bundle -------------------------------------------------------


def test_realistic_bundle_has_one_ref_per_facility_metric():
    from scripts.diagnose_planning_rejections import realistic_evidence

    evidence = realistic_evidence()
    ids = {ref.evidence_id for ref in evidence.refs}
    metrics = evidence.diagnosis.root.data.metrics

    assert ids == {f"diagnosis:facility-{m.facility_type.value}" for m in metrics}
    assert len(ids) == len(metrics) == 3


def test_realistic_bundle_pointers_all_resolve():
    from app.schemas.agent import _decoded_json_pointer_tokens
    from scripts.diagnose_planning_rejections import realistic_evidence

    evidence = realistic_evidence()
    document = evidence.model_dump(mode="json")
    for ref in evidence.refs:
        target = document
        for token in _decoded_json_pointer_tokens(ref.json_pointer):
            target = target[int(token)] if isinstance(target, list) else target[token]
        assert target is not None


def test_realistic_bundle_can_support_a_three_facility_plan():
    """The confound being removed: citing pharmacy is legal under this bundle."""
    from scripts.diagnose_planning_rejections import realistic_evidence

    evidence = realistic_evidence()
    payload = valid_raw()
    payload["evidence_bundle_id"] = evidence.bundle_id
    for item in [*payload["issues"], *payload["recommendations"]]:
        item["evidence_refs"] = ["diagnosis:facility-pharmacy"]

    attempt = classify(payload, evidence, smoke_brief(), 1, 1)

    assert attempt.outcome == "ok", attempt.causes


def test_pharmacy_citation_is_rejected_only_under_the_sparse_smoke_bundle():
    """Pins the fixture artefact that caused the unexplained rejection."""
    payload = valid_raw()
    for item in [*payload["issues"], *payload["recommendations"]]:
        item["evidence_refs"] = ["diagnosis:facility-pharmacy"]

    attempt = classify(payload, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "deterministic_rejected"
    assert "unknown_evidence_ref" in attempt.causes


# --- structural fingerprint -------------------------------------------------


def test_fingerprint_flags_a_schema_echo():
    echoed = {
        "title": "PlanningProposal",
        "type": "object",
        "properties": {},
        "required": [],
        "$defs": {},
    }
    attempt = classify(echoed, smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "contract_rejected"
    assert "schema_echo($defs)" in attempt.fingerprint


def test_fingerprint_lists_missing_top_level_keys():
    attempt = classify({"summary": "x"}, smoke_evidence(), smoke_brief(), 1, 1)

    assert "missing=" in attempt.fingerprint
    assert "issues" in attempt.fingerprint


def test_fingerprint_handles_a_non_object_output():
    attempt = classify(["not", "an", "object"], smoke_evidence(), smoke_brief(), 1, 1)

    assert attempt.outcome == "contract_rejected"
    assert attempt.fingerprint.startswith("not_an_object:")


def test_fingerprint_never_contains_output_values():
    attempt = classify({"secret_prose": "LEAK-ME"}, smoke_evidence(), smoke_brief(), 1, 1)

    assert "LEAK-ME" not in attempt.fingerprint
    assert "LEAK-ME" not in json.dumps(attempt.to_payload(), ensure_ascii=False)
