"""Offline planning/reviewer tests for capability-gated V2 proposals."""

import pytest

from app.agents.planning_v2 import PlanningAgentV2, PlanningOutputErrorV2
from app.agents.reviewer_v2 import ReviewerAgentV2
from app.modules.poi.facility_catalog import load_facility_catalog
from app.schemas.agent_v2 import AgentBriefV2, AgentIntentV2
from app.schemas.planning_v2 import (
    PlanningEvidenceBundleV2,
    PlanningEvidenceItemV2,
    PlanningProposalV2,
)


class RecordingLLM:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate_object(self, *, system_prompt, user_message, response_schema):
        self.calls.append((system_prompt, user_message, response_schema))
        return self.output


def brief(*facility_ids: str) -> AgentBriefV2:
    return AgentBriefV2(
        schema_version="2.0",
        intent=AgentIntentV2.PLANNING_ANALYSIS,
        user_goal="根据空间可达性提出改进方向",
        location={"type": "address", "query": "武汉市武昌区中南路", "city": "武汉市"},
        facility_ids=list(facility_ids),
        exploratory_only=False,
        needs_diagnosis=True,
        needs_planning=True,
        needs_review=True,
    )


def evidence(*items: PlanningEvidenceItemV2) -> PlanningEvidenceBundleV2:
    return PlanningEvidenceBundleV2(evidence_bundle_id="evb_01", items=list(items))


def fact(evidence_id="fact_market_1", facility_id="market", metric="reachable_count", value=2):
    return PlanningEvidenceItemV2(
        facility_id=facility_id,
        facility_name=facility_id,
        poi_count=value if metric == "poi_count" else 3,
        poi_status="complete",
        reachable_count=value if metric == "reachable_count" else 2,
        covered_ratio=value if metric == "covered_ratio" else 0.75,
        metric_status="complete",
        source="V2 diagnosis result",
        evidence_id=evidence_id,
    )


def proposal_payload(**overrides):
    value = {
        "summary": "菜市场的步行可达性仍可改善。",
        "issues": [
            {
                "issue_id": "issue_01",
                "facility_id": "market",
                "title": "部分点位步行可达性不足",
                "description": "现有步行路线证据显示仍有改善空间。",
                "priority": "medium",
                "evidence_refs": ["fact_market_1"],
            }
        ],
        "recommendations": [
            {
                "recommendation_id": "rec_01",
                "facility_id": "market",
                "title": "核查服务盲区",
                "rationale": "先结合社区边界复核步行服务盲区。",
                "priority": "medium",
                "actions": ["复核现有市场点位与居民步行路径，再评估补充服务点的必要性。"],
                "evidence_refs": ["fact_market_1"],
            }
        ],
        "quantitative_claims": [
            {
                "facility_id": "market",
                "metric": "reachable_count",
                "value": 2,
                "unit": "处",
                "evidence_refs": ["fact_market_1"],
            }
        ],
    }
    value.update(overrides)
    return value


def test_planner_rejects_facility_without_planning_capability_before_model_call():
    llm = RecordingLLM(proposal_payload())
    planner = PlanningAgentV2(llm)

    with pytest.raises(ValueError, match="planning capability"):
        planner.create_proposal(brief("supermarket"), evidence(fact(facility_id="supermarket")))

    assert llm.calls == []


def test_planner_requires_evidence_for_every_selected_plannable_facility():
    planner = PlanningAgentV2(RecordingLLM(proposal_payload()))

    with pytest.raises(ValueError, match="evidence"):
        planner.create_proposal(brief("market", "pharmacy"), evidence(fact()))


def test_planner_links_claims_and_deterministically_lists_unsupported_dimensions():
    llm = RecordingLLM(proposal_payload())
    result = PlanningAgentV2(llm).create_proposal(brief("market"), evidence(fact()))

    assert isinstance(result, PlanningProposalV2)
    assert result.quantitative_claims[0].evidence_refs == ["fact_market_1"]
    dimensions = {dimension for item in result.unsupported_dimensions for dimension in item.unsupported_dimensions}
    assert {"population_capacity", "operating_status", "opening_hours", "service_quality", "spatial_coverage"} <= dimensions
    assert all(item.facility_id == "market" for item in result.unsupported_dimensions)
    assert result.catalog_version == load_facility_catalog().version


def test_planner_rejects_numeric_claim_not_supported_by_its_evidence():
    llm = RecordingLLM(proposal_payload(quantitative_claims=[{
        "facility_id": "market", "metric": "reachable_count", "value": 99,
        "unit": "处", "evidence_refs": ["fact_market_1"],
    }]))

    with pytest.raises(PlanningOutputErrorV2, match="unsupported claims"):
        PlanningAgentV2(llm).create_proposal(brief("market"), evidence(fact()))


def test_planner_rejects_unstructured_numbers_and_unsupported_operating_claims():
    for summary in ("附近有 99 处市场。", "这家菜市场目前正常营业。"):
        llm = RecordingLLM(proposal_payload(summary=summary))
        with pytest.raises(PlanningOutputErrorV2):
            PlanningAgentV2(llm).create_proposal(brief("market"), evidence(fact()))


def test_planner_allows_the_fixed_fifteen_minute_life_circle_term():
    llm = RecordingLLM(proposal_payload(summary="围绕15分钟生活圈完善设施信息核验。"))

    proposal = PlanningAgentV2(llm).create_proposal(brief("market"), evidence(fact()))

    assert "15分钟生活圈" in proposal.summary


def test_planner_allows_capacity_data_gaps_and_recommendations_to_verify_them():
    payload = proposal_payload(
        summary="当前证据仅能反映地图点位和步行可达情况，无法判断学位供需。",
        issues=[{
            "issue_id": "issue_01", "facility_id": "primary_school",
            "title": "补充教育服务证据",
            "description": "现有资料缺少适龄人口和学位供需信息，不能据此判断供需匹配。",
            "priority": "medium", "evidence_refs": ["fact_market_1"],
        }],
        recommendations=[{
            "recommendation_id": "rec_01", "facility_id": "primary_school",
            "title": "核查教育服务数据",
            "rationale": "学位供需尚无可靠证据，建议结合教育主管部门公开资料核验。",
            "priority": "medium",
            "actions": ["补充适龄人口与学位供需数据，并记录统计口径。"],
            "evidence_refs": ["fact_market_1"],
        }],
        quantitative_claims=[],
    )
    llm = RecordingLLM(payload)

    proposal = PlanningAgentV2(llm).create_proposal(
        brief("primary_school"), evidence(fact(facility_id="primary_school"))
    )

    assert "无法判断学位供需" in proposal.summary
    assert proposal.recommendations[0].actions == ["补充适龄人口与学位供需数据，并记录统计口径。"]


@pytest.mark.parametrize("summary", [
    "当前小学学位供给充足。",
    "周边小学存在明显学位缺口。",
    "学校容量不足以满足实际需求。",
])
def test_planner_still_rejects_asserted_capacity_or_supply_findings(summary):
    llm = RecordingLLM(proposal_payload(summary=summary))

    with pytest.raises(PlanningOutputErrorV2, match="unsupported claims"):
        PlanningAgentV2(llm).create_proposal(brief("market"), evidence(fact()))


def test_reviewer_rejects_unreferenced_numeric_claim_without_calling_model():
    planner = PlanningAgentV2(RecordingLLM(proposal_payload()))
    bundle = evidence(fact())
    plan = planner.create_proposal(brief("market"), bundle)
    # Mutate through validated reconstruction to simulate a compromised proposal.
    plan = plan.model_copy(update={"quantitative_claims": [
        plan.quantitative_claims[0].model_copy(update={"value": 900})
    ]})
    llm = RecordingLLM({})

    review = ReviewerAgentV2(llm).review_proposal(bundle, plan)

    assert review.status == "revision_required"
    assert any(issue.category == "unsupported_numeric" for issue in review.issues)
    assert llm.calls == []


@pytest.mark.parametrize("claim", [
    "菜市场当前营业且还有余量。",
    "附近养老机构可提供 20 张床位。",
    "超市全天开放。",
])
def test_reviewer_rejects_capacity_or_open_status_claims_deterministically(claim):
    bundle = evidence(fact())
    payload = proposal_payload(summary=claim)
    # Build a valid planner output first, then simulate imported/legacy model output.
    llm = RecordingLLM(payload)
    with pytest.raises(PlanningOutputErrorV2):
        PlanningAgentV2(llm).create_proposal(brief("market"), bundle)


def test_reviewer_accepts_safe_proposal_and_checks_model_references():
    bundle = evidence(fact())
    plan = PlanningAgentV2(RecordingLLM(proposal_payload())).create_proposal(brief("market"), bundle)
    review_output = {
        "schema_version": "2.0",
        "status": "approved",
        "reviewed_proposal_id": plan.proposal_id,
        "reviewed_planning_round": 1,
        "reviewed_evidence_bundle_id": "evb_01",
        "issues": [],
        "revision_instructions": [],
    }
    llm = RecordingLLM(review_output)

    result = ReviewerAgentV2(llm).review(brief("market"), bundle, plan)

    assert result.status == "approved"
    assert len(llm.calls) == 1


def test_reviewer_does_not_allow_model_to_approve_a_proposal_with_unlinked_references():
    bundle = evidence(fact())
    plan = PlanningAgentV2(RecordingLLM(proposal_payload())).create_proposal(brief("market"), bundle)
    plan = plan.model_copy(update={"quantitative_claims": [
        plan.quantitative_claims[0].model_copy(update={"evidence_refs": ["unknown_fact"]})
    ]})

    result = ReviewerAgentV2(RecordingLLM({})).review_proposal(bundle, plan)

    assert result.status == "revision_required"
    assert any(issue.category == "invalid_evidence_ref" for issue in result.issues)
