"""V2 planning proposal generation with catalog and evidence guards."""

import json
import re
from uuid import uuid4

from pydantic import Field, ValidationError

from app.modules.poi.facility_catalog import FacilityCatalog, FacilityDefinition, load_facility_catalog
from app.providers.llm import StructuredLLM
from app.schemas.agent_v2 import AgentBriefV2, AgentIntentV2
from app.schemas.common import ContractModel
from app.schemas.planning_v2 import (
    FacilityPlanningLimitV2,
    PlanningDimensionV2,
    PlanningEvidenceBundleV2,
    PlanningEvidenceItemV2,
    PlanningIssueV2,
    PlanningProposalV2,
    PlanningQuantitativeClaimV2,
    PlanningRecommendationV2,
)


PLANNING_SYSTEM_PROMPT_V2 = """你是 15 分钟生活圈 V2 的规划分析角色。输入包含已由服务端目录校验的规划设施、结构化证据和严格输出 Schema。
只能为 planning_facilities 中明确列出的设施提出建议；不得为 catalog_limits 中规划能力未授权的设施生成问题或建议。
每一条问题、建议和量化主张都必须引用与该设施一致的证据 ID。量化事实只能放入 quantitative_claims，数值、指标、单位必须原样来自对应的 complete 证据；summary、issue 和 recommendation 的文字中不得写无证据数字。“15分钟生活圈”是固定项目术语，可以原样使用，不表示本次测得的路线时长。
不得声称或暗示 POI 数量代表人口供需、容量、学位/床位/名额、经营状态、营业时间、价格、服务质量、服务资格或无障碍条件。可以说明缺少相关数据，并建议后续核查或补充调查；不得据此判断供给充足/不足、存在缺口或供需匹配。不得把“未检索到”写成“不存在”。
只输出 Schema 对象，不输出 Markdown、额外字段、工具调用或流程决定。"""


_UNSUPPORTED_DIMENSIONS: tuple[PlanningDimensionV2, ...] = (
    "population_capacity", "operating_status", "opening_hours", "price_affordability",
    "service_quality", "service_eligibility", "accessibility_barriers", "demand_supply_balance",
)
_DIMENSION_EXPLANATIONS = {
    "population_capacity": "当前证据不包含服务人口、学位、床位、名额或设施容量。",
    "operating_status": "地图点位不能证明设施当前营业、开放或正常运行。",
    "opening_hours": "当前没有经过核实的营业或开放时段数据。",
    "price_affordability": "当前没有价格、收费或可负担性数据。",
    "service_quality": "当前没有服务质量或用户评价的可靠证据。",
    "service_eligibility": "当前没有招生、服务对象或使用资格规则。",
    "accessibility_barriers": "坐标和普通步行路线不能证明无障碍通行条件。",
    "demand_supply_balance": "当前没有人口需求和服务容量数据，不能判断供需平衡。",
    "planning_recommendation": "该类别未在目录中获得规划建议能力授权。",
    "spatial_coverage": "当前没有 V2 等时圈/人口覆盖证据，不能提供人口空间覆盖率。",
}
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:[,.]\d+)*(?:%|％)?")
_CAPACITY_CLAIM = re.compile(
    r"(?:床位|学位|名额|容量|供给)[^，。；;\n]{0,8}"
    r"(?:充足|不足|缺口|短缺|紧张|富余|过剩|饱和|有余量|已满|不够|供需匹配|满足需求|无法满足|不能满足)"
    r"|(?:充足|不足|缺口|短缺|紧张|富余|过剩|饱和|有余量|已满|不够|供需匹配|满足需求|无法满足|不能满足)"
    r"[^，。；;\n]{0,8}(?:床位|学位|名额|容量|供给)"
)
_OPERATING_CLAIM = re.compile(
    r"正常营业|正在营业|目前营业|当前营业|营业中|已开门|正在开放|目前开放|当前开放|全天开放|24\s*小时开放|已经关门|已停业"
)


class PlanningDraftV2(ContractModel):
    summary: str = Field(min_length=1)
    issues: list[PlanningIssueV2] = Field(min_length=1)
    recommendations: list[PlanningRecommendationV2] = Field(min_length=1)
    quantitative_claims: list[PlanningQuantitativeClaimV2] = Field(default_factory=list)


class PlanningOutputErrorV2(ValueError):
    """Model output failed V2 planning safety or evidence validation."""


def _narrative_text(proposal: PlanningProposalV2) -> list[str]:
    parts = [proposal.summary]
    for item in proposal.issues:
        parts.extend((item.title, item.description))
    for item in proposal.recommendations:
        parts.extend((item.title, item.rationale, *item.actions))
    return parts


def planning_safety_issues(
    catalog: FacilityCatalog,
    bundle: PlanningEvidenceBundleV2,
    proposal: PlanningProposalV2,
    allowed_facility_ids: set[str],
) -> list[tuple[str, str]]:
    """Return deterministic contract/policy violations before model review."""
    issues: list[tuple[str, str]] = []
    evidence_items = {item.evidence_id: item for item in bundle.items}
    typed_facts = _typed_facts(bundle)
    for item in [*proposal.issues, *proposal.recommendations]:
        if item.facility_id not in allowed_facility_ids:
            issues.append(("unsupported_facility_capability", item.facility_id))
            continue
        for ref in item.evidence_refs:
            evidence_item = evidence_items.get(ref)
            if evidence_item is None or evidence_item.facility_id != item.facility_id:
                issues.append(("invalid_evidence_ref", ref))
    for claim in proposal.quantitative_claims:
        if claim.facility_id not in allowed_facility_ids:
            issues.append(("unsupported_facility_capability", claim.facility_id))
            continue
        matching = [typed_facts.get((ref, claim.metric)) for ref in claim.evidence_refs]
        if not matching or any(
            fact is None or fact[0] != claim.facility_id or fact[2] != "complete" or fact[1] != claim.value
            for fact in matching
        ):
            issues.append(("unsupported_numeric", claim.metric))
    fact_ids = set(evidence_items)
    for item in [*proposal.issues, *proposal.recommendations, *proposal.quantitative_claims]:
        if not set(item.evidence_refs) <= fact_ids:
            issues.append(("invalid_evidence_ref", "unknown evidence reference"))

    for text in _narrative_text(proposal):
        # The fixed project name is not a measured numeric claim. Keep the
        # guard on every other number, including actual walking-time claims.
        narrative_without_fixed_term = text.replace("15分钟生活圈", "")
        if _NUMBER_PATTERN.search(narrative_without_fixed_term):
            issues.append(("unsupported_numeric", "numeric text must use a typed evidence-linked claim"))
        if _CAPACITY_CLAIM.search(text):
            issues.append(("unsupported_capacity", "capacity or service-places claim is unsupported"))
        if _OPERATING_CLAIM.search(text):
            issues.append(("unsupported_operating_status", "operating/open status claim is unsupported"))
    # Check that every explicitly selected planning-capable category is represented in the evidence.
    for facility_id in allowed_facility_ids:
        if not any(item.facility_id == facility_id for item in bundle.items):
            issues.append(("invalid_evidence_ref", f"missing evidence for {facility_id}"))
    # Force catalog resolution here as a defense against forged allowed IDs.
    for item in [*proposal.issues, *proposal.recommendations, *proposal.quantitative_claims]:
        try:
            definition = catalog.get(item.facility_id)
        except KeyError:
            issues.append(("unsupported_facility_capability", item.facility_id))
        else:
            if "planning" not in definition.capabilities:
                issues.append(("unsupported_facility_capability", item.facility_id))
    return list(dict.fromkeys(issues))


def _typed_facts(bundle: PlanningEvidenceBundleV2) -> dict[tuple[str, str], tuple[str, int | float, str]]:
    """Map evidence ID/metric pairs to (facility ID, value, completeness)."""
    result = {}
    for item in bundle.items:
        if item.poi_count is not None:
            result[(item.evidence_id, "poi_count")] = (item.facility_id, item.poi_count, item.poi_status)
        if item.reachable_count is not None:
            result[(item.evidence_id, "reachable_count")] = (
                item.facility_id, item.reachable_count, item.metric_status
            )
        if item.covered_ratio is not None:
            result[(item.evidence_id, "covered_ratio")] = (
                item.facility_id, item.covered_ratio, item.metric_status
            )
    return result


def _evidence_fact_rows(bundle: PlanningEvidenceBundleV2) -> list[dict[str, object]]:
    rows = []
    for item in bundle.items:
        for metric, value, status in (
            ("poi_count", item.poi_count, item.poi_status),
            ("reachable_count", item.reachable_count, item.metric_status),
            ("covered_ratio", item.covered_ratio, item.metric_status),
        ):
            if value is not None:
                rows.append({
                    "evidence_id": item.evidence_id,
                    "facility_id": item.facility_id,
                    "facility_name": item.facility_name,
                    "metric": metric,
                    "value": value,
                    "status": status,
                    "source": item.source,
                })
    return rows


def facility_planning_limits(
    catalog: FacilityCatalog, facility_ids: list[str]
) -> list[FacilityPlanningLimitV2]:
    limits = []
    for facility_id in facility_ids:
        definition = catalog.get(facility_id)
        planning_supported = "planning" in definition.capabilities
        dimensions = list(_UNSUPPORTED_DIMENSIONS)
        if "coverage" not in definition.capabilities:
            dimensions.append("spatial_coverage")
        if not planning_supported:
            dimensions.append("planning_recommendation")
        explanation = _DIMENSION_EXPLANATIONS["spatial_coverage"]
        if not planning_supported:
            explanation = _DIMENSION_EXPLANATIONS["planning_recommendation"]
        else:
            explanation = "仅支持基于已列 POI 与步行路线证据的有限建议；人口覆盖、容量、运营和服务质量等维度未评估。"
        limits.append(FacilityPlanningLimitV2(
            facility_id=facility_id,
            planning_supported=planning_supported,
            unsupported_dimensions=list(dict.fromkeys(dimensions)),
            explanation=explanation,
        ))
    return limits


class PlanningAgentV2:
    """Generate bounded planning proposals without modifying the V1 PlanningAgent."""

    def __init__(self, llm: StructuredLLM, catalog: FacilityCatalog | None = None) -> None:
        self._llm = llm
        self._catalog = catalog or load_facility_catalog()

    def create_proposal(
        self,
        brief: AgentBriefV2,
        evidence_bundle: PlanningEvidenceBundleV2,
        planning_round: int = 1,
    ) -> PlanningProposalV2:
        if brief.intent is not AgentIntentV2.PLANNING_ANALYSIS or not brief.needs_planning:
            raise ValueError("V2 planning requires a planning-analysis brief")
        if brief.missing_information or brief.unresolved_mentions or brief.exploratory_terms:
            raise ValueError("V2 planning requires a resolved, complete planning brief")
        if type(planning_round) is not int or planning_round < 1:
            raise ValueError("planning_round must be a positive integer")

        selected: list[FacilityDefinition] = []
        for facility_id in brief.facility_ids:
            try:
                selected.append(self._catalog.get(facility_id))
            except KeyError:
                raise ValueError("V2 planning brief contains an unknown facility ID") from None
        allowed = {item.id for item in selected if "planning" in item.capabilities}
        if not allowed:
            raise ValueError("No selected facility has catalog planning capability")
        if not any(item.facility_id in allowed for item in evidence_bundle.items):
            raise ValueError("V2 planning requires evidence for a planning-capable facility")
        if any(item.facility_id not in {facility.id for facility in selected} for item in evidence_bundle.items):
            raise ValueError("Planning evidence contains a facility outside the selected brief")
        evidence_facilities = {item.facility_id for item in evidence_bundle.items}
        if not allowed <= evidence_facilities:
            raise ValueError("V2 planning requires evidence for every selected planning-capable facility")

        evidence_by_id = {item.evidence_id: item for item in evidence_bundle.items}
        payload = {
            "brief": brief.model_dump(mode="json"),
            "planning_facilities": [
                {"id": item.id, "name_zh": item.name_zh, "metric_profile": item.metric_profile,
                 "data_notes": item.data_notes, "basis": item.basis}
                for item in selected if item.id in allowed
            ],
            "catalog_limits": [
                {"facility_id": item.id, "planning_supported": "planning" in item.capabilities,
                 "reason": _DIMENSION_EXPLANATIONS["planning_recommendation"]}
                for item in selected if item.id not in allowed
            ],
            "evidence_bundle_id": evidence_bundle.evidence_bundle_id,
            "facts": [row for row in _evidence_fact_rows(evidence_bundle)
                      if row["facility_id"] in allowed],
            "planning_round": planning_round,
        }
        raw = self._llm.generate_object(
            system_prompt=PLANNING_SYSTEM_PROMPT_V2,
            user_message=json.dumps(payload, ensure_ascii=False),
            response_schema=PlanningDraftV2.model_json_schema(),
        )
        try:
            draft = PlanningDraftV2.model_validate(raw)
            if any(ref not in evidence_by_id for item in [*draft.issues, *draft.recommendations, *draft.quantitative_claims]
                   for ref in item.evidence_refs):
                raise PlanningOutputErrorV2("Planning output contains an unknown evidence reference")
            proposal = PlanningProposalV2(
                schema_version="2.0",
                catalog_version=self._catalog.version,
                proposal_id=f"plan_{uuid4().hex[:16]}",
                planning_round=planning_round,
                evidence_bundle_id=evidence_bundle.evidence_bundle_id,
                summary=draft.summary,
                issues=draft.issues,
                recommendations=draft.recommendations,
                quantitative_claims=draft.quantitative_claims,
                unsupported_dimensions=facility_planning_limits(self._catalog, brief.facility_ids),
            )
        except ValidationError:
            raise PlanningOutputErrorV2("Model output does not satisfy the V2 planning contract") from None
        except ValueError as exc:
            if isinstance(exc, PlanningOutputErrorV2):
                raise
            raise PlanningOutputErrorV2("Model output does not satisfy the V2 planning contract") from None

        safety_issues = planning_safety_issues(self._catalog, evidence_bundle, proposal, allowed)
        if safety_issues:
            raise PlanningOutputErrorV2("Model output contains unsupported claims or evidence links")
        if any(
            not set(item.evidence_refs) <= set(evidence_by_id)
            for item in [*proposal.issues, *proposal.recommendations, *proposal.quantitative_claims]
        ):
            raise PlanningOutputErrorV2("Model output contains an unknown evidence reference")
        return proposal
