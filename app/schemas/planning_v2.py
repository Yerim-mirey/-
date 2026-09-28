"""Evidence-linked, capability-bounded V2 planning and review contracts."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import ContractModel


PlanningMetricV2 = Literal["poi_count", "reachable_count", "covered_ratio"]
PlanningDimensionV2 = Literal[
    "population_capacity", "operating_status", "opening_hours", "price_affordability",
    "service_quality", "service_eligibility", "accessibility_barriers", "demand_supply_balance",
    "planning_recommendation", "spatial_coverage",
]


class PlanningEvidenceItemV2(ContractModel):
    """Compact per-facility evidence record adapted from the V2 runner."""

    facility_id: str = Field(min_length=1)
    facility_name: str = Field(min_length=1)
    poi_count: int | None = Field(default=None, ge=0)
    poi_status: Literal["complete", "partial", "unavailable"]
    reachable_count: int | None = Field(default=None, ge=0)
    covered_ratio: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    metric_status: Literal["complete", "partial", "unavailable"]
    source: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)


class PlanningEvidenceBundleV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    evidence_bundle_id: str = Field(min_length=1)
    items: list[PlanningEvidenceItemV2] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> "PlanningEvidenceBundleV2":
        if len({item.evidence_id for item in self.items}) != len(self.items):
            raise ValueError("evidence_id must be unique within a planning bundle")
        if len({item.facility_id for item in self.items}) != len(self.items):
            raise ValueError("planning evidence must have one item per facility")
        return self


class PlanningIssueV2(ContractModel):
    issue_id: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: Literal["low", "medium", "high"]
    evidence_refs: list[str] = Field(min_length=1)


class PlanningRecommendationV2(ContractModel):
    recommendation_id: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    priority: Literal["low", "medium", "high"]
    actions: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class PlanningQuantitativeClaimV2(ContractModel):
    facility_id: str = Field(min_length=1)
    metric: PlanningMetricV2
    value: int | float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class FacilityPlanningLimitV2(ContractModel):
    facility_id: str = Field(min_length=1)
    planning_supported: bool
    unsupported_dimensions: list[PlanningDimensionV2] = Field(min_length=1)
    explanation: str = Field(min_length=1)


class PlanningProposalV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    catalog_version: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    planning_round: int = Field(ge=1)
    evidence_bundle_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    issues: list[PlanningIssueV2] = Field(min_length=1)
    recommendations: list[PlanningRecommendationV2] = Field(min_length=1)
    quantitative_claims: list[PlanningQuantitativeClaimV2] = Field(default_factory=list)
    unsupported_dimensions: list[FacilityPlanningLimitV2] = Field(min_length=1)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "PlanningProposalV2":
        if len({item.issue_id for item in self.issues}) != len(self.issues):
            raise ValueError("issue IDs must be unique")
        if len({item.recommendation_id for item in self.recommendations}) != len(self.recommendations):
            raise ValueError("recommendation IDs must be unique")
        for item in [*self.issues, *self.recommendations, *self.quantitative_claims]:
            if len(set(item.evidence_refs)) != len(item.evidence_refs):
                raise ValueError("evidence refs must be unique per planning item")
        if len({item.facility_id for item in self.unsupported_dimensions}) != len(self.unsupported_dimensions):
            raise ValueError("unsupported dimensions must have one record per facility")
        return self


class PlanningReviewIssueV2(ContractModel):
    issue_id: str = Field(min_length=1)
    category: Literal[
        "unsupported_numeric", "unsupported_capacity", "unsupported_operating_status",
        "invalid_evidence_ref", "unsupported_facility_capability", "unsupported_dimension_missing",
        "evidence_mismatch", "semantic_quality",
    ]
    message: str = Field(min_length=1)
    recommendation_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class PlanningReviewResultV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    status: Literal["approved", "revision_required", "insufficient_evidence"]
    reviewed_proposal_id: str = Field(min_length=1)
    reviewed_planning_round: int = Field(ge=1)
    reviewed_evidence_bundle_id: str = Field(min_length=1)
    issues: list[PlanningReviewIssueV2] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)
