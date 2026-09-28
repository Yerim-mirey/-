"""Public response contract for a V2 Agent run."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.agent_v2 import AgentBriefV2
from app.schemas.common import CenterPoint, ContractModel, ErrorDetail
from app.schemas.diagnosis_v2 import DiagnosisDataV2
from app.schemas.planning_v2 import PlanningProposalV2, PlanningReviewResultV2
from app.schemas.poi_v2 import POISearchFailureV2, POISearchSuccessV2
from app.schemas.routing_v2 import RoutingResponseV2


RunStatusV2 = Literal["waiting_for_input", "completed", "failed"]
POIResultEnvelopeV2 = POISearchSuccessV2 | POISearchFailureV2


class AgentRunStateV2(ContractModel):
    """Structured evidence and outcome from one generic facility request."""

    schema_version: Literal["2.0"] = "2.0"
    run_id: str = Field(pattern=r"^run:[A-Za-z0-9._:-]{1,96}$")
    status: RunStatusV2
    user_message: str = Field(min_length=1, max_length=4000)
    brief: AgentBriefV2 | None = None
    resolved_center: CenterPoint | None = None
    poi_results: list[POIResultEnvelopeV2] = Field(default_factory=list)
    routing: RoutingResponseV2 | None = None
    diagnosis: DiagnosisDataV2 | None = None
    planning_proposal: PlanningProposalV2 | None = None
    review_result: PlanningReviewResultV2 | None = None
    limitations: list[str] = Field(default_factory=list)
    answer: str | None = Field(default=None, min_length=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)
