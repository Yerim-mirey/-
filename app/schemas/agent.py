"""Shared Agent v1 contracts built on deterministic Core MVP results."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.blindspot import BlindspotResult
from app.schemas.common import ContractModel, ErrorDetail, WarningItem
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.isochrone import IsochroneResult
from app.schemas.location import LocationInput, LocationResult
from app.schemas.poi import FacilityType, POISearchResult
from app.schemas.routing import RoutingResult


AgentSchemaVersion = Literal["1.0"]
NonEmptyText = Annotated[str, Field(min_length=1)]
Facility = Annotated[FacilityType, Field(strict=False)]
JsonPointer = Annotated[
    str,
    Field(min_length=2, pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])*)*$"),
]
EvidenceId = Annotated[
    str,
    Field(pattern=r"^(location|poi|routing|isochrone|blindspot|diagnosis):[a-z0-9][a-z0-9._-]*$"),
]
EvidenceBundleId = Annotated[str, Field(pattern=r"^evidence:[a-z0-9][a-z0-9._-]*$")]
IssueId = Annotated[str, Field(pattern=r"^issue:[a-z0-9][a-z0-9._-]*$")]
RecommendationId = Annotated[str, Field(pattern=r"^recommendation:[a-z0-9][a-z0-9._-]*$")]
ProposalId = Annotated[str, Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")]
RequestId = Annotated[str, Field(pattern=r"^request:[a-z0-9][a-z0-9._-]*$")]
ReviewIssueId = Annotated[str, Field(pattern=r"^review:[a-z0-9][a-z0-9._-]*$")]
RunId = Annotated[str, Field(pattern=r"^run:[a-z0-9][a-z0-9._-]*$")]


def _decoded_json_pointer_tokens(pointer: str) -> list[str]:
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer.split("/")[1:]
    ]


class AgentIntent(str, Enum):
    FACILITY_QUERY = "facility_query"
    ACCESSIBILITY_QUERY = "accessibility_query"
    BLINDSPOT_QUERY = "blindspot_query"
    COMMUNITY_DIAGNOSIS = "community_diagnosis"
    PLANNING_ANALYSIS = "planning_analysis"


class BriefMissingField(str, Enum):
    LOCATION = "location"
    FACILITY_TYPES = "facility_types"


class EvidenceKind(str, Enum):
    LOCATION = "location"
    POI = "poi"
    ROUTING = "routing"
    ISOCHRONE = "isochrone"
    BLINDSPOT = "blindspot"
    DIAGNOSIS = "diagnosis"


class MissingInformation(ContractModel):
    field: BriefMissingField = Field(strict=False)
    message: NonEmptyText


class LifeCircleBrief(ContractModel):
    schema_version: AgentSchemaVersion
    intent: AgentIntent = Field(strict=False)
    user_goal: NonEmptyText
    location: LocationInput | None = None
    facility_types: list[Facility] = Field(default_factory=list)
    needs_full_diagnosis: bool
    needs_planning: bool
    needs_review: bool
    missing_information: list[MissingInformation] = Field(default_factory=list)

    @model_validator(mode="after")
    def intent_is_actionable(self) -> "LifeCircleBrief":
        if len(set(self.facility_types)) != len(self.facility_types):
            raise ValueError("facility_types 不能重复。")
        missing = [item.field for item in self.missing_information]
        if len(set(missing)) != len(missing):
            raise ValueError("missing_information.field 不能重复。")
        if (self.location is None) != (BriefMissingField.LOCATION in missing):
            raise ValueError("location 与对应 missing_information 必须一致。")
        if (not self.facility_types) != (BriefMissingField.FACILITY_TYPES in missing):
            raise ValueError("facility_types 与对应 missing_information 必须一致。")
        expected = {
            AgentIntent.FACILITY_QUERY: (False, False, False),
            AgentIntent.ACCESSIBILITY_QUERY: (False, False, False),
            # 盲区结论必须由 Diagnosis 的等时圈与盲区覆盖支撑，不能用 POI 计数代替。
            AgentIntent.BLINDSPOT_QUERY: (True, False, False),
            AgentIntent.COMMUNITY_DIAGNOSIS: (True, False, False),
            AgentIntent.PLANNING_ANALYSIS: (True, True, True),
        }[self.intent]
        if (self.needs_full_diagnosis, self.needs_planning, self.needs_review) != expected:
            raise ValueError("Intent 与 diagnosis/planning/review 标志不一致。")
        return self


class EvidenceRef(ContractModel):
    evidence_id: EvidenceId
    kind: EvidenceKind = Field(strict=False)
    json_pointer: JsonPointer
    summary: NonEmptyText

    @model_validator(mode="after")
    def identity_matches_kind(self) -> "EvidenceRef":
        if self.evidence_id.split(":", 1)[0] != self.kind.value:
            raise ValueError("evidence_id 前缀必须与 kind 一致。")
        tokens = _decoded_json_pointer_tokens(self.json_pointer)
        if tokens[0] != self.kind.value:
            raise ValueError("json_pointer 根必须与 kind 一致。")
        if "root" in tokens:
            raise ValueError("json_pointer 不得暴露 RootModel.root。")
        return self


class EvidenceBundle(ContractModel):
    schema_version: AgentSchemaVersion
    bundle_id: EvidenceBundleId
    refs: list[EvidenceRef] = Field(min_length=1)
    location: LocationResult | None = None
    poi: POISearchResult | None = None
    routing: RoutingResult | None = None
    isochrone: IsochroneResult | None = None
    blindspot: BlindspotResult | None = None
    diagnosis: DiagnosisResult | None = None
    warnings: list[WarningItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_is_traceable(self) -> "EvidenceBundle":
        lower_level = (self.location, self.poi, self.routing, self.isochrone, self.blindspot)
        if self.diagnosis is not None and any(item is not None for item in lower_level):
            raise ValueError("Diagnosis 证据不能与其低层 Tool 结果重复出现。")
        if self.diagnosis is None and not any(item is not None for item in lower_level):
            raise ValueError("EvidenceBundle 至少包含一个 Tool Result。")
        if len({item.evidence_id for item in self.refs}) != len(self.refs):
            raise ValueError("evidence_id 不能重复。")
        if any(getattr(self, item.kind.value) is None for item in self.refs):
            raise ValueError("EvidenceRef 必须指向 Bundle 中存在的 Tool Result。")
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("EvidenceBundle warnings 必须去重。")
        return self


class PlanningPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlanningIssue(ContractModel):
    issue_id: IssueId
    title: NonEmptyText
    description: NonEmptyText
    priority: PlanningPriority = Field(strict=False)
    facility_type: Facility | None = None
    evidence_refs: list[EvidenceId] = Field(min_length=1)


class PlanningRecommendation(ContractModel):
    recommendation_id: RecommendationId
    title: NonEmptyText
    rationale: NonEmptyText
    priority: PlanningPriority = Field(strict=False)
    actions: list[NonEmptyText] = Field(min_length=1)
    evidence_refs: list[EvidenceId] = Field(min_length=1)
    limitations: list[NonEmptyText] = Field(default_factory=list)


class PlanningProposal(ContractModel):
    schema_version: AgentSchemaVersion
    proposal_id: ProposalId
    planning_round: int = Field(ge=1)
    evidence_bundle_id: EvidenceBundleId
    summary: NonEmptyText
    issues: list[PlanningIssue] = Field(min_length=1)
    recommendations: list[PlanningRecommendation] = Field(min_length=1)
    limitations: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_and_refs_are_unique(self) -> "PlanningProposal":
        if len({item.issue_id for item in self.issues}) != len(self.issues):
            raise ValueError("issue_id 不能重复。")
        if len({item.recommendation_id for item in self.recommendations}) != len(self.recommendations):
            raise ValueError("recommendation_id 不能重复。")
        for item in [*self.issues, *self.recommendations]:
            if len(set(item.evidence_refs)) != len(item.evidence_refs):
                raise ValueError("单个规划项的 evidence_refs 不能重复。")
        return self


class EvidenceRequest(ContractModel):
    request_id: RequestId
    kind: Literal["diagnosis"]
    reason: NonEmptyText
    required_json_pointers: list[JsonPointer] = Field(min_length=1)
    facility_types: list[Facility] = Field(min_length=1)

    @model_validator(mode="after")
    def requested_paths_match_kind(self) -> "EvidenceRequest":
        if len(set(self.required_json_pointers)) != len(self.required_json_pointers):
            raise ValueError("required_json_pointers 不能重复。")
        if len(set(self.facility_types)) != len(self.facility_types):
            raise ValueError("EvidenceRequest facility_types 不能重复。")
        for pointer in self.required_json_pointers:
            tokens = _decoded_json_pointer_tokens(pointer)
            if tokens[0] != self.kind or "root" in tokens:
                raise ValueError("Agent v1 补证据只能请求不暴露 RootModel.root 的 Diagnosis JSON Pointer。")
        return self


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    REVISION_REQUIRED = "revision_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ReviewIssueCategory(str, Enum):
    UNSUPPORTED_FACT = "unsupported_fact"
    NUMERIC_MISMATCH = "numeric_mismatch"
    OVERREACH = "overreach"
    WARNING_IGNORED = "warning_ignored"
    ABSENCE_OVERSTATED = "absence_overstated"
    PROBLEM_MISMATCH = "problem_mismatch"


class ReviewIssue(ContractModel):
    issue_id: ReviewIssueId
    category: ReviewIssueCategory = Field(strict=False)
    message: NonEmptyText
    recommendation_ids: list[RecommendationId] = Field(default_factory=list)
    evidence_refs: list[EvidenceId] = Field(default_factory=list)


class ReviewResult(ContractModel):
    schema_version: AgentSchemaVersion
    status: ReviewStatus = Field(strict=False)
    reviewed_proposal_id: ProposalId
    reviewed_planning_round: int = Field(ge=1)
    reviewed_evidence_bundle_id: EvidenceBundleId
    issues: list[ReviewIssue]
    revision_instructions: list[NonEmptyText]
    missing_evidence: list[EvidenceRequest]

    @model_validator(mode="after")
    def status_matches_payload(self) -> "ReviewResult":
        if len({item.issue_id for item in self.issues}) != len(self.issues):
            raise ValueError("Review issue_id 不能重复。")
        if len({item.request_id for item in self.missing_evidence}) != len(self.missing_evidence):
            raise ValueError("EvidenceRequest request_id 不能重复。")
        if self.status is ReviewStatus.APPROVED and (self.issues or self.revision_instructions or self.missing_evidence):
            raise ValueError("approved 不能携带问题、修改要求或缺失证据。")
        if self.status is ReviewStatus.REVISION_REQUIRED and not (self.issues or self.revision_instructions):
            raise ValueError("revision_required 必须说明问题或修改要求。")
        if self.status is ReviewStatus.REVISION_REQUIRED and self.missing_evidence:
            raise ValueError("需要补证据时应使用 insufficient_evidence。")
        if self.status is ReviewStatus.INSUFFICIENT_EVIDENCE and not self.missing_evidence:
            raise ValueError("insufficient_evidence 必须包含结构化 EvidenceRequest。")
        return self


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionMode(str, Enum):
    NORMAL = "normal"
    WITH_LIMITATIONS = "with_limitations"


class AgentRunState(ContractModel):
    schema_version: AgentSchemaVersion
    run_id: RunId
    status: RunStatus = Field(strict=False)
    completion_mode: Annotated[CompletionMode, Field(strict=False)] | None = None
    user_message: NonEmptyText
    brief: LifeCircleBrief | None = None
    evidence: EvidenceBundle | None = None
    planning_proposal: PlanningProposal | None = None
    review_result: ReviewResult | None = None
    planning_round: int = Field(default=0, ge=0)
    tool_retry_count: int = Field(default=0, ge=0)
    warnings: list[WarningItem] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def state_is_reachable(self) -> "AgentRunState":
        outputs = (self.brief, self.evidence, self.planning_proposal, self.review_result)
        if self.status is RunStatus.PENDING and (
            any(item is not None for item in outputs)
            or self.planning_round != 0
            or self.tool_retry_count != 0
            or self.completion_mode is not None
            or self.warnings
            or self.errors
        ):
            raise ValueError("pending Run 必须保持初始化状态。")
        if self.status is RunStatus.FAILED and not self.errors:
            raise ValueError("failed Run 必须包含 error。")
        if self.status is not RunStatus.FAILED and self.errors:
            raise ValueError("非 failed Run 不能包含 error。")
        if self.status is RunStatus.WAITING_FOR_INPUT and (
            self.brief is None
            or not self.brief.missing_information
            or any(item is not None for item in (self.evidence, self.planning_proposal, self.review_result))
            or self.planning_round != 0
            or self.tool_retry_count != 0
        ):
            raise ValueError("waiting_for_input 必须只包含带缺失信息的 Brief。")
        if self.brief is not None and self.brief.missing_information and self.status not in {
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.FAILED,
        }:
            raise ValueError("存在 missing_information 时 Run 必须等待用户输入。")
        if self.status is RunStatus.COMPLETED and self.completion_mode is None:
            raise ValueError("completed Run 必须声明 completion_mode。")
        if self.status is not RunStatus.COMPLETED and self.completion_mode is not None:
            raise ValueError("只有 completed Run 可以声明 completion_mode。")
        if self.evidence is not None and self.brief is None:
            raise ValueError("Evidence 必须依赖 Brief。")
        if self.evidence is not None and self.brief.missing_information:
            raise ValueError("缺少必要用户信息时不能获取 Evidence。")
        if self.planning_proposal is not None and (self.brief is None or self.evidence is None):
            raise ValueError("PlanningProposal 必须依赖 Brief 和 Evidence。")
        if self.review_result is not None and self.planning_proposal is None:
            raise ValueError("ReviewResult 必须依赖 PlanningProposal。")
        if self.planning_proposal is not None and (
            not self.brief.needs_planning
            or self.planning_proposal.planning_round != self.planning_round
            or self.planning_proposal.evidence_bundle_id != self.evidence.bundle_id
        ):
            raise ValueError("PlanningProposal 与 Brief、round 或 Evidence 不一致。")
        if self.review_result is not None and (
            self.review_result.reviewed_proposal_id != self.planning_proposal.proposal_id
            or self.review_result.reviewed_planning_round != self.planning_round
            or self.review_result.reviewed_evidence_bundle_id != self.evidence.bundle_id
        ):
            raise ValueError("ReviewResult 必须绑定当前 Proposal、round 和 Evidence。")
        if self.brief is not None and not self.brief.needs_planning and (
            self.planning_round != 0 or self.planning_proposal is not None or self.review_result is not None
        ):
            raise ValueError("非规划任务不能携带规划或审查状态。")
        if self.status is RunStatus.COMPLETED:
            if self.brief is None or self.evidence is None:
                raise ValueError("completed Run 必须包含 Brief 和 Evidence。")
            if self.brief.needs_planning:
                if self.planning_proposal is None or self.review_result is None:
                    raise ValueError("规划任务完成前必须生成 Proposal 和 Review。")
                approved = self.review_result.status is ReviewStatus.APPROVED
                if (self.completion_mode is CompletionMode.NORMAL) != approved:
                    raise ValueError("normal 完成要求 approved；未通过只能带限制完成。")
            elif self.completion_mode is not CompletionMode.NORMAL:
                raise ValueError("非规划任务只能 normal 完成。")
        if len({(item.code, item.message, item.retryable) for item in self.errors}) != len(self.errors):
            raise ValueError("Run errors 必须去重。")
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("Run warnings 必须去重。")
        return self
